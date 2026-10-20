"""Jobs per store (docs/PLATFORM_CONTRACT.md 6.1, 6.2).

- ``for_each_store(fn)``: run ``fn(store)`` once per active store this node
  serves, each inside its own store context; one store's failure is logged
  and never stops the others.
- ``register_store_job(job_id, func, trigger)``: a job that runs once PER
  STORE, ``func()`` inside that store's context, with job id
  ``"<job_id>:<CODE>"``. ``trigger(store)`` is called inside the store's
  context (so it reads the store's own settings and timezone) and may
  return None (no job for that store). A deterministic per-store offset of
  0-120 s (hash of the code) spreads POS calls.
- ``reconcile_store_jobs()`` adds / removes / reschedules the per-store
  jobs on the attached schedulers (at start, every 5 minutes, and from
  ``on_store_changed``).
- ``each_store_job(func)``: wraps an interval job registered ONCE so each
  tick runs it for every store.

Capability gating is per store: a store whose POS lacks what a job needs
(``app.pos.gating.JOB_CAPABILITIES`` by function name, plus an explicit
``capability``) gets no job / is skipped.
"""

import datetime as dt
import functools
import hashlib
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, TypeVar, Union

from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.tenancy.context import store_context
from app.tenancy.errors import StoreMoved, StoreNotActive, UnknownStore
from app.tenancy.registry import StoreRef, find_store, invalidate_store, list_stores, normalize_store_code
from app.tenancy.routing import dedicated_store_codes, node_serves

logger = logging.getLogger(__name__)

T = TypeVar("T")
MAX_JITTER_SECONDS = 120
RECONCILE_EVERY_MINUTES = 5


def store_jitter_seconds(code: str) -> int:
    """0..MAX_JITTER_SECONDS, fixed for a store code."""
    digest = hashlib.sha256(normalize_store_code(code).encode()).digest()
    return int.from_bytes(digest[:4], "big") % (MAX_JITTER_SECONDS + 1)


class OffsetTrigger(BaseTrigger):
    """`inner` shifted later by `seconds` (deterministic per-store jitter)."""

    def __init__(self, inner: BaseTrigger, seconds: int):
        self.inner = inner
        self.seconds = int(seconds)

    def get_next_fire_time(self, previous_fire_time, now):
        off = dt.timedelta(seconds=self.seconds)
        prev = previous_fire_time - off if previous_fire_time is not None else None
        nxt = self.inner.get_next_fire_time(prev, now - off)
        return nxt + off if nxt is not None else None

    def __repr__(self) -> str:
        return f"<OffsetTrigger {self.seconds}s {self.inner!r}>"

    def __str__(self) -> str:
        return f"{self.inner} +{self.seconds}s"


def _trigger_signature(trigger: BaseTrigger) -> str:
    if isinstance(trigger, OffsetTrigger):
        return f"offset{trigger.seconds}:{_trigger_signature(trigger.inner)}"
    if isinstance(trigger, IntervalTrigger):
        return f"interval:{trigger.interval}"
    return repr(trigger)


# --- for_each_store ----------------------------------------------------------------------


def _served_stores(statuses: Iterable[str], hosting: Optional[str]) -> List[StoreRef]:
    if dedicated_store_codes():
        hosting = None  # a dedicated server serves its own (dedicated) stores
    return [r for r in list_stores(statuses, hosting) if node_serves(r)]


def for_each_store(
    fn: Callable[[StoreRef], T],
    *,
    statuses: Iterable[str] = ("active",),
    hosting: Optional[str] = "shared",
    stop_on_error: bool = False,
    max_workers: int = 1,
) -> Dict[str, Union[T, BaseException]]:
    """``fn(store)`` once per store (sequential unless max_workers > 1),
    inside that store's context. Returns code -> result or the exception it
    raised. ``stop_on_error`` stops at the first failure (sequential only)."""
    refs = _served_stores(statuses, hosting)
    results: Dict[str, Union[T, BaseException]] = {}

    def one(ref: StoreRef):
        with store_context(ref.code, allow_inactive=True):
            return fn(ref)

    if max_workers <= 1 or len(refs) <= 1:
        for ref in refs:
            try:
                results[ref.code] = one(ref)
            except Exception as exc:  # noqa: BLE001 - one store never stops the others
                logger.exception("Store %s: %s failed", ref.code, getattr(fn, "__name__", fn))
                results[ref.code] = exc
                if stop_on_error:
                    break
        return results
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="for_each_store") as pool:
        futures = {ref.code: pool.submit(one, ref) for ref in refs}
        for code, fut in futures.items():
            try:
                results[code] = fut.result()
            except Exception as exc:  # noqa: BLE001
                logger.exception("Store %s: %s failed", code, getattr(fn, "__name__", fn))
                results[code] = exc
    return results


