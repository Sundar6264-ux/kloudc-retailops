"""Pre-App-Store audit fixes (Oct 1, 2026) that don't belong to one feature's
own test file:

- an old-format .xls upload gets a clear 400 ("Save the file as .xlsx or
  .csv"), not pandas' missing-xlrd error;
- the inventory upload's default date and the Ask bot's "today" are the
  store's date (America/New_York), not the server clock's;
- checklist option values are trimmed on save, and answers match options
  ignoring surrounding spaces on definitions published before that.
"""

import datetime as dt
import json

import pytest

from app.models import ChecklistDefinitionVersion, JobRun
from app.routers import checklists as checklists_router
from app.services import checklist_engine as engine
from app.services import checklist_schemas
from app.services.inventory_parser import XLS_NOT_SUPPORTED, InventoryParseError, parse_and_store
from app.services.purchase_log_import import PurchaseLogImportError, import_purchase_log
from tests.conftest import client_for, make_user

# --- .xls uploads ---------------------------------------------------------------------


def test_xls_inventory_upload_says_what_to_do(db, admin_client):
    with pytest.raises(InventoryParseError) as exc:
        parse_and_store(db, "Retail items.XLS", b"\xd0\xcf\x11\xe0 old excel", dt.date(2026, 10, 1))
    assert str(exc.value) == XLS_NOT_SUPPORTED
    assert "Save the file as .xlsx or .csv and upload again" in XLS_NOT_SUPPORTED

    r = admin_client.post(
        "/api/inventory/upload",
        files={"file": ("items.xls", b"\xd0\xcf\x11\xe0 old excel", "application/vnd.ms-excel")},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == XLS_NOT_SUPPORTED


def test_xls_purchase_log_import_says_what_to_do(db):
    with pytest.raises(PurchaseLogImportError) as exc:
        import_purchase_log(db, "purchases.xls", b"\xd0\xcf\x11\xe0 old excel", None, None)
    assert "Save the file as .xlsx or .csv" in str(exc.value)


# --- The store's date, not the server's --------------------------------------------------


def test_inventory_upload_defaults_to_the_store_date(db, admin_client, monkeypatch):
    from app.routers import inventory as inventory_router

    store_day = dt.date(2026, 10, 1)
    monkeypatch.setattr(inventory_router, "restaurant_today", lambda: store_day)
    r = admin_client.post("/api/inventory/upload", files={"file": ("items.xls", b"x", "application/vnd.ms-excel")})
    assert r.status_code == 400  # the .xls refusal - but the job row records the date used
    job = db.query(JobRun).order_by(JobRun.id.desc()).first()
    assert job.business_date == store_day


def test_ask_bot_prompt_uses_the_store_date(monkeypatch):
    from app.services import ask_bot

    seen = {}

    class _Runner:
        def __iter__(self):
            return iter([])

    class _Beta:
        class messages:  # noqa: N801 - mirrors the SDK's attribute name
            @staticmethod
            def tool_runner(**kw):
                seen["system"] = kw["system"]
                return _Runner()

    class _Client:
        beta = _Beta()

    monkeypatch.setattr(ask_bot, "get_anthropic_client", lambda: _Client())
    monkeypatch.setattr(ask_bot, "_classify_complexity", lambda client, q: "SIMPLE")
    monkeypatch.setattr(ask_bot, "_build_tools", lambda db, include_sales=True: [])
    monkeypatch.setattr(ask_bot, "restaurant_today", lambda: dt.date(2031, 2, 3))
    try:
        ask_bot.ask(None, "what sold today?")
    except Exception:  # noqa: BLE001 - only the prompt matters here
        pass
    assert "2031-02-03" in seen["system"]


# --- Checklist option values with stray spaces ----------------------------------------------


def _stale_schema():
    """An items-form schema as stored BEFORE option values were trimmed."""
    return {
        "format": "items",
        "sections": [
            {"id": "s1", "items": [
                {"id": "q_door", "type": "multiple_choice", "title": "Back door locked?", "required": True,
                 "options": [{"value": "Yes ", "issue_tag": None}, {"value": " No", "issue_tag": "not_done"}],
                 "go_to_section": {"Yes ": "submit", " No": "s2"}},
                {"id": "q_who", "type": "dropdown", "title": "Closer", "required": True,
                 "options": [{"value": "RK ", "issue_tag": None}]},
                {"id": "q_clean", "type": "checkboxes", "title": "Cleaned", "required": False,
                 "options": [{"value": "Counter ", "issue_tag": None}, {"value": "Glass", "issue_tag": None}]},
                {"id": "q_grid", "type": "multiple_choice_grid", "title": "Fridges", "required": True,
                 "rows": [{"id": "r1", "title": "Fridge 1"}],
                 "options": [{"value": "OK ", "issue_tag": None}, {"value": "Warm", "issue_tag": "not_done"}]},
            ]},
            {"id": "s2", "items": [
                {"id": "q_why", "type": "short_text", "title": "Why not?", "required": True},
            ]},
        ],
    }


def test_answers_match_options_ignoring_surrounding_spaces():
    schema = _stale_schema()
    answers = {"q_door": "Yes", "q_who": "RK", "q_clean": '["Counter"]', "q_grid.r1": "OK"}
    engine.validate_answers(schema, answers)  # no "Invalid answer"
    canon = engine.canonical_answers(schema, answers)
    assert canon == {"q_door": "Yes ", "q_who": "RK ", "q_clean": '["Counter "]', "q_grid.r1": "OK "}
    assert answers["q_door"] == "Yes"  # the caller's dict isn't changed
    # Flags and branching see the definition's own value.
    assert engine.compute_status(schema, canon) == (True, None)
    no = engine.canonical_answers(schema, {"q_door": "No", "q_who": "RK", "q_grid.r1": "OK"})
    assert no["q_door"] == " No"
    assert engine.visited_sections(schema, no) == [0, 1]
    with pytest.raises(ValueError, match="Why not"):
        engine.validate_answers(schema, no)
    # Exact matches are untouched; real mistakes are still refused.
    assert engine.canonical_answers(schema, {"q_door": "Yes "}) == {"q_door": "Yes "}
    with pytest.raises(ValueError, match="Invalid answer 'Maybe'"):
        engine.validate_answers(schema, dict(answers, q_door="Maybe"))


def test_option_values_are_trimmed_when_saved():
    schema = engine.normalize_schema({"sections": _stale_schema()["sections"]})
    door, who, clean, grid = schema["sections"][0]["items"]
    assert [o["value"] for o in door["options"]] == ["Yes", "No"]
    assert door["go_to_section"] == {"Yes": "submit", "No": "s2"}
    assert [o["value"] for o in who["options"]] == ["RK"]
    assert [o["value"] for o in clean["options"]] == ["Counter", "Glass"]
    assert [o["value"] for o in grid["options"]] == ["OK", "Warm"]
    assert engine.validate_schema(schema) == []
    # "Yes" and "Yes " are the same option once trimmed: listed twice.
    dup = engine.normalize_schema({"sections": [{"id": "s", "items": [
        {"id": "q", "type": "dropdown", "title": "T", "options": [{"value": "Yes"}, {"value": "Yes "}]},
    ]}]})
    assert any("listed twice" in p for p in engine.validate_schema(dup))


def test_app_submission_against_a_published_definition_with_spaces(db, admin_client, monkeypatch):
    monkeypatch.setattr(checklists_router, "send_checklist_notification", lambda sid: None)
    r = admin_client.post("/api/checklists/admin/definitions", json={
        "title": "Door Check", "settings": {"whatsapp_group": None},
    })
    ctype = r.json()["checklist_type"]
    sections = _stale_schema()["sections"]
    r = admin_client.post(f"/api/checklists/admin/definitions/{ctype}/publish", json={"sections": sections})
    assert r.status_code == 200, r.text
    published = admin_client.get(f"/api/checklists/schema/{ctype}").json()
    assert [o["value"] for o in published["sections"][0]["items"][0]["options"]] == ["Yes", "No"]

    # A version stored before trimming existed (spaces kept in the DB row).
    from app.models import ChecklistDefinition

    definition = db.query(ChecklistDefinition).filter_by(checklist_type=ctype).one()
    row = db.query(ChecklistDefinitionVersion).filter_by(definition_id=definition.id).one()
    row.schema_json = json.dumps(_stale_schema())
    db.commit()
    checklist_schemas.clear_cache()

    staff = client_for(db, make_user(db, "closer", features=["daily_checklists"]))
    r = staff.post(f"/api/checklists/{ctype}/submit", json={"answers": {
        "q_door": "Yes", "q_who": "RK", "q_clean": '["Counter"]', "q_grid.r1": "OK",
    }})
    assert r.status_code == 200, r.text
    assert r.json()["all_clear"] is True
    stored = staff.get(f"/api/checklists/{r.json()['id']}").json()
    assert stored["answers"]["q_door"] == "Yes "  # the definition's own spelling
