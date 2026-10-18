"""Oct 14 batch W3: average on-hand count beside each week of an item's
weekly sales (GET /api/ordering/item-weekly-sales, services/weekly_sales.py).

Covers: the average uses only days with a tracked count (and says how many),
the Container-location twin is added in, "-" (None) when no count, partial
first/last weeks, include_empty_weeks (and its default off for old app
builds), hide-sales users keep on-hand, and the widened feature gate."""

import datetime as dt

import pytest

from app.models import InventorySnapshot, Order
from app.services.weekly_sales import container_twin_id, daily_on_hand, weekly_sales_for_item
from tests.conftest import client_for, make_user

ITEM = "item-rice"
CONT = "item-rice-container"


def _snap(db, item_id, day, qty, name="Rice 20lb", price=19.99, supplier="Raja Foods", source="file.csv"):
    db.add(
        InventorySnapshot(
            snapshot_date=dt.date.fromisoformat(day),
            item_id=item_id,
            name=name,
            inventory_quantity=qty,
            price=price,
            supplier=supplier,
            source_filename=source,
        )
    )


def _sale(db, day, qty, net=10.0, guid=None, item_id=ITEM, parent=None, voided=False):
    guid = guid or f"{item_id}-{day}-{qty}-{parent}"
    db.add(
        Order(
            toast_order_guid="o-" + guid,
            toast_check_guid="c-" + guid,
            toast_selection_guid=guid,
            business_date=dt.date.fromisoformat(day),
            item_guid=item_id,
            item_name="Rice 20lb",
            quantity=qty,
            unit_price=net / qty,
            net_price=net,
            parent_selection_guid=parent,
            voided=voided,
        )
    )


def _week(rows, start):
    return next(r for r in rows if r["week_start"] == start)


def test_average_counts_only_tracked_days_and_says_how_many(db):
    # Week of Mon Sep 7 - Sun Sep 13: counts on 6 days (Sep 10 blank), 7th day missing.
    for day, qty in [("2026-09-07", 10), ("2026-09-08", 20), ("2026-09-09", 30),
                     ("2026-09-11", 40), ("2026-09-12", 50), ("2026-09-13", 60)]:
        _snap(db, ITEM, day, qty)
    _snap(db, ITEM, "2026-09-10", None)  # listed, no count -> not a tracked day
    _sale(db, "2026-09-08", 4)
    _sale(db, "2026-09-12", 6)
    db.commit()

    rows = weekly_sales_for_item(db, ITEM, dt.date(2026, 9, 7), dt.date(2026, 9, 14))
    assert len(rows) == 1
    w = rows[0]
    assert w["quantity"] == 10
    assert w["avg_on_hand"] == 35.0  # (10+20+30+40+50+60)/6
    assert w["on_hand_days"] == 6
    assert w["period_days"] == 7


def test_container_twin_is_added_on_days_it_has_a_count(db):
    _snap(db, ITEM, "2026-09-07", 16)
    _snap(db, CONT, "2026-09-07", 68, price=None, supplier=None)
    _snap(db, ITEM, "2026-09-08", 10)  # container has no row that day -> shelf only
    # Latest upload: the pairing is read from here (one bare row, same name).
    _snap(db, ITEM, "2026-09-14", 9)
    _snap(db, CONT, "2026-09-14", 60, price=None, supplier="")
    _sale(db, "2026-09-07", 2)
    db.commit()

    assert container_twin_id(db, ITEM) == CONT
    counts, cont = daily_on_hand(db, ITEM, dt.date(2026, 9, 7), dt.date(2026, 9, 14))
    assert cont == CONT
    assert counts == {dt.date(2026, 9, 7): 84.0, dt.date(2026, 9, 8): 10.0}
    w = weekly_sales_for_item(db, ITEM, dt.date(2026, 9, 7), dt.date(2026, 9, 14))[0]
    assert w["avg_on_hand"] == 47.0 and w["on_hand_days"] == 2


