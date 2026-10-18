"""Sends marketing flyers to WhatsApp groups in the background
(docs/MARKETING_FLYERS_CONTRACT.md, section 4).

POST /api/flyers/{id}/send and /retry only queue rows (services/flyers.py)
and hand their ids to enqueue(). One worker thread per process then works
through every queued send - one group at a time across all batches, with a
random 3-6 s pause between groups so the store's number never looks like a
spammer:

1. Re-check the row right before sending: the flyer still exists, the group
   is still an active marketing group with the same chat, the file is still
   on disk.
2. A retry of a send that may already have gone out (`ambiguous`) first looks
   in the group's recent messages and settles it as sent if it's there.
3. Upload the file to Whapi once (MediaID, reused for every group), check
   step 1 again (an upload can take minutes), then POST /messages/image or
   /messages/document (whapi_client).
4. Each send ends `sent` (with Whapi's message id) or `failed` (with a short
   error). A timeout is checked against the group's history before it's
   called a failure, as for invoices and checklists.
5. When nothing in a batch is pending any more, the person who sent it gets
   one push (alert `flyer_sent`, quiet hours respected).

Platform milestone (docs/PLATFORM_CONTRACT.md 6.3): one worker thread per
process for every store. Queue items are (store code, send id) and each is
sent inside its store's context; the idle sweep runs per store
(for_each_store). The MediaID cache and the pause between sends are per
store, so one store's sends never change another store's pacing.

Nothing restarts by itself after a crash or restart: start() (app startup)
and an idle sweep every few minutes mark rows stuck in queued / sending for
more than STALE_AFTER as failed ("Interrupted - retry"), so a person decides
whether to send again. A row another live process (or this one) is still
working through is left alone.
"""

import datetime as dt
import logging
import queue
import random
import threading
import time
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Flyer, FlyerSend
from app.services import flyers, whapi_client
from app.services.flyers import CHANNEL_WHATSAPP, FAILED, PENDING, QUEUED, SENDING, SENT
from app.services.notifications import Alert, dispatch_alert, in_quiet_hours
from app.services.whapi_client import WhapiAmbiguousTimeout, WhapiMediaError, WhapiNotConfigured
from app.tenancy import current_store_code, for_each_store, store_context, tenant_local, tenant_lock
from app.timeutil import restaurant_timezone, utcnow

logger = logging.getLogger(__name__)

ALERT_KEY = "flyer_sent"
INTERRUPTED = "Interrupted - retry"
STALE_AFTER = dt.timedelta(minutes=15)
IDLE_SWEEP_SECONDS = 300
DELAY_RANGE = (3.0, 6.0)
# A timed-out send's message can show up in the group a few seconds late.
RECONCILE_ATTEMPTS = 3
RECONCILE_DELAY_SECONDS = 5
# How long an uploaded MediaID is reused (Whapi keeps it 30 days).
MEDIA_ID_TTL_SECONDS = 6 * 3600
MAX_ERROR = 300
# Whapi statuses that can mean "this MediaID wasn't accepted" - then the
# same send is made once more with the file inline (nothing went out).
_MEDIA_ID_REFUSED = (400, 404, 415, 422)

# Seams for tests (and nothing else).
sleep = time.sleep


def jitter() -> float:
    return random.uniform(*DELAY_RANGE)


_lock = threading.Lock()  # global: guards the queue bookkeeping below (keys carry the store)
# (store code, send id), or None = stop.
_queue: "queue.Queue[Optional[Tuple[str, int]]]" = queue.Queue()
# (store code, send id) -> how many times it's waiting in, or being sent by,
# this process's queue. A count, not a set: a send retried while the worker
# is still finishing its failed attempt (e.g. dispatching the push) is queued
# again rather than skipped - the worker re-reads its status anyway.
_owned: Dict[Tuple[str, int], int] = {}
_thread: Optional[threading.Thread] = None
# Per store: time.monotonic() when the store's last real send finished.
_pacing: dict = tenant_local("flyer_sender.pacing", lambda: {"last_send_done": 0.0})
_media_lock = tenant_lock("flyer_sender.media")
# Per store: (flyer id, sha256) -> (MediaID, or None = upload failed: send inline; monotonic time)
_media_ids: Dict[Tuple[int, str], Tuple[Optional[str], float]] = tenant_local("flyer_sender.media_ids", dict)


