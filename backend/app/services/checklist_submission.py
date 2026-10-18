"""Daily Closing Checklists - business logic. A native replacement for two
Google Forms (see checklist_schemas.py); this app's own DB is now the sole
source of truth for both checklists' submission history. The old Forms +
Apps Script WhatsApp automation are no longer used for these two checklists
at all - this module sends the equivalent WhatsApp summary itself, via the
same Whapi account already used for Invoice Submission, just to two
different, dedicated staff groups (see config.py's
whapi_spice_town_checklist_group_id / whapi_street_kitchen_checklist_group_id).

Mirrors invoice_submission.py's established, hardened patterns on purpose
(save first, notify after; a WhapiAmbiguousTimeout is reconciled against
Whapi's own message history before ever being reported as a confirmed
failure) rather than inventing a second, less battle-tested way of doing
the same kind of send.
"""

import datetime as dt
import hashlib
import json
import logging
import time
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.demo import hide_demo_clause, is_demo_username
from app.models import ChecklistSubmission, User
from app.services import checklist_engine as engine
from app.services import whatsapp_groups
from app.services.checklist_schemas import (
    checklist_title,
    get_version_schema,
    items_schema,
    live_version,
    submission_version,
    summary_details,
    whatsapp_group,
)
from app.team import media as media_svc
from app.team.models import TeamMedia
from app.services.whapi_client import (
    WhapiAmbiguousTimeout,
    WhapiNotConfigured,
    find_sent_message,
    send_whatsapp_message,
)
from app.timeutil import restaurant_timezone, utcnow

logger = logging.getLogger(__name__)

# A double-tap on Submit, or a client retry after a slow/ambiguous response,
# submitted again with byte-identical answers within this window is treated
# as the same submission (the existing row is returned, nothing new is
# inserted, no second WhatsApp message goes out) - genuine "safe replay"
# handling, not a business rule about how many times someone may close in a
# day (a real second submission, even seconds later, is accepted as new the
# moment its answers differ even slightly).
DUPLICATE_WINDOW_MINUTES = 5


class ChecklistValidationError(Exception):
    """Raised when submitted answers fail validate_answers() - missing a
    required item, or a value that isn't one of that item's real choices.
    """


# Photos answering a file_upload question are team media (uploaded through
# POST /api/checklists/media) attached to the submission: visible to admins
# and to whoever submitted it.
MEDIA_OWNER_TYPE = "checklist"
UPDATE_APP_MESSAGE = "Update the app to fill this checklist"
STALE_FORM_MESSAGE = (
    "This checklist was changed since you opened it. Go back, open it again and submit the new version."
)


class ChecklistStaleVersionError(ChecklistValidationError):
    """The answers were filled on an older version of the checklist and don't
    fit the current one (the router answers 409). Answers that still fit the
    current version are accepted and stored against it - item ids are stable
    across versions, so nothing is lost by that."""


# submitted_by_user_id of a public-link submission (no account). Real user
# ids start at 1. The API returns it as is: app build 5 parses this field as
# a non-null int, so null would break its whole history list.
PUBLIC_SUBMITTER_USER_ID = 0


class PublicSubmitter:
    """Who filled a checklist on the logged-out public page
    (services/checklist_public.py): the link and the cleaned name."""

    def __init__(self, link_id: int, name: str, media_owner: str, media_keys: Optional[Dict[str, str]] = None):
        self.link_id = link_id
        self.name = name
        # TeamMedia.owner_type the link's pending uploads carry
        self.media_owner = media_owner
        # {media id: key from the upload response}: proves the uploader
        self.media_keys = media_keys or {}


def is_public(entry: ChecklistSubmission) -> bool:
    return bool(entry.via_public_link)


def can_view_submission(user: User, entry: ChecklistSubmission) -> bool:
    """Admins: everything. Others with Daily Checklists: their own, plus
    every public-link submission (the people who fill those have no account,
    so nobody would see them otherwise). Same rule for list, detail,
    Overview and the photos."""
    if user.is_admin or entry.submitted_by_user_id == user.id:
        return True
    from app.features import user_has_feature

    return is_public(entry) and user_has_feature(user, "daily_checklists")


