# Critical vendors: Close / Skip this week / Remind me later - contract (Oct 6, 2026)

Owner's request: "For critical orders on the Today tab on the home screen, can you give admins options to
close, skip or remind later (skipped this week, that kind of thing)? It looks annoying every day in red."

Owner's decisions:
- **Which tile:** the red **Critical** tile, which lists vendors with critical stock (the Reorder Candidates urgency
  tier `critical`).
- **Who it affects:** when an admin closes, skips or snoozes a vendor, it is hidden for **everyone**. The action is
  store-wide, and who/when/why is kept in the history.

## Today
- **App:** the Home Today card's "Critical" tile comes from `todayCriticalVendorsProvider`
  (mobile `lib/features/home/providers/today_providers.dart`). It covers the vendors in
  `getReorderCandidates(lookbackDays: 30, leadTimeDays: 7)` that aren't inactive and have `urgency.tier == critical`.
  The tile is shown to users with the `reorder_candidates` feature.
- **Push:** the server sends the critical-vendors alert `critical_vendors_alerts` in
  `backend/app/services/notification_triggers.py`.
- **Website:** the Reorder Candidates page (and any Overview or summary card that shows critical vendors) shows the
  same data.

## Actions (admins only), per vendor, store-wide
1. **Close ("Handled")**
   - **Reason** (required pick):
     - "Ordered / will order"
     - "Not ordering from this vendor right now"
     - "Stock counts are wrong"
     - "Other" (with a note)
   - The vendor is hidden **until something new turns critical for it**. Store the item ids that are critical at the
     moment of closing. The vendor comes back when its current critical set contains an item that wasn't in the
     stored set.
   - The vendor also comes back after **14 days** at most, as a safety net so nothing hides forever.
2. **Skip this week**
   - Hidden until the start of next store week: **Monday 00:00 America/New_York**.
   - If today is Sunday, that's tomorrow. Optional note.
3. **Remind me later**
   - Snooze until a chosen time:
     - "Later today": 4 hours, but never past 9 PM store time. After 5 PM it becomes "Tomorrow 9 AM".
     - "Tomorrow 9 AM"
     - "In 3 days (9 AM)"
     - "Pick a date": 9 AM that day, at most 30 days ahead.
   - Optional note.

### Coming back
- A hidden vendor comes back the moment its condition ends. Either the time passes, or (for Close) a new item turns
  critical.
- A vendor that's no longer critical at all simply isn't shown. The dismissal stays recorded, and an active
  dismissal still applies if it turns critical again before it ends.
- Admins can **Show again** (undo) any active dismissal.

## Storage (migration 0047, down_revision "0046")
Table `critical_vendor_dismissals`:
- `id`, `vendor` (the vendor key exactly as the reorder report uses it).
- `kind`: `close`, `skip_week` or `snooze`. `reason` (key, for close), `note`.
- `until` (UTC). For close, it's created_at + 14 days.
- `critical_item_ids_json` (for close).
- `created_by` (id + name snapshot), `created_at`.
- `cleared_at`, `cleared_by` and `cleared_reason`: `show_again`, `new_critical_item`, `expired` or `superseded`.
- A new action on a vendor supersedes any active one.

`UTCDateTime` for timestamps; Python 3.9 rules apply.

## API (backend)
- **`GET /api/reorder/critical-dismissals`**: users with `reorder_candidates` get the active dismissals.
  - Each item is `{id, vendor, kind, reason, reason_label, note, until, created_by_name, created_at, ends_label}`.
    `ends_label` is human text, e.g. "Hidden until Mon Oct 12", "Until something new turns critical",
    "Until 1:30 PM".
  - `?include_history=1` (admins) adds the last 60 days.
- **`POST /api/reorder/critical-dismissals`** (admin only) with `{vendor, kind, reason?, note?, until?}`.
  - `until` is only used for snooze. The server computes `until` for skip_week, close and the snooze presets: the
    client sends `preset: "later_today" | "tomorrow" | "in_3_days" | "date"` plus `date` for "Pick a date".
  - Returns the dismissal. Unknown vendor: 404. A vendor that isn't critical can still be dismissed: 200, with a
    `warning` field.
- **`DELETE /api/reorder/critical-dismissals/{id}`** (admin only): Show again. Returns 204.
- **The reorder candidates response** (the vendor groups the app and website already read) gains these additive
  fields per vendor group:
  - `critical_dismissed: bool`, true when an active dismissal hides it;
  - `critical_dismissal: {id, kind, ends_label, created_by_name} | null`.

  The vendors and urgency are NOT removed, so the Reorder Candidates screens still show everything. Old app builds
  ignore the new fields and keep showing the vendor in their tile.
