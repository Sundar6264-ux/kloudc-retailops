"""The checklist WhatsApp summary format (Oct 1): items grouped under their
section, "N items not done" / "N issues", the staff note at the end, a short
"<name> · <time>" line. Only the WhatsApp text changed - the stored
issue_summary (rendered by app builds 5 and 6) keeps its old format, which
test_checklist_builder.py's old-engine comparisons pin down."""

import datetime as dt
import json

from app.models import ChecklistSubmission
from app.services import checklist_engine as engine
from app.services import checklist_submission
from app.services.checklist_schemas import checklist_title, items_schema

FSA = "street_kitchen_fsa"
CHEF = "street_kitchen_closing"
STORE = "spice_town_closing"
# Oct 1 05:40 UTC = Oct 1, 1:40 AM EDT
CREATED = dt.datetime(2026, 10, 1, 5, 40, tzinfo=dt.timezone.utc)


def good(checklist_type: str, version=None) -> dict:
    """All-clear answers: first option everywhere, every box ticked."""
    answers = {}
    for _, item in engine.iter_items(items_schema(checklist_type, version)):
        itype = item.get("type")
        if item.get("summary_label"):
            answers[item["id"]] = "RK"
        elif itype == "multiple_choice_grid":
            for row in item["rows"]:
                answers[engine.row_answer_key(item, row)] = item["options"][0]["value"]
        elif itype == "checkboxes":
            for option in item["options"]:
                answers[option["id"]] = engine.CHECKBOX_DONE_VALUE
        elif itype in ("multiple_choice", "dropdown"):
            answers[item["id"]] = item["options"][0]["value"]
        elif itype in engine.TEXT_TYPES and item.get("required"):
            answers[item["id"]] = "None"
    return answers


def submission(checklist_type: str, answers: dict, version=None, public=False, name="asha") -> ChecklistSubmission:
    """A row exactly as create_submission stores it (status from the engine)."""
    all_clear, summary = engine.compute_status(items_schema(checklist_type, version), answers)
    return ChecklistSubmission(
        id=1,
        checklist_type=checklist_type,
        submitted_by_user_id=0 if public else 1,
        submitted_by_name=name,
        answers_json=json.dumps(answers),
        all_clear=all_clear,
        issue_summary=summary,
        answers_hash="h",
        created_at=CREATED,
        definition_version=version,
        via_public_link=True if public else None,
        public_name=name if public else None,
    )


def text_of(entry: ChecklistSubmission) -> str:
    return checklist_submission._whatsapp_text(entry)


def test_prod_example_grouped_under_sections(db):
    """Submission 9 in prod (Sadia, public link, Kitchen Closing - Service)."""
    answers = {"g0_0": "Yes", "g0_1": "Yes", "g0_2": "Yes", "g0_3": "Yes", "g0_4": "Yes", "g0_5": "Yes",
               "g0_6": "Yes", "g0_7": "Yes", "g0_8": "Yes", "g1_0": "Done", "g1_1": "Not Applicable",
               "c2_1": "Done", "c0_1": "Done", "issues_text": "TEST", "c1_6": "Done"}
    entry = submission(FSA, answers, version=2, public=True, name="Sadia")
    old_summary = entry.issue_summary
    assert text_of(entry) == (
        f"⚠️ *{checklist_title(FSA)}*\n"
        "Sadia (public link) · Oct 1, 1:40 AM\n\n"
        "*15 items not done*\n\n"
        "*Wrap Up Leftovers & Sauce Bottles*\n"
        "❌ Discard old or mushy vegetables\n"
        "❌ Wrap up leftover proteins, biryani, sweets and other food items properly and keep them in cold "
        "storage appropriately for next day use\n"
        "❌ Clean sauce bottles and move them to the main bain-marie\n"
        "❌ Safely wrap Parathas, Breads, Naans and buns and clean the area\n"
        "❌ Check with the chef before discarding any undated items in the cooler.\n\n"
        "*Clean up the entire area*\n"
        "❌ Outside Kitchen's customer area, Food stand see through glass & Counter top\n"
        "❌ Sweets Cooler (Wipe off inside and outside)\n"
        "❌ Rice Cooker (Wipe off inside and outside)\n"
        "❌ Chaat Cooker (Wipe off inside and outside)\n"
        "❌ Samosa Warmer (Wipe off inside and outside)\n"
        "❌ Chaat Station (Wipe off inside and outside)\n"
        "❌ Panini / Paratha Machine and the area\n"
        "❌ Printer table\n"
        "❌ Surface of the Trash can next to printer\n\n"
        "*Re-organize Supplies and display boards and cutlery*\n"
        "❌ Pack and organize all packaging and cutlery supplies neatly.\n\n"
        "*Note from staff*\n"
        "TEST"
    )
    # The stored summary the apps show is untouched.
    assert entry.issue_summary == old_summary
    assert old_summary.startswith("Not Done:\n  - Discard old or mushy vegetables (Wrap Up Leftovers & Sauce Bottles)")


