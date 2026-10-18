"""Oct 14 batch X1: suggested order qty on Supplier Projection and the cart's
projection context (services/order_suggestion.py).

Covers the rule (need to order for the window, rounded up, never negative,
0 = enough stock), its edge cases (no sales, negative stock, no stock count,
weighed items, multi-vendor items already in another vendor's cart, a known
Case of from the cart or the last placed order), the delivery-schedule
basis, the 28-day average on hand, and the additive API fields
(supplier-projection `suggested_order` / `order_window`, GET /cart
`?projection=1` -> per-line `projection`, nothing extra by default)."""

import datetime as dt
import json

import pytest

from app.models import CartItem, InventorySnapshot, Order, PlacedOrder, VendorSchedule
from app.services import order_suggestion as OS
from app.timeutil import utcnow
from tests.conftest import client_for, make_user

PROJ = "/api/ordering/supplier-projection"
CART = "/api/ordering/cart"
TODAY = dt.date.today()


# --------------------------------------------------------------------------
# The pure rule
# --------------------------------------------------------------------------


def _row(need=10.0, projected=13.0, on_hand=6.0, weekly=14.0, horizon=30, safety=1.0):
    return {
        "item_id": "i1",
        "avg_weekly_demand": weekly,
        "on_hand_qty": on_hand,
        "projections": {str(horizon): {"projected_demand": projected, "need_to_order": need, "safety_buffer": safety}},
    }


def test_whole_units_rounds_up_with_the_forecast_tolerance():
    assert OS.whole_units(None) is None
    assert OS.whole_units(0) == 0
    assert OS.whole_units(0.05) == 0  # under 0.1 -> enough stock
    assert OS.whole_units(-4) == 0  # never negative
    assert OS.whole_units(1.05) == 1  # a leftover of 0.1 or less is dropped
    assert OS.whole_units(1.2) == 2
    assert OS.whole_units(10) == 10
    assert OS.whole_units(float("nan")) == 0


def test_basic_suggestion_and_basis():
    s = OS.suggest(_row(), 30, avg=(9.0, 28))
    assert s["qty"] == 10 and s["units"] == 10 and s["need_units"] == 10
    assert s["enough_stock"] is False and s["stock_known"] is True
    assert s["basis"] == (
        "sold ~14/week · on hand 6 (avg 9) · no delivery schedule · suggested 10 for 1 month"
    )
    assert s["avg_on_hand"] == 9.0 and s["avg_on_hand_days"] == 28
    assert OS.compact_basis(s) == "~14/week · suggested 10 · on hand 6"


def test_no_projection_for_that_horizon_is_none():
    assert OS.suggest(_row(horizon=30), 7) is None


def test_no_sales_is_zero_enough_stock():
    s = OS.suggest(_row(need=0.0, projected=0.0, weekly=0.0, on_hand=3, horizon=14), 14)
    assert s["qty"] == 0 and s["enough_stock"] is True
    assert s["basis_parts"][0] == "no sales in the sales window"
    assert s["basis_parts"][-1] == "enough stock for 2 weeks"
    assert OS.compact_basis(s) == "no sales · enough for 2 weeks · on hand 3"


def test_negative_stock_is_counted_as_zero_and_said_so():
    # The projection already treats a negative count as 0 (need = target).
    s = OS.suggest(_row(need=13.0, projected=12.0, on_hand=-915.095), 30)
    assert s["qty"] == 13
    assert "on hand -915.1 (counted as 0)" in s["basis"]


def test_no_stock_count_suggests_projected_sales_and_says_stock_not_subtracted():
    s = OS.suggest(_row(need=None, projected=12.3, on_hand=None), 30)
    assert s["qty"] == 13 and s["stock_known"] is False and s["enough_stock"] is False
    assert "on hand not counted - stock not subtracted" in s["basis"]
    # Nothing expected to sell: 0, but not "enough stock" (we don't know).
    z = OS.suggest(_row(need=None, projected=0.0, on_hand=None, weekly=0), 30)
    assert z["qty"] == 0 and z["enough_stock"] is False
    assert z["basis_parts"][-1] == "no sales expected in 1 month - suggested 0"
    assert OS.compact_basis(z).endswith("on hand not counted")