# --- capability gating ---------------------------------------------------------------------


def _capabilities_needed(func: Callable, capability: Any) -> frozenset:
    from app.pos.capabilities import Capability
    from app.pos.gating import JOB_CAPABILITIES

    need = set(JOB_CAPABILITIES.get(getattr(func, "__name__", ""), frozenset()))
    if capability is not None:
        caps = capability if isinstance(capability, (set, frozenset, list, tuple)) else [capability]
        for c in caps:
            need.add(c if isinstance(c, Capability) else Capability(c))
    return frozenset(need)


def job_supported_here(func: Callable, capability: Any = None) -> bool:
    """Inside a store context: does this store's POS have what `func` needs?"""
    from app.pos.gating import usable_capabilities

    return _capabilities_needed(func, capability) <= usable_capabilities()


def each_store_job(func: Callable[[], Any], capability: Any = None) -> Callable[[], None]:
    """An interval job registered once: each run calls ``func()`` for every
    store (skipping stores whose POS lacks the capability). Keeps
    ``func.__name__`` (gating and tests read it)."""

    @functools.wraps(func)
    def run() -> None:
        def one(_ref: StoreRef):
            if not job_supported_here(func, capability):
                return None
            return func()

        for_each_store(one)

    run.each_store = True  # type: ignore[attr-defined]
    return run


def store_job_runner(func: Callable[..., Any], code: str) -> Callable[..., Any]:
    """``func`` run inside store `code`'s context; a store that is no longer
    active (or moved) is skipped with a log line."""

    @functools.wraps(func)
    def run(*args, **kwargs):
        try:
            with store_context(code):
                return func(*args, **kwargs)
        except (StoreNotActive, UnknownStore, StoreMoved) as exc:
            logger.info("Job %s skipped for store %s: %s", getattr(func, "__name__", func), code, exc)
            return None

    run.store_code = code  # type: ignore[attr-defined]
    return run


def queued_store_job_runner(job_id: str, code: str) -> Callable[[], None]:
    """Queue mode (JOB_QUEUE_MODE=queue): instead of running the job here,
    the leader's scheduler adds a ``store_job`` row for store `code`; a
    worker runs it. The dedupe key keeps at most one waiting or running
    copy per job and store (the queue's form of coalesce + max_instances=1)."""

    def run() -> None:
        from app import jobqueue

        try:
            jobqueue.enqueue("store_job", {"job_id": job_id}, code=code, dedupe_key=f"{job_id}:{code}")
        except Exception:  # noqa: BLE001 - the next fire time tries again
            logger.exception("Job %s for store %s not queued", job_id, code)

    run.__name__ = f"queue_{job_id}"
    run.store_code = code  # type: ignore[attr-defined]
    return run


def _runner(job_id: str, func: Callable[[], None], code: str) -> Callable[..., Any]:
    from app import jobqueue

    if jobqueue.mode() == "queue":
        return queued_store_job_runner(job_id, code)
    return store_job_runner(func, code)


# --- per-store jobs ------------------------------------------------------------------------


@dataclass
class StoreJobSpec:
    job_id: str
    func: Callable[[], None]
    trigger: Callable[[StoreRef], Optional[BaseTrigger]]
    capability: Any = None
    scheduler: str = "main"
    misfire_grace_time: Optional[int] = None
    coalesce: bool = True
    max_instances: int = 1
    jitter: bool = True


@dataclass
class StoreStartupSpec:
    job_id: str
    func: Callable[[], None]
    delay_seconds: float
    capability: Any = None
    scheduler: str = "main"


_specs: Dict[str, StoreJobSpec] = {}
_startup_specs: Dict[str, StoreStartupSpec] = {}
_schedulers: Dict[str, Any] = {}
_started: Dict[str, Set[str]] = {}
_reconcile_lock = threading.RLock()


def store_job_id(job_id: str, code: str) -> str:
    return f"{job_id}:{normalize_store_code(code)}"


