"""Which POS features this store has, and the gates built on that (multi-store
plan 4.5, batch MT6).

One process serves one store (plan 3.1), so the store's POS and its
capability set are process-wide, like Settings. A Toast store:
``pos_name()`` is "Toast" and ``store_capabilities()`` is
``TOAST_CAPABILITIES``, which holds every capability today's code uses - so
every gate here passes for a Toast store and nothing changes for it.

What uses it:
- ``require_capability(...)``: FastAPI dependency for a POS-only route. A
  store without the capability gets HTTP 409 with a plain-English reason
  (plan 4.3: NotSupported -> 409).
- ``features_for_store(keys)``: drops grantable features whose POS
  capability is missing (``/api/auth/me``'s allowed_features).
- ``drop_unsupported_jobs(scheduler)``: removes scheduled jobs whose POS
  capability is missing, before the scheduler starts (app/scheduler.py).
- ``pos_name()`` / ``capability_values()``: sent to the website and app in
  ``/api/app/config`` and ``/api/auth/me``.

The provider is ``settings.pos_provider`` (``POS_PROVIDER``, default "toast"),
normalised by ``app.pos.registry.provider_name``. The name and capability set
come from that provider's connector (``registry.get_connector()``:
``display_name`` / ``capabilities``); the ``PROVIDERS`` table below is only the
fallback for a provider with no registered adapter.
"""

import logging
from typing import Dict, FrozenSet, Iterable, List, Tuple

from fastapi import HTTPException

from app.config import get_settings
from app.pos import registry
from app.pos.capabilities import CLOVER_CAPABILITIES, CSV_CAPABILITIES, TOAST_CAPABILITIES, Capability

logger = logging.getLogger(__name__)

# provider -> (name shown to people, capabilities)
PROVIDERS: Dict[str, Tuple[str, FrozenSet[Capability]]] = {
    "toast": ("Toast", TOAST_CAPABILITIES),
    # No adapter in M2: these fallback entries gate pages and jobs (M2 contract 7).
    "clover": ("Clover", CLOVER_CAPABILITIES),
    "csv": ("CSV upload", CSV_CAPABILITIES),
}
DEFAULT_PROVIDER = "toast"

# Plain words for a 409 message: "<pos> doesn't provide <label>".
CAPABILITY_LABELS: Dict[Capability, str] = {
    Capability.ORDERS_BY_BUSINESS_DATE: "orders by business date",
    Capability.ORDERS_INCREMENTAL: "changed-orders sync",
    Capability.OPEN_ORDERS: "open checks",
    Capability.LIVE_TODAY_SALES: "today's live sales",
    Capability.CATALOG: "the item catalog",
    Capability.CATALOG_CHANGE_STAMP: "menu change checks",
    Capability.STOCK_LEVELS: "live stock levels",
    Capability.STOCK_NEGATIVE_COUNTS: "negative stock counts",
    Capability.ITEM_VENDORS: "item vendors",
    Capability.ITEM_COST: "item cost",
    Capability.RECEIVING_RECORDS: "receiving records",
    Capability.EMPLOYEES: "employee names",
    Capability.DAY_TOTALS: "day totals for the sales accuracy check",
    Capability.ANALYTICS_REPORTS: "Insights reports",
    Capability.CARD_FINGERPRINTS: "card fingerprints for returning customers",
    Capability.CSV_CATALOG_IMPORT: "an inventory export to upload",
    Capability.MODIFIER_PRICE_IN_PARENT: "modifier prices inside the item price",
}

# Grantable feature key (app/features.py) -> the POS capabilities it needs.
# A feature not listed works on any POS.
FEATURE_CAPABILITIES: Dict[str, FrozenSet[Capability]] = {
    "open_orders": frozenset({Capability.OPEN_ORDERS}),
    "sales_insights": frozenset({Capability.ANALYTICS_REPORTS}),
}

# Grantable feature key -> the store features (not POS capabilities; M2
# contract 1.3, app/store/settings_view.store_features) it needs.
FEATURE_STORE_FEATURES: Dict[str, FrozenSet[str]] = {
    "ask_bot": frozenset({"ai"}),
}

# Scheduled job function (__name__, app/scheduler.py) -> the POS capabilities
# it needs. tests/test_pos_gating.py checks every job the schedulers register
# is either listed here or in POS_NEUTRAL_JOB_FUNCS, so a new Toast job can't
# slip in ungated.
JOB_CAPABILITIES: Dict[str, FrozenSet[Capability]] = {
    # Toast ordersBulk per business date (sales_sync.sync_sales_for_date).
    "run_daily_sales_sync": frozenset({Capability.ORDERS_BY_BUSINESS_DATE}),
    "run_open_orders_full_scan": frozenset({Capability.OPEN_ORDERS}),
    "run_open_orders_today_refresh": frozenset({Capability.OPEN_ORDERS}),
    # Mid-day / closing open-checks summary push (refreshes from the POS first).
    "run_open_orders_summary": frozenset({Capability.OPEN_ORDERS}),
    # Toast Analytics day totals vs our stored sales.
    "run_sales_repull_job": frozenset({Capability.DAY_TOTALS}),
    "run_insights_nightly": frozenset({Capability.ANALYTICS_REPORTS}),
    "run_insights_catchup": frozenset({Capability.ANALYTICS_REPORTS}),
    # /stock + /menus snapshot and comparison.
    "run_inventory_api_snapshot": frozenset({Capability.CATALOG, Capability.STOCK_LEVELS}),
    # Hourly /menus/v2/metadata re-check.
    "run_menu_barcode_recheck": frozenset({Capability.CATALOG_CHANGE_STAMP}),
}
POS_NEUTRAL_JOB_FUNCS: FrozenSet[str] = frozenset(
    {
        "warm_fix_issues_cache",
        "run_alert_job",
        # Refreshes open checks first only when the store has OPEN_ORDERS.
        "run_morning_summary",
    }
)