def test_singular_and_mixed_counts(db):
    answers = good(FSA, 2)
    del answers["c1_8"]  # Printer table not ticked
    assert text_of(submission(FSA, answers, version=2)) == (
        f"⚠️ *{checklist_title(FSA)}*\n"
        "asha · Oct 1, 1:40 AM\n\n"
        "*1 item not done*\n\n"
        "*Clean up the entire area*\n"
        "❌ Printer table"
    )
    answers["g0_7"] = "No"  # Oven left on: not a "not done" item -> "issues"
    assert text_of(submission(FSA, answers, version=2)) == (
        f"⚠️ *{checklist_title(FSA)}*\n"
        "asha · Oct 1, 1:40 AM\n\n"
        "*2 issues*\n\n"
        "*Turn off Heat*\n"
        "❌ Oven\n\n"
        "*Clean up the entire area*\n"
        "❌ Printer table"
    )
    answers["g0_6"] = "No"
    assert "*3 issues*" in text_of(submission(FSA, answers, version=2))


def test_all_clear(db):
    entry = submission(FSA, good(FSA, 2), version=2)
    assert entry.all_clear is True
    assert text_of(entry) == f"✅ *{checklist_title(FSA)}*\nasha · Oct 1, 1:40 AM\n\n*All done - no issues.*"
    public = submission(STORE, good(STORE), public=True, name="Sai Durga")
    assert text_of(public) == f"✅ *{checklist_title(STORE)}*\nSai Durga (public link) · Oct 1, 1:40 AM\n\n*All done - no issues.*"


def test_only_a_note(db):
    entry = submission(FSA, dict(good(FSA, 2), issues_text="Fryer oil low"), version=2)
    assert entry.all_clear is False
    assert text_of(entry) == (
        f"⚠️ *{checklist_title(FSA)}*\nasha · Oct 1, 1:40 AM\n\n"
        "*All tasks done - see note.*\n\n*Note from staff*\nFryer oil low"
    )


def test_old_fsa_shows_chef_under_the_name(db):
    """Version 1 of the FSA closing asked for the chef."""
    answers = good(FSA, 1)
    assert answers["chef_name"] == "RK"
    entry = submission(FSA, answers, version=None)  # NULL = version 1
    assert text_of(entry) == (
        f"✅ *{checklist_title(FSA)}*\n"
        "asha · Oct 1, 1:40 AM\nChef: RK\n\n*All done - no issues.*"
    )


def test_answers_shown_only_when_they_add_information(db):
    answers = good(CHEF)
    schema = items_schema(CHEF)
    grids = {item["title"].strip(): item for _, item in engine.iter_items(schema) if item.get("rows")}
    marinated, frozen, cleanup = grids["Marinated?"], grids["Frozen Stuff"], grids["Cooking Equipment Cleanup"]
    answers[engine.row_answer_key(marinated, marinated["rows"][0])] = "No, I couldn't Marinate"
    answers[engine.row_answer_key(frozen, frozen["rows"][0])] = "Running Low (Good for a day)"
    answers[engine.row_answer_key(cleanup, cleanup["rows"][0])] = "I could not do"
    text = text_of(submission(CHEF, answers))
    # Several titled grids in one section: each grid is its own heading.
    assert f"*Marinated?*\n❌ {marinated['rows'][0]['title']}: No, I couldn't Marinate" in text
    assert f"*Frozen Stuff*\n❌ {frozen['rows'][0]['title']}: Running Low (Good for a day)" in text
    assert f"*Cooking Equipment Cleanup*\n❌ {cleanup['rows'][0]['title']}\n" not in text  # last group
    assert text.endswith(f"*Cooking Equipment Cleanup*\n❌ {cleanup['rows'][0]['title']}")
    assert text.count("❌") == 3 and "*3 issues*" in text
    # Store Closing: one grid per section -> the section title; "Issue / Not completed" left off.
    store = good(STORE)
    store["2_1"] = "Issue / Not completed"
    store["issues_text"] = "Cart broken"
    assert text_of(submission(STORE, store)).endswith(
        "*1 issue*\n\n*15 Minutes Before Closing*\n❌ All Shopping baskets inside?\n\n*Note from staff*\nCart broken"
    )


