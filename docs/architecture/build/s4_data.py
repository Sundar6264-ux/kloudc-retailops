from doc_core import Raw, esc

PURPOSE = {
    "orders": "One row per Toast selection (line item or modifier) per business date; upserted by toast_selection_guid by the sales sync.",
    "open_order_cache": "Toast orders still open: today's (refreshed every few minutes) and every older date (04:15 full scan).",
    "job_runs": "Every background job run (sync, scans, snapshots, Insights, lead tasks...) with status and JSON detail; the job status widget reads it.",
    "inventory_snapshots": "Daily inventory per item (CSV/XLSX upload or Toast API build), one row per item per snapshot_date. The biggest table.",
    "toast_menu_items": "Toast menu metadata per item (sku = barcode, plu = supplier text, price) from the hourly menu re-check.",
    "category_overrides": "App-side category corrections applied on read (Toast API is read-only).",
    "po_section_rules": "Editable PO section rules (item / vendor / rename) for the PO PDF headings.",
    "vendors_reference": "Scaffold for Toast Purchasing data; empty (scope not granted).",
    "setting_changes": "Admin-changed settings with history: the 'Use Toast for inventory' switch and (Oct 15) notification store defaults.",
    "po_cart_items": "The store's one shared PO cart: lines per vendor, qty, case size, PO section, removal requests.",
    "placed_orders": "Orders sent or recorded: status pdf / placed, source cart / outside / sent, items snapshot, for_delivery_date.",
    "vendor_schedules": "One manual schedule per vendor: delivery weekdays + lead days; the Vendor Calendar derives everything else.",
    "vendor_order_skips": "'Skip this order' for one vendor delivery date (calendar / Today actions).",
    "critical_vendor_dismissals": "Critical tile: Close (handled) / Skip this week / Remind me later, with expiry and who cleared it.",
    "vendor_contacts": "Who receives a vendor's PO (email, WhatsApp, CC list); admin managed.",
    "po_sends": "Log of every Send PO attempt with idempotency key, email/WhatsApp status and the resulting placed order.",
    "suggestion_dismissals": "Dismissed suggestions (deliveries, invoice closes, schedules) so they stop showing.",
    "purchase_log": "Confirmed deliveries / purchases per item with quantity and captured unit cost (Delivery Review, Reconciliation).",
    "location_transfers": "Store <-> Container stock moves (retired UI, kept for old app builds).",
    "invoice_submissions": "Every invoice / return / transfer upload: file on disk, WhatsApp + Toast email status, closed-in-Toast, delete workflow.",
    "issue_resolutions": "Current close / snooze state per Fix Issues item (issue_type + issue_key).",
    "issue_resolution_events": "History of every close, reopen, snooze and came-back event.",
    "issue_leads": "Who leads each PO section / vendor / fallback for Fix Issues.",
    "issue_lead_task_runs": "The morning list per lead per day (items_json) and the task it created; basis of 'N left of M'.",
    "checklist_definitions": "Checklist builder: one row per checklist with draft and current version number.",
    "checklist_definition_versions": "Immutable published versions of each checklist schema.",
    "checklist_submissions": "Submitted checklists with answers, flags, WhatsApp status, public-link name.",
    "checklist_public_links": "Public link per checklist: slug, code/token, enabled, allowed names, hourly cap.",
    "whatsapp_groups": "WhatsApp groups (Whapi chat ids) a checklist or a flyer can be sent to; marketing flag.",
    "flyers": "Marketing flyer library (file in ~/Spice Town Flyers/<Category>/, caption, soft delete).",
    "flyer_sends": "One row per flyer per WhatsApp group per send batch, with status, attempts and ambiguity.",
    "analytics_cache": "One Toast Analytics answer per report per business date (or an 'unavailable' marker).",
    "analytics_call_log": "Every ERA call (family, date, ok) - the rolling 24 h budgets are counted from it.",
    "guest_visits": "Returning customers: HMAC of the card fingerprint per visit date (no card data kept).",
    "loss_watch_flags": "Flagged checks (discount / void / refund / odd) for admins, with acknowledgement.",
    "users": "Logins: bcrypt hash, is_admin, allowed_features JSON, can_assign, hide_sales.",
    "user_sessions": "Opaque session tokens with expiry (website cookie and app bearer token alike).",
    "device_tokens": "FCM device tokens per user and platform.",
    "notification_prefs": "A user's explicit on/off per alert key (no row = the default for their role).",
    "notification_log": "Dedupe log: one row per dedupe key after a real send (never sent twice).",
    "push_deliveries": "Oct 15: one row per user reached per push (key + title only); 'Recent notifications', volume; 180-day prune.",
    "teams": "Teams (Everyone is implicit: is_everyone); colour; archive.",
    "team_members": "Team membership.",
    "team_changes": "The Team change feed (seq = cursor); pruned after 30 days.",
    "team_media": "Validated, re-encoded uploads (images, voice, PDFs) for tasks, chat, announcements, checklists.",
    "team_rules_acceptances": "Who accepted which version of the Team rules (EULA).",
    "tasks": "Team tasks: one person or one team, due, status, completion, source link, (Oct 15) progress.",
    "task_items": "Steps (sub-items) of a task with who ticked them.",
    "task_events": "Task history (created, edited, done, can't do, comments, step ticks, bulk...).",
    "task_templates": "Recurring schedules: RRULE, timezone, generate-ahead, steps, weekday assignees.",
    "announcements": "Admin posts (optionally require acknowledgement, pinned, reminders).",
    "announcement_audience": "Which teams an announcement went to.",
    "announcement_recipients": "Snapshot of recipients with seen / acked / reminded times.",
    "channels": "Chat channels: team channels and DMs.",
    "channel_members": "Per user per channel: last read, mute, hide, last push.",
    "messages": "Chat messages (text, media, mentions, pins, soft delete, filter flag).",
    "message_reports": "Moderation reports on messages, tasks and comments.",
    "dm_blocks": "Who blocked whom in direct messages.",
}

