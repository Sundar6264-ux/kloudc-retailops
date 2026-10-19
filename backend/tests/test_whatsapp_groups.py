"""WhatsApp groups for checklists (docs/CHECKLIST_BUILDER_APP_AND_GROUPS_CONTRACT.md,
section 1): the whatsapp_groups table, the admin API under
/api/checklists/admin/whatsapp-groups, settings validation against the table,
and group_id_for_group resolution (table chat id, env fallback for built-ins).

Whapi is always mocked: every httpx call from whapi_client goes to a fake, and
any other call fails the test. Nothing here sends a WhatsApp message.
"""

import datetime as dt
import json
import os
import pathlib
import sqlite3
import subprocess
import sys

import httpx
import pytest

from app.config import get_settings
from app.db import Base
from app.models import ChecklistDefinition, ChecklistSubmission, WhatsAppGroup
from app.services import checklist_submission, whapi_client
from app.services import whatsapp_groups as groups_svc
from app.team import auto_tasks
from app.team.tasks_models import Task

from tests.conftest import client_for, make_user

# Made-up rules in the shape these tests were written for (two built-in
# WhatsApp roles, store and kitchen): tests/fixtures/packs/suite.json.
pytestmark = pytest.mark.suite_pack

BACKEND = pathlib.Path(__file__).resolve().parents[1]
API = "/api/checklists/admin/whatsapp-groups"
DEFS = "/api/checklists/admin/definitions"
ST, SK = "store_closing", "kitchen_closing"
GROUP_A = "120363011111111111@g.us"
GROUP_B = "120363022222222222@g.us"


class FakeResponse:
    def __init__(self, payload, status=200, bad_json=False):
        self._payload = payload
        self.status_code = status
        self.bad_json = bad_json

    def json(self):
        if self.bad_json:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code}", request=httpx.Request("GET", "https://whapi.test"), response=None
            )


class FakeWhapi:
    """GET /groups with count/offset over `groups`; counts calls."""

    def __init__(self, groups=None, status=200, exc=None):
        self.groups = groups or []
        self.status = status
        self.exc = exc
        self.bad_json = False
        self.calls = []

    def get(self, url, headers=None, params=None, timeout=None):
        assert url.endswith("/groups"), f"unexpected Whapi GET {url}"
        self.calls.append(dict(params or {}))
        if self.exc is not None:
            raise self.exc
        count, offset = params["count"], params["offset"]
        page = self.groups[offset:offset + count]
        return FakeResponse({"groups": page, "count": len(page), "total": len(self.groups), "offset": offset},
                            self.status, self.bad_json)