def register_store_job(
    job_id: str,
    func: Callable[[], None],
    trigger: Callable[[StoreRef], Optional[BaseTrigger]],
    *,
    capability: Any = None,
    scheduler: str = "main",
    misfire_grace_time: Optional[int] = None,
    coalesce: bool = True,
    max_instances: int = 1,
    jitter: bool = True,
) -> None:
    """Declare a per-store job (see module doc). Registering the same id
    again replaces the declaration; ``reconcile_store_jobs`` applies it."""
    with _reconcile_lock:
        _specs[job_id] = StoreJobSpec(
            job_id, func, trigger, capability, scheduler, misfire_grace_time, coalesce, max_instances, jitter
        )


def register_store_startup_job(
    job_id: str, func: Callable[[], None], delay_seconds: float, *, capability: Any = None, scheduler: str = "main"
) -> None:
    """A one-off job run `delay_seconds` (+ the store's offset) after the
    scheduler first sees each store (process start, or a store approved
    later) - the multi-mode form of the M2 startup catch-ups."""
    with _reconcile_lock:
        _startup_specs[job_id] = StoreStartupSpec(job_id, func, delay_seconds, capability, scheduler)


def unregister_store_jobs(scheduler: Optional[str] = None) -> None:
    with _reconcile_lock:
        for key in [k for k, s in _specs.items() if scheduler is None or s.scheduler == scheduler]:
            del _specs[key]
        for key in [k for k, s in _startup_specs.items() if scheduler is None or s.scheduler == scheduler]:
            del _startup_specs[key]


def store_job_specs(scheduler: Optional[str] = None) -> List[StoreJobSpec]:
    return [s for s in _specs.values() if scheduler is None or s.scheduler == scheduler]


def attach_scheduler(name: str, scheduler: Any) -> None:
    """The scheduler that runs the per-store jobs declared for `name`."""
    with _reconcile_lock:
        _schedulers[name] = scheduler
        _started.setdefault(name, set())


def detach_scheduler(name: Optional[str] = None) -> None:
    with _reconcile_lock:
        for key in [name] if name is not None else list(_schedulers):
            _schedulers.pop(key, None)
            _started.pop(key, None)


def attached_schedulers() -> Dict[str, Any]:
    return dict(_schedulers)


def _split_store_job_id(job_id: str):
    base, sep, code = job_id.rpartition(":")
    return (base, code) if sep else (None, None)


def reconcile_store_jobs() -> Dict[str, Dict[str, int]]:
    """Make the attached schedulers hold exactly one job per (declared job,
    active store this node serves). Returns per scheduler counts of
    added / removed / rescheduled jobs."""
    with _reconcile_lock:
        stats: Dict[str, Dict[str, int]] = {
            n: {"added": 0, "removed": 0, "rescheduled": 0, "startup": 0} for n in _schedulers
        }
        if not _schedulers:
            return stats
        refs = _served_stores(("active",), "shared")
        desired: Dict[str, Dict[str, tuple]] = {n: {} for n in _schedulers}
        startups: Dict[str, Dict[str, tuple]] = {n: {} for n in _schedulers}
        failed_codes: Set[str] = set()
        for ref in refs:
            try:
                with store_context(ref.code):
                    for spec in _specs.values():
                        if spec.scheduler not in _schedulers:
                            continue
                        if not job_supported_here(spec.func, spec.capability):
                            continue
                        trig = spec.trigger(ref)
                        if trig is None:
                            continue
                        if spec.jitter:
                            trig = OffsetTrigger(trig, store_jitter_seconds(ref.code))
                        desired[spec.scheduler][store_job_id(spec.job_id, ref.code)] = (trig, spec, ref.code)
                    for sspec in _startup_specs.values():
                        if sspec.scheduler not in _schedulers or ref.code in _started[sspec.scheduler]:
                            continue
                        if not job_supported_here(sspec.func, sspec.capability):
                            continue
                        startups[sspec.scheduler][store_job_id(sspec.job_id, ref.code)] = (sspec, ref.code)
            except Exception:  # noqa: BLE001 - keep that store's jobs as they are
                logger.exception("Store %s: per-store jobs not reconciled", ref.code)
                failed_codes.add(ref.code)
        now = dt.datetime.now(dt.timezone.utc)
        for name, sched in _schedulers.items():
            existing = {}
            for job in sched.get_jobs():
                base, code = _split_store_job_id(job.id)
                if base in _specs:
                    existing[job.id] = (job, code)
            for jid, (job, code) in existing.items():
                if jid not in desired[name] and code not in failed_codes:
                    sched.remove_job(jid)
                    stats[name]["removed"] += 1
            for jid, (trig, spec, code) in desired[name].items():
                have = existing.get(jid)
                if have is None:
                    kwargs = {}
                    if spec.misfire_grace_time is not None:
                        kwargs["misfire_grace_time"] = spec.misfire_grace_time
                    sched.add_job(
                        _runner(spec.job_id, spec.func, code),
                        trigger=trig,
                        id=jid,
                        replace_existing=True,
                        coalesce=spec.coalesce,
                        max_instances=spec.max_instances,
                        **kwargs,
                    )
                    stats[name]["added"] += 1
                elif _trigger_signature(have[0].trigger) != _trigger_signature(trig):
                    sched.reschedule_job(jid, trigger=trig)
                    stats[name]["rescheduled"] += 1
            for jid, (sspec, code) in startups[name].items():
                delay = float(sspec.delay_seconds) + store_jitter_seconds(code)
                sched.add_job(
                    _runner(sspec.job_id, sspec.func, code),
                    trigger=DateTrigger(run_date=now + dt.timedelta(seconds=delay)),
                    id=jid,
                    replace_existing=True,
                )
                stats[name]["startup"] += 1
            _started[name].update(code for code in {r.code for r in refs} - failed_codes)
        return stats