def test_weighed_item_fractional_numbers_round_up_to_whole_units():
    # Legacy method: need = projected - on hand, fractional for a weighed item.
    s = OS.suggest(_row(need=3.25, projected=143.8, on_hand=140.55, weekly=33.6), 30)
    assert s["qty"] == 4
    assert "on hand 140.6" in s["basis"] and "sold ~34/week" in s["basis"]


def test_known_case_of_turns_units_into_whole_cases():
    s = OS.suggest(_row(need=10.0), 30, case=(12.0, "cart"))
    assert s["qty"] == 1 and s["units"] == 10 and s["case_of"] == 12.0 and s["case_of_source"] == "cart"
    assert s["basis_parts"][-1] == "suggested 1 case of 12 (12 units) for 1 month"
    s = OS.suggest(_row(need=25.0), 30, case=(12.0, "last_order"))
    assert s["qty"] == 3 and "suggested 3 cases of 12 (36 units)" in s["basis"]
    assert OS.compact_basis(s) == "~14/week · suggested 3 x 12 · on hand 6"
    # Enough stock stays 0 cases.
    s = OS.suggest(_row(need=0.0), 30, case=(12.0, "cart"))
    assert s["qty"] == 0 and s["enough_stock"]


def test_units_in_other_vendors_carts_are_subtracted_never_below_zero():
    s = OS.suggest(_row(need=10.0), 30, other_carts=[{"vendor": "RBest", "units": 4.0}])
    assert s["qty"] == 6 and s["need_units"] == 10
    assert "4 already in RBest's cart" in s["basis"]
    s = OS.suggest(_row(need=10.0), 30, other_carts=[{"vendor": "RBest", "units": 12.0}])
    assert s["qty"] == 0
    assert s["enough_stock"] is True


def test_delivery_label_in_basis():
    delivery = {"lead_days": 1, "next_delivery": "2026-10-15", "order_by": "2026-10-14",
                "label": "next delivery Thu Oct 15 (order by today)"}
    s = OS.suggest(_row(), 30, delivery=delivery)
    assert "next delivery Thu Oct 15 (order by today)" in s["basis"]
    assert s["next_delivery"] == "2026-10-15" and s["order_by"] == "2026-10-14" and s["lead_days"] == 1


# --------------------------------------------------------------------------
# Lookups
# --------------------------------------------------------------------------


def _schedule(db, vendor, weekdays, lead):
    db.add(VendorSchedule(vendor=vendor, delivery_weekdays=json.dumps(weekdays), lead_days=lead, active=True,
                          created_at=utcnow()))
    db.commit()


def test_vendor_delivery_next_orderable_delivery(db):
    _schedule(db, "Babco", [3], 1)  # Thursdays, order a day ahead
    tue = dt.date(2026, 10, 13)
    d = OS.vendor_delivery(db, "Babco", tue)
    assert d["next_delivery"] == "2026-10-15" and d["order_by"] == "2026-10-14"
    assert d["label"] == "next delivery Thu Oct 15 (order by Wed Oct 14)"
    # On Wednesday the order is due today.
    assert OS.vendor_delivery(db, "Babco", dt.date(2026, 10, 14))["label"].endswith("(order by today)")
    # Thursday itself is too late for that Thursday -> the next one.
    assert OS.vendor_delivery(db, "Babco", dt.date(2026, 10, 15))["next_delivery"] == "2026-10-22"
    assert OS.vendor_delivery(db, "Nobody", tue) is None


def test_known_case_sizes_cart_then_last_placed_order(db):
    db.add(CartItem(supplier="Babco", item_id="a", name="A", qty=2, case_of=6))
    db.add(CartItem(supplier="Babco", item_id="c", name="C", qty=2, case_of=1))  # 1 = unknown
    db.add(CartItem(supplier="Other", item_id="d", name="D", qty=2, case_of=24))  # another vendor's
    lines = [{"name": "B", "item_id": "b", "qty": 1, "case_of": 12},
             {"name": "A", "item_id": "a", "qty": 1, "case_of": 99}]
    db.add(PlacedOrder(supplier="babco ", item_count=2, items_json=json.dumps(lines), status="placed"))
    # A PDF preview isn't an order.
    db.add(PlacedOrder(supplier="Babco", item_count=1, items_json=json.dumps([{"name": "C", "item_id": "c",
                                                                               "qty": 1, "case_of": 10}]),
                       status="pdf"))
    db.commit()
    got = OS.known_case_sizes(db, "Babco", ["a", "b", "c", "d"])
    assert got == {"a": (6.0, "cart"), "b": (12.0, "last_order")}


