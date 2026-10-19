"""Reference copy of the checklist logic as it was before the checklist
builder (checklist_schemas.py at 854165d), parameterized by the schema dict
instead of the checklist type. Tests only: tests/test_checklist_builder.py
runs real stored answers through this AND the new engine and requires
identical results (validation, all_clear, issue_summary, details).
Do not "fix" anything here - it is the old behaviour on purpose.
"""

CHECKBOX_DONE_VALUE = "Done"

# Human-readable label per issue_tag, used to build the plain-language
# summary shown in the app and sent to WhatsApp - one place, reused by both.
ISSUE_TAG_LABELS = {
    "issue": "Issue",
    "morning_prep": "Morning Prep Required",
    "low_stock": "Low Stock",
    "critical": "Critical Issues",
    "cleanup": "Cleanup Not Completed",
    "not_turned_off": "Not Turned Off",
    "not_done": "Not Done",
}
# Display order for the summary/WhatsApp message - a heat source left on
# first (a safety problem), then critical, since it's the most actionable,
# cleanup last.
ISSUE_TAG_ORDER = ["not_turned_off", "critical", "morning_prep", "low_stock", "cleanup", "not_done", "issue"]

# Answers to a REQUIRED free-text "anything unusual?" field that mean
# "nothing to report" (compared after lowercasing and dropping punctuation).
# Anything else is reported as an issue - over-flagging is the safe side.
NOTHING_TO_REPORT = {
    "no", "nope", "none", "nothing", "nil", "na", "n a", "no issue", "no issues",
    "nothing unusual", "no nothing", "nothing to report", "no problem", "no problems",
    "all good", "all ok", "all okay", "all fine", "ok", "okay", "good", "fine",
    "everything is fine", "everything fine", "everything ok", "everything good",
    "everything is good", "everything is ok", "not really", "no unusual",
    "none all good", "no all good", "no everything is fine", "no everything fine",
}


def _is_nothing_to_report(text: str) -> bool:
    cleaned = "".join(ch if ch.isalnum() else " " for ch in text.lower())
    return " ".join(cleaned.split()) in NOTHING_TO_REPORT


def short_name(schema: dict) -> str:
    """A checklist's short name: its schema's `short_name` if it has one,
    else the title up to its first " - " / " – " ("Main Store - Daily
    Closing Checklist" -> "Main Store")."""
    if schema.get("short_name"):
        return schema["short_name"]
    title = schema["title"]
    for sep in (" - ", " – "):
        if sep in title:
            return title.split(sep)[0].strip()
    return title


def summary_details(schema: dict, answers: dict) -> list:
    """The answers a checklist wants shown next to "Submitted by" (its
    questions with a `summary_label`, e.g. the FSA closing's chef on duty):
    [{"label": "Chef", "value": "JL"}], in form order. Unknown types and
    unanswered questions are skipped."""
    details = []
    for question in _iter_standalone_questions(schema):
        label = question.get("summary_label")
        value = answers.get(question["id"]) if isinstance(answers, dict) else None
        if label and isinstance(value, str) and value.strip():
            details.append({"label": label, "value": value.strip()})
    return details


def _iter_grids(schema: dict):
    """Yields (grid_dict) for every grid in a schema, regardless of which
    checklist's slightly different section shape it came from (Main Store
    nests one grid directly under each section; Market Kitchen nests a list
    of grids under each section) - one shared walk instead of two per-type
    copies of the same traversal.
    """
    for section in schema["sections"]:
        grid = section.get("grid")
        if grid is not None:
            yield grid
        for grid in section.get("grids", []) or []:
            yield grid


def _iter_standalone_questions(schema: dict):
    for section in schema["sections"]:
        for question in section.get("questions", []) or []:
            yield question


def _iter_checkbox_groups(schema: dict):
    for section in schema["sections"]:
        for group in section.get("checkbox_groups", []) or []:
            yield group


def all_row_ids(schema: dict) -> set:
    """Every row/question id a real submission for this checklist type must
    answer - used to validate a submission actually covers the whole form,
    not just some of it.
    """
    ids = set()
    for grid in _iter_grids(schema):
        for row in grid["rows"]:
            ids.add(row["id"])
    for question in _iter_standalone_questions(schema):
        ids.add(question["id"])
    return ids


def _tag_for_value(options: list, value: str):
    for opt in options:
        if opt["value"] == value:
            return opt["issue_tag"]
    return "unrecognized"  # a value that isn't even one of the real choices