@pytest.fixture(autouse=True)
def _never_real_whapi(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a real Whapi call was attempted")

    monkeypatch.setattr(whapi_client.httpx, "get", refuse)
    monkeypatch.setattr(whapi_client.httpx, "post", refuse)
    monkeypatch.setattr(checklist_submission, "send_whatsapp_message", refuse)
    groups_svc.clear_available_cache()
    yield
    groups_svc.clear_available_cache()


@pytest.fixture
def env_groups(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "whapi_store_checklist_group_id", "120363099999999991@g.us")
    monkeypatch.setattr(s, "whapi_kitchen_checklist_group_id", "")
    monkeypatch.setattr(s, "whapi_chat_id", "invoice-group@g.us")


@pytest.fixture
def whapi(monkeypatch):
    fake = FakeWhapi()
    monkeypatch.setattr(get_settings(), "whapi_api_token", "test-token")
    monkeypatch.setattr(whapi_client.httpx, "get", fake.get)
    return fake


def _add(admin_client, label="Deli team", chat_id=GROUP_A):
    r = admin_client.post(API, json={"chat_id": chat_id, "label": label})
    assert r.status_code == 200, r.text
    return r.json()


def _items(admin_client):
    return {g["key"]: g for g in admin_client.get(API).json()["items"]}


# --- List / CRUD --------------------------------------------------------------------------------


def test_list_starts_with_the_two_builtins(admin_client, env_groups):
    items = admin_client.get(API).json()["items"]
    assert [(g["key"], g["label"], g["builtin"], g["active"]) for g in items] == [
        ("store", "Store staff group", True, True),
        ("kitchen", "Kitchen staff group", True, True),
    ]
    by_key = {g["key"]: g for g in items}
    # The env chat id, masked to its last 6 characters; kitchen isn't configured.
    assert by_key["store"]["chat_id_masked"] == "…999991@g.us" and by_key["store"]["configured"] is True
    assert by_key["kitchen"]["chat_id_masked"] is None and by_key["kitchen"]["configured"] is False
    # The seeded checklists use them (Main Store -> store, both Market Kitchen -> kitchen).
    assert len(by_key["store"]["used_by"]) == 1 and by_key["store"]["used_by"][0].startswith("Main Store")
    assert len(by_key["kitchen"]["used_by"]) == 2
    # `marketing` is additive (flyers, migration 0046); every group starts unticked.
    assert set(items[0]) == {"key", "label", "chat_id_masked", "configured", "builtin", "active", "marketing", "used_by"}
    assert [g["marketing"] for g in items] == [False, False]


def test_create_rename_turn_off_delete(admin_client, db):
    created = _add(admin_client, "  Deli   Team! ", GROUP_A)
    assert created == {"key": "deli_team", "label": "Deli Team!", "chat_id_masked": "…111111@g.us",
                       "configured": True, "builtin": False, "active": True, "marketing": False, "used_by": []}
    row = db.query(WhatsAppGroup).filter_by(key="deli_team").one()
    assert row.chat_id == GROUP_A and row.created_by == "boss" and row.created_at.tzinfo is not None
    # The full chat id is never returned.
    assert GROUP_A not in admin_client.get(API).text

    r = admin_client.patch(f"{API}/deli_team", json={"label": "Deli + bakery"})
    assert r.status_code == 200 and r.json()["label"] == "Deli + bakery" and r.json()["key"] == "deli_team"
    r = admin_client.patch(f"{API}/deli_team", json={"active": False})
    assert r.status_code == 200 and r.json()["active"] is False
    assert admin_client.patch(f"{API}/deli_team", json={"active": "no"}).status_code == 400
    assert admin_client.patch(f"{API}/deli_team", json={"label": "  "}).status_code == 400
    assert admin_client.patch(f"{API}/nope", json={"label": "x"}).status_code == 404

    r = admin_client.delete(f"{API}/deli_team")
    assert r.status_code == 204 and r.content == b""
    assert "deli_team" not in _items(admin_client)
    assert admin_client.delete(f"{API}/deli_team").status_code == 404


def test_create_validation(admin_client, env_groups):
    for bad in ("", "not-a-chat", "12345@example.com", "@g.us", None, 5):
        r = admin_client.post(API, json={"chat_id": bad, "label": "X"})
        assert r.status_code == 400, bad
        assert "chat id" in r.json()["detail"]
    assert admin_client.post(API, json={"chat_id": GROUP_A}).status_code == 400
    assert admin_client.post(API, json={"chat_id": GROUP_A, "label": "!!!"}).status_code == 400
    assert admin_client.post(API, json={"chat_id": GROUP_A, "label": "x" * 61}).status_code == 400
    assert admin_client.post(API, json=["x"]).status_code == 422
    # A person or a channel is a valid chat id too.
    assert admin_client.post(API, json={"chat_id": "18605551234@s.whatsapp.net", "label": "Owner"}).status_code == 200

    _add(admin_client, "Deli team", GROUP_A)
    # Same key (slug of the label) -> 409.
    r = admin_client.post(API, json={"chat_id": GROUP_B, "label": "deli-team"})
    assert r.status_code == 409 and "already exists" in r.json()["detail"]
    # Same chat id -> 409, naming the existing group.
    r = admin_client.post(API, json={"chat_id": GROUP_A, "label": "Other"})
    assert r.status_code == 409 and "Deli team" in r.json()["detail"]
    # A built-in's env chat id counts as added.
    r = admin_client.post(API, json={"chat_id": "120363099999999991@g.us", "label": "Store again"})
    assert r.status_code == 409 and "Store staff group" in r.json()["detail"]
    # A label slugging to a built-in key is taken too.
    assert admin_client.post(API, json={"chat_id": GROUP_B, "label": "Store"}).status_code == 409


def test_builtin_cant_be_deleted_but_can_be_renamed_and_turned_off(admin_client):
    r = admin_client.delete(f"{API}/store")
    assert r.status_code == 400 and "built-in" in r.json()["detail"]
    assert admin_client.patch(f"{API}/store", json={"label": "Store staff"}).json()["label"] == "Store staff"
    assert admin_client.patch(f"{API}/kitchen", json={"active": False}).json()["active"] is False
    meta = admin_client.get("/api/checklists/admin/meta").json()["whatsapp_groups"]
    assert meta == [{"key": "store", "label": "Store staff", "builtin": True}]


def test_delete_in_use_is_409_and_names_the_checklists(admin_client):
    _add(admin_client, "Deli team")
    for title in ("Deli Opening", "Deli Closing"):
        r = admin_client.post(DEFS, json={"title": title, "settings": {"whatsapp_group": "deli_team"}})
        assert r.status_code == 200, r.text
    r = admin_client.delete(f"{API}/deli_team")
    assert r.status_code == 409
    body = r.json()
    assert sorted(body["used_by"]) == ["Deli Closing", "Deli Opening"]
    assert "Deli Opening" in body["detail"] and "Deli Closing" in body["detail"]
    assert sorted(_items(admin_client)["deli_team"]["used_by"]) == ["Deli Closing", "Deli Opening"]
    # An inactive checklist still counts.
    for t in ("deli_opening", "deli_closing"):
        assert admin_client.patch(f"{DEFS}/{t}", json={"active": False}).status_code == 200
    assert admin_client.delete(f"{API}/deli_team").status_code == 409
    for t in ("deli_opening", "deli_closing"):
        admin_client.patch(f"{DEFS}/{t}", json={"settings": {"whatsapp_group": None}})
    assert admin_client.delete(f"{API}/deli_team").status_code == 204


def test_admin_only(db, admin_client, staff_client, anon_client):
    checker = client_for(db, make_user(db, "asha", features=["daily_checklists"]))
    for client, code in ((staff_client, 403), (checker, 403), (anon_client, 401)):
        assert client.get(API).status_code == code
        assert client.get(f"{API}/available").status_code == code
        assert client.post(API, json={"chat_id": GROUP_A, "label": "X"}).status_code == code
        assert client.patch(f"{API}/store", json={"label": "X"}).status_code == code
        assert client.delete(f"{API}/store").status_code == code
    assert _items(admin_client)["store"]["label"] == "Store staff group"


# --- Meta -----------------------------------------------------------------------------------


def test_meta_shape(admin_client):
    meta = admin_client.get("/api/checklists/admin/meta").json()
    assert meta["whatsapp_groups"] == [
        {"key": "store", "label": "Store staff group", "builtin": True},
        {"key": "kitchen", "label": "Kitchen staff group", "builtin": True},
    ]
    _add(admin_client, "Deli team")
    _add(admin_client, "Old team", GROUP_B)
    admin_client.patch(f"{API}/old_team", json={"active": False})
    meta = admin_client.get("/api/checklists/admin/meta").json()["whatsapp_groups"]
    assert meta[-1] == {"key": "deli_team", "label": "Deli team", "builtin": False}
    assert "old_team" not in [g["key"] for g in meta]
    # Everything else in meta is unchanged.
    assert {"item_types", "issue_tags", "validation", "default_settings", "file_types", "max_files"} <= set(
        admin_client.get("/api/checklists/admin/meta").json()
    )


# --- Available on WhatsApp ----------------------------------------------------------------------


def test_available_not_configured(admin_client, monkeypatch):
    monkeypatch.setattr(get_settings(), "whapi_api_token", "")
    r = admin_client.get(f"{API}/available")
    assert r.status_code == 200 and r.json() == {"items": [], "error": "WhatsApp isn't connected"}


def test_available_lists_groups_marks_added_and_caches(admin_client, whapi, env_groups):
    whapi.groups = [
        {"id": GROUP_B, "name": "Zeta crew", "type": "group"},
        {"id": GROUP_A, "name": "alpha team", "type": "group"},
        {"id": "120363099999999991@g.us", "name": "Main Store Staff", "type": "group"},
        {"name": "no id - skipped"},
    ]
    _add(admin_client, "Deli team", GROUP_A)
    body = admin_client.get(f"{API}/available").json()
    assert "error" not in body
    assert body["items"] == [
        {"chat_id": GROUP_B, "name": "Zeta crew", "already_added_key": None},
        {"chat_id": GROUP_A, "name": "alpha team", "already_added_key": "deli_team"},
        {"chat_id": "120363099999999991@g.us", "name": "Main Store Staff", "already_added_key": "store"},
    ]
    assert len(whapi.calls) == 1
    # Cached for 5 minutes - but "already added" is always fresh.
    admin_client.delete(f"{API}/deli_team")
    body = admin_client.get(f"{API}/available").json()
    assert len(whapi.calls) == 1
    assert [g["already_added_key"] for g in body["items"]] == [None, None, "store"]
    # ?refresh=1 asks Whapi again.
    admin_client.get(f"{API}/available?refresh=1")
    assert len(whapi.calls) == 2


def test_available_cache_expires(admin_client, whapi, monkeypatch):
    whapi.groups = [{"id": GROUP_A, "name": "A"}]
    clock = [1000.0]
    monkeypatch.setattr(groups_svc.time, "monotonic", lambda: clock[0])
    admin_client.get(f"{API}/available")
    clock[0] += 299
    admin_client.get(f"{API}/available")
    assert len(whapi.calls) == 1
    clock[0] += 2
    admin_client.get(f"{API}/available")
    assert len(whapi.calls) == 2


def test_available_paginates(admin_client, whapi):
    whapi.groups = [{"id": f"1203630{i:011d}@g.us", "name": f"Group {i:03d}"} for i in range(250)]
    items = admin_client.get(f"{API}/available").json()["items"]
    assert len(items) == 250 and len({g["chat_id"] for g in items}) == 250
    assert whapi.calls == [{"count": 100, "offset": 0}, {"count": 100, "offset": 100}, {"count": 100, "offset": 200}]
    # An exact multiple of the page size stops on `total`, not an empty page.
    groups_svc.clear_available_cache()
    whapi.calls.clear()
    whapi.groups = whapi.groups[:200]
    assert len(admin_client.get(f"{API}/available").json()["items"]) == 200
    assert len(whapi.calls) == 2


@pytest.mark.parametrize("kind", ["status", "timeout", "bad_json"])
def test_available_whapi_error_is_never_a_500(admin_client, whapi, kind):
    if kind == "status":
        whapi.status = 401
    elif kind == "timeout":
        whapi.exc = httpx.ReadTimeout("slow")
    else:
        whapi.bad_json = True
    r = admin_client.get(f"{API}/available")
    assert r.status_code == 200
    assert r.json()["items"] == [] and "Couldn't load" in r.json()["error"]
    # An error isn't cached.
    whapi.status, whapi.exc, whapi.bad_json = 200, None, False
    whapi.groups = [{"id": GROUP_A, "name": "A"}]
    assert admin_client.get(f"{API}/available").json()["items"][0]["chat_id"] == GROUP_A


# --- Settings validation --------------------------------------------------------------------------


def test_settings_accept_table_keys_and_reject_unknown(admin_client):
    _add(admin_client, "Deli team")
    r = admin_client.post(DEFS, json={"title": "Deli Opening", "settings": {"whatsapp_group": "deli_team"}})
    assert r.status_code == 200 and r.json()["settings"]["whatsapp_group"] == "deli_team"
    r = admin_client.patch(f"{DEFS}/{SK}", json={"settings": {"whatsapp_group": "deli_team"}})
    assert r.status_code == 200 and r.json()["settings"]["whatsapp_group"] == "deli_team"
    assert admin_client.get(f"/api/checklists/schema/{SK}").json()["settings"]["whatsapp_group"] == "deli_team"

    r = admin_client.post(DEFS, json={"title": "Bad", "settings": {"whatsapp_group": "nope"}})
    assert r.status_code == 400
    assert r.json()["detail"] == (
        'settings.whatsapp_group "nope" isn\'t an active WhatsApp group. '
        "Choose one of: store, kitchen, deli_team - or null for no WhatsApp message"
    )
    assert admin_client.patch(f"{DEFS}/{ST}", json={"settings": {"whatsapp_group": "nope"}}).status_code == 400
    assert admin_client.patch(f"{DEFS}/{ST}", json={"settings": {"whatsapp_group": 3}}).status_code == 400
    assert admin_client.patch(f"{DEFS}/{ST}", json={"settings": {"whatsapp_group": None}}).status_code == 200


def test_turned_off_group_cant_be_chosen_but_its_checklist_still_saves(admin_client):
    _add(admin_client, "Deli team")
    admin_client.patch(f"{DEFS}/{SK}", json={"settings": {"whatsapp_group": "deli_team"}})
    admin_client.patch(f"{API}/deli_team", json={"active": False})
    # Another checklist can't pick it now...
    r = admin_client.patch(f"{DEFS}/{ST}", json={"settings": {"whatsapp_group": "deli_team"}})
    assert r.status_code == 400
    # ...but the checklist that has it can still save its settings (the
    # website sends every setting back).
    r = admin_client.patch(f"{DEFS}/{SK}", json={"settings": {"closing": False, "whatsapp_group": "deli_team"}})
    assert r.status_code == 200 and r.json()["settings"]["whatsapp_group"] == "deli_team"
    # Turning a built-in off also blocks choosing it.
    admin_client.patch(f"{API}/store", json={"active": False})
    assert admin_client.patch(f"{DEFS}/{SK}", json={"settings": {"whatsapp_group": "store"}}).status_code == 400


# --- Resolution -------------------------------------------------------------------------------------


def test_group_id_for_group_resolution(db, admin_client, env_groups):
    resolve = checklist_submission.group_id_for_group
    # Before the table is used (empty, as in a fresh test DB): built-ins from env.
    assert db.query(WhatsAppGroup).count() == 0
    assert resolve("store") == "120363099999999991@g.us"
    assert resolve("kitchen") == ""
    assert resolve("deli_team") == ""
    assert resolve(None) == "" and resolve("") == ""

    _add(admin_client, "Deli team", GROUP_A)
    assert resolve("deli_team") == GROUP_A
    assert resolve("store") == "120363099999999991@g.us"  # chat_id NULL -> env
    assert resolve("unknown") == ""
    # A built-in with its own chat id uses it instead of env.
    db.query(WhatsAppGroup).filter_by(key="kitchen").one().chat_id = GROUP_B
    db.commit()
    assert resolve("kitchen") == GROUP_B
    # Inactive -> "".
    admin_client.patch(f"{API}/deli_team", json={"active": False})
    admin_client.patch(f"{API}/store", json={"active": False})
    assert resolve("deli_team") == "" and resolve("store") == ""
    admin_client.patch(f"{API}/deli_team", json={"active": True})
    assert resolve("deli_team") == GROUP_A


def test_resolution_without_the_table_falls_back_to_env(db, env_groups):
    WhatsAppGroup.__table__.drop(bind=db.get_bind())
    try:
        assert checklist_submission.group_id_for_group("store") == "120363099999999991@g.us"
        assert checklist_submission.group_id_for_group("deli_team") == ""
    finally:
        WhatsAppGroup.__table__.create(bind=db.get_bind())


def test_submission_posts_to_the_table_group(db, admin_client, env_groups, monkeypatch):
    sent = []
    monkeypatch.setattr(checklist_submission, "send_whatsapp_message", lambda text, to=None: sent.append(to))
    _add(admin_client, "Deli team", GROUP_A)
    admin_client.patch(f"{DEFS}/{SK}", json={"settings": {"whatsapp_group": "deli_team"}})

    def entry(n):
        e = ChecklistSubmission(checklist_type=SK, submitted_by_user_id=1, submitted_by_name="asha",
                                answers_json="{}", all_clear=True, answers_hash=f"h-{n}",
                                created_at=dt.datetime(2026, 9, 29, 1, n, tzinfo=dt.timezone.utc))
        db.add(e)
        db.commit()
        return e

    checklist_submission._attempt_whatsapp(db, entry(1))
    assert sent == [GROUP_A]
    # Turned off: nothing is sent; recorded as not set up - never the invoice group.
    admin_client.patch(f"{API}/deli_team", json={"active": False})
    e = entry(2)
    checklist_submission._attempt_whatsapp(db, e)
    assert sent == [GROUP_A]
    assert e.whatsapp_sent is False and "isn't set up" in e.whatsapp_error


def test_nightly_task_fallback_uses_the_table_group(db, admin_client, env_groups):
    _add(admin_client, "Deli team", GROUP_A)
    admin_client.patch(f"{DEFS}/{SK}", json={"settings": {"whatsapp_group": "deli_team"}})
    task = Task(source_type="checklist", source_id=f"{SK}:2026-09-28")
    assert auto_tasks._group_for(task) == GROUP_A
    task = Task(source_type="checklist", source_id=f"{ST}:2026-09-28")
    assert auto_tasks._group_for(task) == "120363099999999991@g.us"


def test_unknown_stored_group_is_kept_and_sends_nothing(db, admin_client):
    """A checklist whose stored key isn't in the table (e.g. a group added
    after a downgrade / restored backup) keeps the value and sends nothing."""
    admin_client.get(DEFS)  # seeds the built-in checklists
    row = db.query(ChecklistDefinition).filter_by(checklist_type=ST).one()
    row.settings_json = json.dumps({"closing": True, "whatsapp_group": "gone_group", "all_as_usual": True})
    db.commit()
    from app.services import checklist_schemas

    checklist_schemas.clear_cache()
    assert checklist_schemas.whatsapp_group(ST) == "gone_group"
    assert checklist_submission.group_id_for_group("gone_group") == ""
    # Junk is still dropped.
    row.settings_json = json.dumps({"whatsapp_group": "Not A Key!"})
    db.commit()
    checklist_schemas.clear_cache()
    assert checklist_schemas.whatsapp_group(ST) is None


# --- Demo guard --------------------------------------------------------------------------------


def test_group_routes_are_not_in_the_demo_allowlist():
    from app.demo_guard import ALLOWLIST

    assert not [k for k in ALLOWLIST if "whatsapp-groups" in k[1]]


# --- Migration 0044 --------------------------------------------------------------------------------


def _alembic(db_file, *args):
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_file}")
    alembic = pathlib.Path(sys.executable).parent / "alembic"
    return subprocess.run([str(alembic), *args], cwd=BACKEND, env=env, check=True, capture_output=True, text=True)