# --- Queue + worker ---------------------------------------------------------------------


def enqueue(send_ids: Iterable[int]) -> None:
    """Hands queued rows to this process's worker (started on first use).
    An id already in the queue is queued again: process_send skips a row
    that isn't `queued` any more, so nothing is sent twice."""
    code = current_store_code()
    if code is None:
        raise RuntimeError("flyer sends are queued inside a store")
    with _lock:
        for send_id in send_ids:
            key = (code, send_id)
            _owned[key] = _owned.get(key, 0) + 1
            _queue.put(key)
        _ensure_thread()


def _release(key: Tuple[str, int]) -> None:
    with _lock:
        left = _owned.get(key, 0) - 1
        if left > 0:
            _owned[key] = left
        else:
            _owned.pop(key, None)


def _ensure_thread() -> None:
    global _thread
    if _thread is None or not _thread.is_alive():
        _thread = threading.Thread(target=_run, name="flyer-sender", daemon=True)
        _thread.start()


def _run() -> None:
    while True:
        try:
            item = _queue.get(timeout=IDLE_SWEEP_SECONDS)
        except queue.Empty:
            _sweep()
            continue
        if item is None:
            _queue.task_done()
            return
        code, send_id = item
        try:
            with store_context(code, allow_inactive=True):
                process_send(send_id)
        except Exception:  # noqa: BLE001 - one bad send must never stop the worker
            logger.exception("Flyer send %s (store %s) crashed", send_id, code)
        finally:
            _release(item)
            _queue.task_done()


def _sweep() -> None:
    """The stale-row sweep, for every store this process serves."""
    try:
        for_each_store(lambda _ref: _sweep_store())
    except Exception:  # noqa: BLE001 - the registry may be briefly unreachable
        logger.exception("Flyer sends: sweep failed")


def _sweep_store() -> None:
    code = current_store_code()
    with _lock:
        owned = {send_id for (c, send_id) in _owned if c == code}
    db = SessionLocal()
    try:
        recovered = recover_stuck_sends(db, owned=owned)
        if recovered:
            logger.warning("Flyer sends: %d stuck send(s) marked failed (%s)", recovered, INTERRUPTED)
    except (OperationalError, ProgrammingError):
        # The tables aren't there yet (database not migrated) - nothing to do.
        db.rollback()
    except Exception:  # noqa: BLE001
        logger.exception("Flyer sends: sweep failed")
        db.rollback()
    finally:
        db.close()


def start() -> None:
    """App startup (main.py lifespan): fail sends a previous run left stuck,
    then start the (idle) worker so the sweep keeps running. Never raises.
    Multi mode: the first sweep (every store) runs in the background."""
    from app.tenancy import is_multi_tenant, spawn_in_store

    if is_multi_tenant():
        spawn_in_store(_sweep, name="flyer-sender-first-sweep")
    else:
        _sweep()
    with _lock:
        _ensure_thread()


def stop(timeout: float = 5.0) -> None:
    """App shutdown: stops the worker after the send it's on. Sends still
    waiting stay `queued` in the database and are failed as interrupted
    later (a person decides whether to send them again)."""
    global _thread
    with _lock:
        thread = _thread
    if thread is None or not thread.is_alive():
        return
    while True:
        try:
            dropped = _queue.get_nowait()
        except queue.Empty:
            break
        _queue.task_done()
        if dropped is not None:
            _release(dropped)
    _queue.put(None)
    thread.join(timeout)
    with _lock:
        if _thread is thread and not thread.is_alive():
            _thread = None


# --- Stale rows -------------------------------------------------------------------------


