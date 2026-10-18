# Batch Oct 15 - contract

Owner decisions and what each area built in the Oct 15 batch (ops fixes: tasks, notifications, speed, navigation, insights). Each area appends its own "As built - <AREA>" section below.

## As built - INSIGHTS (backfill fix)

Why: on Oct 14 an off-schedule fill at 01:01 ET (before the 04:00 closeout) used all 30 check calls, the 04:30 nightly
had none left for Oct 13, nothing retried, and the website said "They're filled in the background" (untrue).

Server (`services/insights.py`, `services/insights_data.py`, `services/toast_analytics.py`, `scheduler.py`). No migration
(0057-0059 untouched), no new route, no write route (demo guard unaffected).
- **Envelope (additive)** on every `/api/insights/*` answer: top-level `note` (plain words; **null whenever `stale` is
  false**) and `coverage.missing_dates` (ISO, newest first, max 10), `coverage.pending` (`"filling"` | `"waiting_budget"`
  | `"tonight"` | `"failed"` | null) and `coverage.retry_at` (UTC ISO when the rolling 24 h budget frees; only with
  `waiting_budget`). `dates_expected` / `dates_cached` / `stale` / `filling` unchanged (an unavailable day counts as not
  cached). The app (any build with Insights) already shows `note` first. Example notes: "Oct 13 isn't in yet: Toast lets
  us read check details 30 times a day and today's are used up. It's added automatically by 2:20 PM." / "Yesterday's
  numbers (Oct 13) arrive after the 4:30 AM update." / "Toast couldn't provide check details for Oct 3. We'll ask again
  with the 4:30 AM update." / "Oct 11 is being read from Toast now. Check back in a few minutes." (only while a fill
  really runs or was just started). "tonight" = the just-closed day, nightly not finished yet, until 07:00.
- **Loss watch** coverage counts only the chosen 7/14/30 days (`Loaded.subset`); the 30-day median window still loads.
  Odd checks 1 day with the day pending shows the note, not "0 of 1".
- **Reserve** (`fill_reserved`, used by the nightly and page-open fills): check / guest fill the last 2 closed dates
  first, older dates only above `DAILY_RESERVE` = check 4, guest 2 (module constants; config untouched).
- **Catch-up job** `insights_catchup`: hourly 05:20-23:20 store time (registered next to `insights_nightly`, same
  `INSIGHTS_NIGHTLY_ENABLED` switch). DB-only check of the last 2 closed dates of every report; **no Toast call when
  nothing is missing**; fills only while the budget has room; refreshes Loss watch flags after a check fill; skips while
  another fill holds the lock; writes a `job_runs` row only when it called Toast.
- **Channels 12 weeks**: `HISTORY_DAYS["metrics_dining"]` 63 -> 84.
- **Unavailable marker**: `toast_analytics.AnalyticsReportUnprocessable` (subclass of `AnalyticsReportFailed`, raised
  after the 404/409 new-request retries). `fill()` stores a dated marker (`analytics_cache` row, meta
  `unavailable: true`, no rows); pages don't read it as a $0 day, page opens and the catch-up don't re-spend on it, the
  nightly retries it once a night (`retry_unavailable`).
- **Truthful status**: `fill()` with dates to fetch and `max_calls <= 0` returns `error: "no calls left"`, `budget: true`
  (was `error: None`). Nightly `job_runs.status` = `failed` (real error) / `partial` (budget only, or yesterday still
  missing; `detail.warnings` like "check: Oct 13 waiting for budget (frees 1:17 AM)") / `success`.

Website: `insightsModel.staleText` returns the server `note` when present; fallback no longer says "filled in the
background" ("29 of 30 days are saved; the rest are added automatically."). Skill gotcha added (no hand-run fills before
the closeout; leave the reserve).

Tests: `tests/test_insights_backfill.py` (22, Toast always faked), `tests/test_insights.py` (fixture now lets a day pass
between its three nights; the budget-cap test runs with the reserve off), `insightsModel.test.mjs`.

Not done / notes: whatever started the 01:01 ET run is not in either repo (only a read-only `job_runs` query in
`DEPLOY_BUILD9.md`): it was a hand-run, hence the skill note. No "info" styling for `pending: "tonight"` on the
website (same warning look, softer words). `test_insights.py::test_loss_watch_alert_is_one_9am_push_to_admins` and
`::test_daily_sales_summary_push_skips_hidden_sales` fail against the NOTIFICATIONS work in progress (9 AM defaults
moved to the Morning summary); they're for that area to update.

## As built - NOTIFICATIONS (server)

Owner: "Recommended" defaults - fewer pushes, one 9 AM summary for admins, any alert can be switched back on.
Files: `services/notifications.py`, `services/notification_triggers.py`, `routers/notifications.py`,
`app/notification_models.py` (new), `scheduler.py` (the 9:00 jobs only), `team/chat_service.py` (pref filter + push log
lines only), migration `0058_notification_defaults.py`, `tests/test_notifications_defaults.py` (27 tests).

**Effective value (P1).** `effective = the user's own notification_prefs row (on or off) ?? admin store default for
their role ?? catalog default for their role` (role = `admin` if `is_admin` else `staff`). Used by `get_prefs`,
`recipient_tokens` (every store-wide and targeted alert), and chat/announcement pushes
(`chat_service.pref_disabled_user_ids` -> `notifications.disabled_user_ids`). Keys outside the catalog
(`chat_report`, `test`): only an explicit "off" row counts, as before. Prod has 0 pref rows, so the new defaults apply
to everyone on deploy; an explicit choice always wins over any later default change.

| key | group | staff | admin | essential | note |
|---|---|---|---|---|---|
| morning_summary (new) | daily_summary | off | **on** | | 09:00, route `home` |
| order_due | daily_summary | off | off | | summary line |
| critical_vendors | daily_summary | off | off | | summary line |
| open_orders_morning | daily_summary | off | off | | 09:00 count is a summary line; 16:00/21:30 opt-in |
| fix_issues_reopened | daily_summary | off | off | | summary line |
| daily_sales_summary | daily_summary | off | off | | summary line (can_see_sales only) |
| closing_checklist | needs_action | **on** | **on** | yes | |
| invoice_failed | needs_action | **on** | **on** | yes | now submitter + admins only (P8) |
| task_assigned | needs_action | **on** | **on** | yes | incl. Fix Issues lead tasks |
| task_due ("Task overdue") | needs_action | **on** | **on** | | overdue reminder of your own tasks |
| task_due_soon (new) | needs_action | off | off | | "Due in 30 minutes" |
| fix_issues_task_due (new) | needs_action | off | off | | due/overdue of system Fix Issues lead tasks |
| task_cant_do | needs_action | **on** | **on** | yes | |
| flyer_sent | needs_action | off | off | | |
| chat_message | team | **on** | **on** | | |
| chat_mention | team | **on** | **on** | yes | |
| announcement | team | **on** | **on** | yes | |
| task_done, task_comment (added by TASKS) | team | off | off | | |
| cart_removal_request | admin | - | **on** | yes | admin-only |
| loss_watch | admin | - | off | | admin-only; summary line |
| inventory_upload_due | admin | - | off | | **admin-only now** (P8); summary line |
| inventory_api_ready | admin | - | off | | admin-only; summary line |