def _tables(db_file):
    con = sqlite3.connect(db_file)
    try:
        return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        con.close()


def test_migration_0044_up_down_and_up_again_on_a_fresh_database(tmp_path):
    db_file = tmp_path / "fresh.db"
    _alembic(db_file, "upgrade", "0043")
    assert "whatsapp_groups" not in _tables(db_file)
    _alembic(db_file, "upgrade", "head")
    con = sqlite3.connect(db_file)
    try:
        migrated = {r[1] for r in con.execute("PRAGMA table_info(whatsapp_groups)")}
        assert migrated == {c.name for c in Base.metadata.tables["whatsapp_groups"].columns}
        rows = con.execute("SELECT key, label, chat_id, builtin, active FROM whatsapp_groups ORDER BY id").fetchall()
        assert rows == [
            ("store", "Main Store staff group", None, 1, 1),
            ("kitchen", "Market Kitchen staff group", None, 1, 1),
        ]
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO whatsapp_groups (key, label, created_at) VALUES ('store', 'x', '2026-10-01')")
        # At or past 0044 (later migrations, e.g. 0045, sit on top of it).
        assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] >= "0044"
    finally:
        con.close()
    _alembic(db_file, "downgrade", "0043")
    assert "whatsapp_groups" not in _tables(db_file)
    _alembic(db_file, "upgrade", "head")
    con = sqlite3.connect(db_file)
    try:
        assert con.execute("SELECT count(*) FROM whatsapp_groups WHERE builtin = 1").fetchone()[0] == 2
    finally:
        con.close()


