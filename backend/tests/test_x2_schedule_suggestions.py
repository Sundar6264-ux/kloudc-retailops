"""X2 (Oct 14): suggested vendor schedules from EVERY source, confidence, "Add all", vendors without a schedule.

services/schedule_suggestions.py merges invoice uploads, hand-logged deliveries, Delivery Review confirms, Toast
receiving records (the daily snapshots' last-received history) and stock rises on API-only days into deliveries per
vendor (names matched across spellings), reads the weekday pattern, scores it, and lists every other active vendor
with why it has no suggestion. Add all never overwrites a schedule an admin set (any spelling, paused too).
Nothing here calls Toast: every input is a row in the test database.
"""

import datetime as dt
import zoneinfo

import pytest

from app.demo_guard import ALLOWLIST, DEMO_READ_ONLY
from app.models import InventorySnapshot, InvoiceSubmission, PlacedOrder, PurchaseLogEntry, VendorSchedule
from app.services import delivery_suggestions as ds
from app.services import schedule_suggestions as ss
from tests.conftest import client_for, make_user

# Made-up rules in the shape these tests were written for: tests/fixtures/packs/suite.json.
pytestmark = pytest.mark.suite_pack

NY = zoneinfo.ZoneInfo("America/New_York")
UTC = dt.timezone.utc
TODAY = dt.date(2026, 10, 14)  # Wednesday
API = "/api/vendor-calendar/schedule-suggestions"


def ny(day, h=12):
    return dt.datetime.combine(day, dt.time(h), NY).astimezone(UTC)


def weekly(weekday, weeks, skip=()):
    """The last `weeks` dates on `weekday` up to TODAY, newest first, minus `skip` (week indexes)."""
    last = TODAY - dt.timedelta(days=(TODAY.weekday() - weekday) % 7)
    return [last - dt.timedelta(weeks=i) for i in range(weeks) if i not in skip]


_n = [0]


def invoice(db, vendor, day, kind="invoice"):
    _n[0] += 1
    db.add(
        InvoiceSubmission(
            vendor=vendor,
            submission_type=kind,
            original_filename="i.pdf",
            stored_filename=f"x2-{_n[0]}.pdf",
            file_token=f"x2tok{_n[0]}",
            whatsapp_sent=True,
            toast_email_sent=True,
            created_at=ny(day, 15),
            submitted_by_name="ravi",
        )
    )


def receipt(db, vendor, day, receipt_id=None, items=3, snapshot=None):
    """One Toast receiving: `items` items listing `vendor`, received on `day`,
    seen in the next day's snapshot (as the daily CSV would carry it)."""
    _n[0] += 1
    rid = receipt_id or f"IN{_n[0]}"
    when = ny(day, 14).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    for i in range(items):
        db.add(
            InventorySnapshot(
                snapshot_date=snapshot or day + dt.timedelta(days=1),
                item_id=f"{rid}-{i}",
                name=f"item {rid} {i}",
                category_group="RETAIL",
                inventory_quantity=5,
                supplier=vendor,
                price=2.0,
                inventory_last_received=when,
                last_received_from=rid,
                source_filename="retail-items.csv",
                uploaded_at=ny(day + dt.timedelta(days=1), 2),
            )
        )


def supplier_row(db, supplier, item_id=None, day=TODAY):
    """An item in the latest snapshot listing `supplier` (";"-separated is fine)."""
    _n[0] += 1
    db.add(
        InventorySnapshot(
            snapshot_date=day,
            item_id=item_id or f"sup-{_n[0]}",
            name=f"sup item {_n[0]}",
            category_group="RETAIL",
            inventory_quantity=1,
            supplier=supplier,
            price=1.0,
            source_filename="retail-items.csv",
            uploaded_at=ny(day, 2),
        )
    )


def manual_log(db, vendor, day):
    db.add(PurchaseLogEntry(item_id="a", supplier=vendor, quantity_received=1, received_date=day))


def confirm(db, vendor, snapshot_day):
    db.add(
        PurchaseLogEntry(
            item_id="a", supplier=vendor, quantity_received=1, received_date=snapshot_day,
            notes=f"{ds.DELIVERY_REVIEW_NOTE_PREFIX} (qty ...)",
        )
    )