def recover_stuck_sends(db: Session, now: Optional[dt.datetime] = None, owned: Iterable[int] = ()) -> int:
    """Marks queued / sending rows failed ("Interrupted - retry") when their
    batch has shown no activity (requested, queued, attempted or sent) for
    STALE_AFTER and this process isn't holding them. A row that was being
    sent becomes `ambiguous`, so a retry checks the group first. Returns how
    many rows changed."""
    now = now or utcnow()
    cutoff = now - STALE_AFTER
    owned = set(owned)
    stuck = list(db.execute(select(FlyerSend).where(FlyerSend.status.in_(PENDING))).scalars())
    stuck = [r for r in stuck if r.id not in owned]
    if not stuck:
        return 0
    latest: Dict[str, dt.datetime] = {}
    batch_rows = db.execute(select(FlyerSend).where(FlyerSend.batch_id.in_({r.batch_id for r in stuck}))).scalars()
    for row in batch_rows:
        for stamp in (row.requested_at, row.queued_at, row.attempted_at, row.sent_at):
            if stamp is not None and (row.batch_id not in latest or stamp > latest[row.batch_id]):
                latest[row.batch_id] = stamp
    changed = 0
    for row in stuck:
        last = latest.get(row.batch_id)
        if last is not None and last >= cutoff:
            continue
        row.ambiguous = bool(row.ambiguous) or row.status == SENDING
        row.status = FAILED
        row.error = INTERRUPTED
        changed += 1
    if changed:
        db.commit()
    return changed


# --- One send ---------------------------------------------------------------------------


def _media_kind(flyer: Flyer) -> str:
    return "document" if flyers.kind_for(flyer.mime) == "pdf" else "image"


def _problem(db: Session, flyer: Optional[Flyer], row: FlyerSend) -> Optional[str]:
    """Why this row can't be sent now, or None."""
    from app.services.whatsapp_groups import marketing_targets

    if flyer is None or flyer.deleted_at is not None:
        return "The flyer was deleted before it was sent."
    if row.channel != CHANNEL_WHATSAPP:
        return f"Sending to {row.channel} isn't supported yet."
    target = {g["key"]: g for g in marketing_targets(db)}.get(row.target_key)
    if target is None:
        return "This group isn't marked \"Use for flyers\" (or is turned off) any more."
    if not row.target_ref:
        return "This group has no WhatsApp chat id."
    if target["chat_id"] != row.target_ref:
        # The group was removed and added again (same name) with another
        # WhatsApp chat: the chat this was queued for isn't a marketing group
        # any more, so it must not get the flyer. Retry sends to the new chat.
        return "This group's WhatsApp chat was changed after the flyer was queued. Retry to send it to the new chat."
    if flyers.flyer_file(flyer) is None:
        return "The flyer's file is missing from the Flyers folder."
    return None


