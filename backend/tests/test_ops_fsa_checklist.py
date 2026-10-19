"""Third Daily Closing Checklist (ops fix S): "Market Kitchen - Food Service
Associate Closing", built from the owner's Google Form
(docs/FSA_CHECKLIST_FORM.md). Covers the schema (rows/options/required rules
copied verbatim), validation (every grid row, at least one box per required
checkbox group, the required "anything unusual?" text), flagging, the API
submit path, the WhatsApp group it posts to (mocked - never reaches Whapi),
and the single 9:30 PM closing-checklist alert.

Sep 30 (checklist builder): the "Chef Name" question was dropped (owner:
"names with user only, no dropdown - like other checklists") - the current
FSA closing is version 2; submissions made before are version 1 and still
show their chef (tests/test_checklist_builder.py covers the versions).
"""

import datetime as dt
import json
import zoneinfo

import pytest

from app.config import get_settings
from app.models import ChecklistSubmission
from app.routers import checklists as checklists_router
from app.services import checklist_submission
from app.services.checklist_schemas import (
    closing_checklist_types,
    compute_status,
    get_schema,
    get_version_schema,
    list_schemas,
    short_name,
    summary_details,
    validate_answers,
)
from app.services.notification_triggers import closing_checklist_alerts

from tests.conftest import client_for, make_user

# Made-up rules in the shape these tests were written for: tests/fixtures/packs/suite.json.
pytestmark = pytest.mark.suite_pack

FSA = "kitchen_fsa"
NY = zoneinfo.ZoneInfo("America/New_York")


def _schema():
    return get_schema(FSA)


def _grids():
    for section in _schema()["sections"]:
        for grid in section.get("grids", []):
            yield grid


def _groups():
    for section in _schema()["sections"]:
        for group in section.get("checkbox_groups", []):
            yield group


def all_good_answers():
    answers = {}
    for grid in _grids():
        for row in grid["rows"]:
            answers[row["id"]] = grid["options"][0]["value"]  # "Yes" / "Done"
    for group in _groups():
        for item in group["items"]:
            answers[item["id"]] = "Done"
    answers["issues_text"] = "None"
    return answers


# --- Schema: exactly the Google Form ----------------------------------------------------


