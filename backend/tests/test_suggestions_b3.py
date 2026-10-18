"""Automation batch B3 (docs/AUTOMATION_BATCH_CONTRACT.md items 4, 5, 7, 9; "As built - B3").

1. Suggested deliveries: Toast receiving records group into a vendor (votes, tie-break by the last invoice upload),
   quantity = count change + units sold between the snapshots, untracked / container / count-edit / old receipts,
   the stock-rise rule on API-only snapshot days (cluster, transfers, recounts from below zero, weighed noise,
   kitchen items, API out-of-stock rows, invoice evidence), handled vendor-days, dismiss / undo, the API and its gate,
   and that confirming writes exactly what the manual Delivery Review confirm writes.
2. Suggested "Closed in Toast": receipt after the upload, oldest invoice first, closed invoices keep their receipt,
   returns never, the invoice list's additive fields, /close-suggestions, dismiss / undo, one tap closes.
3. Suggested vendor schedules: weekday pattern from invoice uploads, Toast receipts and the purchase log, thresholds,
   lead days from placed orders, never for a vendor with a schedule (a read-only check instead), dismiss / undo,
   Accept goes through POST /schedules (which refuses a second schedule).
4. Migration 0049 up / down / up. Nothing here calls Toast: every input is a row in the test database.
"""

import datetime as dt
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import zoneinfo

import pytest

from app.db import Base
from app.models import (
    InventorySnapshot,
    InvoiceSubmission,
    JobRun,
    Order,
    PlacedOrder,
    PurchaseLogEntry,
    SuggestionDismissal,
    VendorSchedule,
)
from app.services import delivery_suggestions as ds
from app.services import invoice_close_suggestions as ics
from app.services import schedule_suggestions as ss
from app.services.delivery_review import confirm_delivery
from app.services.sales_sync import JOB_NAME as SALES_JOB
from tests.conftest import client_for, make_user

# Made-up rules in the shape these tests were written for: tests/fixtures/packs/suite.json.
pytestmark = pytest.mark.suite_pack

BACKEND = pathlib.Path(__file__).resolve().parents[1]
NY = zoneinfo.ZoneInfo("America/New_York")
UTC = dt.timezone.utc
P = dt.date(2026, 10, 5)  # Monday
C = dt.date(2026, 10, 6)


def ny(day, h=0, mi=0):
    return dt.datetime.combine(day, dt.time(h, mi), NY).astimezone(UTC)


