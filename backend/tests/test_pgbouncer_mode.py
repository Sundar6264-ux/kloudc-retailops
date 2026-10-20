"""PgBouncer-compatible engine options (docs/WEBSITE_READY_CONTRACT.md C5):
DB_POOL_MODE=pgbouncer turns off server-side prepared statements and startup
options and uses no per-store pool; session features (the leader's advisory
lock, LISTEN) keep a direct connection. Direct mode is unchanged."""

import pytest
from sqlalchemy.pool import NullPool, QueuePool

URL = "postgresql+psycopg://u@127.0.0.1:1/so_x"


def test_direct_mode_is_the_default(monkeypatch):
    from app.tenancy import engines

    monkeypatch.delenv("DB_POOL_MODE", raising=False)
    assert engines.pool_mode() == "direct"
    args = engines.postgres_connect_args()
    assert args["options"].startswith("-c timezone=UTC") and "prepare_threshold" not in args
    eng = engines.create_store_engine(URL, tenant=True)
    try:
        assert isinstance(eng.pool, QueuePool)
    finally:
        eng.dispose()


def test_pgbouncer_mode(monkeypatch):
    from app.tenancy import engines

    monkeypatch.setenv("DB_POOL_MODE", "pgbouncer")
    assert engines.pool_mode() == "pgbouncer"
    assert engines.postgres_connect_args() == {"prepare_threshold": None}
    # the leader's lock connection stays direct (a session connection)
    assert "options" in engines.postgres_connect_args(direct=True)
    eng = engines.create_store_engine(URL, tenant=True)
    try:
        assert isinstance(eng.pool, NullPool)
    finally:
        eng.dispose()
    # the connection budget is PgBouncer's job then
    assert engines.check_connection_budget(max_connections=1) == engines.PLATFORM_CONNECTION_RESERVE


def test_platform_engine_without_prepared_statements(monkeypatch):
    from app.platform import db as pdb

    seen = {}
    real = pdb.create_engine

    def spy(url, **kw):
        seen.update(kw.get("connect_args", {}))
        return real(url, **kw)

    monkeypatch.setattr(pdb, "create_engine", spy)
    monkeypatch.setenv("DB_POOL_MODE", "pgbouncer")
    pdb.make_engine(URL).dispose()
    assert seen == {"prepare_threshold": None}
    seen.clear()
    monkeypatch.setenv("DB_POOL_MODE", "direct")
    pdb.make_engine(URL).dispose()
    assert seen == {"options": "-c timezone=UTC"}


def test_leader_and_listener_use_the_direct_url(monkeypatch):
    from app import livebus

    monkeypatch.setenv("PLATFORM_DIRECT_URL", "postgresql+psycopg://u@db:5432/so_platform")
    assert livebus.direct_url().endswith("@db:5432/so_platform")
    monkeypatch.delenv("PLATFORM_DIRECT_URL")
    from app.platform.config import pget

    assert livebus.direct_url() == str(pget("platform_database_url") or "").strip()


@pytest.mark.parametrize("value", ["", "Direct", "nonsense"])
def test_anything_else_is_direct(monkeypatch, value):
    from app.tenancy import engines

    monkeypatch.setenv("DB_POOL_MODE", value)
    assert engines.pool_mode() == "direct"
