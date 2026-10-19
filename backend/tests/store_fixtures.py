"""Fake stores and the legacy pack for tests (docs/M2_CONTRACT.md section 7).

- ``use_store(monkeypatch, code)``: switch this test to one of the fake stores
  (RETAILOPS_TEST_TOAST, RETAILOPS_TEST_CLOVER, DEMO): its settings from
  ``tests/fixtures/stores/<code>.env`` and its pack. The database and upload
  folders stay the test run's own.
- ``fake_store``: a fixture parametrised over the three (import it into a
  test module: ``from tests.store_fixtures import fake_store  # noqa: F401``).
- ``use_legacy_pack(monkeypatch)``: the private legacy pack + settings
  (section 1.6); skips the test when they are not on this machine.
- ``legacy_checklist_seeds()``: the legacy built-in checklists in the shape
  checklist_schemas.store_seed_definitions returns (conftest serves them to
  the older checklist tests).
"""

import os
import pathlib
from typing import Dict

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
STORES_DIR = pathlib.Path(__file__).resolve().parent / "fixtures" / "stores"
STORE_CODES = ("RETAILOPS_TEST_TOAST", "RETAILOPS_TEST_CLOVER", "DEMO")
# The private folder outside git with the origin store's pack and recorded
# data (never committed): RETAILOPS_PRIVATE_DIR (or the old
# RETAILOPS_PRIVATE_DIR), default ~/.kloudc-retailops/private.
PRIVATE_DIR = pathlib.Path(
    os.environ.get("RETAILOPS_PRIVATE_DIR")
    or pathlib.Path.home() / ".kloudc-retailops" / "private"
)
# Settings a fixture file may never move: the run's own database and folders.
_KEEP = {
    "database_url",
    "flyers_dir",
    "team_uploads_dir",
    "invoice_uploads_dir",
    "uploads_root",
    "frontend_dist_dir",
    "insights_hmac_key_file",
    "firebase_credentials_file",
}


def read_env_file(path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for raw in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip().lower()] = value
    return values


def apply_settings(monkeypatch, values: Dict[str, str]) -> None:
    """Set `values` (lower-case field names) on the cached Settings object,
    validated through a fresh Settings first, so every module that reads
    get_settings() sees them; monkeypatch undoes it after the test."""
    from app.config import Settings, get_settings

    settings = get_settings()
    wanted = {k: v for k, v in values.items() if k not in _KEEP}
    unknown = [k for k in wanted if k not in Settings.model_fields]
    assert not unknown, f"unknown settings in a store fixture: {unknown}"
    base = {k: getattr(settings, k) for k in Settings.model_fields if k not in wanted}
    # The neutral/legacy pairs are kept equal by a validator: let the new
    # value of either name win.
    if "store_timezone" in wanted or "toast_timezone" in wanted:
        base.pop("store_timezone", None)
        base.pop("toast_timezone", None)
    if "ap_inbox_email" in wanted or "toast_invoice_email" in wanted:
        base.pop("ap_inbox_email", None)
        base.pop("toast_invoice_email", None)
    fresh = Settings(_env_file=None, **base, **wanted)
    for name in Settings.model_fields:
        new = getattr(fresh, name)
        if getattr(settings, name) != new:
            monkeypatch.setattr(settings, name, new)


def _reset_caches() -> None:
    from app.pos import registry
    from app.services import checklist_schemas
    from app.store import pack as store_pack

    registry.reset_cache()
    store_pack.reload_pack()
    checklist_schemas.clear_cache()


def use_store(monkeypatch, code: str) -> dict:
    """Switch to a fake store. Returns the values applied."""
    values = read_env_file(STORES_DIR / f"{code}.env")
    pack_file = values.get("store_pack_file")
    if pack_file and not os.path.isabs(pack_file):
        values["store_pack_file"] = str(BACKEND / pack_file)
    apply_settings(monkeypatch, values)
    from app.store import pack as store_pack

    store_pack.set_override(None)
    _reset_caches()
    monkeypatch.setattr("app.pos.registry._cache", {})
    return values


@pytest.fixture(params=STORE_CODES)
def fake_store(request, monkeypatch):
    use_store(monkeypatch, request.param)
    yield request.param
    _reset_caches()


SUITE_PACK = pathlib.Path(__file__).resolve().parent / "fixtures" / "packs" / "suite.json"


def use_suite_pack() -> None:
    from app.services import checklist_schemas
    from app.store import pack as store_pack

    store_pack.set_override(store_pack.load_pack_file(SUITE_PACK))
    checklist_schemas.clear_cache()


def private_data(name: str) -> pathlib.Path:
    """A data file recorded from the origin store (business data), kept in the
    private folder outside git (M2 contract 0.6 / 1.6). Skips without it."""
    path = PRIVATE_DIR / name
    if not path.is_file():
        pytest.skip(f"private origin-store data not on this machine ({path}); see docs/M2_CONTRACT.md 1.6")
    return path


def legacy_private_files():
    return PRIVATE_DIR / "origin.pack.json", PRIVATE_DIR / "origin.env"


def use_legacy_pack(monkeypatch) -> None:
    pack_path, env_path = legacy_private_files()
    if not pack_path.is_file() or not env_path.is_file():
        pytest.skip(f"private legacy pack not on this machine ({PRIVATE_DIR}); see docs/M2_CONTRACT.md 1.6")
    values = read_env_file(env_path)
    values.pop("document_logo_file", None)
    apply_settings(monkeypatch, values)
    from app.store import pack as store_pack

    store_pack.set_override(store_pack.load_pack_file(pack_path))
    from app.services import checklist_schemas

    checklist_schemas.clear_cache()


def legacy_checklist_seeds():
    """checklist_builtin.SEED_DEFINITIONS as store seed definitions, with the
    version notes and author migration 0039 writes."""
    from app.services import checklist_builtin as builtin

    out = []
    for seed in builtin.SEED_DEFINITIONS:
        latest = seed["versions"][-1]
        total = len(seed["versions"])
        out.append(
            {
                "checklist_type": seed["checklist_type"],
                "title": latest["title"],
                "short_name": seed["short_name"],
                "description": latest.get("description"),
                "sort_order": seed["sort_order"],
                "settings": seed["settings"],
                "versions": list(seed["versions"]),
                "notes": {i + 1: builtin._seed_note(seed["checklist_type"], i + 1, total) for i in range(total)},
                "created_by": builtin.SEEDED_BY,
            }
        )
    return out
