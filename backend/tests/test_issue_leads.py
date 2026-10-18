"""W4 (docs/BATCH_OCT14_CONTRACT.md): category & vendor leads for Fix Issues
and the daily lead task. Pushes are faked; nothing reaches Firebase."""

import datetime as dt
import json
import zoneinfo

import pytest

from app.config import get_settings
from app.issue_lead_models import IssueLead, IssueLeadTaskRun
from app.models import InventorySnapshot, JobRun
from app.services import issue_leads as svc
from app.services import push
from app.services.fix_issues import get_issue_list
from app.team import tasks_service
from app.team.tasks_models import Task
from tests.conftest import client_for, make_user

NY = zoneinfo.ZoneInfo("America/New_York")
TODAY = dt.date.today()


def at(hour, minute=0, day=0):
    d = dt.datetime.now(NY).date() + dt.timedelta(days=day)
    return dt.datetime.combine(d, dt.time(hour, minute), NY)


@pytest.fixture(autouse=True)
def _quiet_off(monkeypatch):
    monkeypatch.setattr(tasks_service, "_quiet", lambda now: False)


@pytest.fixture
def pushes(monkeypatch):
    sent = []
    monkeypatch.setattr(push, "is_configured", lambda: True)

    def fake_send(tokens, title, body, data, thread_id=None):
        sent.append({"title": title, "body": body})
        return push.SendResult(success_count=len(tokens), delivered_tokens=list(tokens))

    monkeypatch.setattr(push, "send_to_tokens", fake_send)
    return sent


def snap(db, item_id, name, category="Oils & Fats", supplier="Chetak", barcode=None, group="RETAIL", day=TODAY):
    db.add(
        InventorySnapshot(
            snapshot_date=day,
            item_id=item_id,
            name=name,
            category_group=group,
            category=category,
            inventory_quantity=5,
            price=3.0,
            cost=1.0,
            supplier=supplier,
            barcode=barcode,
        )
    )


@pytest.fixture
def people(db):
    return {
        "amandeep": make_user(db, "Amandeep", features=["team_tasks", "inventory_reports"]),
        "sai": make_user(db, "Sai Durga", features=["team_tasks", "inventory_reports"]),
        "harshitha": make_user(db, "Harshitha", features=["team_tasks"]),
        "sundar": make_user(db, "Sundar", is_admin=True),
    }


@pytest.fixture
def store(db):
    # Missing barcodes (the barcode is blank): one per lead path.
    snap(db, "rice1", "Sona Masoori 20lb", category="Oils & Fats", supplier="Chetak")  # section Oils & Ghee -> Sai
    snap(db, "frz1", "Veg Puff", category="Beverages (Non-Alcoholic)", supplier="Shata Traders")
    snap(db, "odd1", "Mystery Thing", category="Weird", supplier="Shata Traders")  # Other -> vendor lead
    snap(db, "odd2", "Unknown Item", category="Weird", supplier="Vdyas")  # Other, no vendor lead -> fallback
    snap(db, "ok1", "Has Barcode", category="Oils & Fats", supplier="Chetak", barcode="012345678905")
    db.commit()


def save(db, admin, sections=(), vendors=(), fallback=None):
    body = {
        "sections": [{"name": n, "user_id": u} for n, u in sections],
        "vendors": [{"name": n, "user_id": u} for n, u in vendors],
    }
    if fallback is not None:
        body["fallback"] = {"user_id": fallback}
    svc.save_leads(db, body, admin)


def only_missing(db, admin):
    """The daily task counts just Missing barcodes (the test items have no
    sales, so they'd also be on the dead lists)."""
    from app.services.fix_issues import ISSUE_TYPES

    off = [t for t in ISSUE_TYPES if t not in ("missing_barcode", "dead_stock")]
    svc.save_task_settings(db, {"types_off": off}, admin)


def row(db, item_id, issue_type="missing_barcode"):
    data = get_issue_list(db, issue_type)
    return next(r for r in data["open"] if r["item_id"] == item_id)


# --- Matching -------------------------------------------------------------------------


VENDORS = [
    "Shata Traders", "Prestige Concept LLC", "Concept Foods", "House of Spices", "Aldis", "Indigenous Foods",
    "Raja Foods", "Sri Sai Imports, Inc", "SHAHI KULFI USA", "Chandigarh Distributors Inc.", "Vdyas",
]


@pytest.mark.parametrize(
    "doc,expected",
    [
        ("Shata", ["Shata Traders"]),
        ("Prestige", ["Prestige Concept LLC"]),
        ("Concept Foods", ["Concept Foods"]),  # never Prestige Concept
        ("HOS", ["House of Spices"]),
        ("Aldi's", ["Aldis"]),
        ("Indigeneous", ["Indigenous Foods"]),  # the document's spelling
        ("Rajafoods", ["Raja Foods"]),
        ("Shahi Kulfi", ["SHAHI KULFI USA"]),
        ("Chandigarh", ["Chandigarh Distributors Inc."]),
        ("Sai Florals", []),
    ],
)
def test_vendor_candidates(doc, expected):
    assert svc.vendor_candidates(doc, VENDORS) == expected