def test_container_on_its_own_day_does_not_make_a_tracked_day(db):
    _snap(db, CONT, "2026-09-07", 68, price=None, supplier=None)
    _snap(db, ITEM, "2026-09-14", 9)
    _snap(db, CONT, "2026-09-14", 60, price=None, supplier=None)
    _sale(db, "2026-09-07", 2)
    db.commit()
    w = weekly_sales_for_item(db, ITEM, dt.date(2026, 9, 7), dt.date(2026, 9, 14))[0]
    assert w["avg_on_hand"] is None and w["on_hand_days"] == 0


def test_ambiguous_or_priced_twins_are_not_merged(db):
    # Two bare rows with the name: ambiguous, skipped (same rule as Supplier Projection).
    _snap(db, ITEM, "2026-09-14", 9)
    _snap(db, CONT, "2026-09-14", 60, price=None, supplier=None)
    _snap(db, "other-bare", "2026-09-14", 5, price=None, supplier=None)
    db.commit()
    assert container_twin_id(db, ITEM) is None


def test_priced_second_row_is_a_different_product(db):
    _snap(db, ITEM, "2026-09-14", 9)
    _snap(db, "item-rice-other", "2026-09-14", 55, price=21.0, supplier=None)
    db.commit()
    assert container_twin_id(db, ITEM) is None


def test_no_counts_means_none_and_negative_counts_average_as_is(db):
    _sale(db, "2026-09-08", 3)
    _snap(db, ITEM, "2026-09-15", -4)
    _snap(db, ITEM, "2026-09-16", 2)
    _sale(db, "2026-09-15", 1)
    db.commit()
    rows = weekly_sales_for_item(db, ITEM, dt.date(2026, 9, 7), dt.date(2026, 9, 21))
    assert _week(rows, "2026-09-07")["avg_on_hand"] is None
    assert _week(rows, "2026-09-07")["on_hand_days"] == 0
    assert _week(rows, "2026-09-14")["avg_on_hand"] == -1.0
    assert _week(rows, "2026-09-14")["on_hand_days"] == 2


def test_partial_weeks_at_the_window_edges(db):
    # Window Wed Sep 9 .. Tue Sep 15 (end exclusive Sep 16).
    for day in ["2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-15", "2026-09-16"]:
        _snap(db, ITEM, day, 100 if day < "2026-09-09" or day > "2026-09-15" else 10)
    _sale(db, "2026-09-09", 1)
    _sale(db, "2026-09-15", 1)
    db.commit()
    rows = weekly_sales_for_item(db, ITEM, dt.date(2026, 9, 9), dt.date(2026, 9, 16))
    first, second = rows
    assert first["week_start"] == "2026-09-07" and first["period_days"] == 5  # Wed-Sun
    assert first["avg_on_hand"] == 10.0 and first["on_hand_days"] == 2  # Sep 7/8 are outside
    assert second["week_start"] == "2026-09-14" and second["period_days"] == 2  # Mon-Tue
    assert second["avg_on_hand"] == 10.0 and second["on_hand_days"] == 1


def test_modifier_and_voided_lines_are_still_not_sales(db):
    _snap(db, ITEM, "2026-09-08", 5)
    _sale(db, "2026-09-08", 2)
    _sale(db, "2026-09-08", 7, parent="p1")
    _sale(db, "2026-09-08", 9, voided=True)
    db.commit()
    w = weekly_sales_for_item(db, ITEM, dt.date(2026, 9, 7), dt.date(2026, 9, 14))[0]
    assert w["quantity"] == 2


