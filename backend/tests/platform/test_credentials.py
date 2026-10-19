"""Credentials vault (contract 8.3, 8.4; attack 13)."""

import base64
import json
import os
import stat

import pytest
from sqlalchemy import select

from app.platform import credentials as creds
from app.platform import crypto
from app.platform.models import AuditLog, Store, StoreCredential, utcnow

SECRET = "tst-secret-value-123456"


def _store(pdb, code="ALPHA"):
    s = Store(code=code, name=code.title(), status="active", hosting="shared", db_name="so_x_" + code.lower(),
              timezone="America/Chicago", pos_provider="toast", pack_id="generic", created_by="test",
              created_at=utcnow(), updated_at=utcnow())
    pdb.add(s)
    pdb.commit()
    return s


def test_key_ring_parsing_and_errors(platform_env, monkeypatch):
    ring = crypto.load_keyring()
    assert ring.ids() == ["t2", "t1"] and ring.active_id == "t2" and ring.source == "env"
    for bad in ("nokey", "k1:" + base64.b64encode(b"short").decode(), "k1:AAAA,k1:AAAA", "k 1:" + "A" * 44):
        with pytest.raises(crypto.KeyRingError):
            crypto.parse_ring(bad)
    entry = crypto.gen_key("k9")
    kid, key = crypto.parse_ring(entry)[0]
    assert kid == "k9" and len(key) == 32
    monkeypatch.setattr(platform_env, "retailops_secret_keys", "")
    monkeypatch.setattr(platform_env, "retailops_secret_key", "")
    monkeypatch.setattr(platform_env, "app_env", "production")
    with pytest.raises(crypto.KeyRingError):
        crypto.load_keyring()
    from app.platform.startup import PlatformStartupError, startup_checks

    crypto.reset_keyring()
    with pytest.raises(PlatformStartupError):
        startup_checks()


def test_dev_key_ring_file_is_private(platform_env, monkeypatch, tmp_path):
    monkeypatch.setattr(platform_env, "retailops_secret_keys", "")
    monkeypatch.setattr(platform_env, "app_env", "development")
    monkeypatch.setattr(platform_env, "retailops_secret_key_file", str(tmp_path / ".secrets" / "ring"))
    ring = crypto.load_keyring()
    assert ring.source == "dev-file" and ring.active_id == "dev1"
    path = tmp_path / ".secrets" / "ring"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert (tmp_path / ".secrets" / ".gitignore").read_text() == "*\n"
    assert crypto.load_keyring().keys == ring.keys  # reused, not regenerated


def test_save_view_and_no_plaintext_at_rest(pdb):
    _store(pdb)
    view = creds.save_credential("ALPHA", "toast", {"client_id": "cid", "restaurant_guid": "guid-1"},
                                 {"client_secret": SECRET}, actor="store:ALPHA:ann")
    assert view.configured and view.status == "untested" and view.last4 == SECRET[-4:]
    assert view.public == {"client_id": "cid", "restaurant_guid": "guid-1", "api_base": "https://ws-api.toasttab.com",
                           "analytics_client_id": ""}
    row = pdb.execute(select(StoreCredential)).scalar_one()
    blob = json.dumps({c.name: str(getattr(row, c.name)) for c in StoreCredential.__table__.columns})
    assert SECRET not in blob and SECRET[:8] not in blob
    assert row.key_id == "t2"
    assert SECRET not in json.dumps(view.as_dict())
    assert creds.decrypt_secrets("ALPHA", "toast") == {"client_secret": SECRET}
    over = creds.settings_overrides("ALPHA")
    assert over["toast_client_secret"] == SECRET and over["toast_client_id"] == "cid"
    assert over["toast_api_base"] == "https://ws-api.toasttab.com"
    audit = pdb.execute(select(AuditLog).where(AuditLog.action == "credential.saved")).scalar_one()
    assert SECRET not in audit.details and "client_secret" in audit.details  # the NAME only


def test_blank_secret_keeps_old_and_clear_removes(pdb):
    _store(pdb)
    creds.save_credential("ALPHA", "toast", {"client_id": "cid", "restaurant_guid": "g"},
                          {"client_secret": SECRET, "analytics_client_secret": "an-secret-999"}, actor="a")
    v = creds.save_credential("ALPHA", "toast", {"client_id": "cid2"}, {"client_secret": "  "}, actor="a")
    assert v.public["client_id"] == "cid2" and v.public["restaurant_guid"] == "g"
    assert creds.decrypt_secrets("ALPHA", "toast") == {"client_secret": SECRET, "analytics_client_secret": "an-secret-999"}
    creds.save_credential("ALPHA", "toast", {}, {}, clear=["analytics_client_secret"], actor="a")
    assert creds.decrypt_secrets("ALPHA", "toast") == {"client_secret": SECRET}
    with pytest.raises(creds.CredentialError):
        creds.save_credential("ALPHA", "toast", {}, {}, clear=["client_secret"], actor="a")  # primary required
    with pytest.raises(creds.CredentialError):
        creds.save_credential("ALPHA", "toast", {"nope": 1}, {}, actor="a")
    with pytest.raises(creds.CredentialError):
        creds.save_credential("ALPHA", "smtp", {"host": "h", "username": "u", "port": 99999}, {"password": "p"}, actor="a")
    with pytest.raises(creds.CredentialError):
        creds.save_credential("ALPHA", "whatsapp", {"base_url": "http://plain"}, {"api_token": "tok-123456"}, actor="a")