def _can_see_checklist_media(db: Session, user: User, submission_id: int) -> bool:
    entry = db.get(ChecklistSubmission, submission_id)
    return entry is not None and can_view_submission(user, entry)


media_svc.register_media_access(MEDIA_OWNER_TYPE, _can_see_checklist_media)


def _needs_newer_app(checklist_type: str, version: int, answers: dict) -> bool:
    """True when the version has sections only a newer app can show
    (min_app_build) and the submission has no answer for any of them - i.e.
    it came from an app that couldn't display those questions."""
    served = get_version_schema(checklist_type, version)
    if not served.get("min_app_build"):
        return False
    keys = engine.newer_app_keys(served)
    return bool(keys) and not any(k in answers for k in keys)


def _check_media(db: Session, user: Optional[User], ids: List[int], public: Optional["PublicSubmitter"] = None) -> List[TeamMedia]:
    import hmac

    rows = []
    for media_id in ids:
        media = db.get(TeamMedia, media_id)
        if public is not None:
            # Only this link's own, not yet used uploads, and only with the
            # key its upload response gave (so one visitor can't attach
            # another visitor's photo by guessing its id).
            key = public.media_keys.get(str(media_id))
            ok = (
                media is not None
                and media.uploaded_by_user_id is None
                and media.owner_type == public.media_owner
                and media.owner_id == public.link_id
                and isinstance(key, str)
                and hmac.compare_digest(key.encode("utf-8"), media.token.encode("utf-8"))
            )
        else:
            ok = media is not None and media.uploaded_by_user_id == user.id and media.owner_type is None
        if not ok:
            raise ChecklistValidationError("Unknown photo - upload it again.")
        rows.append(media)
    return rows