def test_builder_items_form(db, monkeypatch):
    """A checklist made in the editor: plain questions with their answer,
    a flagged text under its section, an untitled section with no heading."""
    schema = {
        "format": "items",
        "sections": [
            {"id": "a", "title": "Fridges", "items": [
                {"id": "temp", "type": "multiple_choice", "title": "Fridge temperature", "required": True,
                 "options": [{"value": "Below 41°F", "issue_tag": None},
                             {"value": "Above 41°F", "issue_tag": "issue"}]},
                {"id": "clean", "type": "multiple_choice", "title": "Fridge clean?", "required": True,
                 "options": [{"value": "Yes", "issue_tag": None}, {"value": "No", "issue_tag": "issue"}]},
                {"id": "repair", "type": "short_text", "title": "Needs repair", "flag_if_answered": True},
            ]},
            {"id": "b", "title": None, "items": [
                {"id": "lights", "type": "dropdown", "title": "Lights", "required": True,
                 "options": [{"value": "Off", "issue_tag": None}, {"value": "On", "issue_tag": "not_turned_off"}]},
            ]},
        ],
    }
    monkeypatch.setattr(checklist_submission, "items_schema", lambda t, v=None: schema)
    monkeypatch.setattr(checklist_submission, "checklist_title", lambda t: "Custom")
    monkeypatch.setattr(checklist_submission, "summary_details", lambda t, a, v=None: [])
    answers = {"temp": "Above 41°F", "clean": "No", "repair": "door seal", "lights": "On"}
    all_clear, summary = engine.compute_status(schema, answers)
    entry = ChecklistSubmission(id=1, checklist_type="custom", submitted_by_user_id=1, submitted_by_name="asha",
                                answers_json=json.dumps(answers), all_clear=all_clear, issue_summary=summary,
                                answers_hash="h", created_at=CREATED)
    assert text_of(entry) == (
        "⚠️ *Custom*\nasha · Oct 1, 1:40 AM\n\n*4 issues*\n\n"
        "*Fridges*\n❌ Fridge temperature: Above 41°F\n❌ Fridge clean?\n❌ Needs repair: door seal\n\n"
        "❌ Lights"
    )


def test_falls_back_to_the_stored_summary(db):
    """Unknown checklist / version or unreadable answers: the old summary text."""
    entry = ChecklistSubmission(id=1, checklist_type="gone_checklist", submitted_by_user_id=1,
                                submitted_by_name="asha", answers_json="{}", all_clear=False,
                                issue_summary="Not Done:\n  - Printer table (Clean up the entire area)",
                                answers_hash="h", created_at=CREATED)
    assert text_of(entry) == (
        "⚠️ *gone_checklist*\nasha · Oct 1, 1:40 AM\n\nNot Done:\n  - Printer table (Clean up the entire area)"
    )
    entry.checklist_type, entry.definition_version = FSA, 99
    assert text_of(entry).endswith("\n\nNot Done:\n  - Printer table (Clean up the entire area)")
    entry.definition_version, entry.answers_json = 2, "not json"
    assert text_of(entry).endswith("\n\nNot Done:\n  - Printer table (Clean up the entire area)")
    entry.all_clear, entry.issue_summary = True, None
    assert text_of(entry).endswith("asha · Oct 1, 1:40 AM\n\n*All done - no issues.*")


def test_status_matches_compute_status_on_random_answers(db):
    """flagged_groups finds exactly what compute_status flags (same count)."""
    import random

    rng = random.Random(7)
    for ctype, version in ((FSA, 1), (FSA, 2), (CHEF, None), (STORE, None)):
        schema = items_schema(ctype, version)
        for _ in range(300):
            answers = good(ctype, version)
            for key in list(answers):
                if rng.random() < 0.15:
                    del answers[key]
            for _, item in engine.iter_items(schema):
                if item.get("type") == "multiple_choice_grid":
                    for row in item["rows"]:
                        if rng.random() < 0.15:
                            answers[engine.row_answer_key(item, row)] = rng.choice(item["options"])["value"]
            all_clear, summary = engine.compute_status(schema, answers)
            flagged = engine.flagged_groups(schema, answers)
            count = sum(len(g["items"]) for g in flagged["groups"]) + len(flagged["notes"])
            assert count == (0 if all_clear else summary.count("\n  - ")), (ctype, answers)
