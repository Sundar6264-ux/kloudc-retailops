"""Security + ops verifier (final pass): attacks on the production hardening,
multi mode on PostgreSQL.

- pilot mode blocks every send of the pilot store - in a request, in its
  background task, in a per-store job, in a spawned thread - while another
  store sends and the pilot store's POS sync still writes; a send with no
  store selected is refused (fail closed);
- a corrupted copy fails the import and leaves the store's database exactly
  as it was (staging database, swapped in only after verification);
- backups count rows in the dump's own snapshot, so the drill is exact even
  while the store keeps writing, and a dump that restores short fails;
- readiness never reports a check that could not run as passed;
- parity with an empty reference is not "clean".
"""

import datetime as dt
import json
import os
import pathlib
import shutil
import sqlite3
import sys

import httpx
import pytest
from sqlalchemy import create_engine, pool, text

from tests.platform.conftest import provision, store_login

BACKEND = pathlib.Path(__file__).resolve().parents[2]


def _store_url(code):
    from app.platform import bridge
    from app.platform.db import platform_session
    from app.platform.models import Store

    with platform_session() as db:
        store = db.query(Store).filter(Store.code == code).one()
        return bridge.store_url(store.db_name, store.db_host_ref)


def _scalar(url, sql, **params):
    eng = create_engine(url, poolclass=pool.NullPool)
    try:
        with eng.connect() as conn:
            return conn.execute(text(sql), params).scalar()
    finally:
        eng.dispose()


def _exec(url, sql, **params):
    eng = create_engine(url, poolclass=pool.NullPool)
    try:
        with eng.begin() as conn:
            conn.execute(text(sql), params)
    finally:
        eng.dispose()


def _pg_bin():
    for candidate in (os.environ.get("PG_BIN"), "/opt/homebrew/opt/postgresql@16/bin", "/usr/lib/postgresql/16/bin"):
        if candidate and (pathlib.Path(candidate) / "pg_dump").exists():
            return candidate
    found = shutil.which("pg_dump")
    return str(pathlib.Path(found).parent) if found else None


# --- pilot mode: every send path of the pilot store ------------------------------------


