# Checklist builder - shared contract (Sep 30)

Owner: "give access to admins to create / modify existing checklists questionnaires (give all the options
that google form gives)". Checklists move from hardcoded `checklist_schemas.py` into the database, editable by
admins on the website. The app renders every checklist from `GET /api/checklists/schema/{type}` (it already
does), so new question types need app build 6; the released build 5 must keep working for the 3 existing
checklists.

## Storage (backend agent)
- Tables (migration 0039, down_revision "0038"): `checklist_definitions` (checklist_type slug unique, title,
  short_name, description, active, sort_order, settings JSON, created/updated at+by) and
  `checklist_definition_versions` (definition id, version int, schema JSON, created_at/by, note).
  Publishing an edit creates a new version; the definition points at its current version.
- `checklist_submissions` gains nullable `definition_version` (additive). Old rows = the seeded version 1.
  History/detail always render a submission with the version it was submitted against.
- Seed: the 3 current schemas as version 1, exactly as served today, EXCEPT the FSA checklist drops the
  "Chef Name" question (owner: "names with user only, no dropdown, like other checklists") - old FSA
  submissions keep showing their chef answer (their version has it).
- `checklist_schemas.py` becomes a thin layer reading the DB (cached, invalidated on publish). If the tables
  are missing, fall back to the built-in dicts.

## Schema JSON (served by GET /api/checklists/schema/{type}; additive over today's shape)
```
{
  "checklist_type": "street_kitchen_fsa",
  "title": "...", "short_name": "...", "description": "...",
  "version": 3,
  "settings": {
    "closing": true,              // counted in the single 21:30 missing-checklist alert + nightly silent task
    "whatsapp_group": "store" | "kitchen" | null,
    "all_as_usual": true          // show the "All as usual" button on grid sections
  },
  "sections": [
    {
      "id": "s1", "title": "...", "description": "...",
      // LEGACY blocks (keep emitting for the 3 seeded checklists so build 5 renders them):
      "grid": {...}, "checkbox_groups": [...], "questions": [...],
      // NEW generic block (build 6+). Checklists created/edited in the new editor use ONLY "items":
      "items": [ <item>, ... ]
    }
  ]
}
```
When an admin edits one of the 3 seeded checklists, the backend converts it to `items` form. To keep build 5
working for them, the backend ALSO emits the legacy `grid` / `checkbox_groups` / `questions` blocks for any
section whose items are all legacy-compatible (grid of single-choice rows, checkbox group, single-choice
question, paragraph). Sections that use new types are emitted only as `items`; build 5 shows those checklists
with a "Update the app to fill this checklist" message (backend adds `"min_app_build": 6` on such schemas; the
app shows the message if its build is lower).

### Item
```
{
  "id": "q_abc123",               // stable; answers are keyed by it
  "type": "short_text" | "paragraph" | "multiple_choice" | "checkboxes" | "dropdown" | "linear_scale"
          | "rating" | "multiple_choice_grid" | "checkbox_grid" | "date" | "time" | "file_upload"
          | "title_text" | "image",
  "title": "...", "description": "..." | null,
  "required": true,
  "options": [ {"value": "Yes", "issue_tag": null}, {"value": "No", "issue_tag": "issue"} ],   // choice types
  "other_option": false,          // multiple_choice/checkboxes: "Other: ___"
  "shuffle": false,
  "rows": [ {"id": "r1", "title": "Stoves"} ],        // grid types
  "scale": {"min": 1, "max": 5, "min_label": "Poor", "max_label": "Great"},   // linear_scale / rating (max<=10)
  "validation": {"kind": "number"|"text"|"length"|"regex"|"checkbox_count", "rule": "...", "value": ..., "message": "..."}
                  | null,        // like Google Forms response validation
  "file": {"max_files": 3, "types": ["image"]},        // file_upload: photos via existing team media upload
  "image_url": "...",             // type "image" (display only)
  "go_to_section": {"Yes": "s3", "No": "submit"} | null   // multiple_choice/dropdown section branching
}
```
`title_text` and `image` are display-only (no answer).

### Answers (POST /api/checklists/{type}/submit, body unchanged: {"answers": {...}})
All answer values stay STRINGS (build 5 and old rows assume string maps):
- single value types -> the string ("Yes", "3", "2026-09-30", "21:30", free text)
- `checkboxes` -> JSON array string, e.g. `"[\"Wiped glass\",\"Printer table\"]"`
- grid types -> one key per row: `"{item_id}.{row_id}"`; checkbox_grid row value is a JSON array string
- "Other" -> `"Other: <text>"`
- file_upload -> JSON array string of media ids
Validation: required, per-row required for grids, validation rules, branching (skipped sections aren't
required). Issue flagging: an answer whose option has an `issue_tag` flags the submission (existing
ISSUE_TAG_LABELS; editor can pick from existing tags or "none"); the "anything unusual?"-style paragraph flag
= a paragraph item with `"flag_if_answered": true`.

## Admin API (backend agent; `require_admin`)
- `GET /api/checklists/admin/definitions` (all incl. inactive, with version count, submission count)
- `GET /api/checklists/admin/definitions/{type}` (current draft/published schema, versions list)
- `POST /api/checklists/admin/definitions` (create; slug from title), `PUT .../{type}` (save draft),
  `POST .../{type}/publish` (new version), `POST .../{type}/duplicate`, `PATCH .../{type}` (active, sort_order,
  settings, title), `GET .../{type}/versions/{n}`, `POST .../{type}/revert/{n}` (publish an old version as new)
- Validation of schema on save/publish (unique ids, required fields per type, branching targets exist).

## Public API changes (additive)
- `GET /api/checklists/schemas` lists active definitions ordered by sort_order (the app hub builds its tiles
  from this; build 5 already lists what the server returns? - check; if build 5 hardcodes 3 tiles, new
  checklists only appear in build 6+).
- Notifications / nightly tasks / Overview tiles use `settings.closing` instead of a hardcoded list;
  WhatsApp routing uses `settings.whatsapp_group`.
