"""Per-user "hide sales numbers" (users.hide_sales, migration 0033).

An admin can tick "Hide sales numbers" for a non-admin (Users page / the
app's Manage Users). That person then never sees what the store sold:

- Endpoints whose whole point is sales (Items Sold, its CSV, the sales
  CSV/PDF export) answer 403 with SALES_HIDDEN_DETAIL.
- Mixed responses keep working but lose their money fields (open orders'
  `total_amount`, the weekly sales breakdown's `revenue`), set to null so
  installed app builds (which read them as `num?`) still parse.
- The weekly digest is written without its sales section and the Ask bot
  loses its items-sold tool and politely refuses sales questions.
- `/me` drops `items_sold` from the effective feature list, so web and app
  (including App Store builds that don't know `hide_sales`) hide Items Sold
  and the Overview sales tiles on their own.

Where the line is: dollars sold, revenue, order totals and the Items Sold
report (units per item is the sales report) are sales. Demand/usage numbers
that drive ordering and stock work (reorder, Supplier Projection demand,
reconciliation, delivery review, Fix Issues "last sold") are operational and
stay visible - hiding them would break ordering.

An admin's own flag is ignored, like feature grants: admins always see sales.
"""

from typing import Iterable, List, Optional

from fastapi import Depends, HTTPException

from app.auth import get_current_user
from app.models import User

SALES_HIDDEN_DETAIL = "Sales numbers are hidden for your account."

# Feature keys that are nothing but sales numbers - taken out of a
# hide_sales user's effective feature list (see effective_features).
SALES_ONLY_FEATURES = ("items_sold",)

# Money fields on one open order (open_orders.py) and one weekly-sales row
# (weekly_sales.py).
OPEN_ORDER_MONEY_FIELDS = ("total_amount",)
WEEKLY_SALES_MONEY_FIELDS = ("revenue",)
# Toast's per-item "last N day sales" columns are DOLLARS (its "last N day
# orders" are the units, which stay). They ride along on inventory rows:
# Fix Issues' `product`, dead stock, the digest/Ask bot's dead-stock data.
TOAST_SALES_DOLLAR_FIELDS = ("last_7_day_sales", "last_30_day_sales", "last_90_day_sales")


def hides_sales(user: Optional[User]) -> bool:
    """True when this user must not see sales numbers. Admins never do."""
    if user is None:
        return False
    return bool(getattr(user, "hide_sales", False)) and not bool(user.is_admin)


def can_see_sales(user: Optional[User]) -> bool:
    return not hides_sales(user)


def effective_features(user: User, features: List[str]) -> List[str]:
    """The feature list a client should act on: a hide_sales user loses the
    sales-only features even if they're granted (the grant itself is kept,
    so unticking hide_sales brings them straight back)."""
    if not hides_sales(user):
        return list(features)
    return [k for k in features if k not in SALES_ONLY_FEATURES]


def require_sales_access(user: User = Depends(get_current_user)) -> User:
    """FastAPI dependency for endpoints that are only sales numbers."""
    if hides_sales(user):
        raise HTTPException(status_code=403, detail=SALES_HIDDEN_DETAIL)
    return user


def strip_sales(rows: Iterable[dict], fields: Iterable[str], user: Optional[User]) -> list:
    """Copies of `rows` with `fields` set to None when `user` hides sales;
    the rows untouched (same objects) otherwise."""
    rows = list(rows)
    if not hides_sales(user):
        return rows
    fields = tuple(fields)
    return [{**r, **{f: None for f in fields if f in r}} for r in rows]


def strip_sales_deep(obj, fields: Iterable[str], user: Optional[User]):
    """Like strip_sales, but through nested dicts/lists (e.g. a Fix Issues row's
    `product` and duplicate `variants[].product`): a copy with every `fields`
    key set to None when `user` hides sales; `obj` itself otherwise."""
    if not hides_sales(user):
        return obj
    return _null_keys(obj, frozenset(fields))


def _null_keys(obj, fields: frozenset):
    if isinstance(obj, dict):
        return {k: (None if k in fields else _null_keys(v, fields)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_null_keys(v, fields) for v in obj]
    return obj
