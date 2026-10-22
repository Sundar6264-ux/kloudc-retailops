"""Oct 22 regression: Supplier Projection and the cart must suggest the SAME
quantity for the same item.

The owner's screenshots: "Garlic 5-Pack" (receiving units Case;Case;Pound
30;50;1 -> case of 30) said "Order 6 cases" on Supplier Projection (one tap
added 6 cases), while its cart line then said "suggested 2 x 30". Both come
from services/order_suggestion.suggest(); the cause was the WINDOW: the
projection page asked for 30 days, the cart for its own 7 days. The clients
now send one shared window; this test pins that, with that window, the
projection row and the cart line agree item by item (cases, units, a
multi-vendor item, an item already in the cart, no stock count).
"""

import datetime as dt

import pytest

from app.models import CartItem, InventorySnapshot, Order

PROJ = "/api/ordering/supplier-projection"
CART = "/api/ordering/cart"
TODAY = dt.date.today()
VENDOR = "Fresh Farms"
LOOKBACK = 90
WINDOW = 30


def _snap(db, item_id, name, qty, case_size=None, raw=None, supplier=VENDOR):
    db.add(
        InventorySnapshot(
            snapshot_date=TODAY, item_id=item_id, name=name, inventory_quantity=qty, price=3.99, cost=2.0,
            supplier=supplier, category="Produce", source_filename="f.csv",
            case_size=case_size, receiving_unit="Case" if case_size else None, receiving_units_raw=raw,
        )
    )


def _sales(db, item_id, per_day, days=LOOKBACK):
    for n in range(days):
        day = TODAY - dt.timedelta(days=n + 1)
        guid = f"{item_id}-{n}"
        db.add(Order(toast_order_guid="o" + guid, toast_check_guid="c" + guid, toast_selection_guid=guid,
                     business_date=day, item_guid=item_id, item_name=item_id, quantity=per_day,
                     unit_price=1.0, net_price=per_day, voided=False))


@pytest.fixture
def store(db):
    # Garlic: ~39/week, not counted, case of 30 (the owner's item).
    _snap(db, "garlic", "Garlic 5-Pack", None, case_size=30.0, raw="Case;Case;Pound|30;50;1")
    _sales(db, "garlic", 5.6)
    # Tea masala: 1 on hand, case of 24, slow.
    _snap(db, "tea", "Swad Tea Masala 100g", 1, case_size=24.0)
    _sales(db, "tea", 0.35)
    # Units item, counted.
    _snap(db, "okra", "Okra Indian in LBs", 20)
    _sales(db, "okra", 4)
    # Sold by two vendors, some already in the other vendor's cart.
    _snap(db, "cil", "Cilantro Bunch", 5, supplier="Fresh Farms;Green Valley")
    _sales(db, "cil", 3)
    db.add(CartItem(supplier="Green Valley", item_id="cil", name="Cilantro Bunch", qty=10, case_of=1))
    db.commit()


def _projection(client, method):
    start = (TODAY - dt.timedelta(days=LOOKBACK)).isoformat()
    r = client.get(PROJ, params={"supplier": VENDOR, "start": start, "end": TODAY.isoformat(),
                                 "horizons": str(WINDOW), "method": method})
    assert r.status_code == 200, r.text
    return {i["item_id"]: i["suggested_order"] for i in r.json()["items"]}


def _cart(client, horizon=WINDOW):
    r = client.get(CART, params={"projection": "true", "lookback_days": LOOKBACK, "horizon_days": horizon})
    assert r.status_code == 200, r.text
    return {i["item_id"]: i for s in r.json()["suppliers"] if s["supplier"] == VENDOR for i in s["items"]}


@pytest.mark.parametrize("method", ["availability", "legacy"])
def test_projection_and_cart_suggest_the_same_qty_for_the_same_window(db, admin_client, store, method):
    before = _projection(admin_client, method)
    garlic = before["garlic"]
    assert garlic["case_of"] == 30.0 and garlic["qty"] >= 5, garlic  # ~169 units -> 6 cases of 30

    # One-tap Add puts each suggestion in the cart, in the projection's case size.
    for item_id, s in before.items():
        if s["qty"] > 0:
            db.add(CartItem(supplier=VENDOR, item_id=item_id, name=item_id, qty=s["qty"], case_of=s["case_of"] or 1))
    db.commit()

    lines = _cart(admin_client)
    after = _projection(admin_client, method)
    for item_id, s in before.items():
        if s["qty"] == 0:
            continue
        line = lines[item_id]["projection"]
        assert line is not None, item_id
        # The cart line and the projection row: same suggestion, same case size.
        assert (line["qty"], line["case_of"], line["units"]) == (s["qty"], s["case_of"], s["units"]), item_id
        assert (after[item_id]["qty"], after[item_id]["case_of"]) == (s["qty"], s["case_of"]), item_id
        assert line["horizon_days"] == s["horizon_days"] == WINDOW
    # The owner's line: "suggested 6 x 30", not "suggested 2 x 30".
    assert lines["garlic"]["projection"]["compact"].split(" · ")[1] == f"suggested {garlic['qty']} x 30"


def test_a_different_window_is_what_made_them_disagree(db, admin_client, store):
    """The root cause, pinned: the same function over 7 days instead of 30
    gives the smaller number the owner saw in the cart. The app and the
    website now send the projection's window to the cart."""
    s = _projection(admin_client, "availability")["garlic"]
    db.add(CartItem(supplier=VENDOR, item_id="garlic", name="Garlic 5-Pack", qty=s["qty"], case_of=30))
    db.commit()
    week = _cart(admin_client, horizon=7)["garlic"]["projection"]
    assert week["qty"] < s["qty"]
    assert _cart(admin_client)["garlic"]["projection"]["qty"] == s["qty"]