def test_all_kinds_map_to_settings(pdb):
    _store(pdb)
    creds.save_credential("ALPHA", "whatsapp", {"group_chat_ids": {"store": "1@g.us"}}, {"api_token": "wa-token-123"}, actor="a")
    creds.save_credential("ALPHA", "smtp", {"host": "smtp.x.test", "port": "587", "ssl": "false", "username": "u@x.test",
                                            "from_name": "Alpha"}, {"password": "smtp-pass-1"}, actor="a")
    creds.save_credential("ALPHA", "ai", {}, {"api_key": "sk-test-abcdef"}, actor="a")
    over = creds.settings_overrides("ALPHA")
    assert over["whapi_api_token"] == "wa-token-123"
    assert json.loads(over["whatsapp_group_chat_ids"]) == {"store": "1@g.us"}
    assert over["smtp_port"] == 587 and over["smtp_ssl"] is False and over["smtp_password"] == "smtp-pass-1"
    assert over["anthropic_api_key"] == "sk-test-abcdef"
    from app.tenancy.settings import CREDENTIAL_SETTING_KEYS

    assert set(over) <= CREDENTIAL_SETTING_KEYS
    kinds = [v.kind for v in creds.list_credentials("ALPHA")]
    assert kinds == ["toast", "clover", "whatsapp", "smtp", "ai"]


def test_copied_ciphertext_does_not_decrypt_in_another_store(pdb):
    """Attack 13: B's row pasted into A's does not open (AAD binds store+kind)."""
    a, b = _store(pdb, "ALPHA"), _store(pdb, "BRAVO")
    creds.save_credential("BRAVO", "ai", {}, {"api_key": "bravo-key-0001"}, actor="a")
    creds.save_credential("ALPHA", "ai", {}, {"api_key": "alpha-key-0002"}, actor="a")
    rows = {r.store_id: r for r in pdb.execute(select(StoreCredential)).scalars()}
    rows[a.id].ciphertext, rows[a.id].nonce, rows[a.id].key_id = rows[b.id].ciphertext, rows[b.id].nonce, rows[b.id].key_id
    pdb.commit()
    assert creds.decrypt_secrets("ALPHA", "ai") == {}
    view = creds.credential_view("ALPHA", "ai")
    assert view.configured is False and view.last4 is None
    assert "anthropic_api_key" not in creds.settings_overrides("ALPHA")
    assert pdb.execute(select(AuditLog).where(AuditLog.action == "credential.decrypt_failed")).scalars().first()
    # Same store, other kind: no either.
    with pytest.raises(crypto.DecryptError):
        crypto.decrypt(rows[b.id].key_id, rows[b.id].nonce, rows[b.id].ciphertext, crypto.aad_for("BRAVO", "toast"))


def test_key_rotation(pdb, platform_env, monkeypatch):
    _store(pdb)
    monkeypatch.setattr(platform_env, "retailops_secret_keys", "t1:" + "QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE=")
    crypto.reset_keyring()
    creds.save_credential("ALPHA", "ai", {}, {"api_key": "rot-key-12345"}, actor="a")
    assert pdb.execute(select(StoreCredential.key_id)).scalar() == "t1"
    new = crypto.gen_key("t3")
    monkeypatch.setattr(platform_env, "retailops_secret_keys", new + ",t1:" + "QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE=")
    counts = creds.rotate_all()
    assert counts["credentials"] == 1
    pdb.expire_all()
    assert pdb.execute(select(StoreCredential.key_id)).scalar() == "t3"
    # Old key gone from the ring: still opens (re-encrypted with t3).
    monkeypatch.setattr(platform_env, "retailops_secret_keys", new)
    crypto.reset_keyring()
    assert creds.decrypt_secrets("ALPHA", "ai") == {"api_key": "rot-key-12345"}


def test_delete_and_version_bump(pdb):
    s = _store(pdb)
    v0 = s.settings_version
    creds.save_credential("ALPHA", "ai", {}, {"api_key": "x-key-12345"}, actor="a")
    assert creds.delete_credential("ALPHA", "ai", actor="a") is True
    assert creds.delete_credential("ALPHA", "ai", actor="a") is False
    pdb.expire_all()
    assert pdb.get(Store, s.id).settings_version == v0 + 2
    assert [r.action for r in pdb.execute(select(AuditLog).order_by(AuditLog.id)).scalars()] == ["credential.saved", "credential.deleted"]
