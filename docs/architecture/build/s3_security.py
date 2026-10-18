from collections import defaultdict

from doc_core import Raw, code, esc

# Where each feature key shows up in the clients, and what hide_sales / the demo login change.
FEATURE_NOTES = {
    "overview": ("Overview page; Upload inventory (web Overview, app Store › Stock); weekly digest; job status", "Overview sales tiles hidden", "Granted; digest is a canned answer"),
    "open_orders": ("Sales › Open Checks; Home Today 'Open checks' tile", "total_amount nulled", "Granted (counts, no $)"),
    "items_sold": ("Sales › Items Sold; Overview sales tiles", "Removed from /me; endpoints 403", "Removed (demo has hide_sales)"),
    "reorder_candidates": ("Ordering › Reorder Candidates; Home Critical tile", "No $ (demand numbers stay)", "Granted; Critical actions refused (admin only)"),
    "supplier_projection": ("Ordering › New order (Supplier Projection); may add to cart", "-", "Granted; cart writes refused"),
    "vendor_calendar": ("Ordering › Vendor Calendar; Home Orders due tile", "-", "Granted; writes refused"),
    "purchase_order_cart": ("PO cart (floating button / Store), Placed Orders, Vendor contacts, Send PO", "-", "Granted read-only; Vendor contacts hidden"),
    "delivery_review": ("Ordering › Delivery Review (suggested + manual deliveries)", "-", "Granted; writes refused"),
    "transfer_review": ("Retired page (API kept for old app builds)", "-", "-"),
    "inventory_reports": ("Stock › Fix Issues and Inventory Reports; sales CSV/PDF export", "Sales export 403; $ fields nulled", "Granted; closes refused"),
    "reconciliation": ("Stock › Reconciliation (purchase log, report, invoice gaps, worked example)", "-", "Granted; writes refused"),
    "category_edits": ("Setup › PO sections (old 'Product categories' paths)", "-", "Not granted (and hidden for demo)"),
    "invoice_submission": ("Invoices list; Home Upload invoice; Today 'Invoices' tile", "-", "Granted; submits saved but never sent"),
    "daily_checklists": ("Daily Checklists; Home checklist card", "-", "Granted; submits never sent to WhatsApp"),
    "ask_bot": ("Ask Inventory Bot (web chat widget, app Store › Stock)", "No items-sold tool", "Granted; fixed answer, no Anthropic call"),
    "team_tasks": ("Team › Tasks (+ Recurring tasks for admins)", "-", "Granted; sandboxed to its own team"),
    "team_chat": ("Team › Chat (+ Reported messages for admins)", "-", "Granted; only people sharing a team"),
    "marketing_flyers": ("Team › Flyers", "-", "Always refused (require_flyers)"),
    "sales_insights": ("Sales & Insights pages (Loss watch / Odd checks admin only)", "Sales-only pages 403; heatmap counts only", "Not granted"),
    "help": ("Help (with Glossary tab)", "-", "Granted"),
}