def rerun_store_startup_jobs(code: str, *, _remote: bool = False) -> None:
    """Run store `code`'s startup catch-ups again (once, after the usual
    delay): its POS keys were just saved in Settings > Integrations - the
    multi-mode moment a single-store server has at the restart after its
    .env changed. Other stores are untouched. With several processes
    (LIVE_UPDATES_MODE=postgres) the scheduler leader is told too."""
    norm = normalize_store_code(code)
    if not _remote:
        _publish("store_startup", norm)
    with _reconcile_lock:
        for seen in _started.values():
            seen.discard(norm)
    try:
        reconcile_store_jobs()
    except Exception:  # noqa: BLE001 - the 5-minute reconcile retries
        logger.exception("Store %s: startup jobs not rescheduled", norm)


def on_store_changed(code: str, *, _remote: bool = False) -> None:
    """Track C calls this after approve / suspend / resume / archive /
    settings, timezone or POS changes: drops the cached registry row and
    settings, clears the store's caches and engine when it is no longer
    served here, and re-reconciles the per-store jobs."""
    from app.tenancy.cache import clear_tenant_caches
    from app.tenancy.engines import dispose_engine
    from app.tenancy.settings import invalidate_store_settings

    norm = normalize_store_code(code)
    if not _remote:
        _publish("store_changed", norm)
    invalidate_store(norm)
    invalidate_store_settings(norm)
    ref = find_store(norm)
    if ref is None or ref.status != "active" or not node_serves(ref):
        clear_tenant_caches(norm)
        dispose_engine(norm)
    else:
        # Settings may have changed (timezone, POS): cached values built from
        # the old ones (connectors, tokens) are dropped too.
        from app.pos import registry as pos_registry

        pos_registry.reset_cache(norm)
    try:
        reconcile_store_jobs()
    except Exception:  # noqa: BLE001 - the 5-minute reconcile retries
        logger.exception("Store %s: job reconcile after a change failed", norm)


# --- other processes (website-ready C4, app/livebus.py) ------------------------------------


def _publish(event: str, code: str) -> None:
    try:
        from app import livebus

        livebus.publish(event, code)
    except Exception:  # noqa: BLE001 - the 5-minute reconcile is the safety net
        logger.debug("store change not published", exc_info=True)


def _remote_store_changed(code: Optional[str], _data: dict) -> None:
    if code:
        on_store_changed(code, _remote=True)


def _remote_store_startup(code: Optional[str], _data: dict) -> None:
    if code:
        rerun_store_startup_jobs(code, _remote=True)


def _subscribe_remote() -> None:
    from app import livebus

    livebus.subscribe("store_changed", _remote_store_changed)
    livebus.subscribe("store_startup", _remote_store_startup)


_subscribe_remote()
