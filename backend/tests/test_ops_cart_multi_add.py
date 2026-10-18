"""Purchase Order Cart "add items" on the website (Oct 7): several items in one
go, from the chosen vendor's own products or the whole catalog.

- GET /api/inventory/items/search carries the cart's "(in stock: N)" figure
  for every result (`in_stock_qty`: on hand incl. the Container row,
  `container_qty`) and marks a Container duplicate row (`container_of`) so
  "search all products" can leave it out. The old fields are unchanged.
- One POST /api/ordering/cart/items adds the whole list to the chosen
  vendor's section - a catalog item from another vendor included - and an
  item already in that vendor's cart has its qty replaced (why the website
  greys those out instead of letting them be ticked).
"""

import datetime as dt

import pytest

from app.models import InventorySnapshot
from tests.conftest import client_for, make_user

SEARCH = "/api/inventory/items/search"
CART = "/api/ordering/cart"
ADD = "/api/ordering/cart/items"

OLD_KEYS = ("item_id", "name", "category", "supplier", "supplier_item_id", "barcode", "price", "cost", "on_hand_qty")


@pytest.fixture
def buyer(db):
    return client_for(db, make_user(db, "buyer", features=["purchase_order_cart"]))


def _snap(db, day, item_id, name, qty, price=3.99, supplier="Babco", code=None):
    db.add(
        InventorySnapshot(
            snapshot_date=day,
            item_id=item_id,
            name=name,
            category_group="GROCERY",
            category="Rice",
            inventory_quantity=qty,
            price=price,
            cost=2.0,
            supplier=supplier,
            supplier_item_id=code,
        )
    )


@pytest.fixture
def catalog(db):
    today = dt.date.today()
    # An older upload must not be used.
    _snap(db, today - dt.timedelta(days=2), "rice", "Basmati Rice 10lb", 99)
    _snap(db, today, "rice", "Basmati Rice 10lb", 4, supplier="Babco;Raja Foods", code="BR10;RJ-77")
    # Its bare Container-location duplicate (no price, no supplier).
    _snap(db, today, "rice-ctr", "Basmati Rice 10lb", 20, price=None, supplier=None)
    _snap(db, today, "sona", "Sona Masoori Rice 20lb", 6, supplier="Raja Foods", code="RJ-20")
    # On hand not tracked.
    _snap(db, today, "idli", "Idli Rice 10lb", None, code="ID10")
    # No price and no vendor, but no priced item shares its name: a kitchen
    # dish, not a Container duplicate.
    _snap(db, today, "bowl", "Biryani Rice Bowl", None, price=None, supplier=None)
    db.commit()


def _search(client, **params):
    r = client.get(SEARCH, params=params)
    assert r.status_code == 200, r.text
    return {i["item_id"]: i for i in r.json()["items"]}


def test_all_products_search_has_each_items_vendor_and_in_stock(buyer, catalog):
    items = _search(buyer, q="rice")
    assert set(items) == {"rice", "rice-ctr", "sona", "idli", "bowl"}
    for item in items.values():
        for key in OLD_KEYS + ("in_stock_qty", "container_qty", "container_of"):
            assert key in item

    rice = items["rice"]
    assert rice["supplier"] == "Babco;Raja Foods"
    assert rice["on_hand_qty"] == 4  # the shelf row alone, as before
    assert rice["in_stock_qty"] == 24 and rice["container_qty"] == 20
    assert rice["container_of"] is None
    assert rice["supplier_item_id"] is None  # no vendor asked for

    ctr = items["rice-ctr"]
    assert ctr["container_of"] == "rice"
    assert ctr["in_stock_qty"] == 20 and ctr["container_qty"] is None

    sona = items["sona"]
    assert sona["supplier"] == "Raja Foods"
    assert sona["in_stock_qty"] == 6 and sona["container_qty"] is None and sona["container_of"] is None

    assert items["idli"]["in_stock_qty"] is None and items["idli"]["on_hand_qty"] is None
    assert items["bowl"]["container_of"] is None


def test_vendor_search_keeps_its_filter_and_that_vendors_code(buyer, catalog):
    items = _search(buyer, q="rice", supplier="Raja Foods")
    assert set(items) == {"rice", "sona"}
    assert items["rice"]["supplier_item_id"] == "RJ-77"
    assert items["rice"]["in_stock_qty"] == 24
    assert items["sona"]["supplier_item_id"] == "RJ-20"

    babco = _search(buyer, q="rice", supplier="Babco")
    assert set(babco) == {"rice", "idli"}
    assert babco["rice"]["supplier_item_id"] == "BR10"