def render(d, ctx):
    f = ctx["facts"]
    d.h(1, "3", "Security and access")
    d.h(2, "3.1", "Authentication")
    d.p(
        "There is one user table and one session table for both clients. A session is an opaque random token "
        "(<code>secrets.token_urlsafe(32)</code>) stored as the primary key of <code>user_sessions</code> with a "
        "14-day expiry (SESSION_LIFETIME_DAYS). Passwords are bcrypt hashes. There are no JWTs: deleting the row "
        "revokes a session at once (an admin password reset deletes every session of that user). There is no "
        "'forgot password' - only an admin sets a password; anyone can change their own with the current one."
    )
    d.table(
        ["", "Website", "Mobile app", "Labels app"],
        [
            ["Sign in", "POST /api/auth/login", "POST /api/auth/mobile-login", "uses the dashboard session"],
            ["Credential carried", "httpOnly, Secure, SameSite=Lax cookie session_token, Domain .spicetown.shop", "Authorization: Bearer <token> (token returned in the body, never readable by web JS)", "Cookie session_token (the app sends its bearer token as this cookie header)"],
            ["Where it is kept", "Browser cookie jar", "flutter_secure_storage (iOS Keychain / Android Keystore), optionally unlocked by Face ID / biometrics", "-"],
            ["Validated by", "auth.get_current_user: cookie or bearer -> user_sessions row -> not expired -> users row", "same", "labels dashboard_auth.py reads user_sessions read-only"],
            ["Sign out", "POST /api/auth/logout deletes the row and clears the cookie (same domain)", "logout + DELETE /api/notifications/devices (unregister this phone) + wipe secure storage", "-"],
            ["Any 401", "api.js fires auth:unauthorized -> back to login", "AuthInterceptor signs out (except on credential paths)", "-"],
        ],
        widths=["16%", "30%", "34%", "20%"],
        cls="compact",
    )
    d.fig(
        r"""
sequenceDiagram
  autonumber
  participant U as Person
  participant APP as App (AuthController)
  participant SS as Secure storage
  participant BIO as Face ID (local_auth)
  participant API as FastAPI
  participant DB as SQLite
  Note over APP: Cold start - /splash
  APP->>SS: read session_token
  alt token stored and Face ID on
    APP->>BIO: authenticate()
    BIO-->>APP: ok
  end
  alt token stored
    APP->>API: GET /api/auth/me (Bearer)
    API->>DB: user_sessions + users
    API-->>APP: id, username, is_admin, allowed_features, can_assign, hide_sales, demo
    APP->>APP: authenticated - go to /home
  else no token or 401
    APP->>APP: unauthenticated - go to /login
    U->>APP: username + password
    APP->>API: POST /api/auth/mobile-login
    API->>DB: bcrypt check, insert user_sessions (14 days)
    API-->>APP: token, expires_at + me fields
    APP->>SS: store token
    APP->>API: POST /api/notifications/devices (FCM token)
  end
""",
        "App sign-in, Face ID unlock and session check.",
    )

    d.h(2, "3.2", "How one request is authorised")
    d.fig(
        r"""
flowchart TB
  R["Request"] --> PUB{"Public route?"}
  PUB -- "yes: login, health, app/config,<br/>public checklist, /c, unregister" --> OK1["Own limits,<br/>no login"]
  PUB -- no --> SES{"Cookie or Bearer token<br/>-> user_sessions row,<br/>not expired?"}
  SES -- no --> E401["401"]
  SES -- yes --> DG{"Demo login and<br/>a write not allowed?"}
  DG -- yes --> E403d["403 DEMO_READ_ONLY"]
  DG -- no --> GATE{"Route gate passes?<br/>admin / feature key /<br/>any of keys / sales access"}
  GATE -- no --> E403["403"]
  GATE -- yes --> H["Handler + service rules:<br/>task readers, chat members,<br/>money nulled for hide_sales,<br/>demo rows hidden"]
""",
        "Authorisation pipeline for every API request.",
    )

    d.h(2, "3.3", "Roles and flags")
    d.table(
        ["Role / flag", "Where stored", "Effect"],
        [
            ["Admin", "users.is_admin", "Every feature key; every require_admin route (Users, Teams, Setup pages, order actions, critical dismissals, store defaults, moderation, invoice trash...). hide_sales is ignored for admins."],
            ["Staff", "users.allowed_features (JSON list of keys)", "Only the granted feature keys. /api/auth/me returns the effective list; both clients build menus from it."],
            ["can_assign", "users.can_assign", "A non-admin may assign tasks to other people and sees 'My team today' for their own teams."],
            ["hide_sales", "users.hide_sales", "Items Sold removed from /me, sales-only endpoints 403, money fields nulled on mixed responses, no sales section in the digest, Ask bot without its sales tool."],
            ["Demo (App Review)", "DEMO_USERNAMES (app_store_review, appreview)", "Read what its features allow; writes only via the allowlist (3.5); nothing leaves the server (no WhatsApp, email, Anthropic, label print, push outside its teams); excluded from the Everyone team; its rows hidden from real staff."],
            ["Public link user", "checklist_public_links", "One checklist's form, submit and photo upload; no account."],
        ],
        widths=["16%", "24%", "60%"],
        cls="compact",
    )

    d.h(2, "3.4", "Feature keys")
    by_feat = defaultdict(int)
    for r in f["routes"]:
        for g in r["gate"]:
            if g.startswith("feat:"):
                by_feat[g[5:]] += 1
            elif g.startswith("any:"):
                for k in g[4:].split(","):
                    by_feat[k] += 1
    by_feat["purchase_order_cart"] += sum(1 for r in f["routes"] if "require_po_sender" in r["gate"])
    by_feat["marketing_flyers"] += sum(1 for r in f["routes"] if "require_flyers" in r["gate"])
    by_feat["team_tasks"] += sum(1 for r in f["routes"] if "tasks_user" in r["gate"])
    by_feat["team_chat"] += 0
    rows = []
    for feat in f["features"]:
        k = feat["key"]
        where, hs, demo = FEATURE_NOTES.get(k, ("", "", ""))
        rows.append([Raw(f"<code>{esc(k)}</code><br/><span class='muted'>{esc(feat['label'])}</span>"), str(by_feat.get(k, 0)), where, "always", "if granted", hs, demo])
    d.p(
        f"{len(f['features'])} grantable keys (<code>app/features.py</code>). '#' counts the API routes gated on the "
        "key (directly, through require_any_feature, or through a wrapper such as require_po_sender / require_flyers / "
        "tasks_user). Announcements need no key (everyone receives what is sent to them); Change password, Notifications "
        "and the Glossary are for everyone."
    )
    d.table(
        ["Feature key", "#", "Used by (web / app)", "Admin", "Staff", "hide_sales", "Demo login (id 8)"],
        rows,
        widths=["21%", "5%", "28%", "7%", "8%", "14%", "17%"],
        cls="compact small",
    )

    d.h(2, "3.5", "Demo guard (App Review login)")
    allow = f["allowlist"]
    d.p(
        "The App Review login must be able to look around but never change real data or reach anyone. "
        "<code>app/demo_guard.py</code> is an app-wide FastAPI dependency: for a demo session every POST / PUT / "
        f"PATCH / DELETE is refused with <code>403 DEMO_READ_ONLY</code> unless its (method, path) is on an allowlist of "
        f"<b>{len(allow)}</b> entries, some with an ownership check. New routes are refused by default; "
        "<code>tests/test_demo_guard.py</code> enumerates <code>app.routes</code> to keep it that way. Every Oct 15 "
        "write route (steps editing, bulk, prefs reset, store defaults) is deliberately not on the list."
    )
    d.fig(
        r"""
flowchart TB
  A["Write request<br/>POST PUT PATCH DELETE"] --> C{"Demo login?"}
  C -- no --> PASS["Route runs"]
  C -- yes --> D{"(method, path)<br/>on the allowlist?"}
  D -- no --> R["403 DEMO_READ_ONLY"]
  D -- yes --> F{"Ownership check<br/>if any: own invoice,<br/>own message, assigned task,<br/>own sandbox"}
  F -- fails --> R
  F -- passes --> PASS2["Route runs, effects stay inside:<br/>saved not sent, canned answers,<br/>pushes only to its teams"]
""",
        "Demo write guard decision.",
    )
    rows = []
    for m, p in sorted(allow, key=lambda x: x[1]):
        rows.append([m, p])
    d.table(["Method", "Allowed path for the demo login"], rows, widths=["12%", "88%"], cls="compact small")

    d.h(2, "3.6", "Public links, invoice links and HTTP security headers")
    d.ul(
        [
            "<b>Public checklist link</b> <code>{PUBLIC_BASE_URL}/c/&lt;slug&gt;-&lt;4-digit code&gt;</code> plus a 22-character token for the API. An admin turns it on per checklist, optionally with a list of allowed names and a per-hour cap (default 6, max 60); rotate gives a new code. It reaches exactly one checklist's form, submit and photo upload.",
            "Page response headers: <code>Cache-Control: no-store</code>, <code>Referrer-Policy: no-referrer</code>, <code>X-Robots-Tag: noindex, nofollow</code>, <code>X-Content-Type-Options: nosniff</code>, <code>X-Frame-Options: DENY</code> and CSP <code>default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; font-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'</code>. The 'not active' page uses <code>default-src 'none'</code>.",
            "<b>Invoice links</b> (<code>/public/invoices/{token}</code>) used to be public-by-unguessable-token; they now need a dashboard login (feature invoice_submission). A logged-out tap is redirected to <code>/#/login?next=...</code>.",
            "<b>Team media</b> (<code>/api/team/media/{token}</code>) and flyer files need a login and pass an owner-specific access check (task readers, channel members, announcement recipients).",
            "The main website sets no CSP of its own; it is same-origin and loads no third-party scripts.",
        ]
    )
    d.fig(
        r"""
flowchart TB
  P["GET /c/slug-code"] --> IP{"Under the per-IP and<br/>failed-lookup limits?"}
  IP -- no --> T429["429 'Too many tries' page"]
  IP -- yes --> L{"Link enabled and<br/>code matches?"}
  L -- no --> NF["404 'not active'<br/>failure counted"]
  L -- yes --> IDX["index.html + strict CSP"]
  IDX --> API{"API call within limits?<br/>IP 20 / 10 min, all 600 / 10 min,<br/>link max_per_hour, 40 photos / hour,<br/>body 256 KB"}
  API -- no --> T429
  API -- yes --> SUB["checklist_submissions<br/>via_public_link = true"]
""",
        "Public checklist link guards (services/checklist_public.py).",
        size="small",
    )

    d.h(2, "3.7", "Rate limits, throttles and budgets")
    d.table(
        ["Where", "Limit", "Code"],
        [
            ["Public checklist API", "Per IP 20 / 10 min (API), 60 / 10 min (page, images); all public traffic 600 / 10 min; failed lookups 10 per IP and 200 overall per 10 min (then 429); per link max_per_hour submits; 40 photos per link per hour", "services/checklist_public.py"],
            ["Inventory from Toast 'compare now'", "Once per 120 s", "routers/inventory_api.py"],
            ["Chat pushes", "At most 1 push per minute per channel member", "team/chat_service.py"],
            ["Toast Standard API", "429 back-off and retry in sales sync, open checks and the inventory API client", "services/sales_sync.py, inventory_api.py"],
            ["Toast Analytics (per process)", "POST day/week 9 per min, 55 per hour; long ranges 9 per hour; check / payout / guest 4 per min and 55 per day; GET 25 per min; login 10 per min; Toast's own rate headers and 429s honoured", "services/toast_analytics.py"],
            ["Insights daily budgets", "check 30 and guest 10 calls per rolling 24 h; the newest 2 closed days first, older days only above a reserve (check 4, guest 2)", "services/insights_data.py"],
            ["Task push sweep", "A task push nobody can receive is logged once (recipient_count 0) and never rebuilt", "team/tasks_service.py"],
            ["Login", "No attempt limiter on /login and /mobile-login (known gap)", "routers/auth.py"],
        ],
        widths=["24%", "56%", "20%"],
        cls="compact",
    )

    d.h(2, "3.8", "Known security gaps and decisions")
    d.ul(
        [
            "No login throttling or lockout on the two login routes, although they are on the public internet.",
            "One SQLite file on one machine; backups are manual and on the same disk.",
            "No log rotation; logs can contain usernames, vendor names and push titles (never passwords, tokens or push bodies).",
            "Admin is all-or-nothing (no partial admin).",
            "The labels app trusts the dashboard session table directly (read-only); revoking a dashboard session also revokes labels access.",
        ]
    )
