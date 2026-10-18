"""App Review build 7 - gaps found in verification (docs/APP_REVIEW_BUILD7_CONTRACT.md):

- the weekly digest called Anthropic for a demo login;
- recurring-task schedules (templates) skipped the content filter, so every
  generated occurrence carried the unmasked text;
- the last-admin guard on account deletion ran before any write, so two
  deletions racing each other could leave no admin;
- migration 0043's downgrade rebuilt `messages` and lost its AUTOINCREMENT.
"""

import os
import pathlib
import sqlite3
import subprocess
import sys
import types

import pytest

from app.auth import hash_password
from app.models import User, UserSession
from app.routers import auth as auth_router
from app.services import weekly_digest
from app.team.tasks_models import TaskTemplate
from tests.conftest import client_for, make_user

BACKEND = pathlib.Path(__file__).resolve().parents[1]


# --- Weekly digest ----------------------------------------------------------------------


@pytest.fixture
def quiet_reports(monkeypatch):
    for name in ("compute_reorder_candidates", "get_dead_stock", "get_vendor_price_comparison",
                 "get_reconciliation", "get_missing_barcodes", "get_invalid_barcodes"):
        monkeypatch.setattr(weekly_digest, name, lambda *a, **k: [])


def test_demo_weekly_digest_makes_no_anthropic_call(db, monkeypatch, quiet_reports):
    def boom():
        raise AssertionError("Anthropic must not be called for the demo account")

    monkeypatch.setattr(weekly_digest, "get_anthropic_client", boom)
    demo = client_for(db, make_user(db, "app_store_review", features=["overview"]))
    r = demo.post("/api/weekly-digest/generate")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["demo"] is True
    assert body["narrative"]["headline"] == weekly_digest.DEMO_DIGEST_HEADLINE
    assert set(body["narrative"]) == set(weekly_digest.WeeklyDigestNarrative.model_fields)
    assert "week_start" in body and "data" in body


def test_real_weekly_digest_still_asks_claude(db, monkeypatch, quiet_reports):
    calls = []

    def parse(**kw):
        calls.append(kw)
        text = "x"
        return types.SimpleNamespace(parsed_output=weekly_digest.WeeklyDigestNarrative(
            headline="real", sales=text, reorder=text, money_at_risk=text,
            vendor_savings=text, reconciliation=text, housekeeping=text,
        ))

    fake = types.SimpleNamespace(messages=types.SimpleNamespace(parse=parse))
    monkeypatch.setattr(weekly_digest, "get_anthropic_client", lambda: fake)
    real = client_for(db, make_user(db, "asha", features=["overview"]))
    body = real.post("/api/weekly-digest/generate").json()
    assert body["narrative"]["headline"] == "real" and len(calls) == 1
    assert "demo" not in body


# --- Content filter on schedules ----------------------------------------------------------


def test_schedule_text_is_masked(db, admin_client, staff):
    r = admin_client.post("/api/team/templates", json={
        "title": "Clean the shit off the shelf", "description": "no bullshit",
        "rrule": "FREQ=DAILY;BYHOUR=9;BYMINUTE=0", "timezone": "America/New_York",
        "assignee_user_id": staff.id,
    })
    assert r.status_code in (200, 201), r.text
    tpl = db.query(TaskTemplate).one()
    assert tpl.title == "Clean the s*** off the shelf"
    assert tpl.description == "no b*******"
    r = admin_client.patch(f"/api/team/templates/{tpl.id}", json={"title": "Fix the d@mn sh1t"})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(TaskTemplate, tpl.id).title == "Fix the d@mn s***"


# --- Last-admin guard race ----------------------------------------------------------------


def test_last_admin_recheck_inside_the_delete(db, monkeypatch):
    """The up-front check saw two admins (the other one was deleted in the
    meantime by a racing request): the count inside the write refuses it
    and the account is left fully intact."""
    boss = make_user(db, "boss", is_admin=True)
    boss.password_hash = hash_password("pw")
    db.commit()
    c = client_for(db, boss)
    real_count = auth_router._admin_count
    seen = []

    def stale_then_real(session):
        seen.append(1)
        return 2 if len(seen) == 1 else real_count(session)

    monkeypatch.setattr(auth_router, "_admin_count", stale_then_real)
    r = c.post("/api/auth/delete-account", json={"password": "pw"})
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "You are the last admin - make someone else an admin first."
    db.expire_all()
    assert db.get(User, boss.id) is not None
    assert db.query(UserSession).filter(UserSession.user_id == boss.id).count() == 1
    assert c.get("/api/auth/me").status_code == 200


def test_admin_deleting_the_other_admin_rechecks_too(db, monkeypatch):
    a = make_user(db, "boss", is_admin=True)
    b = make_user(db, "boss2", is_admin=True)
    ca = client_for(db, a)
    # b has (in a racing request) just deleted a's admin flag... simulated by
    # demoting a after a's request passed its own check.
    real_count = auth_router._admin_count
    seen = []

    def racing(session):
        seen.append(1)
        if len(seen) == 1:
            n = real_count(session)
            session.execute(User.__table__.update().where(User.id == a.id).values(is_admin=False))
            return n
        return real_count(session)

    monkeypatch.setattr(auth_router, "_admin_count", racing)
    r = ca.delete(f"/api/auth/users/{b.id}")
    assert r.status_code == 400 and r.json()["detail"] == "Can't delete the last remaining admin"
    db.expire_all()
    assert db.get(User, b.id) is not None


def test_ordinary_account_deletion_unaffected(db, admin, staff):
    staff.password_hash = hash_password("pw")
    db.commit()
    r = client_for(db, staff).post("/api/auth/delete-account", json={"password": "pw"})
    assert r.status_code == 200 and r.json() == {"ok": True}


# --- Migration 0043 downgrade keeps messages' AUTOINCREMENT ------------------------------------


def test_migration_0043_downgrade_keeps_message_autoincrement(tmp_path):
    db_file = tmp_path / "m.db"
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_file}")
    alembic = pathlib.Path(sys.executable).parent / "alembic"
    run = lambda *a: subprocess.run([str(alembic), *a], cwd=BACKEND, env=env, check=True, capture_output=True)  # noqa: E731
    run("upgrade", "head")
    con = sqlite3.connect(db_file)
    con.execute("INSERT INTO channels (id, kind, team_id, created_at) VALUES (1, 'dm', NULL, '2026-01-01')")
    con.execute(
        "INSERT INTO messages (id, channel_id, sender_user_id, sender_name, body, client_uuid, created_at) "
        "VALUES (7, 1, 2, 'ravi', 'hi', 'u1', '2026-01-01')"
    )
    con.commit()
    con.close()
    run("downgrade", "0042")
    con = sqlite3.connect(db_file)
    try:
        sql = con.execute("SELECT sql FROM sqlite_master WHERE name = 'messages'").fetchone()[0]
        assert "AUTOINCREMENT" in sql
        assert "filtered" not in {r[1] for r in con.execute("PRAGMA table_info(messages)")}
        assert con.execute("SELECT seq FROM sqlite_sequence WHERE name = 'messages'").fetchone() == (7,)
        assert con.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    finally:
        con.close()
    run("upgrade", "head")