@pytest.fixture
def sink(monkeypatch, platform_env):
    """Every outbound send is recorded here instead of leaving the machine;
    the transports are configured (a token, an SMTP login, Firebase) so a
    store NOT in pilot mode really sends."""
    from app.ops import pilot
    from app.services import po_send, push, toast_email, whapi_client

    calls = []

    def fake_post(url, **kwargs):
        calls.append(("whatsapp", url))
        return httpx.Response(200, json={"media": [{"id": "m1"}], "sent": True, "message": {"id": "w1"}},
                              request=httpx.Request("POST", url))

    class FakeSMTP:
        def __init__(self, *a, **k):
            pass

        def login(self, *a):
            pass

        def ehlo(self, *a):
            pass

        def starttls(self, *a, **k):
            pass

        def sendmail(self, sender, to, msg):
            calls.append(("email", tuple(to)))
            return {}

        def send_message(self, *a, **k):
            calls.append(("email", "send_message"))
            return {}

        def quit(self):
            pass

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(whapi_client.httpx, "post", fake_post)
    for mod in (toast_email, po_send):
        monkeypatch.setattr(mod.smtplib, "SMTP_SSL", FakeSMTP)
        monkeypatch.setattr(mod.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(push, "_get_app", lambda: object())
    from firebase_admin import messaging

    def fake_send_each(messages, app=None):
        calls.append(("push", len(messages)))

        class _R:
            success = True
            exception = None
            message_id = "p1"

        class _Batch:
            responses = [_R() for _ in messages]
            success_count = len(messages)
            failure_count = 0

        return _Batch()

    monkeypatch.setattr(messaging, "send_each", fake_send_each)
    # Every store's resolved settings carry sending credentials (as if each
    # store had saved its own); pilot_mode stays the store's real setting.
    from app.tenancy import settings as tsettings

    real_for_ref = tsettings._settings_for_ref
    creds = {"whapi_api_token": "tok", "smtp_username": "store@example.com", "smtp_password": "pw",
             "smtp_host": "smtp.example.com", "whapi_chat_id": "1203@g.us", "ap_inbox_email": "ap@example.com",
             "toast_invoice_email": "ap@example.com"}
    monkeypatch.setattr(tsettings, "_settings_for_ref", lambda ref: real_for_ref(ref).model_copy(update=creds))
    pilot.reset_counts()
    yield calls
    pilot.reset_counts()


def _pilot_on(code):
    from app.platform.__main__ import main

    assert main(["pilot-mode", code, "on"]) == 0


def _try_every_transport():
    """Each transport once, as its callers use it; returns what each did."""
    from app.services import po_send, push, toast_email, whapi_client

    out = {}
    try:
        whapi_client.send_whatsapp_message("checklist done", to="1203@g.us")
        out["whatsapp"] = "sent"
    except whapi_client.WhapiNotConfigured as exc:
        out["whatsapp"] = f"not sent: {exc}"
    try:
        whapi_client.send_media_message("image", "1203@g.us", "media-id", caption="flyer")
        out["flyer"] = "sent"
    except whapi_client.WhapiNotConfigured as exc:
        out["flyer"] = f"not sent: {exc}"
    try:
        po_send.send_email(["vendor@example.com"], "PO", "body", b"%PDF", "po.pdf")
        out["po_email"] = "sent"
    except toast_email.EmailNotConfigured as exc:
        out["po_email"] = f"not sent: {exc}"
    try:
        push.send_to_tokens(["t1"], "Title", "Body", {})
        out["push"] = "sent"
    except push.PushNotConfigured:
        out["push"] = "not sent"
    return out


def test_pilot_store_sends_nothing_on_any_path_while_the_other_store_sends(sink, pdb):
    from app.ops import pilot
    from app.tenancy import for_each_store, spawn_in_store, store_context

    provision("ALPHA")
    provision("BETA", owner="bert")
    _pilot_on("ALPHA")

    # A per-store scheduled job (for_each_store), as the scheduler runs them.
    results = for_each_store(lambda ref: _try_every_transport())
    assert all(v.startswith("not sent") for v in results["ALPHA"].values()), results["ALPHA"]
    assert all(v == "sent" for v in results["BETA"].values()), results["BETA"]
    assert {kind for kind, _ in sink} == {"whatsapp", "email", "push"}
    beta_sends = len(sink)

    # A thread started from inside the pilot store keeps its store.
    box = {}
    with store_context("ALPHA"):
        spawn_in_store(lambda: box.update(_try_every_transport())).join(10)
    assert box and all(v.startswith("not sent") for v in box.values()), box
    assert len(sink) == beta_sends, "nothing more went out"
    counts = pilot.blocked_counts()
    assert counts["ALPHA:whatsapp"] == 4 and counts["ALPHA:email"] == 2 and counts["ALPHA:push"] >= 2
    assert not any(k.startswith("BETA:") for k in counts)


def _submit_invoice(client, code, username, password):
    token = store_login(client, code, username, password).json()["token"]
    auth = {"Authorization": f"Bearer {token}"}
    r = client.post(
        "/api/invoices",
        headers=auth,
        data={"vendor": "Acme", "submission_type": "invoice", "total_amount": "10.00"},
        files={"file": ("inv.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf")},
    )
    assert r.status_code == 200, r.text
    # TestClient runs the background send before returning.
    return client.get(f"/api/invoices/{r.json()['id']}", headers=auth).json()


def test_pilot_invoice_request_and_its_background_send_go_nowhere(sink, pdb, client, uploads):
    """The send runs after the response, on worker threads: they must keep
    the store (they didn't - multi mode never sent an invoice at all)."""
    from app.ops import pilot

    provision("ALPHA")
    provision("BETA", owner="bert")
    _pilot_on("ALPHA")
    entry = _submit_invoice(client, "ALPHA", "ann", "alpha-owner-pass")
    assert entry["whatsapp_sent"] is False and "Pilot mode" in (entry["whatsapp_error"] or "")
    assert entry["toast_email_sent"] is False and "Pilot mode" in (entry["toast_email_error"] or "")
    assert sink == []
    assert pilot.blocked_counts() == {"ALPHA:whatsapp": 1, "ALPHA:email": 1}

    entry = _submit_invoice(client, "BETA", "bert", "alpha-owner-pass")
    assert entry["whatsapp_sent"] is True and entry["toast_email_sent"] is True, entry
    assert sorted(kind for kind, _ in sink) == ["email", "whatsapp"]


def test_a_send_with_no_store_selected_is_refused_in_multi_mode(sink, pdb):
    from app.ops import pilot
    from app.services import push, whapi_client

    provision("BETA", owner="bert")
    with pytest.raises(whapi_client.WhapiNotConfigured, match="No store is selected"):
        whapi_client.send_whatsapp_message("hello", to="1203@g.us")
    assert push.is_configured() is False
    assert sink == []
    assert pilot.blocked_counts() == {"-:whatsapp": 1}


def test_pilot_store_still_syncs_sales_from_its_pos(sink, pdb, monkeypatch):
    from app.db import SessionLocal
    from app.models import Order as OrderRow
    from app.pos import registry
    from app.pos.sync import sales
    from app.tenancy import store_context
    from tests.test_pos_sync_neutral import DAY, FakeConnector, line, order

    provision("ALPHA")
    _pilot_on("ALPHA")
    fake = FakeConnector()
    fake.orders[DAY] = [order("o1", [line("s1", "tea", "2", "3.00"), line("s2", "rice", "1", "9.99")])]
    monkeypatch.setitem(registry.PROVIDERS, "toast", lambda: fake)
    registry.reset_cache()
    try:
        with store_context("ALPHA"):
            db = SessionLocal()
            try:
                job = sales.sync_sales_for_date(db, DAY)
                assert job.status == "success", job.detail
                assert db.query(OrderRow).count() == 2
            finally:
                db.close()
    finally:
        registry.reset_cache()
    assert sink == []


# --- import: a corrupted copy changes nothing --------------------------------------------


def _source_db(tmp_path) -> pathlib.Path:
    from tests.platform.test_ops_onboarding import _source_db as make

    return make(tmp_path)


def test_a_corrupted_copy_fails_the_import_and_leaves_the_store_untouched(tmp_path, pdb, client, monkeypatch):
    from app.ops import store_import
    from app.platform.models import AuditLog

    provision("ALPHA")
    url = _store_url("ALPHA")
    stamp_before = _scalar(url, "SELECT instance_id FROM store_identity")
    users_before = _scalar(url, "SELECT count(*) FROM users")
    src = _source_db(tmp_path)

    tool = store_import._copy_tool()
    real_reset = tool._reset_sequences

    def corrupt(cur, tables):  # inside the copy's transaction, after the rows landed
        real_reset(cur, tables)
        cur.execute("UPDATE orders SET net_price = net_price + 0.01 WHERE id = 1")
        cur.execute("DELETE FROM users WHERE id = 4")

    monkeypatch.setattr(tool, "_reset_sequences", corrupt)
    # Provisioning leaves a pooled connection to the store's database; the
    # import refuses while any is open (as it should), so release it first -
    # otherwise this test fails whenever that connection is still alive.
    from app.tenancy.engines import dispose_engine
    dispose_engine("ALPHA")
    with pytest.raises(store_import.StoreImportError) as exc:
        store_import.import_sqlite("ALPHA", src, log=lambda *a: None)
    message = str(exc.value)
    assert "difference(s) after the copy" in message and "users: 2 rows on SQLite, 1 on PostgreSQL" in message
    assert "nothing was changed" in message

    # The store's own database is exactly as it was; the staging one is gone.
    assert _scalar(url, "SELECT instance_id FROM store_identity") == stamp_before
    assert _scalar(url, "SELECT store_code FROM store_identity") == "ALPHA"
    assert _scalar(url, "SELECT count(*) FROM users") == users_before
    assert _scalar(url, "SELECT count(*) FROM orders") == 0
    staging = store_import._side_name(url.rsplit("/", 1)[1], store_import.STAGING_SUFFIX)
    assert _scalar(url, "SELECT count(*) FROM pg_database WHERE datname = :n", n=staging) == 0
    assert store_login(client, "ALPHA", "ann", "alpha-owner-pass").status_code == 200
    failed = pdb.query(AuditLog).filter(AuditLog.action == "store.import_failed").one()
    assert "checksum differs" in json.dumps(failed.details)
    assert pdb.query(AuditLog).filter(AuditLog.action == "store.imported").count() == 0

    # The same import without the corruption goes through.
    monkeypatch.setattr(tool, "_reset_sequences", real_reset)
    report = store_import.import_sqlite("ALPHA", src, log=lambda *a: None)
    assert report["differences"] == 0 and report["owner_added"] is True
    assert _scalar(url, "SELECT count(*) FROM orders") == 3
    assert store_login(client, "ALPHA", "ann", "alpha-owner-pass").status_code == 200


# --- backups: exact even while the store writes ------------------------------------------


def test_backup_counts_come_from_the_dump_s_snapshot_and_the_drill_is_exact(tmp_path, pdb, platform_env,
                                                                         monkeypatch):
    pg_bin = _pg_bin()
    if pg_bin is None:
        pytest.skip("pg_dump not installed")
    sys.path.insert(0, str(BACKEND / "scripts" / "backup"))
    import pg_backup

    provision("ALPHA")
    url = _store_url("ALPHA")
    real_run = pg_backup._run

    def write_while_dumping(cmd, **kwargs):
        if pathlib.Path(cmd[0]).name == "pg_dump":
            # The store keeps working while the dump runs.
            _exec(url, "INSERT INTO job_runs (job_name, started_at, status) VALUES ('during', now(), 'ok')")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(pg_backup, "_run", write_while_dumping)
    dest = tmp_path / "dumps"
    status = pg_backup.backup(url, str(dest), name="ALPHA", pg_bin=pg_bin, now=dt.datetime(2026, 10, 19, 3, 15))
    monkeypatch.setattr(pg_backup, "_run", real_run)
    assert status["ok"] and status["counts_recorded"] is True, status
    dump = dest / "ALPHA-20261019-031500.dump"
    recorded = json.loads(pg_backup.counts_file(dump).read_text())["tables"]
    assert recorded["job_runs"] == _scalar(url, "SELECT count(*) FROM job_runs") - 1, "the dump's snapshot"

    _exec(url, "INSERT INTO job_runs (job_name, started_at, status) VALUES ('after', now(), 'ok')")
    result = pg_backup.drill(url, str(dump), pg_bin=pg_bin)
    assert result["ok"], result["differences"]
    assert result["compared_with"] == "recorded at backup time"
    assert result["live_tables_changed_since"] == ["job_runs"]

    # A dump that restores short of what was recorded fails.
    data = json.loads(pg_backup.counts_file(dump).read_text())
    data["tables"]["job_runs"] += 1
    pg_backup.counts_file(dump).write_text(json.dumps(data))
    bad = pg_backup.drill(url, str(dump), pg_bin=pg_bin)
    assert bad["ok"] is False and bad["differences"] == [
        f"job_runs: expected {recorded['job_runs'] + 1} vs restored {recorded['job_runs']}"]

    # Pruning takes the recorded counts with the dump.
    later = pg_backup.backup(url, str(dest), name="ALPHA", pg_bin=pg_bin, now=dt.datetime(2026, 12, 1, 3, 15))
    assert later["removed"] == [dump.name]
    assert not pg_backup.counts_file(dump).exists()


# --- readiness ----------------------------------------------------------------------------


def test_ready_never_counts_a_check_that_could_not_run(pdb, client, monkeypatch):
    import app.migrate

    provision("ALPHA")
    assert client.get("/api/ready").status_code == 200

    def broken():
        raise RuntimeError("no migration scripts")

    monkeypatch.setattr(app.migrate, "store_head_revision", broken)
    r = client.get("/api/ready")
    assert r.status_code == 503, r.text
    assert r.json()["checks"]["store_migrations"] == "fail: RuntimeError"


# --- parity ----------------------------------------------------------------------------


def test_parity_against_an_empty_reference_is_not_clean(tmp_path, pdb):
    from app.ops.parity import run_parity

    provision("ALPHA")
    empty = tmp_path / "empty.db"
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{empty}", TENANCY_MODE="single")
    import subprocess

    alembic = pathlib.Path(sys.executable).parent / "alembic"
    subprocess.run([str(alembic), "upgrade", "head"], cwd=BACKEND, env=env, check=True, capture_output=True)
    report = run_parity("ALPHA", empty, days=5, today=dt.date(2026, 10, 12), log_dir=str(tmp_path / "logs"))
    assert report["differences"] == [] and report["ok"] is False
    assert "no sales lines" in report["problems"][0]
    sqlite3.connect(empty).close()