def hash_answers(checklist_type: str, user_id, answers: dict) -> str:
    canonical = json.dumps(answers, sort_keys=True, separators=(",", ":"))
    raw = f"{checklist_type}|{user_id}|{canonical}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _find_recent_duplicate(
    db: Session, user_id: int, checklist_type: str, answers_hash: str
) -> Optional[ChecklistSubmission]:
    # A public row's hash covers the link and the name (see create_submission),
    # so two people on the same link never collide.
    cutoff = utcnow() - dt.timedelta(minutes=DUPLICATE_WINDOW_MINUTES)
    stmt = (
        select(ChecklistSubmission)
        .where(
            ChecklistSubmission.submitted_by_user_id == user_id,
            ChecklistSubmission.checklist_type == checklist_type,
            ChecklistSubmission.answers_hash == answers_hash,
            ChecklistSubmission.created_at >= cutoff,
        )
        .order_by(ChecklistSubmission.created_at.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def _fits_older_version(checklist_type: str, below: int, answers: dict) -> bool:
    """True when the answers are valid for a version before `below` - an app
    or browser tab that loaded the checklist before the latest publish."""
    for version in range(below - 1, max(below - 6, 0), -1):
        try:
            engine.validate_answers(items_schema(checklist_type, version), answers)
        except ValueError:
            continue
        return True
    return False


def create_submission(
    db: Session,
    user: Optional[User],
    checklist_type: str,
    answers: dict,
    client_version: Optional[int] = None,
    public: Optional[PublicSubmitter] = None,
) -> ChecklistSubmission:
    """Validates and stores against the CURRENT published version, read from
    the database (never a cached one - see checklist_schemas.live_version).

    `client_version` is the version the form was loaded with (the website
    sends it; app build 5 doesn't). Answers made on an older version are
    accepted when they're valid for the current one; when they aren't, the
    submitter gets ChecklistStaleVersionError (409, "open it again") instead
    of a confusing "missing answer" for a question they never saw."""
    # Raises ValueError on an unknown (or inactive / unpublished) type.
    version = live_version(db, checklist_type)
    schema = items_schema(checklist_type, version)
    # Stored (and flagged / branched) with the definition's own spelling of
    # each choice, e.g. "Yes " on a definition saved before values were trimmed.
    answers = engine.canonical_answers(schema, answers)
    try:
        engine.validate_answers(schema, answers)
    except ValueError as exc:
        if client_version is not None and client_version < version:
            raise ChecklistStaleVersionError(STALE_FORM_MESSAGE)
        if _needs_newer_app(checklist_type, version, answers):
            raise ChecklistValidationError(UPDATE_APP_MESSAGE)
        if client_version is None and _fits_older_version(checklist_type, version, answers):
            raise ChecklistStaleVersionError(STALE_FORM_MESSAGE)
        raise ChecklistValidationError(str(exc))

    # Everything below is the same for an in-app and a public-link
    # submission; only who it's from differs.
    if public is not None:
        user_id = PUBLIC_SUBMITTER_USER_ID
        name = public.name
        answers_hash = hash_answers(checklist_type, f"public:{public.link_id}:{name.casefold()}", answers)
    else:
        user_id = user.id
        name = user.username
        answers_hash = hash_answers(checklist_type, user.id, answers)
    existing = _find_recent_duplicate(db, user_id, checklist_type, answers_hash)
    if existing is not None:
        return existing

    media = _check_media(db, user, engine.media_ids(schema, answers), public)
    all_clear, issue_summary = engine.compute_status(schema, answers)

    entry = ChecklistSubmission(
        checklist_type=checklist_type,
        submitted_by_user_id=user_id,
        submitted_by_name=name,
        answers_json=json.dumps(answers),
        all_clear=all_clear,
        issue_summary=issue_summary,
        answers_hash=answers_hash,
        created_at=utcnow(),
        definition_version=version,
        via_public_link=True if public is not None else None,
        public_name=name if public is not None else None,
    )
    db.add(entry)
    db.flush()
    for row in media:
        row.owner_type = MEDIA_OWNER_TYPE
        row.owner_id = entry.id
    db.commit()
    db.refresh(entry)
    return entry


def group_id_for_group(group: Optional[str]) -> str:
    """A checklist's settings.whatsapp_group -> the WhatsApp chat id to post
    to ("" when the checklist has none, or the group is unknown, turned off
    or not configured). The whatsapp_groups table first; a built-in with no
    chat id of its own uses the env setting (services/whatsapp_groups.py)."""
    return whatsapp_groups.resolve_chat_id(group)


def _group_id_for(checklist_type: str) -> str:
    """The staff group a checklist's summary goes to, from its settings
    (Spice Town -> "store", both Street Kitchen checklists -> "kitchen")."""
    return group_id_for_group(whatsapp_group(checklist_type))


def is_demo_submission(entry: ChecklistSubmission) -> bool:
    """Submitted in-app by an App Review demo login (app/demo.py). A public
    link submission (user id 0) never is, whatever name was typed."""
    return bool(entry.submitted_by_user_id) and is_demo_username(entry.submitted_by_name)


def should_send_whatsapp(entry: ChecklistSubmission) -> bool:
    """A checklist with no WhatsApp group (settings.whatsapp_group null)
    never sends - its rows stay whatsapp_sent False with no error. Neither
    does a demo login's submission (saved, never sent to the staff group)."""
    if is_demo_submission(entry):
        return False
    return whatsapp_group(entry.checklist_type) is not None


def _details(entry: ChecklistSubmission) -> list:
    try:
        answers = json.loads(entry.answers_json or "{}")
    except ValueError:
        return []
    # With the version it was submitted against: an old FSA closing still
    # shows its "Chef: RK"; one after the Chef question was removed doesn't.
    return summary_details(entry.checklist_type, answers, submission_version(entry))


def _submitter_line(entry: ChecklistSubmission) -> str:
    """Who sent it. A public-link name is plain text limited to letters,
    spaces and . ' - (checklist_public.clean_name), so it can't carry
    WhatsApp formatting (* _ ~ `) or extra lines; it's cleaned again here in
    case of an older or hand-edited row."""
    if not is_public(entry):
        return entry.submitted_by_name
    from app.services.checklist_public import display_name

    return f"{display_name(entry.submitted_by_name)} (public link)"


# Answers that only repeat what the ❌ already says ("No" on "Have you turned
# off?", "Issue / Not completed") - left off the line. Anything that adds
# information ("No Stock", "AM prep required", "Above 41°F", typed text) is
# shown after the item: "❌ Paneer: No Stock".
_SELF_EVIDENT_TAGS = {"not_done", "not_turned_off"}
_SELF_EVIDENT_ANSWERS = {
    "no", "not done", "not completed", "issue", "issue / not completed", "issue/not completed",
    "i could not do", "i couldn't do", "could not do", "couldn't do", "not turned off", "not ok", "fail", "failed",
}


def _issue_line(item: dict) -> str:
    answer = (item.get("answer") or "").strip()
    show = (
        answer
        and item["tag"] not in _SELF_EVIDENT_TAGS
        and answer.casefold() not in _SELF_EVIDENT_ANSWERS
        and answer.casefold() != (item.get("label") or "").casefold()
    )
    return f"❌ {item['label']}: {answer}" if show else f"❌ {item['label']}"


def _issue_count(items: list) -> str:
    n = len(items)
    if all(i["tag"] == "not_done" for i in items):
        return f"*{n} item{'' if n == 1 else 's'} not done*"
    return f"*{n} issue{'' if n == 1 else 's'}*"


def _flagged(entry: ChecklistSubmission) -> Optional[dict]:
    """engine.flagged_groups for the version the submission was made
    against; None when that can't be worked out (unknown type / version,
    bad answers JSON) or doesn't agree with the stored status - the message
    then falls back to the stored issue_summary text."""
    try:
        answers = json.loads(entry.answers_json or "{}")
        if not isinstance(answers, dict):
            return None
        flagged = engine.flagged_groups(items_schema(entry.checklist_type, submission_version(entry)), answers)
    except Exception:  # noqa: BLE001 - the message must still go out
        logger.exception("Couldn't group checklist submission %s for WhatsApp", entry.id)
        return None
    has_flags = bool(flagged["groups"] or flagged["notes"])
    if has_flags == bool(entry.all_clear):
        return None
    return flagged


def _whatsapp_text(entry: ChecklistSubmission) -> str:
    """The staff-group summary:

        ⚠️ *Kitchen Closing - Service*
        Sadia (public link) · Oct 1, 1:40 AM

        *15 items not done*

        *Wrap Up Leftovers & Sauce Bottles*
        ❌ Discard old or mushy vegetables
        ...

        *Note from staff*
        Fryer oil low

    Only this text changed format (Oct 1) - entry.issue_summary, which the
    apps show, keeps its old "Not Done:" / "  - item (Section)" lines."""
    title = checklist_title(entry.checklist_type)
    # Local (restaurant) time; created_at is stored in UTC. The minute is
    # part of what makes the text unique for _reconcile_ambiguous_send.
    when = entry.created_at.astimezone(restaurant_timezone()).strftime("%b %-d, %-I:%M %p")
    head = [f"{'✅' if entry.all_clear else '⚠️'} *{title}*", f"{_submitter_line(entry)} · {when}"]
    # Extra "who/what" answers a checklist asks for (the old FSA closing's
    # "Chef: RK"), right under the name line.
    head.extend(f"{d['label']}: {d['value']}" for d in _details(entry))
    parts = ["\n".join(head)]

    flagged = _flagged(entry)
    if flagged is None:
        if entry.all_clear:
            parts.append("*All done - no issues.*")
        else:
            parts.append(entry.issue_summary or "*Issues reported.*")
        return "\n\n".join(parts)

    items = [i for g in flagged["groups"] for i in g["items"]]
    if items:
        parts.append(_issue_count(items))
        for group in flagged["groups"]:
            lines = [f"*{group['heading']}*"] if group["heading"] else []
            lines.extend(_issue_line(i) for i in group["items"])
            parts.append("\n".join(lines))
    elif entry.all_clear:
        parts.append("*All done - no issues.*")
    else:
        # Only the staff note was flagged: every task is done.
        parts.append("*All tasks done - see note.*")
    if flagged["notes"]:
        parts.append("*Note from staff*\n" + "\n\n".join(flagged["notes"]))
    return "\n\n".join(parts)


# Same reconciliation shape as invoice_submission.py's own - see that
# module's comment for why: a timed-out POST isn't proof the message never
# arrived, only that Whapi didn't confirm it quickly enough.
_RECONCILE_ATTEMPTS = 3
_RECONCILE_DELAY_SECONDS = 4


def _reconcile_ambiguous_send(text: str, group_id: str) -> bool:
    for attempt in range(_RECONCILE_ATTEMPTS):
        if find_sent_message(text, to=group_id):
            return True
        if attempt < _RECONCILE_ATTEMPTS - 1:
            time.sleep(_RECONCILE_DELAY_SECONDS)
    return False


def _attempt_whatsapp(db: Session, entry: ChecklistSubmission) -> None:
    if not should_send_whatsapp(entry):
        return
    text = _whatsapp_text(entry)
    group_id = _group_id_for(entry.checklist_type)
    try:
        if not group_id:
            # Never fall through to send_whatsapp_message's default chat (the
            # invoice group) when this checklist's group isn't configured.
            raise WhapiNotConfigured(
                "This checklist's WhatsApp group isn't set up (or is turned off) - check Checklist builder > "
                "WhatsApp groups."
            )
        send_whatsapp_message(text, to=group_id)
        entry.whatsapp_sent = True
        entry.whatsapp_error = None
        entry.whatsapp_ambiguous = False
    except WhapiAmbiguousTimeout as exc:
        if _reconcile_ambiguous_send(text, group_id):
            entry.whatsapp_sent = True
            entry.whatsapp_error = None
            entry.whatsapp_ambiguous = False
        else:
            entry.whatsapp_sent = False
            entry.whatsapp_error = str(exc)
            entry.whatsapp_ambiguous = True
    except WhapiNotConfigured as exc:
        entry.whatsapp_sent = False
        entry.whatsapp_error = str(exc)
        entry.whatsapp_ambiguous = False
    except Exception as exc:  # noqa: BLE001 - a send failure must never break the submission
        logger.exception("WhatsApp send failed for checklist submission %s", entry.id)
        entry.whatsapp_sent = False
        entry.whatsapp_error = f"Send failed: {exc}"
        entry.whatsapp_ambiguous = False
    db.commit()


def queue_whatsapp(background_tasks, entry: ChecklistSubmission, sender=None) -> bool:
    """After a submit (in-app or public link - both call this): queue the
    WhatsApp summary, only for a genuinely new row - a replayed duplicate
    returns the existing row (create_submission) and must not send twice."""
    if not entry.whatsapp_sent and entry.whatsapp_error is None and should_send_whatsapp(entry):
        background_tasks.add_task(sender or send_checklist_notification, entry.id)
        return True
    return False


def send_checklist_notification(submission_id: int) -> None:
    """Run via FastAPI BackgroundTasks after the HTTP response already went
    back - opens its own DB session, same reasoning as
    invoice_submission.py::send_invoice_notifications (a background
    callback's request-scoped `db` is already closed by the time this runs).
    """
    db = SessionLocal()
    try:
        entry = db.get(ChecklistSubmission, submission_id)
        if entry is not None:
            _attempt_whatsapp(db, entry)
    finally:
        db.close()


def list_submissions(
    db: Session,
    user: User,
    checklist_type: Optional[str] = None,
    employee_user_id: Optional[int] = None,
    status: Optional[str] = None,
    start_date: Optional[dt.date] = None,
    end_date: Optional[dt.date] = None,
    limit: int = 200,
    include_demo: bool = False,
) -> List[ChecklistSubmission]:
    """Admin: every submission, optionally filtered. Non-admin: ONLY their
    own submissions, regardless of what `employee_user_id` is passed - this
    is the actual enforcement point (the one place every caller, mobile and
    web alike, ends up going through), not just a UI-level convenience.
    App Review demo submissions are left out for everyone but the demo user
    itself (an admin can ask for them with `include_demo`).
    """
    from sqlalchemy import or_

    from app.features import user_has_feature

    conditions = [
        hide_demo_clause(
            db, ChecklistSubmission.submitted_by_user_id, ChecklistSubmission.submitted_by_name, user, include_demo
        )
    ]
    if user.is_admin:
        if employee_user_id is not None:
            conditions.append(ChecklistSubmission.submitted_by_user_id == employee_user_id)
    elif user_has_feature(user, "daily_checklists"):
        # Their own, plus public-link submissions (see can_view_submission).
        conditions.append(
            or_(
                ChecklistSubmission.submitted_by_user_id == user.id,
                ChecklistSubmission.via_public_link.is_(True),
            )
        )
    else:
        conditions.append(ChecklistSubmission.submitted_by_user_id == user.id)

    if checklist_type is not None:
        conditions.append(ChecklistSubmission.checklist_type == checklist_type)
    if status == "all_clear":
        conditions.append(ChecklistSubmission.all_clear.is_(True))
    elif status == "issues":
        conditions.append(ChecklistSubmission.all_clear.is_(False))
    # Dates are store-local (America/New_York) days, not UTC days: a closing
    # checklist submitted at 9:08 PM EDT is already the next day in UTC, so
    # comparing against naive (UTC) midnight moved every night's closing into
    # the following day's filter. Aware bounds are converted to UTC on bind.
    tz = restaurant_timezone()
    if start_date is not None:
        conditions.append(ChecklistSubmission.created_at >= dt.datetime.combine(start_date, dt.time.min, tz))
    if end_date is not None:
        conditions.append(
            ChecklistSubmission.created_at < dt.datetime.combine(end_date + dt.timedelta(days=1), dt.time.min, tz)
        )

    stmt = (
        select(ChecklistSubmission)
        .where(*conditions)
        .order_by(ChecklistSubmission.created_at.desc())
        .limit(min(limit, 500))
    )
    return list(db.execute(stmt).scalars().all())


def get_submission_for_user(db: Session, user: User, submission_id: int) -> Optional[ChecklistSubmission]:
    """None both when the row genuinely doesn't exist AND when a non-admin
    asks for someone else's submission - the router turns either case into
    the same 404, so a direct-object-reference attempt can't even tell
    which one it hit.
    """
    entry = db.get(ChecklistSubmission, submission_id)
    if entry is None:
        return None
    if not can_view_submission(user, entry):
        return None
    return entry


def _has_file_upload(entry: ChecklistSubmission) -> bool:
    try:
        schema = items_schema(entry.checklist_type, submission_version(entry))
    except ValueError:
        return False
    return any(item.get("type") == "file_upload" for _, item in engine.iter_items(schema))


def _media(db: Optional[Session], entry: ChecklistSubmission) -> Dict[str, dict]:
    """{media_id: {id, token, url, width, height}} for photos attached to this
    submission (file_upload answers store the ids)."""
    if db is None or entry.id is None or not _has_file_upload(entry):
        return {}
    rows = db.execute(
        select(TeamMedia).where(TeamMedia.owner_type == MEDIA_OWNER_TYPE, TeamMedia.owner_id == entry.id)
    ).scalars().all()
    return {str(m.id): media_svc.media_out(m) for m in rows}


def submission_out(entry: ChecklistSubmission, db: Optional[Session] = None) -> dict:
    out = {
        "id": entry.id,
        "checklist_type": entry.checklist_type,
        "checklist_title": checklist_title(entry.checklist_type),
        "submitted_by_user_id": entry.submitted_by_user_id,
        "submitted_by_name": entry.submitted_by_name,
        "answers": json.loads(entry.answers_json),
        # Additive: e.g. [{"label": "Chef", "value": "RK"}] for the FSA
        # closing ([] for the other checklists).
        "details": _details(entry),
        "all_clear": entry.all_clear,
        "issue_summary": entry.issue_summary,
        "whatsapp_sent": entry.whatsapp_sent,
        "whatsapp_error": entry.whatsapp_error,
        "whatsapp_ambiguous": entry.whatsapp_ambiguous,
        "created_at": entry.created_at.isoformat(),
        # Additive (checklist builder): the version to render the answers
        # with - GET /api/checklists/schema/{type}?version=N.
        "definition_version": submission_version(entry),
        # Additive (public checklist link): filled on the logged-out page by
        # `public_name` (then submitted_by_user_id is 0 and
        # submitted_by_name is the same name).
        "via_public_link": is_public(entry),
        "public_name": entry.public_name if is_public(entry) else None,
    }
    media = _media(db, entry)
    if media:
        out["media"] = media
    return out