def toast_time(value: dt.datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@pytest.fixture(autouse=True)
def _clear_caches():
    ds.clear_cache()
    ics.clear_cache()
    yield
    ds.clear_cache()
    ics.clear_cache()


def snap(
    db,
    day,
    item_id,
    qty,
    supplier="Raja Foods",
    name=None,
    price=2.0,
    received=None,
    receipt=None,
    source="retail-items.csv",
    uploaded=None,
    group="RETAIL",
    api_oos=None,
    cost=1.5,
    supplier_item_id=None,
):
    row = InventorySnapshot(
        snapshot_date=day,
        item_id=item_id,
        name=name or item_id,
        category_group=group,
        category="Cat",
        inventory_quantity=qty,
        supplier=supplier,
        supplier_item_id=supplier_item_id,
        cost=cost,
        price=price,
        inventory_last_received=toast_time(received) if isinstance(received, dt.datetime) else received,
        last_received_from=receipt,
        source_filename=source,
        uploaded_at=uploaded or ny(day, 2),
    )
    if api_oos is not None:
        row.api_out_of_stock = api_oos
    db.add(row)
    return row


def sold(db, day, item_id, qty, n=[0]):
    n[0] += 1
    db.add(
        Order(
            toast_order_guid=f"o{n[0]}",
            toast_check_guid=f"c{n[0]}",
            toast_selection_guid=f"s{n[0]}",
            business_date=day,
            item_guid=item_id,
            item_name=item_id,
            quantity=qty,
            unit_price=1,
            net_price=qty,
            tax_amount=0,
            voided=False,
        )
    )


def synced(db, *days):
    for day in days:
        db.add(JobRun(job_name=SALES_JOB, status="success", business_date=day))


def invoice(db, vendor, created, closed=None, kind="invoice", sent=True, n=[0]):
    n[0] += 1
    entry = InvoiceSubmission(
        vendor=vendor,
        submission_type=kind,
        original_filename="i.pdf",
        stored_filename=f"i{n[0]}.pdf",
        file_token=f"tok{n[0]}",
        whatsapp_sent=sent,
        toast_email_sent=sent,
        toast_closed_at=closed,
        toast_closed_by_name="boss" if closed else None,
        created_at=created,
        submitted_by_name="ravi",
        submitted_by_user_id=None,
    )
    db.add(entry)
    db.commit()
    return entry


def groups_by_vendor(result):
    return {g["vendor"]: g for g in result["groups"]}


# --- 1. Suggested deliveries ----------------------------------------------------------------


def test_parse_toast_time_variants():
    assert ds.parse_toast_time("2026-10-10T14:37:21.828895Z") == dt.datetime(2026, 10, 10, 14, 37, 21, 828895, UTC)
    assert ds.parse_toast_time("2026-10-10T14:37:21.8Z") == dt.datetime(2026, 10, 10, 14, 37, 21, 800000, UTC)
    assert ds.parse_toast_time("2026-10-10T14:37:21Z") == dt.datetime(2026, 10, 10, 14, 37, 21, tzinfo=UTC)
    assert ds.parse_toast_time("2026-10-10 14:37:21") == dt.datetime(2026, 10, 10, 14, 37, 21, tzinfo=UTC)
    assert ds.parse_toast_time("") is None and ds.parse_toast_time(None) is None
    assert ds.parse_toast_time("not a date") is None


def _receipt_day(db):
    """Prev snapshot P, current C; Toast received IN378 (Raja Foods) on P at 2 PM."""
    at = ny(P, 14)
    old = ny(P - dt.timedelta(days=30), 12)
    # Received, count went 10 -> 30, 4 sold in between: 24 arrived.
    snap(db, P, "rice", 10, received=old, receipt="IN100", supplier_item_id="R-1")
    snap(db, C, "rice", 30, received=at, receipt="IN378", supplier_item_id="R-1")
    sold(db, P, "rice", 4)
    # Multi-vendor item Toast lists under another vendor: still Raja's delivery.
    snap(db, P, "dal", 5, supplier="Kotecha;Raja Foods")
    snap(db, C, "dal", 17, supplier="Kotecha;Raja Foods", received=at, receipt="IN378")
    # Listed only under Kotecha (not Raja): note says so.
    snap(db, P, "leaves", 1, supplier="Kotecha")
    snap(db, C, "leaves", 6, supplier="Kotecha", received=at, receipt="IN378")
    # Untracked item received: no count -> suggested_qty None, not preselected.
    snap(db, P, "flowers", None)
    snap(db, C, "flowers", None, received=at, receipt="IN378")
    # Brand new item received.
    snap(db, C, "newthing", 12, received=at, receipt="IN378")
    # A count edit (receive time, no receiving number) is not a delivery.
    snap(db, P, "edited", 3)
    snap(db, C, "edited", 40, received=ny(P, 23), receipt=None)
    # Receive time older than the previous snapshot (carried on a new row): not this pair.
    snap(db, C, "olditem", 8, received=ny(P - dt.timedelta(days=3), 10), receipt="IN360")
    # Stock rose without any receiving record: a recount on a CSV day, not suggested.
    snap(db, P, "recount", 2)
    snap(db, C, "recount", 30)
    # Another receipt, another vendor.
    snap(db, P, "milk", 0, supplier="Chetak")
    snap(db, C, "milk", 20, supplier="Chetak", received=ny(P, 16), receipt="IN379")
    synced(db, P)
    db.commit()


def test_toast_received_groups_and_quantities(db):
    _receipt_day(db)
    result = ds.compute_suggestions(db, days=1)
    assert result["through"] == C.isoformat()
    groups = groups_by_vendor(result)
    assert set(groups) == {"Raja Foods", "Chetak"}
    raja = groups["Raja Foods"]
    assert raja["evidence"] == "toast_received"
    assert raja["received_date"] == C.isoformat() and raja["prev_date"] == P.isoformat()
    assert raja["key"] == f"{C.isoformat()}|raja foods"
    assert [r["id"] for r in raja["toast_receipts"]] == ["IN378"]
    assert raja["sales_complete"] is True
    items = {i["item_id"]: i for i in raja["items"]}
    assert set(items) == {"rice", "dal", "leaves", "flowers", "newthing"}
    rice = items["rice"]
    assert (rice["prev_qty"], rice["today_qty"], rice["delta"], rice["sold_between"]) == (10, 30, 20, 4)
    assert rice["suggested_qty"] == 24 and rice["preselected"] is True
    assert rice["supplier_item_id"] == "R-1" and rice["vendor_listed"] is True
    assert items["dal"]["suggested_qty"] == 12 and items["dal"]["vendor_listed"] is True
    assert items["leaves"]["vendor_listed"] is False and "Kotecha" in items["leaves"]["note"]
    assert items["flowers"]["untracked"] is True and items["flowers"]["suggested_qty"] is None
    assert items["flowers"]["preselected"] is False
    assert items["newthing"]["is_new"] is True and items["newthing"]["suggested_qty"] == 12
    assert "Toast received" in raja["reason"] and "IN378" in raja["reason"]


def test_receipt_vote_tie_goes_to_last_invoice_upload(db):
    at = ny(P, 9)
    for item in ("tomato", "onion"):
        snap(db, P, item, 5, supplier="Raja Foods;RBest;Restaurant Depot")
        snap(db, C, item, 25, supplier="Raja Foods;RBest;Restaurant Depot", received=at, receipt="IN359")
    invoice(db, "Raja Foods", ny(P - dt.timedelta(days=5), 12))
    invoice(db, "Restaurant Depot", ny(P - dt.timedelta(days=1), 12))
    invoice(db, "RBest", ny(P, 14))  # well after the receiving: doesn't count
    db.commit()
    assert set(groups_by_vendor(ds.compute_suggestions(db, days=1))) == {"Restaurant Depot"}


def test_handled_vendor_day_is_not_suggested_and_confirm_matches_manual(db, admin_client):
    _receipt_day(db)
    db.add(InventorySnapshot(snapshot_date=C, item_id="x", name="x"))  # unrelated
    db.commit()
    before = admin_client.get("/api/reconciliation/suggested-deliveries?days=1").json()
    raja = groups_by_vendor(before)["Raja Foods"]
    picked = [i for i in raja["items"] if i["preselected"]]
    body = {
        "vendor": raja["vendor"],
        "received_date": raja["received_date"],
        "items": [{"item_id": i["item_id"], "item_name": i["name"], "quantity_received": i["suggested_qty"]} for i in picked],
    }
    r = admin_client.post("/api/reconciliation/delivery-confirm", json=body)
    assert r.status_code == 200 and r.json() == {"logged": len(picked)}
    rows = db.query(PurchaseLogEntry).order_by(PurchaseLogEntry.item_id).all()
    assert {(e.item_id, e.supplier, e.quantity_received, e.received_date, e.unit_cost) for e in rows} == {
        (i["item_id"], "Raja Foods", i["suggested_qty"], C, 1.5) for i in picked
    }
    # Same notes as a manual Delivery Review confirm (it is the same function).
    assert {e.notes for e in rows} == {
        "Confirmed via Delivery Review (qty suggested from inventory count diff, cost captured from that day's "
        "inventory file)"
    }
    after = admin_client.get("/api/reconciliation/suggested-deliveries?days=1").json()
    assert set(groups_by_vendor(after)) == {"Chetak"}
    assert after["counts"]["handled"] == 1


def test_manual_log_the_day_before_counts_as_handled(db):
    _receipt_day(db)
    db.add(PurchaseLogEntry(item_id="milk", supplier="chetak ", quantity_received=20, received_date=P, notes="by hand"))
    db.commit()
    assert set(groups_by_vendor(ds.compute_suggestions(db, days=1))) == {"Raja Foods"}


def test_sales_not_synced_is_flagged(db):
    _receipt_day(db)
    db.query(JobRun).delete()
    db.commit()
    assert all(g["sales_complete"] is False for g in ds.compute_suggestions(db, days=1)["groups"])


def _api_day(db):
    """C is an API-built snapshot (no new receiving records): the stock-rise rule."""
    api = "api:2026-10-06T08:05:00Z"
    for i in range(3):
        snap(db, P, f"babco{i}", 2, supplier="Babco")
        snap(db, C, f"babco{i}", 12, supplier="Babco", source=api)
    sold(db, P, "babco0", 3)
    # Only one Chetak item rose: a recount, not a delivery.
    snap(db, P, "chetak1", 0, supplier="Chetak")
    snap(db, C, "chetak1", 20, supplier="Chetak", source=api)
    # Recount from below zero: -10 -> 2 is not "+12 delivered".
    snap(db, P, "neg", -10, supplier="Babco")
    snap(db, C, "neg", 2, supplier="Babco", source=api)
    # Shelf rose, its Container twin dropped: a move. Pair = same name, bare row.
    snap(db, P, "shelf", 1, supplier="Babco", name="Rice 20lb")
    snap(db, C, "shelf", 21, supplier="Babco", name="Rice 20lb", source=api)
    snap(db, P, "cont", 50, supplier=None, price=None, name="Rice 20lb")
    snap(db, C, "cont", 30, supplier=None, price=None, name="Rice 20lb", source=api)
    # Weighed item: +6 lb on 100 lb is re-weighing noise.
    snap(db, P, "goat", 100.5, supplier="Babco")
    snap(db, C, "goat", 106.5, supplier="Babco", source=api)
    # Kitchen-made counts are production.
    snap(db, P, "samosa", 0, supplier="Babco", group="KITCHEN")
    snap(db, C, "samosa", 40, supplier="Babco", group="KITCHEN", source=api)
    # API out of stock: real count unknown.
    snap(db, P, "oos", -20, supplier="Babco")
    snap(db, C, "oos", 0, supplier="Babco", source=api, api_oos=True)
    sold(db, P, "oos", 30)
    synced(db, P)
    db.commit()


def test_stock_rise_rule_on_api_days(db):
    _api_day(db)
    result = ds.compute_suggestions(db, days=1)
    groups = groups_by_vendor(result)
    assert set(groups) == {"Babco"}
    babco = groups["Babco"]
    assert babco["evidence"] == "stock_rise"
    assert sorted(i["item_id"] for i in babco["items"]) == ["babco0", "babco1", "babco2"]
    assert {i["item_id"]: i["suggested_qty"] for i in babco["items"]} == {"babco0": 13, "babco1": 10, "babco2": 10}
    assert "nightly Toast snapshot" in babco["reason"]
    assert result["counts"]["skipped_container_transfer"] == 1
    assert result["counts"]["skipped_weighed_noise"] == 1
    assert result["counts"]["skipped_too_few_items"] == 1  # Chetak's one item


def test_one_rising_item_is_suggested_when_the_vendor_uploaded_an_invoice(db):
    _api_day(db)
    invoice(db, "Chetak", ny(P, 15))
    groups = groups_by_vendor(ds.compute_suggestions(db, days=1))
    assert set(groups) == {"Babco", "Chetak"}
    assert "invoice was uploaded" in groups["Chetak"]["reason"]


def test_csv_day_ignores_rises_without_receipts(db):
    _api_day(db)
    db.query(InventorySnapshot).update({InventorySnapshot.source_filename: "retail-items.csv"})
    db.commit()
    assert ds.compute_suggestions(db, days=1)["groups"] == []


def test_dismiss_and_undo(db, admin_client):
    _receipt_day(db)
    key = f"{C.isoformat()}|raja foods"
    r = admin_client.post("/api/reconciliation/suggested-deliveries/dismiss", json={"key": key})
    assert r.status_code == 200 and r.json()["dismissed"] is True
    assert admin_client.post("/api/reconciliation/suggested-deliveries/dismiss", json={"key": key}).status_code == 200
    assert db.query(SuggestionDismissal).count() == 1
    body = admin_client.get("/api/reconciliation/suggested-deliveries?days=1").json()
    assert set(groups_by_vendor(body)) == {"Chetak"} and body["counts"]["dismissed"] == 1
    shown = admin_client.get("/api/reconciliation/suggested-deliveries?days=1&include_dismissed=true").json()
    assert groups_by_vendor(shown)["Raja Foods"]["dismissed"] is True
    assert admin_client.delete(f"/api/reconciliation/suggested-deliveries/dismiss?key={key}").status_code == 200
    assert admin_client.delete(f"/api/reconciliation/suggested-deliveries/dismiss?key={key}").status_code == 404
    assert "Raja Foods" in groups_by_vendor(admin_client.get("/api/reconciliation/suggested-deliveries?days=1").json())


def test_endpoint_gate_and_empty(db, staff_client):
    assert staff_client.get("/api/reconciliation/suggested-deliveries").status_code == 403
    user = make_user(db, "dr", features=["delivery_review"])
    c = client_for(db, user)
    body = c.get("/api/reconciliation/suggested-deliveries").json()
    assert body["groups"] == [] and body["through"] is None
    assert c.get("/api/reconciliation/suggested-deliveries?days=0").status_code == 422
    assert c.get("/api/reconciliation/suggested-deliveries?days=32").status_code == 422


def test_cached_result_refreshes_after_confirm(db):
    _receipt_day(db)
    first = ds.get_suggested_deliveries(db, days=1)
    assert ds.get_suggested_deliveries(db, days=1) is first  # cached
    confirm_delivery(db, "Chetak", C, [{"item_id": "milk", "item_name": "milk", "quantity_received": 20}], None)
    assert set(groups_by_vendor(ds.get_suggested_deliveries(db, days=1))) == {"Raja Foods"}


# --- 2. Suggested "Closed in Toast" ---------------------------------------------------------


def _receipts(db, *events, _n=[0]):
    """events: (vendor, receive time, receiving number) -> a snapshot the next day."""
    for vendor, at, receipt in events:
        _n[0] += 1
        day = at.astimezone(NY).date() + dt.timedelta(days=1)
        snap(db, day, f"{vendor}-{_n[0]}", 10, supplier=vendor, received=at, receipt=receipt)
    db.commit()


def test_close_suggestion_after_receipt(db, admin_client):
    inv = invoice(db, "Best Foods", ny(C, 19))
    _receipts(db, ("Best Foods", ny(C + dt.timedelta(days=1), 10), "IN378"))
    body = admin_client.get("/api/invoices").json()
    item = next(i for i in body["items"] if i["id"] == inv.id)
    s = item["close_suggestion"]
    assert s["evidence"] == "toast_received" and s["receipt_id"] == "IN378" and s["key"] == str(inv.id)
    assert "IN378" in s["reason"] and "Best Foods" in s["reason"]
    assert body["close_suggested_count"] == 1
    listed = admin_client.get("/api/invoices/close-suggestions").json()
    assert listed["count"] == 1 and listed["items"][0]["submission_id"] == inv.id
    # One tap: the existing route; then the suggestion is gone.
    assert admin_client.post(f"/api/invoices/{inv.id}/mark-toast-closed").status_code == 200
    body = admin_client.get("/api/invoices").json()
    assert body["close_suggested_count"] == 0
    assert all(i["close_suggestion"] is None for i in body["items"])


def test_receipt_before_upload_or_other_vendor_is_not_evidence(db):
    inv = invoice(db, "Best Foods", ny(C, 19))
    _receipts(db, ("Best Foods", ny(C, 10), "IN300"), ("Chetak", ny(C, 22), "IN301"))
    assert ics.compute_close_suggestions(db) == {}
    # Within the grace (receipt 2 h before the upload) it counts.
    _receipts(db, ("Best Foods", inv.created_at - dt.timedelta(hours=2), "IN302"))
    assert ics.compute_close_suggestions(db)[inv.id]["receipt_id"] == "IN302"


def test_oldest_invoice_first_and_closed_invoices_keep_their_receipt(db):
    closed = invoice(db, "Raja Foods", ny(P, 12), closed=ny(C, 12))
    older = invoice(db, "Raja Foods", ny(P, 13))
    newer = invoice(db, "Raja Foods", ny(C, 13))
    _receipts(db, ("Raja Foods", ny(P, 18), "IN1"), ("Raja Foods", ny(C, 18), "IN2"))
    found = ics.compute_close_suggestions(db)
    # IN1 goes to the closed invoice, IN2 to the older open one; the newer has none.
    assert set(found) == {older.id} and found[older.id]["receipt_id"] == "IN2"
    assert closed.id not in found and newer.id not in found


def test_closed_invoice_never_takes_a_receipt_from_after_its_close(db):
    """Verify 1 (Restaurant Depot #58 on the prod copy): an older invoice closed
    with no receipt of its own must not swallow the next receiving - that one
    belongs to the open invoice uploaded after it."""
    closed = invoice(db, "Raja Foods", ny(P, 12), closed=ny(P, 14))
    newer = invoice(db, "Raja Foods", ny(C, 13))
    _receipts(db, ("Raja Foods", ny(C, 18), "IN9"))
    found = ics.compute_close_suggestions(db)
    assert set(found) == {newer.id} and found[newer.id]["receipt_id"] == "IN9"
    assert closed.id not in found
    # Within UPLOAD_GRACE of the close it is still the closed invoice's own.
    ics.clear_cache()
    other = invoice(db, "Vadilal", ny(P, 12), closed=ny(C, 16))
    after = invoice(db, "Vadilal", ny(C, 13))
    _receipts(db, ("Vadilal", ny(C, 18), "IN10"))
    found = ics.compute_close_suggestions(db)
    assert other.id not in found and after.id not in found


def test_returns_unsent_and_dismissed_are_not_suggested(db, admin_client):
    ret = invoice(db, "Vadilal", ny(C, 9), kind="return")
    unsent = invoice(db, "Vadilal", ny(C, 9), sent=False)
    inv = invoice(db, "Vadilal", ny(C, 10))
    _receipts(db, ("Vadilal", ny(C, 15), "IN5"), ("Vadilal", ny(C, 16), "IN6"))
    found = ics.compute_close_suggestions(db)
    assert set(found) == {inv.id}
    assert ret.id not in found and unsent.id not in found
    r = admin_client.post(f"/api/invoices/{inv.id}/close-suggestion/dismiss")
    assert r.status_code == 200
    assert admin_client.get("/api/invoices").json()["close_suggested_count"] == 0
    assert admin_client.delete(f"/api/invoices/{inv.id}/close-suggestion/dismiss").status_code == 200
    assert admin_client.get("/api/invoices").json()["close_suggested_count"] == 1
    assert admin_client.post("/api/invoices/99999/close-suggestion/dismiss").status_code == 404


def test_stock_rise_evidence_on_api_snapshot(db):
    inv = invoice(db, "Babco", ny(P, 12))
    _api_day(db)
    found = ics.compute_close_suggestions(db)
    assert found[inv.id]["evidence"] == "stock_rise" and found[inv.id]["receipt_id"] is None


def test_list_without_any_suggestion_keeps_old_shape(db, admin_client):
    invoice(db, "OnTime", ny(C, 12))
    body = admin_client.get("/api/invoices").json()
    assert body["count"] == 1 and body["close_suggested_count"] == 0
    item = body["items"][0]
    assert item["close_suggestion"] is None and item["can_mark_toast_closed"] is True


# --- 3. Suggested vendor schedules ----------------------------------------------------------

TODAY = dt.date(2026, 10, 11)  # Sunday


def _thursdays(n):
    first = TODAY - dt.timedelta(days=(TODAY.weekday() - 3) % 7)
    return [first - dt.timedelta(weeks=i) for i in range(n)]


def test_weekday_pattern_from_invoice_uploads(db):
    for day in _thursdays(4):
        invoice(db, "Raja Foods", ny(day, 15))
    invoice(db, "Raja Foods", ny(_thursdays(1)[0] + dt.timedelta(days=1), 15))  # one Friday
    for day in _thursdays(3):
        db.add(PlacedOrder(supplier="Raja Foods", item_count=1, items_json="[]", placed_at=ny(day - dt.timedelta(days=1), 10), status="placed"))
    db.add(PlacedOrder(supplier="Raja Foods", item_count=1, items_json="[]", placed_at=ny(_thursdays(1)[0] - dt.timedelta(days=2), 10), status="pdf"))
    db.commit()
    result = ss.compute_schedule_suggestions(db, today=TODAY)
    [s] = result["suggestions"]
    assert s["vendor"] == "Raja Foods" and s["delivery_weekdays"] == [3] and s["weekday_names"] == ["Thu"]
    assert s["lead_days"] == 1 and s["lead_days_source"] == "placed_orders" and s["order_weekdays"] == [2]
    # X2 (Oct 14): every source is merged into deliveries ("combined").
    assert s["evidence"]["source"] == "combined" and (s["evidence"]["covered"], s["evidence"]["total"]) == (4, 5)
    assert s["evidence"]["sources"]["invoices"] == 5
    assert s["reason"].startswith("Raja Foods usually delivers on Thu: 4 of the last 5 deliveries")


def test_no_pattern_no_suggestion(db):
    # Random days (a cash-and-carry), and too few uploads.
    for offset in (1, 3, 9, 12, 20, 26):
        invoice(db, "Restaurant Depot", ny(TODAY - dt.timedelta(days=offset), 12))
    invoice(db, "Tiny", ny(_thursdays(1)[0], 12))  # one delivery is never a pattern
    db.commit()
    result = ss.compute_schedule_suggestions(db, today=TODAY)
    assert result["suggestions"] == []
    without = {r["vendor"]: r for r in result["vendors_without_schedule"]}
    assert without["Restaurant Depot"]["why"] == "scattered" and without["Tiny"]["why"] == "few"


def test_twice_a_week_needs_three_each(db):
    for day in _thursdays(4):
        invoice(db, "Harbs", ny(day, 12))
        invoice(db, "Harbs", ny(day - dt.timedelta(days=3), 12))  # Mondays
    db.commit()
    [s] = ss.compute_schedule_suggestions(db, today=TODAY)["suggestions"]
    assert s["delivery_weekdays"] == [0, 3] and s["lead_days_source"] == "default" and s["lead_days"] == 1


def test_receipts_and_purchase_log_sources(db):
    # Toast receipts on Fridays (no invoices).
    for day in _thursdays(4):
        fri = day + dt.timedelta(days=1)
        if fri <= TODAY:
            _receipts(db, ("Chetak", ny(fri, 11), f"IN{fri.day}"))
    # Delivery Review confirms are dated the snapshot (the day after): Tue -> Mon deliveries.
    for day in _thursdays(4):
        tue = day - dt.timedelta(days=2)
        db.add(PurchaseLogEntry(item_id="a", supplier="Vdyas", quantity_received=1, received_date=tue,
                                notes="Confirmed via Delivery Review (qty ...)"))
    db.commit()
    got = {s["vendor"]: s for s in ss.compute_schedule_suggestions(db, today=TODAY)["suggestions"]}
    assert got["Chetak"]["delivery_weekdays"] == [4] and got["Chetak"]["evidence"]["sources"]["toast_receipts"] == 4
    assert got["Vdyas"]["delivery_weekdays"] == [0] and got["Vdyas"]["evidence"]["sources"]["purchase_log"] == 4


def test_existing_schedule_is_never_suggested_only_checked(db, admin_client):
    for day in _thursdays(4):
        invoice(db, "Raja Foods", ny(day, 15))
    db.add(VendorSchedule(vendor="Raja Foods", delivery_weekdays="[1]", lead_days=2, active=True))
    db.commit()
    result = admin_client.get("/api/vendor-calendar/schedule-suggestions").json()
    assert result["suggestions"] == []
    [check] = result["schedule_checks"]
    assert check["schedule_weekdays"] == [1] and check["observed_weekdays"] == [3]
    assert "left as it is" in check["reason"]
    # Accept for that vendor can't overwrite: POST /schedules refuses.
    r = admin_client.post("/api/vendor-calendar/schedules", json={"vendor": "Raja Foods", "delivery_weekdays": [3], "lead_days": 1})
    assert r.status_code == 400
    assert db.query(VendorSchedule).one().delivery_weekdays == "[1]"


def test_schedule_dismiss_accept_and_gate(db, admin_client, staff_client):
    for day in _thursdays(4):
        invoice(db, "KP Produce", ny(day, 15))
    db.commit()
    assert staff_client.get("/api/vendor-calendar/schedule-suggestions").status_code == 403
    [s] = admin_client.get("/api/vendor-calendar/schedule-suggestions").json()["suggestions"]
    r = admin_client.post("/api/vendor-calendar/schedule-suggestions/dismiss", json={"vendor": "KP Produce", "delivery_weekdays": [3]})
    assert r.status_code == 200 and r.json()["key"] == s["key"]
    after = admin_client.get("/api/vendor-calendar/schedule-suggestions").json()
    assert after["suggestions"] == [] and after["dismissed_count"] == 1
    assert admin_client.post("/api/vendor-calendar/schedule-suggestions/dismiss", json={"vendor": "KP", "delivery_weekdays": [9]}).status_code == 400
    assert admin_client.delete(f"/api/vendor-calendar/schedule-suggestions/dismiss?key={s['key']}").status_code == 200
    # Accept = the existing create; the suggestion then turns into nothing (vendor has a schedule).
    r = admin_client.post("/api/vendor-calendar/schedules", json={"vendor": s["vendor"], "delivery_weekdays": s["delivery_weekdays"], "lead_days": s["lead_days"]})
    assert r.status_code == 200
    after = admin_client.get("/api/vendor-calendar/schedule-suggestions").json()
    assert after["suggestions"] == [] and after["schedule_checks"] == []


# --- 4. Migration 0049 ----------------------------------------------------------------------


def _alembic(db_file, *args):
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_file}")
    alembic = pathlib.Path(sys.executable).parent / "alembic"
    return subprocess.run([str(alembic), *args], cwd=BACKEND, env=env, check=True, capture_output=True, text=True)