def test_one_post_adds_the_whole_list_to_the_chosen_vendor(buyer, catalog):
    first = buyer.post(ADD, json={"supplier": "Babco", "items": [{"item_id": "idli", "name": "Idli Rice 10lb", "qty": 2}]})
    assert first.status_code == 200, first.text

    in_stock = _search(buyer, q="rice")
    r = buyer.post(
        ADD,
        json={
            "supplier": "Babco",
            "items": [
                {"item_id": "rice", "name": "Basmati Rice 10lb", "supplier_item_id": "BR10", "qty": 6},
                # From "search all products": a Raja Foods item, ordered from Babco.
                {"item_id": "sona", "name": "Sona Masoori Rice 20lb", "supplier_item_id": None, "qty": 3},
                # Typed by hand - not in inventory.
                {"item_id": None, "name": "New Mango Pickle", "supplier_item_id": "MP1", "qty": 1},
            ],
        },
    )
    assert r.status_code == 200, r.text
    assert [row["name"] for row in r.json()] == ["Basmati Rice 10lb", "Sona Masoori Rice 20lb", "New Mango Pickle"]

    cart = buyer.get(CART).json()
    assert [s["supplier"] for s in cart["suppliers"]] == ["Babco"]
    lines = {i["name"]: i for i in cart["suppliers"][0]["items"]}
    assert set(lines) == {"Idli Rice 10lb", "Basmati Rice 10lb", "Sona Masoori Rice 20lb", "New Mango Pickle"}
    assert lines["Idli Rice 10lb"]["qty"] == 2  # untouched
    assert lines["Basmati Rice 10lb"]["qty"] == 6 and lines["Basmati Rice 10lb"]["supplier_item_id"] == "BR10"
    assert lines["Sona Masoori Rice 20lb"]["qty"] == 3
    assert lines["New Mango Pickle"]["item_id"] is None and lines["New Mango Pickle"]["supplier_item_id"] == "MP1"
    # "(in stock: N)" reads the same before and after adding.
    assert lines["Basmati Rice 10lb"]["on_hand_qty"] == in_stock["rice"]["in_stock_qty"] == 24
    assert lines["Sona Masoori Rice 20lb"]["on_hand_qty"] == in_stock["sona"]["in_stock_qty"] == 6
    assert lines["New Mango Pickle"]["on_hand_qty"] is None


def test_an_item_already_in_the_vendors_cart_gets_its_qty_replaced(buyer, catalog):
    buyer.post(ADD, json={"supplier": "Babco", "items": [{"item_id": "rice", "name": "Basmati Rice 10lb", "qty": 5}]})
    buyer.post(ADD, json={"supplier": "Babco", "items": [{"item_id": "rice", "name": "Basmati Rice 10lb", "qty": 1}]})
    lines = buyer.get(CART).json()["suppliers"][0]["items"]
    assert [(i["name"], i["qty"]) for i in lines] == [("Basmati Rice 10lb", 1)]


# --- Adversarial check (Oct 7) ------------------------------------------------


def _lines(client, supplier="Babco"):
    for s in client.get(CART).json()["suppliers"]:
        if s["supplier"] == supplier:
            return [(i["item_id"], i["name"], i["qty"]) for i in s["items"]]
    return []


