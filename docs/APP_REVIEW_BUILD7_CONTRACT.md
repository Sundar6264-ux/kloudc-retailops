# App Review build 7 - API contract (Oct 1, 2026)

Background and the full fix list: `/Users/sundar/spicetown-dev/APP_STORE_REVIEW_PLAN_2026-10-01.md`.
Every change is additive. Build 5 and build 6 apps on phones must keep working against this server.

## 1. Demo accounts
- New setting `DEMO_USERNAMES` (comma-separated, default `"app_store_review,appreview"`) in `app/config.py`.
  A helper `is_demo_user(user)` lives in one place.
- `GET /api/auth/me` (and the mobile-login response if it returns the user) gains `"demo": true|false`.
- What demo users can and can't do:
  - **Invoices:** submit and resend are saved but never sent. No WhatsApp, no Toast email. Each channel is recorded
    with the same shape as the "not configured" path, error text "Demo account - not sent", and the processing
    status shows as done, never failed. No failed-send alerts.
  - **Checklists:** `should_send_whatsapp` is False. Submissions are saved. Demo submissions do not count in the
    9:30 PM missing-checklist alert, and no alert is sent.
  - **Ask Bot:** answers with a fixed message, "Ask Inventory Bot is turned off for the demo account.", with no
    Anthropic call.
  - **Push notifications:** a demo user's actions (chat message, report, task comment) only notify members of
    teams the demo user belongs to. They never notify everyone.
  - **Labels app** (`~/spicetown-labels`): `/api/print` and `/api/price-changes/print` return 403 with
    `{"error": "Printing is turned off for the demo account."}` when the session's user is a demo user.
- **Chat scoping:** if either user is a demo user or is excluded from Everyone (`team_everyone_exclude`), they must
  share at least one team. Otherwise:
  - they are left out of `people()`;
  - `get_or_create_dm` returns 403 "You can only message people in your team.";
  - their messages aren't visible to each other.

## 2. Objectionable-content filter (server side, no app change)
- One module (`app/team/content_filter.py`) with a word list kept in the repo. It matches whole words, ignores case,
  and also catches simple leetspeak (a->@/4, e->3, i->1/!, o->0, s->$/5).
- It's applied on create and edit of: chat messages, task titles, descriptions and comments, announcements and
  their comments, and the "can't do" and completion notes.
- Matching words are **masked** with asterisks, keeping the first letter (`f***`). The request is not rejected.
- An admin can see in the moderation queue that a message was filtered: the message gains an additive
  `"filtered": true` field.

## 3. Team rules (EULA) acceptance
- `GET /api/team/rules` returns `{"version": 1, "title": "Team rules", "text": "<plain text, multi-paragraph>", "accepted": bool, "accepted_at": iso|null}`.
- The text says there is zero tolerance for objectionable content or abusive behaviour, that anyone can report
  content and block people, that admins review reports within 24 hours and remove content and access, and that
  violators lose access. It ends with a contact line: "Questions or concerns: ask your manager or call 860-237-4280."
- `POST /api/team/rules/accept` with `{"version": 1}` returns `{"accepted": true, "accepted_at": iso}`. Acceptance
  is stored per user and version, in a new table (migration 0043, down_revision "0042").
- The server does NOT block team endpoints when rules aren't accepted. The build 7 app gates the Team tab,
  because build 5/6 apps must keep working.

## 4. Reporting tasks and task comments (same admin queue as chat reports)
- `POST /api/team/tasks/{task_id}/report` with `{"reason": str, "note": str?}` reports a task's content: title,
  description and proof photos.
- `POST /api/team/tasks/{task_id}/comments/{comment_id}/report` with `{"reason": str, "note": str?}` reports a comment.
- `reason` uses the same values as the existing chat report (`router_chat.py` `/messages/{id}/report`).
- Both return the same response shape as a chat report and appear in `GET /api/team/reports` with additive fields
  `"target_type": "message"|"task"|"task_comment"` and `"target_id"`. Old chat reports default to "message".
  Resolve and dismiss work for all three.
- You can't report your own content: 400.