def pos_provider() -> str:
    raw = getattr(get_settings(), "pos_provider", "") or ""
    return registry.provider_name(str(raw))


def _connector(provider: str):
    """The registered connector for `provider`, or None (no adapter)."""
    if provider not in registry.PROVIDERS:
        return None
    return registry.get_connector(provider)


def pos_name() -> str:
    """The POS name people see ("Toast")."""
    provider = pos_provider()
    conn = _connector(provider)
    if conn is not None and getattr(conn, "display_name", ""):
        return conn.display_name
    entry = PROVIDERS.get(provider)
    return entry[0] if entry else provider.title()


def store_capabilities() -> FrozenSet[Capability]:
    """This store's POS capabilities: its connector's own set. An unknown
    provider has none, so every POS feature is off rather than calling a POS
    the code can't talk to."""
    provider = pos_provider()
    conn = _connector(provider)
    if conn is not None:
        return frozenset(getattr(conn, "capabilities", frozenset()))
    entry = PROVIDERS.get(provider)
    return entry[1] if entry else frozenset()


# Capabilities the server can use without talking to the POS (a file upload).
OFFLINE_CAPABILITIES: FrozenSet[Capability] = frozenset({Capability.CSV_CATALOG_IMPORT})


def usable_capabilities() -> FrozenSet[Capability]:
    """What this server itself can do: the store's capabilities when its POS
    has an adapter, else only OFFLINE_CAPABILITIES. A provider with only a
    fallback entry (Clover in M2: no adapter until M3) advertises its set to
    the website and app (store_capabilities), but the server's own gates -
    routes, jobs, has_capability - must not call a POS it can't talk to."""
    have = store_capabilities()
    if _connector(pos_provider()) is not None:
        return have
    return have & OFFLINE_CAPABILITIES


def has_capability(*caps: Capability) -> bool:
    have = usable_capabilities()
    return all(c in have for c in caps)


def capability_values() -> List[str]:
    """The store's capabilities as their string values, in the enum's own
    (stable) order - what the website and app read."""
    have = store_capabilities()
    return [c.value for c in Capability if c in have]


def missing_capabilities(caps: Iterable[Capability]) -> List[Capability]:
    have = store_capabilities()
    return [c for c in caps if c not in have]


def not_supported_detail(missing: Iterable[Capability]) -> str:
    labels = [CAPABILITY_LABELS.get(c, c.value) for c in missing]
    return (
        f"Not available for this store: its point of sale ({pos_name()}) doesn't provide "
        f"{' or '.join(labels)}."
    )


def require_capability(*caps: Capability):
    """FastAPI dependency: HTTP 409 unless the store's POS has every one of
    `caps`. Always passes for a Toast store."""
    required = tuple(caps)

    def _dep() -> None:
        missing = missing_capabilities(required)
        if missing:
            raise HTTPException(status_code=409, detail=not_supported_detail(missing))
        usable = usable_capabilities()
        if any(c not in usable for c in required):
            raise HTTPException(
                status_code=409,
                detail=f"Not available for this store yet: its point of sale ({pos_name()}) isn't connected.",
            )

    return _dep


def features_for_store(keys: List[str]) -> List[str]:
    """`keys` minus the features this store's POS can't support and the ones
    whose store feature is off (e.g. the Ask bot without AI), order kept."""
    from app.store.settings_view import store_features

    have = store_capabilities()
    on = set(store_features())
    return [
        k
        for k in keys
        if FEATURE_CAPABILITIES.get(k, frozenset()) <= have and FEATURE_STORE_FEATURES.get(k, frozenset()) <= on
    ]


def require_store_feature(*features: str):
    """FastAPI dependency: HTTP 409 unless every one of `features` is on for
    this store (store features, M2 contract 1.3)."""
    required = tuple(features)

    def _dep() -> None:
        from app.store.settings_view import store_features

        on = set(store_features())
        missing = [f for f in required if f not in on]
        if missing:
            raise HTTPException(status_code=409, detail="Not available for this store.")

    return _dep


def job_func_name(job) -> str:
    func = getattr(job, "func", None)
    return getattr(func, "__name__", "") or ""


def job_supported(job) -> bool:
    need = JOB_CAPABILITIES.get(job_func_name(job), frozenset())
    return need <= usable_capabilities()


def drop_unsupported_jobs(scheduler) -> List[str]:
    """Remove (before start) every job whose POS capability this store lacks.
    Returns the removed job ids; none for a Toast store. A stand-in scheduler
    without get_jobs (some tests record add_job calls only) is left alone."""
    removed: List[str] = []
    get_jobs = getattr(scheduler, "get_jobs", None)
    if get_jobs is None:
        return removed
    for job in list(get_jobs()):
        if not job_supported(job):
            scheduler.remove_job(job.id)
            removed.append(job.id)
    if removed:
        logger.info("POS %s: scheduled jobs not registered (capability missing): %s", pos_name(), ", ".join(removed))
    return removed
