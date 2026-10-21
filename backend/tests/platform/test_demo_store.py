"""The DEMO store for App Review on the shared service (app/ops/demo_store.py,
pre-production 2026-10-21): provision once, reset nightly, view-only review
login, made-up data only, never touches another store. Real generator, dev
PostgreSQL; nothing leaves the machine."""

import datetime as dt
import pytest
from sqlalchemy import create_engine, pool, text

from tests.platform.conftest import provision as provision_store, store_login

END = dt.date(2026, 10, 20)


def _url(code="DEMO"):
    from app.platform import bridge
    from app.platform.db import platform_session
    from app.platform.models import Store

    with platform_session() as db:
        store = db.query(Store).filter(Store.code == code).one()
        return bridge.store_url(store.db_name, store.db_host_ref)


def _scalar(sql, code="DEMO"):
    eng = create_engine(_url(code), poolclass=pool.NullPool)
    try:
        with eng.connect() as conn:
            return conn.execute(text(sql)).scalar()
    finally:
        eng.dispose()


def test_provision_then_reset_gives_a_view_only_demo_with_fresh_made_up_data(tmp_path, pdb, client, monkeypatch):
    from app.ops import demo_store
    from app.platform.models import AuditLog, Store
    from app.platform.stores import settings_values

    monkeypatch.delenv("DEMO_REVIEW_PASSWORD", raising=False)
    pw_file = tmp_path / "secrets" / "demo_review_password"
    lines = []
    out = demo_store.provision(workdir=tmp_path / "work", password_file=pw_file, seed=7, days=7, items=20,
                               end_date=END, log=lines.append)
    assert out["created"] is True and out["review_username"] == "appreview"
    assert (pw_file.stat().st_mode & 0o777) == 0o600
    password = pw_file.read_text().strip()
    pdb.expire_all()
    store = pdb.query(Store).filter(Store.code == "DEMO").one()
    assert store.status == "active" and store.pos_provider == "csv" and store.pack_id == "demo"
    values = settings_values(pdb, store)
    assert values["demo_mode"] is True and values["pilot_mode"] is True
    first_net = _scalar("SELECT round(sum(net_price)::numeric, 2) FROM orders")
    assert first_net and _scalar("SELECT count(*) FROM users WHERE username = 'appreview'") == 1
    assert _scalar("SELECT count(*) FROM users WHERE username = 'demoowner'") == 0, "no usable owner login"
    assert not list((tmp_path / "work").iterdir()), "the build folder is cleaned up"

    # The review login signs in; it is a demo (view-only) login.
    r = store_login(client, "DEMO", "appreview", password)
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    auth = {"Authorization": f"Bearer {token}"}
    me = client.get("/api/auth/me", headers=auth)
    assert me.status_code == 200 and me.json()["demo"] is True
    # View only: a write is refused by the demo guard.
    from app.demo_guard import DEMO_READ_ONLY

    w = client.post("/api/reorder/critical-dismissals", json={}, headers=auth)
    assert w.status_code == 403 and w.json()["detail"] == DEMO_READ_ONLY, w.text

    # Nightly reset: new data, same review password, store active again.
    out2 = demo_store.reset(workdir=tmp_path / "work", password_file=pw_file, seed=8, days=7, items=20,
                            end_date=END, log=lines.append)
    assert out2["seed"] == 8 and out2["store_code"] == "DEMO"
    pdb.expire_all()
    assert pdb.query(Store).filter(Store.code == "DEMO").one().status == "active"
    assert _scalar("SELECT round(sum(net_price)::numeric, 2) FROM orders") != first_net
    assert store_login(client, "DEMO", "appreview", password).status_code == 200
    actions = [a for (a,) in pdb.query(AuditLog.action).filter(AuditLog.store_code == "DEMO")]
    assert "store.suspended" in actions and "store.resumed" in actions and "store.imported" in actions

    # Provision again: idempotent (no second store), just a reset.
    again = demo_store.provision(workdir=tmp_path / "work", password_file=pw_file, seed=9, days=7, items=20,
                                 end_date=END, log=lines.append)
    assert again["created"] is False


def test_reset_refuses_without_a_demo_store_and_a_store_not_in_demo_mode(tmp_path, pdb):
    from app.ops import demo_store
    from app.platform.stores import put_settings

    def never(*a, **k):
        raise AssertionError("must not build anything")

    with pytest.raises(demo_store.DemoStoreError, match="no DEMO store"):
        demo_store.reset(workdir=tmp_path, generate_fn=never)
    # A store called DEMO that is not a demo store (another POS / no demo mode): untouched.
    provision_store("DEMO", pack="test_toast")
    with pytest.raises(demo_store.DemoStoreError, match="not a demo store"):
        demo_store.reset(workdir=tmp_path, generate_fn=never)
    with pytest.raises(demo_store.DemoStoreError, match="already exists"):
        demo_store.provision(workdir=tmp_path, generate_fn=never)
    put_settings(pdb, "DEMO", {"demo_mode": False}, actor="t", actor_type="system")


def test_cli_commands_exist():
    from app.platform.__main__ import main

    with pytest.raises(SystemExit) as exc:
        main(["demo-reset", "--help"])
    assert exc.value.code == 0


def test_nightly_command_is_quiet_without_a_demo_store(capsys):
    from app.platform.__main__ import main

    assert main(["demo-reset", "--skip-if-missing"]) == 0
    assert "nothing to do" in capsys.readouterr().err
    assert main(["demo-reset"]) == 1
    assert "no DEMO store" in capsys.readouterr().err