def test_empty_weeks_only_when_asked_and_not_before_the_item_existed(db):
    # Window Aug 24 .. Sep 21: no data in Aug 24 week; counts from Aug 31; sale only Sep 14.
    for day in ["2026-08-31", "2026-09-01", "2026-09-08", "2026-09-15"]:
        _snap(db, ITEM, day, 12)
    _sale(db, "2026-09-15", 3)
    db.commit()
    start, end = dt.date(2026, 8, 24), dt.date(2026, 9, 21)

    default = weekly_sales_for_item(db, ITEM, start, end)
    assert [r["week_start"] for r in default] == ["2026-09-14"]

    rows = weekly_sales_for_item(db, ITEM, start, end, include_empty_weeks=True)
    assert [r["week_start"] for r in rows] == ["2026-08-31", "2026-09-07", "2026-09-14"]
    assert rows[0]["quantity"] == 0 and rows[0]["revenue"] == 0
    assert rows[0]["avg_on_hand"] == 12.0 and rows[0]["on_hand_days"] == 2
    assert rows[1]["quantity"] == 0 and rows[1]["on_hand_days"] == 1


def test_empty_weeks_after_the_last_activity_stay(db):
    _sale(db, "2026-09-01", 1)
    db.commit()
    rows = weekly_sales_for_item(db, ITEM, dt.date(2026, 8, 31), dt.date(2026, 9, 21), include_empty_weeks=True)
    assert [r["week_start"] for r in rows] == ["2026-08-31", "2026-09-07", "2026-09-14"]
    assert rows[2]["avg_on_hand"] is None


def test_nothing_at_all_is_an_empty_list(db):
    assert weekly_sales_for_item(db, ITEM, dt.date(2026, 9, 7), dt.date(2026, 9, 21), include_empty_weeks=True) == []
    assert weekly_sales_for_item(db, ITEM, dt.date(2026, 9, 21), dt.date(2026, 9, 7), include_empty_weeks=True) == []


# --- route ------------------------------------------------------------------------------


@pytest.fixture
def seeded(db):
    _snap(db, ITEM, "2026-09-07", 16)
    _snap(db, CONT, "2026-09-07", 68, price=None, supplier=None)
    _snap(db, ITEM, "2026-09-14", 9)
    _snap(db, CONT, "2026-09-14", 60, price=None, supplier=None)
    _sale(db, "2026-09-07", 3, net=9.0)
    db.commit()


URL = f"/api/ordering/item-weekly-sales?item_id={ITEM}&start=2026-08-31&end=2026-09-21"


def test_route_returns_additive_fields(db, seeded):
    admin = client_for(db, make_user(db, "boss", is_admin=True))
    body = admin.get(URL).json()
    assert body["item_id"] == ITEM
    assert body["on_hand_includes_container"] is True
    assert [w["week_start"] for w in body["weeks"]] == ["2026-09-07"]  # default: weeks with a sale
    w = body["weeks"][0]
    assert w == {
        "week_start": "2026-09-07",
        "week_end": "2026-09-13",
        "quantity": 3,
        "revenue": 9.0,
        "avg_on_hand": 84.0,
        "on_hand_days": 1,
        "period_days": 7,
    }
    full = admin.get(URL + "&include_empty_weeks=true").json()["weeks"]
    assert [w["week_start"] for w in full] == ["2026-09-07", "2026-09-14"]
    assert full[1]["avg_on_hand"] == 69.0  # 9 + 60 container


def test_route_hide_sales_keeps_on_hand(db, seeded):
    user = make_user(db, "stocker", features=["supplier_projection"])
    user.hide_sales = True
    db.commit()
    w = client_for(db, user).get(URL).json()["weeks"][0]
    assert w["revenue"] is None
    assert w["quantity"] == 3 and w["avg_on_hand"] == 84.0


@pytest.mark.parametrize("feature", ["supplier_projection", "reorder_candidates", "inventory_reports"])
def test_route_gate_takes_any_page_that_shows_it(db, seeded, feature):
    client = client_for(db, make_user(db, "u-" + feature, features=[feature]))
    assert client.get(URL).status_code == 200


def test_route_gate_refuses_others(db, seeded, anon_client):
    client = client_for(db, make_user(db, "nobody", features=["team_tasks"]))
    assert client.get(URL).status_code == 403
    assert anon_client.get(URL).status_code == 401
