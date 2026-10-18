# Spice Town "Team" hub: tasks, announcements and chat

**Status:** Revision 2: review feedback incorporated (see §15). Awaiting owner sign-off before Phase 0.
**Date:** 2026-09-27
**Scope:** iOS app (Flutter), FastAPI backend, React website

---

## 1. What we're building, in one paragraph

A **Team** tab inside the existing Spice Town app where managers can **assign tasks** to people or teams (with due times, repeats and photo proof), **post announcements** that staff must acknowledge, and **chat** in team channels or one-to-one. It is modeled on the parts of Sling/Homebase that we actually need. It is built on what already exists: logins, feature permissions, Firebase push, file uploads and the scheduler. So it needs no new vendors, no new monthly cost and no second app for staff to install.

### Goals

1. Replace "tell them on WhatsApp and hope" with **tracked** work: who was asked, by when, and whether it's done (with proof).
2. One place for store communication, tied to the operations data we already have (orders, invoices, checklists).
3. Ship in **small app releases that pass Apple/Google review easily**. Each phase's build shows its new feature working for review. Server-side flags are only kill switches and rollout controls, and backend-only fixes need no new store build.

### Platforms

The app is going to the **Google Play Store** as well, so everything here must work on both iOS and Android. Flutter covers the UI. Firebase push already supports Android (it needs `google-services.json` and a notification channel), and "Update required" links to the correct store per platform. Play Store review has user-generated content rules similar to Apple's (report, block, moderation), and §10 covers them for both.

### Non-goals (for now)

