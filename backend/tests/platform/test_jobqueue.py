"""The PostgreSQL job queue (docs/WEBSITE_READY_CONTRACT.md C3) on the
scratch platform database: claim with SKIP LOCKED, retries with backoff,
dead jobs, dedupe, the reaper, graceful stop, the scheduler hand-off and the
masters' System routes."""

import datetime as dt
import threading
import time

import pytest
from sqlalchemy import text

from tests.platform.conftest import provision


@pytest.fixture(autouse=True)
def clean_queue(platform_env):
    from app.platform.db import platform_engine

    with platform_engine().begin() as c:
        c.execute(text("TRUNCATE jobs, job_workers RESTART IDENTITY"))
    yield


@pytest.fixture
def queue_mode(monkeypatch):
    monkeypatch.setenv("JOB_QUEUE_MODE", "queue")
    from app import jobqueue

    assert jobqueue.mode() == "queue"
    return jobqueue


_calls = []


def _register_kinds():
    from app import jobqueue

    @jobqueue.register("t_ok", max_attempts=3)
    def ok(payload):
        _calls.append(("ok", payload))
        return {"echo": payload.get("n")}

    @jobqueue.register("t_boom", max_attempts=2)
    def boom(payload):
        _calls.append(("boom", payload))
        raise ValueError("no luck")

    return ok, boom


_register_kinds()


def _job(job_id):
    from app.jobqueue import queue

    return queue.get_job(job_id)


def _set(job_id, **cols):
    from app.platform.db import platform_engine

    sets = ", ".join(f"{k} = :{k}" for k in cols)
    with platform_engine().begin() as c:
        c.execute(text(f"UPDATE jobs SET {sets} WHERE id = :id"), {"id": job_id, **cols})


def test_inline_is_the_default_and_unchanged(platform_env, monkeypatch):
    from app import jobqueue

    monkeypatch.delenv("JOB_QUEUE_MODE", raising=False)
    assert jobqueue.mode() == "inline"
    done = threading.Event()

    @jobqueue.register("t_inline")
    def run(payload):
        done.set()

    handle = jobqueue.enqueue("t_inline", {"a": 1})
    assert handle.id is None and handle.status == "started" and done.wait(5)
    from app.jobqueue import queue

    assert queue.list_jobs() == []  # nothing recorded inline
    assert jobqueue.job_status(1) is None


def test_single_mode_is_always_inline(platform_env, monkeypatch):
    from app import jobqueue

    monkeypatch.setenv("JOB_QUEUE_MODE", "queue")
    monkeypatch.setattr(platform_env, "tenancy_mode", "single")
    assert jobqueue.mode() == "inline"


def test_enqueue_status_and_worker_runs_it(queue_mode):
    from app.jobqueue.worker import Worker

    h = queue_mode.enqueue("t_ok", {"n": 7})
    assert h.id and h.status == "queued" and h.store_code is None
    st = queue_mode.job_status(h.id)
    assert st["status"] == "queued" and st["attempts"] == 0 and st["kind"] == "t_ok"
    w = Worker(concurrency=1, worker_id="w-test")
    from app.jobqueue import queue

    queue.register_worker(w.id, "host", 1, None, 1)
    assert w.run_once() is True
    st = queue_mode.job_status(h.id)
    assert st["status"] == "done" and st["attempts"] == 1 and st["result"] == {"echo": 7}
    assert w.run_once() is False
    assert queue.list_workers()[0]["jobs_done"] == 1


def test_two_workers_never_take_the_same_job(queue_mode):
    from app.jobqueue import queue

    ids = {queue_mode.enqueue("t_ok", {"n": i}).id for i in range(60)}
    taken = {"a": [], "b": []}
    barrier = threading.Barrier(2)

    def grab(name):
        barrier.wait()
        while True:
            rows = queue.claim(f"w-{name}", limit=1)
            if not rows:
                return
            taken[name].extend(r["id"] for r in rows)

    threads = [threading.Thread(target=grab, args=(n,)) for n in taken]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    a, b = set(taken["a"]), set(taken["b"])
    assert not (a & b)
    assert a | b == ids and len(taken["a"]) + len(taken["b"]) == 60