def test_other_cart_units_by_vendor():
    rows = [CartItem(supplier="Babco", item_id="x", name="X", qty=2, case_of=1),
            CartItem(supplier="RBest", item_id="x", name="X", qty=2, case_of=6),
            CartItem(supplier="babco", item_id="x", name="X", qty=5, case_of=1)]
    assert OS.other_cart_units("Babco", ["x"], rows) == {"x": [{"vendor": "RBest", "units": 12.0}]}


def _snap(db, day, item_id, name, qty, price=3.99, supplier="Babco"):
    db.add(InventorySnapshot(snapshot_date=day, item_id=item_id, name=name, inventory_quantity=qty, price=price,
                             cost=2.0, supplier=supplier, category="Snacks", source_filename="f.csv"))


def test_avg_on_hand_last_28_days_with_container_twin(db):
    from app.services.reorder import latest_inventory_by_item

    for back, qty in [(0, 6), (1, 8), (2, 10), (40, 1000)]:  # 40 days back is outside the window
        _snap(db, TODAY - dt.timedelta(days=back), "rice", "Rice", qty)
    _snap(db, TODAY, "rice-ctr", "Rice", 20, price=None, supplier=None)
    _snap(db, TODAY - dt.timedelta(days=1), "rice-ctr", "Rice", 10, price=None, supplier=None)
    db.commit()
    got = OS.avg_on_hand(db, ["rice", "nope"], latest_inventory_by_item(db), TODAY)
    # (6+20 + 8+10 + 10) / 3 days
    assert got == {"rice": (18.0, 3)}


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


def _sale(db, day, item_id, qty, guid):
    db.add(Order(toast_order_guid="o" + guid, toast_check_guid="c" + guid, toast_selection_guid=guid,
                 business_date=day, item_guid=item_id, item_name=item_id, quantity=qty, unit_price=1.0,
                 net_price=qty, voided=False))


@pytest.fixture
def stocked(db):
    # 28 units sold over the last 28 days -> legacy 1/day.
    _snap(db, TODAY, "naan", "Naan 16pcs", 6)
    _snap(db, TODAY, "dal", "Toor Dal", 200)
    _snap(db, TODAY, "neg", "Whole Chicken", -5.5)
    _snap(db, TODAY, "loose", "Curry Leaves", None)
    _snap(db, TODAY, "multi", "Cilantro", 0, supplier="Babco;RBest")
    for n in range(28):
        day = TODAY - dt.timedelta(days=n + 1)
        for item in ("naan", "neg", "loose", "multi"):
            _sale(db, day, item, 1, f"{item}-{n}")
    db.add(CartItem(supplier="RBest", item_id="multi", name="Cilantro", qty=5, case_of=1))
    db.add(CartItem(supplier="Babco", item_id="dal", name="Toor Dal", qty=3, case_of=12))
    db.add(CartItem(supplier="Babco", item_id=None, name="Hand typed", qty=1, case_of=1))
    db.commit()


def _projection(client, method="legacy"):
    start = (TODAY - dt.timedelta(days=28)).isoformat()
    r = client.get(PROJ, params={"supplier": "Babco", "start": start, "horizons": "30", "method": method})
    assert r.status_code == 200, r.text
    return r.json()