DOMAINS = [
    ("4.1", "Sales and Toast ingest", ["orders", "open_order_cache", "job_runs"], r"""
erDiagram
  orders {
    int id PK
    text toast_selection_guid "upsert key"
    text parent_selection_guid "modifiers"
    text toast_order_guid
    text toast_check_guid
    date business_date
    datetime opened_at
    datetime paid_at
    text item_guid "= item_id"
    real quantity
    real net_price
    bool voided
  }
  open_order_cache {
    int id PK
    text toast_order_guid
    date business_date
    text server_name
    real total_amount
    json line_items_json
    datetime scanned_at
  }
  job_runs {
    int id PK
    text job_name
    text status
    date business_date
    json detail
    datetime started_at
  }
  orders }o..|| job_runs : "synced by toast_sales_sync run for business_date"
  open_order_cache }o..|| job_runs : "open_orders_scan"
"""),
    ("4.2", "Inventory and categories", ["inventory_snapshots", "toast_menu_items", "category_overrides", "po_section_rules", "vendors_reference"], r"""
erDiagram
  inventory_snapshots {
    int id PK
    date snapshot_date
    text item_id
    text name
    text category_group
    text category
    text supplier "can list several"
    text barcode "can list several"
    text supplier_item_id
    real inventory_quantity
    real cost
    real price
    real last_30_day_orders
    text source_filename "CSV or API"
    bool api_out_of_stock
  }
  toast_menu_items {
    text item_id PK
    text sku "barcode"
    text plu "suppliers"
    real price
  }
  category_overrides {
    int id PK
    text match_type
    text match_value
    text category
    bool active
  }
  po_section_rules {
    int id PK
    text rule_type
    text match_value
    text section
  }
  orders ||..o{ inventory_snapshots : "item_guid = item_id"
  inventory_snapshots ||..o| toast_menu_items : "item_id"
  category_overrides ||..o{ inventory_snapshots : "applied on read"
  po_section_rules ||..o{ inventory_snapshots : "PO section on PDF"
"""),
    ("4.3", "Ordering and purchase orders", ["po_cart_items", "placed_orders", "vendor_contacts", "po_sends"], r"""
erDiagram
  po_cart_items {
    int id PK
    text supplier
    text item_id
    real qty
    real case_of
    text section
    int added_by_user_id
    datetime removal_requested_at
  }
  placed_orders {
    int id PK
    text supplier
    text status "pdf, placed"
    text source
    json items_json
    date for_delivery_date
    datetime placed_at
  }
  vendor_contacts {
    int id PK
    text vendor_key
    text email
    text whatsapp
    json cc_json
    bool send_po
  }
  po_sends {
    int id PK
    text idempotency_key
    text supplier
    text status
    text email_status
    json email_cc_json
    text whatsapp_status
    int placed_order_id
  }
  inventory_snapshots ||..o{ po_cart_items : "item_id"
  po_cart_items }o..|| placed_orders : "Mark placed / Send PO snapshots and clears"
  vendor_contacts ||..o{ po_sends : "recipients"
  po_sends |o..o| placed_orders : "placed_order_id"
  users ||..o{ po_cart_items : "added_by_user_id"
"""),
    ("4.4", "Vendors, calendar and today's order actions", ["vendor_schedules", "vendor_order_skips", "critical_vendor_dismissals", "suggestion_dismissals"], r"""
erDiagram
  vendor_schedules {
    int id PK
    text vendor
    json delivery_weekdays
    int lead_days
    bool active
  }
  vendor_order_skips {
    int id PK
    text vendor
    date delivery_date
    text reason
    text skipped_by_name
  }
  critical_vendor_dismissals {
    int id PK
    text vendor
    text kind
    datetime until
    json critical_item_ids_json
    datetime cleared_at
  }
  suggestion_dismissals {
    int id PK
    text kind
    text suggestion_key
  }
  placed_orders {
    int id PK
    text supplier
    date for_delivery_date
  }
  vendor_schedules ||..o{ vendor_order_skips : "vendor + delivery date"
  vendor_schedules ||..o{ placed_orders : "ordered for a delivery"
  critical_vendor_dismissals }o..|| vendor_schedules : "vendor"
"""),
    ("4.5", "Receiving and invoices", ["purchase_log", "location_transfers", "invoice_submissions"], r"""
erDiagram
  invoice_submissions {
    int id PK
    text vendor
    text submission_type
    real total_amount
    text stored_filename
    text file_token
    text file_hash "dup check"
    bool whatsapp_sent
    bool toast_email_sent
    datetime toast_closed_at
    datetime delete_requested_at
    datetime deleted_at
    int submitted_by_user_id
  }
  purchase_log {
    int id PK
    text item_id
    text supplier
    real quantity_received
    real unit_cost
    date received_date
  }
  location_transfers {
    int id PK
    text item_id
    text direction
    real quantity
    date transfer_date
  }
  inventory_snapshots ||..o{ purchase_log : "item_id"
  inventory_snapshots ||..o{ location_transfers : "item_id"
  invoice_submissions }o..o{ purchase_log : "invoice gaps by vendor and date"
"""),
    ("4.6", "Fix Issues and leads", ["issue_resolutions", "issue_resolution_events", "issue_leads", "issue_lead_task_runs"], r"""
erDiagram
  issue_resolutions {
    int id PK
    text issue_type
    text issue_key
    text item_id
    text reason
    datetime closed_at
    datetime snooze_until
  }
  issue_resolution_events {
    int id PK
    text issue_type
    text issue_key
    text event "close, reopen..."
    text by_name
    datetime at
  }
  issue_leads {
    int id PK
    text kind "section, vendor, fallback"
    text match_key
    int user_id "null = no lead"
  }
  issue_lead_task_runs {
    int id PK
    date day
    int user_id
    int task_id
    json items_json "morning list"
    datetime updated_at
  }
  tasks {
    int id PK
    text source_type "issue_leads"
    int progress_total
    int progress_done
  }
  issue_resolutions ||..o{ issue_resolution_events : "issue_type + issue_key"
  issue_leads ||..o{ issue_lead_task_runs : "user_id per day"
  issue_lead_task_runs ||--|| tasks : "task_id"
  issue_resolutions }o..o{ issue_lead_task_runs : "closes counted after updated_at"
"""),
    ("4.7", "Daily checklists", ["checklist_definitions", "checklist_definition_versions", "checklist_submissions", "checklist_public_links", "whatsapp_groups"], r"""
erDiagram
  checklist_definitions {
    int id PK
    text checklist_type
    text title
    json settings_json "group, due time"
    int current_version
    json draft_json
  }
  checklist_definition_versions {
    int id PK
    int definition_id FK
    int version
    json schema_json
  }
  checklist_submissions {
    int id PK
    text checklist_type
    int definition_version
    json answers_json
    bool all_clear
    text answers_hash
    bool whatsapp_sent
    bool via_public_link
    text public_name
  }
  checklist_public_links {
    int id PK
    text checklist_type
    text slug
    text token
    bool enabled
    int max_per_hour
  }
  whatsapp_groups {
    int id PK
    text key
    text chat_id
    bool marketing
    bool active
  }
  checklist_definitions ||--o{ checklist_definition_versions : "versions"
  checklist_definitions ||..o{ checklist_submissions : "checklist_type"
  checklist_definitions ||..o| checklist_public_links : "checklist_type"
  checklist_definitions }o..o| whatsapp_groups : "settings.whatsapp_group"
  team_media ||..o{ checklist_submissions : "photos"
"""),
    ("4.8", "Team: teams, change feed, tasks", ["teams", "team_members", "team_changes", "team_media", "team_rules_acceptances", "tasks", "task_items", "task_events", "task_templates"], r"""
erDiagram
  users ||--o{ team_members : "member"
  teams ||--o{ team_members : "has"
  teams {
    int id PK
    text name
    bool is_everyone
    datetime archived_at
  }
  tasks {
    int id PK
    int template_id FK
    text assignment_batch_id
    int assignee_user_id FK
    int assignee_team_id FK
    datetime due_at
    text status
    text requires
    text source_type
    text source_id
    int progress_total
    int progress_done
    datetime updated_at "409 check"
  }
  task_items {
    int id PK
    int task_id FK
    int position
    text text
    bool done
  }
  task_events {
    int id PK
    int task_id FK
    text kind
    json detail_json
  }
  task_templates {
    int id PK
    text rrule
    text timezone
    int generate_ahead_minutes
    json items_json
    json day_assignees_json
    bool active
  }
  team_changes {
    int seq PK "cursor"
    text entity_type
    int entity_id
    text action
    int team_id
    int user_id
    int channel_id
  }
  team_media {
    int id PK
    text token
    text kind
    text owner_type
    int owner_id
  }
  users ||--o{ tasks : "assignee"
  teams ||--o{ tasks : "assignee team"
  task_templates ||--o{ tasks : "generates"
  tasks ||--o{ task_items : "steps"
  tasks ||--o{ task_events : "history"
  tasks ||..o{ team_changes : "every write"
  team_media ||--o| tasks : "completion photo"
"""),
    ("4.9", "Team: chat, announcements, moderation", ["channels", "channel_members", "messages", "message_reports", "dm_blocks", "announcements", "announcement_audience", "announcement_recipients"], r"""
erDiagram
  channels {
    int id PK
    text kind "team, dm"
    int team_id FK
    text dm_key
  }
  channel_members {
    int channel_id PK
    int user_id PK
    int last_read_message_id
    bool muted
    bool hidden
    datetime last_pushed_at
  }
  messages {
    int id PK
    int channel_id FK
    int sender_user_id
    text body
    int media_id FK
    json mentions_json
    datetime pinned_at
    datetime deleted_at
  }
  message_reports {
    int id PK
    int message_id FK
    text target_type
    text status
  }
  dm_blocks {
    int blocker_user_id PK
    int blocked_user_id PK
  }
  announcements {
    int id PK
    text title
    bool require_ack
    bool pinned
    bool pending_push
  }
  announcement_audience {
    int announcement_id PK
    int team_id PK
  }
  announcement_recipients {
    int announcement_id PK
    int user_id PK
    datetime seen_at
    datetime acked_at
  }
  teams ||--o{ channels : "team channel"
  channels ||--o{ channel_members : "read state"
  channels ||--o{ messages : "has"
  messages ||--o{ message_reports : "reported"
  announcements ||--o{ announcement_audience : "to teams"
  announcements ||--o{ announcement_recipients : "snapshot"
"""),
    ("4.10", "Users, sessions and notifications", ["users", "user_sessions", "device_tokens", "notification_prefs", "notification_log", "push_deliveries", "setting_changes"], r"""
erDiagram
  users {
    int id PK
    text username
    text password_hash
    bool is_admin
    json allowed_features
    bool can_assign
    bool hide_sales
  }
  user_sessions {
    text token PK
    int user_id
    datetime expires_at
  }
  device_tokens {
    int id PK
    int user_id FK
    text token
    text platform
    datetime last_seen_at
  }
  notification_prefs {
    int id PK
    int user_id
    text key
    bool enabled
  }
  notification_log {
    int id PK
    text key
    text dedupe_key
    int recipient_count
    datetime sent_at
  }
  push_deliveries {
    int id PK
    int user_id
    text key
    text title
    datetime sent_at
  }
  setting_changes {
    int id PK
    text key
    json value
  }
  users ||..o{ user_sessions : "logins"
  users ||--o{ device_tokens : "phones"
  users ||..o{ notification_prefs : "own choices"
  users ||..o{ push_deliveries : "reached"
  notification_log ||..o{ push_deliveries : "same send"
  setting_changes ||..o{ notification_prefs : "store default below own choice"
"""),
    ("4.11", "Insights (Toast Analytics)", ["analytics_cache", "analytics_call_log", "guest_visits", "loss_watch_flags"], r"""
erDiagram
  analytics_cache {
    int id PK
    text report
    text params_hash
    date business_date
    json payload_json
    datetime fetched_at
  }
  analytics_call_log {
    int id PK
    text family
    date business_date
    bool ok
    datetime called_at
  }
  guest_visits {
    int id PK
    date visit_date
    text card_hash "HMAC"
    int payments
  }
  loss_watch_flags {
    int id PK
    text flag_key
    text kind
    date business_date
    text employee
    real amount
    datetime acked_at
  }
  analytics_call_log }o..|| analytics_cache : "budget per family per 24 h"
  analytics_cache ||..o{ loss_watch_flags : "check report"
  analytics_cache ||..o{ guest_visits : "guest report"
"""),
    ("4.12", "Marketing flyers", ["flyers", "flyer_sends", "whatsapp_groups"], r"""
erDiagram
  flyers {
    int id PK
    text title
    text category
    text caption
    text file_rel_path
    text sha256
    datetime deleted_at
  }
  flyer_sends {
    int id PK
    int flyer_id
    text batch_id
    text target_key
    text status
    text idempotency_key
    int attempts
    bool ambiguous
  }
  whatsapp_groups {
    int id PK
    text key
    text chat_id
    bool marketing
  }
  flyers ||..o{ flyer_sends : "send batches"
  whatsapp_groups ||..o{ flyer_sends : "target"
"""),
]


