"""Checklist builder (Sep 30, docs/CHECKLIST_BUILDER_CONTRACT.md): checklists
in the database, editable by admins, every Google Forms item type, versions,
and - most important - the three existing checklists behaving exactly as
before for the released app (build 5) and for stored submissions.

Nothing here reaches WhatsApp: sends are mocked or the checklist has no group.
"""

import copy
import datetime as dt
import hashlib
import json
import os
import pathlib
import random
import shutil
import sqlite3
import subprocess
import sys
import time
import zoneinfo

import pytest

from app.config import get_settings
from app.db import Base
from app.models import ChecklistDefinition, ChecklistSubmission
from app.routers import checklists as checklists_router
from app.services import checklist_admin, checklist_builtin, checklist_schemas, checklist_submission
from app.services import checklist_engine as engine
from app.services.notification_triggers import closing_checklist_alerts
from app.team import auto_tasks
from app.team import tasks_service as tasks_svc
from app.team.models import Team
from app.team.tasks_models import Task

from tests import checklist_legacy_reference as old
from tests.conftest import add_member, client_for, make_user
from tests.store_fixtures import private_data

NY = zoneinfo.ZoneInfo("America/New_York")
BACKEND = pathlib.Path(__file__).resolve().parents[1]

ST, SK, FSA = "store_closing", "kitchen_closing", "kitchen_fsa"
ORIGINAL = {
    (ST, 1): checklist_builtin.STORE_CLOSING,
    (SK, 1): checklist_builtin.KITCHEN_CLOSING,
    (FSA, 1): checklist_builtin.KITCHEN_FSA_CLOSING_V1,
    (FSA, 2): checklist_builtin.KITCHEN_FSA_CLOSING,
}


# --- Helpers ------------------------------------------------------------------------------


def deep_superset(new, old_value, path="$"):
    """`new` has everything `old_value` has, unchanged (extra dict keys are
    fine - build 5 ignores them; lists must match item for item)."""
    if isinstance(old_value, dict):
        assert isinstance(new, dict), f"{path}: not an object"
        for key, value in old_value.items():
            assert key in new, f"{path}.{key} missing"
            deep_superset(new[key], value, f"{path}.{key}")
    elif isinstance(old_value, list):
        assert isinstance(new, list) and len(new) == len(old_value), f"{path}: list differs"
        for i, (a, b) in enumerate(zip(new, old_value)):
            deep_superset(a, b, f"{path}[{i}]")
    else:
        assert new == old_value and type(new) is type(old_value), f"{path}: {new!r} != {old_value!r}"


def _is_str(v, nullable=False):
    return isinstance(v, str) or (nullable and v is None)


def build5_parse(schema: dict) -> list:
    """Python port of how app build 5 (the origin app, commit 9985842,
    checklist_schema.dart) casts a schema - an assert fails exactly where
    the Dart `as` cast would throw. Returns build 5's allRequiredIds."""
    assert _is_str(schema["checklist_type"]) and _is_str(schema["title"])
    assert _is_str(schema.get("description"), nullable=True)
    required = []

    def rows(value):
        for r in value:
            assert _is_str(r["id"]) and _is_str(r["label"])
        return value

    def options(value):
        for o in value:
            assert _is_str(o["value"]) and _is_str(o.get("issue_tag"), nullable=True)
        return value

    for section in schema["sections"]:
        assert _is_str(section.get("title"), nullable=True)
        assert _is_str(section.get("description"), nullable=True)
        grids = []
        if section.get("grid") is not None:
            grids.append(section["grid"])
        grids.extend(section.get("grids") or [])
        for g in grids:
            assert _is_str(g["title"])
            options(g["options"])
            required.extend(r["id"] for r in rows(g["rows"]))
        for q in section.get("questions") or []:
            assert _is_str(q["id"]) and _is_str(q["label"])
            if q.get("options") is not None:
                options(q["options"])
            assert q.get("required") in (None, True, False)
            assert _is_str(q.get("input"), nullable=True) and _is_str(q.get("summary_label"), nullable=True)
            if q.get("required"):
                required.append(q["id"])
        for group in section.get("checkbox_groups") or []:
            assert _is_str(group["id"]) and _is_str(group["title"])
            assert group.get("required") in (None, True, False)
            rows(group["items"])
            if group.get("required"):
                required.append(group["id"])
    for key in ("issues_field", "confirmation"):
        field = schema.get(key)
        if field is not None:
            assert _is_str(field["id"]) and _is_str(field["label"])
            assert isinstance(field["required"], bool)
            assert _is_str(field.get("hint"), nullable=True)
            if field["required"]:
                required.append(field["id"])
    return required


def assert_whatsapp_matches_summary(text: str, all_clear: bool, summary) -> None:
    """The WhatsApp text lists exactly the flagged answers of the stored
    issue_summary (grouped, without the "(Section)" suffix): one ❌ line per
    summary line, the issues field's text under "Note from staff"."""
    if all_clear:
        assert "*All done - no issues.*" in text and "❌" not in text
        return
    summary_lines = [l[4:] for l in summary.split("\n") if l.startswith("  - ")]
    crosses = [l[2:] for l in text.split("\n") if l.startswith("❌ ")]
    notes = text.split("*Note from staff*\n")[1].split("\n\n") if "*Note from staff*" in text else []
    assert len(crosses) + len(notes) == len(summary_lines), (text, summary)
    for cross in crosses:
        name = cross.split(": ")[0]
        assert any(l.startswith(name) for l in summary_lines), (cross, summary)


def first_error(fn, *args):
    try:
        fn(*args)
    except ValueError as exc:
        return str(exc)
    return None


def random_answers(schema: dict, rng: random.Random) -> dict:
    """Plausible and implausible answers for a legacy schema: most rows
    answered, some missing / bogus / blank."""
    answers = {}
    for g in old._iter_grids(schema):
        for row in g["rows"]:
            r = rng.random()
            if r < 0.03:
                continue
            answers[row["id"]] = "bogus" if r < 0.05 else ("" if r < 0.06 else rng.choice(g["options"])["value"])
    for q in old._iter_standalone_questions(schema):
        r = rng.random()
        if r < 0.1:
            continue
        answers[q["id"]] = "" if r < 0.15 else ("Bob" if r < 0.2 else rng.choice(q["options"])["value"])
    for group in old._iter_checkbox_groups(schema):
        for item in group["items"]:
            r = rng.random()
            if r < 0.55:
                answers[item["id"]] = "Done"
            elif r < 0.58:
                answers[item["id"]] = "done"
        if rng.random() < 0.1:
            for item in group["items"]:
                answers.pop(item["id"], None)
    if schema.get("issues_field"):
        text = rng.choice([None, "", "  ", "None", "no.", "All good!", "N/A", "Fryer 2 broke", "TEST", "nothing unusual"])
        if text is not None:
            answers[schema["issues_field"]["id"]] = text
    if schema.get("confirmation"):
        value = rng.choice([None, "", schema["confirmation"]["label"], "x"])
        if value is not None:
            answers[schema["confirmation"]["id"]] = value
    return answers


def good_answers(schema: dict) -> dict:
    answers = {}
    for g in old._iter_grids(schema):
        for row in g["rows"]:
            answers[row["id"]] = g["options"][0]["value"]
    for q in old._iter_standalone_questions(schema):
        answers[q["id"]] = q["options"][0]["value"]
    for group in old._iter_checkbox_groups(schema):
        for item in group["items"]:
            answers[item["id"]] = "Done"
    if schema.get("issues_field"):
        answers[schema["issues_field"]["id"]] = "None"
    if schema.get("confirmation"):
        answers[schema["confirmation"]["id"]] = schema["confirmation"]["label"]
    return answers


def meta(checklist_type):
    d = checklist_schemas.get_definition(checklist_type)
    return d.meta()


@pytest.fixture
def no_send(monkeypatch):
    queued = []
    monkeypatch.setattr(checklists_router, "send_checklist_notification", lambda sid: queued.append(sid))
    return queued


@pytest.fixture
def checker(db):
    return make_user(db, "asha", features=["daily_checklists"])


@pytest.fixture
def checker_client(db, checker):
    return client_for(db, checker)


# --- The frozen originals -----------------------------------------------------------------


def test_builtin_schemas_are_frozen():
    """checklist_builtin holds the schemas exactly as at 854165d (prod), with
    only the KloudC RetailOps final pass's neutral ids and titles - never edit
    them; stored submissions render with them."""
    expected = {
        "STORE_CLOSING": "758fdc1412faa7eca0f0b0ea8c565af3bcf4be43aa095b38a5886b30f33ed7e7",
        "KITCHEN_CLOSING": "8243182a0145d4b34def0afa8741932f8aba4c9a4513a2ad524f66a6ba33ba72",
        "KITCHEN_FSA_CLOSING_V1": "9fc9243dd8e71592c1f7df653d4c4e0d5a4d6a6fda7c7587bf89cf0659c66830",
    }
    for name, digest in expected.items():
        value = getattr(checklist_builtin, name)
        assert hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest() == digest, name
    fsa_v2 = checklist_builtin.KITCHEN_FSA_CLOSING
    assert fsa_v2["sections"] == checklist_builtin.KITCHEN_FSA_CLOSING_V1["sections"][1:]
    assert "chef_name" not in json.dumps(fsa_v2)


# --- Legacy emission (build 5) ----------------------------------------------------------


@pytest.mark.parametrize("seeded_db", [False, True])
def test_seeded_checklists_are_served_exactly_as_before(db, seeded_db):
    """Today's served JSON is contained unchanged in the new one (additive
    keys only), from the built-in fallback and from the seeded tables."""
    if seeded_db:
        assert checklist_admin.ensure_seeded(db)
    assert checklist_schemas.registry().source == ("db" if seeded_db else "builtin")
    for (ctype, version), original in ORIGINAL.items():
        served = checklist_schemas.get_version_schema(ctype, version)
        deep_superset(served, original)
        assert served["version"] == version
        assert "min_app_build" not in served
        build5_parse(served)
    # The current versions: FSA is version 2 (no Chef Name).
    assert [checklist_schemas.get_schema(t)["version"] for t in (ST, SK, FSA)] == [1, 1, 2]
    served = checklist_schemas.get_schema(ST)
    assert served["short_name"] == "Main Store"
    assert served["settings"] == {"closing": True, "whatsapp_group": "store", "all_as_usual": True}
    assert checklist_schemas.get_schema(SK)["settings"]["whatsapp_group"] == "kitchen"
    assert checklist_schemas.get_schema(FSA)["settings"]["whatsapp_group"] == "kitchen"
    # Every section also carries its generic `items` (build 6 renders those).
    for t in (ST, SK, FSA):
        for section in checklist_schemas.get_schema(t)["sections"]:
            assert section["id"] and isinstance(section["items"], list) and section["items"]


