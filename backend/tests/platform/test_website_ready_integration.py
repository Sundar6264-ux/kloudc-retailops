"""Website ready, Integrate: the four tracks working together on scratch
PostgreSQL (docs/WEBSITE_READY_CONTRACT.md section 5).

- Every job module the workers load exists (a missing one would be skipped
  silently), and every job kind B and D register is known after loading.
- Export all data (B) runs through the job queue (C3) as a worker job and
  writes its ZIP to object storage (C1, moto's in-process S3, no network);
  the server link streams it back with the manifest's checksum.
- The console Stores list flag (A) reads B's usage summary.
"""

import hashlib
import importlib
import io
import json
import zipfile

import pytest
from sqlalchemy import text

from tests.platform.conftest import provision, store_login

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

ENDPOINT = "https://acct.r2.cloudflarestorage.com"
BUCKET = "retailops-integrate"


@pytest.fixture(autouse=True)
def clean_tables(platform_env):
    from app.platform import usage
    from app.platform.db import platform_engine

    with platform_engine().begin() as c:
        c.execute(text("TRUNCATE jobs, job_workers, store_usage_daily, store_plans, store_exports, "
                       "store_offboarding RESTART IDENTITY"))
    usage._reset_for_tests()
    yield
    usage._reset_for_tests()


@pytest.fixture
def s3(monkeypatch):
    monkeypatch.setenv("MOTO_S3_CUSTOM_ENDPOINTS", ENDPOINT)
    for k in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    for k, v in {
        "STORAGE_BACKEND": "s3",
        "STORAGE_S3_ENDPOINT": ENDPOINT,
        "STORAGE_S3_BUCKET": BUCKET,
        "STORAGE_S3_ACCESS_KEY_ID": "test-key-id",
        "STORAGE_S3_SECRET_ACCESS_KEY": "test-secret",
        "STORAGE_S3_REGION": "auto",
        "STORAGE_PRESIGN": "false",  # the server streams: the link below is ours
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


def _bearer(client, token):
    c = client.__class__(client.app)
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


def test_every_job_module_exists_and_every_kind_is_known():
    from app import jobqueue
    from app.jobqueue import kinds

    for name in kinds.JOB_MODULES:
        importlib.import_module(name)  # none may be skipped as "not in this build"
    kinds.load_all()
    for kind in ("store_job", "jobqueue_maintenance", "store_export", "offboarding_daily", "usage_gauges",
                 "clover_webhook_sync"):
        assert jobqueue.handler_for(kind) is not None, kind
    periodic = set(jobqueue.periodic_jobs())
    assert {"offboarding_daily", "usage_gauges", "jobqueue_maintenance"} <= periodic


def test_export_all_data_runs_as_a_queued_job_onto_object_storage(client, platform_env, s3, monkeypatch):
    monkeypatch.setenv("JOB_QUEUE_MODE", "queue")
    from app import jobqueue
    from app.jobqueue import queue
    from app.jobqueue.worker import Worker
    from app.platform.export import list_exports
    from app.storage import get_storage

    assert jobqueue.mode() == "queue"
    provision("ALPHA")
    get_storage().put_bytes("team/2026/photo.jpg", b"\xff\xd8jpeg", code="ALPHA")
    r = store_login(client, "ALPHA", "ann", "alpha-owner-pass")
    assert r.status_code == 200, r.text
    admin = _bearer(client, r.json()["token"])

    r = admin.post("/api/settings/exports")
    assert r.status_code == 202, r.text
    export_id = r.json()["id"]
    jobs = queue.list_jobs()
    assert [(j["kind"], j["status"]) for j in jobs] == [("store_export", "queued")]
    assert list_exports("ALPHA")[0]["status"] in ("queued", "running")

    w = Worker(concurrency=1, worker_id="w-integrate")
    queue.register_worker(w.id, "host", 1, None, 1)
    assert w.run_once() is True
    done = {e["id"]: e for e in list_exports("ALPHA")}[export_id]
    assert done["status"] == "ready", done
    assert queue.get_job(jobs[0]["id"])["status"] == "done"

    keys = [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET).get("Contents", [])]
    assert any(k.startswith("stores/ALPHA/exports/") and k.endswith(".zip") for k in keys), keys

    link = admin.post(f"/api/settings/exports/{export_id}/link")
    assert link.status_code == 200, link.text
    url = link.json()["url"]
    assert url.startswith(f"/api/settings/exports/{export_id}/download?token=")
    got = client.__class__(client.app).get(url)
    assert got.status_code == 200, got.text
    assert hashlib.sha256(got.content).hexdigest() == done["sha256"]
    zf = zipfile.ZipFile(io.BytesIO(got.content))
    assert "files/team/2026/photo.jpg" in zf.namelist()
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["store_id"] == "ALPHA"


def test_usage_summary_feeds_the_stores_list_flag(master_client, platform_env, monkeypatch):
    from app.platform import plans

    provision("ALPHA")
    r = master_client.get("/api/platform/usage/summary")
    assert r.status_code == 200, r.text
    row = next(x for x in r.json() if x["store_code"] == "ALPHA")
    # the fields the console reads (platformModel.usageByStore / usageChip)
    assert set(row) >= {"store_code", "plan", "plan_label", "state"}
    assert row["state"] in ("ok", "near", "over")
    assert row["plan"] in plans.PLAN_IDS
