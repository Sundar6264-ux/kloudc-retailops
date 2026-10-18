from doc_core import Raw, code, esc


CONFIG_GROUPS = [
    ("Database", ["database_url"]),
    ("Toast Standard API", ["toast_api_base", "toast_client_id", "toast_client_secret", "toast_restaurant_guid", "toast_timezone"]),
    ("Sales sync and accuracy", ["sales_sync_hour", "sales_sync_minute", "sales_sync_backfill_days", "sales_repull_enabled", "sales_repull_days", "sales_repull_times", "live_sales_cache_seconds"]),
    ("Toast Analytics / Insights", ["toast_analytics_client_id", "toast_analytics_client_secret", "toast_analytics_base", "insights_nightly_enabled", "insights_nightly_hour", "insights_nightly_minute", "insights_today_cache_seconds", "insights_check_daily_budget", "insights_guest_daily_budget", "loss_watch_large_discount", "loss_watch_large_refund", "loss_watch_large_void", "loss_watch_median_multiple", "odd_check_open_hours", "odd_check_large_refund", "insights_hmac_secret", "insights_hmac_key_file"]),
    ("Open checks", ["open_orders_full_scan_hour", "open_orders_full_scan_minute", "open_orders_today_refresh_minutes"]),
    ("Scheduler / serving", ["scheduler_enabled", "frontend_dist_dir", "cors_origins"]),
    ("Forecast / inventory from Toast", ["projection_method", "inventory_api_snapshots", "inventory_api_snapshot_hour", "inventory_api_snapshot_minute", "inventory_csv_max_age_days", "menu_barcode_recheck_minutes", "live_stock_cache_seconds", "inventory_api_ready_nights", "inventory_api_ready_qty_pct", "inventory_api_ready_price_pct", "inventory_api_ready_barcode_pct", "inventory_api_ready_category_pct", "inventory_api_ready_max_unexplained_qty", "inventory_api_ready_whatsapp", "inventory_api_late_csv_until_hour"]),
    ("Anthropic", ["anthropic_api_key"]),
    ("Sessions", ["session_cookie_secure", "session_lifetime_days", "session_cookie_domain"]),
    ("WhatsApp (Whapi)", ["whapi_api_token", "whapi_base_url", "whapi_chat_id", "whapi_spice_town_checklist_group_id", "whapi_street_kitchen_checklist_group_id"]),
    ("Email / invoices / PO", ["gmail_address", "gmail_app_password", "toast_invoice_email", "po_whatsapp_enabled", "public_base_url", "invoice_uploads_dir"]),
    ("Flyers", ["flyers_dir"]),
    ("Push", ["firebase_credentials_file", "notifications_enabled"]),
    ("Team hub", ["team_tasks_enabled", "team_announcements_enabled", "team_chat_enabled", "team_poll_interval_s", "team_chat_poll_interval_s", "team_badge_poll_interval_s", "team_changes_retention_days", "team_uploads_dir", "team_everyone_exclude", "demo_usernames", "team_jobs_enabled", "team_chat_retention_days", "team_deleted_message_days", "team_autotask_checklist_enabled", "team_autotask_checklist_team", "team_autotask_invoice_enabled", "team_autotask_invoice_team", "team_autotask_order_enabled", "team_autotask_order_team", "team_autotask_order_min_items", "team_whatsapp_fallback"]),
    ("App update gate", ["min_supported_build_ios", "min_supported_build_android", "app_store_url", "play_store_url"]),
]
SECRET = {"toast_client_id", "toast_client_secret", "toast_analytics_client_id", "toast_analytics_client_secret", "anthropic_api_key", "whapi_api_token", "gmail_app_password", "insights_hmac_secret", "firebase_credentials_file"}