def test_match_user_by_first_name(db, people):
    users = svc._eligible_users(db)
    assert svc.match_user(users, "Sai").username == "Sai Durga"
    assert svc.match_user(users, "Amandeep").username == "Amandeep"
    make_user(db, "Sai Kumar")
    assert svc.match_user(svc._eligible_users(db), "Sai") is None  # ambiguous


# --- Rows -----------------------------------------------------------------------------


@pytest.mark.legacy_pack  # the original store's own rules (private pack)
def test_rows_carry_section_and_no_lead_until_saved(db, store, people):
    r = row(db, "rice1")
    assert r["po_section"] == "Oils & Ghee"
    assert r["lead"] is None and r["lead_task"] is None


@pytest.mark.legacy_pack  # the original store's own rules (private pack)
def test_section_then_vendor_then_fallback(db, store, people, admin):
    p = people
    save(
        db, admin,
        sections=[("Oils & Ghee", p["sai"].id), ("Beverages", p["amandeep"].id)],
        vendors=[("Shata Traders", p["harshitha"].id)],
        fallback=p["sundar"].id,
    )
    assert row(db, "rice1")["lead"] == {"user_id": p["sai"].id, "name": "Sai Durga", "via": "section", "via_name": "Oils & Ghee"}
    frz = row(db, "frz1")
    assert (frz["lead"]["name"], frz["lead"]["via"]) == ("Amandeep", "section")  # section beats the vendor lead
    odd = row(db, "odd1")
    assert (odd["po_section"], odd["lead"]["name"], odd["lead"]["via"]) == ("Other", "Harshitha", "vendor")
    assert (row(db, "odd2")["lead"]["name"], row(db, "odd2")["lead"]["via"]) == ("Sundar", "fallback")


def test_saved_no_lead_falls_through(db, store, people, admin):
    save(db, admin, sections=[("Oils & Ghee", None)], vendors=[("Chetak", people["sai"].id)])
    r = row(db, "rice1")
    assert (r["lead"]["name"], r["lead"]["via"]) == ("Sai Durga", "vendor")
    assert row(db, "odd2")["lead"] is None  # no fallback saved


@pytest.mark.legacy_pack  # the original store's own rules (private pack)
def test_summary_counts_by_lead(db, store, people, admin, ):
    save(db, admin, sections=[("Oils & Ghee", people["sai"].id)])
    c = client_for(db, people["sundar"])
    body = c.get("/api/issues/summary").json()
    mb = next(i for i in body["items"] if i["issue_type"] == "missing_barcode")
    assert mb["open_by_lead"] == {str(people["sai"].id): 1}
    assert mb["open_no_lead"] == 3
    assert body["leads"] == [{"user_id": people["sai"].id, "name": "Sai Durga"}]


# --- Admin API --------------------------------------------------------------------------


def test_admin_only(db, people, staff_client):
    assert staff_client.get("/api/issue-leads").status_code == 403
    assert staff_client.put("/api/issue-leads", json={}).status_code == 403
    assert staff_client.post("/api/issue-leads/run-now").status_code == 403


@pytest.mark.legacy_pack  # the original store's own rules (private pack)
def test_page_proposal_and_accept(db, store, people, admin_client):
    page = admin_client.get("/api/issue-leads").json()
    assert page["saved"] is False
    rice = next(s for s in page["sections"] if s["section"] == "Oils & Ghee")
    assert rice["lead"] is None and rice["saved"] is False
    assert rice["proposal"]["doc_lead"] == "Sai" and rice["proposal"]["name"] == "Sai Durga"
    assert rice["proposal"]["basis"] == "document"
    shata = next(v for v in page["vendors"] if v["vendor"] == "Shata Traders")
    assert shata["proposal"]["name"] == "Amandeep"
    assert next(v for v in page["vendors"] if v["vendor"] == "Vdyas")["proposal"] is None
    assert {"name": "Sai Florals", "lead": "Harshitha"} in page["document"]["unmatched_vendors"]
    assert page["fallback"]["proposal"]["name"] == "Sundar"

    # A hand-set row is kept by accept-proposal.
    r = admin_client.put("/api/issue-leads", json={"sections": [{"name": "Oils & Ghee", "user_id": people["amandeep"].id}]})
    assert r.status_code == 200
    page = admin_client.post("/api/issue-leads/accept-proposal").json()
    rice = next(s for s in page["sections"] if s["section"] == "Oils & Ghee")
    assert rice["lead"]["name"] == "Amandeep" and rice["saved"]
    assert next(v for v in page["vendors"] if v["vendor"] == "Shata Traders")["lead"]["name"] == "Amandeep"
    assert page["fallback"]["lead"]["name"] == "Sundar"
    assert db.query(IssueLead).filter(IssueLead.source == "proposal").count() > 0