def render(d, ctx):
    f = ctx["facts"]
    rc = ctx["rowcounts"]
    d.h(1, "4", "Data model")
    d.p(
        f"One SQLite database, <b>{len(f['tables'])} tables</b> declared in the models plus <code>alembic_version</code>. "
        "Models live in <code>app/models.py</code> and in small per-feature files (<code>insights_models.py</code>, "
        "<code>issue_lead_models.py</code>, <code>po_send_models.py</code>, <code>setting_models.py</code>, "
        "<code>notification_models.py</code>, <code>team/models.py</code>, <code>team/tasks_models.py</code>, "
        "<code>team/chat_models.py</code>)."
    )
    d.ul(
        [
            "<b>Conventions</b>: every timestamp column is <code>UTCDateTime</code> (stored UTC, UTC re-attached on read); business dates are plain dates in store time (America/New_York).",
            "<b>Name snapshots</b>: 'by' columns keep a name next to the user id (<code>created_by_name</code>, <code>placed_by_name</code>...), so history reads correctly after an account is deleted.",
            "<b>Few hard foreign keys</b>: most links are logical (vendor names, item ids, checklist types, issue keys). In the diagrams a solid line is a real FOREIGN KEY, a dotted line a logical link the code follows.",
            "<b>Toast identity</b>: <code>orders.item_guid</code> = <code>inventory_snapshots.item_id</code> = <code>toast_menu_items.item_id</code> (the Toast menu item GUID).",
            "<b>Row counts</b> below are from a read-only query of production on the day this was written (production was at 0056, so <code>push_deliveries</code> does not exist there yet).",
        ]
    )
    for num, title, tables, er in DOMAINS:
        d.h(2, num, title)
        d.fig(er, f"{title}: key columns and links. Full column lists in Appendix B.")
        rows = []
        for t in tables:
            n = rc.get(t)
            rows.append([Raw(f"<code>{esc(t)}</code>"), f"{n:,}" if isinstance(n, int) else "new (0058)" if t == "push_deliveries" else "-", PURPOSE.get(t, "")])
        d.table(["Table", "Rows (prod)", "Purpose"], rows, widths=["26%", "11%", "63%"], cls="compact")
