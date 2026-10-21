"""Production start-up checks (app/ops/prod_checks.py) and the TOTP default."""

import asyncio
import logging

import pytest

from app.config import Settings, process_settings
from app.ops import prod_checks
from app.platform import config as pconfig
from app.platform import crypto

RING = "k2:" + "QkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkI="


def _settings(**values):
    base = {"app_env": "production", "tenancy_mode": "single", "store_code": "TEST_STORE", "log_dir": "/tmp/x"}
    base.update(values)
    return Settings(_env_file=None, **base)


def _keys(findings, level="error"):
    return sorted(f.key for f in findings if f.level == level)


def test_safe_single_store_settings_have_no_errors():
    assert _keys(prod_checks.check_config(_settings())) == []


def test_insecure_cookie_and_wildcard_cors_are_errors():
    s = _settings(session_cookie_secure=False, cors_origins="https://a.example, *")
    assert _keys(prod_checks.check_config(s)) == ["CORS_ORIGINS", "SESSION_COOKIE_SECURE"]


def test_production_refuses_to_start_on_an_error_and_names_it():
    s = _settings(session_cookie_secure=False)
    with pytest.raises(prod_checks.ProductionConfigError) as exc:
        prod_checks.enforce(s)
    assert "SESSION_COOKIE_SECURE" in str(exc.value)
    assert "APP_ENV=development" in str(exc.value)


def test_outside_production_the_same_errors_are_only_logged(caplog):
    s = _settings(app_env="development", session_cookie_secure=False)
    with caplog.at_level(logging.WARNING, logger="app.ops.prod_checks"):
        findings = prod_checks.enforce(s)
    assert "SESSION_COOKIE_SECURE" in _keys(findings)
    assert any("SESSION_COOKIE_SECURE" in r.message for r in caplog.records)


@pytest.fixture
def multi_env(monkeypatch):
    """Multi-mode settings on the process object (pget reads it)."""
    s = process_settings()
    for k, v in {"tenancy_mode": "multi", "app_env": "production", "retailops_secret_keys": RING,
                 "retailops_secret_key": "", "platform_database_url": "postgresql+psycopg://u@127.0.0.1:1/x",
                 "platform_require_totp": None, "signup_enabled": False, "session_cookie_secure": True,
                 "cors_origins": "https://app.example"}.items():
        monkeypatch.setattr(s, k, v)
    crypto.reset_keyring()
    yield s
    crypto.reset_keyring()


def test_totp_is_required_by_default_in_production_only(multi_env, monkeypatch):
    assert pconfig.require_totp() is True
    monkeypatch.setattr(multi_env, "app_env", "development")
    assert pconfig.require_totp() is False
    monkeypatch.setattr(multi_env, "platform_require_totp", True)
    assert pconfig.require_totp() is True
    # A blank value in .env is "not set".
    assert Settings(_env_file=None, platform_require_totp="").platform_require_totp is None


def test_multi_mode_production_rules(multi_env, monkeypatch):
    assert _keys(prod_checks.check_config(multi_env, check_database=False)) == []
    monkeypatch.setattr(multi_env, "platform_require_totp", False)
    assert "PLATFORM_REQUIRE_TOTP" in _keys(prod_checks.check_config(multi_env, check_database=False))
    monkeypatch.setattr(multi_env, "platform_require_totp", None)
    monkeypatch.setattr(multi_env, "platform_database_url", "sqlite:///x.db")
    assert "PLATFORM_DATABASE_URL" in _keys(prod_checks.check_config(multi_env, check_database=False))


def test_missing_and_development_key_rings_are_refused(multi_env, monkeypatch, tmp_path):
    monkeypatch.setattr(multi_env, "retailops_secret_keys", "")
    assert "RETAILOPS_SECRET_KEYS" in _keys(prod_checks.check_config(multi_env, check_database=False))
    monkeypatch.setattr(multi_env, "retailops_secret_keys", crypto.gen_key("dev1"))
    found = prod_checks.check_config(multi_env, check_database=False)
    assert any(f.key == "RETAILOPS_SECRET_KEYS" and "development key id" in f.message for f in found)
    # A key copied out of the local dev ring file under another id.
    dev = crypto.gen_key("dev1")
    dev_file = tmp_path / "keyring"
    dev_file.write_text(dev + "\n")
    monkeypatch.setattr(multi_env, "retailops_secret_key_file", str(dev_file))
    monkeypatch.setattr(multi_env, "retailops_secret_keys", "prod1:" + dev.split(":", 1)[1])
    found = prod_checks.check_config(multi_env, check_database=False)
    assert any("local development ring" in f.message for f in found)