@pytest.mark.parametrize("key", list(ORIGINAL))
def test_items_form_round_trip_emits_the_same_legacy_blocks(key):
    """An admin opening a seeded checklist and publishing it (now items form)
    still gives build 5 the same grids / questions / boxes / fields."""
    ctype, version = key
    original = ORIGINAL[key]
    items = engine.normalize_schema(engine.legacy_to_items(original))
    assert engine.validate_schema(items) == []
    served = engine.build_served(
        {"checklist_type": ctype, "title": original["title"], "short_name": original.get("short_name"),
         "description": original["description"], "settings": {}},
        version + 1,
        items,
    )
    deep_superset(served, original)
    assert "min_app_build" not in served
    assert build5_parse(served) == build5_parse(original)


def test_items_are_answer_compatible_with_legacy_keys():
    items = engine.legacy_to_items(checklist_builtin.KITCHEN_FSA_CLOSING)
    keys = set()
    for _, item in engine.iter_items(items):
        keys.update(engine.answer_keys(item))
    assert keys == old.all_row_ids(checklist_builtin.KITCHEN_FSA_CLOSING) | {
        f"c{g}_{i}" for g, n in ((0, 6), (1, 10), (2, 2)) for i in range(n)
    } | {"issues_text"}


# --- Regression: stored answers, old code vs new ------------------------------------------


def _engines(ctype, version):
    """(legacy dict, items view of the stored version, items form after an
    admin round trip) - all three must behave identically."""
    original = ORIGINAL[(ctype, version)]
    stored_view = checklist_schemas.items_schema(ctype, version)
    round_trip = engine.normalize_schema(engine.legacy_to_items(original))
    return original, stored_view, round_trip


def test_real_stored_answers_give_the_same_results():
    """Every checklist submission in the dev DB copy (synced from prod),
    through the old code and the new engine: same validation, all_clear and
    issue_summary as stored, and the same WhatsApp text."""
    rows = json.loads(private_data("checklist_real_answers_2026-09-30.json").read_text())["submissions"]
    assert rows
    for row in rows:
        ctype, answers = row["checklist_type"], row["answers"]
        original, stored_view, round_trip = _engines(ctype, 1)
        assert first_error(old.validate_answers, original, answers) is None
        assert old.compute_status(original, answers) == (row["all_clear"], row["issue_summary"])
        for schema in (stored_view, round_trip):
            assert first_error(engine.validate_answers, schema, answers) is None
            assert engine.compute_status(schema, answers) == (row["all_clear"], row["issue_summary"])
            assert engine.summary_details(schema, answers) == old.summary_details(original, answers)
        # Through the registry as the submit path uses it.
        checklist_schemas.validate_answers(ctype, answers)
        assert checklist_schemas.compute_status(ctype, answers) == (row["all_clear"], row["issue_summary"])
        created = dt.datetime(2026, 9, 28, 1, 9, tzinfo=dt.timezone.utc)
        entry = ChecklistSubmission(
            checklist_type=ctype, submitted_by_user_id=1, submitted_by_name="asha",
            answers_json=json.dumps(answers), all_clear=row["all_clear"],
            issue_summary=row["issue_summary"], answers_hash="h", created_at=created,
        )
        assert_whatsapp_matches_summary(
            checklist_submission._whatsapp_text(entry), row["all_clear"], row["issue_summary"]
        )


@pytest.mark.parametrize("key", list(ORIGINAL))
def test_generated_answers_give_the_same_results(key):
    """Thousands of random (valid and invalid) answer sets per checklist:
    the first validation error, all_clear, issue_summary and details match
    the old code exactly - for the stored version and after an admin round
    trip through the editor."""
    ctype, version = key
    original, stored_view, round_trip = _engines(ctype, version)
    rng = random.Random(f"{ctype}-{version}-answers")
    samples = [good_answers(original)] + [random_answers(original, rng) for _ in range(1500)]
    valid = 0
    for answers in samples:
        expected_error = first_error(old.validate_answers, original, answers)
        expected_status = old.compute_status(original, answers)
        valid += expected_error is None
        for schema in (stored_view, round_trip):
            assert first_error(engine.validate_answers, schema, answers) == expected_error, answers
            assert engine.compute_status(schema, answers) == expected_status, answers
            assert engine.summary_details(schema, answers) == old.summary_details(original, answers)
    assert valid > 5  # the sample covers accepted submissions too


def test_old_fsa_submission_still_shows_its_chef(db, admin_client):
    """Stored before Sep 30 (definition_version NULL = 1): history, detail
    and the WhatsApp text keep "Chef: JL"; a new one has no chef line."""
    answers = good_answers(checklist_builtin.KITCHEN_FSA_CLOSING_V1)
    assert answers["chef_name"] == "Avery"
    old_row = ChecklistSubmission(
        checklist_type=FSA, submitted_by_user_id=1, submitted_by_name="asha",
        answers_json=json.dumps(answers), all_clear=True, answers_hash="h1",
        created_at=dt.datetime(2026, 9, 29, 1, 0, tzinfo=dt.timezone.utc),
    )
    db.add(old_row)
    db.commit()
    body = admin_client.get(f"/api/checklists/{old_row.id}").json()
    assert body["details"] == [{"label": "Chef", "value": "Avery"}]
    assert body["definition_version"] == 1
    assert "\nasha · Sep 28, 9:00 PM\nChef: Avery\n\n" in checklist_submission._whatsapp_text(old_row)
    # Its version renders with the chef question.
    schema = admin_client.get(f"/api/checklists/schema/{FSA}?version=1").json()
    assert schema["sections"][0]["questions"][0]["id"] == "chef_name"


def test_new_fsa_submission_has_no_chef(db, checker_client, no_send):
    answers = good_answers(checklist_builtin.KITCHEN_FSA_CLOSING)
    r = checker_client.post(f"/api/checklists/{FSA}/submit", json={"answers": answers})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["details"] == [] and body["definition_version"] == 2 and body["all_clear"] is True
    entry = db.get(ChecklistSubmission, body["id"])
    assert entry.definition_version == 2
    text = checklist_submission._whatsapp_text(entry)
    assert "Chef" not in text and "\nasha · " in text and text.endswith("\n\n*All done - no issues.*")


# --- Every item type ----------------------------------------------------------------------


def _one(item, **extra):
    schema = engine.normalize_schema({"sections": [{"id": "s1", "items": [item]}]})
    assert engine.validate_schema(schema) == [], engine.validate_schema(schema)
    return schema


def _err(schema, answers):
    return first_error(engine.validate_answers, schema, answers)


def test_short_text_and_validation_rules():
    s = _one({"id": "name", "type": "short_text", "title": "Name", "required": True})
    assert _err(s, {}) == "Missing answer for required item: 'Name'"
    assert _err(s, {"name": "  "}) == "Missing answer for required item: 'Name'"
    assert _err(s, {"name": "Asha"}) is None
    temp = _one({"id": "t", "type": "short_text", "title": "Temp", "required": True,
                 "validation": {"kind": "number", "rule": "lte", "value": 40}})
    assert _err(temp, {"t": "38"}) is None
    assert _err(temp, {"t": "41"}) == "Temp: must be a number less than or equal to 40"
    assert _err(temp, {"t": "cold"}) == "Temp: must be a number less than or equal to 40"
    between = _one({"id": "t", "type": "short_text", "title": "Temp", "required": False,
                    "validation": {"kind": "number", "rule": "between", "value": [35, 40], "message": "35-40 F please"}})
    assert _err(between, {}) is None  # optional and empty: no rule check
    assert _err(between, {"t": "36.5"}) is None
    assert _err(between, {"t": "50"}) == "Temp: 35-40 F please"
    whole = _one({"id": "n", "type": "short_text", "title": "Count", "validation": {"kind": "number", "rule": "whole_number"}})
    assert _err(whole, {"n": "3"}) is None and _err(whole, {"n": "3.5"}) == "Count: must be a whole number"
    email = _one({"id": "e", "type": "short_text", "title": "Email", "validation": {"kind": "text", "rule": "email"}})
    assert _err(email, {"e": "a@b.co"}) is None and _err(email, {"e": "nope"}) == "Email: must be an email address"
    url = _one({"id": "u", "type": "short_text", "title": "Link", "validation": {"kind": "text", "rule": "url"}})
    assert _err(url, {"u": "https://mainstore.example/x"}) is None and _err(url, {"u": "not a url"})
    contains = _one({"id": "c", "type": "short_text", "title": "Code", "validation": {"kind": "text", "rule": "contains", "value": "ST-"}})
    assert _err(contains, {"c": "ST-12"}) is None and _err(contains, {"c": "12"}) == "Code: must contain 'ST-'"
    regex = _one({"id": "r", "type": "short_text", "title": "Code", "validation": {"kind": "regex", "rule": "matches", "value": "[A-Z]{2}\\d+"}})
    assert _err(regex, {"r": "AB12"}) is None and _err(regex, {"r": "ab12"}) == "Code: doesn't match the required format"
    length = _one({"id": "l", "type": "short_text", "title": "Note", "validation": {"kind": "length", "rule": "max_chars", "value": 5}})
    assert _err(length, {"l": "12345"}) is None and _err(length, {"l": "123456"}) == "Note: must be at most 5 characters"


def test_paragraph_flags_and_nothing_to_report():
    s = _one({"id": "p", "type": "paragraph", "title": "Anything unusual?", "required": True,
              "flag_if_answered": True, "nothing_to_report_ok": True,
              "validation": {"kind": "length", "rule": "min_chars", "value": 2}})
    assert _err(s, {"p": "x"}) == "Anything unusual?: must be at least 2 characters"
    assert engine.compute_status(s, {"p": "None"}) == (True, None)
    assert engine.compute_status(s, {"p": "Freezer 3 is loud"}) == (
        False, "Issue:\n  - Anything unusual?: Freezer 3 is loud"
    )
    plain = _one({"id": "p", "type": "paragraph", "title": "Notes"})
    assert engine.compute_status(plain, {"p": "Freezer 3 is loud"}) == (True, None)
    tagged = _one({"id": "p", "type": "paragraph", "title": "Notes", "flag_if_answered": True, "flag_tag": "cleanup"})
    assert engine.compute_status(tagged, {"p": "No"}) == (False, "Cleanup Not Completed:\n  - Notes: No")