## 5. Deleting your own account
- `POST /api/auth/delete-account` with `{"password": str}`:
  - **200** `{"ok": true}`: the user row is deleted (or anonymised where foreign keys need it - follow what admin
    delete `DELETE /api/auth/users/{id}` already does), all their sessions and push tokens are removed, and the
    cookie is cleared.
  - **400** "Wrong password."
  - **409** "You are the last admin - make someone else an admin first."
- Demo users get **403** "The demo account can't be deleted." That stops a reviewer from accidentally locking out
  the review account during review. Mention it in the review notes.

## 6. Blocked users in team channels (app only)
- The app already has the user's block list (existing blocked-people endpoint). In team channels, messages from
  blocked people collapse to "Message from someone you blocked - tap to show".
- No server change is required. If the channel messages response lacks the author id, add it additively.

## Server implementation notes (Oct 1, 2026 - what was built, and where it differs from the text above)
Backend commit on `ops-fixes` (migration **0043**), labels app commit on `main`. Everything is additive.

- **Demo helper:** `app/demo.py` (`is_demo_user`, `demo_user_ids`, `DEMO_NOT_SENT`, `DEMO_ASK_ANSWER`).
  `"demo"` is on `/me`, `/login`, `/mobile-login` and the admin Users list.
- **Adjusted - demo users are always left out of Everyone**, even if a name is missing from `TEAM_EVERYONE_EXCLUDE`
  (`changes._everyone_excluded_names` unions both settings).
- **Invoices:** the "done" status is `processing_status: "completed"`. A demo user's Resend of its own submission
  re-records "Demo account - not sent"; **adjusted:** a demo user's Resend of a real staff invoice changes nothing and
  sends nothing (returns the record as it was). A demo-submitted invoice is never sent, whoever presses Resend.
  The invoice-failed alert ignores demo errors.
- **Checklists:** demo submissions also never close the 9:30 PM automatic "Submit the ... checklist" task.
  **Interpretation of "no alert is sent":** store-wide (broadcast) push alerts are never sent to a demo login;
  alerts about its own things (a task assigned to it) still are.
- **Ask Bot:** `{"answer": "Ask Inventory Bot is turned off for the demo account.", "model_used": "demo",
  "escalated": false, "tool_calls": [], "demo": true}`.
- **Pushes:** chat messages, reports and "can't do" from a demo user only reach its teammates. Task comments don't
  push today, so nothing to scope there.
- **Chat scoping:** "share a team" means a named team (not Everyone) that isn't archived. An existing DM between two
  out-of-scope people disappears for both (list, GET and send all 404). Message author id is already in every
  message as `sender.id` (section 6 needs no server change).
- **Content filter:** `app/team/content_filter.py`. Also applied to task sub-items. Announcements have no comments
  today, so only title/body are filtered. `"filtered"` is on every chat message (`message_out`) as well as in the
  report queue's `message` block. Words with an everyday grocery meaning ("cock", "negro" beans, "hoe", "rape"seed)
  are deliberately not in the list.
- **Team rules:** `POST /api/team/rules/accept` with any other version -> 400 "These rules have been updated - please
  read the latest version."; accepting again returns the first `accepted_at`. Stored in `team_rules_acceptances`.
- **Reports:** both new endpoints need the `team_tasks` feature and return `{"id", "status"}`; reporting twice returns
  the first report; a task the reporter can't see is 404 (same as GET). `comment_id` is the comment's task-event id
  (`events[].id` where `kind == "commented"`). The chat report also accepts the optional `note`.
  `GET /api/team/reports` adds `target_type`, `target_id`, `note`, `task` (task reports and comment reports) and
  `comment` (`{id, task_id, task_title, author, body, removed, created_at}`); `task.reported` is the text at report
  time. For task/comment reports `message` is filled with a chat-shaped summary (`id: null`) so build 5/6 admin apps
  still show what was reported. Resolve with `delete_message: true` removes the content: a task's title becomes
  "Removed by a manager", its description/notes/proof photo are removed and an open task is cancelled (a "moderated"
  event); a comment's text becomes "Comment removed by a manager" (`detail.removed: true`). The original text stays
  visible to admins in the report.