def validate_answers(schema: dict, answers: dict) -> None:
    """Raises ValueError with a clear message on the first problem found -
    every required row/question must be present, and every answered value
    must be one of that question's real choices. Never trusts the client's
    own notion of "valid" - this is what actually decides whether a
    submission is accepted at all.
    """
    for grid in _iter_grids(schema):
        valid_values = {opt["value"] for opt in grid["options"]}
        for row in grid["rows"]:
            value = answers.get(row["id"])
            if value is None:
                raise ValueError(f"Missing answer for required item: {row['label']!r}")
            if value not in valid_values:
                raise ValueError(f"Invalid answer {value!r} for {row['label']!r}")
    for question in _iter_standalone_questions(schema):
        value = answers.get(question["id"])
        if question["required"] and value is None:
            raise ValueError(f"Missing answer for required item: {question['label']!r}")
        if value is not None and question["options"]:
            valid_values = {opt["value"] for opt in question["options"]}
            if value not in valid_values:
                raise ValueError(f"Invalid answer {value!r} for {question['label']!r}")
    for group in _iter_checkbox_groups(schema):
        ticked = 0
        for item in group["items"]:
            value = answers.get(item["id"])
            if value is None:
                continue
            if value != CHECKBOX_DONE_VALUE:
                raise ValueError(f"Invalid answer {value!r} for {item['label']!r}")
            ticked += 1
        if group["required"] and ticked == 0:
            raise ValueError(f"Tick at least one box for: {group['title']!r}")
    issues_field = schema.get("issues_field")
    if issues_field and issues_field["required"] and not str(answers.get(issues_field["id"]) or "").strip():
        raise ValueError(f"Missing answer for required item: {issues_field['label']!r}")
    confirmation = schema.get("confirmation")
    if confirmation and confirmation["required"] and not answers.get(confirmation["id"]):
        raise ValueError(f"Missing answer for required item: {confirmation['label']!r}")


def compute_status(schema: dict, answers: dict) -> tuple:
    """The one place "all clear vs issues, and which ones" gets decided -
    server-side, from the schema's own issue_tag data, never trusting a
    client-computed status. Returns (all_clear: bool, issue_summary: str | None).

    issue_summary is a plain-language, human-readable block grouping every
    flagged row under its category (e.g. "Critical Issues: No Stock (No
    Stock) - Sella Rice"), used both for the app's own detail view and as
    the body of the WhatsApp message - one derivation, two consumers.
    """
    by_tag: dict = {}

    for grid in _iter_grids(schema):
        for row in grid["rows"]:
            value = answers.get(row["id"])
            tag = _tag_for_value(grid["options"], value) if value is not None else None
            if tag:
                label = row["label"]
                if grid["title"]:
                    label = f"{row['label']} ({grid['title']})"
                by_tag.setdefault(tag, []).append(label)

    for question in _iter_standalone_questions(schema):
        if not question["options"]:
            continue
        value = answers.get(question["id"])
        tag = _tag_for_value(question["options"], value) if value is not None else None
        if tag:
            by_tag.setdefault(tag, []).append(question["label"])

    # Checkbox groups: every box left unticked is a task not done.
    for group in _iter_checkbox_groups(schema):
        tag = group.get("unticked_tag")
        if not tag:
            continue
        for item in group["items"]:
            if answers.get(item["id"]) != CHECKBOX_DONE_VALUE:
                by_tag.setdefault(tag, []).append(f"{item['label']} ({group['title']})")

    issues_field = schema.get("issues_field")
    extra_note = None
    if issues_field:
        extra_note = (answers.get(issues_field["id"]) or "").strip() or None
        # A required "anything unusual?" box always has text in it - only a
        # real report counts, not "No" / "None".
        if extra_note and issues_field.get("required") and _is_nothing_to_report(extra_note):
            extra_note = None
        if extra_note:
            by_tag.setdefault("issue", []).append(extra_note)

    all_clear = not by_tag
    if all_clear:
        return True, None

    lines = []
    for tag in ISSUE_TAG_ORDER:
        items = by_tag.get(tag)
        if not items:
            continue
        label = ISSUE_TAG_LABELS.get(tag, tag)
        lines.append(f"{label}:")
        for item in items:
            lines.append(f"  - {item}")
    # Any tag not in the known order (shouldn't happen, but never silently
    # drop a real flagged answer) is appended at the end.
    for tag, items in by_tag.items():
        if tag in ISSUE_TAG_ORDER:
            continue
        lines.append(f"{tag}:")
        for item in items:
            lines.append(f"  - {item}")
    return False, "\n".join(lines)