Judgment call: `invoice_failed` (rare; the submitter's own invoice) and `cart_removal_request` (admin action item) are
not in the owner's lists - kept ON as essentials. One store-default PUT turns either off.

**task_due split without breaking old builds.** Tasks still send `key="task_due"` (payload `type`/`route` unchanged);
`notifications.alert_pref_key(db, alert)` picks the governing switch: `fix_issues_task_due` for a system lead task
(`created_by_user_id=0`, `source_type="issue_leads"`), `task_due_soon` when a dedupe key has `:soon:`, else the key.
`Alert.pref_key` (new optional field) overrides it explicitly.

**Morning summary (P2).** One job `notify_morning_summary` at 09:00 (`scheduler.run_morning_summary`: refresh today's
open orders, then `notification_triggers.run_morning_job`) replaces `notify_order_due`, `notify_critical_vendors`,
`notify_inventory_upload_due`, `notify_inventory_api_ready`, `notify_loss_watch`, `notify_daily_sales_summary`,
`notify_fix_issues_reopened` and `notify_open_orders_0900`. It runs each source trigger once (in `MORNING_SOURCES`
order), dispatches each alert on its own (reaches only users who turned that key on), then sends each user with
`morning_summary` on (device, not demo) ONE push "Morning summary", one line per source they can access, are targeted by
(`Alert.user_ids`) and don't already get separately. Dedupe `morning_summary:<date>:<user_id>`. Alerts that made it into
a delivered summary get their dedupe keys written to `notification_log` (so Fix Issues items back, loss-watch flags and
the Inventory-from-Toast streak are still "in one push only"). Fix Issues reopen honours SCHEDULER_ENABLED as before.
Prod-copy run for Oct 15 09:00: Sundar and RK each get 1 push ("Order tomorrow: Indigenous Foods / Loss watch: 1 check
to review / Opening: 25 open checks") instead of 3 each; staff phones get nothing at 09:00 (were 2 each).

**API (all additive; builds 5-9 keep reading `items[].key/label/description/enabled`).**
- `GET/PUT /api/notifications/prefs` -> `{"items": [...], "groups": [{key,label,description}]}`; items listed group by
  group (`needs_action`, `team`, `daily_summary`, `admin`) and gain `group`, `default` (what they'd get without their
  own choice), `essential` (confirm before switching off), `admin_only`, `explicit` (they chose it). New keys appear in
  old builds as plain switches. Label `task_due` is now "Task overdue".
- `POST /api/notifications/prefs/reset` -> same shape; deletes the caller's rows for catalog keys only (chat's
  hide-previews row stays). **Refused for the demo** (not allowlisted; PUT /prefs still allowed).
- `GET /api/notifications/recent?limit=50` (own) -> `{"items": [{key,label,title,sent_at}]}`.
- Admin only (`require_admin`; not on the demo allowlist): `GET /api/notifications/defaults` -> `{items: [{key, label,
  description, group, essential, admin_only, roles, catalog:{role:bool}, store:{role:bool|null}, effective:{role:bool}}],
  groups, changed_by, changed_at, history:[{id,note,changed_by,changed_at}]}`; `PUT /api/notifications/defaults`
  `{key, role: "staff"|"admin", enabled: bool|null}` (null = back to recommended; staff role on an admin-only key = 400)
  -> same state; `POST /api/notifications/defaults/reset`; `GET /api/notifications/volume?days=14` -> `{days, users:
  [{user_id, username, total, per_day, by_key}]}`.
- Store defaults (P4) = `setting_changes` key `notification_defaults`, value `{key: {role: bool}}` (overrides only),
  `previous_value`, `changed_by_*`, `note` ("Morning summary for staff: on"). A no-op change writes no row.

**Push log (P7a).** Table `push_deliveries(id, user_id, key, title, sent_at UTCDateTime)` (0058, indexes on user_id/key/
sent_at). One row per user reached, written after the send (best effort, own commit) by `dispatch_alert` and
`chat_service.push_to_users` (chat, mentions, announcements, reports). Titles only, never bodies. Pruned after 180 days
by the morning job; removed with the user on account deletion.

**Noise (P7b/c).** "has no recipients" is DEBUG (was 846 INFO lines/14 d); run_alert_job logs "found N" only when N>0 and
the quiet-hours skip at DEBUG. P5: quiet hours unchanged (22:00-07:00 store-wide).

**Helpers for other areas** (`services/notifications.py`): `effective_enabled(db, user, key)`, `enabled_user_ids(db,
key, users)`, `disabled_user_ids(db, key, ids)`, `deliverable_user_ids(db, key, ids)` (device + access + effectively on -
TASKS uses it with `alert_pref_key` so the 5-minute sweep skips unreachable people), `alert_pref_key(db, alert)`,
`log_deliveries(db, key, ids, title)`, `prefs_payload(db, user)`. TASKS already has its own background-send
(`tasks_service.after_response`), so no extra one was added here. A new alert = a catalog entry with `group` and
`defaults` (TASKS added `task_done`/`task_comment` that way).

Tests touched outside my files (smallest edits, defaults changed under them): `test_insights.py` (2 tests opt admins in),
`test_inventory_api_readiness.py` (`_devices` opts admins in), `test_flyers.py`, `test_ops_snooze_cartremove.py`
(`people` opts in; scheduler test expects `notify_morning_summary`), `test_team_tasks.py` (3 reminder tests opt in to
`task_due_soon`), `test_review_route_gates.py` (2 new login-only routes).

Not done / for others: app + website Notifications screens (P3 UI, grouping, Reset button, essential confirm, the
stale "new customer orders" copy is app-side `notification_settings_screen.dart`, nothing server-side mentioned it);
chat pushes still dropped (not queued) in quiet hours (optional P5); no per-alert "off for everyone" kill switch (set
both roles off instead); `models.NotificationPref` docstring still says "no row means enabled" (models.py not mine -
now "no row means the default").

## As built - TEAM TASKS (server)

Owner: tasks advanced and in sync end to end, as few taps as possible, app speed first. Everything below is **additive**
(builds 5-9 keep working: no field, route or shape removed or renamed; old requests behave as before). Files:
`team/tasks_service.py`, `team/router_tasks.py`, `team/auto_tasks.py`, `team/changes.py`, `team/router_core.py`,
`team/bootstrap.py`, `team/recurrence.py`, `team/tasks_models.py`, `team/source_sync.py` (new),
`services/issue_leads.py` (lead progress), `config.py` (checklist task switch only), migration
`0059_tasks_advanced.py`, tests `tests/test_team_tasks_oct15.py` (46 tests) + small edits in `test_team_tasks.py`,
`test_team_w5.py`, `test_issue_leads.py`, `test_demo_guard.py`. One-line hooks in others' files (see 14):
`routers/checklists.py`, `routers/checklist_public.py`, `routers/order_actions.py`, `routers/ordering.py` (mark-placed),
`routers/critical_dismissals.py` (POST), `routers/invoices.py` (mark-toast-closed, approve-delete, delete),
`routers/issues.py` (close, reopen). Catalog entries `task_done` / `task_comment` added to `services/notifications.py`.

**Migration 0059** (chained 0058 -> 0059): `tasks.progress_total INTEGER NULL`, `tasks.progress_done INTEGER NULL`;
downgrade = plain DROP COLUMN (as 0055). Tested up/down/up; prod copy upgraded 0056 -> 0059 in 1.7 s.

### Task shape (`task_out`, every list / detail / write response) - new fields
```json
{
  "id": 49, "title": "Fix Issues: 607 left of 611", "...": "all existing fields unchanged",
  "is_auto": true,
  "progress": {"done": 4, "total": 611, "left": 607},
  "action": {"kind": "fix_issues", "label": "Open the Fix Issues list", "route": "fix_issues",
             "params": {"lead": "12"}, "web_path": "/fix-issues?lead=12"},
  "action_route": "fix_issues",
  "can": {"complete": true, "edit": false, "reopen": false, "comment": true, "start": true, "unstart": false,
          "edit_items": false},
  "items": [{"id": 7, "text": "Wipe", "done": false, "done_by": null, "done_at": null, "position": 0}]
}
```
- `progress` null unless the task tracks a count (today: Fix Issues lead tasks). `is_auto` = made by the system.
- `action` (null for a plain task) = where the real work is; its main button should open that instead of a bare "Mark
  done" (the source then closes the task itself). `route` uses the push-route names every app build already parses
  (`push_target.dart`): `checklists` (params `checklist_type`, `day`), `supplier_projection` (`vendor`,
  `delivery_date`), `invoice` (`invoice_id`), `fix_issues` (`lead`). `web_path` is the website route (HashRouter path).
- `can.edit_items`: may add / rename / remove steps (creator or admin, task open; always false for the demo login).
- `items[].position` (detail only).

### 1. Empty teams
- Creating, reassigning, a schedule (template / weekday assignee) for a team with **0 members** -> `400 {"detail":
  "Kitchen has no members yet - add people to it on the Teams page, or assign it to a person."}`. Recurring generation
  skips (and logs once) an occurrence whose team is empty; automatic rules skip an empty team.
- `GET /api/team/teams` items and `GET /api/team/bootstrap` `teams[]` gain `member_count` (Everyone = all users minus
  excluded/demo). Clients: show "No members" and don't offer a 0-member team in the assign picker.
- `PUT /teams/{id}/members`: people who joined/left also get `task` feed rows for the team's open tasks (their lists
  refresh at once).

### 2. Closing-checklist tasks stopped; leftovers closed; order tasks expire
- `TEAM_AUTOTASK_CHECKLIST_ENABLED` default is now **false** (owner: the 9:30 PM push + Home card cover it). While off,
  the task job (every 5 min, idempotent) closes every still-open checklist auto task as `cancelled` with
  `completion_note` **"Missed - not submitted"** (completed_by_name "Spice Town", event `cancelled {"auto": ...}`);
  one whose checklist was submitted is closed `done` first. If the switch is ever turned on again, a task is only
  expired after its store day. **Never touched**: tasks 11, 12, 47 (`auto_tasks.PROTECTED_TASK_IDS`) and any task
  assigned to / made by a demo login or a team a demo login is in (prod team 5 "Review") - `auto_tasks.demo_protected`.
  Prod copy: the 3 checklist tasks made tonight (53-55, team Managers with 0 members) close as Missed on the first run.
- Order auto tasks still open more than 7 days after their order day -> `cancelled` "Expired - no order recorded within
  7 days". "Skip this week" on Critical for that vendor (since its order day) -> `cancelled` "Skipped this week
  (Critical)".

### 3. Conditional writes + `expected_updated_at`
- Every task write changes the row with one `UPDATE ... WHERE status='open'` (system auto-close too - it can no longer
  overwrite a person's done / can't do); edits, reassign, step ticks/renames/adds/removes only while open.
