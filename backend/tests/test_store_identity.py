"""The store identity stamp (StoreOps M2, MT8; docs/M2_CONTRACT.md 1.5).
Every row of the startup table plus the upload-folder marker cases."""

import json
import types

import pytest

from app.store import identity
from app.store.identity import StoreIdentityError, check_store_identity, read_stamp, stamp_database


def _settings(tmp_path, code="", adopt=False, uploads="uploads"):
    return types.SimpleNamespace(
        store_code=code, store_identity_adopt=adopt, uploads_root=str(tmp_path / uploads)
    )


def test_blank_code_and_no_stamp_starts_with_a_warning(db, tmp_path, caplog):
    check_store_identity(_settings(tmp_path), db)
    assert "identity check skipped" in caplog.text
    assert read_stamp(db) is None
    assert not (tmp_path / "uploads").exists()  # nothing written


def test_blank_code_with_a_stamp_refuses(db, tmp_path):
    stamp_database(db, "STORE_A", "store-init")
    with pytest.raises(StoreIdentityError, match="STORE_CODE is blank but this database belongs to STORE_A"):
        check_store_identity(_settings(tmp_path), db)


def test_code_set_and_no_stamp_refuses_with_the_init_hint(db, tmp_path):
    with pytest.raises(StoreIdentityError, match="python -m app.store init"):
        check_store_identity(_settings(tmp_path, "STORE_A"), db)
    assert read_stamp(db) is None
    assert not (tmp_path / "uploads").exists()


def test_code_set_no_stamp_adopt_stamps_once(db, tmp_path):
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "old.jpg").write_bytes(b"x")
    check_store_identity(_settings(tmp_path, "STORE_A", adopt=True), db)
    stamp = read_stamp(db)
    assert stamp["store_code"] == "STORE_A" and stamp["created_by"] == "adopt"
    marker = json.loads((tmp_path / "uploads" / ".store_identity").read_text())
    assert marker == {"store_code": "STORE_A", "instance_id": stamp["instance_id"]}
    # Later starts (adopt left on or off) keep the same stamp.
    check_store_identity(_settings(tmp_path, "STORE_A"), db)
    assert read_stamp(db)["instance_id"] == stamp["instance_id"]


def test_same_code_writes_a_missing_marker_into_an_empty_folder(db, tmp_path):
    instance = stamp_database(db, "STORE_A", "store-init")
    check_store_identity(_settings(tmp_path, "STORE_A"), db)
    marker = json.loads((tmp_path / "uploads" / ".store_identity").read_text())
    assert marker == {"store_code": "STORE_A", "instance_id": instance}
    check_store_identity(_settings(tmp_path, "store_a"), db)  # case-insensitive code


def test_same_code_missing_marker_with_files_refuses_unless_adopting(db, tmp_path):
    stamp_database(db, "STORE_A", "store-init")
    (tmp_path / "uploads" / "invoices").mkdir(parents=True)
    with pytest.raises(StoreIdentityError, match="no store identity marker"):
        check_store_identity(_settings(tmp_path, "STORE_A"), db)
    check_store_identity(_settings(tmp_path, "STORE_A", adopt=True), db)
    assert (tmp_path / "uploads" / ".store_identity").is_file()


def test_other_code_refuses(db, tmp_path):
    stamp_database(db, "STORE_A", "store-init")
    with pytest.raises(StoreIdentityError, match="This database belongs to STORE_A, not STORE_B"):
        check_store_identity(_settings(tmp_path, "STORE_B"), db)
    with pytest.raises(StoreIdentityError):
        check_store_identity(_settings(tmp_path, "STORE_B", adopt=True), db)


def test_marker_of_another_store_or_instance_refuses(db, tmp_path):
    instance = stamp_database(db, "STORE_A", "store-init")
    root = tmp_path / "uploads"
    identity.write_uploads_marker(root, "STORE_B", instance)
    with pytest.raises(StoreIdentityError, match="belongs to STORE_B"):
        check_store_identity(_settings(tmp_path, "STORE_A"), db)
    identity.write_uploads_marker(root, "STORE_A", "0" * 32)
    with pytest.raises(StoreIdentityError, match="instance"):
        check_store_identity(_settings(tmp_path, "STORE_A"), db)
    identity.write_uploads_marker(root, "STORE_A", instance)
    check_store_identity(_settings(tmp_path, "STORE_A"), db)


def test_adopt_refuses_another_stores_upload_folder(db, tmp_path):
    identity.write_uploads_marker(tmp_path / "uploads", "STORE_B", "1" * 32)
    with pytest.raises(StoreIdentityError, match="belongs to STORE_B"):
        check_store_identity(_settings(tmp_path, "STORE_A", adopt=True), db)
    assert read_stamp(db) is None


def test_unreadable_marker_refuses(db, tmp_path):
    stamp_database(db, "STORE_A", "store-init")
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / ".store_identity").write_text("{not json")
    with pytest.raises(StoreIdentityError, match="not readable JSON"):
        check_store_identity(_settings(tmp_path, "STORE_A"), db)


def test_stamp_is_one_row_and_never_changes_code(db):
    first = stamp_database(db, "STORE_A", "store-init")
    assert stamp_database(db, "store_a", "store-init") == first
    with pytest.raises(StoreIdentityError):
        stamp_database(db, "STORE_B", "store-init")
    from sqlalchemy import text

    assert db.execute(text("SELECT count(*) FROM store_identity")).scalar() == 1


def test_lifespan_runs_the_check_before_the_schedulers(monkeypatch):
    """main.lifespan calls check_store_identity first: a refusal means no job
    ever starts."""
    import asyncio

    from app import main

    calls = []
    monkeypatch.setattr(main, "check_store_identity", lambda: (_ for _ in ()).throw(StoreIdentityError("no")))
    monkeypatch.setattr(main, "start_scheduler", lambda: calls.append("scheduler"))

    async def run():
        async with main.lifespan(main.app):
            pass

    with pytest.raises(StoreIdentityError):
        asyncio.run(run())
    assert calls == []
