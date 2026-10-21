"""G31: the reorder report says whether the store has any sales / stock data,
so an empty Reorder Candidates list on a brand-new store reads "no sales yet"
instead of "nothing needs reordering". Additive JSON: `has_sales_data`,
`has_stock_data`.
"""

import datetime as dt

from app.models import InventorySnapshot, Order
from app.timeutil import restaurant_today

REORDER = "/api/ordering/reorder-candidates"
TODAY = restaurant_today()


def _snap(db, item_id, qty):
    db.add(InventorySnapshot(snapshot_date=TODAY, item_id=item_id, name=item_id.title(), inventory_quantity=qty,
                             price=3.99, cost=2.0, supplier="Raja Foods", category="Snacks", source_filename="f.csv"))


def _sale(db, item_id, qty, guid, voided=False):
    db.add(Order(toast_order_guid="o" + guid, toast_check_guid="c" + guid, toast_selection_guid=guid,
                 business_date=TODAY - dt.timedelta(days=1), item_guid=item_id, item_name=item_id, quantity=qty,
                 unit_price=1.0, net_price=qty, voided=voided))


def test_empty_store_has_no_sales_or_stock_data(admin_client):
    body = admin_client.get(REORDER).json()
    assert body["candidates"] == [] and body["vendors"] == []
    assert body["has_sales_data"] is False
    assert body["has_stock_data"] is False


def test_only_voided_sales_do_not_count(db, admin_client):
    _sale(db, "a", 1, "v1", voided=True)
    db.commit()
    body = admin_client.get(REORDER).json()
    assert body["has_sales_data"] is False
    assert body["has_stock_data"] is False


def test_store_with_data_everything_stocked(db, admin_client):
    """Sales and plenty on hand: the list is empty, but the flags say the data
    is there - "Nothing needs reordering right now"."""
    _snap(db, "a", 500)
    _sale(db, "a", 1, "s1")
    db.commit()
    body = admin_client.get(REORDER).json()
    assert body["candidates"] == []
    assert body["has_sales_data"] is True
    assert body["has_stock_data"] is True


def test_flags_are_on_the_critical_only_report_too(db, admin_client):
    _snap(db, "a", 500)
    db.commit()
    body = admin_client.get(REORDER, params={"critical_only": 1}).json()
    assert body["has_sales_data"] is False
    assert body["has_stock_data"] is True
