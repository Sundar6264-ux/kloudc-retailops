# In-app checklist builder + WhatsApp group choice - contract (Oct 1, 2026)

The owner asked for: "give users an option to build their own custom checklists like Google Forms, in the app too.
On every custom checklist, give an option to choose which WhatsApp group it should be sent to."

The website builder already exists. It is the reference implementation:
- `frontend/src/components/checklists/ChecklistEditor.jsx`, `ItemEditor.jsx`, `model.js`;
- backend `app/routers/checklist_admin.py` and `app/services/checklist_admin.py`;
- engine `app/services/checklist_engine.py`;
- the original contract, `docs/CHECKLIST_BUILDER_CONTRACT.md`.

The app version must produce **exactly the same JSON** as the website.
Everything here is admin-only. All changes are additive, so build 5/6/7 apps keep working.

## 1. WhatsApp groups (backend + website)
**Today:** `settings.whatsapp_group` is `"store" | "kitchen" | null`. It maps to the env chat ids
`WHAPI_SPICE_TOWN_CHECKLIST_GROUP_ID` / `WHAPI_STREET_KITCHEN_CHECKLIST_GROUP_ID` (`checklist_submission.group_id_for_group`).
`engine.WHATSAPP_GROUPS = ("store", "kitchen")` limits the allowed values.

**New:**
- **Storage:** table `whatsapp_groups` (migration **0044**, down_revision "0043").
  - Columns: `id`, `key` (unique slug), `label`, `chat_id` (Whapi chat id, e.g. `1203...@g.us`; nullable for the
    built-ins), `builtin` bool, `active` bool, `created_at`/`created_by`, `updated_at`/`updated_by`.
  - Seed two built-in rows: `store` "Spice Town staff group" and `kitchen` "Street Kitchen staff group", with
    `builtin = true` and `chat_id = NULL`. A NULL chat_id means "use the env setting", so existing behaviour is unchanged.
- **Resolving a group:** `group_id_for_group(key)` checks the table first. An active row returns its chat_id, or the
  env fallback for the built-ins. Unknown, inactive or empty returns "", meaning nothing is sent, as today.
- **Validation:** `settings.whatsapp_group` accepts any active key in the table, or null. Saving with an unknown key
  returns 400 with a clear message.
- **Admin API** (require_admin), under `/api/checklists/admin/whatsapp-groups`:
  - `GET ""` returns `{items: [{key, label, chat_id_masked, builtin, active, used_by: [checklist titles]}]}`.
    The full chat_id is never needed by clients: mask it to the last 6 characters.
  - `GET "/available"` lists the groups the store's WhatsApp number belongs to.
    - It calls Whapi `GET {whapi_base_url}/groups` (read-only; check Whapi's docs/shape and paginate with
      count/offset) and caches the result for 5 minutes.
    - It returns `{items: [{chat_id, name, already_added_key|null}]}`.
    - If Whapi isn't configured it returns `{items: [], error: "WhatsApp isn't connected"}`, never a 500.
      Tests must mock it; never call the real Whapi in tests.
  - `POST ""` with `{chat_id, label}` returns the new row. The key is slugified from the label and must be unique.
    The chat_id must look like a WhatsApp chat id.
  - `PATCH "/{key}"` with `{label?, active?}`.
  - `DELETE "/{key}"`: built-ins can't be deleted (400). A group used by any checklist returns 409 and names the
    checklists. Otherwise 204.
- `GET /api/checklists/admin/meta` `whatsapp_groups` now comes from the table: active rows,
  `[{key, label, builtin}]`, in the same shape plus the additive `builtin`.
- **Website:** the editor's WhatsApp group dropdown lists every active group plus "None (no WhatsApp message)".
  - A "Manage WhatsApp groups" panel (from the editor settings or the builder list) has:
    - the list with which checklists use each group;
    - add from "Available on WhatsApp" with a label;
    - rename;
    - turn off;
    - delete when unused.
- **Settings summary:** the WhatsApp message itself is unchanged. The checklist overview and history may show the
  group label (additive).
- **As built (Oct 1), details the above left open** (service `app/services/whatsapp_groups.py`, migration
  `0044_whatsapp_groups.py`, website `WhatsAppGroupsPanel.jsx`):
  - `GET ""` items also carry an additive `configured` bool (a chat id resolves: the row's own, or the env one for a
    built-in). `chat_id_masked` is `"…" + last 6 characters of the id before "@" + "@domain"`, e.g. `…678901@g.us`
    (a built-in shows its masked env id); `null` when there is none.
  - `GET "/available"` takes an optional `?refresh=1` to skip the 5-minute cache. Whapi errors are `{items: [],
    error: "Couldn't load the groups from WhatsApp right now. Try again in a minute."}` and are not cached.
    `already_added_key` also matches a built-in's env chat id.
  - `POST ""`: chat_id must match `^[0-9][0-9-]{4,60}@(g.us|s.whatsapp.net|c.us|newsletter)$`; label 1-60 chars.
    409 when the slug key already exists or the chat id is already added (naming that group).
  - `DELETE` 409 body: `{detail, used_by: [titles]}` (inactive checklists count too).
  - Validation message: `settings.whatsapp_group "x" isn't an active WhatsApp group. Choose one of: store, kitchen,
    ... - or null for no WhatsApp message`. A checklist whose group was turned off can still save its other
    settings with that same key; choosing a turned-off group is refused.
  - A stored key that isn't in the table is kept (not reset to null) and sends nothing; the nightly-task WhatsApp
    fallback resolves the same way. The submission's error then reads "This checklist's WhatsApp group isn't set
    up (or is turned off) - check Checklist builder > WhatsApp groups."
  - Website: "WhatsApp groups" button on the builder list, "Manage WhatsApp groups" under the Settings tab's
    dropdown (hidden when `meta.whatsapp_groups` has no `builtin`).