def _short(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= MAX_ERROR else text[: MAX_ERROR - 1] + "…"


def _mark_sent(db: Session, row: FlyerSend, message_id) -> None:
    row.status = SENT
    row.sent_at = utcnow()
    row.provider_message_id = str(message_id)[:200] if message_id else None
    row.error = None
    row.ambiguous = False
    db.commit()


def _mark_failed(db: Session, row: FlyerSend, error: str, ambiguous: bool = False) -> None:
    row.status = FAILED
    row.error = _short(error)
    row.ambiguous = ambiguous
    db.commit()


def _pace() -> None:
    """3-6 s (random) between the end of one real send and the next."""
    last = _pacing["last_send_done"]
    if last:
        wait = last + jitter() - time.monotonic()
        if wait > 0:
            sleep(wait)


def _send_finished() -> None:
    _pacing["last_send_done"] = time.monotonic()


def _media_for(flyer: Flyer, data: bytes) -> Tuple[str, bool]:
    """(media, is_media_id): the flyer's MediaID (uploaded once, reused), or
    the file inline as a data URL when the upload doesn't work."""
    key = (flyer.id, flyer.sha256)
    with _media_lock:
        cached = _media_ids.get(key)
    if cached is not None and time.monotonic() - cached[1] < MEDIA_ID_TTL_SECONDS:
        if cached[0]:
            return cached[0], True
        return whapi_client.media_data_url(data, flyer.mime, flyers.display_file_name(flyer)), False
    try:
        media_id: Optional[str] = whapi_client.upload_media(data, flyer.mime)
    except WhapiNotConfigured:
        raise
    except Exception as exc:  # noqa: BLE001 - any upload problem: send inline instead
        logger.warning("Flyer %s: media upload failed (%s) - sending the file inline", flyer.id, exc)
        media_id = None
    with _media_lock:
        _media_ids[key] = (media_id, time.monotonic())
    if media_id:
        return media_id, True
    return whapi_client.media_data_url(data, flyer.mime, flyers.display_file_name(flyer)), False


def _forget_media_id(flyer: Flyer) -> None:
    with _media_lock:
        _media_ids[(flyer.id, flyer.sha256)] = (None, time.monotonic())


def clear_media_cache() -> None:
    with _media_lock:
        _media_ids.clear()


def _deliver(flyer: Flyer, data: bytes, row: FlyerSend) -> dict:
    kind = _media_kind(flyer)
    caption = flyer.caption or None
    filename = flyers.display_file_name(flyer) if kind == "document" else None
    media, by_id = _media_for(flyer, data)
    try:
        return whapi_client.send_media_message(kind, row.target_ref, media, caption=caption, filename=filename)
    except WhapiMediaError as exc:
        if not by_id or exc.maybe_sent or exc.status_code not in _MEDIA_ID_REFUSED:
            raise
        # Refused outright (nothing went out) while using the MediaID: send
        # the file itself instead.
        logger.warning("Flyer %s: MediaID refused (%s) - sending the file inline", flyer.id, exc.status_code)
        inline = whapi_client.media_data_url(data, flyer.mime, flyers.display_file_name(flyer))
        answer = whapi_client.send_media_message(kind, row.target_ref, inline, caption=caption, filename=filename)
        # Only now is it clear the MediaID was the problem: stop using it.
        # A group that refuses the file inline too (it's gone, the number
        # left it...) raised above and leaves the MediaID for the other
        # groups - one bad group must not send every later group (for
        # MEDIA_ID_TTL_SECONDS) a full base64 copy of the file.
        _forget_media_id(flyer)
        return answer


def _find_in_group(
    db: Session, flyer: Flyer, row: FlyerSend, since: dt.datetime, attempts: int = 1
) -> Optional[dict]:
    """This flyer, posted by us in the row's group since `since` - never a
    message already credited to another send (e.g. the same flyer sent to
    the same group a minute earlier)."""
    kind = _media_kind(flyer)
    filename = flyers.display_file_name(flyer) if kind == "document" else None
    credited = set(
        db.execute(
            select(FlyerSend.provider_message_id).where(
                FlyerSend.target_ref == row.target_ref,
                FlyerSend.provider_message_id.is_not(None),
                FlyerSend.id != row.id,
                FlyerSend.sent_at >= since - dt.timedelta(days=1),
            )
        ).scalars()
    )
    for attempt in range(attempts):
        found = whapi_client.find_sent_media(
            row.target_ref, kind, flyer.caption, since.timestamp(), filename, exclude_ids=credited
        )
        if found is not None:
            return found
        if attempt < attempts - 1:
            sleep(RECONCILE_DELAY_SECONDS)
    return None


def process_send(send_id: int) -> None:
    """Sends one queued row (see the module docstring). Never raises for a
    send problem - it's recorded on the row."""
    db = SessionLocal()
    try:
        row = db.get(FlyerSend, send_id)
        if row is None or row.status != QUEUED:
            return
        flyer = db.get(Flyer, row.flyer_id)
        problem = _problem(db, flyer, row)
        if problem:
            # Nothing is attempted, so an earlier attempt that may have gone
            # out stays "may have gone out" (a later retry checks first).
            _mark_failed(db, row, problem, ambiguous=bool(row.ambiguous))
            _notify_if_batch_done(db, row.batch_id)
            return
        if row.ambiguous and row.attempted_at is not None:
            # The last attempt may have gone through: look before resending.
            found = _find_in_group(db, flyer, row, row.attempted_at)
            if found is not None:
                logger.info("Flyer send %s: found in the group already - not sent again", row.id)
                _mark_sent(db, row, found.get("id"))
                _notify_if_batch_done(db, row.batch_id)
                return
        # End the read transaction before waiting, so the re-read below sees
        # any change made meanwhile (e.g. another process's sweep).
        db.rollback()
        _pace()
        db.refresh(row)
        if row.status != QUEUED:
            return
        # Anything may have changed during the pause (flyer deleted, group
        # unticked): check again right before sending.
        problem = _problem(db, flyer, row)
        if problem:
            _mark_failed(db, row, problem, ambiguous=bool(row.ambiguous))
            _notify_if_batch_done(db, row.batch_id)
            return
        started = utcnow()
        row.status = SENDING
        row.attempted_at = started
        row.attempts = (row.attempts or 0) + 1
        row.error = None
        row.ambiguous = False
        db.commit()
        try:
            _attempt(db, flyer, row, started)
        finally:
            _send_finished()
        _notify_if_batch_done(db, row.batch_id)
    finally:
        db.close()


def _attempt(db: Session, flyer: Flyer, row: FlyerSend, started: dt.datetime) -> None:
    try:
        path = flyers.flyer_file(flyer)
        if path is None:
            raise FileNotFoundError(flyer.file_rel_path)
        data = path.read_bytes()
    except OSError:
        _mark_failed(db, row, "The flyer's file is missing from the Flyers folder.")
        return
    try:
        # Upload first (once per flyer and run - up to ~2 minutes for a big
        # PDF; the send below reuses it), then check the group and flyer
        # once more: a group unticked or a flyer deleted meanwhile must not
        # get it.
        _media_for(flyer, data)
        problem = _problem(db, flyer, row)
        if problem:
            _mark_failed(db, row, problem)
            return
        answer = _deliver(flyer, data, row)
    except WhapiNotConfigured:
        _mark_failed(db, row, flyers.NOT_CONNECTED)
        return
    except (WhapiAmbiguousTimeout, WhapiMediaError) as exc:
        if isinstance(exc, WhapiMediaError) and not exc.maybe_sent:
            _mark_failed(db, row, str(exc))
            return
        found = _find_in_group(db, flyer, row, started, attempts=RECONCILE_ATTEMPTS)
        if found is not None:
            _mark_sent(db, row, found.get("id"))
        else:
            _mark_failed(db, row, str(exc), ambiguous=True)
        return
    except Exception as exc:  # noqa: BLE001 - recorded on the row, never raised
        logger.exception("Flyer send %s failed", row.id)
        _mark_failed(db, row, f"Send failed: {exc}", ambiguous=True)
        return
    message = answer.get("message") if isinstance(answer.get("message"), dict) else {}
    if answer.get("sent") is False:
        found = _find_in_group(db, flyer, row, started, attempts=RECONCILE_ATTEMPTS)
        if found is not None:
            _mark_sent(db, row, found.get("id"))
        else:
            _mark_failed(
                db, row, "WhatsApp didn't confirm this one - check the group before retrying.", ambiguous=True
            )
        return
    _mark_sent(db, row, message.get("id"))
    logger.info("Flyer %s sent to %s (send %s)", flyer.id, row.target_key, row.id)


# --- Batch finished -----------------------------------------------------------------------


def _plural_groups(n: int) -> str:
    return f"{n} group" if n == 1 else f"{n} groups"


def batch_summary(sent: int, failed: int) -> str:
    if failed == 0:
        return f"Flyer sent to {_plural_groups(sent)}"
    return f"Flyer: {sent} sent, {failed} failed"


def _notify_if_batch_done(db: Session, batch_id: str) -> int:
    """One push to whoever sent the batch once nothing in it is pending
    (again after a retry - the dedupe key carries the counts). Skipped in
    quiet hours. Never raises."""
    try:
        rows: List[FlyerSend] = list(db.execute(select(FlyerSend).where(FlyerSend.batch_id == batch_id)).scalars())
        if not rows or any(r.status in PENDING for r in rows):
            return 0
        requester = rows[0].requested_by_user_id
        if not requester:
            return 0
        if in_quiet_hours(utcnow().astimezone(restaurant_timezone())):
            logger.info("Flyer batch %s finished during quiet hours - no push", batch_id)
            return 0
        sent = sum(1 for r in rows if r.status == SENT)
        failed = sum(1 for r in rows if r.status == FAILED)
        flyer = db.get(Flyer, rows[0].flyer_id)
        return dispatch_alert(
            db,
            Alert(
                key=ALERT_KEY,
                dedupe_keys=[f"{ALERT_KEY}:{batch_id}:{sent}:{failed}"],
                title=batch_summary(sent, failed),
                body=flyer.title if flyer is not None else "",
                data={"flyer_id": rows[0].flyer_id, "batch_id": batch_id},
                user_ids=[requester],
            ),
        )
    except Exception:  # noqa: BLE001 - a push problem never affects sending
        logger.exception("Flyer batch %s: push failed", batch_id)
        db.rollback()
        return 0