def test_schema_matches_the_form():
    s = _schema()
    assert list_schemas()[FSA] is s
    assert s["title"] == "Market Kitchen - Food Service Associate Closing"
    assert s["description"].startswith("Complete all closing tasks before leaving.")
    assert s["version"] == 2
    # No "Chef Name" any more (Sep 30): the form starts at "Turn off Heat".
    assert all(not sec.get("questions") for sec in s["sections"])
    # Version 1 (every submission before Sep 30) still has it: the chef on
    # duty, a required dropdown before "Turn off Heat".
    v1 = get_version_schema(FSA, 1)
    chef = v1["sections"][0]["questions"][0]
    assert v1["sections"][0]["title"] is None and v1["sections"][0]["grids"] == []
    assert chef["id"] == "chef_name" and chef["label"] == "Chef Name"
    assert chef["required"] is True and chef["input"] == "dropdown"
    assert chef["summary_label"] == "Chef"
    assert [o["value"] for o in chef["options"]] == ["Avery", "Hayden", "JL", "Dana", "Other"]
    assert all(o["issue_tag"] is None for o in chef["options"])
    # Otherwise the same form.
    assert [x.get("grids") for x in v1["sections"][1:]] == [x.get("grids") for x in s["sections"]]
    assert [x.get("checkbox_groups") for x in v1["sections"][1:]] == [x.get("checkbox_groups") for x in s["sections"]]
    assert s["sections"][0]["title"] == "Turn off Heat"
    assert s["sections"][0]["description"] == (
        "Turn off all heat sources, including Food Stand Stoves, Burners, Fryers, and other Cooking Equipment."
    )

    grids = {g["title"]: g for g in _grids()}
    off = grids["Have you Turned Off?"]
    assert [o["value"] for o in off["options"]] == ["Yes", "No", "Not Applicable"]
    assert [r["label"] for r in off["rows"]] == [
        "Stoves of the Hot Food Stand",
        "Panini / Paratha Warmer",
        "Flavored Rice Cooker",
        "Snacks/Samosa Stand",
        "Chaat Rice Cooker",
        "Both Fryers",
        "All Stoves",
        "Oven",
        '"Open" board light',
    ]
    assert [o["issue_tag"] for o in off["options"]] == [None, "not_turned_off", None]
    veg = grids["Wrap up Leftover Veggies"]
    assert [o["value"] for o in veg["options"]] == ["Done", "Not Applicable"]
    assert [r["label"] for r in veg["rows"]] == [
        "Trash old and mushy veggies",
        "Consolidate remaining into one container and get them ready for next day in Chaat station",
    ]

    groups = {g["title"]: g for g in _groups()}
    assert list(groups) == [
        "Wrap Up Leftovers & Sauce Bottles",
        "Clean up the entire area",
        "Re-organize Supplies and display boards and cutlery",
    ]
    assert [i["label"] for i in groups["Wrap Up Leftovers & Sauce Bottles"]["items"]] == [
        "Discard old or mushy vegetables",
        "Consolidate remaining vegetables into one container and keep ready for next day (Chaat station)",
        "Wrap up leftover proteins, biryani, sweets and other food items properly and keep them in cold "
        "storage appropriately for next day use",
        "Clean sauce bottles and move them to the main bain-marie",
        "Safely wrap Parathas, Breads, Naans and buns and clean the area",
        "Check with the chef before discarding any undated items in the cooler.",
    ]
    assert [i["label"] for i in groups["Clean up the entire area"]["items"]] == [
        "Outside Kitchen's customer area, Food stand see through glass & Counter top",
        "Sweets Cooler (Wipe off inside and outside)",
        "Rice Cooker (Wipe off inside and outside)",
        "Chaat Cooker (Wipe off inside and outside)",
        "Samosa Warmer (Wipe off inside and outside)",
        "Chaat Station (Wipe off inside and outside)",
        "Food Stand (empty the water in container wells and move ice bags into frontside glass door freezer)",
        "Panini / Paratha Machine and the area",
        "Printer table",
        "Surface of the Trash can next to printer",
    ]
    assert [i["label"] for i in groups["Re-organize Supplies and display boards and cutlery"]["items"]] == [
        "Pack and organize all packaging and cutlery supplies neatly.",
        "Inform chef about any shortage of supplies",
    ]
    assert all(g["required"] for g in groups.values())
    assert s["issues_field"]["required"] is True
    assert s["issues_field"]["label"] == (
        "Is there anything unusual? (like shortage of supplies, technical or mechanical issues etc.)"
    )
    # The same unique-id rule the other checklists follow.
    ids = (
        [r["id"] for g in _grids() for r in g["rows"]]
        + [i["id"] for g in _groups() for i in g["items"]]
    )
    assert len(ids) == len(set(ids))


def test_short_name_differs_from_the_chef_checklist():
    assert short_name(FSA) == "Market Kitchen FSA"
    assert short_name("kitchen_closing") == "Market Kitchen"
    assert short_name("store_closing") == "Main Store"


def test_schemas_endpoint_lists_it_and_answers_stay_strings(db):
    user = make_user(db, "asha", features=["daily_checklists"])
    r = client_for(db, user).get("/api/checklists/schemas")
    assert r.status_code == 200
    assert list(r.json()["schemas"]) == ["store_closing", "kitchen_closing", FSA]
    r = client_for(db, user).get(f"/api/checklists/schema/{FSA}")
    assert r.status_code == 200 and r.json()["checklist_type"] == FSA


def test_feature_gated_like_the_others(db):
    user = make_user(db, "nofeature", features=["team_tasks"])
    r = client_for(db, user).get(f"/api/checklists/schema/{FSA}")
    assert r.status_code == 403


# --- Validation -------------------------------------------------------------------------