- Shift scheduling, time clock and payroll (Homebase's core). This is noted as a possible Phase 4 in §11. Toast already does clock-in.
- Voice/video calls, message reactions beyond a simple 👍, threads-within-threads, end-to-end encryption.
- Ad-hoc group chats in v1. Team channels and DMs cover it; an admin who needs a new group creates a team.

---

## 2. The current app (context for reviewers)

### 2.1 The business

Spice Town is an Indian grocery store with a street-food kitchen (**Street Kitchen**) at the same site. Toast is the point of sale for both (retail and restaurant). There are about 9 staff logins: 3 owners/managers (admins) and the rest store and kitchen staff with limited access. Staff currently coordinate over **WhatsApp groups**. The app already sends some automated WhatsApp messages (checklist results, invoice copies) through the Whapi API.

### 2.2 Systems and hosting

| Part | Tech | Notes |
|---|---|---|
| iOS app "Spice Town" | Flutter (Dart 3.13), Riverpod 2, go_router 16, Dio, Firebase Messaging, ML Kit on-device OCR | On the App Store, currently version `1.0.0+3`. Distributed to staff via the App Store and TestFlight. 88 widget/unit test files. |
| Backend | Python FastAPI 0.115, SQLAlchemy, Alembic (25 migrations), APScheduler in-process, firebase-admin 6.8 | One process on a Mac mini (LaunchDaemon). There is **no backend test suite** today. |
| Database | SQLite (WAL mode), a single file | Prod data is copied to dev with `sync_dev_data.sh` |
| Website ("ops dashboard") | React 18 + Vite, hash routes (`#/reorder`) | Built to `dist/` and served by the same FastAPI process |
| Public access | Cloudflare Tunnel → `https://ops.spicetown.shop` | Dev runs on a separate port, reached over Tailscale |
| Environments | `dev` branch and dev DB on port 8001; `main` = prod on port 8000 | Deploys are manual: merge to `main`, `git pull`, restart the daemon. App builds are archived from a separate prod folder. |
| External APIs | Toast (sales, orders, inventory export), Whapi (WhatsApp), Gmail SMTP (invoices emailed to Toast), Firebase (push) | Invoice scanning is deliberately **on-device and free** (no cloud OCR/LLM calls) |

### 2.3 Accounts and permissions

- There is **no public sign-up**. The owner creates every login (More › Manage Users) and ticks which features each person can open. Admins see everything.
- Grantable feature keys: `overview, open_orders, items_sold, reorder_candidates, supplier_projection, vendor_calendar, purchase_order_cart, delivery_review, transfer_review, inventory_reports, reconciliation, invoice_submission, daily_checklists, ask_bot, help`. The backend enforces the same keys on every endpoint.
- Sessions use a server-side token (a cookie for the website, a bearer token for the app). The app also supports Face ID unlock.
- Users have **no location/team field today**, and one shared store phone can be logged into by different people.

### 2.4 What the app does today

**Bottom tabs:** Home · Ordering · Scan · Reports · More

| Area | What it does |
|---|---|
| **Home** | A time-of-day greeting; a **Today** grid of colored tiles (Orders due, Critical vendors, Open customer orders, Invoices failed/to close in Toast, Missing closing checklists), each tapping into its list; 4 quick actions (Print Labels, Upload Invoice, Fix Issues, New Order); recent invoices and recent orders placed; a system status strip |
| **Ordering** | Reorder candidates across all vendors (urgency accounts for vendor minimum orders and inactive vendors); supplier projection (what to order from one vendor for N days); a vendor order calendar (editable delivery/order schedule per vendor, with skips); a purchase-order cart and PO export; open customer orders from Toast |
| **Scan** | Barcode scanner for looking up items and printing shelf labels |
| **Reports** | Items sold, weekly sales, inventory reports (CSV/PDF with chosen columns), price change log (searchable) |
| **Invoices** | Upload an invoice by **Scan** (camera, auto-crop, auto-rotate, on-device OCR fills vendor, total and date) or **Manual**. It then sends to WhatsApp and emails Toast, tracks failures and resends, and has a "Close in Toast" step once it's entered in Toast. |
| **Delivery / Transfer review** | Check deliveries against invoices; move stock between container storage and the shelf |
| **Daily checklists** | Two native closing checklists (Spice Town store, Street Kitchen chef) replacing Google Forms. The submitter comes from the login, and results post to WhatsApp. There is a history view. |
| **Fix Issues** | Queue of data problems (missing or invalid barcodes, duplicates, dead stock) with resolve reasons |
| **Reconciliation, Upload Inventory** | Daily Toast Retail inventory export upload and stock reconciliation |
| **Ask Inventory Bot** | Q&A over inventory and sales data (backend-side, admin-granted) |
| **Notifications** | Firebase push with per-user toggles: order due, critical vendors (morning), closing checklist missing (9:30 PM), invoice failed, new customer order, and an open-orders morning summary. There are quiet hours, and a dedupe log so each alert fires once. Tapping one opens the right screen. |
| **More** | Everything above that isn't a tab, plus Notifications, Change Password, About, Manage Users (admin) and Sign Out |

**Background jobs (APScheduler):** daily Toast sales sync, an open-orders refresh every few minutes plus a full scan, and the alert runner. This is the same scheduler the Team hub will use for recurring tasks and reminders.

### 2.5 Pieces the Team hub will reuse

| Existing piece | Where | How the Team hub uses it |
|---|---|---|
| Users, admin flag, feature grants (`allowed_features` JSON) | `app/models.py::User`, `app/features.py` | New feature keys `team_tasks`, `team_chat`; admins manage everything |
| Session/bearer auth | `app/auth.py` | Same auth on all new endpoints |
| Push via Firebase (FCM), per-alert prefs, dedupe log, quiet hours | `services/notifications.py`, `push.py`, `notification_*` tables | New alert keys: `task_assigned`, `task_due`, `announcement`, `chat_message` |
| Push tap to screen routing | `mobile/lib/core/notifications/push_target.dart` | New routes: `task`, `announcement`, `chat` |
| File uploads (invoices on disk) | `config.invoice_uploads_dir` pattern | Same pattern for `uploads/team/` (task proof photos, chat images) |
| APScheduler in-process jobs | `app/scheduler.py` | Recurring task generation, overdue reminders |
| Daily closing checklists (2 forms) | `checklist_submissions` | Link: "Checklist not done" can become an auto-task (Phase 3) |
| Home "Today" tile grid | `home/widgets/today_card.dart` | New tile: **My tasks** (count due today / overdue) |
| WhatsApp sends via Whapi | `services/whapi_client.py` | Optional fallback for people without the app (Phase 3) |

**Current scale:** 9 users (3 admins), 2 locations (Spice Town grocery, Street Kitchen). A single SQLite database runs on the Mac mini behind a Cloudflare Tunnel. The design is sized for **under 50 users**. It deliberately avoids infrastructure that only pays off at hundreds of users.

---


## 3. Concepts and permissions

| Concept | Meaning |
|---|---|
| **Team** | A group of users, e.g. *Everyone*, *Grocery*, *Kitchen*, *Managers*. A user can be in several teams. Teams are what tasks, announcements and chat channels are aimed at. There is no separate "location" field. Teams are created and staffed by admins (Phase 0). |
| **Task** | Something one person or one team must do, optionally by a due time. It can require a photo or a note to complete. |
| **Team task** | One task aimed at a team. Any member can complete it, and the first to do so completes it for everyone. The list shows who did it. |
| **Assignment batch** | Assigning one task to several named people creates **one task per person** (each is tracked separately), linked by a shared `assignment_batch_id`. The creator still sees it as one assignment: *"Clean freezer · Sent to Ravi, Sam, John · 2 of 3 done"*. |
| **Recurring template** | A rule that creates tasks on a schedule. The rule describes **when the task is due** (e.g. *every Monday 3 PM*) in store time (America/New_York). A separate "appears" time says how far ahead it shows up in people's lists (default 7 hours: *due 3 PM, appears 8 AM*). Editing a template only changes future occurrences. |
| **Announcement** | A one-way post from an admin to one or more teams, optionally requiring acknowledgment. The people it was sent to are **fixed when it's posted**. Someone who joins Kitchen later doesn't get old Kitchen announcements, and someone who leaves stays counted, so "7 of 9 acknowledged" never changes after the fact. |
| **Team channel** | A group chat. Each team gets exactly one, created automatically. Its members are the team's members. **There are no ad-hoc group channels in v1.** An admin who needs a new group creates a team. |
| **Direct message (DM)** | A one-to-one chat between two users. Either person can **block** the other (§4.4). |
| **Report** | A flag on a chat message, sent to admins for moderation. |

### Who can do what

"Member" means a non-admin user. Feature keys are granted per user in Manage Users, exactly like existing pages.

| Action | Admin | Member with `can_assign` | Member with `team_tasks` | Member with `team_chat` |
|---|---|---|---|---|
| Create teams, set members | ✅ | – | – | – |
| Create a task for **themselves** (personal to-do) | ✅ | ✅ | ✅ | – |
| Assign tasks to other people, a team, or several people (batch) | ✅ | ✅ | – | – |
| See and complete tasks assigned to them or to their teams | ✅ | ✅ | ✅ | – |
| Edit, reassign or cancel a task | ✅ any | Ones they created | Own personal to-dos | – |
| View or cancel a whole batch | ✅ any | Ones they created | – | – |
| Create and edit recurring templates | ✅ | – | – | – |
| Post announcements, see who acknowledged, send reminders | ✅ | – | – | – |
| Read and acknowledge announcements sent to them | ✅ | ✅ | ✅ | ✅ (everyone gets their own announcements; no feature key needed) |
| Chat in their teams' channels, send DMs | ✅ | – | – | ✅ |
| Delete their own messages | ✅ | – | – | ✅ |
| Report a message | ✅ | – | – | ✅ |
| Block or unblock a user in DMs | ✅ (blocks apply to admins too) | – | – | ✅ |
| Moderation: see reports, resolve or dismiss, delete any message | ✅ | – | – | – |

Notes:

- `can_assign` is a per-user boolean on `users`, set in Manage Users. It's for shift leads who assign work but shouldn't be full admins, and it can be switched on or off without an app release. It only makes sense together with `team_tasks`.
- Blocking only affects DMs. It doesn't hide someone's messages in a shared team channel, and it doesn't affect tasks or announcements. Abuse in a team channel goes through report and moderation.
- Admins can see that a block exists between two users (in moderation), but not why.

---

## 4. Product design (screens)

### 4.1 Navigation

- **Phase 0: no change.** The bar stays **Home · Ordering · Scan · Reports · More**. Phase 0 ships nothing visible for Team in the app, only the "Update required" gate and the push-token fixes (§8).
- **Phase 1** (when Tasks ships): the bar becomes **Home · Team · Ordering · Scan · More**, and **Reports moves into More** (a row near the top, same screens). Six tabs are cramped on small iPhones, and staff will open Team daily but Reports rarely.
- The **Team** tab shows only **Tasks** in Phase 1, with no segment control. **Phase 2** adds the segments **Tasks · Announcements · Chat**.
- The Team tab badge is open tasks due today, plus (from Phase 2) unread messages and unacknowledged required announcements.
- The Home **Today** grid gets a **My tasks** tile in Phase 1 (e.g. `3 · Clean cooler +2`), which opens Team › Tasks.
- It is gated by feature keys like existing pages. A user without `team_tasks` sees no Tasks segment. In Phase 2, a user without `team_tasks` or `team_chat` sees Team with Announcements only.

### 4.2 Tasks (Phase 1)

**List (default "Mine")**

```
[ Mine | Team | Created by me | All (admin) ]         [ + ]
─ Overdue ─────────────────────────────────
 ⚠ Restock rice aisle            Grocery · due 2:00 PM   📷
─ Today ───────────────────────────────────
 ○ Clean walk-in cooler          Kitchen · due 3:00 PM   📷  ↻
 ○ Call KP Produce re: credit    You · due 5:00 PM
─ Upcoming ────────────────────────────────
 ○ Deep clean fryer              Kitchen · Tue
─ Done (today) ────────────────────────────
 ✓ Face shelves    by Ravi 11:40 AM  [photo]
```

- **Swipe right** marks a task done. If the task requires a photo or note, a sheet asks for it instead. The server enforces this too.
- **Tap** opens the detail view: description, assignee, due time, sub-items (optional), comments, completion proof and the full history (who created, reassigned or completed it, and when).
- **Status:** `open → done`, or `open → can't do`, which requires a reason and notifies the creator. A done or can't-do task can be **reopened** by its creator or an admin. There is no "in progress" status; it isn't worth the extra tap for a store crew.
- **Recurring tasks** show a ↻ icon. The detail view says *"Repeats: due every Monday 3 PM, appears 8 AM"*. A missed occurrence (the server was down) still appears, marked overdue, if it was due within the last 12 hours.
- **Batch tasks** (one assignment to several people):
  - Each person sees only their own copy.
  - In **Created by me** and **All**, the creator sees one row: *"Clean freezer · Sent to Ravi, Sam, John · 2 of 3 done"*.
  - Tapping it shows each person's status and proof, with **Cancel for everyone** (cancels the remaining open copies).

**Create (admins, or members with `can_assign`; others create personal to-dos only)**

The fields are:

- Title (required)
- Assign to: Me / a person / a team / several people (the last creates a batch)
- Due: Today / Tomorrow / Pick date+time / No due
- Repeat (admins only; creates a recurring template): None / Daily / Weekdays / Weekly on… / Monthly on day…, plus **Appears: at due time / 1 h / 3 h / 7 h (default) / 1 day before**. The form previews it in words: *"Due every Monday 3 PM, appears 8 AM"*. Times are always store time.
- Requires: nothing / photo / note
- Priority: normal / urgent (urgent notifies even during quiet hours)
- Optional description and sub-items

Quick-create chips offer common titles. They come from the server, so they can change without an app release.

### 4.3 Announcements (Phase 2)

- A card feed, newest first, with pinned items on top.
- **Admin compose:** title, body, optional image, audience (one or more teams), and a "Require acknowledgment" toggle. The compose screen shows *"Will be sent to 9 people"*. That list is saved at posting and never recalculated.
- **Staff:** an **"I've read this"** button. Required announcements they haven't acknowledged show a banner on Home until they do.
- **Admin view:** *"7 of 9 acknowledged"* (from the saved recipient list), who hasn't yet, and a **Remind** button that notifies only them.

### 4.4 Chat (Phase 2)

- **Channel list:** team channels first, then DMs. Each row shows the last message and an unread count. A **New message** button starts a DM with anyone in the user's teams.
- **Conversation:**
  - Text and photos (camera or library, compressed on the phone and checked again by the server, §9).
  - Long-press your own message to **Delete**, or anyone else's to **Report**.
  - "Seen by N" on the last message.
- **Create task from a message:** long-press › "Create task…" opens the task form filled in with the message text and a link back to it. *This is what makes the hub better than WhatsApp.* It is only offered to people who can assign tasks (or to create a personal to-do).
- **@mentions** notify only the person mentioned, even if they muted the channel.
- **Mute channel** is a per-user setting in the channel's menu.
- **Report:** pick a reason (the list comes from the server) and add an optional note. You get a confirmation, and the message stays visible until an admin acts.
- **Block (DMs only):**
  - **Block** is in the DM's header menu, with *"Block Ravi? Neither of you will be able to message the other here. Team channels aren't affected."* The DM then disappears from your list.
  - The other person's sends fail with a neutral *"You can't message this person."* Nothing tells them they were blocked.
  - **Unblock** is in **Team › Settings › Blocked people**, which lists everyone you've blocked.
  - No new DM can be started between two people while either has blocked the other.

### 4.5 Admin moderation (Phase 2)

**More › Admin › Reports** (admins only; the Home status area shows a count of open reports):

```
Open reports (2)
 ─ "…" in Kitchen · by Sam, 2:14 PM
   Reported by Ravi · Harassment · "not ok"
   [ Delete message ]  [ Resolve ]  [ Dismiss ]
```

- **Delete message** removes it for everyone (it shows as "Message removed by an admin") and resolves the report.
- **Resolve** means handled some other way, e.g. a conversation in person. It takes an optional note.
- **Dismiss** means no action was needed.
- Resolved and dismissed reports stay in a **History** tab, with who acted and when.
- Admins can also delete any message directly from the conversation (long-press › Delete for everyone), without a report.
- The screen also shows any blocks that exist between two users (pairs only, no reasons).

### 4.6 Website

| Phase | Website pages |
|---|---|
| **0** | **Teams** admin page (`#/team/teams`): create, rename and archive teams, set members. Manage Users gets the `can_assign` checkbox and the `team_tasks`/`team_chat` feature keys. |
| **1** | **Tasks** (`#/team/tasks`): the same views as the app (Mine / Team / Created by me / All), create/assign including batches, and batch detail. **Templates** (`#/team/templates`): create, edit, pause and delete recurring templates, with the "due … appears …" preview. |
| **2** | **Announcements** admin (`#/team/announcements`): compose, acknowledgment status, Remind. **Moderation** queue (`#/team/reports`): the same actions as the app. |
| Not in v1 | **Chat on the website.** Chat is phone-only in v1. |

Admins do most setup (teams, templates, announcements) from a desk, so the website covers management and the app covers doing the work.

---

## 5. Data model (SQLite via SQLAlchemy + Alembic, migrations 0026+)

All stored timestamps are UTC (the existing `UTCDateTime` type). Business rules that depend on the calendar (recurring tasks, "today", quiet hours) are evaluated in **America/New_York**. Every "by" field stores `*_user_id` plus a denormalized `*_name` snapshot, the same convention as `checklist_submissions`, so records still read correctly if an account is later deleted.

```sql
-- Teams (Phase 0) -----------------------------------------------------
teams(id PK, name UNIQUE, color, is_everyone BOOL, created_at, archived_at NULL)
team_members(team_id FK, user_id FK, added_at, PRIMARY KEY(team_id, user_id))
users + can_assign BOOL DEFAULT 0            -- new column: may assign tasks to others

-- Change feed (Phase 0) - see §7 ------------------------------------------
team_changes(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,     -- the cursor; never reused, even after pruning
  entity_type TEXT CHECK(entity_type IN ('task','task_template','announcement',
                         'channel','message','team','dm_block')),
  entity_id INT,
  action TEXT CHECK(action IN ('created','updated','deleted')),
  team_id NULL, user_id NULL, channel_id NULL,   -- who may see this row (visibility keys)
  created_at)
INDEX team_changes(created_at)                   -- nightly prune of rows older than 30 days

-- Media (Phase 0 module, first used in Phase 1) - see §9 -------------------
team_media(
  id PK, token TEXT UNIQUE,                  -- secrets.token_urlsafe(24); the only public handle
  path TEXT,                                 -- uploads/team/YYYY/MM/<token>.jpg, server-generated
  content_type TEXT DEFAULT 'image/jpeg',    -- always JPEG after re-encoding
  bytes INT, width INT, height INT,
  uploaded_by_user_id, uploaded_by_name, created_at)

-- Tasks (Phase 1) -------------------------------------------------------
task_templates(                              -- recurring rules
  id PK, title, description,
  requires TEXT CHECK(requires IN ('none','photo','note')),
  priority TEXT CHECK(priority IN ('normal','urgent')),
  assignee_user_id NULL, assignee_team_id NULL,          -- exactly one set (CHECK)
  rrule TEXT,                  -- WHEN THE TASK IS DUE, e.g. 'FREQ=WEEKLY;BYDAY=MO;BYHOUR=15;BYMINUTE=0'
                               -- RFC 5545 subset: FREQ DAILY|WEEKLY|MONTHLY, BYDAY, BYMONTHDAY,
                               -- BYHOUR, BYMINUTE
  timezone TEXT DEFAULT 'America/New_York',              -- the rrule is evaluated in this zone
  generate_ahead_minutes INT DEFAULT 420,                -- due Mon 3 PM -> appears Mon 8 AM
  active BOOL DEFAULT 1,
  created_by_user_id, created_by_name, created_at, updated_at)

tasks(
  id PK, template_id FK NULL,
  assignment_batch_id TEXT NULL,             -- uuid shared by tasks from one multi-person assignment
  title, description, requires, priority,
  assignee_user_id NULL, assignee_team_id NULL,          -- exactly one set (CHECK)
  due_at NULL,                                           -- UTC
  status TEXT CHECK(status IN ('open','done','cant_do','cancelled')),
  completed_by_user_id NULL, completed_by_name NULL, completed_at NULL,
  completion_note NULL, completion_media_id FK NULL,     -- -> team_media
  cant_do_reason NULL,
  source_type NULL, source_id NULL,   -- 'chat_message' | 'checklist' | 'invoice' | 'alert'
  created_by_user_id, created_by_name, created_at, updated_at,
  UNIQUE(template_id, due_at))        -- recurring generation is idempotent
INDEX tasks(assignee_user_id, status, due_at), tasks(assignee_team_id, status, due_at),
      tasks(assignment_batch_id)

task_items(id PK, task_id FK, position, text, done BOOL, done_by_user_id NULL, done_at NULL)
task_events(id PK, task_id FK, user_id, user_name, kind, detail_json, created_at)
     -- created / reassigned / done / reopened / cant_do / cancelled / commented / edited;
     -- the audit trail, never updated or deleted

-- Announcements (Phase 2) -----------------------------------------------
announcements(id PK, title, body, media_id FK NULL, require_ack BOOL, pinned BOOL,
  created_by_user_id, created_by_name, created_at, edited_at NULL, deleted_at NULL)
announcement_audience(announcement_id FK, team_id FK)    -- display only ("Kitchen, Grocery")
announcement_recipients(                                 -- snapshot, written at post time
  announcement_id FK, user_id FK, seen_at NULL, acked_at NULL,
  last_reminded_at NULL,
  PRIMARY KEY(announcement_id, user_id))

-- Chat (Phase 2) ---------------------------------------------------------
channels(id PK, kind TEXT CHECK(kind IN ('team','dm')),
  team_id NULL,             -- set for kind='team' (one channel per team)
  dm_key UNIQUE NULL,       -- 'minUserId:maxUserId' so a DM pair has exactly one channel
  created_at, archived_at NULL)
channel_members(channel_id FK, user_id FK, last_read_message_id INT DEFAULT 0,
  muted BOOL DEFAULT 0, hidden BOOL DEFAULT 0,           -- hidden: blocker's view of a blocked DM
  PRIMARY KEY(channel_id, user_id))
messages(id PK AUTOINCREMENT, channel_id FK, sender_user_id, sender_name,
  body TEXT, media_id FK NULL, client_uuid TEXT,         -- client-generated for retry-safe sends
  mentions_json TEXT NULL,                               -- [user_id, ...] parsed server-side
  created_at, edited_at NULL,
  deleted_at NULL, deleted_by_user_id NULL, delete_reason NULL,   -- own delete or moderation
  UNIQUE(sender_user_id, client_uuid))
INDEX messages(channel_id, id)
message_reports(id PK, message_id FK, reporter_user_id, reporter_name, reason, created_at,
  status TEXT CHECK(status IN ('open','resolved','dismissed')) DEFAULT 'open',
  resolved_by_user_id NULL, resolved_at NULL, resolution_note NULL)
dm_blocks(blocker_user_id FK, blocked_user_id FK, created_at,
  PRIMARY KEY(blocker_user_id, blocked_user_id))
```

**Design choices worth reviewing:**

- **The change feed is written in the same transaction as the change.** Every write path calls one helper, `record_change(db, entity_type, entity_id, action, team_id=, user_id=, channel_id=)`, before its `commit()`, so a change can never exist without its feed row (or the reverse). Rows are *pointers*, not payloads: the client refetches the entity by id. Deletions are rows too (tombstones), so a deleted message or cancelled task disappears on other phones. The visibility keys (`team_id`, `user_id`, `channel_id`) let `/updates` filter to what the caller may see without joining back to every entity table. See §7.
- **Announcement recipients are a snapshot.** At post time the server expands the audience teams into one `announcement_recipients` row per member *at that moment*. Joining Kitchen later does not add you to old announcements; leaving does not remove you. "7 of 9 acknowledged" is computed only from these rows, so historical numbers never change. `announcement_audience` is kept only to display which teams it went to.
- **Recurring templates describe when the task is due.** `rrule` is the due time in `timezone` (America/New_York), not a generation time plus an offset. The task *appears* `generate_ahead_minutes` before it's due. The old `due_offset_minutes` is gone. `last_generated_for` is also dropped: `UNIQUE(template_id, due_at)` already makes generation idempotent, so the scheduler simply tries to insert every occurrence whose generate time has passed (within the catch-up window) and ignores the ones that already exist. One fewer piece of state that can drift. Edits to a template apply to future occurrences only; already-created tasks keep their own copy of title/requires/assignee.
- **Multi-person assignment keeps one task per person** (each tracked individually) and links them with `assignment_batch_id`, so "Clean freezer → Ravi, Sam, John, 2 of 3 done" can still be shown and cancelled as one assignment.
- **Team tasks are one row.** Assigning to "Kitchen" creates one task that any Kitchen member can complete, first one wins.
- **Team channel membership is derived from `team_members`**, synced whenever membership changes. Removing someone from Kitchen removes them from the Kitchen chat; re-adding restores their access to the history. v1 has **no ad-hoc group channels**: an admin who needs a new group creates a team.
- **Unread = `messages.id > channel_members.last_read_message_id`.** One integer per member per channel; "seen by N" is computed from members' values. Cheap and correct for small teams.
- **DM blocks** are one row per direction. While a block exists in *either* direction, no DM can be created between the two users and no message can be sent in their existing DM. The blocker's copy is hidden (`channel_members.hidden`). Team channels, tasks and announcements are unaffected.
- **Soft delete for messages** (`deleted_at` + who + reason). The API returns a deleted message as "Message deleted". Admins can see the original through the report that caused the deletion for 30 days. Reports have a real status (`open` / `resolved` / `dismissed`) and record who handled them.
- **Media has its own table.** Tasks, messages and announcements reference `team_media.id`; clients only ever see the random `token`. The stored file is always a server re-encoded JPEG under a server-generated path; the original filename is never stored or used (§9).
- **Idempotent writes:** `client_uuid` on messages and `UNIQUE(template_id, due_at)` on tasks, so retries and scheduler reruns never create duplicates. Same principle as the existing notification dedupe log.

---

## 6. API (FastAPI, `/api/team/...`)

All endpoints use the existing session/bearer auth unless marked **no auth**. Lists are **cursor-paginated** (`?before_id=` / `?after_id=`, `limit` ≤ 100), never offset. Authorization is checked on every call, including direct-ID access to a single object (§12 tests each negative case). "Member" below means a user with the named feature key; admins can always do everything.

Who can call each endpoint:

| Label | Meaning |
|---|---|
| **any** | any signed-in user |
| **tasks** | has `team_tasks` (or admin) |
| **assigner** | admin, or `can_assign` user |
| **chat** | has `team_chat` (or admin) |
| **admin** | `is_admin` only |

```
# Bootstrap and change feed ------------------------------------------------ any
GET  /api/team/bootstrap
     -> teams + my memberships, unread counts, open task counts, badge total,
        config flags (kill switches / options, §10), current cursor,
        min_supported_build: {ios: N, android: N}
GET  /api/team/updates?since=<seq>
     -> {cursor, tasks: [ids], task_templates: [ids], announcements: [ids],
         channels: [ids], messages: [{channel_id, ids}], teams: [ids], dm_blocks: bool,
         deleted: {type: [ids]}, badges}
     -> or {resync_required: true} when since < oldest retained seq (§7)

# Teams (Phase 0) ----------------------------------------------------------
GET    /api/team/teams                          any (own teams); admin (all)
POST   /api/team/teams                          admin   {name, color}
PATCH  /api/team/teams/{id}                     admin   {name?, color?, archived?}
DELETE /api/team/teams/{id}                     admin   (archives; history kept)
PUT    /api/team/teams/{id}/members             admin   {user_ids: [...]}  (syncs team channel)
PATCH  /api/admin/users/{id}                    admin   + {can_assign}  (existing users endpoint)

# Tasks (Phase 1) ----------------------------------------------------------
GET  /api/team/tasks?view=mine|team|created|all&status=open|done|cant_do&before_id=
                                                tasks ('all' = admin only)
POST /api/team/tasks                            tasks (self only) / assigner (others)
     {title, description?, assignee_user_ids[] | assignee_team_id, due_at?,
      requires, priority, items[], source_type?, source_id?}
     -> several user ids = one task each, sharing a new assignment_batch_id
GET  /api/team/tasks/{id}                       assignee, assignee's team member, creator, admin
                                                -> task + items + events + batch summary
PATCH /api/team/tasks/{id}                      creator or admin  (edit, reassign, cancel)
POST /api/team/tasks/{id}/complete              assignee / team member; multipart note?, photo?
                                                (400 if `requires` not met)
POST /api/team/tasks/{id}/cant-do               assignee / team member  {reason} (required)
POST /api/team/tasks/{id}/reopen                creator, admin, or whoever completed it
POST /api/team/tasks/{id}/comments              anyone who can read the task  {body}
PATCH /api/team/tasks/{id}/items/{item_id}      assignee / team member  {done}

GET  /api/team/task-batches/{batch_id}          creator or admin  -> tasks + "2 of 3 done"
POST /api/team/task-batches/{batch_id}/cancel   creator or admin  (cancels the open ones)

GET    /api/team/templates                      assigner
POST   /api/team/templates                      admin   {title, rrule, timezone?, generate_ahead_minutes,
                                                         assignee_user_id | assignee_team_id, requires, ...}
PATCH  /api/team/templates/{id}                 admin   (future occurrences only)
DELETE /api/team/templates/{id}                 admin   (sets active=0; created tasks stay)
POST   /api/team/templates/preview              admin   {rrule, timezone, generate_ahead_minutes}
     -> next 5 occurrences: [{due_at_local, generate_at_local, due_at_utc}]  (DST-checked)

# Announcements (Phase 2) --------------------------------------------------
GET  /api/team/announcements?before_id=         any (only ones they're a recipient of); admin (all)
POST /api/team/announcements                    admin   multipart {title, body, team_ids[],
                                                         require_ack, pinned, image?}
                                                -> writes announcement_recipients snapshot
PATCH /api/team/announcements/{id}              admin   (edit text / pin / delete)
POST /api/team/announcements/{id}/seen          recipient
POST /api/team/announcements/{id}/ack           recipient
GET  /api/team/announcements/{id}/recipients    admin   -> each recipient + seen/acked times,
                                                           "7 of 9 acknowledged"
POST /api/team/announcements/{id}/remind        admin   (push only to recipients not yet acked)

# Chat (Phase 2) - team channels + DMs only --------------------------------
GET  /api/team/channels                         chat  -> my channels (hidden DMs excluded),
                                                         last message, unread, muted
POST /api/team/channels/dm                      chat  {user_id} -> get-or-create
                                                (403 if a block exists either way)
GET  /api/team/channels/{id}/messages?before_id=&after_id=&limit=50
                                                channel member
POST /api/team/channels/{id}/messages           channel member  {body, client_uuid} | multipart + image
                                                (same client_uuid -> same message, no duplicate;
                                                 403 in a blocked DM)
POST /api/team/channels/{id}/read               channel member  {last_read_message_id}
PATCH /api/team/channels/{id}/me                channel member  {muted?, hidden?}
DELETE /api/team/messages/{id}                  sender (own)   or admin (moderation, {reason})
POST /api/team/messages/{id}/report             channel member  {reason}  (notifies admins)

# Moderation (Phase 2) ------------------------------------------------------ admin
GET  /api/team/reports?status=open|resolved|dismissed&before_id=
     -> report + reported message (original text even if deleted, for 30 days) + reporter
POST /api/team/reports/{id}/resolve             {note?, delete_message: bool}
POST /api/team/reports/{id}/dismiss             {note?}

# DM blocks (Phase 2) ------------------------------------------------------ chat
GET    /api/team/blocks                         -> users I have blocked
POST   /api/team/blocks                         {user_id}
DELETE /api/team/blocks/{user_id}
     (admins can see that a block exists in moderation views, not why)

# Media ---------------------------------------------------------------------
POST /api/team/media                            tasks or chat  multipart image
     -> {token, width, height}   (validated + re-encoded server-side, §9; 413 / 415 on reject)
GET  /api/team/media/{token}                    any user allowed to see the task / message /
                                                announcement that references it; never a
                                                public static folder

# Notifications / devices (Phase 0 changes to the existing router) --------
POST   /api/notifications/devices               any  {token, platform, previous_token?}
                                                (moves the token to the caller; deletes previous_token)
POST   /api/notifications/unregister            no auth  {token}
                                                (deletes only that token row; works after the
                                                 session expired - possessing the token is the proof)
```

**Notes:**

- The website uses the same endpoints.
- `min_supported_build` is per platform because iOS and Android builds are numbered and released separately. The app compares it with its own build number (`package_info_plus`) and shows "Update required" linking to the right store.
- The existing authenticated `DELETE /api/notifications/devices?token=` stays for older app builds (additive-only API, §10).
- Error shape is the existing `{detail}`; block and authorization failures use neutral messages that don't reveal whether the other user exists or who blocked whom.

---

## 7. Change feed and real-time delivery

### 7.1 Why v1's single counter wasn't enough

Revision 1 proposed one `team_change_seq` integer that went up on every write. That tells a client *that* something changed after cursor 120, but not *what*. To answer that, the server would have to scan every task, message and announcement for an "updated after 120" marker. It also could not report deletions at all, because a deleted row has nothing left to scan. A deterministic "what changed after X" needs every change recorded as its own row.

### 7.2 The `team_changes` table

```sql
team_changes(
  seq         INTEGER PRIMARY KEY AUTOINCREMENT,  -- the cursor; strictly increasing
  entity_type TEXT,   -- 'task' | 'task_template' | 'announcement' | 'channel' | 'message' | 'team' | 'dm_block'
  entity_id   INTEGER,
  action      TEXT,   -- 'created' | 'updated' | 'deleted'
  team_id     INTEGER NULL,   -- set when the change is visible to a team
  user_id     INTEGER NULL,   -- set when it's visible to one person (their task, their block)
  channel_id  INTEGER NULL,   -- set for channel/message changes
  created_at  DATETIME
)
INDEX team_changes(created_at)
```

- **Written in the same database transaction as the change itself**, through one helper, `record_change(db, entity_type, entity_id, action, team_id=…, user_id=…, channel_id=…)`. If the write rolls back, its change row rolls back with it, so the feed can never claim a change that didn't happen or miss one that did. Every Team write path calls it, and a test enforces that (§12).
- **Rows are pointers, not payloads.** A row says "task 57 was updated", and the client then fetches task 57. This keeps the table tiny, and it means the feed can never leak content the reader isn't allowed to see: the follow-up fetch goes through the normal authorization checks.
- **Deletions are rows too (tombstones).** `('message', 912, 'deleted')` tells the open chat to replace that bubble with "Message deleted".
- `AUTOINCREMENT` (not a plain rowid) guarantees a `seq` is never reused, even after pruning.

### 7.3 `GET /api/team/updates?since=<seq>`

1. If `since` is older than the oldest retained row (see §7.4), the response is `{"resync_required": true, "cursor": <current max seq>}`, and nothing else.
2. Otherwise the server selects `seq > since`, **filtered to what the caller may see**:
   - Admins see everything.
   - For everyone else a row is visible if it has:
     - `user_id` = the caller; or
     - `team_id` in the caller's teams; or
     - `channel_id` in channels the caller is a member of.
   - Rows with none of the three set (e.g. a team renamed) are visible to everyone.
3. The response groups the visible rows by type, with ids de-duplicated, and adds the new cursor and the badge counts:

```json
{
  "cursor": 131,
  "tasks": {"changed": [57, 60], "deleted": []},
  "announcements": {"changed": [9], "deleted": []},
  "channels": {"changed": [3], "deleted": []},
  "messages": {"changed": [], "deleted": [912]},
  "teams": {"changed": [], "deleted": []},
  "badges": {"tasks_due_today": 3, "unread_messages": 5, "unacked_announcements": 1}
}
```

**`cursor` is the highest seq the server examined, not just the highest visible one.** If rows 121–131 were all somebody else's, the caller still gets `cursor: 131`, so they don't re-scan those rows on the next poll. For new messages the client doesn't fetch message by message: a changed `channel` id means "call `GET /channels/{id}/messages?after_id=<last seen>`".

### 7.4 Worked example: `since=120`

Ravi is in **Kitchen** (team 2), not Grocery (team 1). He is in the Kitchen channel (channel 3) and in a DM with Sam (channel 8).

| seq | entity | action | team_id | user_id | channel_id | Visible to Ravi? |
|---|---|---|---|---|---|---|
| 121 | task 57 | created | 2 | – | – | ✅ Kitchen team task |
| 122 | task 58 | created | – | 14 (Sam) | – | ❌ Sam's own task |
| 123 | message 910 | created | – | – | 3 | ✅ Kitchen channel |
| 124 | task 57 | updated | 2 | – | – | ✅ (merged with 121) |
| 125 | message 905 | created | – | – | 5 (Grocery) | ❌ not a member |
| 126 | announcement 9 | created | – | 11 (Ravi) | – | ✅ he's a snapshot recipient* |
| 127 | task 60 | created | – | 11 (Ravi) | – | ✅ assigned to him |
| 128–130 | messages 911–913 | created | – | – | 8 | ✅ his DM |
| 131 | message 912 | deleted | – | – | 8 | ✅ tombstone |

\*An announcement writes **one change row per recipient** (`user_id` set), because its audience is the snapshot in `announcement_recipients`, not current team membership (§5).

Response: tasks `[57, 60]`; announcements `[9]`; channels `[3, 8]` (fetch after the last seen id); messages deleted `[912]`; `cursor: 131`. Ravi's next poll asks for `since=131`.

### 7.5 Pruning and resync

- A nightly job deletes `team_changes` rows older than **30 days**.
- A phone that hasn't polled for a month, or a cursor from before a restored backup, gets `resync_required`. The client then drops its Team cache and reloads everything through `/bootstrap` and the list endpoints, just like a fresh install. It never tries to piece together a partial state.
- A `since` *greater* than the current max seq (a cursor from a different database, e.g. dev vs prod) is treated the same way, as a resync.

### 7.6 Polling cadence (unchanged from v1)

| Where the user is | Poll `/updates` every |
|---|---|
| An open chat conversation | 5 s |
| Anywhere else in the Team tab | 30 s |
| Rest of the app (foreground) | 120 s, badge counts only |
| App resumed from background | Immediately, once |
| App in background | Never; push (§8) covers it |

At about 10 users this is at most roughly 120 small requests a minute, even if everyone sits in an open chat. Each is an indexed range scan on `team_changes`.

### 7.7 Delivery options

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **A. FCM push + polling `/updates` (the cadence above)** | Uses only what exists. Works through Cloudflare Tunnel and Tailscale unchanged. Easy to test. Survives backend restarts. The change feed makes it exact. | A 0–5 s delay in an open chat, and some extra requests. | **v1** |
| B. WebSocket `/ws` per open app | Instant | Connection management, reconnect/auth edge cases, and tunnel idle timeouts needing heartbeats. With a single uvicorn worker it's feasible, but it's real work. | Phase 3, **only if the trigger below is met** |
| C. Third-party chat SDK (Stream, Sendbird, Firestore) | Fast to build and polished | Monthly cost, data outside our DB, a second auth system, and hard to link to tasks and invoices | **No** |

**Concrete trigger for WebSockets.** Build option B only if, after at least 4 weeks of real Phase 2 use, either of these holds:
- `/updates` p95 latency on the Mac mini is above 300 ms; or
- staff report delayed messages in an open chat more than twice in a month, and the server logs confirm the delay came from the polling interval (not push or network).

WebSockets would only change *how the client learns to call `/updates`* (a "poke" message). The change feed and data model stay exactly as they are.

---

## 8. Notifications and devices

### 8.1 Push rules (all through the existing `dispatch_alert`)

The rules below apply to every alert. They are already enforced by `dispatch_alert` and the job runner for the six existing alerts:
- A recipient must have the alert's feature and must not have switched it off.
- Each dedupe key is sent at most once (`notification_log`, written only after a real Firebase send).
- The existing quiet hours are **10 PM – 7 AM, America/New_York**.
- **The sender is never notified of their own action.** The actor is always removed from the recipient set.

| Alert key | Feature | When | Recipients | Dedupe key | Quiet hours |
|---|---|---|---|---|---|
| `task_assigned` | `team_tasks` | Task created, reassigned, or a recurring occurrence generated | Assignee; for a team task, all current team members | `task_assigned:{task_id}:{user_id}` (a reassignment to someone new gets its own key) | Respected. **`urgent` tasks bypass them.** |
| `task_due` | `team_tasks` | 30 min before `due_at` (stage `soon`), and 1 h after, if still open (stage `overdue`) | `soon`: the assignee(s). `overdue`: the assignee(s) **and** the creator. | `task_due:{task_id}:{stage}` | Respected; `urgent` bypasses. A `soon` reminder skipped by quiet hours is **dropped, not queued**. The `overdue` one is sent at 7 AM if the task is still open. |
| `task_cant_do` | `team_tasks` | Someone taps "Can't do" | The creator (or the batch creator, for a batch) | `task_cant_do:{task_id}` | Respected |
| `announcement` | *(none; everyone)* | Posted, or on an admin's **Remind** | **The snapshot rows in `announcement_recipients`**, never current team membership. On Remind: only recipients with `acked_at IS NULL`. | `announcement:{id}:post`, `announcement:{id}:remind:{n}` (n = the remind count, so each Remind sends once) | Respected. A post during quiet hours is delivered at 7 AM. |
| `chat_message` | `team_chat` | A new message in a team channel or DM | Channel members except the sender, except anyone who **muted** the channel, and except the other side of a **blocked** DM (§3). An **@mention reaches the mentioned person even if they muted** (but never across a block). | Not deduped per message. Collapsed with FCM `thread-id` (iOS) / `tag` (Android) = channel id. **Throttled** to at most one push per user per channel per 60 s while they still have unread messages there. | Respected. Nothing is queued: unread counts show when the app next opens. |

**Payloads** carry `route` (`task` | `announcement` | `chat`) plus the id, so tapping opens the right screen through the existing `push_target.dart` routing. Unknown routes fall back to Home, as they do today. The iOS app badge is set from `badges` (§7.3) and also sent in the APNs payload.

### 8.2 Recurring generation and reminder timing (America/New_York)

- **Business times are local; stored times are UTC.** A template's `rrule` describes **when each occurrence is due**, evaluated in `America/New_York` with python-dateutil `rrule` + `zoneinfo`. `generate_ahead_minutes` (default 420) says how early the task appears.
  - *Example:* "Clean walk-in cooler", `FREQ=WEEKLY;BYDAY=MO;BYHOUR=15;BYMINUTE=0`, 420 minutes ahead. It is **due Monday 3:00 PM ET** and **appears Monday 8:00 AM ET**, which triggers `task_assigned`. The `soon` reminder fires at 2:30 PM, and if it's still open, `overdue` fires at 4:00 PM, to the assignee(s) and the creator.
- **Generator:** a scheduler job runs every 5 minutes. It creates each occurrence whose `generate_at = due_at − generate_ahead` is ≤ now and doesn't exist yet. `UNIQUE(template_id, due_at)` makes it idempotent across overlapping runs and restarts.
- **DST:**
  - A local time that doesn't exist (e.g. 2:30 AM on the spring-forward Sunday) moves forward to the next valid time (3:00 AM).
  - An ambiguous time (1:30 AM on the fall-back Sunday) takes the **first** occurrence.
  - Because the rule is evaluated in local time, "every day at 3 PM" stays 3 PM ET all year; only the UTC value changes.
- **Catch-up after downtime:**
  - Occurrences due within the **last 12 h** are still created, and marked overdue immediately.
  - Older missed ones are skipped and logged, not dumped on staff all at once.
  - Catch-up tasks don't get a `soon` reminder, only `task_assigned`.
- **Template edits** affect future occurrences only. Existing tasks keep their own title, assignee and due time.
- **Reminder jobs** run in the same 5-minute loop. A task edited to a new `due_at` gets fresh reminders, because the dedupe key includes the stage and the job checks the task's current `due_at`.

### 8.3 Shared store phone: the FCM token lifecycle

One store phone can be used by several employees. A push must only ever reach **whoever is signed in on that phone right now**.

**What happens today** (`services/notifications.py`, `notification_service.dart`):
- `device_tokens` is keyed by the FCM token (unique), not by user. On sign-in, `register_device` **upserts by token**. A token last registered by someone else is **moved** to the new user (the row's `user_id` is overwritten). So "A signs in, then B signs in on the same phone" already works: after B registers, only B gets pushes.
- On sign-out, `onSigningOut` calls `DELETE /api/notifications/device?token=…`. That endpoint requires auth, and `unregister_device` deletes the row by token.
- `onTokenRefresh` re-registers the new token for the signed-in user.

**The two gaps:**
1. **Expired session.** When the session is already dead (the logout path triggered by a 401), `onSigningOut(unregister: false)` skips the call, because the authenticated DELETE would fail anyway. The same happens if the unregister request times out (3 s). The token row stays attached to the old user, so **their alerts keep arriving on the shared phone until someone else signs in on it**.
2. **Token refresh.** When Firebase rotates the token, the new one is registered, but the **old row stays** until a later Firebase send reports it invalid. Until then the old user's row can still exist, and on a shared phone it might not be the current user's.

**Fixes (Phase 0):**
- **Unauthenticated unregister.** `POST /api/notifications/unregister {token}` takes **no auth**. Possessing the FCM token is the proof: the token is only readable on that phone, and the call can only *delete that one row*. It can't read anything or register anything. It always returns 204, whether or not the token existed, so it reveals nothing. The client **always** calls it on sign-out, including expired-session and 401 paths. The old authenticated DELETE stays for older app builds.
- **Retry.** If the unregister call fails (offline, timeout), the client keeps the token in a small "pending unregister" slot in secure storage. It retries on the next app launch *before* anyone signs in. If someone signs in first, their register call moves the token to them anyway.
- **Refresh replaces.** `POST /api/notifications/devices` accepts an optional `previous_token`. The client remembers the last token it registered (in secure storage, so it survives restarts). On `onTokenRefresh` it sends `{token: new, previous_token: old}`, and the server deletes the old row in the same transaction.
- **Server hygiene (unchanged):** rows for tokens FCM reports as invalid are deleted after each send, as today.

**Sequences:**

| Scenario | Steps | Result |
|---|---|---|
| A signs in, signs out, B signs in | A: register(T) → row(T→A). A signs out: unregister(T) → row deleted. B: register(T) → row(T→B) | Only B receives pushes. Between A's sign-out and B's sign-in, nobody does. |
| A's session expired, then sign-out | 401 → sign-out → **unauthenticated** unregister(T) → row deleted | Nobody receives pushes on the phone (fixed; today A still would). |
| Sign-out while offline | unregister fails → T saved as pending → next launch retries before sign-in | Row deleted at next launch. If B signs in first, register moves it to B. |
| A signs in and B signs in without A signing out (e.g. reinstall) | B: register(T) → row moved T→B | Only B (already works today) |
| Token rotates while A is signed in | refresh → register(T2, previous_token=T1) → row T1 deleted, row T2→A | Only A, only on the current token |

These are pytest cases in §12.

---

## 9. Uploads, security and privacy

### 9.1 Server-side image validation (tasks, chat, announcements)

The client still resizes to at most 1600 px and strips EXIF, but **the server trusts nothing from the client**. One module, `services/team_media.py`, handles every Team image upload:
1. **Declared type:** the multipart `Content-Type` must be `image/jpeg`, `image/png`, `image/webp` or `image/heic`/`heif`. Anything else is rejected with **415**.
2. **Size:** the body is read in chunks with a hard cap of **10 MB**. Over the cap, the read stops and returns **413**; the whole file is never buffered first.
3. **Decode check:** `Image.MAX_IMAGE_PIXELS` is set to a fixed limit, so decompression bombs raise instead of allocating. Then `Image.open(...).verify()`, then **re-open and fully `load()`** (verify alone doesn't decode pixel data). Any failure returns **415** ("not a valid image"). HEIC opens through the already-installed `pillow-heif` (Pillow 11.3 and pillow-heif are already in requirements).
4. **Check the real format:** Pillow's detected `format` must be JPEG, PNG, WEBP or HEIF, whatever the declared type said. A mismatch returns 415.
5. **Re-encode:** `ImageOps.exif_transpose` (keep the orientation), downscale so the longest side is at most **2048 px**, convert to RGB, save as **JPEG quality 85**. Re-encoding drops **all** metadata, including EXIF and GPS.
6. **Server-chosen name and path:** `secrets.token_urlsafe(24) + ".jpg"` under `uploads/team/YYYY/MM/`. The original filename and any client path are **never** used; they aren't even stored. The database row keeps only the server path, the byte size and the pixel dimensions.
7. **Atomic write:** write to a temp file in the same folder, `fsync`, then rename. A crash can never leave a half-written image referenced by a row.

### 9.2 Serving media

- Only through **`GET /api/team/media/{id}`**, which uses the normal session and bearer auth. It re-checks that the caller can see the *owning* object:
  - for a task image, the task is visible to them;
  - for a message image, they're a member of the channel;
  - for an announcement image, they're a recipient or an admin.
- `uploads/team/` is **never** under the public `dist/` static mount and has no direct URL. The response sets `Cache-Control: private, max-age=3600` and `X-Content-Type-Options: nosniff`.
- Images belonging to a soft-deleted message are not served to members (404). Admins can still view them from the report queue for 30 days (§9.4).

### 9.3 Authorization rules (enforced in queries, not just in the UI)

| Object | Who can read it | Who can write/act |
|---|---|---|
| Task | Assignee; members of the assignee team; creator; admin | Complete / can't do / comment: assignee or a team member. Edit / reassign / cancel: creator or admin. Assign to others: admin or `can_assign`. |
| Task batch | Creator; admin | Cancel: creator or admin |
| Task template | Admin | Admin |
| Announcement | Snapshot recipients; admin | Post, remind, view acks: admin. Ack: the recipient only. |
| Channel / messages | Channel members; admin (for moderation) | Send: members, and not across a DM block. Delete: the author, or an admin. |
| Report | The reporter (their own); admin | Resolve / dismiss: admin |
| DM block | The blocker (their own list); admin sees that it exists, not why | Create / remove: the blocker |

- Every list query is built from the caller's scope (their user id, team ids, channel ids).
- Every by-id endpoint re-checks access and returns **404, not 403**, for objects the caller can't see, so ids can't be probed.
- The change feed (§7) filters the same way, and its rows carry only ids.

### 9.4 Retention

| Data | Kept for | How |
|---|---|---|
| Chat messages and their images | 12 months | Nightly job hard-deletes messages older than 12 months and their files |
| Soft-deleted messages | Content kept 30 days for moderation, then purged | The same nightly job blanks `body` and removes the image |
| Reports | 12 months after resolution | Same job |
| Tasks, task events, announcements, recipients | Indefinitely | They're the audit trail |
| `team_changes` | 30 days | §7.5 |

All retention periods are settings in `config.py`, not constants.

### 9.5 Push previews

A push shows the sender and the first ~80 characters of the message ("Ravi: cooler door not closing…"), which can appear on a locked shared phone. Notifications › **Hide previews** (a per-user toggle, stored server-side like the existing per-alert prefs) replaces the body with "New message in Kitchen". DMs from a blocked user are never pushed at all.

### 9.6 Backups

The existing SQLite backup already covers the new tables. **`uploads/team/` must be added to the backup set** alongside `uploads/invoices/`. A restore check in Phase 0 confirms that a restored database and uploads folder serve a task image end to end. After a restore, clients reconnect with a cursor the database no longer has and get `resync_required` (§7.5).

### 9.7 Dev data copies

`sync_dev_data.sh` copies the prod database to dev. Chat is more personal than inventory, so the script gains a **`--no-chat` default**. After copying, it:
- deletes all rows from `messages`, `message_reports` and `dm_blocks`;
- removes message images from the dev copy of `uploads/team/`;
- clears push previews from `notification_log`.

Copying chat requires an explicit `--with-chat`.

---

## 10. App Store / Play review and releases

### 10.1 Pass review the first time

From Phase 2, chat and announcements make the app a **user-generated content (UGC)** app. That is App Review Guideline **1.2**, and Google Play's UGC policy is similar. Each item below ships in the same build as the feature it covers:

| Requirement | How we meet it | Phase |
|---|---|---|
| Report objectionable content | Long-press a message › **Report** (with a reason). It creates a `message_reports` row and sends a push to admins. | 2 |
| Block abusive users | A real **DM block** (§5, §6): while a block exists in either direction, neither person can start or send a DM. The blocker's DM is hidden from their own list. It applies to admins too. An admin can also deactivate a login entirely. | 2 |
| Timely moderation | An admin **moderation queue** in More › Admin › Reports: see the reported message in context, **delete** it, **resolve** or **dismiss** the report. Admins can delete any message. | 2 |
| Closed membership | No sign-up; every account is issued by the owner. Review notes say so plainly: *"Internal staff app for Spice Town employees. Accounts are issued by the owner; there is no public sign-up or public content."* | Already true |
| Working demo account | Reviewer login is a non-admin with only the features that phase ships. It sits in a "Review" team seeded with realistic data: tasks (open, done with photo, recurring) in Phase 1; announcements, a team channel and a DM in Phase 2. A dev-side seed script recreates it for each submission. | Every phase |
| Permission strings | `NSCameraUsageDescription` is reworded to cover "scanning invoices and task proof photos". `NSPhotoLibraryUsageDescription` is "attach photos to tasks and chat". Android declares the matching permissions. | 1 |
| Contact info | About screen (already present) | Already true |

No new background modes are needed, because push is already approved.

### 10.2 Feature flags: kill switches, not hiding

`/api/team/bootstrap` returns `config` flags (e.g. `tasks_enabled`, `chat_enabled`, `announcements_enabled`, `poll_interval_s`). They are used **only** for:

- turning off a piece that is broken in production without an emergency release;
- gradual rollout (e.g. chat for managers first);
- server-controlled options (poll interval, defaults, quick-create chips, report reasons).

**Each phase's App Store build is submitted with its feature visible and working**, reachable from the demo account. We do **not** ship a feature hidden and switch it on after approval. That risks rejection or removal under App Review Guideline **2.3.1** (hidden, dormant or undocumented features). The v1 advice to "ship the build with Team hidden" is withdrawn. Phase 0 has no Team UI at all, so nothing is hidden in it.

### 10.3 Changing the app less often

- **Tolerant clients.** An unknown enum value (a new task status, alert key, push route or change-feed entity type) renders as a generic row or is ignored. It never crashes. `push_target.dart` already falls back to Home for unknown routes.
- **Additive-only API.** New fields are optional. Nothing is removed or renamed while any store build still reads it. A breaking change gets a new path (`/api/team/v2/...`).
- **Server-defined lists:** quick-create chips, report reasons and team colors.
- **Minimum supported build, per platform.** `/api/auth/me` (and `/bootstrap`) return `min_supported_build: {ios: N, android: M}`. A lower running build (from `package_info_plus`) shows an "Update required" screen linking to the correct store. It lets us retire old API shapes safely. It ships in Phase 0, so it is already in users' hands before anything depends on it.

### 10.4 Getting builds to staff

- **TestFlight internal testing** (up to 100 App Store Connect users) needs **no Apple review**, so managers and key staff get dev builds within minutes of upload. Google Play has the equivalent internal testing track.
- **App Store release settings:** phased release **off** (about 10 users) and **"Automatically release"** after approval.
- **Unlisted distribution** (link-only listing) is still an option worth evaluating. It gets the same review, but the app no longer appears in public search.
- Small releases: each phase is its own build.

---

## 11. Rollout phases

Each phase ships as one backend deploy plus at most one store build. **A phase is done only when its §12 tests pass and the flows have been checked by hand on a phone running the dev build. Compiling is not done.**

### Phase 0: groundwork (no visible Team UI)

- Teams and members: backend, plus a **website** admin page to manage them. The `users.can_assign` flag. New feature keys `team_tasks` and `team_chat`.
- Backend **pytest** setup (a temporary SQLite database, fixtures for users and teams, an authenticated test client). The Playwright smoke test is committed to the repo; today it only exists as a throwaway script.
- **Push token fixes for shared phones** (§8): logout unregisters without needing a valid session; register accepts `previous_token`; the app remembers the last token it registered.
- `team_changes` change feed infrastructure: the `record_change` helper, the `/updates` endpoint skeleton, and the nightly prune (§7).
- Feature flags in `/bootstrap`, and `min_supported_build` per platform.
- The server-side image validation module (§9), with tests, ready for Phase 1.
- **App build:** only the "Update required" gate and the push token fixes. The bottom nav is unchanged.
- **Visible to users:** admins see the Teams page on the website. Staff see nothing new.
- **Exit:** Phase 0 tests in §12 pass. On a dev phone: log in as A, then as B, and only B gets a test push. Log out with an expired session and nobody gets it. Set `min_supported_build` above the dev build and the gate appears.

### Phase 1: Tasks only

- One-off and recurring tasks (§5 recurrence rules, America/New_York).
- Assigning to one person, a team (one task any member completes) or several named people (one task each, linked by `assignment_batch_id`).
- Photo and note proof, "Can't do" with a reason, reopen, comments, sub-items, and the event history.
- Reminders: the `task_assigned`, `task_due` and `task_cant_do` pushes.
- A **My tasks** tile on Home's Today grid.
- Website: task lists, task creation, recurring template management, and batch view and cancel.
- **App build:** the bottom nav becomes **Home · Team · Ordering · Scan · More**, with Reports moved into More. In this phase the Team tab shows only Tasks.
- **Visible to users:** everything above, for users granted `team_tasks`.
- **Exit:** Phase 1 tests in §12 pass. On a dev phone: create, assign, complete with a photo, "Can't do", let a recurring task generate, receive each reminder, and tap a push to open the task.

### Phase 2: Announcements and chat

- Announcements with recipients snapshotted at post time, acknowledgments, "7 of 9 read", and Remind (non-ackers only).
- Chat: team channels (automatic) and DMs only. No ad-hoc group channels in v1.
- Photos, mute, @mentions, unread counts and the app badge.
- Report, the admin moderation queue (resolve, dismiss, delete), own delete, and DM block/unblock.
- Long-press a message › **Create task**.
- **App build:** the Team tab gains Announcements and Chat.
- **Visible to users:** everything above; chat for users granted `team_chat`, announcements for everyone.
- **Exit:** Phase 2 tests in §12 pass. On two dev phones: send and receive messages, including a retry after airplane mode; check unread counts and the badge, mute, mentions, block, report, moderation, an announcement ack and Remind.

### Phase 3: Integrations

- Automatic tasks from existing signals:
  - closing checklist missing → a task for the closing lead;
  - invoice to close in Toast → a task for the office;
  - order due → a task for the orderer.
- WhatsApp fallback for anyone without the app.
- WebSockets **only if** polling is measurably inadequate (e.g. staff report chat delays, or request volume becomes a problem).
- **App build:** mostly none. These are backend changes.
- **Exit:** the tests for each auto-task (created once, correct assignee, closed when the signal clears) pass, plus a check on the dev phone.

### Phase 4 (optional)

A shift schedule (week grid, publish, swap requests), built **only if** it would replace a paid tool.

---

## 12. Testing plan

**Rule:** "done" means the automated tests below pass **and** the flows have been checked by hand on a phone running the dev build. A feature is never reported complete just because it compiles.

### Backend (pytest)

**Authorization** (Phase 0 harness, extended every phase):
- A user cannot read another user's unrelated tasks, **including by guessing IDs** (`GET /tasks/{id}` → 404/403).
- A team member sees that team's tasks; a non-member does not.
- A user cannot read or post in a channel they don't belong to, including by guessing IDs.
- Admin access works everywhere.
- Endpoints without the right feature key return 403.

**Tasks** (Phase 1):
- Create; assign to one user, to a team, and to several users (with `assignment_batch_id` set).
- Complete, including the first-wins rule for team tasks.
- Photo required rejects a completion without a photo; note required rejects one without a note.
- "Can't do" requires a reason; reopen.
- Recurring generation: occurrences are created at the right time, and **running the generator twice creates nothing new** (idempotent).
- Catch-up: occurrences due in the last 12 h are created; older ones are skipped and logged.
- Timezone: due times are computed in America/New_York and stored in UTC. **DST:** a nonexistent 2:30 AM on spring-forward shifts later, and an ambiguous time on fall-back is created once.
- Template edits affect only future occurrences.
- Batch view and batch cancel.

**Notifications** (Phases 0–2):
- The right recipients get each alert, and **the sender is never notified of their own action**.
- **Push token lifecycle:**
  - login as A then B on the same token → only B receives pushes;
  - logout with an expired session → the token is removed;
  - a `previous_token` on refresh → the old token row is gone;
  - unauthenticated unregister deletes only that token.
- Quiet hours are respected; `urgent` bypasses them.
- Dedupe keys prevent repeat sends; the chat throttle holds to one push per 60 s per channel.

**Change feed** (Phase 0, extended in 1–2):
- The change row is written in the same transaction as the change.
- `/updates?since=` returns exactly the changes after the cursor and only what the caller can see.
- Deletions appear as tombstones.
- After the prune, an old cursor gets `resync_required`.

**Chat** (Phase 2):
- A retried send with the same `client_uuid` creates one message.
- Unread counts and `last_read_message_id` behave correctly.
- Membership authorization.
- Mute suppresses pushes, and an @mention overrides mute.
- **DM block:**
  - no new DM in either direction and no sends into an existing DM;
  - it applies to admins;
  - team channels are unaffected.
- Own delete; admin delete.
- Report → it appears in the queue → resolve and dismiss.
- Pagination with `before_id` and `after_id`.
- Announcement recipients are snapshotted: joining or leaving a team later doesn't change the counts.

**Image validation** (Phase 0 module, used from Phase 1):
- Allowed types pass.
- Over 10 MB → 413.
- A non-image or corrupt file → 415.
- A decompression bomb is rejected.
- EXIF/GPS is gone after re-encoding, and the longest side is at most 2048 px.
- The stored filename is random, and the original name or path is never used.
- Media is not reachable without auth, or by a user who can't see the parent task, message or announcement.

### Mobile (Flutter)

- Widget tests for each new screen and sheet (Phases 0–2).
- **Layout:** no overflow at **320, 375 and 402 px** widths and at 1.3× text size, plus golden renders for visual checks.
- Badge counts: the Team tab badge and the app icon badge.
- **Push navigation:** tapping each new push type (`task`, `announcement`, `chat`) opens the right screen, both from a cold start and while running.
- Update-required gate (Phase 0), the "My tasks" tile (Phase 1), and the bottom-nav change (Phase 1).
- The **release build** compiles (`flutter build ios --release`, plus Android once Play Store work starts). That is necessary but never enough on its own.

### Website

- Team admin (Phase 0); task and template management and batch view (Phase 1); the moderation queue if built on the web (Phase 2).
- Permission handling: pages and actions are hidden, and 403s handled, for users without the feature.
- `vite build` production build passes.
- The **Playwright smoke test is committed in the repo** (Phase 0) and extended each phase: log in, open each Team page, create a task, and check permission redirects.

---

## 13. Open questions

1. **Teams:** start with *Everyone, Grocery, Kitchen, Managers*? Any others, such as *Office* or *Delivery*?
2. **Shift leads:** should `can_assign` exist so leads can assign tasks without being full admins?
3. **Team task completion:** "anyone on the team completes it once" (proposed), or "each member must complete it"?
4. **Chat retention:** is 12 months OK, or should it be longer?
5. **Phase 3 auto-tasks:** should they **replace** the existing push alerts for those events (proposed, so people get one signal, not two), or come in addition to them?
6. **WhatsApp:** keep the checklist WhatsApp group messages as they are, or move them into the Team channel once chat is adopted?
7. **Recurring tasks, `generate_ahead_minutes`:** is a default of **7 hours** OK (due Monday 3 PM → appears Monday 8 AM)? Some templates may want "the evening before".
8. **Recurring catch-up:** after downtime, occurrences due within the last **12 hours** are still created (marked overdue) and older ones are skipped. Is 12 hours the right cut-off?

---

## 14. Risks

| Risk | Mitigation |
|---|---|
| Staff keep using WhatsApp and the hub goes unused | Tasks with proof are the hook, and WhatsApp can't do them. Announcements require acknowledgment. Phase 3 auto-tasks put daily work there. |
| Apple or Play rejection over UGC | The §10.1 checklist (report, real DM block, moderation queue), review notes, and a demo account seeded for each phase |
| Rejection for hidden features | Features ship visible and working; flags are kill switches only (§10.2) |
| A shared store phone gets another person's pushes | The Phase 0 token lifecycle fixes and tests (§8, §12) |
| The change feed misses or duplicates updates | Change rows are written in the same transaction; tombstones for deletes; `resync_required` after the prune; tests on exact-after-cursor behavior |
| Recurring tasks fire at the wrong time around DST or after downtime | Rules evaluated in America/New_York; explicit DST and 12 h catch-up rules; idempotent generation; tests |
| Malicious or oversized uploads | Server-side validation and re-encoding (§9), independent of the client |
| The Mac mini is a single point of failure | Already true for the whole app. Polling, `client_uuid` and the change feed mean no lost messages across restarts. |
| SQLite write contention | Not an issue at about 10 users; the DB already runs in WAL mode. Revisit at 50+ concurrent users. |
| Notification fatigue | Per-alert toggles (existing), per-channel mute, the 60 s chat throttle, quiet hours |
| No backend test suite today | Phase 0 sets up pytest before any Team feature is built; every phase's exit requires its tests |

---

## 15. Revision log

### Rev 2: 2026-09-27 (after ChatGPT review)

| # | Review point | How it was addressed |
|---|---|---|
| 1 | `/updates` cursor couldn't reconstruct changes | Added a persisted `team_changes` feed, written in the same transaction; `/updates?since=` returns exactly the changes after the cursor, filtered by visibility, with tombstones (§5, §7) |
| 2 | Announcement audience must be snapshotted | `announcement_recipients` is written at post time and replaces `announcement_acks`; counts come only from it (§5) |
| 3 | Chat/API promises incomplete | Added the report list, resolve, dismiss, admin delete and DM block endpoints; **removed** ad-hoc group channels from v1 (§3, §6) |
| 4 | Shared phone push token handling | Unauthenticated unregister by token, `previous_token` on refresh, and tests for A→B login, expired logout and refresh (§8, §12) |
| 5 | Recurrence semantics | RRULE = when it's due, plus `generate_ahead_minutes`; America/New_York; DST and catch-up rules; stored in UTC (§5) |
| 6 | Phase 1 = tasks only | The phases were rewritten exactly as proposed (§11) |
| 7 | Don't change the nav in Phase 0 | The nav changes in Phase 1, when Tasks ships (§4, §11) |
| 8 | Server-side image validation | Type, size, decode/verify, bomb check, re-encode, random filename (§9) |
| 9 | Multi-person assignments lose their relationship | `assignment_batch_id` plus batch view and cancel (§5, §6) |
| 10 | "Hide conversation" isn't a block | A real `dm_blocks` table, enforced both ways on DM create and send (§5, §6, §10) |
| 11 | Flags aren't a way to ship hidden features | Flags are for kill switches, rollout and options only; builds ship features visible (§10.2) |
| 12 | Keep the simple architecture | Kept all of it: SQLite+WAL, FastAPI, existing auth, FCM + polling, no chat SDK, Firestore, Redis/Celery or WebSockets in v1, `client_uuid`, authenticated media, the audit trail, and both task assignment rules |
| 13 | Testing required | A full §12 testing plan by phase; "done" = tests pass plus a manual dev-phone check |

**Points we pushed back on or narrowed**

- **(a) Group channels:** removed ad-hoc group channels from v1 instead of adding endpoints to create and manage them. Teams already cover groups, and an admin who needs a new group creates a team.
- **(b) Change feed:** feed rows are *pointers* (entity type, ID, action) that the client refetches, not a history of full payloads. Rows are pruned after 30 days, and an older cursor gets `resync_required`. That keeps the table small and avoids storing message content twice.
- **(c) Push tokens:** most of the lifecycle already exists: register already moves a token to the newly logged-in user, and logout already deletes it. There are only two real gaps, logout with an expired session and token refresh, so the Phase 0 work is small.
- **(d) DM block:** it applies to DMs only. Team channels rely on report and moderation, because blocking inside a shared work channel would split the conversation.
- **(e) Recurring catch-up:** occurrences missed by more than 12 hours (e.g. a long outage) are skipped and logged, not backfilled, to avoid a flood of stale tasks.
- **(f) Hidden features:** we agree the v1 advice to "ship the build with Team hidden, then enable it" was wrong, and it has been removed.