def placed(db, vendor, day, for_delivery=None, source=None, status="placed"):
    db.add(
        PlacedOrder(
            supplier=vendor, item_count=0 if source else 1, items_json="[]", placed_at=ny(day, 10),
            status=status, source=source, for_delivery_date=for_delivery,
        )
    )


def run(db, **kw):
    ds.clear_cache()
    return ss.compute_schedule_suggestions(db, today=TODAY, **kw)


def by_vendor(result):
    return {s["vendor"]: s for s in result["suggestions"]}


def without(result):
    return {r["vendor"]: r for r in result["vendors_without_schedule"]}


# --- Inference on synthetic histories ----------------------------------------------------------


def test_weekly_vendor_from_invoices_is_high_with_readable_evidence(db):
    for day in weekly(1, 8):  # Tuesdays
        invoice(db, "Raja Foods", day)
    db.commit()
    [s] = run(db)["suggestions"]
    assert s["vendor"] == "Raja Foods" and s["delivery_weekdays"] == [1] and s["confidence"] == "high"
    assert s["confidence_score"] == 100
    ev = s["evidence"]
    assert ev["summary"] == "Delivered Tue x8 in the last 12 weeks"
    assert (ev["covered"], ev["total"], ev["weeks_seen"]) == (8, 8, 8)
    assert ev["sources"] == {"invoices": 8, "toast_receipts": 0, "purchase_log": 0, "stock_rise": 0}
    assert ev["sources_text"] == "8 invoice uploads"
    assert ss._sources_text({"invoices": 1, "toast_receipts": 2, "purchase_log": 1, "stock_rise": 0}) == (
        "1 invoice upload, 2 Toast receivings, 1 logged delivery"
    )
    assert [d["weekday"] for d in ev["deliveries"]] == ["Tue"] * 8
    assert s["lead_days_source"] == "default" and s["schedule_order_weekdays"] == []


def test_twice_weekly_from_mixed_sources(db):
    for day in weekly(0, 6):  # Mondays: invoice uploads
        invoice(db, "Harbs Farm", day)
    for day in weekly(3, 5):  # Thursdays: Toast receivings + hand logs
        receipt(db, "Harbs Farm", day)
        manual_log(db, "Harbs Farm", day)
    db.commit()
    [s] = run(db)["suggestions"]
    assert s["delivery_weekdays"] == [0, 3] and s["weekday_names"] == ["Mon", "Thu"]
    assert s["confidence"] == "high"
    assert s["evidence"]["summary"] == "Delivered Mon x6, Thu x5 in the last 12 weeks"
    # A Thursday's receiving + hand log are ONE delivery, not two.
    assert s["evidence"]["total"] == 11
    assert s["evidence"]["sources"]["toast_receipts"] == 5 and s["evidence"]["sources"]["purchase_log"] == 5


def test_irregular_vendor_gets_no_suggestion_and_is_listed(db):
    for offset in (1, 3, 9, 12, 20, 26, 33, 41):
        invoice(db, "Restaurant Depot", TODAY - dt.timedelta(days=offset))
    supplier_row(db, "Restaurant Depot")
    db.commit()
    result = run(db)
    assert result["suggestions"] == []
    row = without(result)["Restaurant Depot"]
    assert row["why"] == "scattered" and row["deliveries_seen"] == 8
    assert row["reason"].startswith("Deliveries fall on scattered days (")


def test_holiday_gaps_and_a_moved_week_keep_the_weekday(db):
    # Wednesdays for 10 weeks, two weeks off (holidays), one week moved to Thursday.
    days = weekly(2, 10, skip=(2, 5, 7))
    for day in days:
        invoice(db, "Vdyas", day)
    invoice(db, "Vdyas", weekly(2, 10)[7] + dt.timedelta(days=1))
    db.commit()
    [s] = run(db)["suggestions"]
    assert s["delivery_weekdays"] == [2]
    assert (s["evidence"]["covered"], s["evidence"]["total"]) == (7, 8)
    assert s["confidence"] == "high" and s["evidence"]["weeks_seen"] == 7


