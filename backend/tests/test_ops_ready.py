"""GET /api/ready (app/ops/health.py), single mode. Multi mode:
tests/platform/test_ops_onboarding.py."""

from sqlalchemy import text

from app.db import engine
from app.migrate import store_head_revision


def _at_head():
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)"))
        conn.execute(text("DELETE FROM alembic_version"))
        conn.execute(text("INSERT INTO alembic_version (version_num) VALUES (:v)"), {"v": store_head_revision()})


def test_ready_when_the_database_answers_at_head(anon_client):
    _at_head()
    r = anon_client.get("/api/ready")
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ready", "checks": {"database": "ok", "migrations": "ok"}}
    assert r.headers["cache-control"] == "no-store"


def test_not_ready_when_migrations_are_behind(anon_client):
    _at_head()
    with engine.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num = '0001'"))
    try:
        r = anon_client.get("/api/ready")
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "not_ready"
        assert body["checks"]["migrations"].startswith("fail: at 0001")
    finally:
        _at_head()


def test_ready_command(capsys):
    from app.ops.__main__ import main

    _at_head()
    assert main(["ready"]) == 0
    assert '"ready"' in capsys.readouterr().out
