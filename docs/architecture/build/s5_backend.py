import os
from collections import OrderedDict, defaultdict

from doc_core import Raw, esc

ROUTER_PURPOSE = {
    "routers/auth.py": "Login (web / app), logout, me, change / set password, users admin (features, can_assign, hide_sales), delete my account",
    "routers/jobs.py": "Job status widget, manual sales sync",
    "routers/inventory.py": "Item search / by barcode (code matching), price changes and history, inventory upload, today's invoice vendors",
    "routers/inventory_api.py": "Inventory from Toast admin page: status, compare, nightly compares, menu re-check, the switch",
    "routers/sales.py": "Items Sold (+CSV), sales CSV/PDF export, sales accuracy",
    "routers/insights.py": "Ten Insights reports; Loss watch / Odd checks (admin) and their acknowledgement",
    "routers/ordering.py": "Forecast, reorder candidates (critical_only), suppliers, projection, weekly sales, similar items, cart, PO PDF, placed orders, order again, mark placed",
    "routers/po_send.py": "Vendor contacts, Send PO preview (+PDF), send, send log",
    "routers/critical_dismissals.py": "Critical tile: list, Close / Skip this week / Remind me later, Show again",
    "routers/order_actions.py": "Order already placed / Skip this week / Other, and their Undo",
    "routers/reports.py": "Inventory reports (barcodes, dead stock, negative, duplicates, vendor price, store vs container) + CSV",
    "routers/reconciliation.py": "Purchase log, deliveries (candidates, suggested, confirm, history), reconciliation report, invoice gaps + CSV",
    "routers/ask.py": "Ask Inventory Bot",
    "routers/digest.py": "Weekly digest",
    "routers/transfers.py": "Store <-> Container transfers (retired page; kept for old app builds)",
    "routers/orders.py": "Open checks today / all-time, rescan, CSV, employees",
    "routers/invoices.py": "Invoices: list, submit, duplicate check, resend, statuses, close suggestions, delete workflow, trash; /public/invoices file links",
    "routers/vendor_calendar.py": "Vendor schedules, schedule suggestions (+accept all), calendar, skips",
    "routers/category_overrides.py": "Raw admin API over category corrections",
    "routers/po_sections.py": "PO sections editor on the old /api/categories paths (app build 5 compatible)",
    "routers/issues.py": "Fix Issues lists, summary, close / reopen, history, closed report, CSV",
    "routers/glossary.py": "Every status / tag / label with its rule",
    "routers/checklist_admin.py": "Checklist builder (drafts, publish, versions), public links, WhatsApp groups",
    "routers/flyers.py": "Flyers library, files, thumbnails, send, retry",
    "routers/checklist_public.py": "Logged-out public checklist API and the /c page",
    "routers/checklists.py": "Checklist schemas, overview, media, submit, history",
    "routers/notifications.py": "Devices, prefs (+reset), recent, store defaults, volume, test push",
    "team/router_core.py": "App config (update gate), bootstrap, change feed (long-poll), rules, teams admin, media",
    "team/router_chat.py": "Announcements, channels, DMs, messages, pins, seen-by, search, chat settings, reports, blocks",
    "team/router_tasks.py": "Tasks (list, filters, create, edit, steps, complete, can't do, start, reopen, comments, reports, bulk), summary, stats, templates",
    "routers/issue_leads.py": "Category & vendor leads admin, preview, settings, run now",
    "main.py": "Health",
}


def _prefix(paths):
    parts = [p.split("/") for p in paths]
    out = []
    for seg in zip(*parts):
        if len(set(seg)) == 1 and "{" not in seg[0]:
            out.append(seg[0])
        else:
            break
    return "/".join(out) or "/"