def test_vendor_name_variants_are_one_vendor(db):
    supplier_row(db, "Raja Foods;RBest Produce Inc;Sri Sasha Imports, Inc")
    for day in weekly(0, 3):
        invoice(db, "RBest", day)  # short spelling on the invoice form
    for day in weekly(0, 6)[3:]:
        receipt(db, "RBest Produce Inc", day)
    manual_log(db, "rbest produce inc.", weekly(0, 7)[6])
    for day in weekly(4, 3):
        invoice(db, "Sri Sasha Imports Inc.", day)
    db.commit()
    result = run(db)
    got = by_vendor(result)
    assert set(got) == {"RBest Produce Inc", "Sri Sasha Imports, Inc"}  # Toast's own spelling shown
    assert got["RBest Produce Inc"]["evidence"]["total"] == 7 and got["RBest Produce Inc"]["delivery_weekdays"] == [0]
    assert got["Sri Sasha Imports, Inc"]["delivery_weekdays"] == [4]
    names = [s["vendor"] for s in result["suggestions"]] + [r["vendor"] for r in result["vendors_without_schedule"]]
    assert "RBest" not in names and len(names) == len(set(n.lower() for n in names))
    assert ss.vendor_key("Sri Sasha Imports, Inc") == ss.vendor_key("sri sasha imports INC.") == "sri sasha imports"
    assert ss.vendor_key("Singh & Singh") == "singh and singh"


def test_receivings_alone_are_moved_back_by_the_vendors_usual_lag(db):
    # Lately: invoice Thu, received in Toast Fri (3 samples of a 1-day lag).
    for day in weekly(3, 3):
        invoice(db, "Chetak", day)
        receipt(db, "Chetak", day + dt.timedelta(days=1))
    # Before invoice uploads began: only Friday receivings -> Thursday deliveries.
    for day in weekly(3, 8)[3:]:
        receipt(db, "Chetak", day + dt.timedelta(days=1))
    db.commit()
    [s] = run(db)["suggestions"]
    assert s["delivery_weekdays"] == [3] and s["evidence"]["receipt_lag_days"] == 1
    assert s["evidence"]["summary"] == "Delivered Thu x8 in the last 12 weeks"


def test_too_few_lag_samples_leave_receivings_where_they_are(db):
    invoice(db, "Aldis", weekly(3, 1)[0])
    receipt(db, "Aldis", weekly(3, 1)[0] + dt.timedelta(days=1))
    for day in weekly(4, 6)[2:]:
        receipt(db, "Aldis", day)
    db.commit()
    [s] = run(db)["suggestions"]
    assert s["evidence"]["receipt_lag_days"] == 0 and s["delivery_weekdays"] == [4]


def test_delivery_review_confirm_is_the_day_before(db):
    for day in weekly(1, 4):  # confirmed on the Tuesday snapshot -> Monday deliveries
        confirm(db, "Vijay Home Foods", day)
    db.commit()
    [s] = run(db)["suggestions"]
    assert s["delivery_weekdays"] == [0]


def test_stock_rises_on_api_only_days_count(db, monkeypatch):
    # Snapshot pairs whose newer date is API-only add "stock_rise" evidence (the day before).
    days = weekly(4, 4)  # Fridays
    for day in days:
        for d in (day, day + dt.timedelta(days=1)):
            db.add(InventorySnapshot(snapshot_date=d, item_id=f"api-{d}", name="x", inventory_quantity=1,
                                     supplier="Shata Traders", source_filename="api:2026", uploaded_at=ny(d, 4)))
    db.commit()
    calls = []

    def fake_groups(db_, pair, mode=None):
        calls.append((pair.prev_date, pair.date, mode))
        if pair.date.weekday() == 5:  # the Saturday snapshot after a Friday delivery
            return [{"vendor": "Shata Traders"}], {}
        return [], {}

    monkeypatch.setattr(ds, "pair_groups", fake_groups)
    ss._rise_cache.clear()
    [s] = run(db)["suggestions"]
    assert s["vendor"] == "Shata Traders" and s["delivery_weekdays"] == [4]
    assert s["evidence"]["sources"]["stock_rise"] == 4 and all(m == "rises" for _, _, m in calls)
    # Memoised per pair: a second read doesn't recompute.
    n = len(calls)
    run(db)
    assert len(calls) == n