- `PATCH /api/team/tasks/{id}` and `PUT /api/team/tasks/{id}/items` accept optional `expected_updated_at` (the
  task's `updated_at` as last seen). Mismatch, or the task isn't open any more ->
  ```json
  HTTP 409 {"detail": "This task was changed by someone else - reload it", "task": { ...task_out(detail)... }}
  ```
  Every task 409 (complete / cant-do / start / reopen / PATCH / items) now carries `task` = the current state (the
  `detail` strings are unchanged), so a client shows the real state without another GET. No `expected_updated_at` =
  last write wins, as before.
- History: an `edited` event whose fields include `due_at` also has `"due": {"from": iso|null, "to": iso|null}`.

### 6. Push sweep stops re-trying
- Task pushes go only to `notifications.deliverable_user_ids(db, alert_pref_key(alert), ids)` (a phone + access + switch
  on). Nobody left -> a `notification_log` row with `recipient_count 0` for that dedupe key (only when Firebase is
  configured), so the 5-minute sweep never builds it again; an already-logged dedupe key is skipped before any query.

### 9. Lead tasks and reminders (with NOTIFICATIONS' switches)
- Lead tasks send their morning "New task" only; their due-soon / overdue reminders are governed by
  `fix_issues_task_due` (default off). "Due in 30 minutes" for any task is governed by `task_due_soon` (default off);
  still sent as key `task_due` so old builds route it the same. "Overdue" (`task_due`) stays on for people's own tasks.

### 10. Steps (sub-items)
| Method + path | Body | Who | Response |
|---|---|---|---|
| `PATCH /api/team/tasks/{id}/items/{item_id}` | `{"done": bool}` (as before) and/or `{"text": str}` (rename) | tick: assignees/admins; rename: creator/admin | task (detail) |
| `POST /api/team/tasks/{id}/items` | `{"text": "Sweep"}` | creator/admin | task (detail); max 30 steps |
| `PUT /api/team/tasks/{id}/items` | `{"items": [{"id": 7, "text": "Wipe well"}, {"text": "New step"}], "expected_updated_at"?: iso}` | creator/admin | task (detail) |
| `DELETE /api/team/tasks/{id}/items/{item_id}` | - | creator/admin | task (detail) |

`PUT` = the whole list in order (one Save in an edit sheet): rows with `id` keep their tick, rows without are added,
blank rows dropped, steps left out removed; unknown id -> 400. `PATCH` with neither field -> 400. History events (the
web already has labels for the first two): `item_done` / `item_undone` `{"item_id", "text"}`; `items_edited`
`{"added": [{id,text}], "renamed": [{id,from,to}], "removed": [{id,text}]}` (only the keys that apply).

### 12. Long-poll on the change feed
`GET /api/team/updates?since=<cursor>&wait=<s>&include=tasks`
- `wait` (new, optional, 0-20; larger values are capped at 20 - under the app's 30 s Dio timeout): answers as soon as
  something the caller can see changes, else after `wait` s with `"changes": {}`. Same response shape as today; without
  `wait` it answers at once exactly as before (builds 5-9, website).
- `include=tasks` (new, optional): the response also has `"tasks": [task_out, ...]` - each changed task the caller can
  read (not deleted ones; max 100), so lists are patched without refetching.
  ```json
  {"cursor": 214, "resync_required": false, "changes": {"task": [{"id": 56, "action": "created"}]},
   "badges": {"tasks_due": 1, "unread": 0, "announcements_unacked": 0},
   "tasks": [{"id": 56, "title": "Smoke test task", "...": "..."}]}
  ```
- How: `record_change` marks the session; an SQLAlchemy `after_commit` hook bumps an in-process version and wakes the
  waiting requests (async handler; no thread and no DB connection held while waiting; each wake re-runs the ~1 ms
  query in its own session). Commits from the scheduler thread (task tick, auto tasks) wake it too. **Needs the single
  uvicorn process** (no `--workers`). Measured on a prod copy under uvicorn: woke 30 ms after a task was created in
  another request; 60 parallel waiting long-polls, a normal GET still took 6 ms.
- Client guidance: app foreground `wait=20` loop (replaces the 30 s / 120 s timers), poll at once on resume and on a
  foreground push; website one shared feed hook with `wait=20`; reload the open drawer when its id is in
  `changes.task` (or patch from `tasks`).

### 14. Sources close their tasks at once (+ deep link)
`team/source_sync.task_sources_changed(*kinds)` - own short session, cheap "close" half of each rule only, never raises;
the 5-minute tick stays as safety net. Called after the source's commit from: checklist submit (app + public link) ->
`"checklist"`; order actions placed/skip/other, cart `mark-placed`, Critical POST with `kind=skip_week` -> `"order"`;
invoice mark-toast-closed / approve-delete / delete -> `"invoice"`; Fix Issues `/api/issues/close` and `/reopen` ->
`"issue_leads"`. Prod copy timings: issue_leads 31 ms first / 1.4 ms after, checklist 8 ms, order/invoice 0.3 ms.
Reverse direction = `action` above.

### 15. Fix Issues lead tasks: live "N left of M"
Title `Fix Issues: <left> left of <total>` (was "N open items for you"), `progress` filled. M = the morning list
(`issue_lead_task_runs.items_json`), done = those items with a Fix Issues close on record now (a reopen counts it
again). Recomputed on every Fix Issues close/reopen (hook) and every task tick from the morning list + the
`issue_resolutions` keys only - never the ~6 s full recompute. At 0 left the task closes itself: `done`, note "All M
items fixed or closed". Items that drop off by themselves (fixed in Toast + new upload, not closed on Fix Issues) count
at the next morning's run. Prod copy: 48-52 now read 417/421, 607/611, 596/604, 7/7, 2/2.

### 16. Filters, bulk, "My team today"
- `GET /api/team/tasks` new optional params (narrow the view, never widen it): `assignee_user_id`, `source`
  (`manual` | `auto` | a source_type such as `issue_leads`), `q` (title words, case-insensitive, max 200 chars).
- `POST /api/team/tasks/bulk` (admins) `{"task_ids": [1,2,3], "action": "done" | "cancel", "note"?: str}` ->
  `{"action": "done", "done": [1], "skipped": [{"id": 2, "reason": "needs a photo"}, {"id": 3, "reason": "not open"}],
  "tasks": [task_out...]}`. Each task is closed only if still open; `done` skips photo-required tasks (and note-required
  ones without a note); max 200 ids; events `done`/`cancelled` with `{"bulk": true}`.
- `GET /api/team/task-summary` (admins: everyone; `can_assign` users: people in their own teams; others 403; **demo
  403**) -> `{"day": "2026-10-14", "totals": {"open", "overdue", "done_today"}, "people": [{"user_id", "name", "open",
  "overdue", "due_today", "done_today", "cant_do_today"}], "teams": [{"team_id", "name", "color", "open", "overdue",
  "due_today"}]}` (people sorted most overdue first).

### 17. Recurring schedules from the app
Existing routes are enough for the app to manage schedules (list `GET /templates`, create `POST /templates`, edit /
pause / resume `PATCH /templates/{id}` with `active`, delete = `DELETE /templates/{id}` (deactivate), preview
`POST /templates/preview`, "Create task now" `POST /templates/{id}/create-task`; writes admin-only). Added:
`GET /api/team/templates/{id}` (assigners; same shape as a list entry) and `schedule_text` on every template
("Every Mon, Wed at 3:00 PM", "Weekdays at 7:30 AM", "Every month on the 1st at 8:00 AM", "Every 2 weeks on Sat at 10:00
AM", "Doesn't repeat") so both clients show the same words.

### Pushes after the response; new opt-in task pushes
- Create, reassign, can't do, "Create task now", comment and complete send their pushes from FastAPI `BackgroundTasks`
  in their own session (`tasks_service.after_response`) - the request no longer waits ~250 ms per recipient for FCM.
- `task_done` (to the task's creator when someone else finishes it; not for automatic tasks) and `task_comment` (to
  creator + assignees, never the commenter; body "name: text", 120 chars) - catalog group `team`, **default off**,
  dropped in quiet hours, route `task` with `task_id`.

### Demo login (users.id 8)
New write routes `POST/PUT /tasks/{id}/items`, `DELETE /tasks/{id}/items/{item_id}`, `POST /tasks/bulk` are not on the
demo allowlist -> 403 DEMO_READ_ONLY (tested). Renaming a step via the allowlisted PATCH is refused for the demo too;
ticking its own steps still works. `task-summary` is 403 for it. Clients: hide step editing (`can.edit_items` is false),
bulk and the summary strip when `/api/auth/me` says `demo`.

### Not done / for others
- Clients (website + app): Edit/Reassign drawer, steps editor, `action` buttons, long-poll loop + `onMessage` ->
  poll, 409 handling with the returned `task`, filters/bulk/summary UI, app schedules screen, "Can't do" section.
- Team membership itself (Managers / Grocery / Kitchen are empty on prod) is an owner decision; the guard only stops
  new tasks reaching nobody.
- Lead progress ignores items that disappear without a Fix Issues close until the next daily run (no full recompute
  on purpose).
- Long-poll is in-process: it relies on the single uvicorn process (critic: keep `--workers` off).

## As built - SPEED (server)

Measured in-process (handlers called directly, + JSON encode) on a `.backup` of prod (alembic 0056, 383 MB, 124k
orders, 649k snapshots), scheduler/Toast off, `nice -n 15`, machine busy with other agents (numbers +-20 %). Before =
ops-fixes @ e5bb64a; after = this batch + migration 0057. Outputs compared: reorder-candidates 30/7 and the vendor
calendar (2 and 20 days, with and without include_ordered) are byte-identical before/after.

| Request | before | after |
|---|---|---|
| POST critical-dismissals skip_week (cold process / warm) | 3,945 / 1,636-1,879 ms | 139 / 2-3 ms |
| POST critical-dismissals snooze | 1,632-2,402 ms | 2 ms |
| ... skip while Home's reorder + calendar compute (contention) | 3,903 ms (speed.md) | 142 ms |
| POST critical-dismissals close (report cached / not) | 1,922-2,187 ms | 2 ms / ~1.1-1.8 s |
| GET reorder-candidates 30/7 (cached / first after a data change) | 1,700-1,935 ms | 57-96 ms (incl. 691 KB encode) / 1,774 ms |
| GET reorder-candidates 30/7 `critical_only=1` (new) | (691 KB) | 5-6 ms, 52.8 KB |
| GET reorder-candidates 90/14 (cached / first) | 3,212-3,662 ms | 117-120 / 2,923 ms |
| GET vendor-calendar today..tomorrow (cached / first) | 1,883-2,468 ms | 4 / 893 ms |
| GET vendor-calendar 20 days (cached / first) | 2,995-3,524 ms | 4-5 / 577 ms (counts shared with the 2-day read) |
| GET orders/open/all-time when today's scan is stale (Toast faked at 0.86 s) | 877 ms | 6 ms |
| opening-counts query, all items 30 d (index 0057) | 1,173 ms | 333 ms |

What changed (all additive; no response field renamed/removed; no new write route):
- **#3 report cache** - new `app/services/report_cache.py`: per-process in-memory cache (lock, single-flight per key,
  32 entries, TTL 300 s). An entry ends on: a COMMITTED insert/update/delete through the app's engine on a table the
  reports read (`orders`, `inventory_snapshots`, `placed_orders`, `purchase_log`, `invoice_submissions`,
  `vendor_schedules`, `category_overrides`, `setting_changes`) - so sales sync, inventory upload / inventory from
  Toast, Mark placed, "Order already placed" and its Undo, invoices, Delivery Review all invalidate without hooks in
  their files; a change of a 0.4 ms fingerprint (max ids, placed-order count, sync/upload job runs - catches a script
  writing the DB from outside the server); a new live-stock fetch / the Inventory-from-Toast switch; TTL; or
  `report_cache.invalidate()`. Critical dismissals do NOT invalidate it on purpose: the dismissal annotation is applied
  per request on copies of the cached groups, so a hide/show shows at once without recomputing. Single process only
  (no `--workers 2`). `reorder.cached_report()` caches compute_reorder_candidates + group_candidates_by_vendor; used by
  GET reorder-candidates (any lookback/lead/as_of/method) and `critical_dismissals.today_report_groups`. A stubbed
  compute function (tests) is never cached. `supplier_projection.list_suppliers` is cached the same way.
- **#2** `create_dismissal`: only `close` needs the Today report (from the cache when warm). skip_week / snooze use it
  only if already cached; otherwise vendor spelling from the (cached) supplier list, expiry-only settle (a close that
  "came back" is settled by the next Today report read), and `warning: null`. A vendor not in the supplier list
  (only "Unassigned") still asks the report, then 404s as before. Same response shape.
- **#7** `GET /api/ordering/reorder-candidates?critical_only=1`: only active vendors with tier critical (hidden ones
  included, annotated, same as the app's todayCriticalLoadProvider filter), `candidates: []`, plus new top-level
  `critical_only` (bool, also `false` on the full report). For app build 10+'s Home tile; old builds unchanged.
- **#4** Vendor Calendar: `supplier_projection.project_suppliers()` builds every scheduled vendor's projection in ONE
  pass (one latest inventory + live overlay, one sales window, one store profile, one build_models over the union; a
  model only depends on its own item, so rows are identical - tested), and `vendor_calendar.need_to_order_counts()`
  caches (vendor, horizon) -> (items needing order, total) via `report_cache.cached_many`. Skips / placed orders are
  read per request as before (never stale).
- **#8** `GET /api/orders/open/all-time`: `open_orders.refresh_today_in_background()` - when today's scan is >2 min
  old, the same one-call refresh runs on a daemon thread with its own session (same lock and failure back-off); the
  request serves the cache at once. `ensure_today_fresh` unchanged (still used by the CSV export).
- **#14** `app/db.py` per-connection pragmas: `cache_size=-16384` (16 MB; deliberately not 64 MB - up to 15 pooled
  connections on an 8 GB server), `mmap_size=268435456`, `temp_store=MEMORY`.
- **#6** migration **0057**: `CREATE INDEX IF NOT EXISTS ix_inventory_snapshots_item_date_qty ON inventory_snapshots
  (item_id, snapshot_date, inventory_quantity)` (covering; beat plain (item_id, snapshot_date) 333 vs 545 ms). Prod has
  only the single-column indexes + the (snapshot_date, item_id) unique (checked read-only). ~1.7 s to build on the
  copy, ~+38 MB; downgrade `DROP INDEX IF EXISTS`; re-run safe. No ANALYZE (prod has no sqlite_stat1; the planner
  already picks it). The ORM model doesn't declare it (models.py not mine; create_all DBs simply lack it). Take the
  usual `.backup` before upgrading prod.

Tests: `backend/tests/test_speed_oct15.py` (33). Full backend suite green on .venv (3.9) 2158 passed / 8 skipped and
.venv312 2156 passed.

Not done / for others: #13 workers=2 skipped (critic); scheduler times untouched (#15); the 9 AM critical-vendors push
(`notification_triggers`, not mine) still computes its own report - it could call `reorder.cached_report` /
`critical_dismissals.today_report_groups`; cart `?projection=1` and supplier-projection are not cached (#9 is app
side); app build 10 should call `critical_only=1` for the Home tile and stop awaiting the Skip POST (#1, #5 - app).
Restart needed for the server changes (`sudo launchctl kickstart -k system/com.spicetown.backend`) after `alembic
upgrade` (0057..0059 chain).

## As built - NOTIFICATIONS (app screen)

More > Notifications (`mobile/lib/features/notifications/`) rebuilt on the server's P3/P4 API; still the one place for
alerts (entry point in More unchanged). No route, pubspec or `ApiConfig` change (new paths are constants in
`NotificationsRepository`).
- **Grouped switches:** sections follow the server's `groups` order and labels (Needs action, Team, Daily summary,
  Admin), each with its one-line description; one `SwitchListTile` per item with the server's description. A switch
  set away from the recommendation shows "Recommended: on/off". Morning summary appears wherever the server lists it
  (on for admins by default). Items with a `group` but no `groups` list fall back to built-in labels.
- **Essential confirm:** switching off an `essential` alert asks "Turn off X?" (Keep on / Turn off); switching on never
  asks.
- **Hide message text** is in the Team card (same setting and endpoint, `GET/PUT /api/team/chat/settings`, as Team >
  Chat > settings, which still has it too). It is shown only when the list has `chat_message` or `chat_mention`, so a
  user without chat makes no extra call. It saves optimistically and flips back on error. The demo login can use it
  (allowlisted).
- **Reset to recommended** (`POST /api/notifications/prefs/reset`): shown only on the Oct 15 server, only when some item
  is `explicit` or differs from `default`, and **never for the demo login** (`user.demo`). It confirms first.
- **Store defaults (admins):** one tap from the Notifications screen (a card shown only for `isAdmin && !demo` on the
  Oct 15 server) opens `StoreNotificationDefaultsScreen`:
  - Each alert gets a Staff and an Admins switch, or Admins only for `admin_only` alerts. A tap saves
    `PUT /api/notifications/defaults {key, role, enabled}` optimistically.
  - Essential alerts confirm before a default goes off.
  - It shows "Recommended: Staff off" for overrides and the last change ("Last change: <note> - <who>, <store
    time>").
  - "Reset all to recommended" (`POST /defaults/reset`) appears only when something was changed.
- **Older server** (only key/label/description/enabled): one plain card as before, with no headings, no Reset, no Store
  defaults and no recommendation hints. Hide message text gets its own card when chat keys are present. Builds 5-9
  are untouched; `getPrefs()`/`setPref()` still return lists.
- **Retired copy:** the "not set up" text no longer mentions "new customer orders" (it now names closing checklists,
  tasks, chat and announcements). The model doc no longer lists the non-existent `delivery_review` key.
- **Tests:**
  - `test/features/notifications/`: `notifications_repository_test.dart` (+8), `notification_settings_grouped_test.dart`
    (12, new), `store_notification_defaults_screen_test.dart` (5, new), `notifications_device_matrix_test.dart` (30, new:
    all 10 device shapes including SE at 200% text, admin grouped, older server, Store defaults; no overflow, and the
    switches, Store defaults and Reset are reachable).
  - The existing `notification_settings_screen_test.dart` passes unchanged (older-server path).
  - `test/core/notifications/push_fakes.dart`: `FakeNotificationsRepository` gained fakes for the new methods
    (additive).
- **Not done / for others:**
  - `chat_settings_screen.dart` (not mine) still has its own Hide message text switch rather than a link here. Both
    write the same setting.
  - `GET /api/notifications/recent` ("Recent notifications") and `/volume` have no app UI.
  - The search index entry (`feature_index.dart`) has no "morning summary" keywords.

## As built - TEAM TASKS (app)

App only (`mobile/lib/features/team/**`, `core/notifications/push_messaging.dart`); no server, route or field
changes; pubspec version untouched. Every new call degrades on a server older than Oct 15 (falls back or hides).
- **Sync.** `TeamSyncController` now keeps one long-poll out while the app is in front:
  `GET /updates?since=&wait=20&include=tasks` (`TeamRepository.waitForUpdates`, cancellable). A server that answers
  without `tasks` is "old": the app falls back to the 30 s / 120 s / boost timers until the next sign-in or resume.
  An answer under 2 s with nothing in it counts as a normal poll (no spinning). Failures back off 5 s doubling up
  to the normal interval. Long-poll is cancelled in the background and on sign-out. A push arriving while the app
  is open (`PushMessaging.onForegroundMessage` = `FirebaseMessaging.onMessage`) polls at once (drops a held
  long-poll first). A `team` change now also bumps `task`, so membership changes refresh the lists.
- **Apply now, confirm later.** New `tasks/providers/task_patches.dart`: write answers, `include=tasks` tasks,
  bulk results and this phone's guesses are laid over every list / detail / Home "My tasks" until a list fetched
  after them arrives. Complete, Can't do, Start / To do, Reopen, Undo, step ticks and Cancel show at once; on failure
  the guess is rolled back; on a 409 the returned `task` is shown with plain words ("Already done by Sai Durga -
  showing it as it is now."). Lists still refetch in the background after every write.
- **List.** "Can't do" section (`status=cant_do`, 20 newest); search (`q`, debounced) and a Filter sheet (Made by:
  people / automatic; Whose tasks: a person) with removable chips - sent to the server and applied again on the
  phone; "My team today" strip (`/task-summary`) on Team / All for admins and assigners, tap a person to filter;
  long-press to select (admins, list layout, never the demo) -> bulk Done / Cancel tasks (`/tasks/bulk`, cancel asks
  first); lead tasks show a progress bar; automatic tasks a small bolt icon.
- **Detail.** `action` -> main button ("Open the checklist" etc.) via the push routes / `NotificationRouter`
  (`fix_issues` -> that lead's Fix Issues list; key `task-open-fix-issues` kept), Mark done stays as the second
  button; progress card "4 of 611 done · 607 left"; warning when the task's team has 0 members; history reads
  `item_done` / `item_undone` / `items_edited` and due changes.
- **Edit sheet.** Steps editable on existing tasks when `can.edit_items` (one `PUT /items` on Save); PATCH and PUT
  send `expected_updated_at` (the raw `updated_at`); a 409 shows "Someone else changed this task just now... Save
  again to keep yours." and the next Save uses the new version. Teams with `member_count` 0 show "(no members)" and
  can't be picked for a new assignment (editing a task already on one still works).
- **Schedules in the app** (owner decision): the calendar button on Tasks (key `task-templates`, assigners, never the
  demo) opens `TaskSchedulesScreen` - repeating schedules in the server's `schedule_text`, who, next due, weekday
  assignees, a pause switch (`PATCH active`), Edit, Create now; templates that don't repeat below. Admins (not demo):
  New / Edit sheet for title, details, person or team, Every day / Weekdays / Some days / Monthly + time, steps,
  needs, urgent, repeats on/off. A rule the app can't show (INTERVAL, two times...) is kept unless a new pattern is
  picked. Day assignees are shown, edited on the website. The old "Create from a template" sheet is replaced.
- **Tests** (all `--concurrency=2`): new `test/features/team/tasks_oct15_logic_test.dart` (19),
  `tasks_oct15_screens_test.dart` (25), `tasks_oct15_matrix_test.dart` (20: SE 320x568 at 1x and 2x text, 390x844,
  iPad 1024x1366, Split View 507). `test/features/team` + `test/core/notifications`: 592 passed. Also passed:
  `v2_e2e_matrix_test`, `audit/app_review_journey_test`, `home/today_card_test`, `audit/final_permissions_offline_test`,
  `audit/audit_a_regressions_test`. `flutter analyze`: clean.
- **Edits outside my files:** `integration_test/helpers/fakes.dart` (fake `waitForUpdates` answering like an old
  server; the 3 new optional `list` params) - needed to keep it compiling.
- **Not done:** no "Team activity" feed; schedule weekday assignees and "shows up N hours before" stay website-only;
  a new task made from a long-poll (not this phone) appears after the background refetch, not from `tasks`.

## As built - WEBSITE navigation + Notifications page

Website only (`frontend/`). No API, route or backend change; no new write route (the page uses the NOTIFICATIONS
server routes as built). Every old URL still opens something.
- **Menu (WM1/WM2/WM4)** - `featureRoutes.js` is the single source: `NAV_GROUPS` + new pure helpers `buildSidebar`,
  `routeVisible`, `footerLinks`, `groupsToOpen` (Sidebar.jsx only renders them). Order: Overview · Invoices · Daily
  Checklists · **Team** (Tasks, Chat, News, Flyers) · **Ordering** (Reorder Candidates, New order, Vendor Calendar,
  Placed Orders, Delivery Review) · **Stock** (Fix Issues, Inventory Reports, Reconciliation) · **Sales & Insights**
  (Items Sold, Open Checks, the 7 Insights pages, "Also in Toast" still a collapsed sub-group; label is "Sales" for
  anyone without an Insights page, e.g. staff and the demo) · **Setup** (Users, Teams, Recurring tasks, Reported
  messages, Checklist builder, PO sections, Vendor contacts, Category & vendor leads, Inventory from Toast). The 5
  formerly hardcoded admin links are now `FEATURE_ROUTES` entries with `feature: null, admin: true` (never a landing
  page). Footer: Label Scanner ↗ · Help (or Glossary for anyone without the `help` grant) · **Notifications** · Change
  password · Log out. A group with exactly one link for this user renders as that plain link (prod staff: "Open
  Checks", "Vendor contacts"). Groups "Stock Movement", "Reports", "Marketing", "Insights", "Sales" are gone.
- **Naming** (matches the app): menu + h1 "Invoices", "Reorder Candidates", "New order" (h1 keeps "Supplier
  Projection" beside it), "Vendor Calendar", "Delivery Review" (was h1 "Review Delivery"), "News" (announcements),
  "Reported messages" (was Moderation), "Change password". Help section titles follow.
- **WR1** Overview Quick actions removed (`QuickActions` deleted). Weekly Digest, stat tiles, upload, activity and
  Today's schedule unchanged.
- **WR2** `/transfer-review` -> `<Navigate to="/delivery-review">`; `TransferReview.jsx` + `TransferReviewPage.jsx`
  deleted; `api.js` transfer functions and the server API kept for old app builds.
- **WR3** The page-header subtitle that repeated the InfoBlock brief is removed on Reorder, New order, Vendor Calendar,
  Items Sold, Inventory Reports, Reconciliation, Delivery Review, Fix Issues, Invoices, Open Checks, Placed Orders, PO
  sections and Daily Checklists (Transfer Review deleted). Reorder's brief absorbed "for one supplier, use New
  order". The PO cart InfoBlock is 3 lines pointing to Help (the full cart guide already lives there).
- **WR5** The Reconciliation worked example (was "Demo") is no longer on the Reconciliation page (it fetched
  `/api/reconciliation/demo` on every page load). It's `ReconciliationWorkedExample` in Help > Reconciliation (shown
  with the `reconciliation` grant) and fetches only when opened. The page links to it ("New to this? Help ...").
- **WM3** Help has two tabs, Help and Glossary. `/glossary` (and every `/glossary?k=` "All definitions" link) opens
  the Glossary tab; without the `help` grant it's the whole page. New Help sections: "Finding your way (menu)" and
  "Notifications".
- **WV1** The sales export (`SalesDownload`) moved to Items Sold, shown with `inventory_reports` because
  `GET /api/sales/export` is still gated on that grant; Inventory Reports keeps it only for someone without Items Sold,
  so nobody loses it.
- **Notifications page** `/#/notifications` (`pages/NotificationSettingsPage.jsx` + `.css`,
  `components/notifications/notificationsModel.js`): the app screen's model. Cards per server `groups` (Needs action,
  Team, Daily summary, Admin; fallback "Alerts" for an older server), one switch per item; "Recommended: on/off" under
  a switch set away from its default; essential alerts confirm before going off; **Reset to recommended**
  (`POST /prefs/reset`) shown once there's any own choice and **never for the demo login**; "Hide message text"
  (chat setting) in the Team card for users with chat. Collapsed, loaded only when opened: "Your recent
  notifications" (`GET /recent`), and for admins **Store defaults** (`GET/PUT /defaults`, a Staff / Admins select per
  alert: Recommended (On|Off) / On / Off, essential-off confirm, Reset store defaults, change history) plus "Pushes per
  person (last 14 days)" (`GET /volume`). No "Send test" button (the app has it). New `api.js` helpers (additive):
  `getNotificationPrefs`, `setNotificationPref`, `resetNotificationPrefs`, `getNotificationDefaults`,
  `setNotificationDefault`, `resetNotificationDefaults`, `getRecentNotifications`, `getNotificationVolume`.
- **Tests:** `src/featureRoutes.test.mjs` (12: menus for admin / prod staff / demo / flyers-only, one-link rule, Setup
  admin-only and never a landing page, every menu key routed once, footer, group auto-open, every old URL routed,
  Transfer Review / Quick actions gone, worked example lazy, sales export), `notificationsModel.test.mjs` (8);
  `insightsModel.test.mjs` sidebar test now reads the Insights half of "Sales & Insights". All 182 node tests pass;
  vite build OK. Playwright on a prod copy (port 8871, scheduler off, all sends blank) at 1280 and 375 for admin, a
  hide-sales staff login, a chat-only login and the demo: menu, Setup, old URLs (`/transfer-review`, `/glossary`,
  `/categories`, `/price-changes`, `/barcode-reports`, `/insights/menu`, `/team/moderation`), worked example fetched 0
  times on page load and once when opened, switches persist, essential confirm, reset, store default change + reset,
  demo flips its own switch but sees no Reset, no sideways scroll.
- **Edits outside my list (smallest):** `api.js` (the helpers above, after `setHidePreviews`),
  `components/ReorderCandidates.jsx` (brief text only), `pages/TeamAnnouncementsPage.jsx` / `TeamModerationPage.jsx` /
  `ChangePasswordPage.jsx` (h1 text only), e2e `regression/suite-b/insights.mjs` (sidebar toggle now `/Insights$/`),
  `e2e/smoke.mjs` (+`/notifications`).
- **Not done:** no `index.css` change (the dead `.quick-action*` / transfer rules stay; the page has its own small
  CSS file). `GET /api/sales/export` still needs `inventory_reports` (could also accept `items_sold`, server side).
  SalesAccuracy stays on Items Sold (admins). App Review notes text ("five groups", Glossary listed separately) is
  for the app/notes owner.

## As built - APP navigation clean-up + app-side speed

App only (`mobile/`), no server, route, pubspec or API-shape change; every request the app sends is one builds 5-9
already send, except the additive `critical_only=1` query param (older servers ignore it). No new write call: the
demo login (still view-only) gets no new button. go_router paths unchanged (`/more/glossary`, `/store/insights`,
push routes all still open their screens).
- **Store (M1, R1-R5, R4, V1):** 3 categories, 15 tiles for an admin (was 5 / 25): **Ordering** (Reorder Candidates,
  New order, Vendor Calendar, Purchase Order Cart, Placed Orders) · **Stock** (Fix Issues, Delivery Review, Invoices,
  Inventory Reports, Reconciliation, Upload Inventory, Ask Inventory Bot) · **Sales** (Items Sold, Open Checks,
  Insights). Ids kept (`ordering`, `inventory` = Stock, `sales`); a remembered `invoices` / `checklists` falls back to
  the first category (tested). Removed from Store: Print Label History (Home tile + new **History icon on Scan**,
  key `scan-label-history`, both Scan modes), Upload Invoice (Home tile + Invoices FAB), Invoice Trash (Invoices
  app-bar icon), Daily Checklists (Home card), Store vs Container (now inside Inventory Reports, M2).
- **More:** Account (Face ID, **Notifications**, Change Password, Delete my account - the one-item Settings header is
  gone, M4) · Help & info (**Help with a Glossary tab**, M3; a login without the `help` grant gets a plain Glossary
  tile instead; About) · **Setup** (V1: PO sections, Vendor contacts, Checklist builder, Public checklist links,
  Inventory from Toast, Category & vendor leads, Users, Reported messages - each by its `featureIndex` access rule,
  Vendor contacts also needs Send PO on the server; none for the demo login, PO sections is now `anyOfNotDemo`) ·
  Sign Out. `moreSetupFeatureIds` / `moreSetupEntriesFor` in `more_screen.dart`.
- **Help (M3, V2):** `HelpScreen` = tabs Help | Glossary (`HelpTab`, keys `help-tab-help` / `help-tab-glossary`; the
  Glossary tab is built only when opened). `GlossaryView` is the Glossary body; `GlossaryScreen` still exists for
  term sheets' "See all definitions" and `/more/glossary`. The Reconciliation worked example is a tile at the top of
  Help (needs `reconciliation` + `help`, `canOpenReconciliationDemo`); the Reconciliation hub no longer lists it.
- **Inventory Reports (R5, M2):** Store vs Container · Vendor Price Comparison · Price Change Log. The 3 Fix Issues
  copies are gone (they're in Fix Issues, one tap from Home).
- **Home (R6-R8, R12 kept):** no "Your Workspace" card, no "Connected" pill, **no entrance animation** (it replayed on
  every Home tab tap and refresh: 550 ms + 80 ms per section, ~1.1 s until the last section settled; now everything
  is there on the first frame - `home_entrance_provider.dart` deleted, `app_shell.dart` no longer bumps it). Recent
  invoices / orders previews and the 4 quick-action tiles untouched.
- **Search (R9, naming):** the "Ordering" and "Reports" alias results are gone; the single "Store" entry carries their
  words. Titles as on the website: New order, Vendor Calendar, Invoices, News, Users, Reported messages. Screen app-bar
  titles (Supplier Projection, Vendor Order Calendar, Invoice Submission) are other areas' files and unchanged.
- **Dead code (R10):** deleted `features/transfers/**` and the Missing barcodes / Invalid barcodes / Duplicate products
  screens (nothing opened them) with their providers. `plainLanguageBarcodeReason` moved to
  `inventory_reports/utils/barcode_reason.dart` (Fix Issues uses it). Repository methods / models / `ApiConfig`
  constants kept (test fakes and integration fakes implement them).
- **R11:** Fix Issues has no intro sentence. That moved a lower card into view at 2x text on an SE and showed a
  latent overflow (a long count squeezed the title to 12 pt): the count is now capped at half the row.
- **Speed #1 (Critical sheet):** Submit closes the sheet at once - no "Hiding…". `CriticalDismissalActions.hideNow`
  records a provisional dismissal (`provisionalCriticalDismissal`: the server's end and "Until ..." words) on every
  screen, POSTs in the background, swaps in the server's dismissal (and its `warning`, shown in the sheet's notice)
  when it answers, and puts the vendor back with the server's words if refused (in the sheet, or Home's snackbar
  when both sheets are closed). Undo tapped before the answer waits for it (`criticalPendingHidesProvider`).
  `hide()` (awaiting) is kept.
- **Speed #5:** "Order already placed" / its Undo from the Critical sheet no longer reload the vendor calendar; the
  vendor's next order-due on the Orders due tile is marked (or un-marked) in place through
  `orderDueLocalChangesProvider` (only if that tile's data is loaded; never starts a load). Tested: calendar reads
  stay at 1 through place + Undo (was 3).
- **Speed #7:** Home's Critical tile sends `critical_only=1` (`ReorderRepository.getReorderCandidates(criticalOnly:)`);
  the server answers ~53 KB instead of ~691 KB; an older server's full report is filtered as before.
- **Speed #9 (cart):** `cartProvider` is now an `AsyncNotifierProvider` (same watch / `.future` / invalidate API).
  A saved qty change (cart screen stepper, in-list steppers) is applied in place (`applyCartQty`: units, line cost,
  vendor estimate, grand total - the server's sums) instead of re-reading the cart. The lines' projection context
  (`GET /cart?projection=1`, ~1 s server work) is re-read only when `cartProjectionKey` changes - a line added /
  removed / re-cased, or the qty of an item that another vendor's cart also has (the only qty that moves a
  suggestion) - after a 2 s debounce; pull-to-refresh and new Cart settings re-read it at once. It also no longer
  reads twice on every cart open (Cart settings loading re-sent equal values). Per qty change: 2 reads -> 0 (1
  debounced for a shared item).
- **Speed #10:** Home pull-to-refresh / Refresh all keep the reorder report and the vendor calendar when they loaded
  < 2 min ago (`homeHeavyReportFreshFor`, `homeHeavyReportTimesProvider`); a failed load is always asked again.
- **Tests:** new `test/features/purchase_cart/cart_speed_test.dart` (9), `test/features/reorder/critical_provisional_test.dart`
  (4), `test/features/inventory_reports/barcode_reason_test.dart` (4); updated navigation reachability / device matrix,
  Store, More, Help, home, critical, order-actions, search, insights / inventory-from-Toast / public-links entry
  points, audit matrices, wording, flyers. Run in a scratch tree (mobile HEAD + only these files, because other
  agents' half-done Team files didn't compile mid-run): 3564 passed across test/app, audit, account, store, help,
  home, reorder, purchase_cart, suppliers, search, scanner, inventory_reports, reconciliation, delivery, fix_issues,
  checklists, categories, ordering_hub, orders, reports, invoices and the entry-point tests; `flutter analyze` clean.
- **Edits outside the brief's list (small):** `fix_issues/widgets/issue_row_card.dart` and
  `fix_issues/screens/issue_details_sheet.dart` (import path of `plainLanguageBarcodeReason`),
  `reconciliation/screens/reconciliation_hub_screen.dart` (worked-example tile removed, V2),
  `purchase_cart/screens/purchase_cart_screen.dart` + `models/cart_item.dart` (#9),
  `reorder/data/reorder_repository.dart` (#7) and the matching `criticalOnly` param in 4 test / integration fakes.
- **Not done / for others:** App Review notes in `APP_STORE_SUBMISSION_2.0.0.md` still say "every store tool in five
  groups" and list Glossary in More - edit to "three groups (Ordering, Stock, Sales); setup screens in More › Setup;
  Glossary is a tab of Help" (App Store Connect text only). The screens' own app-bar titles weren't renamed. The
  Critical sheet's server `warning` now arrives a moment after the notice (shown when it does).

## As built - TEAM TASKS (website)

Website screens for the TEAM TASKS (server) section above. No backend, route or migration change; only the existing
Tasks page, drawer, board, create form, Recurring tasks page and Teams page (no new pages). Files:
`frontend/src/pages/TeamTasksPage.jsx`, `TeamTemplatesPage.jsx`, `TeamsPage.jsx`; `frontend/src/components/team/`
`TaskDetailDrawer.jsx`, `TaskBoard.jsx`, `TaskCreateForm.jsx`, `taskUtils.js`, `useTeamBadges.js`; new
`teamSyncEngine.js` (+ `.test.mjs`, 10 tests), `useTeamSync.js`, `taskEditModel.js` (+ `.test.mjs`, 15 tests),
`teamTasks.css`; `frontend/src/api.js` (additive: `listTasks` filters, `getTeamUpdates(since, {wait, include,
signal})`, `replaceTaskItems`, `bulkTasks`, `getTaskSummary`); e2e `e2e/regression/suite-b/tasks-oct15.mjs`.

- **One shared feed (#10/#12):** `useTeamSync` = one `/api/team/updates` cursor loop for the whole site (sidebar
  badges, Tasks page, open drawer). While the Tasks page or a drawer is open it long-polls `wait=20&include=tasks`
  (measured on a prod copy: a task made by someone else appears on an open Tasks page in ~0.47 s, an open drawer
  shows "Done by" ~0.18 s after another person finishes it). Any other page keeps the old 2-minute badge poll with
  `wait=0`, so ordinary pages never hold a request open (keeps Playwright `networkidle` settling fast there).
  Falls back to plain polling if the server answers an empty long-poll at once twice (older server); backs off on
  errors (2 s .. 60 s); hidden tab asks nothing, then once when shown; `refreshTeamBadges()` still asks at once.
  The Tasks page's blind 3-list reload every 30 s is gone: it reloads (debounced 250 ms) only when `changes.task`
  is non-empty / resync, patching visible rows from the feed's `tasks` immediately; `changes.team` refreshes the
  bootstrap (member counts).
- **Drawer:** applies each write's response (`task_out` detail) without another GET; reloads itself when its task
  is in the feed (waits for an in-flight write). 409 (any action) -> the error text plus the `task` the 409
  carries; other 409/404 -> reload. **Edit** (title, details, due, priority, needs, assignee for assigners, steps
  add/rename/remove when `can.edit_items`): sends only changed fields + `expected_updated_at`, then `PUT
  /tasks/{id}/items` with the new `updated_at`. **Reassign** (assigners): one picker + Save. History shows step
  ticks, due changes, `items_edited` counts and bulk closes. Can't do shows its reason with a pill.
- **Main action:** an open task with `action` gets `action.web_path` as the primary button (`.button-link`), "Mark
  done" turns secondary; older servers' lead tasks still link via `source_id`. Lead tasks show a progress bar +
  "N left of M · K done" (row/card label only when the title doesn't already say it).
- **List:** filters row (title search debounced 300 ms, Made by people / Automatic, person for assigners, Clear
  filters); "Can't do" section moved up under Overdue; "automatic" tag. **Admins (never demo):** row checkboxes +
  select-all, sticky bar "N selected · Mark done · Cancel tasks" (`POST /tasks/bulk`, confirm before cancel,
  result "Marked done: 2. Skipped 1: <title> - needs a photo."). **My team today** strip for admins and assigners
  (never demo): totals + a chip per person with open/done today; an admin's chip switches to All + that person.
- **Empty teams:** create form, drawer Reassign/Edit, template form, weekday assignees and "Create task now" show
  "<team> (no members)" disabled (kept selectable only if it's already the assignee) with a warning; Teams page
  says "No members yet - tasks can't be assigned to it". Recurring tasks list shows the server's `schedule_text`.
- **Demo login:** no bulk, no strip, no steps editor (`can.edit_items` false), no Reassign (not an assigner);
  checked in the e2e (no refused calls).
- **Tests:** node 187/187 (incl. the 25 new); `vite build` OK; Playwright on a throwaway prod copy (port 8853,
  scheduler/Toast/Firebase/Whapi off, killed, copy deleted): `tasks-oct15.mjs` ALL OK @1280+375, existing
  `team.mjs` ALL OK @1280+375 (71 checks), Teams/templates pages + "no long-poll on other pages" OK.
- **Notes for others:** the two new `.test.mjs` files are untracked - `git add` them so the standard
  `git ls-files` test command runs them. Suite B `settle()` waits up to 15 s for `networkidle` on the Tasks page
  now (long-poll held open) - harmless, just slower. Not done: reordering steps (API supports order; UI only
  adds/renames/removes), remembering filters between visits (deliberately not, so nobody "loses" tasks).

## As built - VERIFY: NOTIFICATIONS (adversarial)

- **Fixed: morning summary repeated a once-only alert every day.** `inventory_api_ready_alerts` returns the same
  once-per-streak alert every morning (only `dispatch_alert`'s dedupe stopped the repeat), and the summary built
  its lines from the raw trigger output - so from the 7th good night every admin's summary would say "Inventory
  from Toast is ready" daily until the switch is on. `run_morning_job` now keeps only alerts with a dedupe key not
  yet in `notification_log` (the same rule `dispatch_alert` uses). `services/notification_triggers.py`, 4 lines.
- **Fixed: a night announcement queued for 7 AM stayed queued forever** (re-tried with a warning every 5 min)
  when every target had Announcements off - by their own choice or, now, an admin's store default.
  `announcements_service._could_have_reached` leaves out pref-disabled targets. 2 lines.
- Checked and fine: all 18 old keys' effective defaults for prod-shaped staff/admin logins; explicit choice beats
  store default on alert, summary, task and chat paths; one summary per admin per day across re-runs and both DST
  days (9:00 local fire times checked); quiet hours; demo (never a summary, reset/defaults/volume refused, UI never
  offers them); hide-sales opt-in sees no dollars; push log has titles only and a missing table never breaks a send;
  builds 5-9 `{"items"}` shape and PUT. Prod copy (0056->0059): Oct 15/16 = 1 summary per admin per day, staff
  phones only the 21:30 closing checklist. Website page Playwright on a copy (port 8897, all sends blank) at
  1280/375 as admin / staff / demo: ALL OK. App: notifications + 5 dependent test files 418 passed.
- Owner call still open (unchanged): `invoice_failed` (own invoice) ON for staff and `cart_removal_request` ON for
  admins are not in the owner's list.
- Tests: `backend/tests/test_notifications_verify_oct15.py` (18).

## As built - VERIFY: TEAM TASKS (adversarial)

Server + website + app checked end to end (pytest 3.9/3.12, node tests, vite build, flutter team/notifications tests,
and a throwaway uvicorn on port 8867 over a `.backup` of prod upgraded 0056 -> 0059, scheduler/Firebase/Toast/Whapi
off, killed and deleted afterwards). Three defects found and fixed; nothing else changed shape.
- **Lead "N left of M" counted items that came back as already done.** Fix Issues keeps the resolution row of an item
  that "came back" (closed as fixed, still showing after an upload) or recurred, while listing it as open - so it is on
  the morning list AND counted done at once. Prod copy: tasks 49 / 50 read 607 / 596 left; the true counts are 609 /
  597. A lead whose list was only came-back items would have had its task close itself the moment it was made.
  `issue_leads.refresh_lead_progress` now counts a close only if it is no older than the morning list
  (`issue_lead_task_runs.updated_at`).
- **A reopened lead task froze its count.** When someone had reopened a lead task (the system then never closes it),
  progress stopped updating. It now keeps moving (down to "0 left of M") and stays open.
- **Reopen of an already-open task returned a plain 409** (no `task`), against "every task 409 carries the task".
  Same `detail` string, now with `task`.
- Tests: `backend/tests/test_verify_oct15_tasks.py` (12, incl. regression guards for 409s on step writes of a closed
  task, stale `expected_updated_at` on PUT items, step permissions, bulk validation, long-poll cap, `include=tasks`
  visibility). Live checks on the copy: a Fix Issues close by another person woke the lead's long-poll with the new
  progress; reopen restored it; 12 rounds of complete / edit / bulk cancel / step tick / steps PUT fired at once ended
  with exactly one close each, no 5xx, every 409 with `task`; the demo got 403 on every new write route; checklist
  tasks 53-55 closed as Missed, 11 / 12 / 47 untouched.
- Left as is (noted): Undo of an order skip / "placed" doesn't reopen its (already closed) order task - rule is off on
  prod; a comment or step tick bumps `updated_at`, so an edit sheet open at the same time gets a 409 and a second Save;
  admin bulk can close App Review tasks 11 / 12 / 47 if an admin ticks them by hand (no select-all exists).

## As built - VERIFY: SPEED + INSIGHTS (adversarial)

Re-measured on a fresh `.backup` of prod (0056; HEAD code) vs the same copy upgraded to 0059 (working tree),
in-process, machine busy: Skip this week first in a cold process 23.6 s -> 185 ms (warm 1.8 s -> 2 ms); Skip while
Home's two reports load 4.4-6.6 s -> 263-277 ms; Remind me later 1.8 s -> 1-2 ms; Close (report cached) 1.7 s -> 3 ms;
reorder 30/7 3.2-4.3 s -> 1.4 s first / 1-2 ms cached; `critical_only=1` 645 KB -> 50.6 KB (5 ms); reorder 90/14
3.0 s -> 2.7 s / 2 ms; vendor calendar 2 days 0.53 s -> 0.44 s / 3 ms, 20 days 3.0-3.5 s -> 0.81 s / 3 ms; supplier
projection, cart?projection=1, issues summary/lists unchanged (not cached, as built). HTTP on a throwaway server
(port 8893, copy, every service blank, killed afterwards) agrees. `project_suppliers` == one projection per vendor for
40 suppliers on the copy (0 diffs). "Order already placed", its Undo, a schedule change and a delivery all show at
once; a dismissal never clears the cache (by design).
- **Fixed (app): a dead Undo after a refused hide.** Undo tapped while the hide was still saving, then the server
  refused the hide: the Undo re-threw the hide's error and was offered again (in the sheet and on Home) though the
  vendor was already back - every retry failed. `CriticalDismissalActions.showAgain` now treats a refused pending
  hide as done. `mobile/lib/features/reorder/critical_dismissal_actions.dart` (7 lines); test in
  `test/features/home/critical_vendors_test.dart`.
- **Fixed (server): Insights said "today's are used up" while calls were left.** An older day waiting for the
  reserve (calls left, kept for the newest days) got "used up ... added automatically by <hourly catch-up time>",
  but the catch-up never reads older days. Now: "the calls left today are kept for the newest days. It's added
  automatically, at the latest with the 4:30 AM update." `services/insights.py`.
- **Fixed (server): `filling` true on a complete page.** The fill lock is process-wide (now also held by the hourly
  catch-up), and app build 9 shows "Some days are still coming in from Toast" whenever `filling` is true - also on
  a complete page with `note: null`. `filling` is now only true on a stale answer. `services/insights.py`, 1 line.
- Test fix: `test_insights_backfill.py::test_budget_used_up_says_which_day_and_when` failed between ~00:20 and
  05:30 (the next fill slot is then the 4:30 nightly, not within the hour); bound widened to 5 h 10 min.
- Left as is (noted): Home pull-to-refresh keeps Critical / Orders due for 2 min (speed #10) - with the server cache a
  repeat read is ~5 ms, so the window now mostly delays seeing another person's or the cart's "Mark placed"; owner
  call. The cache is cleared in the engine's `commit` event, which SQLAlchemy fires just before the DBAPI commit -
  a theoretical microsecond window, self-healing for inserts via `data_version`. Pre-existing:
  `test_inventory_api_late_csv.py` (2 tests) fails just after midnight at HEAD too.
- Tests: `backend/tests/test_verify_oct15_speed_insights.py` (5). Backend full suite 3.9: 2190 passed, 2 failed (the
  pre-existing late-CSV pair). Insights + speed + critical on 3.12: 153 passed. Node 207/207. App home + reorder +
  cart speed: 339 passed; analyze clean. Insights pages Playwright at 1280 / 375 on the copy: the server note shows,
  no old wording, no sideways scroll, no console errors.

## As built - VERIFY: NAVIGATION (adversarial)
- Website, Playwright on a throwaway prod copy (port 8879, scheduler/Toast/Firebase/Whapi off, migrated 0056->0059;
  server killed, copy deleted): real logins Sundar (admin), Store, Harshitha, Gayathri8, spc_muse, the demo, plus
  chat-only and hide-sales test logins, at 1280 and 375. Every menu link opens its page (no no-access, no page errors,
  no sideways scroll at 375). Old URLs: `/transfer-review` -> Delivery Review, `/glossary` and `/glossary?k=` -> Help's
  Glossary tab (the whole page without Help), `/categories`, `/price-changes`, `/barcode-reports`, `/insights/menu`,
  `/team/moderation` all still land. Overview keeps Weekly Digest (no Quick actions); Also in Toast stays. Demo: no
  Setup, no Flyers/PO sections/Vendor contacts in the menu, no Reset/Store defaults on Notifications; typing an admin
  URL redirects (`/vendor-contacts` opens its "can't see vendor contacts" notice, as before).
- App: analyze clean; nav/home/store/more/help/search/scan/notifications/audit/team suites 2799 + 615 passed. Home's 4
  tiles and Recent invoices/orders unchanged; deleted screens (transfers, barcode lists, templates sheet) have no
  references.
- Fixed (small text edits): web Help section headings now match the renamed jump list (Reorder Candidates, New order
  (Supplier Projection), Vendor Calendar); "the app's Manage Users" -> "Users, in More › Setup"; WhatsApp groups hint
  "Marketing → Flyers" -> "Team → Flyers"; app Team "not turned on" text "Manage Users" -> "Users (More › Setup)".
  Tests: `frontend/src/featureRoutes.test.mjs` (+1), `mobile/test/features/team/tasks_segment_test.dart` (+2 asserts).
- `APP_STORE_SUBMISSION_2.0.0.md`: review notes say Store has three groups (Ordering, Stock with Invoices, Sales) and
  the Glossary is Help's second tab (3,644 chars); What's New says three groups; screenshots `01-home`/`02-store` are
  flagged for a retake if the submitted build has these changes.
- Left as is: a login with no Store tools (spc_muse) now sees Store's "No tools are turned on" line (Print labels is on
  Home and Scan); `integration_test/app_test.dart` "More screen" case was already stale before this batch (expects
  Transfer Review / Invoice Submission on More) and is not part of the normal runs.

## As built - FINAL regression + commits (Oct 15)

Everything above re-tested together, committed per area, runbook `~/spicetown-dev/DEPLOY_OCT15.md`. Nothing pushed,
deployed or sent; prod only read (`mode=ro` `.backup`s, all copies deleted).

- **Backend:** full suite Python 3.9 2190 passed / 8 skipped, Python 3.12 2191 passed / 8 skipped; the only failures
  are the 2 time-of-day `test_inventory_api_late_csv.py` tests, which fail identically on committed HEAD code (checked).
  `test_migration_chain.py` gains the Oct 15 order 0056 -> 0057 -> 0058 -> 0059 (single head 0059). Migration
  rehearsal on a fresh prod copy (0056, 383 MB): up 2.11 s, down to 0056 0.59 s, up again 1.0 s; integrity ok,
  foreign_key_check empty, row counts unchanged (tasks 55, snapshots 649,354, users 10).
- **Website:** node tests 208/208, vite build ok. Suite A (port 8833, worktree build): sweep 1008, cart 149, projection
  59, reorder 90, calendar 64, fixissues 66, pages 85 - all ok. Two specs first failed on **prod data drift, not code**:
  the owner had skipped / marked placed every critical vendor that evening (reorder needs >= 4 critical vendors - those
  rows were removed in the COPY only), and leads were already saved plus 16 earlier "other" closes on prod
  (`fixissues.mjs` now compares against the copy's own counts and accepts the new "N left of M" title). Suite B: team
  103, chat 96, flyers 61, insights 302, checklists 109, admin 134, tasks-oct15 108, sweep 1125 - all ok. tasks-oct15's
  lead check needs lead titles in the new format: on a copy with the scheduler off they only change on a Fix Issues
  close/reopen or a task tick, so run `source_sync.task_sources_changed("issue_leads")` on the copy first (prod: the
  5-minute tick does it). New `e2e/regression/suite-b/oct15-final.mjs` (113 checks, 1280 + 375): menu groups for admin
  / staff / demo, old URLs, Overview, Notifications page (switch, "Recommended: on", Reset, Store defaults, demo sees
  neither), two browsers in sync through the UI (step tick 160 ms, "Done by" 175 ms, Reopen 156 ms), Insights note,
  Fix Issues.
- **App:** `flutter analyze` clean; full `flutter test --concurrency=2` 5200 passed, 6 skipped, 0 failed (was 5112).
  Simulator walk (iPhone SE 3rd gen, then iPad Pro 11, throwaway server on a prod copy, push faked): screenshots in
  `~/spicetown-dev/app-store-screenshots/review-oct15/{iphone-se-3rd-gen,ipad-pro-11}/`. The Critical Skip confirm
  sheet closed 174 ms (SE) / 223 ms (iPad) after the tap. **Fixed:** Store defaults rows zig-zagged on an iPad (each row
  centred to its text width) - the card's Column now stretches (`store_notification_defaults_screen.dart`, 1 line +
  test).
- **Commits:** website `707fdd8` speed, `ff4f7f4` insights, `61831ad` notifications, `291c28f` team tasks (server),
  `4ed742c` team tasks (website), `1f20fb7` navigation + Notifications page, then this final commit (suites, chain test,
  contract, skill). Shared files were split by hunk where it mattered (`routers/ordering.py`, `api.js`);
  `scheduler.py` (insights catch-up + morning summary) went with notifications, `notifications.py` (incl. the task_done
  / task_comment entries) with notifications. Mobile `d951492` navigation + speed, `e3b522c` Notifications screen,
  `2f4412c` team tasks; pubspec still 2.0.0+8.
- **Left open (owner):** invoice_failed / cart_removal_request default on; Managers / Grocery / Kitchen empty; the app's
  2-minute Home refresh rule (verify suggests ~15 s). Minor, not changed: on an iPhone SE the Tasks toolbar's "Filter"
  pill is partly under the search icon until you scroll that row (it is a horizontal scroller); on a simulator without a
  camera the Scan screen's "camera access is off" text overlaps the scan frame (pre-existing, not this batch).

## As built - ARCHITECTURE DOCUMENT

Docs only (no code, route, migration or app change). `docs/architecture/SPICETOWN_ARCHITECTURE.html` (self-contained:
inline CSS + 56 pre-rendered SVG diagrams, no scripts) and the PDF `~/spicetown-dev/SPICETOWN_ARCHITECTURE.pdf`
(Letter, 150 pages, page numbers, linked contents with page numbers, PDF bookmarks; not committed - about 9.6 MB).
Sections: 1 overview + glossary + system context; 2 deployment (daemons, process internals, network, deploy /
rollback, backups, venvs, alembic, env var NAMES only); 3 security (auth, request pipeline, roles, 20-key feature
matrix with hide_sales / demo, demo guard + full allowlist, public link guards + CSP, rate limits, gaps); 4 data
model (12 ER diagrams, every table with prod row counts); 5 backend (routers, modules, every scheduled job + 24 h
timeline, threads, caches incl. report cache, integrations / limits / failure modes); 6 website (route map, menu,
page -> endpoint map, sync patterns); 7 app (tabs, layers, screen -> providers -> repositories -> endpoints, push
routing, Team sync, storage, plugins, iPad, demo); 8 end-to-end sequence diagrams for every major function;
9 measured performance, limits, runbook pointers, Oct 15 change log; appendices A routes (317), B tables + columns,
C migration chain, D client calls checked against server routes (all 281 match; 33 server routes no client uses).
Generated parts come from the code (`docs/architecture/build/`, README there): rebuild with
`bash docs/architecture/build/build.sh <scratch> [<out.pdf>]` (mermaid@10.9.1 pinned, Playwright Chromium,
pypdf). Prod was only read (`mode=ro` row counts; the prod `.env` was not read). Not done: the system-context
diagram renders at 0.58 scale (smallest text in the document, still legible); app screen -> endpoint mapping is
static import analysis (a screen is credited with every endpoint of the repositories it reaches).
