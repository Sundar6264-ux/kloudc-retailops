from doc_core import Raw, esc


def render(d, ctx):
    f = ctx["facts"]
    # ---------------------------------------------------------------- invoices
    d.h(2, "8.11", "Invoice scan -> submit -> WhatsApp + Toast email -> close")
    d.fig(
        r"""
sequenceDiagram
  autonumber
  actor S as Staff
  participant A as App Upload invoice
  participant API as POST /api/invoices
  participant DB as SQLite + uploads/invoices
  participant BG as 2 background threads
  participant WA as Whapi
  participant G as Gmail to Toast invoice inbox
  S->>A: scan pages (document scanner) or attach PDF / photo
  A->>A: review: rotate, crop, B and W, on-device PDF, ML Kit OCR of vendor + total
  A->>API: GET /api/invoices/check-duplicate (file hash)
  A->>API: multipart: file, vendor, type, total, notes
  API->>DB: save file + invoice_submissions row FIRST
  API-->>A: submission (sending...)
  API->>BG: send_invoice_notifications
  par WhatsApp
    BG->>WA: POST /messages/text with link PUBLIC_BASE_URL/public/invoices/token
  and Toast email
    BG->>BG: toast_pdf.build_toast_pdf (Toast-friendly page geometry)
    BG->>G: email the PDF (Toast drafts a bill from it)
  end
  BG->>DB: whatsapp_sent / toast_email_sent or *_error
  A->>API: GET /api/invoices/{id} until both settle
""",
        "Invoice submission. The demo login's submissions are saved and never sent.",
    )
    d.ul(
        [
            "<b>Failures</b>: the 10-minute <code>invoice_failed</code> alert goes to the submitter and admins; Resend (<code>POST /{id}/resend</code>) retries; admins can override a status. Ambiguous WhatsApp timeouts are reconciled against the chat history.",
            "<b>Closed in Toast</b>: <code>invoice_close_suggestions</code> spots the receiving in Toast and suggests the tap; a person marks it (<code>POST /{id}/mark-toast-closed</code>), which also closes any invoice auto task (source_sync 'invoice').",
            "<b>Delete</b>: request-delete -> admin approve-delete -> trash -> restore or permanent delete. Invoice links need a login.",
            "Website: Invoices page (list, statuses, Resend, close, delete workflow, trash). Overview pre-fills 'we received a delivery today' from today's invoice vendors.",
        ]
    )

    # ---------------------------------------------------------------- labels
    d.h(2, "8.12", "Labels")
    d.fig(
        r"""
sequenceDiagram
  autonumber
  actor S as Staff
  participant A as App Scan tab
  participant L as Labels app (labels.spicetown.shop)
  participant LDB as Labels DB + catalog
  participant DDB as Dashboard user_sessions (read-only)
  participant P as CUPS and Brother QL-810W
  S->>A: scan barcode (mobile_scanner)
  A->>L: GET /api/lookup/{upc} (Cookie session_token = app token)
  L->>DDB: validate session (read-only)
  L->>LDB: catalog row (Toast price sync)
  L-->>A: name, price, preview
  S->>A: Print label
  A->>L: POST /api/print
  L->>P: print job
  A->>L: GET /api/print/{job_id}, GET /api/print-history
""",
        "Label printing. The demo login is refused by the labels app. Nothing flows back into the dashboard database.",
    )
    d.p(
        "The labels catalog is a CSV built by a GitHub Action from Toast, pulled every 15 minutes "
        "(<code>com.spicetown.labels.sync</code>); price changes can auto-print. The website links to the labels app "
        "(Label Scanner in the footer); Home's 'Print labels' and Scan's History icon open Print Label History."
    )

    # ---------------------------------------------------------------- fix issues
    d.h(2, "8.13", "Fix Issues, leads and the daily lead tasks")
    d.fig(
        r"""
sequenceDiagram
  autonumber
  actor S as Staff (lead)
  participant C as W Fix Issues / A Fix Issues
  participant API as /api/issues
  participant FI as fix_issues + stock_issues
  participant DB as SQLite
  participant SS as source_sync
  participant IL as issue_leads.refresh_lead_progress
  C->>API: GET /summary, GET /{issue_type}?lead=me
  API->>FI: lists over the latest snapshot + forecasts (cache 10 min)
  FI->>DB: issue_resolutions (closed / more time), lead annotation by PO section or vendor
  API-->>C: open items grouped by PO section, counts, came back
  S->>C: close item with a reason (or 'more time')
  C->>API: POST /api/issues/close
  API->>DB: issue_resolutions + issue_resolution_events
  API->>SS: task_sources_changed('issue_leads')
  SS->>IL: done = morning items with a close newer than the morning list
  IL->>DB: tasks.progress_done / total, title 'Fix Issues: N left of M'
  alt 0 left
    IL->>DB: task done, note 'All M items fixed or closed'
  end
  Note over C: the lead's open Tasks page / app updates via the change feed
""",
        "Fix Issues close and live lead-task progress.",
    )
    d.fig(
        r"""
sequenceDiagram
  autonumber
  participant TICK as tasks_tick (every 5 min)
  participant IL as issue_leads.run_issue_lead_rule
  participant FI as fix_issues lists
  participant DB as SQLite
  participant BG as push sweep
  TICK->>IL: after the set time (default 10:00), once per day
  IL->>DB: issue_leads (section / vendor / fallback owners)
  IL->>FI: today's open items per lead (latest snapshot + resolutions)
  loop each lead with items
    IL->>DB: tasks (system user, source_type issue_leads, due in N hours, progress M)
    IL->>DB: issue_lead_task_runs (day, user, task, items_json = the morning list)
    IL->>DB: team_changes (lead's lists refresh)
  end
  TICK->>BG: task_assigned 'New task' to each lead (reminders opt-in only)
""",
        "Daily Fix Issues lead tasks (one per lead per day, idempotent per day).",
    )
    d.ul(
        [
            "<b>Lists</b>: missing barcodes, invalid barcodes (GS1 check digit), duplicates, dead stock (with / without stock), slow review, negative inventory, refill from container. 'More time' snoozes for a week; at 09:00 the ended snoozes come back (a Morning summary line, opt-in push).",
            "<b>Leads</b> (admin page <code>/api/issue-leads</code>): who leads each PO section, vendor, and the fallback. Settings: daily time (default 10:00), hours until due (default 6, per lead or per type).",
            "<b>Daily lead task</b>: inside the 5-minute task job, after the set time, <code>run_issue_lead_rule</code> creates one task per lead with that morning's list (<code>issue_lead_task_runs.items_json</code> = M) and sends 'New task' only (its due / overdue reminders are governed by the opt-in <code>fix_issues_task_due</code>). The task's <code>action</code> opens that lead's Fix Issues list.",
            "Items that drop off by themselves (fixed in Toast + new upload, no Fix Issues close) count at the next morning's run.",
        ]
    )

    # ---------------------------------------------------------------- checklists
    d.h(2, "8.14", "Daily checklists, public links and WhatsApp groups")
    d.fig(
        r"""
sequenceDiagram
  autonumber
  actor S as Staff or part-time (public link)
  participant C as A form / W form / public page /c/slug-code
  participant API as /api/checklists or /api/public/checklists/token
  participant CS as checklist_submission
  participant DB as SQLite
  participant WA as Whapi
  participant SS as source_sync
  C->>API: GET schemas (definition current version)
  S->>C: answers + photos
  C->>API: POST media (team_media, re-encoded)
  C->>API: POST submit {answers, media keys, name}
  API->>CS: validate against the version, flag issues, dedupe by answers_hash
  CS->>DB: checklist_submissions (via_public_link, public_name)
  API-->>C: saved (all clear or issues)
  API->>WA: queue_whatsapp -> group from settings.whatsapp_group (BackgroundTasks)
  API->>SS: task_sources_changed('checklist')
  Note over API: 21:30 closing_checklist push if a closing checklist is missing
""",
        "Checklist submission (app, website or public link).",
    )
    d.ul(
        [
            "<b>Builder</b> (admins, website and app): Google-Forms-like editor; draft (PUT), publish creates an immutable version, revert, duplicate, order. Old app build 5 still gets LEGACY blocks it can render.",
            "<b>Public link</b>: admin turns it on per checklist (<code>PUT .../public-link</code>), rotates the code, sets allowed names and the hourly cap. Guards in section 3.6.",
            "<b>WhatsApp groups</b> (admins): register Whapi groups by key (<code>/api/checklists/admin/whatsapp-groups</code>), list available groups (Whapi GET /groups, cached 5 min), mark marketing groups for flyers.",
            "<b>Closing-checklist tasks are no longer created</b> (Oct 15: TEAM_AUTOTASK_CHECKLIST_ENABLED default false); leftovers are closed by the task job as cancelled 'Missed - not submitted' (never tasks 11, 12, 47 or the Review team's).",
        ]
    )

    # ---------------------------------------------------------------- tasks
    d.h(2, "8.15", "Team tasks: create, assign, sync, complete")
    d.fig(
        r"""
sequenceDiagram
  autonumber
  actor A as Assigner (web or app)
  participant API as /api/team/tasks
  participant TS as tasks_service
  participant DB as SQLite
  participant BG as after_response (BackgroundTasks)
  participant FCM as Firebase
  actor B as Assignee app
  A->>API: POST {title, steps, person or team, due, requires, priority}
  API->>TS: create (team with 0 members = 400)
  TS->>DB: tasks (one per person, batch id), task_items, task_events, team_changes (one commit)
  DB-->>API: after_commit wakes long-polls
  API-->>A: tasks[] (task_out with can, action, progress)
  API->>BG: task_assigned push (deliverable users only)
  BG->>FCM: send
  B->>API: long-poll GET /api/team/updates?since&wait=20&include=tasks
  API-->>B: changes.task + tasks[] (new task)
  B->>API: POST /tasks/{id}/complete (note / photo proof)
  API->>TS: UPDATE ... WHERE status='open' (409 + task if already done)
  TS->>DB: done, task_events, team_changes
  API->>BG: task_done push to the creator (opt-in, off by default)
  Note over A: the assigner's open screen updates from the feed in about 0.2 s
""",
        "A task from creation to completion.",
    )
    d.table(
        ["Task write", "Route", "Notes"],
        [
            ["Edit", "PATCH /api/team/tasks/{id}", "Only changed fields + optional expected_updated_at; mismatch = 409 with the current task"],
            ["Steps", "PATCH .../items/{item_id} (tick / rename), POST .../items, PUT .../items (whole list), DELETE .../items/{item_id}", "Max 30 steps; creator / admin edit, assignees tick; events item_done, item_undone, items_edited"],
            ["Can't do / start / reopen / comment", "POST .../cant-do, /start, /reopen, /comments", "can't do pushes the creator and admins (on by default)"],
            ["Report", "POST .../report, .../comments/{id}/report", "Into the moderation queue"],
            ["Bulk (admins)", "POST /api/team/tasks/bulk {task_ids, action done or cancel}", "Max 200; skips photo-required and closed tasks"],
            ["Batch cancel", "POST /api/team/task-batches/{id}/cancel", "Cancel every copy of a multi-person assignment"],
            ["Summary", "GET /api/team/task-summary", "'My team today': admins everyone, can_assign their teams; demo 403"],
            ["Filters", "GET /api/team/tasks?assignee_user_id&source&q&status", "Narrow the view, never widen it"],
        ],
        widths=["18%", "44%", "38%"],
        cls="compact small",
    )
    d.ul(
        [
            "<b>Who sees a task</b>: the assignee (or every current member of the assigned team), the creator, admins. A team task is one row any member can complete (first one wins); several named people = one row each.",
            "<b>Badges</b>: <code>tasks_due</code> = open tasks for me or my teams due by the end of today (overdue included), returned with every change-feed answer.",
            "<b>Pushes</b>: task_assigned (on), task_cant_do (on), task_due overdue for your own tasks (on), 'Due in 30 minutes' (task_due_soon, off), lead task reminders (fix_issues_task_due, off), task_done and task_comment (off). The 5-minute sweep only targets people who can receive it, and logs a no-recipient push once so it is never rebuilt.",
        ]
    )

    d.h(2, "8.16", "Recurring schedules (website and app)")
    d.fig(
        r"""
sequenceDiagram
  autonumber
  actor A as Admin
  participant C as W Recurring tasks / A Schedules
  participant API as /api/team/templates
  participant DB as SQLite
  participant TICK as tasks_tick (every 5 min)
  participant REC as recurrence.generate_occurrences
  A->>C: title, person or team, Every day / Weekdays / Some days / Monthly + time, steps
  C->>API: POST /templates/preview (next dates, read-only)
  C->>API: POST /templates (or PATCH {active} to pause / resume)
  API->>DB: task_templates (RRULE in America/New_York, generate_ahead_minutes)
  API-->>C: template with schedule_text 'Weekdays at 7:30 AM'
  TICK->>REC: occurrences whose show-up time has come
  REC->>DB: tasks (template_id, due_at) + team_changes, skip empty teams
  REC-->>TICK: task_assigned pushes via the sweep
  A->>C: Create now
  C->>API: POST /templates/{id}/create-task
""",
        "Recurring schedules. Weekday assignees and 'shows up N hours before' are edited on the website; the app shows them.",
    )
    d.p(
        "DST: a local time that does not exist (spring forward) moves to the next valid time; a repeated hour is used once. "
        "Deleting a template deactivates it (history keeps its tasks)."
    )

    d.h(2, "8.17", "Automatic tasks and source sync")
    d.table(
        ["Rule (TEAM_AUTOTASK_*)", "Default", "Creates", "Closed by"],
        [
            ["checklist", "OFF since Oct 15", "(none) - leftovers closed as 'Missed - not submitted'", "checklist submit (source_sync 'checklist')"],
            ["invoice", "off", "'Resend / fix invoice' when sends failed", "mark-toast-closed / approve-delete / delete ('invoice')"],
            ["order", "off", "'Place the order' for an order-due vendor with >= 5 items", "order actions, mark-placed, Critical skip_week ('order'); expires after 7 days"],
            ["issue_leads", "on (leads configured)", "One Fix Issues task per lead per day", "Fix Issues close / reopen ('issue_leads') -> progress, closes at 0"],
        ],
        widths=["20%", "16%", "34%", "30%"],
        cls="compact",
    )
    d.p(
        "<code>team/source_sync.task_sources_changed(kind)</code> runs the cheap 'close' half of a rule right after the "
        "source's own commit, in its own short session, and never raises; the 5-minute job stays the safety net. "
        "Every system close is a conditional <code>UPDATE ... WHERE status='open'</code>, so it can never overwrite a "
        "person's done or can't do. Protected: tasks 11, 12, 47 and anything of a demo login or its teams."
    )

    # ---------------------------------------------------------------- chat
    d.h(2, "8.18", "Chat")
    d.fig(
        r"""
sequenceDiagram
  autonumber
  actor S as Sender
  participant C as W Chat / A Conversation
  participant API as /api/team
  participant CS as chat_service
  participant DB as SQLite
  participant BG as Pushes (BackgroundTasks to FCM)
  actor R as Members
  S->>C: text, photo, voice (m4a) or PDF
  C->>API: POST /api/team/media (validate, re-encode, store) if any
  C->>API: POST /channels/{id}/messages {body, media_id, client_uuid}
  API->>CS: membership check (team members with team_chat, or DM pair, not blocked)
  CS->>CS: content filter masks words, parse @mentions
  CS->>DB: messages + team_changes
  API-->>C: message
  API->>BG: chat_message / chat_mention pushes in their own session, max 1 per minute per member, prefs + hide previews
  R->>API: change feed (5 s on an open chat) -> GET messages, POST /read
""",
        "Chat message.",
    )
    d.p(
        "Channels: one per team (membership derived live from team_members) plus DMs (<code>POST /channels/dm</code>, dm_key). "
        "Also: pins, seen-by, search, mute / hide per channel, DM blocks, delete own message (soft; text kept 30 days "
        "for moderation), chat settings 'hide message text' in pushes. Retention: 365 days. The demo login only sees "
        "people sharing a team with it."
    )

    d.h(2, "8.19", "Announcements (News)")
    d.fig(
        r"""
sequenceDiagram
  autonumber
  actor A as Admin
  participant API as /api/team/announcements
  participant AS as announcements_service
  participant DB as SQLite
  participant J as pending pushes job (5 min)
  actor R as Recipients
  A->>API: POST {title, body, media, teams, require_ack, pinned}
  API->>AS: snapshot recipients = members of those teams now (Everyone = all real users)
  AS->>DB: announcements, announcement_audience, announcement_recipients, team_changes
  alt quiet hours
    AS->>DB: pending_push = true
    J->>R: announcement push at 07:00
  else
    API->>R: announcement push (BackgroundTasks)
  end
  R->>API: POST /{id}/seen, POST /{id}/ack
  A->>API: GET /{id}/recipients (7 of 9 acknowledged), POST /{id}/remind
""",
        "Announcements. A push whose every target turned announcements off is not kept queued (Oct 15 fix).",
    )

    d.h(2, "8.20", "Moderation")
    d.fig(
        r"""
sequenceDiagram
  autonumber
  actor S as Staff
  participant API as /api/team
  participant MOD as moderation
  participant DB as SQLite
  actor A as Admin
  S->>API: POST /messages/{id}/report (or /tasks/{id}/report, /comments/{id}/report) {reason, note}
  API->>MOD: snapshot the reported content
  MOD->>DB: message_reports (open)
  MOD->>A: chat_report push to admins (always, not a switch)
  A->>API: GET /api/team/reports (website Setup, app More Setup)
  alt remove
    A->>API: POST /reports/{id}/resolve {delete_message: true, note}
    API->>DB: message soft-deleted, report resolved, team_changes
  else keep
    A->>API: POST /reports/{id}/dismiss
  end
""",
        "Reporting and moderation.",
    )
    d.p(
        "Anyone in a channel can report a message; anyone who can see a task can report it or a comment "
        "(<code>message_reports</code> with a snapshot). Admins see one queue (<code>GET /api/team/reports</code>, "
        "website Setup &rsaquo; Reported messages, app More &rsaquo; Setup) and resolve (optionally removing the content) "
        "or dismiss. The content filter masks objectionable words in chat, task text, comments and announcements "
        "(first letter kept), never rejects them. Team rules (EULA) must be accepted once before Team opens "
        "(<code>POST /api/team/rules/accept</code>, versioned)."
    )

    # ---------------------------------------------------------------- notifications
    d.h(2, "8.21", "Notifications: catalog, defaults, dispatch and quiet hours")
    groups = {g["key"]: g["label"] for g in f["groups"]}
    rows = []
    for a in f["alerts"]:
        dfl = a.get("defaults") or {}
        staff = dfl.get("staff")
        admin = dfl.get("admin")
        rows.append(
            [
                Raw(f"<code>{esc(a['key'])}</code>"),
                a.get("label", ""),
                groups.get(a.get("group"), a.get("group", "")),
                "-" if a.get("admin_only") else ("on" if staff else "off"),
                "on" if admin else "off",
                "yes" if a.get("essential") else "",
                ", ".join(a.get("features") or []) or ("admin" if a.get("admin_only") else "-"),
                a.get("route", ""),
            ]
        )
    d.p(
        f"The catalog (<code>services/notifications.py</code> ALERTS, generated below) has <b>{len(f['alerts'])}</b> "
        "alerts in 4 groups. <b>Effective value</b> for a person = their own explicit choice (notification_prefs row); "
        "if they made none, the admin's store default for their role; if there is none, the catalog default for their role. Production had 0 pref rows, "
        "so the 'Recommended' defaults apply to everyone at deploy; an explicit choice always wins later."
    )
    d.table(["Key", "Label", "Group", "Staff", "Admin", "Essential", "Needs", "Opens"], rows, widths=["17%", "20%", "11%", "6%", "6%", "8%", "17%", "15%"], cls="compact small")
    d.fig(
        r"""
sequenceDiagram
  autonumber
  participant J as Trigger (job or a write)
  participant RA as run_alert_job
  participant D as dispatch_alert
  participant DB as SQLite
  participant F as push.send_to_tokens (FCM)
  J->>RA: key (e.g. closing_checklist)
  RA->>RA: quiet hours 22:00-07:00? then stop
  RA->>J: trigger function -> Alert(key, dedupe_keys, title, body, data, user_ids)
  RA->>D: each alert
  D->>DB: dedupe keys already in notification_log? then stop
  D->>DB: device_tokens -> users with access to the alert's features
  D->>D: effective on (own choice, store default, catalog default)
  D->>D: user_ids filter, or drop demo logins for store-wide alerts
  D->>F: send_each (APNs sound + thread-id)
  F-->>D: delivered, invalid tokens
  D->>DB: delete invalid tokens, notification_log per dedupe key, push_deliveries per user
""",
        "One alert from trigger to phones. Firebase not configured = dry run (logged, never marked sent).",
    )
    d.h(3, "8.21.1", "The 09:00 Morning summary")
    d.fig(
        r"""
sequenceDiagram
  autonumber
  participant J as notify_morning_summary 09:00
  participant OO as open_orders refresh
  participant T as notification_triggers.run_morning_job
  participant D as dispatch_alert
  participant DB as SQLite
  participant F as FCM
  J->>OO: refresh today's open checks (one Toast call)
  J->>T: include_fix_issues = SCHEDULER_ENABLED
  loop MORNING_SOURCES in order
    T->>T: critical_vendors, order_due, loss_watch, fix_issues_reopened, inventory_upload_due, inventory_api_ready, daily_sales_summary, open_orders_morning
    T->>D: each alert alone, to whoever turned that key on (all off by default)
  end
  T->>DB: keep only alerts whose dedupe keys are not yet logged
  loop users with morning_summary on, a device, not demo
    T->>T: one line per source they can access, are targeted by, and don't get separately
    T->>F: ONE push 'Morning summary' (dedupe morning_summary:date:user)
  end
  T->>DB: log the summarised alerts' dedupe keys, prune push_deliveries older than 180 days
""",
        "Morning summary: about 6 separate 9 AM pushes become one (prod copy: admins 1 push each, staff none).",
    )
    d.ul(
        [
            "<b>Settings API</b>: <code>GET/PUT /api/notifications/prefs</code> (items grouped, with default, essential, admin_only, explicit), <code>POST /prefs/reset</code> (own catalog rows only), <code>GET /recent</code>; admins: <code>GET/PUT /defaults</code>, <code>POST /defaults/reset</code>, <code>GET /volume?days=14</code>. Old app builds keep reading items[].key / label / description / enabled.",
            "<b>Screens</b>: app More &rsaquo; Notifications (grouped switches, essential confirm, Reset, Store defaults for admins); website <code>/#/notifications</code> (same model, plus recent pushes and per-person volume).",
            "<b>Demo</b>: may flip its own switches (PUT allowlisted) but never reset or touch store defaults; never gets a summary or store-wide alerts.",
        ]
    )
