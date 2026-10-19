"""Context propagation, locks, caches, settings overlay, files, long-poll and
engines (docs/PLATFORM_CONTRACT.md 18.2, track B)."""

import asyncio
import datetime as dt
import threading
import time

import pytest
from fastapi import BackgroundTasks, Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.concurrency import run_in_threadpool

from app.tenancy import (
    current_store_code,
    keyed_lock,
    spawn_in_store,
    store_context,
    tenant_cache,
    tenant_local,
    tenant_lock,
)
from app.tenancy.middleware import TenantMiddleware
from tests.tenancy.conftest import seed_users

# --- context propagation -------------------------------------------------------------------


def _probe_app(seen):
    probe = FastAPI()
    probe.add_middleware(TenantMiddleware)

    def dep():
        return current_store_code()

    def background(tag):
        seen.append((tag, current_store_code()))

    @probe.get("/sync")
    def sync_endpoint(d: str = Depends(dep), tasks: BackgroundTasks = None):
        tasks.add_task(background, "background")
        return {"endpoint": current_store_code(), "dependency": d}

    @probe.get("/async")
    async def async_endpoint():
        in_pool = await run_in_threadpool(current_store_code)
        box = []
        t = spawn_in_store(lambda: box.append(current_store_code()), name="probe")
        t.join(5)
        return {"endpoint": current_store_code(), "threadpool": in_pool, "spawned": box[0]}

    return probe


def test_context_reaches_threadpool_dependencies_background_and_threads(multi):
    a = seed_users("ALPHA", "alice")
    b = seed_users("BETA", "bob")
    seen = []
    client = TestClient(_probe_app(seen))
    for code, token in (("ALPHA", a["alice"][1]), ("BETA", b["bob"][1])):
        r = client.get("/sync", headers={"Authorization": f"Bearer {token}"})
        assert r.json() == {"endpoint": code, "dependency": code}
        assert seen[-1] == ("background", code)
        r = client.get("/async", headers={"Authorization": f"Bearer {token}"})
        assert r.json() == {"endpoint": code, "threadpool": code, "spawned": code}
    # No token: no store.
    assert client.get("/sync").json() == {"endpoint": None, "dependency": None}


def test_raw_thread_without_spawn_in_store_has_no_store(multi):
    box = []
    with store_context("ALPHA"):
        t = threading.Thread(target=lambda: box.append(current_store_code()))
        t.start()
        t.join()
    assert box == [None]


def test_scheduler_job_runner_runs_in_its_store(multi):
    from app.tenancy.jobs import store_job_runner

    box = []
    store_job_runner(lambda: box.append(current_store_code()), "GAMMA")()
    assert box == ["GAMMA"]


# --- locks -------------------------------------------------------------------------------


def test_keyed_lock_is_per_store(multi):
    lock = tenant_lock("probe.lock")
    with store_context("ALPHA"):
        assert lock.acquire()
        try:
            assert keyed_lock("probe.lock").locked()
            with store_context("BETA"):
                # Another store never waits on ALPHA's lock.
                assert lock.acquire(blocking=False)
                lock.release()
            assert not lock.acquire(blocking=False)
        finally:
            lock.release()
    with store_context("ALPHA"):
        assert not lock.locked()


def test_reentrant_tenant_lock(multi):
    lock = tenant_lock("probe.rlock", reentrant=True)
    with store_context("ALPHA"):
        with lock:
            with lock:
                pass


def test_advisory_lock_serialises_across_sessions_on_postgres(multi):
    from sqlalchemy import text

    from app.tenancy import advisory_lock, tenant_session
    from app.tenancy.locks import advisory_key

    with store_context("ALPHA"):
        key = advisory_key("probe")
        with tenant_session() as db1:
            with advisory_lock(db1, "probe"):
                with tenant_session() as db2:
                    got = db2.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": key}).scalar()
                    assert got is False  # held by db1's transaction
            db1.rollback()
            with tenant_session() as db3:
                assert db3.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": key}).scalar() is True


# --- caches ---------------------------------------------------------------------------------


