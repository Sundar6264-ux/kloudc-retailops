"""MT3 verification fix: Toast order fields the sales sync never read before
M1 must not be able to fail it.

Since MT3 the nightly sales sync and Items Sold "today" map the WHOLE Toast
order (``map_order``), including ``promisedDate`` / ``estimatedFulfillmentDate``
and ``server``, which before MT3 only the open checks code read. An odd value
there (a number, an object, a string server) made the old sync write its rows
as usual, but made ``map_order`` raise - failing the whole business date (and
Items Sold today with a 503). It now maps to None, and the rows stay exactly
the pre-MT3 ones. No network: Toast's fetch is replaced.
"""

import datetime as dt

import pytest

from app.models import Order as OrderRow
from app.pos.adapters.toast import orders as toast_orders
from app.pos.sync import sales as sales_new
from app.services import sales_sync
from tests import pre_mt3_reference as ref

DAY = dt.date(2026, 10, 14)


def _raw(**extra):
    order = {
        "guid": "o-1",
        "openedDate": "2026-10-14T18:00:00.000+0000",
        "createdDate": "2026-10-14T17:59:00.000+0000",
        "paidDate": "2026-10-14T18:05:00.000+0000",
        "checks": [
            {
                "guid": "c-1",
                "totalAmount": 5.3,
                "selections": [
                    {"guid": "s-1", "quantity": 1, "price": 5.0, "tax": 0.3, "item": {"guid": "i-1"}, "displayName": "Tea"}
                ],
            }
        ],
    }
    order.update(extra)
    return order


ODD = [
    {"promisedDate": 1760465100000},
    {"promisedDate": {"at": "x"}},
    {"estimatedFulfillmentDate": ["2026-10-14T18:00:00.000+0000"]},
    {"server": "emp-1"},
    {"server": ["emp-1"]},
    {"server": "emp-1", "promisedDate": 12},
]


@pytest.mark.parametrize("extra", ODD)
def test_odd_unused_fields_give_the_pre_mt3_rows(extra):
    raw = _raw(**extra)
    order = toast_orders.map_order(raw, DAY)
    assert order.promised_at is None and order.server_external_id is None
    assert repr(sales_new.line_rows(order)) == repr(ref._extract_line_items(raw))
    assert repr(sales_sync._extract_line_items(raw)) == repr(ref._extract_line_items(raw))


def test_well_formed_promise_and_server_unchanged():
    raw = _raw(promisedDate="2026-10-14T22:00:00.000+0000", server={"guid": "emp-1"})
    order = toast_orders.map_order(raw, DAY)
    assert order.promised_at == ref._promised_at(raw)
    assert order.server_external_id == "emp-1"
    assert toast_orders.map_order(_raw(server=None), DAY).server_external_id is None
    assert toast_orders.map_order(_raw(server={}), DAY).server_external_id is None


def test_nightly_sync_writes_the_date_despite_odd_fields(db, monkeypatch):
    payload = [_raw(**ODD[-1]), dict(_raw(), guid="o-2", checks=[dict(_raw()["checks"][0], guid="c-2", selections=[
        {"guid": "s-2", "quantity": 2, "price": 8.0, "tax": 0.5, "item": {"guid": "i-2"}, "displayName": "Rice"}
    ])])]
    monkeypatch.setattr(sales_sync, "fetch_orders_for_business_date", lambda d: [dict(o) for o in payload])
    job = sales_sync.sync_sales_for_date(db, DAY)
    assert job.status == "success", job.detail
    assert job.detail == "2 line items synced from 2 orders"
    assert {r.toast_selection_guid for r in db.query(OrderRow).all()} == {"s-1", "s-2"}