def test_priority_then_age_and_run_after(queue_mode):
    from app.jobqueue import queue

    later = queue_mode.enqueue("t_ok", {"n": 1}, run_after=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1))
    low = queue_mode.enqueue("t_ok", {"n": 2})
    high = queue_mode.enqueue("t_ok", {"n": 3}, priority=5)
    order = [queue.claim("w")[0]["id"], queue.claim("w")[0]["id"]]
    assert order == [high.id, low.id]
    assert queue.claim("w") == []  # the future job waits
    assert _job(later.id)["status"] == "queued"
    assert queue.claim("w", kinds=["other_kind"]) == []


def test_retry_with_backoff_then_dead_then_master_retry(queue_mode):
    from app.jobqueue import queue
    from app.jobqueue.worker import Worker

    w = Worker(worker_id="w-fail")
    queue.register_worker(w.id, "host", 1, None, 1)
    h = queue_mode.enqueue("t_boom", {"n": 1})
    t0 = dt.datetime.now(dt.timezone.utc)
    assert w.run_once()
    row = _job(h.id)
    assert row["status"] == "failed" and row["attempts"] == 1 and "no luck" in row["error"]
    wait = (row["run_after"] - t0).total_seconds()
    assert 25 <= wait <= 40  # 30 s backoff after the 1st failure
    assert w.run_once() is False  # not due yet
    _set(h.id, run_after=t0 - dt.timedelta(seconds=1))
    assert w.run_once()
    row = _job(h.id)
    assert row["status"] == "dead" and row["attempts"] == 2  # max_attempts=2
    assert queue.counts()["dead"] == 1
    assert queue.retry(h.id)["status"] == "queued"
    row = _job(h.id)
    assert row["attempts"] == 0 and row["status"] == "queued"
    assert queue.retry(h.id) is None  # only failed / dead jobs


def test_backoff_doubles_and_caps():
    from app.jobqueue.queue import backoff_seconds

    assert [backoff_seconds(n) for n in (1, 2, 3, 4)] == [30, 60, 120, 240]
    assert backoff_seconds(20) == 3600


def test_dedupe_while_open_then_new_after_done(queue_mode):
    from app.jobqueue import queue

    a = queue_mode.enqueue("t_ok", {"n": 1}, dedupe_key="store:ALPHA")
    b = queue_mode.enqueue("t_ok", {"n": 2}, dedupe_key="store:ALPHA")
    other = queue_mode.enqueue("t_ok", {"n": 3}, dedupe_key="store:BETA")
    assert a.id == b.id and other.id != a.id
    claimed = queue.claim("w", limit=5)
    assert {r["id"] for r in claimed} == {a.id, other.id}
    assert queue_mode.enqueue("t_ok", {}, dedupe_key="store:ALPHA").id == a.id  # still running
    assert queue.complete(a.id, "w", 1, {"ok": True})
    c = queue_mode.enqueue("t_ok", {}, dedupe_key="store:ALPHA")
    assert c.id not in (a.id, other.id)
    # A master's retry never makes a second open copy of the same key.
    assert queue.fail(other.id, "w", 1, "x") == "failed"
    _set(other.id, status="dead")
    queue_mode.enqueue("t_ok", {}, dedupe_key="store:BETA")
    assert queue.retry(other.id) is None


def test_reaper_takes_back_a_silent_workers_job(queue_mode):
    from app.jobqueue import queue

    h = queue_mode.enqueue("t_ok", {})
    row = queue.claim("w-dead")[0]
    assert queue.reap_stuck() == []  # fresh heartbeat
    _set(h.id, heartbeat_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5))
    assert queue.reap_stuck() == [{"id": h.id, "status": "failed"}]
    # The dead worker's late answer is dropped; another worker gets the job.
    assert queue.complete(h.id, "w-dead", row["attempts"], {"late": True}) is False
    _set(h.id, run_after=dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1))
    again = queue.claim("w-new")[0]
    assert again["id"] == h.id and again["attempts"] == 2