def render(d, ctx):
    f = ctx["facts"]
    d.h(1, "2", "Deployment topology")
    d.h(2, "2.1", "Host and daemons")
    d.p(
        "Everything runs on one always-on <b>Mac mini</b> (hostname <code>spicetown-server</code>) in the store. "
        "Services are macOS <b>LaunchDaemons</b> in <code>/Library/LaunchDaemons/</code> (they run without anyone "
        "logged in), each with <code>RunAtLoad</code> + <code>KeepAlive</code>. There is no crontab, no container "
        "and no second server."
    )
    d.fig(
        r"""
flowchart LR
  W["watchdog<br/>every 300 s"] -. "re-bootstrap if unloaded<br/>(backend, cloudflared, labels)" .-> CF
  U["cloudflared.updater<br/>04:30"] -.-> CF
  CF["cloudflared<br/>tunnel spicetown-ops"] --> B["backend<br/>uvicorn :8000<br/>.venv312, one process"]
  CF --> L["labels<br/>gunicorn :8080"]
  CF --> FL["flowers<br/>static :8085"]
  LS["labels.sync<br/>every 15 min"] -.-> L
  B --> DB[("spicetown.db<br/>SQLite WAL")]
  B --> UP[("uploads/invoices<br/>uploads/team")]
  B --> FLY[("~/Spice Town Flyers")]
  L --> LDB[("labels DB")]
  L -. "read-only sessions" .-> DB
  L --> PRN["CUPS<br/>Brother QL-810W"]
""",
        "Daemons on the Mac mini and the files each one owns. The watchdog covers backend, cloudflared and labels only.",
    )
    d.table(
        ["launchd label", "Program", "Notes"],
        [
            ["com.spicetown.backend", "~/spicetown-backend/backend/.venv312/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000", "API + website + 3 schedulers + flyer sender. ONE process, never --workers (caches, locks and the task long-poll are in-process). stdout logs/backend.log, stderr logs/backend.error.log. The repo copy of the plist still names .venv (py3.9); the installed one uses .venv312."],
            ["com.spicetown.cloudflared", "/opt/homebrew/bin/cloudflared tunnel run spicetown-ops", "Ingress (~/.cloudflared/config.yml): ops.spicetown.shop -> :8000, labels.spicetown.shop -> :8080, flowers.spicetown.shop -> :8085, anything else 404."],
            ["com.spicetown.cloudflared.updater", "backend/scripts/update_cloudflared.sh", "Daily 04:30; restarts the tunnel only when the version changed."],
            ["com.spicetown.watchdog", "backend/scripts/watchdog.sh (root)", "Every 300 s; re-bootstraps backend, cloudflared, labels if not loaded. Not flowers or labels.sync."],
            ["com.spicetown.labels", "spicetown-labels/scripts/run_gunicorn.sh", "Separate repo, venv and SQLite DB. Prints over CUPS."],
            ["com.spicetown.labels.sync", "spicetown-labels/scripts/sync_catalog.sh", "Every 15 min: git pull the catalog CSV (built by a GitHub Action from Toast), POST /api/refresh."],
            ["com.spicetown.flowers", "spicetown-flowers/site/serve.py", "Unrelated static site sharing the tunnel."],
        ],
        widths=["24%", "36%", "40%"],
        cls="compact",
    )

    d.h(2, "2.2", "Inside the backend process")
    d.p(
        "One <code>uvicorn</code> process runs one FastAPI app. Async handlers (the change-feed long-poll) run on the "
        "event loop; ordinary <code>def</code> handlers run in Starlette's thread pool. Three APScheduler "
        "<code>BackgroundScheduler</code>s and a few daemon threads live in the same process and share its CPU, GIL "
        "and SQLite connection pool."
    )
    d.fig(
        r"""
flowchart TB
  REQ["HTTPS requests from cloudflared"] --> UV["uvicorn :8000 - asyncio event loop"]
  UV --> MW["CORS + demo_write_guard on every route"]
  MW --> ASY["async handlers<br/>team updates long-poll"]
  MW --> TP["thread pool<br/>every def handler"]
  MW --> ST["StaticFiles /<br/>frontend/dist"]
  TP --> SVC["services/*.py and team/*.py"]
  ASY -. "short sessions,<br/>no thread held" .-> SVC
  subgraph SCH["In-process schedulers"]
    S1["main<br/>SCHEDULER_ENABLED"]
    S2["notification<br/>NOTIFICATIONS_ENABLED"]
    S3["team<br/>TEAM_JOBS_ENABLED"]
  end
  subgraph THR["Daemon threads"]
    T1["flyer sender"]
    T2["insights fill"]
    T3["open checks refresh"]
    T4["invoice sends"]
  end
  SCH --> SVC
  THR --> SVC
  SVC --> POOL[("SQLAlchemy engine - SQLite WAL, busy_timeout 5 s")]
  POOL -. "commit event" .-> RC["report_cache (per process)"]
  POOL -. "after_commit" .-> LP["change_version wakes long-polls"]
  SVC --> RC
  LP -.-> ASY
""",
        "What runs inside the single backend process.",
    )
    d.ul(
        [
            "<b>Lifespan</b> (<code>main.py</code>): start main scheduler, notification scheduler, team scheduler, flyer sender; stop in reverse on shutdown.",
            "<b>App-wide dependency</b> <code>demo_write_guard</code> runs for every route, including ones added later (section 3.5).",
            "<b>Routers</b>: 34 router objects included in a fixed order; the public checklist page router is registered before the static mount at <code>/</code>.",
            "<b>Exception handler</b>: a 401/403 on <code>/public/invoices/*</code> becomes a 302 to <code>/#/login?next=...</code> so a WhatsApp link opens the login page instead of JSON.",
            "<b>SQLite</b> (<code>app/db.py</code>): WAL journal, <code>busy_timeout=5000</code>, and (Oct 15) per-connection <code>cache_size=-16384</code> (16 MB), <code>mmap_size=256 MB</code>, <code>temp_store=MEMORY</code>. The <code>UTCDateTime</code> column type re-attaches UTC on read (SQLite drops it).",
        ]
    )

    d.h(2, "2.3", "Network exposure and domains")
    d.ul(
        [
            "<b>Cloudflare Tunnel</b>: <code>cloudflared</code> keeps an outbound connection to Cloudflare; Cloudflare terminates TLS. No port forwarding, no public IP. Domains: <code>ops.spicetown.shop</code> (this system), <code>labels.spicetown.shop</code>, <code>flowers.spicetown.shop</code>.",
            "<b>Session cookie domain</b> is <code>.spicetown.shop</code> in production (SESSION_COOKIE_DOMAIN), so the labels subdomain receives the same cookie and can check the dashboard login.",
            "<b>CORS</b>: CORS_ORIGINS (dev Vite origin and leftovers of the retired Capacitor wrapper), credentials allowed. The website is same-origin in production, so CORS matters only for dev.",
            "<b>PUBLIC_BASE_URL</b> = <code>https://ops.spicetown.shop</code> in production; used to build invoice links and public checklist links. (Its code default still names the old Tailscale host.)",
            "<b>Tailscale</b> is dev-only now: <code>up.sh</code> starts a dev uvicorn on :8001 (with --reload, DB <code>spicetown-dev.db</code>, schedulers off) and <code>tailscale serve --https=8443</code> exposes it to the tailnet for phone testing.",
        ]
    )

    d.h(2, "2.4", "How the website is served")
    d.ul(
        [
            "<code>npm run build</code> writes <code>frontend/dist</code>; FastAPI mounts it at <code>/</code> with <code>StaticFiles(html=True)</code> (one process, one port).",
            "<code>index.html</code> is sent with <code>Cache-Control: no-cache</code>; hashed assets with <code>public, max-age=86400</code>. A new build is live on the next page load - no restart for website-only changes.",
            "The website uses <code>HashRouter</code> (<code>/#/reorder</code>), so no server-side catch-all route is needed.",
            "<code>/c/&lt;slug&gt;-&lt;code&gt;</code> is a FastAPI route that returns the same <code>index.html</code> with a strict CSP; <code>main.jsx</code> renders only the public checklist page for <code>/c/</code> paths.",
        ]
    )

    d.h(2, "2.5", "Deploy flow and rollback")
    d.fig(
        r"""
sequenceDiagram
  autonumber
  participant DEV as Dev checkout (ops-fixes)
  participant GH as GitHub
  participant PROD as ~/spicetown-backend
  participant DB as spicetown.db
  participant OWN as Owner (sudo)
  participant LD as launchd
  DEV->>GH: git push ops-fixes and ops-fixes:main
  PROD->>DB: sqlite3 mode=ro .backup bak-pre-NNNN + integrity_check
  PROD->>GH: git pull --ff-only origin main (old process keeps serving)
  PROD->>DB: .venv312/bin/alembic upgrade head (additive migrations only)
  PROD->>PROD: npm run build -- --outDir dist-next
  OWN->>PROD: sudo bash ~/finish-deploy.sh
  PROD->>PROD: dist to dist-prev, dist-next to dist
  PROD->>LD: launchctl kickstart -k system/com.spicetown.backend
  LD-->>PROD: new process (about 15 s offline)
  OWN->>PROD: curl /api/health = 200, new-route markers = 401 (logged out)
""",
        "Production deploy as practised (DEPLOY_OCT14.md / DEPLOY_OCT15.md).",
    )
    d.ul(
        [
            "Every migration is additive, so the old process keeps working against the new schema between steps 4 and 8.",
            "Avoid restarting at 04:00-04:35 (nightly sync and Insights), 06:30 / 12:30 / 17:30 / 22:30 (sales re-pulls), 09:00 (Morning summary), 10:00 (lead tasks) and 21:30 (closing-checklist push).",
            "<b>Rollback</b> (light): <code>git reset --hard &lt;previous&gt;</code>, keep the DB (old code ignores new tables/columns), swap <code>dist-prev</code> back, kickstart. Optional <code>alembic downgrade</code>. Full restore from the <code>.bak-pre-*</code> copy only for a damaged DB (loses later writes).",
            "<b>App releases</b> are separate: Xcode archive of the Flutter app, TestFlight, App Store review. The server always stays compatible with app builds 5-9 already installed (additive API only).",
        ]
    )

    d.h(2, "2.6", "Backups, logs and disk")
    d.table(
        ["Item", "State"],
        [
            ["Database backups", "Manual only: an online sqlite3 .backup (spicetown.db.bak-pre-NNNN-<ts>) before every migration, kept next to the DB. No scheduled backup and no off-machine copy exists."],
            ["Logs", "backend/logs/backend.log, backend.error.log (Python logging goes to stderr), cloudflared and watchdog logs. No rotation configured."],
            ["Disk (Oct 14)", "DB about 383 MB (+ WAL), uploads/invoices 65 MB, uploads/team 52 KB, ~/Spice Town Flyers about 18 MB."],
            ["Uploads", "Invoice files: uploads/invoices (served only with a login). Team media: uploads/team (re-encoded, token URLs, login). Flyers: a Finder folder per category, soft-deleted into _Deleted/."],
        ],
        widths=["22%", "78%"],
    )

    d.h(2, "2.7", "Python environments and the dev server")
    d.ul(
        [
            "<b>.venv312</b> (Python 3.12) is the live production venv. <b>.venv</b> (Python 3.9) is kept for rollback and for the dev test runs - code must stay 3.9-compatible (<code>typing.Optional</code>, never <code>X | None</code>).",
            "Dev: <code>~/spicetown-dev/up.sh</code> / <code>down.sh</code> (PID file, never touches prod). Dev <code>.env</code> sets SCHEDULER_ENABLED, NOTIFICATIONS_ENABLED and TEAM_JOBS_ENABLED to false so dev never spends the shared Toast rate limit or sends anything.",
            "Throwaway test servers (regression suites) run on other ports over a <code>.backup</code> copy of prod with every outside service blank, and are killed afterwards.",
        ]
    )

    d.h(2, "2.8", "Database migrations (Alembic)")
    al = f["alembic"]
    d.p(
        f"{len(al)} revision files in <code>backend/alembic/versions/</code>, one linear chain "
        f"(<code>tests/test_migration_chain.py</code> checks a single head). Production was at <b>0056</b>; the Oct 15 batch "
        "adds 0057-0059. The full list is in Appendix G. The latest ones:"
    )
    d.table(
        ["Revision", "Down", "What it does"],
        [[a["rev"], a["down"], a["title"]] for a in al[-10:]],
        widths=["12%", "10%", "78%"],
        cls="compact",
    )
    d.ul(
        [
            "<b>0057</b> covering index <code>ix_inventory_snapshots_item_date_qty (item_id, snapshot_date, inventory_quantity)</code> - about 2 s to build, about +38 MB; opening-count query 1,173 ms to 333 ms.",
            "<b>0058</b> table <code>push_deliveries</code> (per-user push log, titles only, pruned after 180 days).",
            "<b>0059</b> nullable <code>tasks.progress_total</code> and <code>tasks.progress_done</code> (Fix Issues lead tasks 'N left of M').",
            "Rehearsed on a prod copy: upgrade 0056 to 0059 in 2.1 s, downgrade in 0.6 s, upgrade again 1.0 s; integrity and foreign-key checks clean.",
        ]
    )

    d.h(2, "2.9", "Configuration (environment variable names only)")
    d.p(
        "All settings come from <code>app/config.py</code> (pydantic-settings) reading <code>backend/.env</code> in the "
        "process working directory. Names are the field names in upper case. <b>Values are never listed here.</b> "
        "Entries marked <span class=\"tag secret\">secret</span> are credentials (kept only in the production "
        "<code>.env</code>, or a file it points to, readable by the service user)."
    )
    known = set(f["settings"])
    rows = []
    used = set()
    for group, names in CONFIG_GROUPS:
        cells = []
        for n in names:
            if n not in known:
                continue
            used.add(n)
            tag = ' <span class="tag secret">secret</span>' if n in SECRET else ""
            cells.append(f"<code>{n.upper()}</code>{tag}")
        rows.append([group, Raw(", ".join(cells))])
    rest = [n for n in f["settings"] if n not in used]
    if rest:
        rows.append(["Other", Raw(", ".join(f"<code>{n.upper()}</code>" for n in rest))])
    d.table(["Group", "Variables"], rows, widths=["20%", "80%"], cls="compact")
    d.p(
        "Other secrets on disk: the Firebase service-account JSON (path in FIREBASE_CREDENTIALS_FILE), the Insights "
        "HMAC key file (INSIGHTS_HMAC_KEY_FILE, chmod 600, created once), <code>~/.cloudflared/</code> tunnel "
        "credentials, and the labels app's own config. The app has no secrets compiled in except the public Firebase "
        "client options (<code>firebase_options.dart</code>, project <code>spice-town-ef061</code>)."
    )
