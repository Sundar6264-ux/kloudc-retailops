"""The shared interfaces of docs/WEBSITE_READY_CONTRACT.md (I1-I5) exist with
the agreed names, and their stub behaviour is safe (single mode, no
outside service, nothing raised to callers that must never fail)."""

import threading

import pytest

import app.config as app_config
from app.config import get_settings


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    settings = get_settings().model_copy(update={"uploads_root": str(tmp_path / "uploads")})
    monkeypatch.setattr(app_config, "get_settings", lambda: settings)
    return tmp_path / "uploads"


def test_i1_local_storage_round_trip(uploads, tmp_path):
    from app.storage import ObjectNotFound, StorageError, get_storage

    st = get_storage()
    assert st.backend == "local"
    obj = st.put_bytes("exports/a.txt", b"hello")
    assert obj.key == "exports/a.txt" and obj.size == 5
    assert (uploads / "exports" / "a.txt").read_bytes() == b"hello"
    src = tmp_path / "src.bin"
    src.write_bytes(b"12345678")
    st.put_file("invoices/2026/b.bin", src)
    assert st.read_bytes("invoices/2026/b.bin") == b"12345678"
    assert st.exists("exports/a.txt") and not st.exists("exports/missing.txt")
    assert [o.key for o in st.list()] == ["exports/a.txt", "invoices/2026/b.bin"]
    assert st.bytes_used() == 13 and st.bytes_used("invoices") == 8
    assert st.presigned_url("exports/a.txt") is None
    assert st.local_path("exports/a.txt") == (uploads / "exports" / "a.txt").resolve()
    (uploads / ".keys").mkdir()
    (uploads / ".keys" / "k").write_bytes(b"secret")
    assert all(not o.key.startswith(".") for o in st.list())
    assert st.delete_prefix("invoices") == 1 and st.delete("exports/a.txt")
    assert not st.delete("exports/a.txt")
    with pytest.raises(ObjectNotFound):
        st.open("exports/a.txt")
    for bad in ("/etc/passwd", "../x", "a/../b", "a//b", "a\\b", ""):
        with pytest.raises(StorageError):
            st.put_bytes(bad, b"x")


def test_i2_jobqueue_inline_runs_once_in_a_thread():
    from app import jobqueue

    done = threading.Event()
    seen = {}

    @jobqueue.register("contract_probe")
    def probe(payload):
        seen.update(payload)
        done.set()
        return {"ok": True}

    assert jobqueue.mode() == "inline"
    handle = jobqueue.enqueue("contract_probe", {"n": 1})
    assert handle.kind == "contract_probe" and handle.id is None
    assert done.wait(5) and seen == {"n": 1}
    assert jobqueue.job_status(1) is None
    with pytest.raises(jobqueue.UnknownJobKind):
        jobqueue.enqueue("no_such_kind")
    with pytest.raises(TypeError):
        jobqueue.enqueue("contract_probe", {"bad": object()})
    with pytest.raises(ValueError):
        jobqueue.register("contract_probe")(lambda p: None)


def test_i3_usage_never_raises():
    from app.platform import usage

    assert set(usage.COUNTERS) <= set(usage.METRICS) and set(usage.GAUGES) <= set(usage.METRICS)
    usage.record(usage.POS_API_CALLS)
    usage.record("orders_synced", 25, code="ABC")
    usage.touch_activity()
    usage.start()
    usage.stop()
    assert usage.snapshot("ABC")["metrics"] == {}


def test_i4_plans_and_i5_platform_settings():
    from app.platform import plans, platform_settings as ps

    assert plans.DEFAULT_PLAN in plans.PLAN_IDS and plans.plan_of("ABC") == "custom"
    assert plans.limit_states("ABC") == []
    assert ps.get_platform_setting(ps.OFFBOARD_GRACE_DAYS) == 30
    assert ps.get_platform_setting(ps.EXPORT_LINK_HOURS) == 24
    assert ps.get_platform_setting(ps.DEFAULT_PLAN) in plans.PLAN_IDS
    assert isinstance(ps.get_platform_setting(ps.SIGNUP_ENABLED), bool)
    with pytest.raises(KeyError):
        ps.get_platform_setting("platform_require_totp")  # security switches are never editable


def test_i5_environment_values_are_coerced(monkeypatch):
    from app.platform import platform_settings as ps

    monkeypatch.setenv("OFFBOARD_GRACE_DAYS", "45")
    monkeypatch.setenv("EXPORT_LINK_HOURS", "nope")
    assert ps.get_platform_setting(ps.OFFBOARD_GRACE_DAYS) == 45
    assert ps.get_platform_setting(ps.EXPORT_LINK_HOURS) == 24


def test_i2_periodic_registration():
    from app import jobqueue

    @jobqueue.register("contract_probe_nightly")
    def nightly(payload):
        return None

    jobqueue.register_periodic("contract_probe_nightly", hour=3, minute=30)
    spec = jobqueue.periodic_jobs()["contract_probe_nightly"]
    assert (spec.hour, spec.minute, spec.per_store) == (3, 30, True)
    with pytest.raises(jobqueue.UnknownJobKind):
        jobqueue.register_periodic("not_registered", hour=1)
    with pytest.raises(ValueError):
        jobqueue.register_periodic("contract_probe_nightly", hour=24)


def test_track_routers_are_mounted():
    from app.main import app

    prefixes = {getattr(r, "path", "") for r in app.routes}
    assert "/api/health" in prefixes
    from app.platform import routes_clover, routes_console, routes_data, routes_system
    from app.routers import item_vendors

    assert routes_console.router.prefix == "/api/platform"
    assert routes_data.store_router.prefix == "/api/settings"
    assert routes_system.router.prefix == "/api/platform/system"
    assert routes_clover.router.prefix == "/api/settings/integrations/clover"
    assert item_vendors.router.prefix == "/api/item-vendors"