def test_reaper_fails_a_job_past_its_timeout(queue_mode):
    from app.jobqueue import queue

    h = queue_mode.enqueue("t_ok", {})
    queue.claim("w")
    _set(h.id, started_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=900 + 120))
    assert queue.reap_stuck() == [{"id": h.id, "status": "failed"}]


def test_graceful_stop_puts_unfinished_jobs_back(queue_mode):
    from app import jobqueue
    from app.jobqueue import queue
    from app.jobqueue.worker import Worker

    started, release = threading.Event(), threading.Event()

    @jobqueue.register("t_slow")
    def slow(_payload):
        started.set()
        release.wait(10)

    h = queue_mode.enqueue("t_slow", {})
    w = Worker(concurrency=1, poll_seconds=0.1, grace_seconds=0.3, heartbeat_seconds=0.2)
    w.start()
    assert started.wait(10)
    assert _job(h.id)["status"] == "running"
    w.stop()
    row = _job(h.id)
    assert row["status"] == "queued" and row["attempts"] == 0 and row["locked_by"] is None
    workers = queue.list_workers()
    assert workers[0]["state"] == "stopped"
    release.set()


def test_worker_threads_drain_the_queue(queue_mode):
    from app.jobqueue.worker import Worker

    ids = [queue_mode.enqueue("t_ok", {"n": i}).id for i in range(12)]
    w = Worker(concurrency=3, poll_seconds=0.05, heartbeat_seconds=0.2)
    w.start()
    deadline = time.time() + 15
    while time.time() < deadline and any(_job(i)["status"] != "done" for i in ids):
        time.sleep(0.1)
    w.stop()
    assert all(_job(i)["status"] == "done" for i in ids)
    assert w.done == 12


def test_unknown_kind_in_a_worker_fails_the_job(queue_mode):
    from app.jobqueue import queue
    from app.jobqueue.worker import Worker

    res = queue.insert_job("t_not_here", {}, store_code=None, max_attempts=1)
    w = Worker(worker_id="w")
    queue.register_worker("w", "h", 1, None, 1)
    assert w.run_once()
    row = _job(res["id"])
    assert row["status"] == "dead" and "No handler" in row["error"]


def test_a_scheduled_store_job_lands_in_the_queue_and_runs_in_its_store(queue_mode):
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    from app.jobqueue import queue
    from app.jobqueue.worker import Worker
    from app.tenancy import current_store_code, jobs as tjobs

    provision("ALPHA")
    seen = []

    def probe_job():
        seen.append(current_store_code())

    tjobs.unregister_store_jobs()
    tjobs.register_store_job("probe_job", probe_job, lambda _ref: CronTrigger(hour=3), jitter=False)
    sched = BackgroundScheduler(timezone=dt.timezone.utc)
    tjobs.attach_scheduler("main", sched)
    try:
        stats = tjobs.reconcile_store_jobs()
        assert stats["main"]["added"] == 1
        job = sched.get_job("probe_job:ALPHA")
        job.func()  # the scheduler fires it: in queue mode it only enqueues
        job.func()  # a second fire while the first waits: deduped
        assert seen == []
        rows = queue.list_jobs()
        assert len(rows) == 1 and rows[0]["kind"] == "store_job" and rows[0]["store_code"] == "ALPHA"
        assert _job(rows[0]["id"])["payload"] == {"job_id": "probe_job"}
        w = Worker(worker_id="w")
        queue.register_worker("w", "h", 1, None, 1)
        assert w.run_once()
        assert seen == ["ALPHA"] and _job(rows[0]["id"])["status"] == "done"
    finally:
        tjobs.detach_scheduler("main")
        tjobs.unregister_store_jobs()