# --- End to end through the real submit route (Oct 1 pre-release audit) ------------------------


def test_custom_checklist_group_end_to_end_through_submit(db, admin_client, whapi, env_groups, monkeypatch):
    """A checklist made in the builder, a group added from "Available on
    WhatsApp" (Whapi mocked), chosen with the app settings sheet's exact PATCH
    body; POST /submit then posts to that chat id. Turned off: nothing is
    sent. A built-in with chat_id NULL still uses the env id; the kitchen
    built-in with a blank env id sends nothing."""
    sent = []
    monkeypatch.setattr(checklist_submission, "send_whatsapp_message", lambda text, to=None: sent.append((to, text)))
    whapi.groups = [{"id": GROUP_B, "name": "Night crew"}]
    available = admin_client.get(f"{API}/available").json()["items"]
    assert available == [{"chat_id": GROUP_B, "name": "Night crew", "already_added_key": None}]
    group = _add(admin_client, "Night crew", available[0]["chat_id"])
    assert group["key"] == "night_crew"

    created = admin_client.post(DEFS, json={"title": "Night Close"}).json()
    ctype = created["checklist_type"]
    sections = [{"id": "s1", "title": "Close", "description": None, "items": [
        {"id": "q_fryer", "type": "multiple_choice", "title": "Fryer off?", "description": None, "required": True,
         "options": [{"value": "Yes", "issue_tag": None}, {"value": "No", "issue_tag": "not_turned_off"}]},
        {"id": "q_who", "type": "short_text", "title": "Who closed?", "description": None, "required": True},
    ]}]
    assert admin_client.put(f"{DEFS}/{ctype}", json={"sections": sections, "title": "Night Close"}).status_code == 200
    assert admin_client.post(f"{DEFS}/{ctype}/publish", json={}).status_code == 200

    def app_settings(key):
        d = admin_client.get(f"{DEFS}/{ctype}").json()
        body = {"active": d["active"], "settings": {**d["settings"], "closing": False, "whatsapp_group": key,
                                                      "all_as_usual": True}}
        r = admin_client.patch(f"{DEFS}/{ctype}", json=body)
        assert r.status_code == 200, r.text
        return r.json()

    runs = iter(range(1, 100))

    def submit(answer):
        # A different name each time: an identical resubmission is a replay
        # (create_submission returns the first row and sends nothing).
        answers = {"q_fryer": answer, "q_who": f"Closer {next(runs)}"}
        r = admin_client.post(f"/api/checklists/{ctype}/submit", json={"answers": answers})
        assert r.status_code == 200, r.text
        return r.json()

    assert app_settings("night_crew")["settings"]["whatsapp_group"] == "night_crew"
    body = submit("No")
    assert body["all_clear"] is False and "Fryer off?" in body["issue_summary"]
    assert [to for to, _ in sent] == [GROUP_B]
    assert "*Night Close*" in sent[0][1] and "Fryer off?" in sent[0][1]

    # Turned off: still saved, nothing sent, the reason recorded.
    admin_client.patch(f"{API}/night_crew", json={"active": False})
    app_settings("night_crew")  # its other settings still save with the turned-off key
    submit("Yes")
    assert len(sent) == 1
    row = db.query(ChecklistSubmission).filter_by(checklist_type=ctype).order_by(ChecklistSubmission.id.desc()).first()
    db.refresh(row)
    assert row.whatsapp_sent is False and "isn't set up" in row.whatsapp_error

    # Built-ins: the env fallback is unchanged.
    app_settings("store")
    submit("No")
    assert sent[-1][0] == "120363099999999991@g.us"
    app_settings("kitchen")  # env id blank
    before = len(sent)
    submit("Yes")
    assert len(sent) == before
    app_settings(None)
    submit("No")
    assert len(sent) == before
