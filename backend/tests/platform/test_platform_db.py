"""Platform database: migrations up/down/up, metadata parity, append-only
audit log (contract 3.6, 11.2)."""

import pytest
from sqlalchemy import create_engine, text

from tests.platform.conftest import PREFIX, PID, _admin, _url_for


def test_platform_migration_up_down_up_and_parity():
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.platform.db import (
        current_platform_revision,
        downgrade_platform_db,
        platform_head_revision,
        upgrade_platform_db,
    )
    from app.platform.models import PlatformBase

    name = f"{PREFIX}_mig_{PID}"
    admin = _admin()
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        c.execute(text(f'CREATE DATABASE "{name}"'))
    url = _url_for(name)
    try:
        head = platform_head_revision()
        assert head == "p0001"
        assert upgrade_platform_db(url) == head
        assert downgrade_platform_db(url, "base") is None
        eng = create_engine(url)
        with eng.connect() as c:
            left = c.execute(text("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name <> 'alembic_version'")).scalar()
        assert left == 0
        assert upgrade_platform_db(url) == head
        assert current_platform_revision(url) == head
        with eng.connect() as c:
            diffs = compare_metadata(MigrationContext.configure(c), PlatformBase.metadata)
        eng.dispose()
        assert diffs == []
    finally:
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def test_audit_log_is_append_only(pdb):
    from app.platform.audit import audit
    from app.platform.models import AuditLog

    audit("test.action", actor_type="system", details={"a": 1})
    row = pdb.query(AuditLog).one()
    assert row.action == "test.action"
    with pytest.raises(Exception, match="append only"):
        pdb.execute(text("UPDATE audit_log SET action = 'x'"))
        pdb.commit()
    pdb.rollback()
    with pytest.raises(Exception, match="append only"):
        pdb.execute(text("DELETE FROM audit_log"))
        pdb.commit()
    pdb.rollback()
    assert pdb.query(AuditLog).count() == 1


def test_audit_details_never_keep_secret_keys(pdb):
    import json

    from app.platform.audit import audit
    from app.platform.models import AuditLog

    audit("t.x", actor_type="system", details={"password": "p", "client_secret": "s", "api_token": "t",
                                                 "nested": {"api_key": "k", "ok": 1}, "secrets_changed": ["api_key"]})
    details = json.loads(pdb.query(AuditLog).one().details)
    assert details == {"nested": {"ok": 1}, "secrets_changed": ["api_key"]}


def test_models_smoke_on_sqlite():
    from app.platform.models import PlatformBase

    eng = create_engine("sqlite://")
    PlatformBase.metadata.create_all(eng)
    assert "stores" in PlatformBase.metadata.tables
    eng.dispose()
