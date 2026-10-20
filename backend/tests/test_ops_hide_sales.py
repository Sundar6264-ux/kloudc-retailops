"""Hide sales (ops fixes D): an admin-set per-user option that keeps sales
numbers out of that person's web dashboard and app (app/sales_access.py).

Covers: only an admin can set/clear it, admins are never affected, /me drops
Items Sold for a hide_sales user while the admin list keeps the stored grant,
each sales endpoint is blocked or stripped for that user and unchanged for
everyone else."""

import datetime as dt
import json
import types

import pytest

from app.models import OpenOrderCache, Order, User
from app.timeutil import restaurant_today, utcnow
from tests.conftest import client_for, make_user

PAST = "2026-09-01"
ALL_FEATURES = ["overview", "open_orders", "items_sold", "inventory_reports", "supplier_projection", "ask_bot"]
HIDDEN = "Sales numbers are hidden for your account."


@pytest.fixture
def seller(db):
    """A non-admin with every sales-related grant, sales visible."""
    return client_for(db, make_user(db, "cashier", features=ALL_FEATURES))


@pytest.fixture
def hider(db):
    """Same grants, but "Hide sales numbers" is on."""
    user = make_user(db, "stocker", features=ALL_FEATURES)
    user.hide_sales = True
    db.commit()
    return client_for(db, user)


@pytest.fixture
def flagged_admin(db):
    """An admin whose own flag is (somehow) set - must be ignored."""
    user = make_user(db, "owner", is_admin=True)
    user.hide_sales = True
    db.commit()
    return client_for(db, user)


def _sale(db, name="Samosa", qty=3, net=9.0, date=PAST, guid="s1"):
    db.add(
        Order(
            toast_order_guid="o-" + guid,
            toast_check_guid="c-" + guid,
            toast_selection_guid=guid,
            business_date=dt.date.fromisoformat(date),
            item_guid="item-" + name,
            item_name=name,
            quantity=qty,
            unit_price=net / qty,
            net_price=net,
        )
    )
    db.commit()


def _open_order(db, total=42.5):
    db.add(
        OpenOrderCache(
            toast_order_guid="g1",
            business_date=dt.date(2026, 9, 1),
            display_number="17",
            opened_at=utcnow() - dt.timedelta(hours=3),
            server_name="Ravi",
            total_amount=total,
            num_checks=1,
            line_items_json=json.dumps([{"name": "Samosa", "quantity": 2}]),
        )
    )
    db.commit()


# --- setting the flag ---------------------------------------------------------------


def test_admin_sets_and_clears_hide_sales(db, admin_client):
    target = make_user(db, "newbie", features=["items_sold"])
    r = admin_client.patch(f"/api/auth/users/{target.id}/hide-sales", json={"hide_sales": True})
    assert r.status_code == 200, r.text
    assert r.json()["hide_sales"] is True
    listed = {u["username"]: u for u in admin_client.get("/api/auth/users").json()}
    assert listed["newbie"]["hide_sales"] is True
    # The admin list keeps the stored grant, so unticking restores it as it was.
    assert "items_sold" in listed["newbie"]["allowed_features"]

    r = admin_client.patch(f"/api/auth/users/{target.id}/hide-sales", json={"hide_sales": False})
    assert r.json()["hide_sales"] is False
    db.expire_all()
    assert db.get(User, target.id).hide_sales is False