def test_inline_mode_scheduler_still_runs_the_job_itself(platform_env, monkeypatch):
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    from app.jobqueue import queue
    from app.tenancy import current_store_code, jobs as tjobs

    monkeypatch.delenv("JOB_QUEUE_MODE", raising=False)
    provision("ALPHA")
    seen = []

    def probe_inline():
        seen.append(current_store_code())

    tjobs.unregister_store_jobs()
    tjobs.register_store_job("probe_inline", probe_inline, lambda _ref: CronTrigger(hour=3), jitter=False)
    sched = BackgroundScheduler(timezone=dt.timezone.utc)
    tjobs.attach_scheduler("main", sched)
    try:
        tjobs.reconcile_store_jobs()
        sched.get_job("probe_inline:ALPHA").func()
        assert seen == ["ALPHA"] and queue.list_jobs() == []
    finally:
        tjobs.detach_scheduler("main")
        tjobs.unregister_store_jobs()


def test_periodic_jobs_are_scheduled_on_the_leader(queue_mode):
    from apscheduler.schedulers.background import BackgroundScheduler

    from app import jobqueue
    from app.jobqueue import queue
    from app.scheduler import add_periodic_jobs
    from app.tenancy import jobs as tjobs

    @jobqueue.register("t_daily_store")
    def daily(_payload):
        return None

    jobqueue.register_periodic("t_daily_store", hour=2, minute=5, per_store=True)
    platform = BackgroundScheduler(timezone=dt.timezone.utc)
    tjobs.unregister_store_jobs()
    try:
        assert add_periodic_jobs(platform) >= 2
        # platform-level: a UTC cron job on the platform scheduler
        maint = platform.get_job("periodic_jobqueue_maintenance")
        assert maint is not None and "hour='4'" in str(maint.trigger)
        # per store: declared as a store job (store time zone) on "platform"
        spec = {s.job_id: s for s in tjobs.store_job_specs("platform")}["periodic_t_daily_store"]
        provision("ALPHA")
        from app.tenancy import store_context

        with store_context("ALPHA"):
            spec.func()
            spec.func()  # same day: deduped
        rows = queue.list_jobs()
        assert [(r["kind"], r["store_code"]) for r in rows] == [("t_daily_store", "ALPHA")]
    finally:
        tjobs.unregister_store_jobs()
        jobqueue._PERIODIC.pop("t_daily_store", None)


# --- masters' System routes -------------------------------------------------------------


def test_system_routes_for_masters_only(master_client, client, queue_mode, pdb):
    from app.jobqueue import queue

    h = queue_mode.enqueue("t_boom", {"n": 1})
    queue.claim("w")
    queue.fail(h.id, "w", 1, "boom")
    _set(h.id, status="dead")

    assert client.get("/api/platform/system/status").status_code in (401, 403)
    assert client.post(f"/api/platform/system/jobs/{h.id}/retry").status_code in (401, 403)

    r = master_client.get("/api/platform/system/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["jobs"]["mode"] == "queue" and body["jobs"]["counts"]["dead"] == 1
    assert body["storage"]["backend"] == "local"
    assert body["live_updates"]["mode"] == "local"
    assert body["database"]["pool_mode"] == "direct"
    assert "postgresql" not in r.text  # no URLs (they can carry passwords)

    r = master_client.get("/api/platform/system/jobs", params={"status": "dead"})
    assert [j["id"] for j in r.json()["jobs"]] == [h.id]
    assert master_client.get("/api/platform/system/jobs", params={"status": "weird"}).status_code == 400

    r = master_client.post(f"/api/platform/system/jobs/{h.id}/retry")
    assert r.status_code == 200, r.text
    assert r.json()["job"]["status"] == "queued"
    assert master_client.post(f"/api/platform/system/jobs/{h.id}/retry").status_code == 409
    assert master_client.post("/api/platform/system/jobs/999999/retry").status_code == 404
    audit = master_client.get("/api/platform/audit", params={"action": "job.retried"}).json()
    assert [a["target"] for a in audit] == [f"job:{h.id}"]