def test_multiple_choice_other_and_issue_tags():
    s = _one({"id": "q", "type": "multiple_choice", "title": "Fryer oil", "required": True, "other_option": True,
              "options": [{"value": "Changed"}, {"value": "Not changed", "issue_tag": "cleanup"}]})
    assert _err(s, {}) == "Missing answer for required item: 'Fryer oil'"
    assert _err(s, {"q": "Changed"}) is None
    assert _err(s, {"q": "Other: filtered only"}) is None
    assert _err(s, {"q": "Other: "}) == "Invalid answer 'Other: ' for 'Fryer oil'"
    assert _err(s, {"q": "Maybe"}) == "Invalid answer 'Maybe' for 'Fryer oil'"
    assert engine.compute_status(s, {"q": "Other: filtered only"}) == (True, None)
    assert engine.compute_status(s, {"q": "Not changed"}) == (False, "Cleanup Not Completed:\n  - Fryer oil")


def test_dropdown_and_summary_label():
    s = _one({"id": "who", "type": "dropdown", "title": "Manager on duty", "required": True, "summary_label": "Manager",
              "options": [{"value": "JL"}, {"value": "Dana"}]})
    assert _err(s, {"who": "JL"}) is None
    assert _err(s, {"who": "Other: Bob"}) == "Invalid answer 'Other: Bob' for 'Manager on duty'"
    assert engine.summary_details(s, {"who": "JL"}) == [{"label": "Manager", "value": "JL"}]


def test_checkboxes():
    s = _one({"id": "cb", "type": "checkboxes", "title": "Cleaned", "required": True, "other_option": True,
              "options": [{"value": "Glass"}, {"value": "Printer table"}, {"value": "Spill", "issue_tag": "issue"}],
              "unticked_tag": "not_done", "validation": {"kind": "checkbox_count", "rule": "at_least", "value": 2}})
    assert _err(s, {}) == "Missing answer for required item: 'Cleaned'"
    assert _err(s, {"cb": "[]"}) == "Missing answer for required item: 'Cleaned'"
    assert _err(s, {"cb": "Glass"}) == "Invalid answer 'Glass' for 'Cleaned'"
    assert _err(s, {"cb": '["Glass","Glass"]'}) == "Invalid answer '[\"Glass\",\"Glass\"]' for 'Cleaned'"
    assert _err(s, {"cb": '["Glass"]'}) == "Cleaned: tick at least 2"
    assert _err(s, {"cb": '["Glass","Other: mop"]'}) is None
    assert _err(s, {"cb": '["Glass","Nope"]'}) == "Invalid answer '[\"Glass\",\"Nope\"]' for 'Cleaned'"
    assert engine.compute_status(s, {"cb": '["Glass","Printer table"]'}) == (
        False, "Not Done:\n  - Spill (Cleaned)"
    )
    assert engine.compute_status(s, {"cb": '["Glass","Printer table","Spill"]'}) == (
        False, "Issue:\n  - Spill (Cleaned)"
    )


def test_linear_scale_and_rating():
    s = _one({"id": "sc", "type": "linear_scale", "title": "Busy", "required": True,
              "scale": {"min": 0, "max": 5, "min_label": "Quiet", "max_label": "Slammed"}})
    assert [_err(s, {"sc": v}) is None for v in ("0", "5", "6", "-1", "x", "")] == [True, True, False, False, False, False]
    r = _one({"id": "r", "type": "rating", "title": "Cleanliness", "required": True, "scale": {"max": 5}})
    assert [_err(r, {"r": v}) is None for v in ("1", "5", "0", "6")] == [True, True, False, False]


def test_multiple_choice_grid():
    s = _one({"id": "g", "type": "multiple_choice_grid", "title": "Equipment", "required": True, "one_per_column": False,
              "options": [{"value": "Off"}, {"value": "On", "issue_tag": "not_turned_off"}],
              "rows": [{"id": "stove", "title": "Stove"}, {"id": "oven", "title": "Oven"}]})
    assert engine.answer_keys(s["sections"][0]["items"][0]) == ["g.stove", "g.oven"]
    assert _err(s, {"g.stove": "Off"}) == "Missing answer for required item: 'Oven'"
    assert _err(s, {"g.stove": "Off", "g.oven": "Hot"}) == "Invalid answer 'Hot' for 'Oven'"
    assert engine.compute_status(s, {"g.stove": "Off", "g.oven": "On"}) == (False, "Not Turned Off:\n  - Oven (Equipment)")
    col = _one({"id": "g", "type": "multiple_choice_grid", "title": "Shifts", "one_per_column": True,
                "options": [{"value": "AM"}, {"value": "PM"}], "rows": [{"id": "a", "title": "Asha"}, {"id": "b", "title": "Ravi"}]})
    assert _err(col, {"g.a": "AM", "g.b": "PM"}) is None
    assert _err(col, {"g.a": "AM", "g.b": "AM"}) == "Shifts: 'AM' can only be picked for one row"


def test_checkbox_grid():
    s = _one({"id": "g", "type": "checkbox_grid", "title": "Restocked", "required": True,
              "options": [{"value": "Cups"}, {"value": "Lids"}, {"value": "Out", "issue_tag": "low_stock"}],
              "rows": [{"id": "front", "title": "Front"}, {"id": "back", "title": "Back"}]})
    assert _err(s, {"g.front": '["Cups"]', "g.back": "[]"}) == "Missing answer for required item: 'Back'"
    assert _err(s, {"g.front": '["Cups"]', "g.back": '["Plates"]'}) == "Invalid answer '[\"Plates\"]' for 'Back'"
    answers = {"g.front": '["Cups","Lids"]', "g.back": '["Out"]'}
    assert _err(s, answers) is None
    assert engine.compute_status(s, answers) == (False, "Low Stock:\n  - Back: Out (Restocked)")


def test_date_time_and_file_upload():
    d = _one({"id": "d", "type": "date", "title": "Delivery date", "required": True})
    assert [_err(d, {"d": v}) is None for v in ("2026-09-30", "2026-02-30", "30/09/2026")] == [True, False, False]
    t = _one({"id": "t", "type": "time", "title": "Closed at", "required": True})
    assert [_err(t, {"t": v}) is None for v in ("21:30", "00:00", "24:00", "9:30")] == [True, True, False, False]
    f = _one({"id": "f", "type": "file_upload", "title": "Photo of the cooler", "required": True,
              "file": {"max_files": 2, "types": ["image"]}})
    assert _err(f, {"f": "[]"}) == "Missing answer for required item: 'Photo of the cooler'"
    assert _err(f, {"f": "[4, 5]"}) is None
    assert _err(f, {"f": "[4, 5, 6]"}) == "Photo of the cooler: at most 2 photo(s)"
    assert _err(f, {"f": '["abc"]'}) == "Invalid answer '[\"abc\"]' for 'Photo of the cooler'"
    assert engine.media_ids(f, {"f": '[4, "5"]'}) == [4, 5]


def test_display_only_items_need_no_answer():
    s = engine.normalize_schema({"sections": [{"id": "s1", "items": [
        {"id": "h", "type": "title_text", "title": "Before you start", "required": True},
        {"id": "img", "type": "image", "title": None, "image_url": "https://example.com/a.png"},
        {"id": "q", "type": "short_text", "title": "Name"},
    ]}]})
    assert engine.validate_schema(s) == []
    assert _err(s, {}) is None
    assert s["sections"][0]["items"][0]["required"] is False


# --- Branching ----------------------------------------------------------------------------


def _branching_schema():
    return engine.normalize_schema({"sections": [
        {"id": "s1", "title": "Start", "items": [
            {"id": "open", "type": "multiple_choice", "title": "Kitchen open today?", "required": True,
             "options": [{"value": "Yes"}, {"value": "No"}, {"value": "Half day"}],
             "go_to_section": {"No": "s3", "Half day": "submit"}},
        ]},
        {"id": "s2", "title": "Kitchen", "next_section": "s4", "items": [
            {"id": "fryer", "type": "multiple_choice", "title": "Fryer off?", "required": True,
             "options": [{"value": "Yes"}, {"value": "No", "issue_tag": "not_turned_off"}]},
        ]},
        {"id": "s3", "title": "Closed", "items": [
            {"id": "why", "type": "short_text", "title": "Why closed?", "required": True},
        ]},
        {"id": "s4", "title": "End", "items": [
            {"id": "sign", "type": "short_text", "title": "Initials", "required": True},
        ]},
    ]})


def test_branching_skips_sections_and_their_requirements():
    s = _branching_schema()
    assert engine.validate_schema(s) == []
    assert engine.visited_sections(s, {"open": "Yes"}) == [0, 1, 3]
    assert engine.visited_sections(s, {"open": "No"}) == [0, 2, 3]
    assert engine.visited_sections(s, {"open": "Half day"}) == [0]
    assert engine.visited_sections(s, {}) == [0, 1, 3]
    assert _err(s, {"open": "Half day"}) is None
    assert _err(s, {"open": "No"}) == "Missing answer for required item: 'Why closed?'"
    assert _err(s, {"open": "No", "why": "Holiday", "sign": "AK"}) is None
    assert _err(s, {"open": "Yes", "sign": "AK"}) == "Missing answer for required item: 'Fryer off?'"
    # Answers in a skipped section are neither required nor flagged.
    assert engine.compute_status(s, {"open": "No", "why": "x", "sign": "AK", "fryer": "No"}) == (True, None)
    assert engine.compute_status(s, {"open": "Yes", "sign": "AK", "fryer": "No"})[0] is False


def test_branching_schema_rules():
    s = _branching_schema()
    bad = copy.deepcopy(s)
    bad["sections"][2]["items"][0]["go_to_section"] = {"x": "s1"}
    bad["sections"][3]["next_section"] = "s2"
    bad["sections"][0]["items"].append(
        {"id": "b2", "type": "dropdown", "title": "Also", "options": [{"value": "A"}], "go_to_section": {"A": "nope"}}
    )
    problems = engine.validate_schema(bad)
    assert "Section 3, question 1 ('Why closed?'): only multiple choice and dropdown questions can go to a section" in problems
    assert "Section 4: after-section target: can only go to a later section" in problems
    assert "Section 1, question 2 ('Also'): go-to for 'A': no section 'nope'" in problems
    assert "Section 1: only one question per section can choose the next section" in problems