def test_create_user_with_hide_sales(admin_client):
    r = admin_client.post(
        "/api/auth/users",
        json={"username": "counter", "password": "longpassword", "allowed_features": ["items_sold"], "hide_sales": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["hide_sales"] is True
    # Default stays off.
    r = admin_client.post("/api/auth/users", json={"username": "plain", "password": "longpassword"})
    assert r.json()["hide_sales"] is False
    # An admin can't be created with sales hidden - admins always see sales.
    r = admin_client.post(
        "/api/auth/users", json={"username": "boss2", "password": "longpassword", "is_admin": True, "hide_sales": True}
    )
    assert r.json()["hide_sales"] is False


def test_admin_target_rejected(db, admin_client):
    other_admin = make_user(db, "boss3", is_admin=True)
    r = admin_client.patch(f"/api/auth/users/{other_admin.id}/hide-sales", json={"hide_sales": True})
    assert r.status_code == 400
    assert admin_client.patch(f"/api/auth/users/{other_admin.id}/hide-sales", json={"hide_sales": False}).status_code == 200


def test_non_admin_cannot_set(db, seller, hider):
    r = seller.patch(f"/api/auth/users/{seller.user.id}/hide-sales", json={"hide_sales": True})
    assert r.status_code == 403
    # Nor clear their own.
    r = hider.patch(f"/api/auth/users/{hider.user.id}/hide-sales", json={"hide_sales": False})
    assert r.status_code == 403
    db.expire_all()
    assert db.get(User, hider.user.id).hide_sales is True
    assert db.get(User, seller.user.id).hide_sales is False


def test_unknown_user_404(admin_client):
    assert admin_client.patch("/api/auth/users/9999/hide-sales", json={"hide_sales": True}).status_code == 404


# --- /me ------------------------------------------------------------------------------


def test_me_effective_features(seller, hider, flagged_admin):
    me = hider.get("/api/auth/me").json()
    assert me["hide_sales"] is True
    assert "items_sold" not in me["allowed_features"]  # old app builds hide Items Sold on their own
    assert "open_orders" in me["allowed_features"]

    me = seller.get("/api/auth/me").json()
    assert me["hide_sales"] is False
    assert "items_sold" in me["allowed_features"]

    me = flagged_admin.get("/api/auth/me").json()
    assert me["hide_sales"] is False
    assert "items_sold" in me["allowed_features"]


# --- sales-only endpoints: 403 --------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        f"/api/sales/items-sold?start={PAST}&end={PAST}",
        f"/api/sales/items-sold?date={PAST}",
        f"/api/sales/items-sold/export?start={PAST}&end={PAST}",
        f"/api/sales/export?start={PAST}&end={PAST}&format=csv",
        f"/api/sales/export?start={PAST}&end={PAST}&format=pdf",
    ],
)
def test_sales_endpoints_blocked(db, url, seller, hider, flagged_admin, admin_client):
    _sale(db)
    r = hider.get(url)
    assert r.status_code == 403
    assert r.json()["detail"] == HIDDEN
    for client in (seller, flagged_admin, admin_client):
        assert client.get(url).status_code == 200, url


def test_items_sold_unchanged_for_others(db, seller):
    _sale(db)
    body = seller.get(f"/api/sales/items-sold?start={PAST}&end={PAST}").json()
    assert body["total_revenue"] == 9.0
    assert body["items"][0]["revenue"] == 9.0


# --- mixed endpoints: money stripped --------------------------------------------------


def test_open_orders_all_time_total_stripped(db, seller, hider, flagged_admin):
    _open_order(db)
    hidden = hider.get("/api/orders/open/all-time").json()
    assert hidden["count"] == 1
    order = hidden["orders"][0]
    assert order["total_amount"] is None
    assert order["display_number"] == "17" and order["line_items"] == [{"name": "Samosa", "quantity": 2}]

    for client in (seller, flagged_admin):
        assert client.get("/api/orders/open/all-time").json()["orders"][0]["total_amount"] == 42.5


def test_open_orders_day_total_stripped(monkeypatch, seller, hider):
    from app.routers import orders as orders_router

    fake = [{"guid": "g", "display_number": "5", "total_amount": 12.0, "line_items": []}]
    monkeypatch.setattr(orders_router, "get_open_orders", lambda d, employee_guid=None: [dict(o) for o in fake])
    assert hider.get("/api/orders/open").json()["orders"][0]["total_amount"] is None
    assert seller.get("/api/orders/open").json()["orders"][0]["total_amount"] == 12.0
    assert fake[0]["total_amount"] == 12.0  # never mutates the service's rows