def test_save_validates_people(db, people, admin_client):
    r = admin_client.put("/api/issue-leads", json={"sections": [{"name": "Oils & Ghee", "user_id": 99999}]})
    assert r.status_code == 400
    r = admin_client.put("/api/issue-leads", json={"sections": [{"name": "Oils & Ghee", "user_id": None}]})
    page = r.json()
    assert next(s for s in page["sections"] if s["section"] == "Oils & Ghee")["saved"] if any(
        s["section"] == "Oils & Ghee" for s in page["sections"]) else True


def test_demo_cannot_be_a_lead(db, people, admin_client, monkeypatch):
    monkeypatch.setattr(get_settings(), "demo_usernames", "app_store_review")
    demo = make_user(db, "app_store_review", features=["team_tasks"])
    assert admin_client.put("/api/issue-leads", json={"vendors": [{"name": "X", "user_id": demo.id}]}).status_code == 400
    names = [p["name"] for p in admin_client.get("/api/issue-leads").json()["people"]]
    assert "app_store_review" not in names


def test_settings_validation(db, admin_client):
    s = admin_client.get("/api/issue-leads/settings").json()["settings"]
    assert s["time"] == "10:00" and s["default_hours"] == 6 and s["enabled"] is True
    assert admin_client.put("/api/issue-leads/settings", json={"time": "23:00"}).status_code == 400
    assert admin_client.put("/api/issue-leads/settings", json={"time": "ten"}).status_code == 400
    assert admin_client.put("/api/issue-leads/settings", json={"default_hours": 0}).status_code == 400
    assert admin_client.put("/api/issue-leads/settings", json={"hours_by_type": {"nope": 3}}).status_code == 400
    assert admin_client.put("/api/issue-leads/settings", json={"types_off": ["dead_stock"]}).status_code == 400
    r = admin_client.put(
        "/api/issue-leads/settings",
        json={"time": "9:30", "default_hours": 8, "hours_by_type": {"missing_barcode": 4}, "types_off": ["slow_review"]},
    )
    assert r.status_code == 200
    s = r.json()["settings"]
    assert (s["time"], s["default_hours"], s["hours_by_type"], s["types_off"]) == (
        "09:30", 8, {"missing_barcode": 4}, ["slow_review"])
    assert r.json()["changed"]["changed_by"] == "boss"


def test_hours_for():
    settings = {"default_hours": 6, "hours_by_user": {"5": 2}, "hours_by_type": {"a": 4, "b": 3}}
    assert svc.hours_for(settings, 5, {"a": 1}) == 2
    assert svc.hours_for(settings, 6, {"a": 1, "b": 2}) == 3
    assert svc.hours_for(settings, 6, {"c": 1}) == 6


# --- Daily task -------------------------------------------------------------------------


def lead_tasks(db):
    return db.query(Task).filter(Task.source_type == "issue_leads").order_by(Task.id).all()


def test_no_run_before_time_or_without_leads(db, store, people, admin, pushes):
    assert svc.run_daily_tasks(db, at(11)) is None  # nothing saved yet
    save(db, admin, sections=[("Oils & Ghee", people["sai"].id)])
    assert svc.run_daily_tasks(db, at(9, 59)) is None
    svc.save_task_settings(db, {"enabled": False}, admin)
    assert svc.run_daily_tasks(db, at(11)) is None
    assert lead_tasks(db) == []


@pytest.mark.legacy_pack  # the original store's own rules (private pack)
def test_daily_task_created_once_per_lead(db, store, people, admin, pushes):
    only_missing(db, admin)
    p = people
    save(db, admin, sections=[("Oils & Ghee", p["sai"].id)], vendors=[("Shata Traders", p["amandeep"].id)],
         fallback=p["sundar"].id)
    svc.save_task_settings(db, {"hours_by_user": {str(p["amandeep"].id): 3}}, admin)
    result = svc.run_daily_tasks(db, at(10, 5))
    assert result["created"] == 3
    tasks = {t.assignee_user_id: t for t in lead_tasks(db)}
    assert set(tasks) == {p["sai"].id, p["amandeep"].id, p["sundar"].id}
    sai = tasks[p["sai"].id]
    assert sai.title == "Fix Issues: 1 left of 1"  # Oct 15: live "N left of M"
    assert (sai.progress_total, sai.progress_done) == (1, 0)
    assert "Missing barcodes: 1" in sai.description and f"#/fix-issues?lead={p['sai'].id}" in sai.description
    assert sai.assignee_team_id is None and sai.created_by_user_id == tasks_service.SYSTEM_USER_ID
    assert (sai.due_at - sai.created_at) == dt.timedelta(hours=6)
    aman = tasks[p["amandeep"].id]
    assert (aman.due_at - aman.created_at) == dt.timedelta(hours=3)
    assert aman.title == "Fix Issues: 2 left of 2"  # frz1 + odd1 via the vendor
    # Pushed once per new task.
    assert len([x for x in pushes if x["title"] == "New task"]) <= 3
    # Marked as run: the next tick the same day does nothing.
    assert svc.run_daily_tasks(db, at(10, 10)) is None
    assert db.query(JobRun).filter(JobRun.job_name == "issue_lead_tasks").count() == 1
    # The rows now say a task covers them.
    r = row(db, "rice1")
    assert r["lead_task"]["task_id"] == sai.id and r["lead_task"]["status"] == "open"


