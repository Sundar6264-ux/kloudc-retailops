"""Single mode (TENANCY_MODE=single, the default) keeps the M2 behaviour
(docs/PLATFORM_CONTRACT.md 0.2.3, 4.2). Runs on the suite's SQLite database."""

from fastapi.testclient import TestClient

from app.main import app


def test_single_mode_has_one_implicit_store(db):
    from app.config import get_settings, process_settings
    from app.db import SessionLocal, engine
    from app.tenancy import current_scope, current_store, for_each_store, is_multi_tenant, tenant_engine

    assert not is_multi_tenant()
    assert get_settings() is process_settings()
    assert current_store().code == "_" and current_scope() == "store"
    assert tenant_engine() is engine
    assert for_each_store(lambda ref: ref.code) == {"_": "_"}
    s = SessionLocal()
    try:
        assert s.info["store_code"] == "_"
    finally:
        s.close()


def test_single_mode_tokens_carry_the_store_code(db, admin, monkeypatch):
    from app.auth import create_session, token_hash
    from app.config import process_settings
    from app.models import UserSession

    monkeypatch.setattr(process_settings(), "store_code", "XYZ_GROCERY")
    token, _ = create_session(db, admin.id)
    assert token.startswith("XYZ_GROCERY.")
    assert db.get(UserSession, token_hash(token)) is not None
    assert db.get(UserSession, token) is None  # never stored raw
    ok = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    assert ok.get("/api/auth/me").status_code == 200
    # The same secret under another store's code is refused here.
    other = "OTHER_STORE." + token.split(".", 1)[1]
    assert TestClient(app, headers={"Authorization": f"Bearer {other}"}).get("/api/auth/me").status_code == 401
    # A pre-platform raw token (no prefix) no longer works: one re-login.
    raw = token.split(".", 1)[1]
    db.add(UserSession(token=token_hash(raw), user_id=admin.id, expires_at=_future()))
    db.commit()
    assert TestClient(app, headers={"Authorization": f"Bearer {raw}"}).get("/api/auth/me").status_code == 401


def _future():
    import datetime as dt

    from app.timeutil import utcnow

    return utcnow() + dt.timedelta(days=1)


def test_middleware_is_a_no_op_in_single_mode(admin_client):
    # X-Store / ?store= mean nothing in single mode (M2 clients never send them).
    assert admin_client.get("/api/auth/me", headers={"X-Store": "ANY"}).status_code == 200
    assert admin_client.get("/api/auth/me", params={"store": "ANY"}).status_code == 200


def test_get_settings_cache_clear_still_exists():
    from app.config import get_settings

    assert callable(get_settings.cache_clear)