def test_open_orders_export_total_blank(db, seller, hider):
    _open_order(db)
    hidden = hider.get("/api/orders/open/export?mode=all_time").text.splitlines()
    header = hidden[0].split(",")
    total_col = header.index("Total")
    assert hidden[1].split(",")[total_col] == ""
    shown = seller.get("/api/orders/open/export?mode=all_time").text.splitlines()
    assert shown[1].split(",")[total_col] == "42.5"


def test_item_weekly_sales_revenue_stripped(db, seller, hider):
    _sale(db, name="Samosa", qty=3, net=9.0, date=PAST)
    url = "/api/ordering/item-weekly-sales?item_id=item-Samosa&start=2026-08-25&end=2026-09-08"
    weeks = hider.get(url).json()["weeks"]
    assert weeks and weeks[0]["revenue"] is None
    assert weeks[0]["quantity"] == 3  # demand stays - ordering needs it
    assert seller.get(url).json()["weeks"][0]["revenue"] == 9.0


def test_dead_stock_toast_sales_dollars_stripped(monkeypatch, seller, hider):
    from app.routers import reports as reports_router

    rows = [{"name": "Old Jar", "on_hand_qty": 4, "inventory_value": 50.0, "last_90_day_sales": 12.5}]
    monkeypatch.setattr(reports_router, "get_dead_stock", lambda db: [dict(r) for r in rows])
    item = hider.get("/api/reports/dead-stock").json()["items"][0]
    assert item["last_90_day_sales"] is None
    assert item["on_hand_qty"] == 4 and item["inventory_value"] == 50.0  # stock value is not sales
    assert seller.get("/api/reports/dead-stock").json()["items"][0]["last_90_day_sales"] == 12.5

    csv_rows = hider.get("/api/reports/dead-stock/export").text.splitlines()
    col = csv_rows[0].split(",").index("Sales $ (90 days)")
    assert csv_rows[1].split(",")[col] == ""


# --- weekly digest and Ask bot ----------------------------------------------------------


class _FakeClaude:
    """Stands in for the Anthropic client: records what was sent."""

    def __init__(self):
        self.calls = []
        self.messages = types.SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs):
        from app.services.weekly_digest import WeeklyDigestNarrative

        self.calls.append(kwargs)
        text = "x"
        parsed = WeeklyDigestNarrative(
            headline=text, sales="Sales were $9", reorder=text, money_at_risk=text,
            vendor_savings=text, reconciliation=text, housekeeping=text,
        )
        return types.SimpleNamespace(parsed_output=parsed)


@pytest.fixture
def fake_claude(monkeypatch):
    from app.services import weekly_digest

    fake = _FakeClaude()
    monkeypatch.setattr(weekly_digest, "get_anthropic_client", lambda: fake)
    # The digest pulls every report; they aren't what's being tested here.
    for name in ("compute_reorder_candidates", "get_dead_stock", "get_vendor_price_comparison",
                 "get_reconciliation", "get_missing_barcodes", "get_invalid_barcodes"):
        monkeypatch.setattr(weekly_digest, name, lambda *a, **k: [])
    return fake


def test_weekly_digest_without_sales(db, fake_claude, monkeypatch, seller, hider):
    from app.services import weekly_digest

    monkeypatch.setattr(
        weekly_digest, "get_dead_stock", lambda db: [{"name": "Old Jar", "inventory_value": 50.0, "last_90_day_sales": 12.5}]
    )
    _sale(db, date=restaurant_today().isoformat())
    body = hider.post("/api/weekly-digest/generate").json()
    assert "sales" not in body["data"]
    assert body["data"]["dead_stock"] == [{"name": "Old Jar", "inventory_value": 50.0}]
    assert body["narrative"]["sales"] is None
    sent = fake_claude.calls[-1]
    assert '"sales"' not in sent["messages"][0]["content"]
    assert "not shown sales figures" in sent["system"]

    body = seller.post("/api/weekly-digest/generate").json()
    assert body["data"]["sales"]["this_week"]["revenue"] == 9.0
    assert body["data"]["dead_stock"][0]["last_90_day_sales"] == 12.5
    assert body["narrative"]["sales"] == "Sales were $9"
    assert "not shown sales figures" not in fake_claude.calls[-1]["system"]


