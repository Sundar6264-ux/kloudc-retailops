"""App build 5 (spicetown-mobile 9985842, lib/features/checklists/**) against
the checklist builder's served schemas.

Build 5 parses EVERY entry of GET /api/checklists/schemas with hard Dart
casts (checklist_schema.dart), so one malformed entry breaks all three
checklist tiles. It renders only the legacy blocks (grid / grids /
questions / checkbox_groups, top-level issues_field / confirmation), sends
`confirmation.label` as the confirmation's answer, and knows nothing about
min_app_build. These tests check, for random admin-built schemas and edits
of the seeded three, that:
  - every served schema parses the way build 5 casts it;
  - when the schema needs no newer app, the answers build 5 would send for
    a complete form validate;
  - when it does, build 5's answers get the "Update the app" message, never
    a confusing validation error.

Nothing here sends WhatsApp (no_send / the checklists have no group).
"""

import copy
import json
import random

import pytest

from app.services import checklist_builtin
from app.services import checklist_engine as engine
from app.services import checklist_submission

from tests.test_checklist_builder import ST, SK, FSA, build5_parse, checker, checker_client, no_send  # noqa: F401

META = {"checklist_type": "x", "title": "X", "settings": {}}


def build5_answers(served: dict, rng=None) -> dict:
    """What build 5 submits for a completely filled form: a grid row / a
    question gets one of its options, a required box group gets a box
    ticked ("Done"), the issues text only when typed (required -> "None"),
    and the confirmation's LABEL as its value (checklist_form_screen.dart
    _submit)."""
    rng = rng or random.Random(0)
    answers = {}
    for section in served["sections"]:
        grids = ([section["grid"]] if section.get("grid") else []) + list(section.get("grids") or [])
        for grid in grids:
            for row in grid["rows"]:
                answers[row["id"]] = rng.choice(grid["options"])["value"]
        for q in section.get("questions") or []:
            if q.get("required") or rng.random() < 0.5:
                answers[q["id"]] = rng.choice(q["options"])["value"]
        for group in section.get("checkbox_groups") or []:
            if group.get("required") or rng.random() < 0.5:
                for box in group["items"]:
                    if rng.random() < 0.6:
                        answers[box["id"]] = "Done"
                answers.setdefault(group["items"][0]["id"], "Done")
    field = served.get("issues_field")
    if field is not None and (field["required"] or rng.random() < 0.5):
        answers[field["id"]] = "None"
    confirmation = served.get("confirmation")
    if confirmation is not None:
        answers[confirmation["id"]] = confirmation["label"]
    return answers


def assert_build5_can_submit(stored: dict, served: dict, rng=None) -> None:
    build5_parse(served)
    answers = build5_answers(served, rng)
    items = engine.items_view(stored)
    errors = engine.answer_errors(items, answers)
    if not served.get("min_app_build"):
        assert errors == [], (errors, answers)
    elif errors:
        # The submit route turns this into "Update the app to fill this checklist".
        keys = engine.newer_app_keys(served)
        assert keys and not any(k in answers for k in keys), (errors, keys)


# --- Random admin-built schemas ---------------------------------------------------------------

TAGS = [None, None, "issue", "not_done", "critical"]


def _options(rng, n=None):
    n = n or rng.randint(1, 4)
    return [{"value": f"Opt {i}", "issue_tag": rng.choice(TAGS)} for i in range(n)]