def test_tenant_cache_and_local_are_per_store(multi):
    from app.tenancy import clear_tenant_caches

    cache = tenant_cache("probe.cache", maxsize=2, ttl=60)
    local = tenant_local("probe.local", dict)
    with store_context("ALPHA"):
        cache.set("k", "alpha")
        local["k"] = "alpha"
    with store_context("BETA"):
        assert cache.get("k") is None and "k" not in local
        cache.set("k", "beta")
        local["k"] = "beta"
    with store_context("ALPHA"):
        assert cache.get("k") == "alpha" and local["k"] == "alpha"
        cache.clear()
        local.clear()
    with store_context("BETA"):
        assert cache.get("k") == "beta" and local["k"] == "beta"
    clear_tenant_caches("BETA")
    with store_context("BETA"):
        assert cache.get("k") is None and "k" not in local


def test_tenant_cache_lru_and_ttl(multi, monkeypatch):
    cache = tenant_cache("probe.lru", maxsize=2, ttl=10)
    with store_context("ALPHA"):
        cache.clear()
        cache.set(1, "a")
        cache.set(2, "b")
        cache.get(1)
        cache.set(3, "c")  # evicts 2 (least recently used)
        assert cache.get(2) is None and cache.get(1) == "a"
        assert cache.get_or_set(4, lambda: "d") == "d"
        real = time.monotonic
        monkeypatch.setattr(time, "monotonic", lambda: real() + 11)
        assert cache.get(4) is None


def test_real_caches_do_not_leak_between_stores(multi):
    """Warm the report cache and the POS connector cache in ALPHA, read them in
    BETA: BETA computes its own."""
    from app.pos import registry as pos_registry
    from app.services import report_cache

    with store_context("ALPHA"):
        report_cache.put(("probe",), ("v",), "alpha-numbers", report_cache.generation())
        assert report_cache.peek(("probe",), ("v",)) == (True, "alpha-numbers")
        conn_a = pos_registry.get_connector()
    with store_context("BETA"):
        assert report_cache.peek(("probe",), ("v",)) == (False, None)
        assert pos_registry.get_connector() is not conn_a
    with store_context("ALPHA"):
        report_cache.invalidate("probe")
    with store_context("ALPHA"):
        assert report_cache.peek(("probe",), ("v",)) == (False, None)


def test_a_commit_in_one_store_ends_only_its_report_cache(multi):
    """The engine hook: a committed write to a tracked table through ALPHA's
    engine ends ALPHA's cached reports, never BETA's."""
    from app.services import report_cache
    from app.setting_models import SettingChange
    from app.tenancy import tenant_session

    for code in ("ALPHA", "BETA"):
        with store_context(code):
            report_cache.put(("probe",), ("v",), code, report_cache.generation())
    with tenant_session("ALPHA") as db:
        db.add(SettingChange(key="probe", value="1"))
        db.commit()
    with store_context("ALPHA"):
        assert report_cache.peek(("probe",), ("v",)) == (False, None)
    with store_context("BETA"):
        assert report_cache.peek(("probe",), ("v",)) == (True, "BETA")


# --- settings overlay ------------------------------------------------------------------------


def test_settings_overlay_precedence(multi):
    from app.config import get_settings, process_settings
    from app.tenancy.testing import set_test_store_settings

    set_test_store_settings(
        "ALPHA",
        {"store_phone": "555-0100", "sales_sync_hour": 5, "cors_origins": "https://evil.test", "store_code": "BETA"},
    )
    set_test_store_settings("ALPHA", {"toast_client_id": "alpha-client", "smtp_password": "pw"}, credentials=True)
    with store_context("ALPHA"):
        s = get_settings()
        assert s.store_code == "ALPHA"  # derived wins, never a stored value
        assert s.store_name == "Alpha Market"  # registry
        assert s.store_phone == "555-0100" and s.sales_sync_hour == 5  # store_settings
        assert s.toast_client_id == "alpha-client" and s.smtp_password == "pw"  # credentials
        assert s.cors_origins == process_settings().cors_origins  # platform-only: ignored
        assert s.public_host == "https://app.retailops.test"
        assert s.team_uploads_dir.endswith("/uploads/ALPHA/team")
    with store_context("BETA"):
        s = get_settings()
        assert s.store_code == "BETA" and s.store_phone == "" and s.toast_client_id == ""
    assert get_settings() is process_settings()  # no store: platform level


