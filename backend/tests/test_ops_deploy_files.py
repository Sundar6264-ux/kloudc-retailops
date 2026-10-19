"""The pilot-on-a-Mac kit (deploy/pilot-mac), the cloud VM compose and the
image's start script: valid, safe, and wired to the ops commands. Nothing
here installs or starts anything."""

import getpass
import pathlib
import plistlib
import re
import shutil
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
PILOT = REPO / "deploy" / "pilot-mac"
LABEL_PREFIX = "com.kloudc.retailops.pilot"
JOBS = ("postgres", "server", "backup", "drill", "parity")


def _conf() -> dict:
    out = {}
    for m in re.finditer(r'^: "\$\{(\w+):=([^}]*)\}"', (PILOT / "pilot.conf").read_text(), re.M):
        out[m.group(1)] = m.group(2)
    return out


def test_every_script_parses():
    scripts = sorted(PILOT.glob("*.sh")) + [REPO / "deploy" / "cloud-vm" / "backup-loop.sh", REPO / "docker" / "entrypoint.sh"]
    assert len(scripts) >= 9
    for script in scripts:
        shell = "sh" if script.name in ("backup-loop.sh", "entrypoint.sh") else "bash"
        done = subprocess.run([shell, "-n", str(script)], capture_output=True, text=True)
        assert done.returncode == 0, (script.name, done.stderr)


def test_the_pilot_has_its_own_cluster_port_and_app_port():
    conf = _conf()
    assert conf["PILOT_PG_PORT"] == "5434", "never the dev cluster (5433)"
    assert conf["PILOT_APP_PORT"] == "8900"
    assert conf["PILOT_TS_HTTPS_PORT"] not in ("443", "8443", "10000", "10001", "10002")
    assert "pgdata" in (PILOT / "lib.sh").read_text()


def test_only_install_sh_uses_root_and_only_the_pilot_labels():
    for script in PILOT.glob("*.sh"):
        text = script.read_text()
        if script.name != "install.sh":
            assert "sudo " not in re.sub(r"#.*", "", text), script.name
            assert "launchctl" not in text, script.name
    install = (PILOT / "install.sh").read_text()
    for line in install.splitlines():
        if "launchctl " in line and not line.lstrip().startswith("#"):
            assert "PILOT_LABEL_PREFIX" in line or "$dest" in line, line
    assert 'PILOT_LABEL_PREFIX="com.kloudc.retailops.pilot"' in (PILOT / "pilot.conf").read_text()
    for forbidden in ("bootout system/com.kloudc.retailops.backend", "tailscale serve reset", "cloudflared service"):
        assert forbidden not in install


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("plutil"), reason="macOS only (plutil, dscl)")
def test_install_dry_run_renders_valid_launch_daemons(tmp_path):
    out = tmp_path / "plists"
    done = subprocess.run(["bash", str(PILOT / "install.sh"), "--dry-run", str(out)], capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "PILOT_ROOT": str(tmp_path / "root")})
    assert done.returncode == 0, done.stderr
    files = sorted(p.name for p in out.iterdir())
    assert files == sorted(f"{LABEL_PREFIX}.{j}.plist" for j in JOBS)
    user = getpass.getuser()
    for job in JOBS:
        data = plistlib.loads((out / f"{LABEL_PREFIX}.{job}.plist").read_bytes())
        assert data["Label"] == f"{LABEL_PREFIX}.{job}"
        assert data["UserName"] == user, "runs as the Mac user, never root"
        text = (out / f"{LABEL_PREFIX}.{job}.plist").read_text()
        assert "__" not in text, "every placeholder filled"
    pg = plistlib.loads((out / f"{LABEL_PREFIX}.postgres.plist").read_bytes())
    assert pg["ProgramArguments"][1:] == ["-D", str(tmp_path / "root" / "pgdata")]
    assert pg["KeepAlive"] is True and pg["ExitTimeOut"] >= 30
    server = plistlib.loads((out / f"{LABEL_PREFIX}.server.plist").read_bytes())
    assert server["ProgramArguments"][-1].endswith("/app/deploy/pilot-mac/start-server.sh")
    assert server["EnvironmentVariables"]["FORWARDED_ALLOW_IPS"] == "127.0.0.1"
    backup = plistlib.loads((out / f"{LABEL_PREFIX}.backup.plist").read_bytes())
    assert backup["StartCalendarInterval"] == {"Hour": 3, "Minute": 15}
    assert backup["ProgramArguments"][-1] == "backup"
    drill = plistlib.loads((out / f"{LABEL_PREFIX}.drill.plist").read_bytes())
    assert drill["StartCalendarInterval"]["Day"] == 1 and drill["ProgramArguments"][-1] == "drill"