def test_all_good_is_valid_and_all_clear():
    answers = all_good_answers()
    validate_answers(FSA, answers)
    assert compute_status(FSA, answers) == (True, None)


def test_not_applicable_is_fine():
    answers = all_good_answers()
    answers["g0_7"] = "Not Applicable"
    answers["g1_0"] = "Not Applicable"
    validate_answers(FSA, answers)
    assert compute_status(FSA, answers) == (True, None)


def test_every_grid_row_is_required():
    answers = all_good_answers()
    del answers["g0_8"]
    with pytest.raises(ValueError, match="Open"):
        validate_answers(FSA, answers)
    answers = all_good_answers()
    del answers["g1_1"]
    with pytest.raises(ValueError, match="Consolidate"):
        validate_answers(FSA, answers)


def test_chef_name_no_longer_asked():
    # Not required any more; an app still showing the old form may send it -
    # accepted and ignored (no chef line, never an issue).
    answers = all_good_answers()
    validate_answers(FSA, answers)
    answers["chef_name"] = "Bob"
    validate_answers(FSA, answers)
    assert compute_status(FSA, answers) == (True, None)
    assert summary_details(FSA, answers) == []


def test_version_1_still_requires_a_chef_from_the_list():
    answers = all_good_answers()
    with pytest.raises(ValueError, match="Chef Name"):
        validate_answers(FSA, answers, version=1)
    answers["chef_name"] = "Bob"
    with pytest.raises(ValueError, match="Invalid answer"):
        validate_answers(FSA, answers, version=1)
    answers["chef_name"] = "Other"
    validate_answers(FSA, answers, version=1)
    # Any chef is a normal answer - never an issue.
    assert compute_status(FSA, answers, version=1) == (True, None)


def test_summary_details():
    assert summary_details(FSA, {"chef_name": "JL"}, version=1) == [{"label": "Chef", "value": "JL"}]
    assert summary_details(FSA, {"chef_name": "JL"}) == []
    assert summary_details(FSA, {}, version=1) == []
    assert summary_details("store_closing", {"chef_name": "JL"}) == []
    assert summary_details("nope", {"chef_name": "JL"}) == []


def test_invalid_grid_value_rejected():
    answers = all_good_answers()
    answers["g0_0"] = "Maybe"
    with pytest.raises(ValueError, match="Invalid answer"):
        validate_answers(FSA, answers)


def test_required_checkbox_group_needs_at_least_one_box():
    answers = all_good_answers()
    for i in range(2):
        del answers[f"c2_{i}"]
    with pytest.raises(ValueError, match="Tick at least one box"):
        validate_answers(FSA, answers)
    # One box is enough to be accepted (Google Forms' required-checkboxes rule).
    answers["c2_1"] = "Done"
    validate_answers(FSA, answers)


def test_checkbox_value_must_be_done():
    answers = all_good_answers()
    answers["c0_0"] = "yes please"
    with pytest.raises(ValueError, match="Invalid answer"):
        validate_answers(FSA, answers)


@pytest.mark.parametrize("text", [None, "", "   "])
def test_unusual_text_is_required(text):
    answers = all_good_answers()
    if text is None:
        del answers["issues_text"]
    else:
        answers["issues_text"] = text
    with pytest.raises(ValueError, match="anything unusual"):
        validate_answers(FSA, answers)


# --- Flagging ---------------------------------------------------------------------------


def test_heat_left_on_flags_first():
    answers = all_good_answers()
    answers["g0_5"] = "No"
    all_clear, summary = compute_status(FSA, answers)
    assert all_clear is False
    assert summary.splitlines()[0] == "Not Turned Off:"
    assert "Both Fryers (Have you Turned Off?)" in summary


def test_unticked_box_flags_not_done():
    answers = all_good_answers()
    del answers["c1_8"]
    all_clear, summary = compute_status(FSA, answers)
    assert all_clear is False
    assert "Not Done:" in summary
    assert "Printer table (Clean up the entire area)" in summary