def test_settings_invalidation_and_version(multi, monkeypatch):
    from app.config import get_settings, process_settings
    from app.tenancy import invalidate_store_settings
    from app.tenancy.testing import test_settings

    monkeypatch.setattr(process_settings(), "settings_refresh_seconds", 3600)
    with store_context("BETA"):
        assert get_settings().store_phone == ""
        test_settings.settings["BETA"] = {"store_phone": "555-0199"}
        assert get_settings().store_phone == ""  # cached until invalidated / refreshed
        invalidate_store_settings("BETA")
        assert get_settings().store_phone == "555-0199"
        # A monkeypatch of get_settings() patches this store's object only.
        monkeypatch.setattr(get_settings(), "store_email", "beta@x.test")
    with store_context("ALPHA"):
        assert get_settings().store_email == ""


def test_bad_store_setting_is_dropped_not_fatal(multi):
    from app.config import get_settings
    from app.tenancy import validate_store_setting
    from app.tenancy.testing import set_test_store_settings

    set_test_store_settings("GAMMA", {"brand_color": "red", "store_phone": "555-0177"})
    with store_context("GAMMA"):
        s = get_settings()
        assert s.brand_color == "#004BAD" and s.store_phone == "555-0177"
    with pytest.raises(ValueError):
        validate_store_setting("brand_color", "red")
    with pytest.raises(ValueError):
        validate_store_setting("cors_origins", "x")
    assert validate_store_setting("brand_color", "#abcdef") == "#ABCDEF"


def test_store_timezone_comes_from_the_registry(multi):
    from app.config import get_settings
    from app.tenancy.testing import update_test_store

    update_test_store("GAMMA", timezone="Asia/Kolkata")
    with store_context("GAMMA"):
        assert get_settings().store_timezone == "Asia/Kolkata"
        assert get_settings().toast_timezone == "Asia/Kolkata"


def test_push_data_carries_the_current_store(multi):
    from app.services.push import with_store

    with store_context("ALPHA"):
        assert with_store({"type": "x"})["store"] == "ALPHA"
    with store_context("BETA"):
        assert with_store({"type": "x"})["store"] == "BETA"


# --- files ------------------------------------------------------------------------------------


def test_store_paths_stay_inside_the_store(multi, tmp_path):
    import os

    from app.tenancy import store_path, store_root

    with store_context("ALPHA"):
        root = store_root()
        assert root == (tmp_path / "uploads" / "ALPHA").resolve()
        assert store_path("team", "a.jpg") == root / "team" / "a.jpg"
        for bad in (("..", "BETA", "x"), ("/etc/passwd",), ("team/../../BETA",), ("a\\..\\b",)):
            with pytest.raises(ValueError):
                store_path(*bad)
        (root / "team").mkdir(parents=True, exist_ok=True)
        (tmp_path / "outside").mkdir()
        os.symlink(tmp_path / "outside", root / "team" / "escape")
        with pytest.raises(ValueError):
            store_path("team", "escape", "x.jpg")
        assert store_path("invoices", create=True).is_dir()
    assert store_root("BETA") == (tmp_path / "uploads" / "BETA").resolve()


# --- long-poll --------------------------------------------------------------------------------


def test_change_feed_wakeups_are_per_store(multi):
    from app.team import changes

    async def scenario():
        with store_context("BETA"):
            vb = changes.change_version()
            waiter = asyncio.ensure_future(changes.wait_for_change(vb, 1.0))
        await asyncio.sleep(0.05)
        with store_context("ALPHA"):
            va = changes.change_version()
            changes.notify_changed()
            assert changes.change_version() == va + 1
        woke = await waiter
        with store_context("BETA"):
            assert changes.change_version() == vb
        return woke

    assert asyncio.run(scenario()) is False  # ALPHA's change never woke BETA


def test_commit_bumps_the_sessions_store(multi):
    from app.team import changes
    from app.tenancy import tenant_session

    with store_context("GAMMA"):
        before = changes.change_version()
    with tenant_session("GAMMA") as db:
        db.info[changes._DIRTY] = True
        db.commit()
    with store_context("GAMMA"):
        assert changes.change_version() == before + 1


# --- engines ----------------------------------------------------------------------------------