def test_start_script_checks_migrates_then_runs_one_worker():
    text = (PILOT / "start-server.sh").read_text()
    order = [text.index(s) for s in ("app.ops check-config", "app.platform migrate-platform",
                                     "app.platform migrate-all --keep-going", "exec ")]
    assert order == sorted(order)
    assert "--host 127.0.0.1" in text and "--workers 1" in text and "--timeout-graceful-shutdown" in text


def test_prepare_writes_quoted_settings_and_keeps_secrets_apart():
    text = (PILOT / "prepare.sh").read_text()
    block = text.split("# KloudC RetailOps pilot settings", 1)[1].split("\nCONF", 1)[0]
    settings = [ln for ln in block.splitlines() if re.match(r"^[A-Z_]+=", ln)]
    assert settings and all(re.match(r"^[A-Z_]+='.*'$", ln) for ln in settings), settings
    keys = {ln.split("=", 1)[0] for ln in settings}
    assert {"APP_ENV", "TENANCY_MODE", "PLATFORM_DATABASE_URL", "STORE_DATABASE_URL_TEMPLATE", "LOG_DIR",
            "SESSION_COOKIE_SECURE", "SIGNUP_ENABLED"} <= keys
    assert "RETAILOPS_SECRET_KEYS" not in keys, "the key ring lives in secrets/, not the config"
    assert "APP_ENV='production'" in block and "SESSION_COOKIE_SECURE='true'" in block
    assert "peer map=kloudc" in text, "only the Mac user, over the socket"
    assert "max_connections = 250" in text


def test_docker_start_migrates_every_store_in_multi_mode():
    entry = (REPO / "docker" / "entrypoint.sh").read_text()
    assert "python -m app.ops check-config" in entry
    assert "migrate-platform" in entry and "migrate-all --keep-going" in entry
    assert "--timeout-graceful-shutdown" in entry
    dockerfile = (REPO / "Dockerfile").read_text()
    assert "COPY content/ /src/content/" in dockerfile and "COPY content/ /app/content/" in dockerfile


def test_cloud_vm_compose_has_backups_https_and_enough_connections():
    compose = (REPO / "docker-compose.platform.yml").read_text()
    assert "max_connections=250" in compose
    assert 'profiles: ["backup"]' in compose and 'profiles: ["https"]' in compose
    assert "deploy/cloud-vm/backup.Dockerfile" in compose and "deploy/cloud-vm/Caddyfile" in compose
    backup = (REPO / "deploy" / "cloud-vm" / "backup.Dockerfile").read_text()
    assert backup.startswith("# syntax") and "FROM postgres:16" in backup and "USER postgres" in backup
    example = (REPO / "deploy" / "cloud-vm" / "env.platform.example").read_text()
    for secret in ("POSTGRES_PASSWORD", "RETAILOPS_SECRET_KEYS"):
        assert re.search(rf"^{secret}=$", example, re.M), secret


def test_runbook_names_the_one_sudo_command():
    runbook = (REPO / "docs" / "PILOT_RUNBOOK.md").read_text()
    assert "sudo ~/storeops/storeops/deploy/pilot-mac/install.sh" in runbook
    for topic in ("Tailscale", "app.kloudc.com", "Cloudflare", "pilot-mode", "import-sqlite", "load-pack", "parity",
                  "/api/ready", "Restore"):
        assert topic in runbook, topic
