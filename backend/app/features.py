"""Canonical list of admin-grantable dashboard features (tabs/pages) and the
FastAPI dependencies used to gate access to them.

An admin always has full access to everything and is never subject to these
checks - this system exists so an admin can choose which of these a
non-admin user may access. Two things are deliberately NOT part of this
grantable list: Change Password (must always stay available to every logged
-in user - it's how they manage their own login, not a business-data
feature) and the Users admin page (already a hard is_admin boundary, not
something to grant piecemeal to a non-admin).
"""

import json
from typing import List

from fastapi import Depends, HTTPException

from app.auth import get_current_user
from app.models import User

FEATURES = [
    {"key": "overview", "label": "Overview"},
    {"key": "open_orders", "label": "Open Checks"},
    {"key": "items_sold", "label": "Items Sold"},
    {"key": "reorder_candidates", "label": "Reorder Candidates (All Suppliers)"},
    {"key": "supplier_projection", "label": "Supplier Projection"},
    {"key": "vendor_calendar", "label": "Vendor Order Calendar"},
    {"key": "purchase_order_cart", "label": "Purchase Order Cart"},
    {"key": "delivery_review", "label": "Delivery Review"},
    {"key": "transfer_review", "label": "Transfer Review"},
    {"key": "inventory_reports", "label": "Inventory Reports"},
    {"key": "reconciliation", "label": "Reconciliation"},
    # PO sections page (the heading each product prints under on vendor PO
    # PDFs; routers/po_sections.py). Key kept from the old Product categories
    # page so installed app builds keep working. Never auto-granted.
    {"key": "category_edits", "label": "Edit PO sections"},
    {"key": "invoice_submission", "label": "Invoice Submission"},
    {"key": "daily_checklists", "label": "Daily Checklists"},
    {"key": "ask_bot", "label": "Ask Inventory Bot"},
    # Team hub (app/team/). Announcements need no key - everyone gets them.
    {"key": "team_tasks", "label": "Team Tasks"},
    {"key": "team_chat", "label": "Team Chat"},
    # Flyers page (routers/flyers.py): upload a flyer, send it to the
    # WhatsApp groups an admin marked Marketing. Never auto-granted; the
    # App Review demo login is refused even if ticked.
    {"key": "marketing_flyers", "label": "Marketing flyers"},
    # Insights from Toast Analytics (routers/insights.py): heatmap, channels,
    # compare, check trends, menu performance, add-ons, prep forecast,
    # returning customers; Loss watch / Odd checks are admin only on top.
    # Never auto-granted.
    {"key": "sales_insights", "label": "Sales insights"},
    {"key": "help", "label": "Help"},
]
FEATURE_KEYS = {f["key"] for f in FEATURES}


def get_allowed_features(user: User) -> List[str]:
    """Full grantable-feature list for an admin; this user's own granted
    subset otherwise. Unknown/stale keys in the stored JSON are dropped
    rather than erroring, so removing a feature later can't break login.
    """
    if user.is_admin:
        return [f["key"] for f in FEATURES]
    try:
        keys = json.loads(user.allowed_features or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(keys, list):
        return []
    return [k for k in keys if k in FEATURE_KEYS]


def user_has_feature(user: User, key: str) -> bool:
    return user.is_admin or key in get_allowed_features(user)


def require_feature(key: str):
    """FastAPI dependency: 403s unless the current user is an admin or has
    been granted this exact feature key.
    """

    def _dep(user: User = Depends(get_current_user)) -> User:
        if not user_has_feature(user, key):
            raise HTTPException(status_code=403, detail="You don't have access to this feature")
        return user

    return _dep


def require_any_feature(*keys: str):
    """Like require_feature, but passes if the user has ANY of the given
    keys - for endpoints genuinely shared by more than one page (e.g. the
    purchase-order cart is written to from both the Supplier Projection page
    and the Purchase Order Cart page itself).
    """

    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.is_admin or any(k in get_allowed_features(user) for k in keys):
            return user
        raise HTTPException(status_code=403, detail="You don't have access to this feature")

    return _dep
