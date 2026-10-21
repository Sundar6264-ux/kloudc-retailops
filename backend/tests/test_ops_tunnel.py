"""The KloudC RetailOps Cloudflare Tunnel kit (deploy/pilot-mac/install-tunnel.sh,
launchd/tunnel.plist, tunnel/config.yml.template; pre-production 2026-10-21).

It must only ever touch its own launchd job and files: never `cloudflared
service install`, never ~/.cloudflared (config.yml / cert.pem), never another
tunnel's service. Nothing here installs or starts anything."""

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
LABEL = "com.kloudc.retailops.pilot.tunnel"
SCRIPT = PILOT / "install-tunnel.sh"


def _code(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def test_installer_never_touches_another_tunnel():
    code = _code(SCRIPT.read_text())
    assert "service install" not in code and "service uninstall" not in code
    assert ".cloudflared" not in code and "cert.pem" not in code
    assert "com.cloudflare" not in code
    for line in code.splitlines():
        if "launchctl " in line:
            assert "$LABEL" in line or "$DEST" in line, line
    assert 'LABEL="$PILOT_LABEL_PREFIX.tunnel"' in code
    assert "com.kloudc.retailops.*" in code, "refuses any other label"
    plist = (PILOT / "launchd" / "tunnel.plist").read_text()
    args = plist[plist.index("<array>"):plist.index("</array>")]
    assert "service" not in args and ".cloudflared" not in args
    template = (PILOT / "tunnel" / "config.yml.template").read_text()
    assert "tunnel: kloudc-retailops" in template
    assert "hostname: app.kloudc.com" in template and "service: http://127.0.0.1:__APP_PORT__" in template
    assert "credentials-file: __ROOT__/secrets/" in template
    assert template.rstrip().endswith("- service: http_status:404")


def _dry_run(tmp_path, secret):
    root = tmp_path / "root"
    (root / "secrets").mkdir(parents=True)
    (root / "secrets" / secret).write_text("x\n")
    out = tmp_path / "out"
    done = subprocess.run(["bash", str(SCRIPT), "--dry-run", str(out)], capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "PILOT_ROOT": str(root),
                               "CLOUDFLARED": "/opt/homebrew/bin/cloudflared"})
    assert done.returncode == 0, done.stderr
    text = (out / f"{LABEL}.plist").read_text()
    assert "__" not in text, "every placeholder filled"
    return root, out, plistlib.loads(text.encode())


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("plutil"), reason="macOS only (plutil, dscl)")
def test_token_mode_renders_its_own_job_and_config(tmp_path):
    root, out, data = _dry_run(tmp_path, "tunnel.token")
    assert data["Label"] == LABEL and data["UserName"] == getpass.getuser()
    args = data["ProgramArguments"]
    assert args[0].endswith("cloudflared") and args[1:3] == ["tunnel", "--no-autoupdate"]
    assert args[args.index("--config") + 1] == str(root / "config" / "tunnel-token.yml")
    assert args[args.index("--token-file") + 1] == str(root / "secrets" / "tunnel.token")
    assert data["EnvironmentVariables"]["HOME"] == str(root / "run" / "tunnel-home"), "never the user's ~/.cloudflared"
    assert data["KeepAlive"] is True
    assert "no-autoupdate: true" in (out / "tunnel-token.yml").read_text()


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("plutil"), reason="macOS only (plutil, dscl)")
def test_config_mode_routes_app_kloudc_com_to_the_server(tmp_path):
    root, out, data = _dry_run(tmp_path, "tunnel-credentials.json")
    args = data["ProgramArguments"]
    assert args[-2:] == ["run", "kloudc-retailops"]
    assert args[args.index("--config") + 1] == str(root / "config" / "tunnel.yml")
    cfg = (out / "tunnel.yml").read_text()
    assert f"credentials-file: {root}/secrets/tunnel-credentials.json" in cfg
    assert "service: http://127.0.0.1:8900" in cfg and "__" not in cfg


def test_without_a_secret_it_explains_and_stops(tmp_path):
    root = tmp_path / "root"
    done = subprocess.run(["bash", str(SCRIPT), "--dry-run", str(tmp_path / "o")], capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "PILOT_ROOT": str(root)})
    assert done.returncode == 1 and "tunnel.token" in done.stderr
    assert not (tmp_path / "o").exists()


def test_main_installer_never_installs_or_removes_the_tunnel():
    install = (PILOT / "install.sh").read_text()
    jobs = re.search(r"^JOBS=\(([^)]*)\)", install, re.M).group(1).split()
    assert "tunnel" not in jobs


def test_production_env_example_names_real_settings_and_no_secrets():
    from app.config import Settings

    text = (PILOT / "production.env.example").read_text()
    pairs = dict(re.findall(r"^([A-Z_]+)='([^']*)'$", text, re.M))
    shell_only = {"FORWARDED_ALLOW_IPS", "BACKUP_OFFSITE_DIR", "BACKUP_OFFSITE_KEEP"}
    fields = set(Settings.model_fields)
    for key in set(pairs) - shell_only:
        assert key.lower() in fields, key
    assert pairs["PLATFORM_PUBLIC_URL"] == "https://app.kloudc.com"
    assert pairs["PLATFORM_DOMAIN"] == "kloudc.com"
    assert pairs["DIRECTORY_CLIENT_IP_HEADER"] == "CF-Connecting-IP"
    assert pairs["PLATFORM_REQUIRE_TOTP"] == "true" and pairs["SIGNUP_ENABLED"] == "false"
    assert pairs["SESSION_COOKIE_SECURE"] == "true" and pairs["SESSION_COOKIE_DOMAIN"] == ""
    assert "https://app.kloudc.com" in pairs["CORS_ORIGINS"].split(",")
    for word in ("SECRET", "PASSWORD", "TOKEN", "KEYS"):
        assert not any(word in k for k in pairs), "secrets stay in <install>/secrets"


def test_nightly_demo_reset_is_a_no_op_without_a_demo_store():
    text = (PILOT / "run-demo-reset.sh").read_text()
    assert "demo-reset --skip-if-missing" in text
    assert "secrets/demo_review_password" in text