def test_one_delivery_or_one_week_is_not_a_pattern(db):
    invoice(db, "Desi Nation", weekly(3, 1)[0])
    invoice(db, "Babco", weekly(0, 1)[0])
    invoice(db, "Babco", weekly(3, 1)[0] - dt.timedelta(days=7))  # Mon + Thu of different weeks
    db.commit()
    result = run(db)
    assert result["suggestions"] == []
    w = without(result)
    assert w["Desi Nation"]["why"] == "few" and w["Desi Nation"]["reason"].startswith("Only 1 delivery")
    assert w["Babco"]["why"] == "few"


def test_two_thin_days_are_low_confidence(db):
    for day in weekly(3, 2):
        invoice(db, "Best Foods", day)
    for day in weekly(5, 2):
        invoice(db, "Best Foods", day)
    db.commit()
    [s] = run(db)["suggestions"]
    assert s["delivery_weekdays"] == [3, 5] and s["confidence"] == "low"


def test_sorted_by_confidence(db):
    for day in weekly(1, 8):
        invoice(db, "High Co", day)
    for day in weekly(2, 3):
        invoice(db, "Mid Co", day)
    for day in weekly(4, 2):
        invoice(db, "Low Co", day)
    for day in weekly(0, 2) + weekly(5, 2):  # two thin days: low, though its score beats Mid Co's
        invoice(db, "Thin Co", day)
    db.commit()
    result = run(db)
    assert [(s["vendor"], s["confidence"]) for s in result["suggestions"]] == [
        ("High Co", "high"), ("Mid Co", "medium"), ("Thin Co", "low"), ("Low Co", "low")
    ]
    got = by_vendor(result)
    assert got["Thin Co"]["confidence_score"] > got["Mid Co"]["confidence_score"]


# --- Order day ----------------------------------------------------------------------------------


def test_lead_days_from_placed_and_outside_orders(db):
    for day in weekly(3, 6):
        invoice(db, "Jain Foods", day)
    for day in weekly(3, 6)[:3]:
        placed(db, "Jain Foods", day - dt.timedelta(days=2))  # Tue orders
    # An "Order already placed" tied to its delivery counts with that date.
    placed(db, "Jain Foods", weekly(3, 6)[4] - dt.timedelta(days=2), for_delivery=weekly(3, 6)[4], source="outside")
    placed(db, "Jain Foods", weekly(3, 6)[5] - dt.timedelta(days=6), status="pdf")  # a PDF isn't an order
    db.commit()
    [s] = run(db)["suggestions"]
    assert s["lead_days"] == 2 and s["lead_days_source"] == "placed_orders" and s["lead_samples"] == 4
    assert s["schedule_order_weekdays"] == [1] and s["order_weekdays"] == [1]


# --- Every vendor is listed once ----------------------------------------------------------------


def test_every_active_vendor_is_scheduled_suggested_or_without(db, admin_client):
    supplier_row(db, "Raja Foods;Chetak;Raja Foods")
    supplier_row(db, "KP Produce")
    supplier_row(db, "Jyena Enterprises;MainStore")
    supplier_row(db, "MainStore - Kitchen")
    supplier_row(db, "Old Vendor", day=TODAY - dt.timedelta(days=30))  # not in the latest snapshot
    for day in weekly(3, 5):
        invoice(db, "Raja Foods", day)
        invoice(db, "KP Produce", day)
        invoice(db, "Invoice Only Co", day)
    db.add(VendorSchedule(vendor="Chetak", delivery_weekdays="[4]", lead_days=1, active=False))
    db.commit()
    admin_client.post(f"{API}/dismiss", json={"vendor": "KP Produce", "delivery_weekdays": [3]})
    result = admin_client.get(API).json()
    suggested = {s["vendor"] for s in result["suggestions"]}
    rows = without(result)
    assert suggested == {"Raja Foods", "Invoice Only Co"}
    assert set(rows) == {"KP Produce", "Jyena Enterprises"}
    assert rows["KP Produce"]["why"] == "dismissed" and rows["KP Produce"]["dismissed_key"] == "kp produce|[3]"
    assert rows["Jyena Enterprises"]["why"] == "none" and rows["Jyena Enterprises"]["in_inventory"] is True
    assert by_vendor(result)["Invoice Only Co"]["in_inventory"] is False
    assert result["counts"] == {
        "active_vendors": 5, "scheduled": 1, "suggested": 2, "without_schedule": 2, "dismissed": 1,
    }
    # Undo the dismissal: it is suggested again.
    admin_client.delete(f"{API}/dismiss", params={"key": "kp produce|[3]"})
    assert "KP Produce" in {s["vendor"] for s in admin_client.get(API).json()["suggestions"]}


