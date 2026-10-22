"""Fix Issues Closed report: Employee (who closed it) and Category filters,
and the counts agree (owner, Oct 22: "The closed report count for each
employee is correct but if I look at last 30 day closed report the count
does not match").

The Closed report tile (summary `closed_report`) is the report's own default
- last 30 days, everyone - worked out by the same function, and per-person
counts add up to the total and equal the report filtered to that person."""

import datetime as dt

from app.models import IssueResolution
from app.services.fix_issues import CLOSED_REPORT_DEFAULT_DAYS, closed_report_tile, get_closed_report
from app.timeutil import restaurant_today

ET = dt.timezone(dt.timedelta(hours=-4))


def _close(db, key, by, category, days_ago, issue_type="missing_barcode"):
    day = restaurant_today() - dt.timedelta(days=days_ago)
    db.add(
        IssueResolution(
            issue_type=issue_type,
            issue_key=key,
            item_id=key,
            item_name=key,
            category=category,
            reason="fixed",
            closed_by_name=by,
            closed_at=dt.datetime(day.year, day.month, day.day, 13, tzinfo=ET).astimezone(dt.timezone.utc),
        )
    )


def _seed(db):
    _close(db, "a", "ravi", "Snacks", 1)
    _close(db, "b", "ravi", "Snacks", 3)
    _close(db, "c", "ravi", "Rice", 10, issue_type="invalid_barcode")
    _close(db, "d", "anu", "Rice", 2)
    _close(db, "e", "anu", None, 20)
    _close(db, "f", None, "Snacks", 5)
    _close(db, "g", "ravi", "Snacks", 45)  # outside the last 30 days
    _close(db, "h", "Ravi ", "snacks", 4)  # same person / category, other spelling
    db.commit()


def test_tile_equals_the_reports_default(db, admin_client):
    _seed(db)
    tile = admin_client.get("/api/issues/summary").json()["closed_report"]
    report = admin_client.get("/api/issues/report").json()
    assert tile["days"] == CLOSED_REPORT_DEFAULT_DAYS == 30
    assert tile["total"] == report["total"] == report["all_total"] == 7
    assert (tile["start"], tile["end"]) == (report["start"], report["end"])
    assert tile["showing"] == report["showing"] == "Everyone · All categories"
    assert tile["by_person"] == report["by_person"] == {"ravi": 4, "anu": 2, "Unknown": 1}
    assert closed_report_tile(db)["total"] == 7


def test_per_person_counts_add_up_and_match_the_filtered_report(db, admin_client):
    _seed(db)
    report = admin_client.get("/api/issues/report").json()
    assert sum(report["by_person"].values()) == report["total"]
    for person in report["people"]:
        filtered = admin_client.get("/api/issues/report", params={"closed_by": person["name"]}).json()
        assert filtered["total"] == person["count"] == report["by_person"][person["name"]]
        assert filtered["all_total"] == report["total"]
        assert filtered["filters"]["closed_by"] == person["name"]
        assert filtered["showing"] == f"Closed by {person['name']} · All categories"
        assert sum(d["total"] for d in filtered["days"]) == filtered["total"]
        assert all(
            (it["closed_by"] or "Unknown").strip().lower() == person["name"].lower()
            for d in filtered["days"]
            for it in d["items"]
        )


def test_category_filter_and_both_together(db, admin_client):
    _seed(db)
    snacks = admin_client.get("/api/issues/report", params={"category": "snacks"}).json()  # any case
    assert snacks["total"] == 4 and snacks["showing"] == "Everyone · snacks"
    assert snacks["by_person"] == {"ravi": 3, "Unknown": 1}
    # Options say what each choice would show.
    assert {c["name"]: c["count"] for c in snacks["categories"]} == {"No category": 1, "Rice": 2, "Snacks": 4}
    assert {p["name"]: p["count"] for p in snacks["people"]} == {"ravi": 3, "Unknown": 1}
    both = admin_client.get("/api/issues/report", params={"category": "Rice", "closed_by": "Ravi"}).json()
    assert both["total"] == 1 and both["showing"] == "Closed by Ravi · Rice"
    none = admin_client.get("/api/issues/report", params={"category": "No category"}).json()
    assert none["total"] == 1 and none["by_person"] == {"anu": 1}


def test_same_numbers_for_any_range(db):
    _seed(db)
    end = restaurant_today()
    for days in (7, 30, 90):
        start = end - dt.timedelta(days=days - 1)
        full = get_closed_report(db, start, end)
        assert sum(full["by_person"].values()) == full["total"] == full["all_total"]
        for name, count in full["by_person"].items():
            assert get_closed_report(db, start, end, closed_by=name)["total"] == count
        for cat in full["categories"]:
            assert get_closed_report(db, start, end, category=cat["name"])["total"] == cat["count"]