def test_open_signup_needs_a_master_to_approve(multi_env, monkeypatch):
    monkeypatch.setattr(multi_env, "signup_enabled", True)
    monkeypatch.setattr(prod_checks, "_masters_exist", lambda: False)
    assert "SIGNUP_ENABLED" in _keys(prod_checks.check_config(multi_env))
    monkeypatch.setattr(prod_checks, "_masters_exist", lambda: True)
    assert "SIGNUP_ENABLED" not in _keys(prod_checks.check_config(multi_env))
    monkeypatch.setattr(prod_checks, "_masters_exist", lambda: None)  # database down: a warning only
    assert "SIGNUP_ENABLED" in _keys(prod_checks.check_config(multi_env), "warning")


def test_lifespan_runs_the_production_check_before_anything_starts(monkeypatch):
    from app import main

    calls = []
    monkeypatch.setattr(main, "enforce_production_config",
                        lambda: (_ for _ in ()).throw(prod_checks.ProductionConfigError([])))
    monkeypatch.setattr(main, "check_store_identity", lambda: calls.append("identity"))
    monkeypatch.setattr(main, "start_scheduler", lambda: calls.append("scheduler"))

    async def run():
        async with main.lifespan(main.app):
            pass

    with pytest.raises(prod_checks.ProductionConfigError):
        asyncio.run(run())
    assert calls == []


def test_shutdown_runs_every_step_even_after_a_failure(caplog):
    from app import main

    ran = []
    with caplog.at_level(logging.INFO, logger="retailops.lifecycle"):
        main._stop_steps([("a", lambda: (_ for _ in ()).throw(RuntimeError("x"))), ("b", lambda: ran.append("b"))])
    assert ran == ["b"]
    assert any("shutdown step a failed" in r.message for r in caplog.records)
    assert any(r.message.startswith("stopped in") for r in caplog.records)


def test_check_config_command(capsys, monkeypatch):
    from app.ops.__main__ import main as ops_main

    monkeypatch.setattr(process_settings(), "session_cookie_secure", False)
    assert ops_main(["check-config"]) == 1
    out = capsys.readouterr().out
    assert "ERROR SESSION_COOKIE_SECURE" in out


# --- going public on app.kloudc.com (pre-production 2026-10-21) -----------------------


def _warn(findings):
    return {f.key: f.message for f in findings if f.level == "warning"}


def test_go_public_warnings_name_each_missing_setting(multi_env, monkeypatch):
    monkeypatch.delenv("BACKUP_OFFSITE_DIR", raising=False)
    monkeypatch.setattr(multi_env, "platform_public_url", "https://app.kloudc.com")
    monkeypatch.setattr(multi_env, "platform_domain", "retailops.invalid")
    monkeypatch.setattr(multi_env, "public_base_url", "")
    monkeypatch.setattr(multi_env, "terms_url", "")
    w = _warn(prod_checks.check_config(multi_env, check_database=False))
    assert "kloudc.com" in w["PLATFORM_DOMAIN"]
    assert "https://app.kloudc.com" in w["CORS_ORIGINS"]
    assert {"PUBLIC_BASE_URL", "TERMS_URL", "BACKUP_OFFSITE_DIR"} <= set(w)
    # All set: none of these warnings, and never an error.
    monkeypatch.setenv("BACKUP_OFFSITE_DIR", "/Volumes/offsite")
    monkeypatch.setattr(multi_env, "platform_domain", "kloudc.com")
    monkeypatch.setattr(multi_env, "cors_origins", "https://app.kloudc.com/, https://pilot.example")
    monkeypatch.setattr(multi_env, "public_base_url", "https://app.kloudc.com")
    monkeypatch.setattr(multi_env, "terms_url", "https://app.kloudc.com/terms")
    findings = prod_checks.check_config(multi_env, check_database=False)
    w = _warn(findings)
    assert not {"PLATFORM_DOMAIN", "CORS_ORIGINS", "PUBLIC_BASE_URL", "TERMS_URL", "BACKUP_OFFSITE_DIR"} & set(w)
    assert _keys(findings) == []


def test_plain_http_public_address_is_warned(multi_env, monkeypatch):
    monkeypatch.setattr(multi_env, "platform_public_url", "http://app.kloudc.com")
    assert "PLATFORM_PUBLIC_URL" in _warn(prod_checks.check_config(multi_env, check_database=False))


def test_toast_stores_without_analytics_keys_are_named(multi_env, monkeypatch):
    monkeypatch.setattr(prod_checks, "_toast_stores_without_analytics", lambda: ["ALPHA"])
    w = _warn(prod_checks.check_config(multi_env, check_database=True))
    assert "ALPHA" in w["TOAST_ANALYTICS"] and "Integrations" in w["TOAST_ANALYTICS"]
    monkeypatch.setattr(prod_checks, "_toast_stores_without_analytics", lambda: [])
    assert "TOAST_ANALYTICS" not in _warn(prod_checks.check_config(multi_env, check_database=True))