def test_existing_schedule_under_another_spelling_is_never_suggested(db):
    for day in weekly(3, 5):
        invoice(db, "Raja Foods", day)
    db.add(VendorSchedule(vendor="raja foods", delivery_weekdays="[3]", lead_days=1, active=True))
    db.commit()
    result = run(db)
    assert result["suggestions"] == [] and result["schedule_checks"] == []
    assert "Raja Foods" not in without(result)


# --- Add all ------------------------------------------------------------------------------------


def _three_vendors(db):
    for day in weekly(1, 6):
        invoice(db, "A Vendor", day)
    for day in weekly(3, 6):
        invoice(db, "B Vendor", day)
    for day in weekly(4, 6):
        invoice(db, "C Vendor", day)
    db.commit()


def test_add_all_creates_exactly_the_suggested_schedules(db, admin_client):
    _three_vendors(db)
    keys = [s["key"] for s in admin_client.get(API).json()["suggestions"]]
    r = admin_client.post(f"{API}/accept-all", json={"keys": keys})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created_count"] == 3 and body["skipped_count"] == 0
    rows = {s.vendor: (s.delivery_weekdays, s.lead_days, s.active) for s in db.query(VendorSchedule).all()}
    assert rows == {"A Vendor": ("[1]", 1, True), "B Vendor": ("[3]", 1, True), "C Vendor": ("[4]", 1, True)}
    after = admin_client.get(API).json()
    assert after["suggestions"] == [] and after["counts"]["scheduled"] == 3
    # Again: nothing is created twice.
    again = admin_client.post(f"{API}/accept-all", json={"keys": keys}).json()
    assert again["created_count"] == 0 and {s["why"] for s in again["skipped"]} == {"has_schedule"}
    assert db.query(VendorSchedule).count() == 3


def test_add_all_never_overwrites_an_admin_schedule(db, admin_client, monkeypatch):
    _three_vendors(db)
    listed = admin_client.get(API).json()["suggestions"]
    keys = [s["key"] for s in listed]
    # Meanwhile an admin sets B Vendor by hand (other spelling, paused, other day) - between the
    # suggestions being computed and the schedule being created.
    real = ss.compute_schedule_suggestions

    def compute_then_admin_edits(db_, **kw):
        out = real(db_, **kw)
        if not db_.query(VendorSchedule).filter(VendorSchedule.vendor == "b vendor").first():
            db_.add(VendorSchedule(vendor="b vendor", delivery_weekdays="[6]", lead_days=3, active=False))
            db_.commit()
        return out

    monkeypatch.setattr(ss, "compute_schedule_suggestions", compute_then_admin_edits)
    body = admin_client.post(f"{API}/accept-all", json={"keys": keys + ["nobody|[2]"]}).json()
    assert {c["vendor"] for c in body["created"]} == {"A Vendor", "C Vendor"}
    skipped = {s["vendor"]: s["why"] for s in body["skipped"]}
    assert skipped == {"B Vendor": "has_schedule", "nobody": "not_suggested"}
    # A key whose vendor got a schedule before the request was even computed says so too.
    late = admin_client.post(f"{API}/accept-all", json={"keys": ["b vendor|[3]"]}).json()
    assert late["skipped"] == [{"key": "b vendor|[3]", "vendor": "b vendor", "why": "has_schedule",
                                "reason": "b vendor already has a schedule - left as it is."}]
    b = db.query(VendorSchedule).filter(VendorSchedule.vendor == "b vendor").one()
    assert (b.delivery_weekdays, b.lead_days, b.active) == ("[6]", 3, False)
    assert db.query(VendorSchedule).count() == 3