def _item(rng, iid):
    itype = rng.choice(engine.ITEM_TYPES)
    item = {"id": iid, "type": itype, "title": f"Q {iid}", "required": rng.random() < 0.6}
    if rng.random() < 0.3:
        item["description"] = "Some help text"
    if itype in engine.CHOICE_TYPES:
        item["options"] = _options(rng)
        if itype in engine.OTHER_OPTION_TYPES and rng.random() < 0.2:
            item["other_option"] = True
        if rng.random() < 0.2:
            item["shuffle"] = True
    if itype in engine.GRID_TYPES:
        item["options"] = _options(rng, rng.randint(2, 4))
        item["rows"] = [{"id": f"r{i}", "title": f"Row {i}"} for i in range(rng.randint(1, 3))]
        if rng.random() < 0.15:
            item["one_per_column"] = True
    if itype in ("linear_scale", "rating"):
        item["scale"] = {"min": 1, "max": 5, "min_label": "Poor", "max_label": "Great"}
    if itype == "file_upload":
        item["file"] = {"max_files": 2, "types": ["image"]}
    if itype == "image":
        item["image_url"] = "/api/checklists/media/1/abc"
    if itype == "paragraph" and rng.random() < 0.5:
        item["flag_if_answered"] = True
    if itype == "short_text" and rng.random() < 0.3:
        item["validation"] = {"kind": "number", "rule": "is_number"}
    if itype in ("multiple_choice", "dropdown") and rng.random() < 0.2:
        item["summary_label"] = "Shift"
    return item


def random_schema(rng) -> dict:
    sections = []
    n_sections = rng.randint(1, 4)
    for s in range(n_sections):
        items = [_item(rng, f"q{s}_{i}") for i in range(rng.randint(1, 4))]
        section = {"id": f"s{s}", "title": rng.choice([None, f"Section {s}"]), "items": items}
        if s < n_sections - 1 and rng.random() < 0.15:
            section["next_section"] = "submit"
        sections.append(section)
    # Sometimes a branching question.
    if n_sections > 1 and rng.random() < 0.3:
        item = {"id": "branch", "type": "multiple_choice", "title": "Go where?", "required": True,
                "options": [{"value": "A"}, {"value": "B"}], "go_to_section": {"A": f"s{n_sections - 1}", "B": "submit"}}
        sections[0]["items"] = [i for i in sections[0]["items"] if not i.get("go_to_section")] + [item]
    # Sometimes a trailing "anything unusual?" paragraph / a confirmation.
    last = sections[-1]["items"]
    if rng.random() < 0.4:
        last.append({"id": "unusual", "type": "paragraph", "title": "Anything unusual?", "required": rng.random() < 0.5,
                     "flag_if_answered": True})
    if rng.random() < 0.3:
        text = "I checked everything."
        value = text if rng.random() < 0.5 else "I checked it all (old wording)."
        last.append({"id": "confirm", "type": "multiple_choice", "title": text, "required": True,
                     "options": [{"value": value}], "legacy_role": "confirmation"})
    return engine.normalize_schema({"sections": sections})


@pytest.mark.parametrize("seed", range(400))
def test_random_schemas_parse_and_submit_in_build5(seed):
    rng = random.Random(seed)
    schema = random_schema(rng)
    if engine.validate_schema(schema):
        pytest.skip("generator made an invalid schema (the editor refuses to save it)")
    served = engine.build_served(META, 1, schema)
    assert_build5_can_submit(schema, served, rng)
    # The whole served dict is plain JSON (the /schemas response).
    json.dumps(served)


# --- Edits of the seeded three ----------------------------------------------------------------

SEEDED = {
    ST: checklist_builtin.SPICE_TOWN_CLOSING,
    SK: checklist_builtin.STREET_KITCHEN_CLOSING,
    FSA: checklist_builtin.STREET_KITCHEN_FSA_CLOSING,
}


def _editor_copy(stored_legacy: dict) -> dict:
    """What the editor holds for a seeded checklist (items form)."""
    return engine.items_view(stored_legacy)


@pytest.mark.parametrize("ctype", [ST, SK, FSA])
def test_seeded_unchanged_and_items_form_submit_in_build5(ctype):
    stored = SEEDED[ctype]
    assert_build5_can_submit(stored, engine.build_served(META, 1, stored))
    items = engine.normalize_schema(_editor_copy(stored))
    served = engine.build_served(META, 2, items)
    assert "min_app_build" not in served
    assert_build5_can_submit(items, served)