@pytest.mark.parametrize("text", ["No", "none.", "NOTHING", "N/A", "nothing unusual", "All good!"])
def test_nothing_to_report_is_all_clear(text):
    answers = all_good_answers()
    answers["issues_text"] = text
    assert compute_status(FSA, answers) == (True, None)


def test_real_unusual_text_flags():
    answers = all_good_answers()
    answers["issues_text"] = "Out of 12oz cups, fryer 2 making noise"
    all_clear, summary = compute_status(FSA, answers)
    assert all_clear is False
    assert "Issue:" in summary and "fryer 2 making noise" in summary


def test_optional_issue_fields_on_other_checklists_unchanged():
    """The "None" allowance is only for a REQUIRED text field - Main Store's
    optional one still flags anything typed, as before."""
    st = get_schema("store_closing")
    answers = {}
    for section in st["sections"]:
        for row in section["grid"]["rows"]:
            answers[row["id"]] = "Yes"
    answers["final_confirmation"] = "x"
    answers["issues_text"] = "No"
    assert compute_status("store_closing", answers)[0] is False


# --- Submission, storage, WhatsApp ------------------------------------------------------


@pytest.fixture
def no_background_send(monkeypatch):
    queued = []
    monkeypatch.setattr(checklists_router, "send_checklist_notification", lambda sid: queued.append(sid))
    return queued


def test_submit_via_api_stores_and_flags(db, no_background_send):
    user = make_user(db, "asha", features=["daily_checklists"])
    client = client_for(db, user)
    answers = all_good_answers()
    answers["g0_0"] = "No"
    del answers["c0_5"]
    r = client.post(f"/api/checklists/{FSA}/submit", json={"answers": answers})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["checklist_type"] == FSA
    assert body["checklist_title"] == "Market Kitchen - Food Service Associate Closing"
    assert body["submitted_by_name"] == "asha"
    assert "chef_name" not in body["answers"]
    assert body["details"] == []
    assert body["definition_version"] == 2
    assert body["all_clear"] is False
    assert "Stoves of the Hot Food Stand" in body["issue_summary"]
    assert "Check with the chef" in body["issue_summary"]
    # Every stored answer is a string (the App Store build parses them so).
    assert all(isinstance(v, str) for v in body["answers"].values())
    assert no_background_send == [body["id"]]

    # History: filterable by type and status.
    r = client.get("/api/checklists", params={"checklist_type": FSA, "status": "issues"})
    assert [i["id"] for i in r.json()["items"]] == [body["id"]]
    assert r.json()["items"][0]["details"] == []
    r = client.get(f"/api/checklists/{body['id']}")
    assert r.status_code == 200 and r.json()["checklist_type"] == FSA
    assert r.json()["details"] == []


def test_submit_without_chef_is_accepted(db, no_background_send):
    user = make_user(db, "asha", features=["daily_checklists"])
    r = client_for(db, user).post(f"/api/checklists/{FSA}/submit", json={"answers": all_good_answers()})
    assert r.status_code == 200, r.text
    assert r.json()["all_clear"] is True
    assert no_background_send == [r.json()["id"]]


def test_submit_missing_checkbox_group_is_400(db, no_background_send):
    user = make_user(db, "asha", features=["daily_checklists"])
    answers = all_good_answers()
    for i in range(10):
        del answers[f"c1_{i}"]
    r = client_for(db, user).post(f"/api/checklists/{FSA}/submit", json={"answers": answers})
    assert r.status_code == 400
    assert "Clean up the entire area" in r.json()["detail"]
    assert no_background_send == []
    assert db.query(ChecklistSubmission).count() == 0


