"""Multi-process deploy (docs/WEBSITE_READY_CONTRACT.md C6): the pilot's
install.sh default path is unchanged (no worker), the worker LaunchDaemon is
a template installed only on request, and the compose worker service is
opt-in (profile "queue")."""

import os
import pathlib
import plistlib
import subprocess
import sys

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
PILOT = REPO / "deploy" / "pilot-mac"
mac_only = pytest.mark.skipif(sys.platform != "darwin", reason="the pilot scripts use dscl / plutil (macOS)")


def _env(tmp_path):
    env = dict(os.environ)
    env["PILOT_ROOT"] = str(tmp_path / "pilot-root")  # never the live pilot
    return env


def test_install_sh_jobs_do_not_include_the_worker():
    text = (PILOT / "install.sh").read_text()
    jobs = [l for l in text.splitlines() if l.startswith("JOBS=(")]
    assert jobs == ["JOBS=(postgres server backup drill parity)"]


@mac_only
def test_install_dry_run_renders_the_same_five_plists(tmp_path):
    out = tmp_path / "rendered"
    r = subprocess.run([str(PILOT / "install.sh"), "--dry-run", str(out)], env=_env(tmp_path),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    names = sorted(p.name for p in out.iterdir())
    assert names == [f"com.kloudc.retailops.pilot.{j}.plist" for j in ("backup", "drill", "parity", "postgres", "server")]


@mac_only
def test_worker_template_renders_and_lints(tmp_path):
    out = tmp_path / "worker"
    r = subprocess.run([str(PILOT / "install.sh"), "--dry-run", str(out), "--worker"], env=_env(tmp_path),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    plist = plistlib.loads((out / "com.kloudc.retailops.pilot.worker.plist").read_bytes())
    assert plist["Label"] == "com.kloudc.retailops.pilot.worker"
    assert plist["ProgramArguments"][-1].endswith("/app/deploy/pilot-mac/start-worker.sh")
    assert str(tmp_path / "pilot-root") in plist["ProgramArguments"][-1]
    assert plist["ExitTimeOut"] >= 30 and plist["KeepAlive"] is True
    assert "__" not in (out / "com.kloudc.retailops.pilot.worker.plist").read_text()


@mac_only
def test_worker_install_needs_root(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("running as root")
    r = subprocess.run([str(PILOT / "install.sh"), "--worker"], env=_env(tmp_path), capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 1 and "sudo" in r.stderr


def test_worker_install_refuses_without_queue_mode():
    text = (PILOT / "install.sh").read_text()
    block = text[text.index('if [ "$WORKER" = install ]; then\n  grep'):]
    assert "JOB_QUEUE_MODE" in block.split("start_job worker")[0]


def test_start_worker_runs_the_queue_worker():
    text = (PILOT / "start-worker.sh").read_text()
    assert "-m app.jobqueue worker" in text and "load_env" in text
    assert subprocess.run(["bash", "-n", str(PILOT / "start-worker.sh")]).returncode == 0
    assert subprocess.run(["bash", "-n", str(PILOT / "install.sh")]).returncode == 0


def test_compose_worker_is_opt_in():
    compose = yaml.safe_load((REPO / "docker-compose.platform.yml").read_text())
    services = compose["services"]
    w = services["worker"]
    assert w["profiles"] == ["queue"]
    assert w["command"][:4] == ["python", "-m", "app.jobqueue", "worker"]
    assert w["image"] == services["app"]["image"]
    assert w["environment"]["SCHEDULER_ENABLED"] == "false"
    assert w["environment"]["TENANCY_MODE"] == "multi"
    assert "ports" not in w  # never serves HTTP
    assert "profiles" not in services["app"]  # the default stack is unchanged
    assert w["volumes"] == services["app"]["volumes"]


def test_deploy_doc_covers_rollback_and_every_switch():
    doc = (REPO / "docs" / "DEPLOY_MULTI_PROCESS.md").read_text()
    for name in ("JOB_QUEUE_MODE", "LIVE_UPDATES_MODE", "STORAGE_BACKEND", "DB_POOL_MODE", "PLATFORM_DIRECT_URL",
                 "install.sh --worker", "## 6. Rollback", "python -m app.storage migrate --to s3"):
        assert name in doc, name
