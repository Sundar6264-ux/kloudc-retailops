"""Cross-process live updates (docs/WEBSITE_READY_CONTRACT.md C4) on the
scratch platform database: LISTEN/NOTIFY between two listeners, Team
long-polls woken from another process, store changes reaching the
scheduler leader, and local mode unchanged."""

import asyncio
import json
import threading
import time

import psycopg
import pytest


@pytest.fixture
def pg_mode(monkeypatch, platform_env):
    from app import livebus

    monkeypatch.setenv("LIVE_UPDATES_MODE", "postgres")
    assert livebus.mode() == "postgres"
    yield livebus
    livebus.stop()


def _notify(url, payload):
    from app.livebus import CHANNEL, _conninfo

    with psycopg.connect(_conninfo(url), autocommit=True) as conn:
        conn.execute("SELECT pg_notify(%s, %s)", (CHANNEL, json.dumps(payload)))


def test_local_mode_is_the_default_and_sends_nothing(platform_env, monkeypatch):
    from app import livebus

    monkeypatch.delenv("LIVE_UPDATES_MODE", raising=False)
    assert livebus.mode() == "local"
    assert livebus.publish("team", "ALPHA") is False
    assert livebus.start() is False
    assert livebus.status()["mode"] == "local"


def test_single_mode_is_always_local(platform_env, monkeypatch):
    from app import livebus

    monkeypatch.setenv("LIVE_UPDATES_MODE", "postgres")
    monkeypatch.setattr(platform_env, "tenancy_mode", "single")
    assert livebus.mode() == "local"


def test_two_listeners_hear_one_notify_within_a_second(pg_mode, platform_db_url):
    got = {"a": threading.Event(), "b": threading.Event()}
    seen = []

    def on_probe(code, data):
        seen.append((code, data))
        got[data["to"]].set()

    pg_mode.subscribe("probe", on_probe)
    first = pg_mode.Listener(platform_db_url).start()
    second = pg_mode.Listener(platform_db_url).start()
    try:
        assert first.ready.wait(5) and second.ready.wait(5)
        t0 = time.monotonic()
        _notify(platform_db_url, {"e": "probe", "s": "ALPHA", "o": "another-process", "d": {"to": "a"}})
        assert got["a"].wait(1.0)
        assert time.monotonic() - t0 < 1.0
        time.sleep(0.3)
        # both listeners got it (two processes = two deliveries)
        assert seen.count(("ALPHA", {"to": "a"})) == 2
    finally:
        first.stop()
        second.stop()
        pg_mode.unsubscribe("probe", on_probe)


def test_own_messages_are_ignored(pg_mode):
    assert pg_mode.dispatch(json.dumps({"e": "team", "s": "A", "o": pg_mode.ORIGIN})) is False
    assert pg_mode.dispatch("not json") is False
    assert pg_mode.dispatch(json.dumps({"e": "nobody", "s": None, "o": "x"})) is True


def test_publish_reaches_another_processes_listener(pg_mode, platform_db_url, monkeypatch):
    """publish() from "process 1" (its own ORIGIN) wakes the listener of
    "process 2" (here: a listener that sees a different ORIGIN)."""
    got = threading.Event()
    pg_mode.subscribe("probe2", lambda code, data: got.set())
    listener = pg_mode.Listener(platform_db_url).start()
    try:
        assert listener.ready.wait(5)
        monkeypatch.setattr(pg_mode, "ORIGIN", "process-1")
        assert pg_mode.publish("probe2", "ALPHA", {"n": 1}) is True
        monkeypatch.setattr(pg_mode, "ORIGIN", "process-2")  # the listener's process
        assert got.wait(2)
        # The publisher thread counts after pg_notify returns, so the listener
        # can be woken a moment before the count moves (seen on slow CI).
        deadline = time.monotonic() + 2
        while pg_mode.status()["published"] < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pg_mode.status()["published"] >= 1
    finally:
        listener.stop()


def test_team_commit_in_one_process_wakes_long_polls_in_another(pg_mode, platform_db_url):
    from app.team import changes

    listener = pg_mode.Listener(platform_db_url).start()
    assert listener.ready.wait(5)

    async def wait_one():
        version = changes.change_version()
        t0 = time.monotonic()
        woke = await changes.wait_for_change(version, 5)
        return woke, time.monotonic() - t0

    async def main():
        task = asyncio.create_task(wait_one())
        await asyncio.sleep(0.2)
        # another web process committed a Team change (no store here: the
        # waiter above runs without a store context, like the payload)
        await asyncio.to_thread(_notify, platform_db_url, {"e": "team", "s": None, "o": "web-2", "d": {}})
        return await task

    try:
        woke, took = asyncio.run(main())
        assert woke and took < 1.5
    finally:
        listener.stop()


def test_store_change_in_a_web_process_reaches_the_leader(pg_mode, platform_db_url, monkeypatch):
    from app.tenancy import jobs as tjobs

    calls = []
    real_reconcile = tjobs.reconcile_store_jobs
    monkeypatch.setattr(tjobs, "reconcile_store_jobs", lambda: calls.append("reconcile") or {})
    listener = pg_mode.Listener(platform_db_url).start()
    try:
        assert listener.ready.wait(5)
        _notify(platform_db_url, {"e": "store_startup", "s": "ALPHA", "o": "web-2", "d": {}})
        deadline = time.time() + 2
        while time.time() < deadline and not calls:
            time.sleep(0.05)
        assert calls == ["reconcile"]
    finally:
        listener.stop()
        monkeypatch.setattr(tjobs, "reconcile_store_jobs", real_reconcile)


def test_local_changes_publish_in_postgres_mode(pg_mode, monkeypatch):
    from app.team import changes
    from app.tenancy import jobs as tjobs

    sent = []
    monkeypatch.setattr(pg_mode, "publish", lambda e, s, d=None: sent.append((e, s)) or True)
    monkeypatch.setattr(tjobs, "reconcile_store_jobs", lambda: {})
    changes.notify_changed("ALPHA")
    changes.notify_changed("ALPHA", _remote=True)  # from the bus: not sent back
    tjobs.rerun_store_startup_jobs("alpha")
    tjobs.rerun_store_startup_jobs("ALPHA", _remote=True)
    assert sent == [("team", "ALPHA"), ("store_startup", "ALPHA")]


def test_listener_reconnects_after_losing_its_connection(pg_mode, platform_db_url):
    from sqlalchemy import create_engine, text

    got = threading.Event()
    pg_mode.subscribe("probe3", lambda code, data: got.set())
    listener = pg_mode.Listener(platform_db_url).start()
    try:
        assert listener.ready.wait(5)
        eng = create_engine(platform_db_url.rsplit("/", 1)[0] + "/postgres", isolation_level="AUTOCOMMIT")
        db = platform_db_url.rsplit("/", 1)[1]
        with eng.connect() as c:
            c.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :d "
                           "AND query LIKE 'LISTEN%' AND pid <> pg_backend_pid()"), {"d": db})
        eng.dispose()
        listener.ready.clear()
        deadline = time.time() + 8
        while time.time() < deadline:
            if listener.ready.is_set():
                break
            time.sleep(0.1)
        # reconnected (ready set again after the first back-off)
        time.sleep(0.2)
        _notify(platform_db_url, {"e": "probe3", "s": None, "o": "x", "d": {}})
        assert got.wait(3)
    finally:
        listener.stop()