def test_single_accept_refuses_another_spelling_of_a_scheduled_vendor(db, admin_client):
    db.add(VendorSchedule(vendor="Sri Sasha Imports, Inc", delivery_weekdays="[1]", lead_days=2, active=True))
    db.commit()
    r = admin_client.post("/api/vendor-calendar/schedules", json={"vendor": "sri sasha imports inc.", "delivery_weekdays": [3], "lead_days": 1})
    assert r.status_code == 400 and "Sri Sasha Imports, Inc already has a schedule" in r.json()["detail"]
    assert db.query(VendorSchedule).count() == 1


def test_add_all_validation(admin_client):
    assert admin_client.post(f"{API}/accept-all", json={"keys": []}).status_code == 422
    assert admin_client.post(f"{API}/accept-all", json={"keys": ["  "]}).status_code == 400
    assert admin_client.post(f"{API}/accept-all", json={"keys": ["x" * 301]}).status_code == 400
    assert admin_client.post(f"{API}/accept-all", json={"keys": ["k"] * (ss.MAX_ACCEPT_KEYS + 1)}).status_code == 422


# --- Who may accept (unchanged: the vendor_calendar feature) + demo ---------------------------------


def test_permissions_keep_the_existing_rule_and_demo_is_blocked(db):
    _three_vendors(db)
    planner = client_for(db, make_user(db, "planner", features=["vendor_calendar"]))
    outsider = client_for(db, make_user(db, "outsider", features=["supplier_projection"]))
    demo = client_for(db, make_user(db, "app_store_review", features=["vendor_calendar"]))
    keys = [s["key"] for s in planner.get(API).json()["suggestions"]]
    assert outsider.get(API).status_code == 403
    assert outsider.post(f"{API}/accept-all", json={"keys": keys}).status_code == 403
    # The demo login can look, never add.
    assert demo.get(API).status_code == 200
    r = demo.post(f"{API}/accept-all", json={"keys": keys})
    assert r.status_code == 403 and r.json() == {"detail": DEMO_READ_ONLY, "error": "demo_read_only", "message": DEMO_READ_ONLY}
    assert ("POST", f"{API}/accept-all") not in ALLOWLIST
    assert db.query(VendorSchedule).count() == 0
    # Anyone with Vendor Calendar may accept (same as the one-at-a-time Accept).
    r = planner.post(f"{API}/accept-all", json={"keys": keys[:1]})
    assert r.status_code == 200 and r.json()["created_count"] == 1


def test_old_fields_stay_for_released_apps(db):
    for day in weekly(1, 6):
        invoice(db, "A Vendor", day)
    db.commit()
    result = run(db)
    [s] = result["suggestions"]
    for field in ("key", "vendor", "delivery_weekdays", "weekday_names", "lead_days", "lead_days_source",
                  "order_weekdays", "evidence", "reason", "dismissed"):
        assert field in s
    for field in ("source", "source_label", "dates", "weekday_counts", "covered", "total", "coverage"):
        assert field in s["evidence"]
    assert isinstance(s["lead_days"], int)
    for field in ("lookback_days", "through", "count", "suggestions", "schedule_checks", "dismissed_count", "rules"):
        assert field in result
    assert result["lookback_days"] == ss.LOOKBACK_DAYS == 84


@pytest.mark.parametrize("name,key", [
    ("Kitchen Xpress Overseas INC.", "kitchen xpress overseas"),
    ("  Raja   Foods ", "raja foods"),
    ("Deen Distributors LLC", "deen distributors"),
    ("Co", "co"),
])
def test_vendor_key(name, key):
    assert ss.vendor_key(name) == key