def render(d, ctx):
    f = ctx["facts"]
    d.h(1, "5", "Backend")
    d.h(2, "5.1", "Code layout")
    d.table(
        ["Folder / file", "What lives there"],
        [
            ["app/main.py", "FastAPI app, lifespan (schedulers), demo guard, CORS, router includes, static website mount, invoice-link redirect"],
            ["app/config.py", "Settings (section 2.9)"],
            ["app/db.py", "Engine, SessionLocal, get_db, SQLite pragmas, UTCDateTime"],
            ["app/auth.py, features.py, sales_access.py, demo.py, demo_guard.py", "Sessions, feature gates, hide_sales, demo rules (section 3)"],
            ["app/models.py + *_models.py", "SQLAlchemy models (section 4)"],
            ["app/routers/", "HTTP layer: validation, gates, calling services; one file per area"],
            ["app/services/", "Business logic: Toast clients, forecasts, ordering, invoices, notifications, Insights..."],
            ["app/team/", "Team hub: models, change feed, tasks, recurrence, auto tasks, source sync, chat, announcements, moderation, media, jobs, routers"],
            ["app/scheduler.py", "The three schedulers (5.4)"],
            ["app/toast_client.py", "Toast Standard API OAuth + ordersBulk + employees"],
            ["app/glossary.py", "Glossary terms quoting the real thresholds"],
            ["alembic/versions/", "57 migration files, one chain ending at 0059"],
            ["tests/", "pytest suite (2,190 tests on 3.9, 2,191 on 3.12 at the end of the Oct 15 batch)"],
        ],
        widths=["34%", "66%"],
        cls="compact",
    )

    d.h(2, "5.2", "Routers")
    by_src = OrderedDict()
    for r in f["routes"]:
        src = r["src"].split(":")[0]
        by_src.setdefault(src, []).append(r)
    rows = []
    for src, rs in by_src.items():
        gates = defaultdict(int)
        for r in rs:
            g = ", ".join(r["gate"]) if r["gate"] else ("login" if r["login"] else "none (public)")
            gates[g] += 1
        gate_txt = "; ".join(f"{k} x{v}" if len(gates) > 1 else k for k, v in sorted(gates.items(), key=lambda x: -x[1]))
        meth = defaultdict(int)
        for r in rs:
            meth[r["method"]] += 1
        rows.append(
            [
                Raw(f"<code>{esc(src)}</code>"),
                Raw(f"<code>{esc(_prefix([r['path'] for r in rs]))}</code>"),
                str(len(rs)),
                " ".join(f"{m} {n}" for m, n in sorted(meth.items())),
                gate_txt.replace("feat:", "").replace("any:", "any of "),
                ROUTER_PURPOSE.get(src, ""),
            ]
        )
    d.p(
        f"{len(f['routes'])} HTTP routes (method + path). The table is generated by importing the app and walking "
        "<code>app.routes</code>; 'Gate' lists the dependencies beyond the login (feature keys, admin, wrappers). "
        "Every route also runs the demo guard. Appendix A lists every route."
    )
    d.table(["Router", "Prefix", "#", "Methods", "Gate", "Purpose"], rows, widths=["18%", "16%", "4%", "12%", "20%", "30%"], cls="compact small")

    d.h(2, "5.3", "Service modules")
    mods = [m for m in f["modules"] if m["path"].startswith(("app/services/", "app/team/")) or m["path"] in ("app/toast_client.py", "app/scheduler.py", "app/glossary.py", "app/demo.py", "app/demo_guard.py", "app/sales_access.py")]
    rows = []
    for m in mods:
        doc = m["doc"]
        cut = doc.find(". ", 60)
        if 0 < cut < 300:
            doc = doc[: cut + 1]
        elif len(doc) > 300:
            doc = doc[:297] + "..."
        rows.append([Raw(f"<code>{esc(m['path'].replace('app/', ''))}</code>"), f"{m['lines']:,}", doc])
    d.p("Purpose = the first sentence of each module's docstring (generated). Line counts show where the weight is.")
    d.table(["Module", "Lines", "Purpose"], rows, widths=["24%", "7%", "69%"], cls="compact small")

    d.h(2, "5.4", "Scheduled jobs")
    d.p(
        "Three APScheduler <code>BackgroundScheduler</code>s inside the backend process, all in America/New_York. "
        "Each has its own kill switch so the dev server can run the safe ones. Most jobs use "
        "<code>coalesce=True</code> and <code>max_instances=1</code> so a slow run never stacks."
    )
    jobs = [
        ["main", "daily_sales_sync", "04:00 (+ startup +10 s)", "Re-sync every day of the last 14 without a successful run; then warm the Fix Issues forecast", "Toast ordersBulk per date", "SCHEDULER_ENABLED"],
        ["main", "inventory_api_snapshot", "04:05", "Build inventory from Toast (waits up to 20 min for yesterday's sync); write it only if the switch is on and no CSV today; always store the comparison", "Toast stock + menus", "SCHEDULER_ENABLED"],
        ["main", "open_orders_full_scan", "04:15 (+ startup +30 s)", "Every business date since sales began -> open_order_cache", "~100+ ordersBulk calls", "SCHEDULER_ENABLED"],
        ["main", "open_orders_today_refresh", "every OPEN_ORDERS_TODAY_REFRESH_MINUTES (default 15; startup +15 s)", "Today's open checks", "1 ordersBulk", "SCHEDULER_ENABLED"],
        ["main", "insights_nightly", "04:30", "Yesterday's ERA reports, re-pulls of recent days, some history, menu map, Loss watch / Odd checks flags, purge", "ERA within budgets", "SCHEDULER_ENABLED + INSIGHTS_NIGHTLY_ENABLED"],
        ["main", "insights_catchup", "hourly 05:20-23:20", "Fill a missing day of the last 2 closed days once the budget allows; no Toast call when nothing is missing", "ERA (only if missing)", "same"],
        ["main", "sales_repull_HHMM", "06:30, 12:30, 17:30, 22:30", "Compare ERA day totals with stored sales for 14 days, re-sync dates that changed", "ERA + ordersBulk", "SCHEDULER_ENABLED + SALES_REPULL_ENABLED"],
        ["main", "menu_barcode_recheck", "every 60 min", "Menu metadata; full menu only if lastUpdated changed -> toast_menu_items", "Toast menus", "SCHEDULER_ENABLED"],
        ["main", "startup_fix_issues_warmup", "startup +20 s", "Recompute the in-memory Slow / Dead forecast", "-", "SCHEDULER_ENABLED"],
        ["notification", "notify_morning_summary", "09:00", "Refresh today's open checks, run 8 morning triggers once, send each to whoever opted in, then ONE Morning summary per user who has it on; prune push log", "FCM (+1 ordersBulk)", "NOTIFICATIONS_ENABLED"],
        ["notification", "notify_closing_checklist", "21:30", "Closing checklists not submitted today", "FCM", "NOTIFICATIONS_ENABLED"],
        ["notification", "notify_invoice_failed", "every 10 min", "Invoices whose WhatsApp or Toast email failed (submitter + admins)", "FCM", "NOTIFICATIONS_ENABLED"],
        ["notification", "notify_cart_removal_request", "every 10 min", "Cart removal requests waiting for an admin", "FCM", "NOTIFICATIONS_ENABLED"],
        ["notification", "notify_open_orders_1600 / 2130", "16:00, 21:30", "Open-checks summaries (opt-in since Oct 15)", "FCM + 1 ordersBulk", "NOTIFICATIONS_ENABLED"],
        ["team", "team_changes_prune", "03:30", "Delete change-feed rows older than 30 days", "-", "TEAM_JOBS_ENABLED"],
        ["team", "team_media_prune_unattached", "03:40", "Delete uploads never attached to anything", "-", "TEAM_JOBS_ENABLED"],
        ["team", "team_chat_retention", "03:45", "Hard-delete chat older than 365 days; blank deleted messages after 30 days", "-", "TEAM_JOBS_ENABLED"],
        ["team", "team_announcement_pending_pushes", "every 5 min", "Send announcement pushes held for quiet hours", "FCM", "TEAM_JOBS_ENABLED"],
        ["team", "tasks_tick", "every 5 min", "Recurring occurrences -> auto-task rules (checklist, invoice, order, issue leads) -> assigned sweep -> can't-do sweep -> due / overdue reminders; closes stale auto tasks; refreshes lead progress", "FCM", "TEAM_JOBS_ENABLED + TEAM_TASKS_ENABLED"],
    ]
    d.table(["Scheduler", "Job id", "When", "What it does", "Outside calls", "Switch"], jobs, widths=["10%", "17%", "15%", "34%", "12%", "12%"], cls="compact small")
    d.fig(
        r"""
gantt
  title 24 hours of scheduled work (store time)
  dateFormat HH:mm
  axisFormat %H:%M
  todayMarker off
  section Team
  change feed / media / chat prune      :t1, 03:30, 20m
  tasks_tick + announcement pushes every 5 min :active, t2, 00:00, 1439m
  section Toast sync
  sales sync                            :crit, s1, 04:00, 10m
  inventory from Toast                  :s2, 04:05, 20m
  open checks full scan                 :s3, 04:15, 15m
  open checks today, every few min      :active, s4, 00:00, 1439m
  menu re-check hourly                  :s5, 00:00, 1439m
  section Analytics
  Insights nightly                      :crit, a1, 04:30, 30m
  Insights catch-up hourly at 20 past :a2, 05:20, 1080m
  sales re-pull                         :a3, 06:30, 10m
  sales re-pull                         :a4, 12:30, 10m
  sales re-pull                         :a5, 17:30, 10m
  sales re-pull                         :a6, 22:30, 10m
  section Pushes
  quiet hours                           :done, q1, 00:00, 420m
  Morning summary                       :crit, n1, 09:00, 5m
  open checks summary (opt-in)          :n2, 16:00, 5m
  closing checklist + open checks       :crit, n3, 21:30, 5m
  quiet hours                           :done, q2, 22:00, 120m
  invoice failed / cart removals every 10 min :active, n4, 07:00, 900m
""",
        "The day at a glance. Quiet hours (22:00-07:00) block scheduled pushes; the 5-minute task job still runs (its pushes wait).",
    )

    d.h(2, "5.5", "Background threads and after-response work")
    d.table(
        ["Worker", "Started by", "What it does"],
        [
            ["Flyer sender", "lifespan (flyer_sender.start)", "One worker thread; works through queued flyer_sends one group at a time with a random 3-6 s pause; uploads media to Whapi once, then sends; reconciles ambiguous timeouts; flyer_sent push. Idle sweep every 300 s; fails sends a previous run left stuck."],
            ["Invoice notifications", "BackgroundTasks after POST /api/invoices and /resend", "Two parallel threads: Whapi text to the invoice group, Gmail email with the Toast-friendly PDF; statuses written back to the row."],
            ["Insights fill", "insights_data.request_fill on a page asking for missing dates", "One background fill at a time (process-wide lock), up to 12 calls, budget-limited."],
            ["Open checks refresh", "GET /api/orders/open/all-time when today's scan is older than 2 min", "Same one-call refresh on a daemon thread; the request answers from the cache at once."],
            ["Task pushes", "tasks_service.after_response (BackgroundTasks)", "Create, reassign, can't do, Create task now, comment, complete: pushes sent after the response in their own session."],
            ["Chat / announcement pushes", "BackgroundTasks in router_chat", "Pushes in their own session; throttled 1/min per member."],
            ["Source sync", "after a source's commit (checklist submit, order actions, mark placed, Critical skip, invoice close/delete, Fix Issues close/reopen)", "team/source_sync.task_sources_changed: closes the matching auto tasks at once; never raises."],
            ["Inventory upload post-work", "BackgroundTasks after POST /api/inventory/upload", "Warm the Fix Issues cache; re-compare with Toast once if before 09:00."],
        ],
        widths=["20%", "30%", "50%"],
        cls="compact",
    )

    d.h(2, "5.6", "Caching")
    d.p(
        "All caches are in-process and empty after a restart. That is one reason the backend must stay a single "
        "process: a second worker would hold its own copies and only see its own writes."
    )
    d.table(
        ["Cache", "Holds", "Lifetime / invalidation", "Code"],
        [
            ["Report cache (Oct 15)", "Today report (reorder candidates + vendor groups, any lookback / lead), supplier list, Vendor Calendar need-to-order counts", "32 entries, single-flight per key, TTL 300 s; ended by a COMMITTED write to orders, inventory_snapshots, placed_orders, purchase_log, invoice_submissions, vendor_schedules, category_overrides, setting_changes; a data fingerprint change; a live-stock refresh; invalidate(). Critical dismissals are applied per request on copies, so hide / show is instant without recomputing.", "services/report_cache.py"],
            ["Live Items Sold today", "Today's sales pulled live from Toast", "60 s (LIVE_SALES_CACHE_SECONDS)", "services/items_sold.py"],
            ["Live stock overlay", "Toast /stock on-hand on top of the latest snapshot (switch on)", "300 s (LIVE_STOCK_CACHE_SECONDS)", "services/inventory_api.py"],
            ["Fix Issues forecast", "Slow / dead classification", "600 s, warmed after sync / upload / startup", "services/stock_issues.py"],
            ["Insights today", "Live ERA numbers for today", "90 s", "services/insights_data.py"],
            ["Toast tokens", "OAuth access tokens (Standard, Analytics)", "Until expiry", "toast_client.py, toast_analytics.py"],
            ["Employees", "Toast labor employees (server names)", "600 s", "services/open_orders.py"],
            ["WhatsApp groups list", "Whapi GET /groups", "300 s", "services/whatsapp_groups.py"],
            ["Others", "Delivery suggestions, invoice close suggestions, schedule-suggestion rises, similar products, checklist schemas", "Per module", "-"],
            ["DB-backed", "open_order_cache, analytics_cache, toast_menu_items", "Jobs above", "-"],
            ["Website", "index.html no-cache; hashed assets 1 day", "-", "main.py"],
            ["App", "Riverpod keepAlive with cacheFor (e.g. Critical 10 min), Home heavy reports kept 2 min on pull-to-refresh", "-", "provider_cache.dart, today_providers.dart"],
        ],
        widths=["17%", "27%", "40%", "16%"],
        cls="compact small",
    )
    d.fig(
        r"""
flowchart LR
  subgraph W["Writers in this process"]
    W1["sales sync"]
    W2["inventory upload /<br/>inventory from Toast"]
    W3["Mark placed, Send PO,<br/>Order already placed + Undo"]
    W4["invoices, Delivery Review"]
    W5["vendor schedules,<br/>category edits, switch"]
  end
  W1 --> C["engine COMMIT event<br/>tracked tables?"]
  W2 --> C
  W3 --> C
  W4 --> C
  W5 --> C
  C -- yes --> X["report_cache: end every entry"]
  EXT["script writing the DB<br/>from outside"] --> FP["data fingerprint<br/>max ids + job runs, 0.4 ms"]
  FP -- changed --> X
  TTL["300 s TTL"] --> X
  R["GET reorder-candidates /<br/>vendor-calendar /<br/>Critical Close"] --> HIT{"cached and valid?"}
  HIT -- yes --> FAST["answer in ms<br/>+ dismissals applied on a copy"]
  HIT -- no --> COMP["compute once<br/>(single-flight) 1-3 s"] --> FAST
""",
        "Report cache: what ends an entry and how a read is served.",
        size="small",
    )

    d.h(2, "5.7", "External integrations, limits and failure modes")
    d.table(
        ["System", "Calls", "Timeouts / limits", "When it fails"],
        [
            ["Toast Standard API", "POST /authentication/v1/authentication/login; GET /orders/v2/ordersBulk?businessDate= (paged); GET /labor/v1/employees; GET /stock/v1/inventory; GET /menus/v2/menus; GET /menus/v2/metadata", "30-60 s (menus 180 s, live stock 20 s, one try); 429 back-off", "The sync job is marked failed and retried by the 14-day gap check; pages show the last cached data; live stock overlay is skipped."],
            ["Toast Analytics (ERA)", "POST /era/v1/<kind>/<range> -> report GUID; GET /era/v1/<kind>/<guid> until ready", "POST 60 s, GET 180 s; per-family limits (3.7); daily budgets; 404/409 retried then marked unavailable", "Pages answer with stale: true and a plain 'note' saying which day is missing and when it will arrive."],
            ["Whapi", "POST /messages/text, /messages/image, /messages/document, /media; GET /messages/list/{chat} (reconcile), GET /groups", "15-25 s, media 120 s; sandbox plan = 5 chats", "Saved first, sent after; status columns record the error; ambiguous timeouts are reconciled against the chat history; invoice failures push every 10 min; Resend buttons."],
            ["Gmail SMTP", "smtp.gmail.com:465 SSL with an app password", "PO send 30 s", "Invoice: toast_email_error + Resend. Send PO: 502 with the po_sends row kept; nothing placed."],
            ["Firebase FCM", "messaging.send_each (APNs sound + thread-id, Android tag)", "-", "No config = dry run (logged, never marked sent); invalid tokens deleted; nothing delivered = not logged, retried next run."],
            ["Anthropic", "Messages API with tools (Ask), one call (digest)", "App waits up to 120 s", "Clear error to the user; demo gets a fixed answer without a call."],
            ["Labels app", "From the app only: lookup, search, print, print job, custom product, preview PNG, print history", "Dio defaults", "Scan shows an error; nothing in the dashboard DB depends on it."],
        ],
        widths=["15%", "35%", "20%", "30%"],
        cls="compact small",
    )
