"""Cart "(in stock: N)" bracket (ops fixes Q): GET /cart carries each line's
on hand from the latest upload incl. the Container duplicate row, a
hand-added item gets none, and neither the PO PDF nor the "Order placed"
snapshot ever includes it."""

import datetime as dt
import json

import pytest

from app.models import CartItem, InventorySnapshot, PlacedOrder
from app.services import po_export
from tests.conftest import client_for, make_user

CART = "/api/ordering/cart"
PDF = "/api/ordering/purchase-order/export-pdf"
MARK = "/api/ordering/cart/mark-placed"
VENDOR = "Babco"


@pytest.fixture
def buyer(db):
    return client_for(db, make_user(db, "buyer", features=["purchase_order_cart"]))


def _snap(db, day, item_id, name, qty, price=3.99, supplier=VENDOR):
    db.add(
        InventorySnapshot(
            snapshot_date=day,
            item_id=item_id,
            name=name,
            category_group="GROCERY",
            category="Snacks",
            inventory_quantity=qty,
            price=price,
            cost=2.0,
            supplier=supplier,
        )
    )


@pytest.fixture
def stocked(db):
    today = dt.date.today()
    old = today - dt.timedelta(days=3)
    # An older upload must not be used.
    _snap(db, old, "rice", "Basmati Rice 10lb", 99)
    _snap(db, today, "rice", "Basmati Rice 10lb", 4)
    # Its bare Container-location duplicate (no price, no supplier).
    _snap(db, today, "rice-ctr", "Basmati Rice 10lb", 20, price=None, supplier=None)
    _snap(db, today, "dal", "Toor Dal 4lb", 7)
    db.add(CartItem(supplier=VENDOR, item_id="rice", name="Basmati Rice 10lb", supplier_item_id="BR10", qty=2, case_of=1))
    db.add(CartItem(supplier=VENDOR, item_id="dal", name="Toor Dal 4lb", supplier_item_id="TD4", qty=3, case_of=1))
    db.add(CartItem(supplier=VENDOR, item_id=None, name="New Mango Pickle", supplier_item_id=None, qty=1, case_of=1))
    db.commit()


def _lines(client):
    r = client.get(CART)
    assert r.status_code == 200, r.text
    return {i["name"]: i for s in r.json()["suppliers"] for i in s["items"]}


def test_on_hand_includes_container_row(db, buyer, stocked):
    lines = _lines(buyer)
    rice = lines["Basmati Rice 10lb"]
    assert rice["on_hand_qty"] == 24
    assert rice["container_qty"] == 20
    dal = lines["Toor Dal 4lb"]
    assert dal["on_hand_qty"] == 7
    assert dal["container_qty"] is None


def test_hand_added_item_has_no_stock(db, buyer, stocked):
    pickle = _lines(buyer)["New Mango Pickle"]
    assert pickle["on_hand_qty"] is None
    assert pickle["container_qty"] is None
    # The old fields are all still there (installed apps read them).
    for key in ("id", "supplier", "item_id", "name", "supplier_item_id", "qty", "case_of", "unit_cost", "line_cost"):
        assert key in pickle


def test_pdf_and_placed_snapshot_never_carry_stock(db, buyer, stocked, monkeypatch):
    seen = {}
    real = po_export.export_simple_po_pdf

    def spy(supplier, items, sections=None):
        seen["items"] = items
        return real(supplier, items, sections) if sections is not None else real(supplier, items)

    # The PDF is built by po_send.build_po_pdf (shared with Send PO).
    monkeypatch.setattr("app.services.po_send.export_simple_po_pdf", spy)
    # Even if a client sent the whole cart line, the PDF input keeps only
    # name / supplier_item_id / qty.
    items = list(_lines(buyer).values())
    r = buyer.post(PDF, json={"supplier": VENDOR, "items": items})
    assert r.status_code == 200, r.text
    for line in seen["items"]:
        assert set(line) == {"name", "supplier_item_id", "qty"}

    r = buyer.post(MARK, params={"supplier": VENDOR})
    assert r.status_code == 200, r.text
    db.expire_all()
    order = db.query(PlacedOrder).one()
    for line in json.loads(order.items_json):
        assert "on_hand_qty" not in line and "container_qty" not in line
