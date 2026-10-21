"""Encrypted off-site backup copy (scripts/backup/offsite_copy.py,
pre-production 2026-10-21)."""

import datetime as dt
import io
import os
import pathlib
import subprocess
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "scripts" / "backup"))

import offsite_copy as oc  # noqa: E402


def _backups(tmp_path):
    dest = tmp_path / "backups"
    for folder, names in {"_platform": ["PLATFORM-1.dump", "PLATFORM-2.dump"], "ALPHA": ["ALPHA-1.dump"]}.items():
        (dest / folder).mkdir(parents=True)
        for i, n in enumerate(names):
            p = dest / folder / n
            p.write_bytes(f"{folder}-{n}".encode() * 1000)
            os.utime(p, (1_700_000_000 + i, 1_700_000_000 + i))
    up = tmp_path / "uploads" / "ALPHA"
    up.mkdir(parents=True)
    (up / "invoice.pdf").write_bytes(b"%PDF sample")
    return dest


def test_copy_then_open_gives_back_the_newest_dumps_and_uploads(tmp_path):
    dest = _backups(tmp_path)
    key = tmp_path / "secrets" / "offsite.key"
    out = oc.copy(str(dest), str(tmp_path / "icloud"), str(key), uploads=str(tmp_path / "uploads"))
    assert out.suffix == ".krbak" and oc.MAGIC == out.read_bytes()[: len(oc.MAGIC)]
    assert (key.stat().st_mode & 0o777) == 0o600
    assert b"PLATFORM-2.dump" not in out.read_bytes()  # encrypted, names too
    opened = oc.open_copy(str(out), str(key), str(tmp_path / "restore"))
    assert (opened / "dumps" / "_platform" / "PLATFORM-2.dump").read_bytes() == (dest / "_platform" / "PLATFORM-2.dump").read_bytes()
    assert not (opened / "dumps" / "_platform" / "PLATFORM-1.dump").exists()
    assert (opened / "dumps" / "ALPHA" / "ALPHA-1.dump").is_file()
    assert (opened / "uploads" / "ALPHA" / "invoice.pdf").read_bytes() == b"%PDF sample"


def test_wrong_key_cut_or_edited_file_never_opens(tmp_path):
    dest = _backups(tmp_path)
    key = tmp_path / "k1"
    out = oc.copy(str(dest), str(tmp_path / "off"), str(key))
    other = tmp_path / "k2"
    oc.load_or_create_key(other)
    with pytest.raises(oc.OffsiteError, match="wrong key"):
        oc.open_copy(str(out), str(other), str(tmp_path / "r1"))
    data = out.read_bytes()
    cut = tmp_path / "cut.krbak"
    cut.write_bytes(data[:-10])
    with pytest.raises(oc.OffsiteError):
        oc.open_copy(str(cut), str(key), str(tmp_path / "r2"))
    edited = tmp_path / "edited.krbak"
    edited.write_bytes(data[:40] + bytes([data[40] ^ 1]) + data[41:])
    with pytest.raises(oc.OffsiteError):
        oc.open_copy(str(edited), str(key), str(tmp_path / "r3"))


def test_multi_chunk_round_trip_and_dropped_last_chunk(monkeypatch):
    monkeypatch.setattr(oc, "CHUNK", 10)
    key = b"k" * 32
    enc = io.BytesIO()
    oc.encrypt_stream(io.BytesIO(b"x" * 35), enc, key)
    plain = io.BytesIO()
    oc.decrypt_stream(io.BytesIO(enc.getvalue()), plain, key)
    assert plain.getvalue() == b"x" * 35
    # Drop the last chunk (4 chunks of 10/10/10/5 bytes): the 3rd isn't marked last.
    raw = enc.getvalue()
    last_len = 4 + 12 + 5 + 16
    with pytest.raises(oc.OffsiteError):
        oc.decrypt_stream(io.BytesIO(raw[:-last_len]), io.BytesIO(), key)


def test_keeps_only_the_newest_copies(tmp_path):
    dest = _backups(tmp_path)
    key = tmp_path / "k"
    for day in range(4):
        oc.copy(str(dest), str(tmp_path / "off"), str(key), keep=2, now=dt.datetime(2026, 10, 1 + day, 3, 30))
    names = sorted(p.name for p in (tmp_path / "off").iterdir())
    assert names == ["retailops-20261003-033000.krbak", "retailops-20261004-033000.krbak"]


def test_no_dumps_is_a_clear_error(tmp_path):
    (tmp_path / "b").mkdir()
    rc = subprocess.run([sys.executable, str(BACKEND / "scripts/backup/offsite_copy.py"), "copy", "--dest",
                         str(tmp_path / "b"), "--to", str(tmp_path / "o"), "--key-file", str(tmp_path / "k")],
                        capture_output=True, text=True)
    assert rc.returncode == 1 and "run the nightly backup first" in rc.stderr


def test_run_backup_makes_the_offsite_copy_only_when_configured():
    text = (BACKEND.parent / "deploy" / "pilot-mac" / "run-backup.sh").read_text()
    assert "BACKUP_OFFSITE_DIR" in text and "offsite_copy.py" in text
    assert "secrets/offsite-backup.key" in text