- **Delete account:** demo check (403) comes before the password check. Also removes Team-rules acceptances.
- **Labels app:** `STL_DEMO_USERNAMES` (default `app_store_review,appreview` - keep in step with `DEMO_USERNAMES`).
  The session is read from the dashboard cookie (or a Bearer token) via `STL_DASHBOARD_DB_PATH`, joined to `users`;
  the 403 applies whether or not `STL_REQUIRE_DASHBOARD_LOGIN` is on.

## Demo read-only guard (Oct 1, 2026 - after verification found demo writes to real store data)
Decision: a demo login may **look at** everything its features grant, and may only **write** to things that are its
own or sandboxed. Everything is additive; build 5/6 apps and real users see no change.

- **One central guard:** `app/demo_guard.py` `demo_write_guard`, passed to `FastAPI(dependencies=[...])` in
  `main.py`, so every route (including future ones) runs it. For a demo session, any POST/PUT/PATCH/DELETE (any
  path, not only `/api`) returns **403** `{"detail": "The demo account can look around but can't change the store's real data."}`
  unless `(method, route path)` is in `ALLOWLIST` (and passes its ownership check). GET/HEAD/OPTIONS are never
  touched; no GET export records anything (the PO PDF that records a `pdf` placed-order row is a POST, so blocked).
  Non-demo and logged-out callers pass straight through (the route's own auth still applies).
- **Allowlist** (everything else is refused):
  - Auth/self: `POST /api/auth/login`, `/mobile-login`, `/logout`; `POST /api/auth/delete-account` (route keeps its
    own 403 "The demo account can't be deleted."); `POST|DELETE /api/notifications/devices`,
    `POST /api/notifications/unregister`, `PUT /api/notifications/prefs`, `POST /api/notifications/test` (own phones).
    **Blocked:** `change-password` (a reviewer can't lock the review account).
  - Invoices: `POST /api/invoices` (sandboxed, never sent). On **its own submissions only**: resend,
    mark-toast-closed, unmark-toast-closed, request-delete, cancel-delete-request, override-status, approve-delete,
    delete, restore, `DELETE .../permanent` (admin-only ones still need admin). Anyone else's invoice -> 403.
  - Checklists: `POST /api/checklists/{type}/submit`, `POST /api/checklists/media`.
  - Ask bot / digest: `POST /api/ask`, `POST /api/weekly-digest/generate` (canned, no Anthropic call).
  - Team: `POST /api/team/rules/accept`; `POST /api/team/media`; announcement `seen` / `ack`;
    `POST /api/team/channels/dm`, `POST .../channels/{id}/messages`, `POST .../read`, `PATCH .../channels/{id}/me`
    (own mute/hide), `PUT /api/team/chat/settings` (own hide-previews); `DELETE /api/team/messages/{id}` **own
    message only**; message / task / task-comment report; `POST|DELETE /api/team/blocks`; task `complete`,
    `cant-do`, `comments`, `PATCH .../items/{item_id}` **only on tasks assigned to it or one of its teams**;
    `POST /api/team/templates/preview` (read-only). There is no chat message edit route today.
  - Tasks inside its sandbox (added Oct 1, 2026 - Undo / New task / Edit were 403s a reviewer would read as broken).
    "Its teams" = named, live teams it is a member of (prod: Review); never Everyone (a demo login is never in it),
    never an archived team, never another person:
    - `POST /api/team/tasks/{id}/reopen` (Undo): a task assigned to it **that it completed itself**, or any task
      assigned to one of its teams. The service's own rule still applies (creator, admin or whoever closed it).
    - `POST /api/team/tasks`: only when every assignee is itself (`assignee_user_ids` empty or only its own id) or
      `assignee_team_id` is one of its teams. A team task also needs `can_assign` on the account (service rule:
      otherwise 403 "You can only create tasks for yourself").
    - `PATCH /api/team/tasks/{id}` (edit / cancel): only a task **it created**, still assigned to itself or one of
      its teams; a reassignment must also stay inside (itself or one of its teams).
    - Anything else (a real staff member, itself plus anyone else, a team it isn't in, Everyone, someone else's task,
      ids sent as strings/booleans) -> the same 403 DEMO_READ_ONLY. Create/edit checks read the JSON body; the guard
      is async and reads it (cached by FastAPI) only for a demo session; DB work stays in the threadpool.
    - Pushes: `tasks_service._demo_scoped` keeps task_assigned, task_due (soon/overdue) and task_cant_do for a task a
      demo login created or acted on inside its teammates (`teammate_user_ids`). Demo-made tasks are never automatic
      tasks, so the WhatsApp fallback (auto_tasks, closing-checklist tasks only) never fires for them.
    - `POST /api/team/task-batches/{id}/cancel` stays blocked (a demo can't make a multi-person batch).
  - Blocked examples: cart add/qty/remove/clear/removal requests/mark placed/PO PDF/order again, vendor calendar
    schedules and skips, Fix Issues close/reopen, delivery confirm, purchase log add/upload/delete, transfer confirm,
    inventory upload, PO sections / category edits, user admin, checklist builder and public links, public checklist
    submit with a demo session, task create/edit/reopen outside its sandbox, announcements create/edit/remind, moderation, teams,
    schedules, sales sync, open-orders rescan.
- **Test:** `tests/test_demo_guard.py` enumerates `app.routes`: every route carries the guard, every allowlist entry
  is a real route, every mutating route not in the allowlist returns that 403 for a demo user, and none returns it
  for a real user. A new route is therefore blocked for the demo login by default.
- **Demo-made records are hidden from real staff** (`app/demo.py` `hide_demo_clause` / `hidden_from`; matched on the
  submitter's user id, or its stored username if the account is gone; rows with no submitter stay visible):
  - Invoices: `GET /api/invoices` and `/trash` (additive `?include_demo=true` for admins), `GET /api/invoices/{id}`
    (404 for non-admin staff), `check-duplicate`; always left out of `invoice-vendors-today`, reconciliation's
    invoice gaps (+ export), reorder's ordered/delivered and last-delivery logic, the invoice-failed alert and the
    invoice auto task. The demo user still sees its own (plus all real ones).
  - Checklists: admin `GET /api/checklists` and `/overview` hide demo submissions (`?include_demo=true` on the list),
    builder submission counts / last-submitted leave them out; non-admins already only see their own + public ones.
    Already excluded from the 9:30 PM alert and the nightly auto task.
- **Fix Issues:** a demo can't close/snooze any more; defensively the 9 AM `fix_issues_reopened` push also skips a
  reopen whose close was made by a demo username.
- **Labels app fails closed:** `dashboard_auth.demo_session_status` returns demo / real / unknown. When a session token
  is sent but can't be checked (no `STL_DASHBOARD_DB_PATH`, file missing, unreadable, any DB error), `/api/print` and
  `/api/price-changes/print` return **503** `{"error": "Printing is paused: we couldn't check your login. Try again in a minute."}`
  and a manual refresh skips auto-printing. No token at all (gate off) is unchanged.

## Pre-App-Store audit fixes (Oct 1, 2026)
- **Labels app:** `POST /api/products/custom` and `POST /api/price-changes/dismiss` now refuse a demo login with **403**
  `{"error": "The demo account can look around but can't change the store's real data.", "message": <same>}` (503
  "Saving is paused: we couldn't check your login..." when the session can't be checked). `/api/refresh` stays open
  (re-pulls Toast, never prints for a demo). A test walks every mutating `/api` route. 404 bodies from `/api/print`,
  `/api/print/<id>` and `/api/preview` gained a human `message` (other keys unchanged).
- **Invoice file** `GET /public/invoices/{id|token}` applies the same `hidden_from` rule as the detail (404 for staff on a
  demo submission; admins and the demo itself can open it).
- `POST /api/invoices/{id}/unmark-toast-closed` on an invoice that isn't closed: **409** "This invoice isn't marked closed
  in Toast, so there's nothing to undo. Refresh the list." (the app shows `detail`).
- Fix Issues closed report skips closes stored for an issue type that no longer exists; exports of a type without extra
  columns use the base columns.
- `.xls` uploads (inventory, purchase log): 400 "This is an old Excel file (.xls). Save the file as .xlsx or .csv and
  upload again." (xlrd is not installed in prod.)
- Inventory upload's default date and the Ask bot's "today" use `restaurant_today()`.
- Checklists: option values (and go-to keys) are trimmed on save; choice answers match options ignoring surrounding
  spaces and are stored with the definition's own spelling (`checklist_engine.canonical_answers`).