@pytest.mark.parametrize("ctype", [ST, SK, FSA])
def test_every_text_rewording_of_a_seeded_checklist_keeps_build5_submitting(ctype):
    """The owner rewords everything (titles, rows, option texts, the
    confirmation). Build 5 sends option values and the confirmation label it
    was served, so every one of those must still validate."""
    items = engine.normalize_schema(_editor_copy(SEEDED[ctype]))
    for section in items["sections"]:
        section["title"] = (section.get("title") or "") + " (new)"
        for item in section["items"]:
            item["title"] = item["title"] + " - reworded"
            for row in item.get("rows") or []:
                row["title"] += " (new)"
            if item.get("legacy_role") == "confirmation":
                continue  # its option text is left as it was
            for option in item.get("options") or []:
                option["value"] += " (new)"
    assert engine.validate_schema(items) == []
    served = engine.build_served(META, 2, items)
    assert "min_app_build" not in served
    assert_build5_can_submit(items, served)


def test_reworded_confirmation_is_still_accepted_from_build5():
    """The confirmation item's title is edited but its single option keeps
    the old text. Build 5 sends `confirmation.label` (the new title); the
    confirmation accepts any non-empty value (the old rule), so it passes."""
    items = engine.normalize_schema(_editor_copy(checklist_builtin.SPICE_TOWN_CLOSING))
    confirmation = items["sections"][-1]["items"][-1]
    assert confirmation["legacy_role"] == "confirmation"
    confirmation["title"] = "I checked everything tonight."
    served = engine.build_served(META, 2, items)
    assert served["confirmation"]["label"] == "I checked everything tonight."
    assert engine.answer_errors(items, build5_answers(served)) == []


def test_items_only_last_section_still_gets_the_update_message_in_build5():
    """Regression: the last section gets a new-type question, so it is served
    items-only - but its trailing "anything unusual?" text and confirmation
    are still served top-level and build 5 sends them. They used to count as
    "questions only a newer app can show", so build 5's submission got
    "Missing answer for required item ..." instead of the update message."""
    items = engine.normalize_schema(_editor_copy(checklist_builtin.SPICE_TOWN_CLOSING))
    items["sections"][-1]["items"].insert(0, {"id": "q_when", "type": "time", "title": "Closed at", "required": True})
    served = engine.build_served(META, 2, items)
    assert served["min_app_build"] == 6 and served["confirmation"] and served["issues_field"]
    keys = engine.newer_app_keys(served)
    assert "q_when" in keys and "final_confirmation" not in keys and "issues_text" not in keys
    answers = build5_answers(served)
    assert engine.answer_errors(items, answers)
    assert not any(k in answers for k in keys)  # -> "Update the app to fill this checklist"


def test_confirmation_moved_out_of_the_last_place_still_works_in_build5():
    items = engine.normalize_schema(_editor_copy(checklist_builtin.SPICE_TOWN_CLOSING))
    confirmation = items["sections"][-1]["items"].pop()
    items["sections"][0]["items"].append(confirmation)
    served = engine.build_served(META, 2, items)
    assert_build5_can_submit(items, served)


# --- Over HTTP: every /schemas entry parses ------------------------------------------------------


def _every_type_sections():
    items = []
    for n, itype in enumerate(engine.ITEM_TYPES):
        fixed = {"id": f"t{n}", "type": itype, "title": f"{itype} question", "required": itype not in engine.DISPLAY_ONLY_TYPES}
        if itype in engine.CHOICE_TYPES:
            fixed["options"] = [{"value": "Yes"}, {"value": "No", "issue_tag": "issue"}]
        if itype in engine.GRID_TYPES:
            fixed["options"] = [{"value": "Done"}, {"value": "Not done", "issue_tag": "not_done"}]
            fixed["rows"] = [{"id": "a", "title": "Mop"}, {"id": "b", "title": "Bins"}]
        if itype in ("linear_scale", "rating"):
            fixed["scale"] = {"min": 1, "max": 5}
        if itype == "file_upload":
            fixed["file"] = {"max_files": 1, "types": ["image"]}
        if itype == "image":
            fixed["image_url"] = "https://example.com/x.png"
        items.append(fixed)
    return [{"id": "s1", "title": "Everything", "items": items}]