def test_projection_items_carry_suggested_order(db, admin_client, stocked):
    body = _projection(admin_client)
    assert body["order_window"]["horizon_days"] == 30 and body["order_window"]["label"] == "1 month"
    assert body["order_window"]["delivery"] is None
    items = {i["item_id"]: i for i in body["items"]}
    # Old fields are all still there.
    for key in ("avg_daily_demand", "avg_weekly_demand", "on_hand_qty", "projections", "supplier_item_id"):
        assert key in items["naan"]
    naan = items["naan"]["suggested_order"]
    assert naan["qty"] == 24  # 30 projected - 6 on hand
    assert naan["basis"] == "sold ~7/week · on hand 6 (avg 6) · no delivery schedule · suggested 24 for 1 month"
    # Legacy subtracts a negative count as is (the page's own Need); it's still flagged as counted as 0 only
    # in the availability method - either way the qty equals the page's Need rounded up.
    neg = items["neg"]
    assert neg["suggested_order"]["qty"] == OS.whole_units(neg["projections"]["30"]["need_to_order"])
    assert items["loose"]["suggested_order"]["qty"] == 30 and not items["loose"]["suggested_order"]["stock_known"]
    # Multi-vendor: 5 already in RBest's cart.
    multi = items["multi"]["suggested_order"]
    assert multi["need_units"] == 30 and multi["qty"] == 25
    assert multi["other_carts"] == [{"vendor": "RBest", "units": 5.0}]
    # Known pack size from this vendor's cart line (Case of 12): dal has plenty -> 0 cases.
    dal = items["dal"]["suggested_order"]
    assert dal["case_of"] == 12.0 and dal["qty"] == 0 and dal["enough_stock"]


def test_projection_availability_method_also_suggests(db, admin_client, stocked):
    body = _projection(admin_client, method="availability")
    for item in body["items"]:
        s = item["suggested_order"]
        need = item["projections"]["30"]["need_to_order"]
        if item["on_hand_qty"] is not None and not s["other_carts"] and not s["case_of"]:
            assert s["qty"] == need  # exactly the page's Need (already whole units)
        assert s["qty"] >= 0


def test_projection_suggestion_follows_the_schedule(db, admin_client, stocked):
    _schedule(db, "Babco", [0, 1, 2, 3, 4, 5, 6], 0)  # every day, order same day
    body = _projection(admin_client)
    d = body["order_window"]["delivery"]
    assert d["next_delivery"] == TODAY.isoformat() and d["order_by"] == TODAY.isoformat()
    naan = next(i for i in body["items"] if i["item_id"] == "naan")
    assert "(order by today)" in naan["suggested_order"]["basis"]


def test_projection_survives_a_suggestion_failure(db, admin_client, stocked, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr("app.routers.ordering.attach_to_projection", boom)
    body = _projection(admin_client)
    assert body["order_window"] is None and body["items"]


def test_cart_projection_is_opt_in(db, admin_client, stocked):
    body = admin_client.get(CART).json()
    assert body["projection_window"] is None
    assert all(i["projection"] is None for s in body["suppliers"] for i in s["items"])


def test_cart_projection_context(db, stocked):
    # A staff member with only Supplier Projection can read the cart (add-to-cart pre-ticks).
    staff = client_for(db, make_user(db, "sp_only", features=["supplier_projection"]))
    r = staff.get(CART, params={"projection": "true", "horizon_days": 30, "lookback_days": 28})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["projection_window"] == {"lookback_days": 28, "horizon_days": 30}
    lines = {i["name"]: i for s in body["suppliers"] for i in s["items"]}
    assert lines["Hand typed"]["projection"] is None
    dal = lines["Toor Dal"]["projection"]
    # Its own Case of 12; 200 on hand covers a month with no sales.
    assert dal["case_of"] == 12.0 and dal["qty"] == 0 and dal["compact"].startswith("no sales")
    cil = lines["Cilantro"]["projection"]  # under RBest; nothing in another vendor's cart
    assert cil["horizon_days"] == 30 and cil["lookback_days"] == 28 and cil["other_carts"] == []
    assert cil["compact"].startswith("~") and "on hand 0" in cil["compact"]
    assert cil["qty"] > 0


def test_cart_projection_failure_keeps_the_cart(db, admin_client, stocked, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr("app.routers.ordering.cart_projection_context", boom)
    r = admin_client.get(CART, params={"projection": "1"})
    assert r.status_code == 200 and r.json()["total_items"] == 3


def test_cart_params_are_bounded(db, admin_client):
    assert admin_client.get(CART, params={"projection": "1", "horizon_days": 0}).status_code == 422
    assert admin_client.get(CART, params={"projection": "1", "lookback_days": 9999}).status_code == 422
