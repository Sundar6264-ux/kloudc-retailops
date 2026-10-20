"""Plans and soft limits (docs/WEBSITE_READY_CONTRACT.md, interface I4; track B).

Every store has a plan: a ``store_plans`` row, else the platform's default
plan (platform setting ``default_plan``; p0002 gave every store that existed
before plans the ``custom`` plan). Limits per plan live here in code; the
numbers are placeholders until the owner sets real ones (owner action 4).

Limits are soft: crossing one never blocks the store's work. It shows a
notice to the store's admins (Setup > Data & usage) and a flag in the master
console. Nothing in the request path reads this module.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

CUSTOM = "custom"  # no limits; every store that existed before plans
TRIAL = "trial"
STARTER = "starter"
PRO = "pro"
PLAN_IDS = (CUSTOM, TRIAL, STARTER, PRO)
DEFAULT_PLAN = CUSTOM  # the fallback when the platform setting is not a plan id

PLAN_LABELS = {CUSTOM: "Custom", TRIAL: "Trial", STARTER: "Starter", PRO: "Pro"}

GB = 1024 ** 3

# Limit key -> (label, the usage gauge it is measured by, unit).
LIMIT_KEYS = (
    ("users", "Users", "users", "count"),
    ("items", "Items", "items", "count"),
    ("storage_bytes", "File storage", "storage_bytes", "bytes"),
)

# Placeholder numbers (owner action 4: set real limits and prices).
_LIMITS: Dict[str, Dict[str, Optional[int]]] = {
    CUSTOM: {"users": None, "items": None, "storage_bytes": None},
    TRIAL: {"users": 5, "items": 2_000, "storage_bytes": 1 * GB},
    STARTER: {"users": 15, "items": 10_000, "storage_bytes": 10 * GB},
    PRO: {"users": 60, "items": 50_000, "storage_bytes": 50 * GB},
}

NEAR_SHARE = 0.8  # "near the limit" from 80 % of it


class UnknownPlan(ValueError):
    pass


def check_plan(plan: Any) -> str:
    value = str(plan or "").strip().lower()
    if value not in PLAN_IDS:
        raise UnknownPlan(f"Unknown plan {plan!r}; use one of {', '.join(PLAN_IDS)}.")
    return value


def default_plan() -> str:
    """The plan of a store that has no plan row (new stores)."""
    try:
        from app.platform.platform_settings import DEFAULT_PLAN as KEY, get_platform_setting

        value = str(get_platform_setting(KEY) or "").strip().lower()
    except Exception:  # noqa: BLE001 - settings not loadable: the built-in default
        value = ""
    return value if value in PLAN_IDS else DEFAULT_PLAN


def plan_of(code: str) -> str:
    """The plan id of store ``code``."""
    from app.platform.data_models import StorePlan
    from app.platform.db import is_configured, platform_session

    if not is_configured():
        return CUSTOM
    with platform_session() as db:
        row = db.get(StorePlan, (code or "").strip().upper())
    if row is not None and row.plan in PLAN_IDS:
        return row.plan
    return default_plan()


def limits_of(plan: str) -> Dict[str, Optional[int]]:
    """{limit key: value or None for unlimited}."""
    return dict(_LIMITS[check_plan(plan)])


def state_of(used: Optional[int], limit: Optional[int]) -> str:
    """"ok", "near" (from 80 % up to and including the limit) or "over"."""
    if limit is None or used is None:
        return "ok"
    if used > limit:
        return "over"
    if used >= limit * NEAR_SHARE:
        return "near"
    return "ok"


def limit_states(code: str, *, metrics: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """[{"key", "label", "used", "limit", "unit", "state": "ok"|"near"|"over"}]
    for store ``code``, read from the platform database only. A ``custom``
    plan has no limits (empty list)."""
    plan = plan_of(code)
    limits = limits_of(plan)
    if all(v is None for v in limits.values()):
        return []
    if metrics is None:
        from app.platform.usage import snapshot

        metrics = snapshot(code)["metrics"]
    out = []
    for key, label, gauge, unit in LIMIT_KEYS:
        limit = limits.get(key)
        if limit is None:
            continue
        used = metrics.get(gauge)
        out.append({"key": key, "label": label, "used": used, "limit": limit, "unit": unit,
                    "state": state_of(used, limit)})
    return out


def worst_state(states: List[Dict[str, Any]]) -> str:
    order = {"ok": 0, "near": 1, "over": 2}
    worst = "ok"
    for s in states:
        if order.get(s.get("state"), 0) > order[worst]:
            worst = s["state"]
    return worst


def set_plan(db, code: str, plan: str, *, actor: str, client_ip: Optional[str] = None) -> Dict[str, Any]:
    """A master changes a store's plan (audited). ``db`` is a platform
    session; the caller's transaction is committed here."""
    from app.platform.audit import audit
    from app.platform.data_models import StorePlan
    from app.platform.models import utcnow
    from app.platform.stores import store_by_code

    plan = check_plan(plan)
    store = store_by_code(db, code)
    row = db.get(StorePlan, store.code)
    before = row.plan if row is not None else default_plan()
    if row is None:
        db.add(StorePlan(store_code=store.code, plan=plan, updated_at=utcnow(), updated_by=actor))
    elif row.plan != plan:
        row.plan, row.updated_at, row.updated_by = plan, utcnow(), actor
    if before != plan or row is None:
        audit("store.plan_changed", actor_type="platform_user", actor_name=actor, store_code=store.code,
              details={"from": before, "to": plan}, client_ip=client_ip, db=db)
    db.commit()
    return plan_view(store.code)


def plan_view(code: str, *, metrics: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    plan = plan_of(code)
    states = limit_states(code, metrics=metrics)
    return {
        "plan": plan,
        "label": PLAN_LABELS[plan],
        "limits": limits_of(plan),
        "limit_states": states,
        "state": worst_state(states),
        "plans": [{"id": p, "label": PLAN_LABELS[p], "limits": limits_of(p)} for p in PLAN_IDS],
    }


__all__ = (
    "CUSTOM", "DEFAULT_PLAN", "PLAN_IDS", "PLAN_LABELS", "PRO", "STARTER", "TRIAL", "UnknownPlan",
    "check_plan", "default_plan", "limit_states", "limits_of", "plan_of", "plan_view", "set_plan", "state_of",
    "worst_state",
)
