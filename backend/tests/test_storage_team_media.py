"""Team media on object storage (docs/WEBSITE_READY_CONTRACT.md C2): with
STORAGE_BACKEND=s3 (moto, in process) an upload becomes the store's object
team/<path>, is served through the same URL with the same headers, and is
deleted from the bucket; nothing is written to the local folder. The local
backend keeps today's files (tests/test_team_core.py, unchanged)."""

import io
import pathlib

import pytest
from PIL import Image

from app.config import get_settings
from app.team.models import TeamMedia

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

ENDPOINT = "https://acct.r2.cloudflarestorage.com"
BUCKET = "retailops-media-test"


@pytest.fixture
def s3(monkeypatch):
    monkeypatch.setenv("MOTO_S3_CUSTOM_ENDPOINTS", ENDPOINT)
    for k, v in {
        "STORAGE_BACKEND": "s3",
        "STORAGE_S3_ENDPOINT": ENDPOINT,
        "STORAGE_S3_BUCKET": BUCKET,
        "STORAGE_S3_ACCESS_KEY_ID": "k",
        "STORAGE_S3_SECRET_ACCESS_KEY": "s",
    }.items():
        monkeypatch.setenv(k, v)
    with moto.mock_aws():
        client = boto3.client("s3", endpoint_url=ENDPOINT, aws_access_key_id="x", aws_secret_access_key="y",
                              region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        from app import storage

        storage._backend_cache.update(key=None, storage=None)
        yield client
        storage._backend_cache.update(key=None, storage=None)


def _jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 30, 30)).save(buf, "JPEG")
    return buf.getvalue()


def _objects(client):
    return sorted(o["Key"] for o in client.list_objects_v2(Bucket=BUCKET).get("Contents", []))


def test_photo_round_trip_through_the_bucket(db, staff_client, admin_client, staff2_client, s3):
    from app.team import media as media_svc
    from app.tenancy.registry import single_store_code

    r = staff_client.post("/api/team/media", files={"file": ("p.jpg", _jpeg(), "image/jpeg")})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    media = db.query(TeamMedia).one()
    key = f"stores/{single_store_code()}/team/{media.path}"
    assert _objects(s3) == [key]
    assert not (pathlib.Path(get_settings().team_uploads_dir) / media.path).exists(), "nothing written locally"
    assert s3.head_object(Bucket=BUCKET, Key=key)["ContentType"] == "image/jpeg"

    got = staff_client.get(f"/api/team/media/{token}")
    assert got.status_code == 200
    assert got.headers["content-type"] == "image/jpeg"
    assert got.headers["cache-control"] == "private, max-age=86400"
    assert got.headers["x-content-type-options"] == "nosniff"
    with Image.open(io.BytesIO(got.content)) as img:
        assert img.size == (64, 48)
    assert admin_client.get(f"/api/team/media/{token}").status_code == 200
    assert staff2_client.get(f"/api/team/media/{token}").status_code == 404  # access rules unchanged

    media_svc.delete_media_file(media)
    assert _objects(s3) == []
    assert staff_client.get(f"/api/team/media/{token}").status_code == 404


def test_missing_object_is_a_404(db, staff_client, s3):
    r = staff_client.post("/api/team/media", files={"file": ("p.jpg", _jpeg(), "image/jpeg")})
    token = r.json()["token"]
    s3.delete_object(Bucket=BUCKET, Key=_objects(s3)[0])
    assert staff_client.get(f"/api/team/media/{token}").status_code == 404


def test_storage_outage_is_a_503_not_a_crash(db, staff_client, s3, monkeypatch):
    from app.storage import StorageError, get_storage

    r = staff_client.post("/api/team/media", files={"file": ("p.jpg", _jpeg(), "image/jpeg")})
    token = r.json()["token"]
    st = get_storage()

    def down(*_a, **_k):
        raise StorageError("object storage: ServiceUnavailable")

    monkeypatch.setattr(st, "open", down)
    assert staff_client.get(f"/api/team/media/{token}").status_code == 503


def test_failed_commit_removes_the_object(db, staff_client, s3, monkeypatch):
    from sqlalchemy.orm import Session

    real_commit = Session.commit

    def boom(self):
        if any(isinstance(o, TeamMedia) for o in self.new):
            raise RuntimeError("database down")
        return real_commit(self)

    monkeypatch.setattr(Session, "commit", boom)
    with pytest.raises(RuntimeError):
        staff_client.post("/api/team/media", files={"file": ("p.jpg", _jpeg(), "image/jpeg")})
    assert _objects(s3) == []