def test_schemas_endpoint_stays_parseable_for_build5_with_every_item_type(db, admin_client, checker_client, no_send):
    r = admin_client.post("/api/checklists/admin/definitions", json={"title": "Every type"})
    assert r.status_code == 200, r.text
    new_type = r.json()["checklist_type"]
    r = admin_client.post(f"/api/checklists/admin/definitions/{new_type}/publish", json={"sections": _every_type_sections()})
    assert r.status_code == 200, r.text
    # Edit each seeded checklist too (reworded + one new-type question in the first section).
    for ctype in (ST, SK, FSA):
        detail = admin_client.get(f"/api/checklists/admin/definitions/{ctype}").json()
        sections = copy.deepcopy(detail["editable"]["sections"])
        sections[0]["title"] = (sections[0].get("title") or "") + " (edited)"
        sections[0]["items"].append({"id": "q_when", "type": "time", "title": "Closed at", "required": False})
        r = admin_client.post(f"/api/checklists/admin/definitions/{ctype}/publish", json={"sections": sections})
        assert r.status_code == 200, r.text
    schemas = checker_client.get("/api/checklists/schemas").json()["schemas"]
    assert {ST, SK, FSA, new_type} <= set(schemas)
    for served in schemas.values():
        build5_parse(served)
    # Build 5's answers for a seeded checklist whose first section it can't
    # show get the update message, not a validation error.
    served = schemas[ST]
    r = checker_client.post(f"/api/checklists/{ST}/submit", json={"answers": build5_answers(served)})
    assert r.status_code in (200, 400), r.text
    if r.status_code == 400:
        assert r.json()["detail"] == checklist_submission.UPDATE_APP_MESSAGE


# --- Media access ----------------------------------------------------------------------------


def test_a_submission_photo_is_not_readable_by_other_staff(db, admin_client, checker_client, checker, no_send):
    """A file_upload answer's photo: the submitter and admins can load it,
    another staff member with the same checklist grant can't - also not by
    pasting its URL into an image item they don't control."""
    from tests.test_checklist_builder import _png_bytes
    from tests.conftest import client_for, make_user

    r = admin_client.post("/api/checklists/admin/definitions", json={"title": "Cooler photo", "sections": [{"id": "s1", "items": [
        {"id": "q_photo", "type": "file_upload", "title": "Photo", "required": True, "file": {"max_files": 1, "types": ["image"]}},
    ]}]})
    assert r.status_code == 200, r.text
    assert admin_client.post("/api/checklists/admin/definitions/cooler_photo/publish", json={}).status_code == 200
    upload = checker_client.post("/api/checklists/media", files={"file": ("c.png", _png_bytes(), "image/png")}).json()
    r = checker_client.post("/api/checklists/cooler_photo/submit", json={"answers": {"q_photo": json.dumps([upload["id"]])}})
    assert r.status_code == 200, r.text
    other = client_for(db, make_user(db, "other_staff", features=["daily_checklists"]))
    assert checker_client.get(upload["url"]).status_code == 200
    assert admin_client.get(upload["url"]).status_code == 200
    assert other.get(upload["url"]).status_code == 404
    # The submission itself is also hidden from the other staff member.
    assert other.get(f"/api/checklists/{r.json()['id']}").status_code == 404
    # An admin reusing that (already attached) photo in an image item doesn't
    # re-home it, so it stays private.
    r2 = admin_client.post("/api/checklists/admin/definitions", json={"title": "Reuse", "sections": [{"id": "s1", "items": [
        {"id": "img", "type": "image", "title": "Pic", "image_url": upload["url"]},
        {"id": "q", "type": "multiple_choice", "title": "Ok?", "required": True, "options": [{"value": "Yes"}]},
    ]}]})
    assert r2.status_code == 200
    assert admin_client.post("/api/checklists/admin/definitions/reuse/publish", json={}).status_code == 200
    assert other.get(upload["url"]).status_code == 404
