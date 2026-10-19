"""Security + ops verifier (final pass), single-store / no database:

- APP_ENV fails closed: a typo or an unknown value is production;
- CORS "null" with credentials, and a private packs folder inside git,
  refuse to start in production;
- a private pack that is a hard link is refused;
- no thread pool in app/ hands work to a worker without the caller's
  context (in multi mode the worker would have no store).
"""

import ast
import os
import pathlib

import pytest

from app.config import Settings, process_settings
from app.ops import prod_checks
from app.ops.private_pack import PrivatePackError, check_outside_git
from app.platform import config as pconfig

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
REPO = APP.parents[1]


def _settings(**values):
    base = {"app_env": "production", "tenancy_mode": "single", "store_code": "TEST_STORE", "log_dir": "/tmp/x"}
    base.update(values)
    return Settings(_env_file=None, **base)


def _keys(findings, level="error"):
    return sorted(f.key for f in findings if f.level == level)


@pytest.mark.parametrize("value", ["production", "prod", "PROD", " Production ", "", "producton", "staging",
                                   "prd", "live"])
def test_any_app_env_but_development_dev_or_test_is_production(value):
    assert prod_checks.is_production(_settings(app_env=value)) is True
    assert pconfig.env_is_production(value) is True


@pytest.mark.parametrize("value", ["development", "dev", "test", "TEST", " Development "])
def test_development_dev_and_test_are_not_production(value):
    assert prod_checks.is_production(_settings(app_env=value)) is False


def test_a_mistyped_app_env_still_refuses_an_insecure_cookie_and_says_why():
    s = _settings(app_env="producton", session_cookie_secure=False)
    with pytest.raises(prod_checks.ProductionConfigError, match="SESSION_COOKIE_SECURE"):
        prod_checks.enforce(s)
    assert "APP_ENV" in _keys(prod_checks.check_config(s), "warning")


def test_a_mistyped_app_env_keeps_totp_required(monkeypatch):
    s = process_settings()
    monkeypatch.setattr(s, "app_env", "producton")
    monkeypatch.setattr(s, "platform_require_totp", None)
    assert pconfig.require_totp() is True


def test_cors_null_origin_is_an_error():
    s = _settings(cors_origins="https://app.example, null")
    assert "CORS_ORIGINS" in _keys(prod_checks.check_config(s))
    assert _keys(prod_checks.check_config(_settings(cors_origins="https://app.example"))) == []


def test_security_headers_off_is_a_warning():
    assert "SECURITY_HEADERS" in _keys(prod_checks.check_config(_settings(security_headers="off")), "warning")


def test_private_packs_dir_inside_a_git_checkout_is_an_error(tmp_path):
    inside = _settings(private_packs_dir=str(REPO / "backend" / "packs"))
    assert "PRIVATE_PACKS_DIR" in _keys(prod_checks.check_config(inside))
    outside = _settings(private_packs_dir=str(tmp_path))
    assert "PRIVATE_PACKS_DIR" not in _keys(prod_checks.check_config(outside))


def test_a_hard_linked_pack_is_refused_a_copy_is_fine(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    tracked = repo / "store.pack.json"
    tracked.write_text("{}")
    outside = tmp_path / "private"
    outside.mkdir()
    with pytest.raises(PrivatePackError, match="inside the git checkout"):
        check_outside_git(tracked)
    link = outside / "link.json"
    link.symlink_to(tracked)
    with pytest.raises(PrivatePackError, match="inside the git checkout"):
        check_outside_git(link)
    hard = outside / "hard.json"
    os.link(tracked, hard)
    with pytest.raises(PrivatePackError, match="hard link"):
        check_outside_git(hard)
    copy = outside / "copy.json"
    copy.write_text(tracked.read_text())
    assert check_outside_git(copy) == copy.resolve()


def _calls(tree, attr):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == attr:
            yield node


def test_thread_pool_work_keeps_the_callers_context():
    """``executor.submit(fn, ...)`` runs fn WITHOUT the caller's context
    variables (unlike Starlette's threadpool): in multi mode the worker has
    no store - it can't open the store's database, and a send can't read the
    store's pilot setting. Submit ``contextvars.copy_context().run`` (one
    copy per task) or run inside ``store_context`` in the worker."""
    offenders = []
    for path in sorted(APP.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if ".submit(" not in source:
            continue
        tree = ast.parse(source)
        for call in _calls(tree, "submit"):
            first = call.args[0] if call.args else None
            ok = (
                isinstance(first, ast.Attribute) and first.attr == "run"
                and isinstance(first.value, ast.Call) and isinstance(first.value.func, ast.Attribute)
                and first.value.func.attr == "copy_context"
            )
            # tenancy/jobs.py: its worker function enters the store's context itself.
            if not ok and path.name == "jobs.py" and path.parent.name == "tenancy":
                ok = isinstance(first, ast.Name) and first.id == "one"
            if not ok:
                offenders.append(f"{path.relative_to(APP.parent)}:{call.lineno}")
    assert offenders == [], offenders