def test_whatsapp_goes_to_the_market_kitchen_group(db, monkeypatch):
    """An FSA closing submitted before Sep 30 (no definition_version = version
    1, with the Chef question) still shows its chef."""
    monkeypatch.setattr(get_settings(), "whapi_kitchen_checklist_group_id", "sk-group@g.us")
    sent = []
    monkeypatch.setattr(checklist_submission, "send_whatsapp_message", lambda text, to=None: sent.append((to, text)))
    entry = ChecklistSubmission(
        checklist_type=FSA,
        submitted_by_user_id=1,
        submitted_by_name="asha",
        answers_json=json.dumps(dict(all_good_answers(), chef_name="JL")),
        all_clear=False,
        issue_summary="Not Turned Off:\n  - Oven (Have you Turned Off?)",
        answers_hash="h",
        created_at=dt.datetime(2026, 9, 29, 1, 5, tzinfo=dt.timezone.utc),
    )
    db.add(entry)
    db.commit()
    checklist_submission._attempt_whatsapp(db, entry)
    assert len(sent) == 1
    to, text = sent[0]
    assert to == "sk-group@g.us"
    assert "Market Kitchen - Food Service Associate Closing" in text
    assert "\nasha · Sep 28, 9:05 PM\nChef: JL\n\n" in text
    # The answers don't flag anything, so the text falls back to the stored summary.
    assert text.endswith("\n\nNot Turned Off:\n  - Oven (Have you Turned Off?)")
    assert entry.whatsapp_sent is True


def test_whatsapp_all_clear_shows_chef_and_other_checklists_have_no_chef_line(db):
    entry = ChecklistSubmission(
        checklist_type=FSA,
        submitted_by_user_id=1,
        submitted_by_name="asha",
        answers_json=json.dumps(dict(all_good_answers(), chef_name="JL")),
        all_clear=True,
        answers_hash="h",
        created_at=dt.datetime(2026, 9, 29, 1, 5, tzinfo=dt.timezone.utc),
    )
    text = checklist_submission._whatsapp_text(entry)
    assert "asha · Sep 28, 9:05 PM\nChef: JL\n\n*All done - no issues.*" in text
    # A new FSA closing (version 2) has no chef line, even if an old app sent one.
    entry.definition_version = 2
    text = checklist_submission._whatsapp_text(entry)
    assert "Chef:" not in text and "\nasha · Sep 28, 9:05 PM\n\n*All done" in text
    entry.definition_version = None
    entry.checklist_type = "kitchen_closing"
    assert "Chef:" not in checklist_submission._whatsapp_text(entry)
    assert checklist_submission.submission_out(entry)["details"] == []


# --- The single 9:30 PM closing-checklist alert -----------------------------------------


def _submit_row(db, ctype, when):
    db.add(
        ChecklistSubmission(
            checklist_type=ctype,
            submitted_by_user_id=1,
            submitted_by_name="asha",
            answers_json="{}",
            all_clear=True,
            answers_hash=f"h-{ctype}",
            created_at=when.astimezone(dt.timezone.utc),
        )
    )
    db.commit()


NIGHT = dt.datetime(2026, 9, 28, 21, 30, tzinfo=NY)


def test_fsa_is_a_closing_checklist():
    assert FSA in closing_checklist_types()


def test_one_alert_lists_every_missing_checklist(db):
    alerts = closing_checklist_alerts(db, NIGHT)
    assert len(alerts) == 1
    body = alerts[0].body
    assert body == (
        "Not submitted yet: Main Store - Daily Closing Checklist, "
        "Market Kitchen – Chef Closing Checklist and "
        "Market Kitchen - Food Service Associate Closing"
    )


def test_alert_lists_only_the_fsa_when_it_alone_is_missing(db):
    _submit_row(db, "store_closing", dt.datetime(2026, 9, 28, 21, 0, tzinfo=NY))
    _submit_row(db, "kitchen_closing", dt.datetime(2026, 9, 28, 21, 10, tzinfo=NY))
    alerts = closing_checklist_alerts(db, NIGHT)
    assert len(alerts) == 1
    assert alerts[0].body == "Not submitted yet: Market Kitchen - Food Service Associate Closing"


def test_no_alert_once_all_three_are_in(db):
    for ctype in closing_checklist_types():
        _submit_row(db, ctype, dt.datetime(2026, 9, 28, 21, 0, tzinfo=NY))
    assert closing_checklist_alerts(db, NIGHT) == []