## 2. In-app checklist builder (Flutter, admins only)
- **Entry point:** Daily Checklists hub → "Checklist builder" (admins only). It also gets feature search entries:
  "checklist builder", "create checklist", "custom checklist", "whatsapp group".
- **Screens:**
  1. **Builder list:**
     - All definitions from `GET /api/checklists/admin/definitions`: title, active on/off, draft badge, version,
       counts, last submitted.
     - Drag to reorder (`POST /definitions/order`).
     - "New checklist" (`POST /definitions` with a title).
     - Each row's menu: Edit, Duplicate, Turn on/off (`PATCH`), Public link (the existing screen).
  2. **Editor** (`GET /definitions/{type}`, which returns the draft or the published version):
     - Title and description.
     - Sections: add, rename, describe, reorder (drag), delete with confirm, and "after this section go to" (branching).
     - Items within a section: add (type picker with all `meta.item_types`), reorder (drag), duplicate, delete, move to
       another section.
     - Tapping an item opens the **item editor** (§3).
     - **Autosave the draft** with `PUT /definitions/{type}`, debounced at about 1.5 s with a "Saved"/"Saving…"
       indicator, as the website does. Handle a 409 (someone else saved) by reloading with a message.
     - **Draft check (added in the Oct 1 pre-release audit):** `PUT` and `POST .../publish` take an optional
       `base_draft_updated_at`: the `draft_updated_at` of the detail being edited, exactly as the server sent it
       (`null` = it had no draft). When the stored draft changed since (another admin saved, discarded, published
       or reverted), the server answers 409 `{detail: "Someone else saved this checklist since you opened it -
       reload to see their changes, then make yours again", problems: []}` and changes nothing. Without the key the
       save goes through as before (last write wins), so older apps and pages keep working. A value that isn't an
       ISO date-time or null is a 400. The app sends it on every autosave and publish (and reloads on 409); the
       website sends it on Save draft and Publish and offers "Reload their version".
     - "Discard draft" (`DELETE .../draft`).
     - **Preview:** render the draft with the existing fill-form widgets (`checklist_form_screen` /
       `checklist_item_fields`) in a read-only preview mode. Nothing is submitted.
     - **Publish:** `POST .../publish`. Show the server's validation errors per item and jump to the item.
  3. **Settings sheet:**
     - closing (counts in the 9:30 PM alert);
     - **WhatsApp group** dropdown from `meta.whatsapp_groups`, plus None;
     - "All as usual" toggle;
     - active.

     The app does not manage groups. It shows a hint: "Add more groups on the website: Checklist builder > WhatsApp groups".
  4. **Versions:** the list (`GET .../versions/{v}`), view one version read-only, "Restore this version"
     (`POST .../revert/{v}`, with confirm).
- **Unsupported values:** anything the app can't edit (an unknown item type from a newer server) is shown read-only
  as "Edit this on the website" and preserved unchanged in the JSON.

## 3. Item editor (Flutter), one sheet or page per item
Mirror `ItemEditor.jsx` for every type in `engine.ITEM_TYPES`:
- **Common fields:** title, description, required, help text.
- **Options:** for multiple_choice/checkboxes/dropdown: add, remove, reorder, an "Other" option, per-option
  "go to section".
- **Scale:** min/max and labels for linear_scale/rating.
- **Grids:** rows and columns.
- **Date/time options.**
- **File upload:** allowed file types and max files, from `meta.file_types` / `meta.max_files`.
- **Display-only items:** text and image blocks.
- **Response validation:** the rules for the type from `meta.validation`.
- **Issue flags:** which answers count as an issue, with tags from `meta.issue_tags`.

Read `model.js` and `ItemEditor.jsx` for the exact JSON keys, defaults and validation, and match them. A schema
saved from the app must round-trip unchanged through the website editor, and the other way round.

## 4. Must-haves
- **Layout:** works at the full device matrix: iPhone SE 320x568 at 1x and 2x text, 390, 430, iPad mini and
  iPad Pro in both orientations, Split View 678 and 904, and with the keyboard up. Use `test/support/device_matrix.dart`.
- **Sheets:** nested scrollables inside sheets must not collapse while scrolling, the class of bug just fixed in
  the PO sections picker. Save and Done buttons must always be reachable above the keyboard.
- **Demo account:** it never sees the builder, because it's admin-only. All writes are already 403 for demo.
- **Old servers:** if `meta` lacks `builtin` or the groups endpoints return 404/405, the app behaves (shows the
  dropdown from `meta`) and the website hides "Manage groups".
- **Tests:**
  - backend: groups CRUD, available (mocked Whapi), validation, resolution with fallback, delete-in-use 409,
    migration up and down;
  - app: round-trip JSON per item type against fixtures produced by the website's `model.js` defaults, every screen
    across the device matrix, autosave, the publish error jump, demo/non-admin can't reach it.