def test_ask_bot_passes_flag(monkeypatch, seller, hider, flagged_admin):
    from app.routers import ask as ask_router

    seen = []

    def fake_ask(db, question, history, include_sales=True):
        seen.append(include_sales)
        return {"answer": "ok"}

    monkeypatch.setattr(ask_router, "ask", fake_ask)
    for client in (hider, seller, flagged_admin):
        assert client.post("/api/ask", json={"question": "sales yesterday?"}).status_code == 200
    assert seen == [False, True, True]


def test_ask_bot_has_no_sales_tool_when_hidden(db):
    from app.services.ask_bot import NO_SALES_PROMPT, _build_tools

    def names(tools):
        return {getattr(t, "name", None) or t.to_dict()["name"] for t in tools}

    assert "items_sold" in names(_build_tools(db))
    hidden = names(_build_tools(db, include_sales=False))
    assert "items_sold" not in hidden and "reorder_candidates" in hidden
    assert "hidden for this user's account" in NO_SALES_PROMPT


# --- helper ---------------------------------------------------------------------------


def test_helper_rules(db):
    from app.sales_access import can_see_sales, effective_features, strip_sales

    staff = make_user(db, "a", features=["items_sold", "overview"])
    assert can_see_sales(staff) and can_see_sales(None)
    staff.hide_sales = True
    assert not can_see_sales(staff)
    assert effective_features(staff, ["items_sold", "overview"]) == ["overview"]
    rows = [{"total_amount": 5, "x": 1}]
    assert strip_sales(rows, ["total_amount", "missing"], staff) == [{"total_amount": None, "x": 1}]
    assert rows[0]["total_amount"] == 5
    boss = make_user(db, "b", is_admin=True)
    boss.hide_sales = True
    assert can_see_sales(boss)
    assert strip_sales(rows, ["total_amount"], boss) is not None and strip_sales(rows, ["total_amount"], boss)[0]["total_amount"] == 5


def test_strip_sales_deep_nested(db):
    from app.sales_access import TOAST_SALES_DOLLAR_FIELDS, strip_sales_deep

    staff = make_user(db, "c")
    staff.hide_sales = True
    row = {
        "name": "Dal",
        "product": {"last_30_day_sales": 62.16, "last_30_day_units": 14},
        "variants": [{"product": {"last_7_day_sales": 9.0, "price": 4.49}}],
    }
    out = strip_sales_deep([row], TOAST_SALES_DOLLAR_FIELDS, staff)[0]
    assert out["product"] == {"last_30_day_sales": None, "last_30_day_units": 14}
    assert out["variants"][0]["product"] == {"last_7_day_sales": None, "price": 4.49}
    assert row["product"]["last_30_day_sales"] == 62.16  # input untouched
    staff.hide_sales = False
    assert strip_sales_deep([row], TOAST_SALES_DOLLAR_FIELDS, staff)[0] is row


# --- Fix Issues (/api/issues/*): Toast's "last N day sales" dollars ------------------


def _issue_snaps(db):
    """Today's upload: one item with no barcode (Missing barcodes) and two
    priced rows sharing a name (Duplicates), all carrying Toast's dollar and
    unit sales columns."""
    from app.models import InventorySnapshot

    today = restaurant_today()
    common = dict(
        snapshot_date=today, category_group="RETAIL", category="Snacks", inventory_quantity=6,
        cost=2.0, supplier="Acme", last_7_day_sales=9.0, last_30_day_sales=62.16,
        last_90_day_sales=150.0, last_7_day_orders=2, last_30_day_orders=14, last_90_day_orders=33,
    )
    db.add(InventorySnapshot(item_id="nobar", name="Plain Chips", price=4.49, barcode=None, **common))
    db.add(InventorySnapshot(item_id="dup1", name="Masala Tea", price=5.99, barcode="012345678905", **common))
    db.add(InventorySnapshot(item_id="dup2", name="Masala Tea", price=6.49, barcode="036000291452", **common))
    db.commit()