def test_schema_validation_catches_editor_mistakes():
    s = engine.normalize_schema({"sections": [{"id": "s1", "items": [
        {"id": "a", "type": "short_text", "title": "A"},
        {"id": "a", "type": "short_text", "title": "Dup"},
        {"id": "b", "type": "wizard", "title": "B"},
        {"id": "c", "type": "multiple_choice", "title": "C", "options": []},
        {"id": "d", "type": "multiple_choice", "title": "D", "options": [{"value": "x", "issue_tag": "made_up"}, {"value": "x"}]},
        {"id": "e", "type": "linear_scale", "title": "E", "scale": {"min": 3, "max": 20}},
        {"id": "f", "type": "short_text", "title": "F", "validation": {"kind": "regex", "rule": "matches", "value": "("}},
        {"id": "g", "type": "paragraph", "title": "G", "validation": {"kind": "number", "rule": "gt", "value": 1}},
        {"id": "h", "type": "image", "title": "H"},
        {"id": "i", "type": "short_text", "title": ""},
        {"id": "j.k", "type": "short_text", "title": "J"},
        {"id": "m", "type": "multiple_choice_grid", "title": "M", "options": [{"value": "ok"}], "rows": [{"id": "r", "title": "R"}, {"id": "r", "title": "R2"}]},
    ]}]})
    problems = engine.validate_schema(s)
    expected = [
        "duplicate id 'a'",
        "unknown type 'wizard'",
        "('C'): add at least one option",
        "unknown issue tag 'made_up'",
        "option 'x' is listed twice",
        "the scale goes from 0 or 1 up to 2-10",
        "the pattern isn't a valid regular expression",
        "validation kind 'number' isn't available for paragraph",
        "an image needs an image_url",
        "question 10: a title is required",
        "invalid id 'j.k'",
        "duplicate row id 'r'",
    ]
    for fragment in expected:
        assert any(fragment in p for p in problems), (fragment, problems)
    assert engine.validate_schema({"sections": []}) == ["Add at least one section"]
    assert "Add at least one question" in engine.validate_schema(
        engine.normalize_schema({"sections": [{"items": [{"type": "title_text", "title": "Hi"}]}]})
    )


def test_normalize_assigns_ids_and_keeps_existing_ones():
    s = engine.normalize_schema({"sections": [{"title": "One", "items": [
        {"type": "multiple_choice_grid", "title": "G", "options": [{"value": "ok"}], "rows": [{"title": "R"}, {"id": "keep", "title": "K"}]},
        {"id": "q_mine", "type": "short_text", "title": "T"},
    ]}]})
    section = s["sections"][0]
    assert section["id"].startswith("s_")
    grid, text = section["items"]
    assert grid["id"].startswith("q_") and grid["rows"][0]["id"].startswith("r_") and grid["rows"][1]["id"] == "keep"
    assert text["id"] == "q_mine"
    assert engine.validate_schema(s) == []


def test_new_types_need_app_build_6_and_legacy_compatible_ones_do_not():
    compatible = engine.normalize_schema({"sections": [
        {"id": "s1", "title": "Checks", "items": [
            {"id": "g", "type": "multiple_choice_grid", "title": "Status", "required": True,
             "options": [{"value": "Done"}, {"value": "Not done", "issue_tag": "not_done"}],
             "rows": [{"id": "a", "title": "Mop"}]},
            {"id": "q", "type": "dropdown", "title": "Shift", "required": True, "options": [{"value": "AM"}, {"value": "PM"}]},
            {"id": "h", "type": "title_text", "title": "Heads up"},
        ]},
        {"id": "s2", "title": "End", "items": [{"id": "notes", "type": "paragraph", "title": "Notes", "flag_if_answered": True}]},
    ]})
    served = engine.build_served({"checklist_type": "x", "title": "X", "settings": {}}, 1, compatible)
    assert "min_app_build" not in served
    assert served["sections"][0]["grids"][0]["rows"] == [{"id": "g.a", "label": "Mop"}]
    assert served["sections"][0]["questions"][0]["input"] == "dropdown"
    assert served["issues_field"] == {"id": "notes", "label": "Notes", "required": False}
    assert served["sections"][1]["grids"] == []
    required = build5_parse(served)
    assert required == ["g.a", "q"]
    # Build 5's answers (row keys "g.a") are exactly the standard keys.
    assert _err(compatible, {"g.a": "Done", "q": "AM"}) is None

    newer = copy.deepcopy(compatible)
    newer["sections"][1]["items"].insert(0, {"id": "t", "type": "time", "title": "Closed at", "required": True})
    served = engine.build_served({"checklist_type": "x", "title": "X", "settings": {}}, 2, newer)
    assert served["min_app_build"] == 6
    assert "grids" in served["sections"][0] and "grids" not in served["sections"][1]
    assert served["sections"][1]["items"][0]["id"] == "t"
    build5_parse(served)  # still parses: build 5 must never crash on /schemas


# --- Admin API ----------------------------------------------------------------------------


ADMIN_ROUTES = [
    ("get", "/api/checklists/admin/meta", None),
    ("get", "/api/checklists/admin/definitions", None),
    ("post", "/api/checklists/admin/definitions", {"title": "X"}),
    ("post", "/api/checklists/admin/definitions/order", {"order": [ST]}),
    ("get", f"/api/checklists/admin/definitions/{ST}", None),
    ("put", f"/api/checklists/admin/definitions/{ST}", {"sections": []}),
    ("delete", f"/api/checklists/admin/definitions/{ST}/draft", None),
    ("post", f"/api/checklists/admin/definitions/{ST}/publish", {}),
    ("post", f"/api/checklists/admin/definitions/{ST}/duplicate", {}),
    ("patch", f"/api/checklists/admin/definitions/{ST}", {"active": False}),
    ("get", f"/api/checklists/admin/definitions/{ST}/versions/1", None),
    ("post", f"/api/checklists/admin/definitions/{ST}/revert/1", {}),
]


@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES)
def test_admin_routes_are_admin_only(db, checker_client, anon_client, method, path, body):
    kwargs = {"json": body} if body is not None else {}
    assert getattr(checker_client, method)(path, **kwargs).status_code == 403
    assert getattr(anon_client, method)(path, **kwargs).status_code == 401
    assert db.query(ChecklistDefinition).count() == 0


def test_admin_list_and_detail_of_the_seeded_three(db, admin_client):
    r = admin_client.get("/api/checklists/admin/definitions")
    assert r.status_code == 200
    rows = r.json()["definitions"]
    assert [d["checklist_type"] for d in rows] == [ST, SK, FSA]
    fsa = rows[2]
    assert fsa["current_version"] == 2 and fsa["version_count"] == 2 and fsa["published"] is True
    assert fsa["settings"] == {"closing": True, "whatsapp_group": "kitchen", "all_as_usual": True}
    assert fsa["min_app_build"] is None and fsa["has_draft"] is False
    detail = admin_client.get(f"/api/checklists/admin/definitions/{FSA}").json()
    assert [v["version"] for v in detail["versions"]] == [2, 1]
    assert detail["versions"][1]["note"].startswith("Original checklist (with Chef Name)")
    assert detail["versions"][0]["format"] == "legacy"
    assert detail["draft"] is None and detail["problems"] == []
    editable = detail["editable"]
    assert editable["format"] == "items" and editable["legacy_order"] is True
    assert editable["title"] == "Market Kitchen - Food Service Associate Closing"
    v1 = admin_client.get(f"/api/checklists/admin/definitions/{FSA}/versions/1").json()
    assert v1["schema"]["sections"][0]["questions"][0]["id"] == "chef_name"
    assert admin_client.get(f"/api/checklists/admin/definitions/{FSA}/versions/9").status_code == 404
    assert admin_client.get("/api/checklists/admin/definitions/nope").status_code == 404
    meta_json = admin_client.get("/api/checklists/admin/meta").json()
    assert "file_upload" in meta_json["item_types"] and meta_json["issue_tags"][0] == {"key": "not_turned_off", "label": "Not Turned Off"}


NEW_SECTIONS = [
    {"id": "s1", "title": "Front", "items": [
        {"id": "q_temp", "type": "short_text", "title": "Cooler temp (F)", "required": True,
         "validation": {"kind": "number", "rule": "between", "value": [33, 41]}},
        {"id": "q_clean", "type": "checkboxes", "title": "Cleaned", "required": True,
         "options": [{"value": "Counter"}, {"value": "Glass"}], "unticked_tag": "not_done"},
    ]},
]