- **The critical-vendors push** (`critical_vendors_alerts`) leaves out dismissed vendors. If all are dismissed, no push.
- **The demo account:** read-only, already enforced by the demo guard. Don't add these routes to the allowlist.
- **Old builds:** responses are additive only.
- **Glossary terms:** `critical_close`, `critical_skip_week`, `critical_snooze`.

## App (build 8, still 2.0.0+8: build 8 hasn't been uploaded yet)
- **The Today card's Critical tile** counts and lists only vendors where `critical_dismissed` is false. When every
  critical vendor is hidden, the tile disappears.
- **Admins: long-press the tile, or use a small "⋯" on it**, to open a **Critical vendors** sheet. It lists each
  critical vendor with its critical item count and three buttons, **Close**, **Skip this week** and **Remind me
  later**:
  - Close asks for a reason, plus a note for "Other".
  - Remind later shows the presets plus a date picker.
  - At the bottom, **"Hidden (N)"** lists the active dismissals with `ends_label`, who did it, and **Show again**.
- **Non-admins** see the filtered tile only, with no actions.
- **Reorder Candidates:** a vendor card that's hidden from Today shows a small "Hidden from Today: <ends_label>" chip.
  Admins can tap the chip to Show again.
- **Undo:** after an action, a snackbar offers **Undo**, which calls Show again.
- **Layout:** works across `test/support/device_matrix.dart`: iPhone SE at 1x and 2x text, 390, 430, iPad mini and
  Pro in both orientations, Split View, with the keyboard up for the note field.

## Website
- **Reorder Candidates page:** for admins, each critical vendor card gets the same three actions, plus a
  "Hidden from Today" chip with **Show again**.
- **A "Hidden critical vendors" panel**, or a filter: active dismissals and the 60-day history.
- **If the website has a Today/Overview critical summary,** filter it the same way.
- **Help and glossary:** add the new terms.
- **CSS gotcha:** custom button hovers need `:not(:disabled)`.

## Non-goals
Per-user hiding; changing how urgency is computed. Vendor minimum orders still apply: don't change what counts as
critical.

## Backend API details (as built, Oct 6 - backend agent)
Exact shapes, so the app and the website agree with the server:
- **`GET /api/reorder/critical-dismissals`** returns
  `{"items": [...], "reasons": [...], "presets": [...], "can_edit": bool}`. For an admin with `?include_history=1`
  it also has `"history": [...]` (dismissals made in the last 60 days, newest first). A non-admin's
  `include_history` is ignored.
  - Item: `{id, vendor, kind, kind_label, reason, reason_label, note, until, created_by_name, created_at, ends_label,
    critical_item_count}`. `critical_item_count` is set for close only, else null. History items also have
    `active`, `cleared_at`, `cleared_by_name`, `cleared_reason` and `cleared_reason_label`.
  - `reasons`: `[{key, label, needs_note}]`. `presets`: `[{key, label}]`.