def _all_keys(obj, key):
    """Every value of `key` anywhere in a nested response."""
    if isinstance(obj, dict):
        return [v for k, v in obj.items() if k == key] + [x for v in obj.values() for x in _all_keys(v, key)]
    if isinstance(obj, list):
        return [x for v in obj for x in _all_keys(v, key)]
    return []


@pytest.mark.parametrize("issue_type", ["missing_barcode", "duplicate"])
def test_fix_issues_list_strips_toast_sales_dollars(db, issue_type, seller, hider, flagged_admin):
    _issue_snaps(db)
    hidden = hider.get(f"/api/issues/{issue_type}")
    assert hidden.status_code == 200, hidden.text
    body = hidden.json()
    assert body["open"], body
    for field in ("last_7_day_sales", "last_30_day_sales", "last_90_day_sales"):
        values = _all_keys(body, field)
        assert values and all(v is None for v in values), (field, values)
    # Units sold, prices and costs are not sales dollars - they stay.
    assert 14 in _all_keys(body, "last_30_day_units")
    assert 2.0 in _all_keys(body, "cost")
    if issue_type == "duplicate":
        variants = body["open"][0]["variants"]
        assert len(variants) == 2 and all(v["product"]["last_30_day_sales"] is None for v in variants)
        assert all(v["product"]["last_30_day_units"] == 14 for v in variants)

    for client in (seller, flagged_admin):
        shown = client.get(f"/api/issues/{issue_type}").json()
        assert 62.16 in _all_keys(shown, "last_30_day_sales")


def test_fix_issues_export_and_report_for_hidden_user(db, seller, hider):
    _issue_snaps(db)
    hidden = hider.get("/api/issues/missing_barcode/export")
    assert hidden.status_code == 200
    assert hidden.text == seller.get("/api/issues/missing_barcode/export").text  # no dollar column either way
    assert "62.16" not in hidden.text
    # Close one so the closed report and the Closed export have rows.
    r = hider.post(
        "/api/issues/close",
        json={"issue_type": "missing_barcode", "issue_keys": ["nobar"], "reason": "discontinued"},
    )
    assert r.status_code == 200, r.text
    report = hider.get("/api/issues/report")
    assert report.status_code == 200 and report.json()["days"]
    closed = hider.get("/api/issues/missing_barcode/export?status=closed")
    assert closed.status_code == 200 and "Plain Chips" in closed.text
    assert hider.get("/api/issues/summary").status_code == 200


def test_job_status_hides_sales_sync_detail(db, seller, hider):
    from app.models import JobRun
    from app.services.sales_sync import JOB_NAME

    now = utcnow()
    db.add(JobRun(job_name=JOB_NAME, started_at=now, finished_at=now, status="success",
                  detail="312 line items synced from 145 orders", business_date=dt.date(2026, 9, 1)))
    db.add(JobRun(job_name="inventory_upload", started_at=now, finished_at=now, status="success",
                  detail="1200 rows loaded"))
    db.commit()
    hidden = {j["job_name"]: j for j in hider.get("/api/jobs/status").json()}
    assert hidden[JOB_NAME]["detail"] is None and hidden[JOB_NAME]["status"] == "success"
    assert hidden["inventory_upload"]["detail"] == "1200 rows loaded"
    shown = {j["job_name"]: j for j in seller.get("/api/jobs/status").json()}
    assert shown[JOB_NAME]["detail"] == "312 line items synced from 145 orders"
