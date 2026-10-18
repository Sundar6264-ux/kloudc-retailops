"""python -m app.platform commands (contract 10, 11.3)."""

from sqlalchemy import select

from app.platform.__main__ import main, migrate_all
from app.platform.models import AuditLog, PlatformUser, Store
from tests.platform.conftest import provision


def test_create_master_from_env(monkeypatch, pdb, capsys):
    monkeypatch.setenv("STOREOPS_MASTER_PASSWORD", "cli-master-pass-1")
    assert main(["create-master", "--username", "Boss", "--display-name", "The Boss"]) == 0
    user = pdb.execute(select(PlatformUser)).scalar_one()
    assert user.username == "boss" and user.password_hash.startswith("$2")
    monkeypatch.setenv("STOREOPS_MASTER_PASSWORD", "short")
    try:
        main(["create-master", "--username", "other"])
    except SystemExit as exc:
        assert "at least" in str(exc)


def test_provision_list_suspend_resume(monkeypatch, pdb, capsys):
    monkeypatch.setenv("STOREOPS_OWNER_PASSWORD", "cli-owner-pass-1")
    assert main(["provision", "--code", "echo", "--name", "Echo Mart", "--pack", "test_toast", "--tz", "UTC",
                 "--owner-username", "eli"]) == 0
    assert pdb.execute(select(Store.status).where(Store.code == "ECHO")).scalar() == "active"
    assert main(["suspend", "ECHO", "--reason", "test"]) == 0
    assert main(["resume", "echo"]) == 0
    assert main(["purge-store", "ECHO", "--confirm", "WRONG"]) == 1
    assert main(["purge-store", "ECHO", "--confirm", "ECHO"]) == 1  # active: refused
    capsys.readouterr()
    assert main(["list-stores"]) == 0
    assert '"code": "ECHO"' in capsys.readouterr().out


def test_migrate_all(pdb):
    provision("ALPHA")
    seen = []

    def fake(url):
        seen.append(url)
        return "0062"

    out = migrate_all(migrator=fake)
    assert out["ok"] and out["stores"] == {"ALPHA": "0062"} and len(seen) == 1
    assert migrate_all(dry_run=True, migrator=fake)["stores"] == {"ALPHA": "would migrate"}

    def broken(url):
        raise RuntimeError("boom")

    out = migrate_all(migrator=broken)
    assert not out["ok"] and out["stores"]["ALPHA"].startswith("FAILED")
    assert pdb.execute(select(AuditLog).where(AuditLog.action == "migrate_all.run")).scalars().first()


def test_gen_key_prints_a_ring_entry(capsys):
    from app.platform.crypto import parse_ring

    assert main(["gen-key", "--id", "k7"]) == 0
    entry = capsys.readouterr().out.strip()
    assert parse_ring(entry)[0][0] == "k7"
