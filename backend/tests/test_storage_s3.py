"""S3 / R2 object storage (docs/WEBSITE_READY_CONTRACT.md C1) against moto's
in-process S3 (no network), plus the local -> S3 migration tool."""

import hashlib
import os
import urllib.parse

import pytest

import app.config as app_config
from app.config import get_settings

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

ENDPOINT = "https://acct.r2.cloudflarestorage.com"
BUCKET = "retailops-test"


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    settings = get_settings().model_copy(update={"uploads_root": str(tmp_path / "uploads")})
    monkeypatch.setattr(app_config, "get_settings", lambda: settings)
    return tmp_path / "uploads"


@pytest.fixture
def s3env(monkeypatch):
    monkeypatch.setenv("MOTO_S3_CUSTOM_ENDPOINTS", ENDPOINT)
    for k in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    values = {
        "STORAGE_BACKEND": "s3",
        "STORAGE_S3_ENDPOINT": ENDPOINT,
        "STORAGE_S3_BUCKET": BUCKET,
        "STORAGE_S3_ACCESS_KEY_ID": "test-key-id",
        "STORAGE_S3_SECRET_ACCESS_KEY": "test-secret",
        "STORAGE_S3_REGION": "auto",
    }
    for k, v in values.items():
        monkeypatch.setenv(k, v)
    with moto.mock_aws():
        client = boto3.client("s3", endpoint_url=ENDPOINT, aws_access_key_id="x", aws_secret_access_key="y",
                              region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        from app import storage

        storage._backend_cache.update(key=None, storage=None)
        yield client
        storage._backend_cache.update(key=None, storage=None)


def _keys(client):
    return sorted(o["Key"] for o in client.list_objects_v2(Bucket=BUCKET).get("Contents", []))


def test_every_i1_method_on_s3(s3env, uploads, tmp_path):
    from app.storage import ObjectNotFound, StorageError, get_storage

    st = get_storage()
    assert st.backend == "s3"
    obj = st.put_bytes("exports/a.txt", b"hello", content_type="text/plain")
    assert obj.key == "exports/a.txt" and obj.size == 5
    src = tmp_path / "src.bin"
    src.write_bytes(b"12345678")
    st.put_file("invoices/2026/b.bin", src)
    # one bucket, keys under stores/<CODE>/ (single mode: the process store code)
    from app.tenancy.registry import single_store_code

    code = single_store_code()
    assert _keys(s3env) == [f"stores/{code}/exports/a.txt", f"stores/{code}/invoices/2026/b.bin"]
    head = s3env.head_object(Bucket=BUCKET, Key=f"stores/{code}/invoices/2026/b.bin")
    assert head["Metadata"]["sha256"] == hashlib.sha256(b"12345678").hexdigest()
    assert st.read_bytes("invoices/2026/b.bin") == b"12345678"
    with st.open("exports/a.txt") as fh:
        assert fh.read(2) == b"he" and fh.read() == b"llo"
    assert st.exists("exports/a.txt") and not st.exists("exports/missing.txt")
    assert st.stat("exports/a.txt").size == 5 and st.stat("nope.txt") is None
    assert [o.key for o in st.list()] == ["exports/a.txt", "invoices/2026/b.bin"]
    assert [o.key for o in st.list("invoices")] == ["invoices/2026/b.bin"]
    assert st.bytes_used() == 13 and st.bytes_used("invoices") == 8
    assert st.local_path("exports/a.txt") is None
    url = st.presigned_url("exports/a.txt", expires_seconds=120, filename='Data "export".zip')
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert url.startswith(ENDPOINT + "/" + BUCKET + "/stores/")
    assert q["X-Amz-Expires"] == ["120"]
    assert q["response-content-disposition"] == ['attachment; filename="Data _export_.zip"']
    assert st.delete_prefix("invoices") == 1 and st.delete("exports/a.txt")
    assert not st.delete("exports/a.txt")
    assert _keys(s3env) == []
    with pytest.raises(ObjectNotFound):
        st.open("exports/a.txt")
    with pytest.raises(ObjectNotFound):
        st.read_bytes("exports/a.txt")
    for bad in ("/etc/passwd", "../x", "a/../b", "a//b", "a\\b", ""):
        with pytest.raises(StorageError):
            st.put_bytes(bad, b"x")


def test_delete_prefix_pages_past_1000(s3env, uploads):
    from app.storage import get_storage

    st = get_storage()
    for i in range(1005):
        st.put_bytes(f"team/p{i:04d}.txt", b"x")
    assert st.bytes_used("team") == 1005
    assert st.delete_prefix("team") == 1005
    assert list(st.list()) == []


def test_presign_off_means_server_streams(s3env, monkeypatch):
    from app.storage import get_storage

    monkeypatch.setenv("STORAGE_PRESIGN", "false")
    assert get_storage().presigned_url("exports/a.txt") is None


def test_stores_are_separate_in_multi_mode(s3env, monkeypatch):
    import app.tenancy.mode
    from app.storage import get_storage

    monkeypatch.setattr(app.tenancy.mode, "is_multi_tenant", lambda: True)
    st = get_storage()
    st.put_bytes("files/a.txt", b"aa", code="ALPHA")
    st.put_bytes("files/b.txt", b"bbb", code="BETA")
    assert [o.key for o in st.list(code="ALPHA")] == ["files/a.txt"]
    assert st.bytes_used(code="BETA") == 3
    assert st.delete_prefix("", code="ALPHA") == 1
    assert _keys(s3env) == ["stores/BETA/files/b.txt"]
    with pytest.raises(Exception):
        st.put_bytes("x.txt", b"x")  # no store selected in multi mode


def test_s3_refused_without_its_settings(monkeypatch):
    from app.storage import StorageError, check_storage_config, storage_status

    monkeypatch.setenv("STORAGE_BACKEND", "local")
    check_storage_config()  # local needs nothing
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    for k in ("STORAGE_S3_ENDPOINT", "STORAGE_S3_BUCKET", "STORAGE_S3_ACCESS_KEY_ID", "STORAGE_S3_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("STORAGE_S3_BUCKET", "b")
    with pytest.raises(StorageError) as err:
        check_storage_config()
    assert "STORAGE_S3_ENDPOINT" in str(err.value) and "STORAGE_S3_SECRET_ACCESS_KEY" in str(err.value)
    status = storage_status()
    assert status["ok"] is False and "STORAGE_S3_ACCESS_KEY_ID" in status["problem"]
    monkeypatch.setenv("STORAGE_BACKEND", "ftp")
    with pytest.raises(StorageError):
        check_storage_config()


def test_status_never_returns_keys(s3env):
    from app.storage import storage_status

    out = storage_status()
    assert out == {"backend": "s3", "ok": True, "problem": None, "bucket": BUCKET,
                   "endpoint_host": "acct.r2.cloudflarestorage.com", "presign": True, "keys_set": True}
    assert "test-secret" not in str(out) and "test-key-id" not in str(out)


def test_migrate_dry_run_copy_verify_and_rerun(s3env, uploads, monkeypatch):
    from app.storage import LocalStorage
    from app.storage.migrate import migrate, store_codes

    monkeypatch.setenv("STORAGE_BACKEND", "local")  # the server still runs on local files
    local = LocalStorage()
    local.put_bytes("invoices/2026/a.pdf", b"%PDF-1" * 100)
    local.put_bytes("flyers/f.png", b"\x89PNG" * 10)
    (uploads / ".keys").mkdir()
    (uploads / ".keys" / "ring").write_bytes(b"secret key")
    before = {p: p.read_bytes() for p in uploads.rglob("*") if p.is_file()}
    codes = store_codes()

    dry = migrate(codes, dry_run=True)
    assert dry["totals"]["would_copy"] == 2 and dry["totals"]["copied"] == 0 and _keys(s3env) == []

    first = migrate(codes, verify=True)
    assert first["ok"] and first["totals"]["copied"] == 2 and first["totals"]["verified"] == 2
    keys = _keys(s3env)
    assert len(keys) == 2 and not any(".keys" in k for k in keys)  # hidden files never leave the server

    again = migrate(codes, verify=True)  # re-run: nothing copied twice
    assert again["ok"] and again["totals"]["copied"] == 0 and again["totals"]["skipped"] == 2

    # A changed local file (or a half-written object) is copied again.
    local.put_bytes("flyers/f.png", b"changed")
    third = migrate(codes)
    assert third["totals"]["copied"] == 1 and third["totals"]["skipped"] == 1

    # verify-only finds a mismatch without writing
    s3env.put_object(Bucket=BUCKET, Key=keys[0], Body=b"tampered")
    check = migrate(codes, dry_run=True, verify=True)
    assert not check["ok"] and len(check["stores"][0]["mismatched"]) == 1

    # Local files are never changed by the tool.
    after = {p: p.read_bytes() for p in uploads.rglob("*") if p.is_file()}
    assert set(after) == set(before) and after[uploads / ".keys" / "ring"] == b"secret key"


def test_local_backend_layout_unchanged(uploads, monkeypatch):
    from app.storage import get_storage

    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    st = get_storage()
    assert st.backend == "local"
    st.put_bytes("team/x.txt", b"x")
    assert (uploads / "team" / "x.txt").read_bytes() == b"x"
    assert os.path.isfile(st.local_path("team/x.txt"))