def _tables(con):
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_migration_0049_up_down_up(tmp_path):
    db_file = tmp_path / "m.db"
    _alembic(db_file, "upgrade", "0048")
    con = sqlite3.connect(db_file)
    before = _tables(con)
    con.close()
    assert "suggestion_dismissals" not in before
    _alembic(db_file, "upgrade", "0049")
    con = sqlite3.connect(db_file)
    try:
        cols = {r[1]: r for r in con.execute("PRAGMA table_info(suggestion_dismissals)")}
        assert set(cols) == {c.name for c in Base.metadata.tables["suggestion_dismissals"].columns}
        for name in ("kind", "suggestion_key", "created_at"):
            assert cols[name][3] == 1, f"{name} is NOT NULL"
        con.execute("INSERT INTO suggestion_dismissals (kind, suggestion_key, created_at) VALUES ('delivery', 'k', '2026-10-11')")
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO suggestion_dismissals (kind, suggestion_key, created_at) VALUES ('delivery', 'k', '2026-10-11')")
        con.commit()
    finally:
        con.close()
    _alembic(db_file, "downgrade", "0048")
    con = sqlite3.connect(db_file)
    try:
        assert _tables(con) == before
    finally:
        con.close()
    _alembic(db_file, "upgrade", "0049")


# --- Item 9: Transfer Review stays reachable for old app builds -----------------------------


def test_transfer_routes_still_served(db):
    user = make_user(db, "old_app", features=["transfer_review"])
    c = client_for(db, user)
    r = c.get("/api/transfers/candidates?direction=container_to_store")
    assert r.status_code in (200, 404)  # 404 = not enough snapshots, never "no such route"
    if r.status_code == 404:
        assert "snapshot" in r.json()["detail"].lower() or "upload" in r.json()["detail"].lower()


def test_glossary_quotes_the_real_thresholds():
    from app import glossary as g

    def text(key):
        t = g.TERMS_BY_KEY[key]
        return f"{t['definition']} {t['rule'] or ''}"

    assert f"{int(ds.RISE_MIN_ITEMS)} or more" in text("delivery_suggested")
    assert f"at least {int(ds.RISE_MIN_UNITS)}" in text("delivery_suggested")
    assert f"{int(ics.UPLOAD_GRACE.total_seconds() // 3600)} hours" in text("invoice_close_suggested")
    assert f"{ss.LOOKBACK_DAYS // 7} weeks" in text("schedule_suggested")
    assert f"{int(ss.MIN_COVERAGE * 100)}%" in text("schedule_suggested")