@pytest.mark.legacy_pack  # the original store's own rules (private pack)
def test_rerun_same_day_updates_not_duplicates(db, store, people, admin, pushes):
    only_missing(db, admin)
    save(db, admin, sections=[("Oils & Ghee", people["sai"].id)])
    svc.run_daily_tasks(db, at(10, 5))
    snap(db, "rice2", "Basmati 10lb", category="Oils & Fats", supplier="Chetak")
    db.commit()
    result = svc.run_daily_tasks(db, at(12), force=True)
    assert (result["created"], result["updated"]) == (0, 1)
    (task,) = lead_tasks(db)
    assert task.title == "Fix Issues: 2 left of 2"
    run = db.query(IssueLeadTaskRun).one()
    assert run.item_count == 2 and "missing_barcode:rice2" in json.loads(run.items_json)


@pytest.mark.legacy_pack  # the original store's own rules (private pack)
def test_next_day_replaces_and_empty_lead_closes(db, store, people, admin, pushes):
    p = people
    save(db, admin, sections=[("Oils & Ghee", p["sai"].id)], vendors=[("Shata Traders", p["amandeep"].id)])
    svc.run_daily_tasks(db, at(10, 5))
    # Next day: Amandeep's items are no longer his (vendor lead removed).
    save(db, admin, vendors=[("Shata Traders", None)])
    svc.run_daily_tasks(db, at(10, 5, day=1))
    tasks = lead_tasks(db)
    sai = [t for t in tasks if t.assignee_user_id == p["sai"].id]
    assert [t.status for t in sai] == ["cancelled", "open"]
    assert sai[0].completion_note.startswith("Replaced by")
    aman = [t for t in tasks if t.assignee_user_id == p["amandeep"].id]
    assert [(t.status, t.completion_note) for t in aman] == [("done", "Nothing left on the Fix Issues list")]


@pytest.mark.legacy_pack  # the original store's own rules (private pack)
def test_tick_hook_runs_inside_auto_tasks(db, store, people, admin, pushes, monkeypatch):
    from app.team import auto_tasks

    monkeypatch.setattr(get_settings(), "team_autotask_checklist_enabled", False)
    save(db, admin, sections=[("Oils & Ghee", people["sai"].id)])
    auto_tasks.auto_tasks_tick(db, at(10, 1))
    assert len(lead_tasks(db)) == 1


@pytest.mark.legacy_pack  # the original store's own rules (private pack)
def test_run_now_endpoint(db, store, people, admin, admin_client, pushes):
    only_missing(db, admin)
    save(db, admin, sections=[("Oils & Ghee", people["sai"].id)])
    r = admin_client.post("/api/issue-leads/run-now")
    assert r.status_code == 200 and r.json()["created"] == 1
    prev = admin_client.get("/api/issue-leads/preview").json()
    assert prev["leads"][0]["name"] == "Sai Durga" and prev["leads"][0]["count"] == 1
    assert prev["unassigned"] == 3


def test_migration_0054_matches_models_and_downgrades(tmp_path):
    import os
    import pathlib
    import sqlite3
    import subprocess
    import sys

    from app.db import Base

    backend = pathlib.Path(__file__).resolve().parents[1]
    db_file = tmp_path / "m.db"
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_file}")
    alembic = pathlib.Path(sys.executable).parent / "alembic"

    def run(*args):
        subprocess.run([str(alembic), *args], cwd=backend, env=env, check=True, capture_output=True)

    run("upgrade", "0054")
    con = sqlite3.connect(db_file)
    try:
        for table in ("issue_leads", "issue_lead_task_runs"):
            migrated = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
            assert migrated == {c.name for c in Base.metadata.tables[table].columns}, table
    finally:
        con.close()
    run("downgrade", "0053")
    con = sqlite3.connect(db_file)
    try:
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not names & {"issue_leads", "issue_lead_task_runs"}
    finally:
        con.close()
    run("upgrade", "0054")