def test_create_draft_publish_version_cycle(db, admin_client, checker_client, no_send):
    r = admin_client.post("/api/checklists/admin/definitions", json={
        "title": "Deli Opening Check", "description": "Morning", "settings": {"whatsapp_group": None},
    })
    assert r.status_code == 200, r.text
    created = r.json()
    ctype = created["checklist_type"]
    assert ctype == "deli_opening_check"
    assert created["published"] is False and created["current_version"] is None and created["has_draft"] is True
    assert created["sort_order"] == 4
    assert created["problems"] == ["Add at least one question"]
    # Not live until published.
    assert ctype not in checker_client.get("/api/checklists/schemas").json()["schemas"]
    assert checker_client.get(f"/api/checklists/schema/{ctype}").status_code == 404
    assert checker_client.post(f"/api/checklists/{ctype}/submit", json={"answers": {}}).status_code == 404

    # A draft with problems is saved; publishing it is refused with every problem.
    broken = copy.deepcopy(NEW_SECTIONS)
    broken[0]["items"][1]["options"] = []
    r = admin_client.put(f"/api/checklists/admin/definitions/{ctype}", json={"sections": broken})
    assert r.status_code == 200 and r.json()["problems"] == ["Section 1, question 2 ('Cleaned'): add at least one option"]
    r = admin_client.post(f"/api/checklists/admin/definitions/{ctype}/publish", json={})
    assert r.status_code == 400
    assert r.json() == {"detail": "Section 1, question 2 ('Cleaned'): add at least one option",
                        "problems": ["Section 1, question 2 ('Cleaned'): add at least one option"]}

    r = admin_client.put(f"/api/checklists/admin/definitions/{ctype}", json={"sections": NEW_SECTIONS, "title": "Deli Opening"})
    assert r.status_code == 200 and r.json()["problems"] == [] and r.json()["title"] == "Deli Opening Check"
    r = admin_client.post(f"/api/checklists/admin/definitions/{ctype}/publish", json={"note": "First"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current_version"] == 1 and body["has_draft"] is False and body["title"] == "Deli Opening"
    assert body["versions"][0]["note"] == "First" and body["versions"][0]["format"] == "items"
    assert body["min_app_build"] == 6

    schemas = checker_client.get("/api/checklists/schemas").json()["schemas"]
    assert list(schemas) == [ST, SK, FSA, ctype]
    for schema in schemas.values():
        build5_parse(schema)  # build 5 never crashes on a new checklist in /schemas
    served = schemas[ctype]
    assert served["min_app_build"] == 6 and served["settings"]["whatsapp_group"] is None
    assert "grids" not in served["sections"][0] and served["sections"][0]["items"][0]["id"] == "q_temp"

    # Build 5 can't show these questions: a clear message instead of a missing-item error.
    r = checker_client.post(f"/api/checklists/{ctype}/submit", json={"answers": {}})
    assert r.status_code == 400 and r.json()["detail"] == "Update the app to fill this checklist"
    r = checker_client.post(f"/api/checklists/{ctype}/submit", json={"answers": {"q_temp": "50", "q_clean": '["Counter"]'}})
    assert r.status_code == 400 and r.json()["detail"] == "Cooler temp (F): must be a number between 33 and 41"
    r = checker_client.post(f"/api/checklists/{ctype}/submit", json={"answers": {"q_temp": "38", "q_clean": '["Counter"]'}})
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["definition_version"] == 1 and first["all_clear"] is False
    assert first["issue_summary"] == "Not Done:\n  - Glass (Cleaned)"
    assert no_send == []  # no WhatsApp group -> no message queued

    # Publishing the same thing again is refused.
    r = admin_client.post(f"/api/checklists/admin/definitions/{ctype}/publish", json={"sections": NEW_SECTIONS})
    assert r.status_code == 400 and r.json()["detail"] == "Nothing changed since version 1"

    # Edit -> version 2; the old submission keeps version 1.
    edited = copy.deepcopy(NEW_SECTIONS)
    edited[0]["items"][1]["options"].append({"value": "Floor"})
    r = admin_client.post(f"/api/checklists/admin/definitions/{ctype}/publish", json={"sections": edited, "base_version": 1})
    assert r.status_code == 200 and r.json()["current_version"] == 2
    r = admin_client.post(f"/api/checklists/admin/definitions/{ctype}/publish", json={"sections": NEW_SECTIONS, "base_version": 1})
    assert r.status_code == 409
    r = checker_client.post(f"/api/checklists/{ctype}/submit", json={"answers": {"q_temp": "38", "q_clean": '["Counter","Glass","Floor"]'}})
    assert r.status_code == 200 and r.json()["definition_version"] == 2 and r.json()["all_clear"] is True
    assert checker_client.get(f"/api/checklists/{first['id']}").json()["definition_version"] == 1
    v1 = checker_client.get(f"/api/checklists/schema/{ctype}?version=1").json()
    assert [o["value"] for o in v1["sections"][0]["items"][1]["options"]] == ["Counter", "Glass"]

    # Revert publishes version 1 again as version 3.
    r = admin_client.post(f"/api/checklists/admin/definitions/{ctype}/revert/1", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["current_version"] == 3 and body["versions"][0]["note"] == "Reverted to version 1"
    assert [(v["version"], v["submission_count"]) for v in body["versions"]] == [(3, 0), (2, 1), (1, 1)]
    served = checker_client.get(f"/api/checklists/schema/{ctype}").json()
    assert served["version"] == 3 and served["sections"][0]["items"] == v1["sections"][0]["items"]
    assert admin_client.post(f"/api/checklists/admin/definitions/{ctype}/revert/3", json={}).status_code == 400

    # Duplicate: a new, unpublished checklist with the same questions.
    r = admin_client.post(f"/api/checklists/admin/definitions/{ctype}/duplicate", json={})
    assert r.status_code == 200
    dup = r.json()
    assert dup["checklist_type"] == "deli_opening_copy" and dup["title"] == "Deli Opening (copy)"
    assert dup["published"] is False and dup["draft"]["sections"] == served_items_sections(served)
    r = admin_client.post(f"/api/checklists/admin/definitions/{dup['checklist_type']}/publish", json={})
    assert r.status_code == 200 and r.json()["current_version"] == 1


def served_items_sections(served):
    return [
        {k: v for k, v in s.items() if k in ("id", "title", "description", "items", "next_section")}
        for s in served["sections"]
    ]


def test_patch_settings_title_active_and_order(db, admin_client, checker_client):
    r = admin_client.patch(f"/api/checklists/admin/definitions/{SK}", json={"settings": {"all_as_usual": False}})
    assert r.status_code == 200
    assert r.json()["settings"] == {"closing": True, "whatsapp_group": "kitchen", "all_as_usual": False}
    assert checker_client.get(f"/api/checklists/schema/{SK}").json()["settings"]["all_as_usual"] is False
    r = admin_client.patch(f"/api/checklists/admin/definitions/{SK}", json={"settings": {"whatsapp_group": "bar"}})
    assert r.status_code == 400 and r.json()["detail"] == (
        'settings.whatsapp_group "bar" isn\'t an active WhatsApp group. '
        "Choose one of: store, kitchen - or null for no WhatsApp message"
    )
    r = admin_client.patch(f"/api/checklists/admin/definitions/{SK}", json={"settings": {"colour": "red"}})
    assert r.status_code == 400
    assert admin_client.patch(f"/api/checklists/admin/definitions/{SK}", json={"title": " "}).status_code == 400
    r = admin_client.patch(f"/api/checklists/admin/definitions/{SK}", json={"title": "Kitchen Closing - Chef", "short_name": "Kitchen Chef"})
    assert r.status_code == 200
    assert checker_client.get(f"/api/checklists/schema/{SK}").json()["title"] == "Kitchen Closing - Chef"
    assert checklist_schemas.short_name(SK) == "Kitchen Chef"
    # Order.
    r = admin_client.post("/api/checklists/admin/definitions/order", json={"order": [FSA, ST]})
    assert [d["checklist_type"] for d in r.json()["definitions"]] == [FSA, ST, SK]
    assert list(checker_client.get("/api/checklists/schemas").json()["schemas"]) == [FSA, ST, SK]
    # Deactivate: hidden and can't be submitted; old versions still render.
    admin_client.patch(f"/api/checklists/admin/definitions/{ST}", json={"active": False})
    assert ST not in checker_client.get("/api/checklists/schemas").json()["schemas"]
    assert checker_client.get(f"/api/checklists/schema/{ST}").status_code == 404
    assert checker_client.get(f"/api/checklists/schema/{ST}?version=1").status_code == 200
    assert checker_client.post(f"/api/checklists/{ST}/submit", json={"answers": {}}).status_code == 404
    rows = admin_client.get("/api/checklists/admin/definitions").json()["definitions"]
    assert [d["active"] for d in rows] == [True, False, True]


def test_editing_a_seeded_checklist_keeps_build5_working(db, admin_client, checker_client, no_send):
    """The owner edits Main Store's checklist in the editor (items form now).
    Build 5 still gets the same `grid` blocks and answer keys, a stored
    real submission's answers still validate and flag the same way - even if
    the editor dropped the legacy keys it doesn't understand."""
    detail = admin_client.get(f"/api/checklists/admin/definitions/{ST}").json()
    sections = copy.deepcopy(detail["editable"]["sections"])
    for section in sections:
        section.pop("legacy_block", None)
        for item in section["items"]:
            item.pop("answer_format", None)
            item.pop("legacy_role", None)
    sections[0]["items"][0]["rows"][0]["title"] = "All freezers below 0°F?"
    r = admin_client.post(f"/api/checklists/admin/definitions/{ST}/publish", json={"sections": sections, "note": "Reworded"})
    assert r.status_code == 200, r.text
    served = checker_client.get(f"/api/checklists/schema/{ST}").json()
    assert served["version"] == 2 and "min_app_build" not in served
    expected = copy.deepcopy(checklist_builtin.STORE_CLOSING)
    expected["sections"][0]["grid"]["rows"][0]["label"] = "All freezers below 0°F?"
    deep_superset(served, expected)
    rows = json.loads(private_data("checklist_real_answers_2026-09-30.json").read_text())["submissions"]
    for row in rows:
        r = checker_client.post(f"/api/checklists/{ST}/submit", json={"answers": row["answers"]})
        assert r.status_code == 200, r.text
        assert r.json()["all_clear"] == row["all_clear"]
        assert r.json()["issue_summary"] == row["issue_summary"]
    # Adding a question build 5 can't show: that section goes items-only.
    sections[1]["items"].append({"id": "q_photo", "type": "file_upload", "title": "Photo of the lock", "required": True})
    r = admin_client.post(f"/api/checklists/admin/definitions/{ST}/publish", json={"sections": sections})
    assert r.status_code == 200 and r.json()["min_app_build"] == 6
    served = checker_client.get(f"/api/checklists/schema/{ST}").json()
    assert "grid" in served["sections"][0] and "grid" not in served["sections"][1]
    r = checker_client.post(f"/api/checklists/{ST}/submit", json={"answers": rows[1]["answers"]})
    assert r.status_code == 400 and r.json()["detail"] == "Update the app to fill this checklist"


def test_photo_answers_are_attached_to_the_submission(db, admin_client, checker_client, checker, no_send, monkeypatch):
    from app.team.models import TeamMedia

    admin_client.post("/api/checklists/admin/definitions", json={"title": "Cooler photo"})
    admin_client.put("/api/checklists/admin/definitions/cooler_photo", json={"sections": [{"id": "s1", "items": [
        {"id": "q_photo", "type": "file_upload", "title": "Photo", "required": True, "file": {"max_files": 2, "types": ["image"]}},
    ]}]})
    assert admin_client.post("/api/checklists/admin/definitions/cooler_photo/publish", json={}).status_code == 200
    media = TeamMedia(token="tok1", path="x/1.jpg", content_type="image/jpeg", bytes=10, width=1, height=1,
                      uploaded_by_user_id=checker.id)
    other = TeamMedia(token="tok2", path="x/2.jpg", content_type="image/jpeg", bytes=10, width=1, height=1,
                      uploaded_by_user_id=checker.id + 100)
    db.add_all([media, other])
    db.commit()
    r = checker_client.post("/api/checklists/cooler_photo/submit", json={"answers": {"q_photo": json.dumps([other.id])}})
    assert r.status_code == 400 and r.json()["detail"] == "Unknown photo - upload it again."
    r = checker_client.post("/api/checklists/cooler_photo/submit", json={"answers": {"q_photo": json.dumps([media.id])}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["media"] == {str(media.id): {"id": media.id, "token": "tok1", "url": "/api/team/media/tok1", "width": 1, "height": 1}}
    db.refresh(media)
    assert (media.owner_type, media.owner_id) == ("checklist", body["id"])
    # Reusing an attached photo in another submission is refused.
    r = checker_client.post("/api/checklists/cooler_photo/submit", json={"answers": {"q_photo": json.dumps([media.id]), "x": "1"}})
    assert r.status_code == 400
    # The upload route is the daily_checklists feature's (team media rules).
    assert checker_client.post("/api/checklists/media").status_code in (411, 413, 422)


# --- Settings drive the alert, the nightly tasks, WhatsApp and the Overview ------------------


NIGHT = dt.datetime(2026, 9, 28, 21, 30, tzinfo=NY)


def _publish_simple(admin_client, title, settings):
    r = admin_client.post("/api/checklists/admin/definitions", json={"title": title, "settings": settings, "sections": [
        {"id": "s1", "items": [{"id": "q_ok", "type": "multiple_choice", "title": "All done?", "required": True,
                                "options": [{"value": "Yes"}, {"value": "No", "issue_tag": "issue"}]}]}]})
    ctype = r.json()["checklist_type"]
    assert admin_client.post(f"/api/checklists/admin/definitions/{ctype}/publish", json={}).status_code == 200
    return ctype


def _row(db, ctype, when):
    db.add(ChecklistSubmission(checklist_type=ctype, submitted_by_user_id=1, submitted_by_name="asha",
                               answers_json="{}", all_clear=True, answers_hash=f"h-{ctype}-{when}",
                               created_at=when.astimezone(dt.timezone.utc)))
    db.commit()


@pytest.mark.suite_pack  # two WhatsApp roles (store, kitchen)
def test_closing_alert_is_still_one_notification_driven_by_settings(db, admin_client):
    alerts = closing_checklist_alerts(db, NIGHT)
    assert len(alerts) == 1 and alerts[0].body.count(",") == 1  # the three built-ins
    deli = _publish_simple(admin_client, "Deli Closing", {"closing": True, "whatsapp_group": "kitchen"})
    _publish_simple(admin_client, "Weekly Deep Clean", {"closing": False})
    alerts = closing_checklist_alerts(db, NIGHT)
    assert len(alerts) == 1
    assert alerts[0].body == (
        "Not submitted yet: Main Store - Daily Closing Checklist, Market Kitchen – Chef Closing Checklist, "
        "Market Kitchen - Food Service Associate Closing and Deli Closing"
    )
    assert alerts[0].dedupe_keys == ["closing_checklist:2026-09-28"]
    admin_client.patch(f"/api/checklists/admin/definitions/{FSA}", json={"settings": {"closing": False}})
    admin_client.patch(f"/api/checklists/admin/definitions/{ST}", json={"active": False})
    _row(db, deli, dt.datetime(2026, 9, 28, 21, 0, tzinfo=NY))
    alerts = closing_checklist_alerts(db, NIGHT)
    assert len(alerts) == 1 and alerts[0].body == "Not submitted yet: Market Kitchen – Chef Closing Checklist"
    _row(db, SK, dt.datetime(2026, 9, 28, 21, 5, tzinfo=NY))
    assert closing_checklist_alerts(db, NIGHT) == []


@pytest.fixture
def managers_team(db, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "team_autotask_checklist_enabled", True)
    monkeypatch.setattr(s, "team_autotask_invoice_enabled", False)
    monkeypatch.setattr(s, "team_autotask_order_enabled", False)
    monkeypatch.setattr(s, "team_whatsapp_fallback", True)
    monkeypatch.setattr(s, "whapi_store_checklist_group_id", "st-group@g.us")
    monkeypatch.setattr(s, "whapi_kitchen_checklist_group_id", "sk-group@g.us")
    monkeypatch.setattr(tasks_svc, "_quiet", lambda now: False)
    team = db.query(Team).filter(Team.name == "Managers").one()
    add_member(db, team, make_user(db, "meena", features=["team_tasks"]))
    return team


@pytest.mark.suite_pack  # two WhatsApp roles (store, kitchen)
def test_nightly_tasks_and_their_whatsapp_group_follow_settings(db, admin_client, managers_team, monkeypatch):
    from app.services import whapi_client

    sent = []
    monkeypatch.setattr(whapi_client, "send_whatsapp_message", lambda text, to=None: sent.append((to, text)))
    deli = _publish_simple(admin_client, "Deli Closing", {"closing": True, "whatsapp_group": "store"})
    _publish_simple(admin_client, "Weekly Deep Clean", {"closing": False})
    admin_client.patch(f"/api/checklists/admin/definitions/{SK}", json={"settings": {"closing": False}})
    admin_client.patch(f"/api/checklists/admin/definitions/{ST}", json={"short_name": "Store Closing"})
    night = dt.datetime(2026, 9, 28, 21, 35, tzinfo=NY)
    auto_tasks.run_checklist_rule(db, night)
    tasks = db.query(Task).filter(Task.source_type == "checklist").order_by(Task.id).all()
    assert [t.title for t in tasks] == [
        "Submit the Store Closing checklist",
        "Submit the Market Kitchen FSA closing checklist",
        "Submit the Deli Closing checklist",
    ]
    assert [to for to, _ in sent] == ["st-group@g.us", "sk-group@g.us", "st-group@g.us"]
    # Submitting closes its task, even after closing is switched off.
    admin_client.patch(f"/api/checklists/admin/definitions/{deli}", json={"settings": {"closing": False}})
    _row(db, deli, dt.datetime(2026, 9, 28, 21, 40, tzinfo=NY))
    auto_tasks.run_checklist_rule(db, night + dt.timedelta(minutes=10))
    db.expire_all()
    assert db.query(Task).filter(Task.source_id == f"{deli}:2026-09-28").one().status == "done"


def test_submission_whatsapp_group_follows_settings(db, admin_client, monkeypatch):
    monkeypatch.setattr(get_settings(), "whapi_store_checklist_group_id", "st-group@g.us")
    monkeypatch.setattr(get_settings(), "whapi_kitchen_checklist_group_id", "")
    monkeypatch.setattr(get_settings(), "whapi_chat_id", "invoice-group@g.us")
    sent = []
    monkeypatch.setattr(checklist_submission, "send_whatsapp_message", lambda text, to=None: sent.append(to))

    def entry(ctype):
        e = ChecklistSubmission(checklist_type=ctype, submitted_by_user_id=1, submitted_by_name="asha",
                                answers_json="{}", all_clear=True, answers_hash=f"h-{ctype}",
                                created_at=dt.datetime(2026, 9, 29, 1, 0, tzinfo=dt.timezone.utc))
        db.add(e)
        db.commit()
        return e

    checklist_submission._attempt_whatsapp(db, entry(ST))
    assert sent == ["st-group@g.us"]
    # Kitchen group not configured: recorded as not configured - never falls
    # back to the invoice group.
    e = entry(SK)
    checklist_submission._attempt_whatsapp(db, e)
    assert sent == ["st-group@g.us"] and e.whatsapp_sent is False and "isn't set" in e.whatsapp_error
    # Switched to the store group.
    admin_client.patch(f"/api/checklists/admin/definitions/{SK}", json={"settings": {"whatsapp_group": "store"}})
    checklist_submission._attempt_whatsapp(db, entry(SK))
    assert sent == ["st-group@g.us", "st-group@g.us"]
    # No group: nothing sent, nothing recorded as failed.
    admin_client.patch(f"/api/checklists/admin/definitions/{ST}", json={"settings": {"whatsapp_group": None}})
    e = entry(ST)
    assert checklist_submission.should_send_whatsapp(e) is False
    checklist_submission._attempt_whatsapp(db, e)
    assert len(sent) == 2 and e.whatsapp_sent is False and e.whatsapp_error is None


def test_overview_lists_every_checklist_with_its_latest(db, admin_client, checker_client, checker):
    _row(db, ST, dt.datetime(2026, 9, 28, 21, 0, tzinfo=NY))
    db.add(ChecklistSubmission(checklist_type=SK, submitted_by_user_id=checker.id, submitted_by_name="asha",
                               answers_json="{}", all_clear=False, issue_summary="Issue:\n  - x", answers_hash="h2",
                               created_at=dt.datetime(2026, 9, 29, 1, 0, tzinfo=dt.timezone.utc)))
    db.commit()
    body = admin_client.get("/api/checklists/overview?date=2026-09-28").json()
    assert body["date"] == "2026-09-28"
    by_type = {c["checklist_type"]: c for c in body["checklists"]}
    assert list(by_type) == [ST, SK, FSA]
    assert by_type[ST]["submitted_on_date"] is True and by_type[ST]["latest"]["checklist_type"] == ST
    assert by_type[SK]["submitted_on_date"] is True and by_type[SK]["latest"]["all_clear"] is False
    assert by_type[FSA]["submitted_on_date"] is False and by_type[FSA]["latest"] is None
    assert by_type[ST]["settings"]["closing"] is True and by_type[ST]["short_name"] == "Main Store"
    # A non-admin sees only their own.
    mine = {c["checklist_type"]: c for c in checker_client.get("/api/checklists/overview?date=2026-09-28").json()["checklists"]}
    assert mine[ST]["latest"] is None and mine[SK]["latest"] is not None


def test_registry_falls_back_to_builtins_without_tables(db):
    Base.metadata.tables["checklist_definition_versions"].drop(bind=db.get_bind())
    Base.metadata.tables["checklist_definitions"].drop(bind=db.get_bind())
    checklist_schemas.clear_cache()
    reg = checklist_schemas.registry()
    assert reg.source == "builtin" and list(reg.definitions) == [ST, SK, FSA]
    assert checklist_schemas.get_schema(FSA)["version"] == 2


def test_registry_notices_changes_made_by_another_process(db, admin_client, monkeypatch):
    checklist_admin.ensure_seeded(db)
    assert checklist_schemas.get_schema(SK)["title"] == "Market Kitchen – Chef Closing Checklist"
    # Another worker renames it (direct write, no clear_cache here).
    row = db.query(ChecklistDefinition).filter_by(checklist_type=SK).one()
    row.title = "Kitchen Closing - Chef"
    row.updated_at = row.updated_at + dt.timedelta(seconds=1)
    db.commit()
    monkeypatch.setattr(checklist_schemas, "STAMP_CHECK_SECONDS", 0)
    assert checklist_schemas.get_schema(SK)["title"] == "Kitchen Closing - Chef"


# --- Migration 0039 -----------------------------------------------------------------------


def _alembic(db_file, *args):
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_file}")
    alembic = pathlib.Path(sys.executable).parent / "alembic"
    return subprocess.run([str(alembic), *args], cwd=BACKEND, env=env, check=True, capture_output=True, text=True)


def _check_migrated(db_file, expected_submissions=None):
    con = sqlite3.connect(db_file)
    try:
        for table in ("checklist_definitions", "checklist_definition_versions", "checklist_submissions"):
            migrated = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
            modeled = {c.name for c in Base.metadata.tables[table].columns}
            assert migrated == modeled, (table, migrated ^ modeled)
        defs = con.execute(
            "SELECT checklist_type, title, short_name, active, sort_order, settings_json, current_version FROM "
            "checklist_definitions ORDER BY sort_order"
        ).fetchall()
        assert [(d[0], d[2], d[4], d[6]) for d in defs] == [
            (ST, "Main Store", 1, 1), (SK, "Market Kitchen", 2, 1), (FSA, "Market Kitchen FSA", 3, 2)
        ]
        assert all(json.loads(d[5])["closing"] for d in defs)
        versions = con.execute(
            "SELECT d.checklist_type, v.version, v.schema_json FROM checklist_definition_versions v "
            "JOIN checklist_definitions d ON d.id = v.definition_id ORDER BY d.sort_order, v.version"
        ).fetchall()
        assert [(t, v) for t, v, _ in versions] == [(ST, 1), (SK, 1), (FSA, 1), (FSA, 2)]
        for t, v, schema_json in versions:
            assert json.loads(schema_json) == ORIGINAL[(t, v)]
        assert con.execute("SELECT count(*) FROM checklist_submissions WHERE definition_version IS NOT NULL").fetchone()[0] == 0
        if expected_submissions is not None:
            assert con.execute("SELECT count(*) FROM checklist_submissions").fetchone()[0] == expected_submissions
        unique = [r[1] for r in con.execute("PRAGMA index_list(checklist_definitions)") if r[2] == 1]
        assert unique, "checklist_type is unique"
    finally:
        con.close()


def _check_downgraded(db_file, expected_submissions=None):
    con = sqlite3.connect(db_file)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "checklist_definitions" not in tables and "checklist_definition_versions" not in tables
        cols = {r[1] for r in con.execute("PRAGMA table_info(checklist_submissions)")}
        assert "definition_version" not in cols and "answers_json" in cols
        if expected_submissions is not None:
            assert con.execute("SELECT count(*) FROM checklist_submissions").fetchone()[0] == expected_submissions
        assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0038"
    finally:
        con.close()


def test_migration_0039_up_and_down_on_a_fresh_database(tmp_path):
    db_file = tmp_path / "fresh.db"
    _alembic(db_file, "upgrade", "0038")
    con = sqlite3.connect(db_file)
    con.execute(
        "INSERT INTO checklist_submissions (checklist_type, submitted_by_user_id, submitted_by_name, answers_json, "
        "all_clear, answers_hash, whatsapp_sent, whatsapp_ambiguous, created_at) VALUES "
        "('kitchen_fsa', 1, 'asha', '{\"chef_name\": \"JL\"}', 1, 'h', 0, 0, '2026-09-29 01:00:00')"
    )
    con.commit()
    con.close()
    _alembic(db_file, "upgrade", "head")
    _check_migrated(db_file, expected_submissions=1)
    _alembic(db_file, "downgrade", "0038")
    _check_downgraded(db_file, expected_submissions=1)
    _alembic(db_file, "upgrade", "head")
    _check_migrated(db_file, expected_submissions=1)


def _db_copies():
    raw = os.environ.get("CHECKLIST_MIGRATION_DB_COPIES", "")
    return [p for p in raw.split(os.pathsep) if p]


@pytest.mark.skipif(not _db_copies(), reason="set CHECKLIST_MIGRATION_DB_COPIES to .backup copies of dev / prod")
@pytest.mark.parametrize("source", _db_copies() or ["-"])
def test_migration_0039_up_and_down_on_real_db_copies(tmp_path, source):
    """Run with CHECKLIST_MIGRATION_DB_COPIES=/tmp/dev-copy.db:/tmp/prod-copy.db
    (made with sqlite3 "file:...?mode=ro" ".backup ..."). The copy is copied
    again into tmp_path, so the given files are never modified."""
    db_file = tmp_path / "copy.db"
    shutil.copyfile(source, db_file)
    con = sqlite3.connect(db_file)
    before = con.execute("SELECT count(*) FROM checklist_submissions").fetchone()[0]
    rows_before = con.execute("SELECT id, answers_json, all_clear, issue_summary FROM checklist_submissions ORDER BY id").fetchall()
    assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0038"
    con.close()
    _alembic(db_file, "upgrade", "head")
    _check_migrated(db_file, expected_submissions=before)
    con = sqlite3.connect(db_file)
    stored = con.execute(
        "SELECT id, checklist_type, answers_json, all_clear, issue_summary FROM checklist_submissions ORDER BY id"
    ).fetchall()
    con.close()
    assert [(r[0], r[2], r[3], r[4]) for r in stored] == rows_before
    # Every stored submission still gives the same result with its version (1).
    for _, ctype, answers_json, all_clear, summary in stored:
        schema = engine.items_view(ORIGINAL[(ctype, 1)])
        assert engine.compute_status(schema, json.loads(answers_json)) == (bool(all_clear), summary)
    _alembic(db_file, "downgrade", "0038")
    _check_downgraded(db_file, expected_submissions=before)
    _alembic(db_file, "upgrade", "head")
    _check_migrated(db_file, expected_submissions=before)


# --- Photos: upload route and image items ---------------------------------------------------


def _png_bytes():
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


def test_checklist_photo_upload_route_accepts_a_real_multipart_png(db, checker_client, checker):
    from app.team.models import TeamMedia

    r = checker_client.post("/api/checklists/media", files={"file": ("cooler.png", _png_bytes(), "image/png")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"id", "token", "url", "width", "height"} and body["url"] == f"/api/team/media/{body['token']}"
    media = db.get(TeamMedia, body["id"])
    assert media.uploaded_by_user_id == checker.id and media.owner_type is None
    assert checker_client.get(body["url"]).status_code == 200
    # Without the file field: 422 with the plain message.
    r = checker_client.post("/api/checklists/media", files={"other": ("x.png", _png_bytes(), "image/png")})
    assert r.status_code == 422 and r.json()["detail"] == "Attach the photo as 'file'."
    # Needs the daily_checklists grant.
    outsider = client_for(db, make_user(db, "nochecklists", features=["team_tasks"]))
    r = outsider.post("/api/checklists/media", files={"file": ("cooler.png", _png_bytes(), "image/png")})
    assert r.status_code == 403


def test_image_items_are_readable_by_staff_once_saved(db, admin_client, checker_client):
    from app.team.models import TeamMedia

    upload = admin_client.post("/api/checklists/media", files={"file": ("sign.png", _png_bytes(), "image/png")}).json()
    # Before it's used by a checklist, only the uploader / admins can load it.
    assert checker_client.get(upload["url"]).status_code == 404
    r = admin_client.post("/api/checklists/admin/definitions", json={"title": "Sign check", "sections": [{"id": "s1", "items": [
        {"id": "img", "type": "image", "title": "The open sign", "image_url": upload["url"]},
        {"id": "q", "type": "multiple_choice", "title": "Sign off?", "required": True, "options": [{"value": "Yes"}]},
    ]}]})
    assert r.status_code == 200
    media = db.get(TeamMedia, upload["id"])
    db.refresh(media)
    assert media.owner_type == "checklist_definition"
    assert admin_client.post("/api/checklists/admin/definitions/sign_check/publish", json={}).status_code == 200
    assert checker_client.get(upload["url"]).status_code == 200
    # A user without the checklist grant still can't.
    outsider = client_for(db, make_user(db, "nochecklists", features=["team_tasks"]))
    assert outsider.get(upload["url"]).status_code == 404
    # Deactivated checklist: staff lose access, admins keep it.
    admin_client.patch("/api/checklists/admin/definitions/sign_check", json={"active": False})
    assert checker_client.get(upload["url"]).status_code == 404
    assert admin_client.get(upload["url"]).status_code == 200
    # Also attached when it first appears in a later draft save.
    second = admin_client.post("/api/checklists/media", files={"file": ("b.png", _png_bytes(), "image/png")}).json()
    detail = admin_client.get(f"/api/checklists/admin/definitions/{SK}").json()
    sections = detail["editable"]["sections"]
    sections[0]["items"].insert(0, {"id": "img2", "type": "image", "title": None, "image_url": second["url"]})
    assert admin_client.put(f"/api/checklists/admin/definitions/{SK}", json={"sections": sections}).status_code == 200
    assert checker_client.get(second["url"]).status_code == 200


def test_created_and_duplicated_drafts_carry_title_and_description(db, admin_client):
    r = admin_client.post("/api/checklists/admin/definitions", json={"title": "Deli Opening", "short_name": "Deli", "description": "Mornings"})
    body = r.json()
    for key in ("draft", "editable"):
        assert (body[key]["title"], body[key]["short_name"], body[key]["description"]) == ("Deli Opening", "Deli", "Mornings")
    dup = admin_client.post(f"/api/checklists/admin/definitions/{FSA}/duplicate", json={}).json()
    assert dup["draft"]["title"] == "Market Kitchen - Food Service Associate Closing (copy)"
    assert dup["draft"]["description"].startswith("Complete all closing tasks")
    # A draft save without meta keeps the definition's.
    r = admin_client.put("/api/checklists/admin/definitions/deli_opening", json={"sections": NEW_SECTIONS})
    assert r.json()["draft"]["title"] == "Deli Opening" and r.json()["draft"]["description"] == "Mornings"


# --- Submissions always use the current published version (regression) ------------------
#
# Seen in the web review: a staff FSA submission made right after publishing
# v4 was stored as definition_version 3. Root cause: the per-process
# registry cache. A registry() load already running on another request
# thread when the publish committed read the old rows, then stored that
# stale registry as the fresh cache AFTER publish's clear_cache() - so for
# up to STAMP_CHECK_SECONDS the schema endpoints served, and submit
# validated/stored against, the previous version. (Another process's publish
# had the same window by design.) Fixed twice over: a load that raced a
# clear_cache() is discarded (generation counter), and submit reads the
# current version from the database, not the cache.

TWO_Q = [{"id": "s1", "items": [
    {"id": "q_ok", "type": "multiple_choice", "title": "All done?", "required": True,
     "options": [{"value": "Yes"}, {"value": "No", "issue_tag": "issue"}]},
    {"id": "q_lock", "type": "multiple_choice", "title": "Back door locked?", "required": True,
     "options": [{"value": "Yes"}, {"value": "No", "issue_tag": "issue"}]},
]}]


def _publish_v2(db, ctype, admin_user):
    session = checklist_schemas.SessionLocal()
    try:
        return checklist_admin.publish(session, admin_user, ctype, {"sections": copy.deepcopy(TWO_Q)})
    finally:
        session.close()


def test_a_registry_load_racing_a_publish_is_not_cached(db, admin, admin_client, checker_client, no_send, monkeypatch):
    ctype = _publish_simple(admin_client, "Race Check", {"whatsapp_group": None})
    assert checklist_schemas.get_schema(ctype)["version"] == 1
    monkeypatch.setattr(checklist_schemas, "STAMP_CHECK_SECONDS", 60)
    real_read_stamp = checklist_schemas._read_stamp
    raced = {}

    def read_stamp_then_publish(session):
        stamp = real_read_stamp(session)  # this request thread reads the OLD stamp...
        if not raced:
            raced["v"] = _publish_v2(db, ctype, admin)["current_version"]  # ...a publish commits meanwhile
        return stamp

    monkeypatch.setattr(checklist_schemas, "_read_stamp", read_stamp_then_publish)
    checklist_schemas._state["checked_at"] = time.monotonic() - 3600  # due for its stamp check
    checklist_schemas.registry()
    monkeypatch.setattr(checklist_schemas, "_read_stamp", real_read_stamp)

    assert raced["v"] == 2
    assert checker_client.get(f"/api/checklists/schema/{ctype}").json()["version"] == 2
    r = checker_client.post(f"/api/checklists/{ctype}/submit", json={"answers": {"q_ok": "Yes", "q_lock": "Yes"}})
    assert r.status_code == 200, r.text
    assert r.json()["definition_version"] == 2
    assert db.get(ChecklistSubmission, r.json()["id"]).definition_version == 2


def test_submit_uses_the_database_version_not_a_stale_cache(db, admin, admin_client, checker_client, no_send, monkeypatch):
    ctype = _publish_simple(admin_client, "Other Worker", {"whatsapp_group": None})
    assert checklist_schemas.get_schema(ctype)["version"] == 1
    monkeypatch.setattr(checklist_schemas, "STAMP_CHECK_SECONDS", 60)
    # Published by another process: this process's cache isn't cleared.
    with monkeypatch.context() as m:
        m.setattr(checklist_schemas, "clear_cache", lambda: None)
        assert _publish_v2(db, ctype, admin)["current_version"] == 2
    assert checklist_schemas.registry().definitions[ctype].current_version == 1  # still cached

    r = checker_client.post(f"/api/checklists/{ctype}/submit",
                            json={"answers": {"q_ok": "No", "q_lock": "Yes"}, "definition_version": 2})
    assert r.status_code == 200, r.text
    assert r.json()["definition_version"] == 2 and r.json()["all_clear"] is False
    # ...and the cache was refreshed on the way.
    assert checker_client.get(f"/api/checklists/schema/{ctype}").json()["version"] == 2


def test_a_form_loaded_before_a_publish_is_handled_deliberately(db, admin, admin_client, checker_client, no_send):
    ctype = _publish_simple(admin_client, "Stale Form", {"whatsapp_group": None})
    _publish_v2(db, ctype, admin)
    stale = "This checklist was changed since you opened it. Go back, open it again and submit the new version."
    # Website form loaded on v1, answers don't fit v2 -> 409 "open it again", nothing stored.
    r = checker_client.post(f"/api/checklists/{ctype}/submit", json={"answers": {"q_ok": "Yes"}, "definition_version": 1})
    assert r.status_code == 409 and r.json()["detail"] == stale
    # App build 5 sends no version: answers that fit only an older version get the same message.
    r = checker_client.post(f"/api/checklists/{ctype}/submit", json={"answers": {"q_ok": "Yes"}})
    assert r.status_code == 409 and r.json()["detail"] == stale
    assert db.query(ChecklistSubmission).filter_by(checklist_type=ctype).count() == 0
    # Loaded on v1 but the answers fit v2 (ids are stable): accepted, stored as v2.
    r = checker_client.post(f"/api/checklists/{ctype}/submit",
                            json={"answers": {"q_ok": "Yes", "q_lock": "Yes"}, "definition_version": 1})
    assert r.status_code == 200 and r.json()["definition_version"] == 2
    # A plain mistake on the current version is still a 400 with the real problem.
    r = checker_client.post(f"/api/checklists/{ctype}/submit",
                            json={"answers": {"q_ok": "Maybe", "q_lock": "Yes"}, "definition_version": 2})
    assert r.status_code == 400 and "q_ok" not in r.json()["detail"]


def test_one_unservable_checklist_does_not_take_down_the_list(db, admin_client, checker_client, monkeypatch):
    ctype = _publish_simple(admin_client, "Broken One", {"whatsapp_group": None})
    real = engine.build_served

    def build(meta, version, stored):
        if meta["checklist_type"] == ctype:
            raise RuntimeError("boom")
        return real(meta, version, stored)

    monkeypatch.setattr(engine, "build_served", build)
    checklist_schemas.clear_cache()
    r = checker_client.get("/api/checklists/schemas")
    assert r.status_code == 200 and list(r.json()["schemas"]) == [ST, SK, FSA]


def test_schema_with_wrong_json_types_is_a_400_not_a_crash(db, admin_client):
    r = admin_client.post("/api/checklists/admin/definitions", json={"title": "Types"})
    ctype = r.json()["checklist_type"]
    bad = [
        [{"id": "s1", "items": [{"id": "q", "type": "dropdown", "title": "x", "options": "Yes,No"}]}],
        [{"id": "s1", "items": [{"id": ["q"], "type": "short_text", "title": "x"}]}],
        [{"id": "s1", "items": [{"id": "q", "type": "dropdown", "title": "x", "options": [{"value": ["a"]}]}]}],
        [{"id": "s1", "items": [{"id": "q", "type": "file_upload", "title": "x", "file": 3}]}],
        [{"id": "s1", "next_section": {"a": 1}, "items": []}],
    ]
    for sections in bad:
        r = admin_client.put(f"/api/checklists/admin/definitions/{ctype}", json={"sections": sections})
        assert r.status_code == 400, (sections, r.text)


def test_a_regex_that_could_hang_the_server_is_refused():
    item = {"id": "q", "type": "short_text", "title": "Code", "required": True,
            "validation": {"kind": "regex", "rule": "matches", "value": "(a+)+$"}}
    problems = engine.validate_schema({"sections": [{"id": "s1", "items": [item]}]})
    assert problems == ["Section 1, question 1 ('Code'): the pattern is too complex (a repeated group that itself repeats)"]
    item["validation"]["value"] = r"^[A-Z]{3}-\d+(-\d{4})?$"
    assert engine.validate_schema({"sections": [{"id": "s1", "items": [item]}]}) == []


# --- Draft concurrency (Oct 1 pre-release audit) ------------------------------------------------


def test_draft_save_and_publish_refuse_a_stale_base(db, admin_client, no_send):
    """PUT / publish take an optional base_draft_updated_at (the draft_updated_at
    of the detail being edited, null for "no draft"): a stale one is a 409 and
    changes nothing; without it, last write wins as before."""
    url = "/api/checklists/admin/definitions"
    created = admin_client.post(url, json={"title": "Two Admins"}).json()
    ctype, base = created["checklist_type"], created["draft_updated_at"]
    assert base

    first = admin_client.put(f"{url}/{ctype}", json={"sections": NEW_SECTIONS, "base_draft_updated_at": base})
    assert first.status_code == 200, first.text
    newer = first.json()["draft_updated_at"]
    assert newer and newer != base

    # The other admin, still on the old base: refused, the draft is untouched.
    other = copy.deepcopy(NEW_SECTIONS)
    other[0]["title"] = "Overwritten"
    r = admin_client.put(f"{url}/{ctype}", json={"sections": other, "base_draft_updated_at": base})
    assert r.status_code == 409
    assert r.json()["detail"] == checklist_admin.STALE_DRAFT_MESSAGE
    assert admin_client.get(f"{url}/{ctype}").json()["draft"]["sections"][0]["title"] != "Overwritten"
    # "I started from no draft" while there is one: also stale.
    assert admin_client.put(f"{url}/{ctype}", json={"sections": other, "base_draft_updated_at": None}).status_code == 409
    # Bad values are a 400, not a 500.
    for bad in (123, "yesterday", ["x"]):
        assert admin_client.put(f"{url}/{ctype}", json={"sections": other, "base_draft_updated_at": bad}).status_code == 400
    # The same instant written with a Z is the same base.
    z = newer.replace("+00:00", "Z")
    r = admin_client.put(f"{url}/{ctype}", json={"sections": NEW_SECTIONS, "base_draft_updated_at": z})
    assert r.status_code == 200, r.text
    newer = r.json()["draft_updated_at"]

    # Publish: stale base refused, current base publishes and clears the draft.
    r = admin_client.post(f"{url}/{ctype}/publish", json={"base_draft_updated_at": base})
    assert r.status_code == 409 and r.json()["detail"] == checklist_admin.STALE_DRAFT_MESSAGE
    r = admin_client.post(f"{url}/{ctype}/publish", json={"base_draft_updated_at": newer, "base_version": None})
    assert r.status_code == 200, r.text
    assert r.json()["current_version"] == 1 and r.json()["draft_updated_at"] is None

    # After a publish there's no draft: an editor still holding the old
    # draft's time is stale; one that reloaded (null) saves.
    assert admin_client.put(f"{url}/{ctype}", json={"sections": other, "base_draft_updated_at": newer}).status_code == 409
    r = admin_client.put(f"{url}/{ctype}", json={"sections": other, "base_draft_updated_at": None})
    assert r.status_code == 200, r.text
    # A discard elsewhere makes a held draft time stale too.
    held = r.json()["draft_updated_at"]
    admin_client.delete(f"{url}/{ctype}/draft")
    assert admin_client.put(f"{url}/{ctype}", json={"sections": other, "base_draft_updated_at": held}).status_code == 409

    # Without the key (older apps / pages): last write wins, as before.
    r = admin_client.put(f"{url}/{ctype}", json={"sections": other})
    assert r.status_code == 200 and r.json()["draft"]["sections"][0]["title"] == "Overwritten"
    assert "base_draft_updated_at" not in r.json()["draft"]
    r = admin_client.put(f"{url}/{ctype}", json={"sections": NEW_SECTIONS})
    assert r.status_code == 200
