"""Placed orders (ops fixes C): a PDF preview + "Mark placed" is ONE order,
a repeated Mark placed doesn't create a second, the full history endpoint's
filters, and migration 0032."""

import datetime as dt
import json
import os
import pathlib
import sqlite3
import subprocess
import sys

import pytest

from app.db import Base
from app.models import CartItem, PlacedOrder
from app.timeutil import utcnow
from tests.conftest import client_for, make_user

BACKEND = pathlib.Path(__file__).resolve().parents[1]

PDF = "/api/ordering/purchase-order/export-pdf"
MARK = "/api/ordering/cart/mark-placed"
RECENT = "/api/ordering/orders/recent"
ORDERS = "/api/ordering/orders"


@pytest.fixture
def buyer(db):
    user = make_user(db, "buyer", features=["purchase_order_cart"])
    return client_for(db, user)


def _cart(db, supplier, lines):
    for name, code, qty in lines:
        db.add(CartItem(supplier=supplier, item_id=f"id-{name}", name=name, supplier_item_id=code, qty=qty, case_of=1.0))
    db.commit()


def _pdf(client, supplier, lines):
    r = client.post(
        PDF,
        json={"supplier": supplier, "items": [{"name": n, "supplier_item_id": c, "qty": q} for n, c, q in lines]},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"


def _rows(db):
    db.expire_all()
    return db.query(PlacedOrder).order_by(PlacedOrder.id).all()


def _add_order(db, supplier, placed_at, status="placed", lines=(("Toor Dal", "TD4", 2),), by="boss"):
    items = [{"name": n, "supplier_item_id": c, "qty": q} for n, c, q in lines]
    row = PlacedOrder(
        supplier=supplier,
        item_count=len(items),
        items_json=json.dumps(items),
        placed_by_name=by,
        placed_at=placed_at,
        status=status,
    )
    db.add(row)
    db.commit()
    return row


# --- the duplicate ---------------------------------------------------------------


def test_preview_then_mark_placed_is_one_order(db, buyer):
    lines = [("Shan Biryani Masala", "SH1", 3), ("Rooh Afza", "RA2", 1)]
    _cart(db, "Shaz International", lines)
    _pdf(buyer, "Shaz International", lines)
    _pdf(buyer, "Shaz International", lines)  # opened the preview twice
    rows = _rows(db)
    assert [(r.supplier, r.status) for r in rows] == [("Shaz International", "pdf")]
    assert buyer.get(RECENT).json()["items"] == [], "a PDF preview isn't a placed order"

    # The owner bumps a qty after the preview, then marks it placed.
    db.query(CartItem).filter(CartItem.name == "Rooh Afza").update({"qty": 2})
    db.commit()
    r = buyer.post(MARK, params={"supplier": "Shaz International"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["placed"] is True and body["item_count"] == 2 and body["duplicate"] is False

    rows = _rows(db)
    assert len(rows) == 1, "the PDF row became the placed order"
    assert rows[0].id == body["order_id"] and rows[0].status == "placed"
    snapshot = {i["name"]: i["qty"] for i in json.loads(rows[0].items_json)}
    assert snapshot == {"Shan Biryani Masala": 3, "Rooh Afza": 2}, "the final cart, not the preview"
    assert db.query(CartItem).count() == 0

    recent = buyer.get(RECENT).json()["items"]
    assert len(recent) == 1
    assert set(recent[0]) >= {"id", "supplier", "item_count", "placed_by_name", "placed_at", "items", "status"}
    assert recent[0]["status"] == "placed" and recent[0]["placed_by_name"] == "buyer"


def test_double_mark_placed_is_one_order(db, buyer):
    _cart(db, "Babco", [("Plantain Chips", "ANP", 2)])
    first = buyer.post(MARK, params={"supplier": "Babco"})
    second = buyer.post(MARK, params={"supplier": "Babco"})
    assert first.status_code == 200 and second.status_code == 200, second.text
    assert second.json()["duplicate"] is True
    assert second.json()["order_id"] == first.json()["order_id"]
    assert len(_rows(db)) == 1


def test_same_items_again_within_two_minutes_is_the_same_order(db, buyer):
    _cart(db, "Babco", [("Plantain Chips", "ANP", 2)])
    first = buyer.post(MARK, params={"supplier": "Babco"}).json()
    _cart(db, "Babco", [("Plantain Chips", "ANP", 2)])  # e.g. a stale second device
    again = buyer.post(MARK, params={"supplier": "Babco"}).json()
    assert again == {**first, "duplicate": True}
    assert len(_rows(db)) == 1
    assert db.query(CartItem).count() == 0

    # Different items within the window are a real second order.
    _cart(db, "Babco", [("Banana Chips", "BAN", 1)])
    third = buyer.post(MARK, params={"supplier": "Babco"}).json()
    assert third["duplicate"] is False and third["order_id"] != first["order_id"]
    assert len(_rows(db)) == 2


def test_same_items_after_the_window_is_a_new_order(db, buyer):
    """The owner genuinely orders the same thing again a few minutes later:
    past the 2-minute double-tap window it's a second order, not a duplicate."""
    _cart(db, "Babco", [("Plantain Chips", "ANP", 2)])
    first = buyer.post(MARK, params={"supplier": "Babco"}).json()
    db.query(PlacedOrder).filter(PlacedOrder.id == first["order_id"]).update(
        {"placed_at": utcnow() - dt.timedelta(minutes=5)}
    )
    db.commit()
    _cart(db, "Babco", [("Plantain Chips", "ANP", 2)])
    second = buyer.post(MARK, params={"supplier": "Babco"}).json()
    assert second["duplicate"] is False and second["order_id"] != first["order_id"]
    assert [r.status for r in _rows(db)] == ["placed", "placed"]


def test_a_pdf_preview_is_not_an_order_for_other_readers(db):
    """Order-due tasks and the reorder "last placed" / inactive check only
    count confirmed orders, not a PO PDF preview."""
    from app.services.reorder import group_candidates_by_vendor
    from app.team.auto_tasks import _placed_since

    today = dt.date(2026, 9, 28)
    # 01:30 UTC on Sep 29 is 9:30 PM on Sep 28 in New York.
    evening = dt.datetime(2026, 9, 29, 1, 30, tzinfo=dt.timezone.utc)
    _add_order(db, "Babco", evening, status="pdf")
    assert _placed_since(db, "babco", today) is False
    candidate = {
        "item_id": "x1", "supplier": "Babco", "estimated_order_value": None, "shortfall": 1,
        "on_hand_qty": 0, "avg_daily_demand": 1, "urgency": {"tier": "order_now", "label": "Order now"},
    }
    (group,) = group_candidates_by_vendor(db, [candidate], dt.date(2026, 9, 29))
    assert group["last_placed_at"] is None and group["inactive"] is True

    _add_order(db, "Babco", evening)
    assert _placed_since(db, "babco", today) is True
    (group,) = group_candidates_by_vendor(db, [candidate], dt.date(2026, 9, 29))
    assert group["last_placed_at"] == evening.isoformat() and group["inactive"] is False


def test_empty_cart_after_the_window_is_still_400(db, buyer):
    _add_order(db, "Babco", utcnow() - dt.timedelta(minutes=10))
    r = buyer.post(MARK, params={"supplier": "Babco"})
    assert r.status_code == 400
    assert len(_rows(db)) == 1


def test_old_pdf_is_not_converted_and_other_vendors_untouched(db, buyer):
    _add_order(db, "Babco", utcnow() - dt.timedelta(hours=60), status="pdf")
    _add_order(db, "Raja Foods", utcnow() - dt.timedelta(hours=1), status="pdf")
    _cart(db, "Babco", [("Plantain Chips", "ANP", 2)])
    buyer.post(MARK, params={"supplier": "Babco"})
    rows = _rows(db)
    assert [(r.supplier, r.status) for r in rows] == [("Babco", "pdf"), ("Raja Foods", "pdf"), ("Babco", "placed")]


def test_pdf_upsert_only_reuses_a_recent_pdf_row(db, buyer):
    _add_order(db, "Babco", utcnow() - dt.timedelta(hours=30), status="pdf")
    _add_order(db, "Babco", utcnow() - dt.timedelta(hours=1), status="placed")
    _pdf(buyer, "Babco", [("Plantain Chips", "ANP", 5)])
    _pdf(buyer, "Babco", [("Plantain Chips", "ANP", 6)])
    rows = _rows(db)
    assert [r.status for r in rows] == ["pdf", "placed", "pdf"]
    assert json.loads(rows[2].items_json)[0]["qty"] == 6


# --- full history -----------------------------------------------------------------


def test_orders_filters_and_paging(db, buyer):
    # 2026-09-29 02:56 UTC is 10:56 PM on Sep 28 in New York.
    late = _add_order(db, "Shaz International", dt.datetime(2026, 9, 29, 2, 56, tzinfo=dt.timezone.utc), status="pdf",
                      lines=[("Shan Masala", "SH1", 1), ("Rooh Afza", "RA2", 1)])
    early = _add_order(db, "Shaz International", dt.datetime(2026, 9, 28, 15, 0, tzinfo=dt.timezone.utc))
    babco = _add_order(db, "Babco", dt.datetime(2026, 9, 27, 15, 0, tzinfo=dt.timezone.utc),
                       lines=[("Plantain Chips", "ANP200", 4)])

    body = buyer.get(ORDERS).json()
    assert [o["id"] for o in body["items"]] == [late.id, early.id, babco.id], "newest first"
    assert body["total"] == 3 and body["has_more"] is False
    assert body["suppliers"] == ["Shaz International", "Babco"]
    first = body["items"][0]
    assert first["status"] == "pdf" and first["item_count"] == 2 and first["total_qty"] == 2
    assert set(first) == {
        "id", "supplier", "status", "item_count", "total_qty", "placed_by_name", "placed_at", "items",
        # W1 (additive): Order already placed.
        "source", "source_label", "note", "for_delivery_date",
    }
    assert first["source"] == "app" and first["source_label"] is None

    def ids(**params):
        return [o["id"] for o in buyer.get(ORDERS, params=params).json()["items"]]

    assert ids(supplier="Babco") == [babco.id]
    assert ids(status="placed") == [early.id, babco.id]
    assert ids(status="pdf") == [late.id]
    assert ids(status="all") == [late.id, early.id, babco.id]
    assert ids(start="2026-09-28", end="2026-09-28") == [late.id, early.id], "store-local dates"
    assert ids(start="2026-09-29") == []
    assert ids(end="2026-09-27") == [babco.id]
    assert ids(search="anp2") == [babco.id], "item code"
    assert ids(search="ROOH") == [late.id], "item name, any case"
    assert ids(search="babco") == [babco.id], "vendor name"

    page = buyer.get(ORDERS, params={"limit": 2}).json()
    assert [o["id"] for o in page["items"]] == [late.id, early.id]
    assert page["total"] == 3 and page["has_more"] is True
    page2 = buyer.get(ORDERS, params={"limit": 2, "offset": 2}).json()
    assert [o["id"] for o in page2["items"]] == [babco.id] and page2["has_more"] is False
    searched = buyer.get(ORDERS, params={"search": "shaz", "limit": 1}).json()
    assert searched["total"] == 2 and searched["has_more"] is True

    assert buyer.get(ORDERS, params={"status": "bogus"}).status_code == 400
    assert buyer.get(ORDERS, params={"start": "2026-09-29", "end": "2026-09-28"}).status_code == 400


def test_recent_returns_only_placed(db, buyer):
    _add_order(db, "Babco", utcnow() - dt.timedelta(minutes=5), status="pdf")
    placed = _add_order(db, "Raja Foods", utcnow() - dt.timedelta(hours=5))
    items = buyer.get(RECENT).json()["items"]
    assert [o["id"] for o in items] == [placed.id]


def test_orders_need_the_purchase_order_cart_grant(db):
    staff = client_for(db, make_user(db, "sam", features=["supplier_projection"]))
    assert staff.get(ORDERS).status_code == 403
    assert staff.get(RECENT).status_code == 403


# --- order again -----------------------------------------------------------------


def test_order_again_refills_the_cart(db, buyer):
    _cart(db, "Babco", [("Plantain Chips", "ANP", 2), ("Banana Chips", "BAN", 1)])
    order_id = buyer.post(MARK, params={"supplier": "Babco"}).json()["order_id"]
    assert db.query(CartItem).count() == 0
    # One of them is already back in the cart with another qty.
    _cart(db, "Babco", [("Plantain Chips", "ANP", 7)])

    r = buyer.post(f"{ORDERS}/{order_id}/order-again")
    assert r.status_code == 200, r.text
    assert r.json() == {"supplier": "Babco", "added": 2}
    db.expire_all()
    cart = {c.name: (c.qty, c.item_id) for c in db.query(CartItem).all()}
    assert cart == {"Plantain Chips": (2, "id-Plantain Chips"), "Banana Chips": (1, "id-Banana Chips")}
    assert buyer.post(f"{ORDERS}/999/order-again").status_code == 404


def test_order_again_for_an_old_snapshot_without_item_ids(db, buyer):
    row = _add_order(db, "Babco", utcnow(), lines=[("Plantain Chips", "ANP", 3)])
    assert buyer.post(f"{ORDERS}/{row.id}/order-again").status_code == 200
    buyer.post(f"{ORDERS}/{row.id}/order-again")  # twice: no second row
    db.expire_all()
    cart = [(c.name, c.supplier_item_id, c.qty) for c in db.query(CartItem).all()]
    assert cart == [("Plantain Chips", "ANP", 3)]


# --- migration 0032 -----------------------------------------------------------------


def test_0032_adds_status_and_keeps_existing_orders_placed(tmp_path):
    db_file = tmp_path / "existing.db"
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_file}")
    alembic = pathlib.Path(sys.executable).parent / "alembic"
    run = lambda *args: subprocess.run(  # noqa: E731
        [str(alembic), *args], cwd=BACKEND, env=env, check=True, capture_output=True
    )
    run("upgrade", "0031")
    con = sqlite3.connect(db_file)
    con.execute(
        "INSERT INTO placed_orders (supplier, item_count, items_json, placed_by_name, placed_at) "
        "VALUES ('Shaz International', 1, '[]', 'boss', '2026-09-28 15:00:00')"
    )
    con.commit()
    con.close()

    run("upgrade", "head")
    con = sqlite3.connect(db_file)
    try:
        assert con.execute("SELECT supplier, status FROM placed_orders").fetchall() == [("Shaz International", "placed")]
        migrated = {row[1] for row in con.execute("PRAGMA table_info(placed_orders)")}
        modeled = {c.name for c in Base.metadata.tables["placed_orders"].columns}
        assert migrated == modeled
        con.execute(
            "INSERT INTO placed_orders (supplier, item_count, items_json, placed_at) VALUES ('Babco', 0, '[]', '2026-09-29')"
        )
        assert con.execute("SELECT status FROM placed_orders WHERE supplier = 'Babco'").fetchone() == ("placed",)
        con.commit()
    finally:
        con.close()

    run("downgrade", "0031")
    con = sqlite3.connect(db_file)
    try:
        cols = {row[1] for row in con.execute("PRAGMA table_info(placed_orders)")}
        assert "status" not in cols
        assert con.execute("SELECT COUNT(*) FROM placed_orders").fetchone() == (2,)
    finally:
        con.close()
    run("upgrade", "head")