def test_engine_lru_never_disposes_a_busy_engine(multi, monkeypatch):
    from app.config import process_settings
    from app.tenancy import engines

    monkeypatch.setattr(process_settings(), "tenant_max_engines", 2)
    engines.dispose_all_engines()
    a = engines.tenant_engine("ALPHA")
    held = a.connect()  # ALPHA busy
    try:
        engines.tenant_engine("BETA")
        engines.tenant_engine("GAMMA")  # over the limit: BETA (idle) goes, ALPHA (busy, older) stays
        assert set(engines.open_engine_codes()) == {"ALPHA", "GAMMA"}
        stats = engines.engine_stats()
        assert stats["ALPHA"]["checked_out"] == 1
        # Idle sweep: ALPHA is old but busy -> kept; GAMMA idle -> closed.
        far = time.monotonic() + 10_000
        closed = engines.sweep_idle_engines(now=far)
        assert closed == ["GAMMA"] and engines.open_engine_codes() == ["ALPHA"]
    finally:
        held.close()
    assert engines.sweep_idle_engines(now=time.monotonic() + 10_000) == ["ALPHA"]


def test_pg_connections_run_in_utc_with_a_statement_timeout(multi):
    from sqlalchemy import text

    from app.tenancy import tenant_session

    with tenant_session("ALPHA") as db:
        assert db.execute(text("SHOW timezone")).scalar() == "UTC"
        assert db.execute(text("SHOW statement_timeout")).scalar() == "1min"
        assert db.execute(text("SELECT current_database()")).scalar().endswith("_alpha")
        assert db.info["store_code"] == "ALPHA"


def test_session_local_needs_a_store_in_multi_mode(multi):
    from app.db import SessionLocal
    from app.tenancy import NoStoreContext

    with pytest.raises(NoStoreContext):
        SessionLocal()
    with store_context("BETA"):
        db = SessionLocal()
        try:
            from sqlalchemy import text

            assert db.execute(text("SELECT current_database()")).scalar().endswith("_beta")
        finally:
            db.close()


def test_connection_budget_check(multi, monkeypatch):
    from app.config import process_settings
    from app.tenancy.engines import check_connection_budget

    assert check_connection_budget(max_connections=300) == 40 * 5 + 10
    with pytest.raises(RuntimeError, match="max_connections"):
        check_connection_budget(max_connections=200)
    monkeypatch.setattr(process_settings(), "tenant_max_engines", 20)
    assert check_connection_budget(max_connections=200) == 110


def test_unknown_or_inactive_store_context(multi):
    from app.tenancy import StoreNotActive, UnknownStore
    from app.tenancy.testing import update_test_store

    with pytest.raises(UnknownStore):
        with store_context("NOPE"):
            pass
    update_test_store("GAMMA", status="pending")
    with pytest.raises(StoreNotActive):
        with store_context("GAMMA"):
            pass
    with store_context("GAMMA", allow_inactive=True) as ref:
        assert ref.status == "pending"


def test_platform_env_credentials_never_reach_a_store(multi, monkeypatch):
    """One store's Toast / WhatsApp / email values left in the platform's .env
    must not become every store's (they would read another store's data)."""
    from app.config import get_settings, process_settings
    from app.tenancy.testing import set_test_store_settings

    s = process_settings()
    for key, value in {
        "toast_client_id": "leaked-id",
        "toast_client_secret": "leaked-secret",
        "toast_restaurant_guid": "leaked-guid",
        "whapi_api_token": "leaked-token",
        "gmail_app_password": "leaked-pw",
        "anthropic_api_key": "leaked-key",
        "toast_invoice_email": "ap@leak.test",
        "insights_hmac_secret": "leaked-hmac",
        "store_phone": "555-LEAK",
        "team_poll_interval_s": 45,
    }.items():
        monkeypatch.setattr(s, key, value)
    set_test_store_settings("BETA", {"toast_client_id": "beta-id", "toast_restaurant_guid": "beta-guid"}, credentials=True)
    with store_context("ALPHA"):
        a = get_settings()
        assert (a.toast_client_id, a.toast_client_secret, a.toast_restaurant_guid) == ("", "", "")
        assert a.whapi_api_token == a.gmail_app_password == a.anthropic_api_key == ""
        assert a.toast_invoice_email == "" and a.insights_hmac_secret == "" and a.store_phone == ""
        assert a.team_poll_interval_s == 45  # a product-wide tuning value still comes from env
    with store_context("BETA"):
        b = get_settings()
        assert (b.toast_client_id, b.toast_restaurant_guid, b.toast_client_secret) == ("beta-id", "beta-guid", "")