- **Close reason keys:** `ordered` ("Ordered / will order"), `not_ordering` ("Not ordering from this vendor right
  now"), `counts_wrong` ("Stock counts are wrong"), `other` ("Other", a note is required). The label text is
  accepted too.
- **`POST` body:** `{vendor, kind: "close" | "skip_week" | "snooze", reason?, note?, preset?, date?, until?}`.
  - `note`: up to 500 characters.
  - `preset`: `"later_today" | "tomorrow" | "in_3_days" | "date"`, with `date: "YYYY-MM-DD"` for "date".
  - `until` (ISO datetime) is only read for a snooze sent without a preset.
  - **200:** the item above plus `"warning"` (null, or a message when the vendor isn't critical right now).
  - **400:** `{"detail": "..."}` for a bad request; the text is ready to show the user.
  - **403:** non-admin, or the demo login. **404:** unknown vendor.
  - It computes the Today report to store the critical items, which takes about 3 s on prod data, so show
    progress.
- **`DELETE /api/reorder/critical-dismissals/{id}`:** 204, and every active dismissal of that vendor ends. An
  unknown id gets 404. A dismissal that has already ended also gets 204, and nothing changes (an Undo after
  someone else's newer action leaves that action in place).
- **Vendor groups** in `GET /api/ordering/reorder-candidates` gain these fields:
  - `critical_dismissed` (bool): true whenever an active dismissal exists for the vendor, whatever its tier.
  - `critical_dismissal`: `{id, kind, kind_label, ends_label, created_by_name, until, reason, reason_label, note,
    created_at}`, or null.
  - A Close is judged against the Today report (30 days of sales, 7-day lead time, as of today). The report with
    those settings, the 9 AM push and every new dismissal end a Close once something new turns critical. Other
    lookback or lead-time settings only show the stored state.
- **`ends_label` always starts with "Until"**, so it reads after "Hidden from Today: ".
  - Skip this week: "Until Mon Oct 12".
  - Close: "Until something new turns critical".
  - Remind me later: "Until 1:30 PM", "Until tomorrow 9:00 AM" or "Until Fri Oct 9, 9:00 AM".
  - **Deviation:** the earlier example "Hidden until Mon Oct 12" is now "Until Mon Oct 12".
- **Time rules:**
  - "Later today" means after 5 PM, so 5:00 PM itself still gets 9 PM.
  - "Pick a date": today counts only before 9 AM. The latest date is today + 30.

## App details (as built, Oct 6 - app agent, build 8, still 2.0.0+8)
- **Today tile:** counts and names only vendors whose `critical_dismissed` is false. A dismissal whose `until` has
  passed counts as ended (the tile looks again at that moment, so a snooze ends on time even with Home left open).
  No vendors left: no tile.
- **Who gets actions:** admins, never the demo login, and only from a server that sends `critical_dismissed`. They
  get a small "⋯" in the tile's corner (where the tile's icon is) and a long-press on the tile. On an older server
  nobody gets the actions and every vendor counts, as before.
- **Critical vendors sheet:** each vendor on the tile with "N critical items" and Close / Skip this week / Remind me
  later. Each one opens a small sheet:
  - Close: a reason (the server's `reasons`), with the note required for Other.
  - Skip this week: shows "Until Mon Oct 12" and takes an optional note.
  - Remind me later: the presets with a preview of when each ends. "Later today" isn't offered after 5 PM.
    "Pick a date" runs from today (only before 9 AM) to today + 30.
  - Every sheet takes a note of up to 500 characters and shows progress while the POST runs.
- **Hidden (N):** the GET items still active, each with kind, `ends_label`, reason, note and who/when. A vendor that
  isn't on the Critical list says "Not critical right now". Each has Show again.
- **Undo:** right after an action, a snackbar-style bar inside the sheet offers Undo for 8 s. A real SnackBar would
  sit under the sheet. If the sheet is closed within that time, Undo moves to a real snackbar on Home. Undo is the
  DELETE.
- **Immediate updates:** the tile, the sheet and Reorder Candidates change the moment the server says yes. The app
  lays this session's own changes over what it last loaded, and anything loaded afterwards wins. The heavy reorder
  report isn't reloaded for an action. Home's refresh reloads it.
- **Reorder Candidates:** a vendor with `critical_dismissed` shows "Hidden from Today: <ends_label>". An admin can
  tap it to see the details, with Show again.
- **Search:** "Critical Vendors on Today" (admins, not demo). It's found by "critical", "skip vendor", "snooze" and
  similar words, and opens the sheet. Help explains the three actions.
- **Glossary:** the app doesn't reference `critical_close`, `critical_skip_week` or `critical_snooze` yet, because
  the bundled glossary hasn't been regenerated. The three action sheets are where their (i) buttons would go.
- **Note for the backend:** GET's `can_edit` is `user.is_admin`, so it's true for the demo login (an admin). The
  app hides the actions for `demo` itself. `is_admin and not demo` would be more exact.

## Verifier fixes (as built, Oct 6 - verifier)
Found by an adversarial pass (prod copy + Playwright at 1280 / 375 px); each has a regression test in
`backend/tests/test_critical_dismissals_edge.py`.
- **One vendor, two spellings.** The reorder report groups vendors by the exact name Toast gives, but a dismissal
  matches ignoring case and outer spaces. A vendor spelled two ways ("Vdyas" / "VDYAS") is two groups and one
  dismissal hides both. A Close now stores and compares the critical items of every group of the vendor
  (`critical_dismissals.groups_by_key`). Before, only the group listed last counted, so a new critical item could
  be missed, or the groups trading places brought the vendor back. Prod has no such pair today (55 suppliers in the
  Oct 6 upload, all distinct ignoring case).
- **Warning for a Close on a vendor that isn't critical** (`NOT_CRITICAL_CLOSE_WARNING`): "This vendor isn't
  critical right now, so it isn't on the Today tile. If something new turns critical for it, it shows up there."
  The old text ("If it turns critical before this ends, it stays hidden") was wrong for a Close: with no critical
  item stored, anything critical is new. Skip this week / Remind me later keep the old text, which is true for them.
- **Two admins at once.** Settling, replacing the vendor's active dismissal and adding the new one is one step per
  process (`_write_lock`, as in `purchase_order_log`), so two POSTs finishing their report together no longer
  leave two active dismissals for one vendor.
- **`can_edit`** in GET is `is_admin and not demo`.
- **Server date vs store date.** The reorder endpoint checks the date its report was worked out for (the server's
  date when `as_of` is left out) against the store's date, so a report for another day never ends a Close.
- **Website.** Show again (chip, panel or Undo) follows the server's list afterwards: if another admin has hidden
  the vendor again since the page loaded, the row keeps their tag and a message says who. Calculate reloads the
  Hidden from Today list, since working out the Today report can end dismissals.
- **Checked, not changed:** an item listed under several suppliers sits under the vendor it was last received
  from, so it can move to a closed vendor. If it's critical, that counts as new for that vendor and the Close ends
  (as the contract says). A vendor renamed in Toast shows on the tile under its new name, while the old
  dismissal stays in the Hidden list until it ends or someone uses Show again. History isn't paged (300 rows is
  about 140 KB).

## App review fixes (as built, Oct 6 - app review, build 8, still 2.0.0+8)
Adversarial pass over the app side; each fix has a regression test that fails without it.
- **The Critical vendors list kept its place.** The in-sheet Undo bar coming up (after every action) and going
  (8 s later) rebuilt the sheet's scroll view, so after acting on a vendor further down the list jumped back to the
  top, twice. The sheet scrolls on every iPhone with prod's 7 vendors. `AppSheetBody(keepScrollPosition: true)`
  now moves the one scroll view between layouts instead of rebuilding it. The flag is opt-in, so other sheets are
  unchanged.
- **A note in landscape.** With the keyboard up on an iPhone SE or 15 turned sideways, the Close / Skip / Remind
  sheets have no room to pin their buttons, so the buttons move into the scroll. That rebuilt the note field and
  closed the keyboard as it opened, so a note couldn't be typed. The same flag on the three sheets fixes it.
- **A save that finished after the sheets were closed** (swiped away during "Hiding…", then Critical vendors
  closed) hid the vendor without a word or an Undo. Home's snackbar now offers Undo. An Undo already on its way
  when the sheet closes isn't offered again there, and a failed Undo says why and is offered again.
- **Hidden (N) lists one row per vendor** (the newest). Two active rows for a vendor (possible before the backend's
  write lock) crashed the list, because its rows are keyed by vendor.
- **"Later today" preview counts 4 real hours**, as the server does, on the night the clocks change. The app's
  previews now match the server's own `options_out` at 11 boundary times. A contract test reads JSON produced by
  the backend's serializers (mobile `test/features/reorder/fixtures/critical_dismissals_backend.json`).
- **Checked, not changed:**
  - Request and response shapes against the backend code (151721b).
  - Double taps send one request.
  - A phone in another time zone (IST, UTC+14, UTC-11) uses the store's date throughout.
  - An older server, and the staff and demo logins.
  - Undo racing Home's refresh is now pinned by a test.
- **Known, not changed:** Home reloads the Today report only on its refresh. A vendor hidden or shown again
  elsewhere (another admin, the website) shows on the tile after a refresh. Until then, the sheet's Hidden list,
  loaded when the sheet opens, can disagree with its vendor list.

## Final pass (as built, Oct 6 - final stage)
Every item above was checked against the backend, the website and the app. All are met; the only deviations are the
ones the sections above already record (`ends_label` starts with "Until", Undo first as an in-sheet bar, the app's
own check of `until`). Changes in this pass:
- **Glossary in the app.** The bundled copy (mobile `lib/core/glossary/glossary_data.dart`) was regenerated with
  `scripts/export_glossary.py`: it gains `critical_close`, `critical_skip_week` and `critical_snooze`, and
  `home_critical` now says hidden vendors are left out. The Close / Skip this week / Remind me later sheets each have
  an (i) for their term (`criticalDismissalTermKeys` in `glossary_keys.dart`). This replaces the "Glossary" bullet in
  the app section above.
- **Home's Refresh all** has its own test: it reloads the Critical report itself (`todayCriticalLoadProvider`), so a
  vendor hidden or shown again elsewhere follows on the next refresh.
- **Migration test.** `test_migration_0047_up_down_up[dev]` steps a copy that is already at 0047 back to 0046 first.
  Before, it failed as soon as the dev database was migrated (or synced from a migrated prod).
- **Dev database** (`backend/spicetown-dev.db`) migrated to 0047, so the dev server on 8001 serves the feature.
  Prod is untouched (0046 until the deploy).
- **Rehearsal on a read-only copy of prod (Oct 6, 06:52), with prod's Python 3.12:**
  - 0046 to 0047 took 0.5 s; integrity and foreign keys were OK.
  - The same 7 Critical vendors as above. With Vdyas skipped, Chetak closed (8 items stored) and Best Foods snoozed,
    the tile and the push dry run (nothing sent) showed the other 4 vendors.
  - Show again gave 204, and 204 again on the same, already-ended dismissal. An unknown vendor got 404, Other without
    a note 400, and staff and the demo login 403. A vendor that isn't critical got the right warning for Skip and for
    Close.
  - POSTs took 1.5 to 2.2 s.
  - The copy was deleted afterwards.
- **Deploy runbook:** `/Users/sundar/spicetown-dev/DEPLOY_CRITICAL_DISMISS.md`.