def test_the_same_item_twice_in_one_list_is_one_line(buyer, catalog):
    # The session doesn't autoflush: the second lookup used to miss the
    # first one's pending row, so one POST made two lines for one item...
    r = buyer.post(
        ADD,
        json={
            "supplier": "Babco",
            "items": [
                {"item_id": "rice", "name": "Basmati Rice 10lb", "qty": 2},
                {"item_id": "idli", "name": "Idli Rice 10lb", "qty": 1},
                {"item_id": "rice", "name": "Basmati Rice 10lb", "qty": 5},
            ],
        },
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    # One row per entry, in order; the repeat is the same line (later qty wins).
    assert [row["item_id"] for row in rows] == ["rice", "idli", "rice"]
    assert rows[0]["id"] == rows[2]["id"] and rows[2]["qty"] == 5
    assert sorted(_lines(buyer)) == [("idli", "Idli Rice 10lb", 1), ("rice", "Basmati Rice 10lb", 5)]
    # ...and every later add of that item then failed with a 500.
    again = buyer.post(ADD, json={"supplier": "Babco", "items": [{"item_id": "rice", "name": "Basmati Rice 10lb", "qty": 3}]})
    assert again.status_code == 200, again.text
    assert ("rice", "Basmati Rice 10lb", 3) in _lines(buyer)


def test_a_cart_already_holding_an_item_twice_still_takes_a_re_add(db, buyer, catalog):
    from app.models import CartItem

    for qty in (1, 2):
        db.add(CartItem(supplier="Babco", item_id="rice", name="Basmati Rice 10lb", qty=qty, case_of=1.0))
    db.commit()
    r = buyer.post(ADD, json={"supplier": "Babco", "items": [{"item_id": "rice", "name": "Basmati Rice 10lb", "qty": 9}]})
    assert r.status_code == 200, r.text
    # The older line takes the qty; nothing new is inserted.
    assert sorted(q for _i, _n, q in _lines(buyer)) == [2, 9]


def test_the_list_is_added_all_or_nothing(monkeypatch, db, catalog):
    """A failure part-way through a list leaves the cart exactly as it was -
    the website relies on this to say "nothing was added"."""
    from app.db import SessionLocal
    from app.models import CartItem
    from app.services import po_cart

    real_lookup = po_cart._lookup_supplier_item_id

    def lookup(session, item_id, supplier):
        if item_id == "sona":
            raise RuntimeError("boom")
        return real_lookup(session, item_id, supplier)

    monkeypatch.setattr(po_cart, "_lookup_supplier_item_id", lookup)
    session = SessionLocal()
    try:
        with pytest.raises(RuntimeError):
            po_cart.add_items(
                session,
                "Babco",
                [
                    {"item_id": "rice", "name": "Basmati Rice 10lb", "qty": 2},
                    {"item_id": None, "name": "New Mango Pickle", "qty": 1},
                    {"item_id": "sona", "name": "Sona Masoori Rice 20lb", "qty": 3},
                ],
                None,
            )
    finally:
        session.close()  # what get_db does after an error: rolls back
    assert db.query(CartItem).count() == 0


def test_two_adds_of_the_same_item_at_once_make_one_line(catalog):
    """A double submit (or the phone and the website at the same moment):
    both used to find no line and both inserted one."""
    import threading
    import time

    from app.db import SessionLocal
    from app.models import CartItem
    from app.services import po_cart

    errors = []

    def add(qty):
        session = SessionLocal()
        real_commit = session.commit

        def slow_commit():
            time.sleep(0.3)  # both lookups would run before either insert lands
            real_commit()

        session.commit = slow_commit
        try:
            po_cart.add_items(session, "Babco", [{"item_id": "rice", "name": "Basmati Rice 10lb", "qty": qty}], None)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=add, args=(q,)) for q in (2, 4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    session = SessionLocal()
    try:
        rows = session.query(CartItem).filter(CartItem.item_id == "rice").all()
        assert len(rows) == 1 and rows[0].qty in (2, 4)
    finally:
        session.close()


@pytest.mark.parametrize(
    "item",
    [
        {"qty": 1e308},  # x cost overflowed to inf - GET /cart then failed for everyone
        {"qty": float("inf")},
        {"qty": float("nan")},
        {"qty": 100001},
        {"qty": 2, "case_of": 1e9},
    ],
)
def test_a_qty_or_case_of_the_cart_cant_hold_is_refused(buyer, catalog, item):
    import json

    body = {"supplier": "Babco", "items": [{"item_id": "rice", "name": "Basmati Rice 10lb", **item}]}
    # json.dumps writes NaN / Infinity literally (as a non-browser client
    # could); the server's JSON parser accepts them.
    r = buyer.post(ADD, content=json.dumps(body), headers={"Content-Type": "application/json"})
    assert r.status_code == 422, r.text
    assert isinstance(r.json()["detail"], str) and "100,000" in r.json()["detail"]
    cart = buyer.get(CART)
    assert cart.status_code == 200 and cart.json()["suppliers"] == []


def test_the_biggest_allowed_qty_still_loads_the_cart(buyer, catalog):
    r = buyer.post(ADD, json={"supplier": "Babco", "items": [{"item_id": "rice", "name": "Basmati Rice 10lb", "qty": 100000, "case_of": 100000}]})
    assert r.status_code == 200, r.text
    line = buyer.get(CART).json()["suppliers"][0]["items"][0]
    assert line["qty"] == 100000 and line["line_cost"] == 2.0 * 100000 * 100000


def test_a_cart_line_cant_be_edited_to_a_qty_it_cant_hold(buyer, catalog):
    line = buyer.post(ADD, json={"supplier": "Babco", "items": [{"item_id": "rice", "name": "Basmati Rice 10lb", "qty": 2}]}).json()[0]
    for body in ({"qty": 1e308}, {"case_of": 1e308}, {"qty": 250000}):
        r = buyer.patch(f"{ADD}/{line['id']}", json=body)
        assert r.status_code == 422, r.text
    assert buyer.patch(f"{ADD}/{line['id']}", json={"qty": 7}).json()["qty"] == 7
    assert buyer.get(CART).status_code == 200


def test_another_vendors_code_never_lands_on_the_chosen_vendors_line(buyer, catalog):
    """Oct 7: an item added from "all products" under a vendor that doesn't
    carry it got its own vendor's code (Sona: Raja Foods' RJ-20 on a Babco
    line), which then printed on Babco's PO. Its own vendors still get theirs."""
    r = buyer.post(
        ADD,
        json={
            "supplier": "Babco",
            "items": [{"item_id": "sona", "name": "Sona Masoori Rice 20lb", "supplier_item_id": None, "qty": 3}],
        },
    )
    assert r.status_code == 200, r.text
    r = buyer.post(
        ADD,
        json={
            "supplier": "raja foods ",
            "items": [{"item_id": "rice", "name": "Basmati Rice 10lb", "supplier_item_id": None, "qty": 2}],
        },
    )
    assert r.status_code == 200, r.text
    lines = {
        (s["supplier"].strip().lower(), i["name"]): i
        for s in buyer.get(CART).json()["suppliers"]
        for i in s["items"]
    }
    assert lines[("babco", "Sona Masoori Rice 20lb")]["supplier_item_id"] is None
    assert lines[("raja foods", "Basmati Rice 10lb")]["supplier_item_id"] == "RJ-77"
