import re

from doc_core import Raw, esc

PAGE_FILE = {
    "/": "Overview.jsx", "/invoices": "InvoiceSubmissionPage.jsx", "/daily-checklists": "DailyChecklistsPage.jsx",
    "/open-orders": "OpenOrdersPage.jsx", "/items-sold": "ItemsSoldPage.jsx", "/reorder": "ReorderPage.jsx",
    "/supplier-projection": "SupplierProjectionPage.jsx", "/vendor-calendar": "VendorCalendarPage.jsx",
    "/delivery-review": "DeliveryReviewPage.jsx", "/transfer-review": "(redirect to /delivery-review)",
    "/inventory-reports": "InventoryReportsPage.jsx", "/fix-issues": "FixIssuesPage.jsx",
    "/reconciliation": "ReconciliationPage.jsx", "/po-sections": "PoSectionsPage.jsx",
    "/purchase-order": "PurchaseOrderPage.jsx", "/placed-orders": "PlacedOrdersPage.jsx",
    "/vendor-contacts": "VendorContactsPage.jsx", "/insights/daily": "insights/DailyComparePage.jsx",
    "/insights/loss-watch": "insights/LossWatchPage.jsx", "/insights/odd-checks": "insights/OddChecksPage.jsx",
    "/insights/prep-forecast": "insights/PrepForecastPage.jsx", "/insights/menu-trends": "insights/MenuPerformancePage.jsx",
    "/insights/add-ons": "insights/AddonsPage.jsx", "/insights/returning-customers": "insights/ReturningCustomersPage.jsx",
    "/insights/channels": "insights/ChannelsPage.jsx", "/insights/check-trends": "insights/CheckTrendsPage.jsx",
    "/insights/busy-hours": "insights/HeatmapPage.jsx", "/insights/best-sellers": "insights/MenuPerformancePage.jsx",
    "/team/tasks": "TeamTasksPage.jsx", "/team/chat": "TeamChatPage.jsx", "/team/templates": "TeamTemplatesPage.jsx",
    "/team/announcements": "TeamAnnouncementsPage.jsx", "/team/moderation": "TeamModerationPage.jsx",
    "/flyers": "FlyersPage.jsx", "/users": "UsersPage.jsx", "/teams": "TeamsPage.jsx",
    "/checklists/admin": "ChecklistAdminPage.jsx", "/issue-leads": "IssueLeadsPage.jsx", "/inventory-api": "InventoryApiPage.jsx",
}
EXTRA_ROUTES = [
    ("/help", "HelpPage.jsx", "help", "Help with a Glossary tab"),
    ("/glossary", "HelpPage.jsx (tab=glossary)", "everyone", "Glossary tab (whole page without the help grant)"),
    ("/notifications", "NotificationSettingsPage.jsx", "everyone", "Own push switches; admins: store defaults, volume"),
    ("/change-password", "ChangePasswordPage.jsx", "everyone", ""),
    ("/login", "LoginPage.jsx", "public", "?next= returns to an invoice link"),
    ("/no-access", "NoAccessPage.jsx", "everyone", "Landing when nothing is granted"),
    ("/insights", "(redirect)", "sales_insights", "Daily compare, or Prep forecast for hide_sales"),
    ("/insights/menu", "(redirect)", "sales_insights", "Old Menu performance address"),
    ("/categories", "PoSectionsPage.jsx", "category_edits", "Old address of PO sections"),
    ("/price-changes, /barcode-reports", "(redirect)", "inventory_reports", "Old addresses -> Inventory Reports"),
    ("/c/<slug>-<code>", "PublicChecklistPage.jsx (main.jsx)", "public link", "Served by FastAPI with a strict CSP"),
]


def parse_feature_routes(src):
    out = []
    for m in re.finditer(r"\{\s*path:\s*\"([^\"]+)\",\s*label:\s*\"([^\"]+)\",\s*feature:\s*(null|\"[^\"]+\")([^}]*)\}", src):
        path, label, feat, rest = m.groups()
        flags = [k for k in ("adminOnly", "salesOnly", "noDemo", "retired", "everyone", "admin") if re.search(rf"\b{k}:\s*true", rest)]
        out.append((path, label, feat.strip('"') if feat != "null" else "-", flags))
    return out


def render(d, ctx):
    cm = ctx["clientmap"]
    d.h(1, "6", "Website")
    d.h(2, "6.1", "Stack and entry points")
    d.ul(
        [
            "React 18 + Vite 5 + react-router-dom 7 (<code>HashRouter</code>), plain CSS (<code>index.css</code> + a few per-page files). No global state library; each page holds its own state.",
            "<code>main.jsx</code>: for <code>/c/</code> paths renders only <code>PublicChecklistPage</code>; otherwise <code>App.jsx</code>: <code>AuthProvider</code> &rarr; <code>GlossaryProvider</code> &rarr; <code>HashRouter</code> &rarr; <code>AppShell</code> (Sidebar + routes + floating <code>CartButton</code> + Ask-bot <code>ChatWidget</code>).",
            "<code>src/api.js</code> (about 2,050 lines): one exported function per endpoint. <code>request()</code> sends <code>credentials: 'include'</code> (the session cookie), turns a non-2xx answer into an Error with <code>status</code> and <code>body</code>, and on any 401 (except login) dispatches <code>auth:unauthorized</code>, which sends the user to the login page. Uploads with progress use <code>XMLHttpRequest</code>.",
            "<code>AuthContext</code> loads <code>GET /api/auth/me</code> once: <code>hasFeature(key)</code> (admins: true; Items Sold false for hide_sales), <code>canSeeSales</code>, <code>user.demo</code>.",
            "<code>src/featureRoutes.js</code> is the single source for which route needs which feature, the landing page order and the sidebar (<code>NAV_GROUPS</code>, <code>buildSidebar</code>, <code>routeVisible</code>, <code>footerLinks</code>). A group with exactly one visible link renders as that plain link.",
            "Legacy: <code>@capacitor/*</code> packages, <code>mobileAuth.js</code> and a <code>build:mobile</code> script remain from the retired Capacitor wrapper; the Flutter app replaced it.",
        ]
    )

    d.h(2, "6.2", "Route map")
    try:
        src = open(ctx["web_src"] + "/featureRoutes.js").read()
        routes = parse_feature_routes(src)
    except OSError:
        routes = []
    rows = []
    for path, label, feat, flags in routes:
        rows.append([Raw(f"<code>/#{esc(path)}</code>"), label, Raw(f"<code>{esc(feat)}</code>"), ", ".join(flags), PAGE_FILE.get(path, "")])
    for path, page, feat, note in EXTRA_ROUTES:
        rows.append([Raw(f"<code>{esc(path)}</code>"), note or "-", Raw(f"<code>{esc(feat)}</code>"), "", page])
    d.p("Generated from <code>FEATURE_ROUTES</code> in <code>featureRoutes.js</code>, plus the routes declared only in <code>App.jsx</code>. Flags: adminOnly / admin = admins only, salesOnly = hidden for hide_sales, noDemo = hidden for the App Review login, retired = no menu entry.")
    d.table(["Route", "Menu label / note", "Feature", "Flags", "Page component (src/pages)"], rows, widths=["24%", "22%", "17%", "12%", "25%"], cls="compact small")

    d.h(2, "6.3", "Sidebar")
    d.fig(
        r"""
flowchart LR
  S["Sidebar<br/>logo -> Overview"] --> O["Overview"]
  S --> I["Invoices"]
  S --> DC["Daily Checklists"]
  S --> T["Team"]
  S --> OR["Ordering"]
  S --> ST["Stock"]
  S --> SI["Sales and Insights<br/>('Sales' without Insights)"]
  S --> SU["Setup (admins)"]
  S --> F["Footer"]
  T --> T1["Tasks"] & T2["Chat"] & T3["News"] & T4["Flyers"]
  OR --> O1["Reorder Candidates"] & O2["New order"] & O3["Vendor Calendar"] & O4["Placed Orders"] & O5["Delivery Review"]
  ST --> S1["Fix Issues"] & S2["Inventory Reports"] & S3["Reconciliation"]
  SI --> I1["Items Sold"] & I2["Open Checks"] & I3["Daily compare, Loss watch,<br/>Odd checks, Prep forecast,<br/>Menu trends, Add-ons,<br/>Returning customers"] & I4["Also in Toast (collapsed):<br/>Sales channels, Check size,<br/>Busy hours, Best and worst"]
  SU --> U1["Users, Teams, Recurring tasks,<br/>Reported messages, Checklist builder,<br/>PO sections, Vendor contacts,<br/>Category and vendor leads,<br/>Inventory from Toast"]
  F --> F1["Label Scanner (labels app)<br/>Help or Glossary<br/>Notifications<br/>Change password<br/>Log out"]
""",
        "Website menu after the Oct 15 clean-up (each person sees only what their grants allow). The PO cart opens from the floating cart button.",
    )

    d.h(2, "6.4", "Page to endpoint map")
    d.p(
        "Generated by following each page's imports (never into another page) to the <code>api.js</code> functions it "
        "uses. Shell-wide calls (<code>/api/auth/me</code>, login / logout, <code>/api/glossary</code>) are left out; "
        "the sidebar adds <code>GET /api/team/bootstrap</code> and <code>GET /api/team/updates</code> on every page. "
        "<code>{}</code> stands for a path parameter."
    )
    rows = []
    for k, v in sorted(cm["web"].items()):
        eps = v["endpoints"]
        rows.append([Raw(f"<code>{esc(k.replace('pages/', ''))}</code>"), Raw("<br/>".join(f"<code>{esc(e)}</code>" for e in eps) or "-")])
    d.table(["Page / shell file", "Endpoints"], rows, widths=["28%", "72%"], cls="compact small endpoints")

    d.h(2, "6.5", "State and sync patterns")
    d.table(
        ["Pattern", "Where", "How"],
        [
            ["Load on mount + manual refresh", "Most pages", "useEffect calls api.js, local useState; buttons re-fetch. No client cache beyond the page."],
            ["Team change feed (one shared loop)", "useTeamSync / teamSyncEngine.js: sidebar badges, Tasks page, task drawer", "One cursor loop for the whole site. While the Tasks page or a drawer is open: long-poll GET /api/team/updates?since=&wait=20&include=tasks. Other pages: badge poll every badge_poll_interval_s (120 s) with wait=0. Hidden tab asks nothing, then once when shown. Errors back off 2 s .. 60 s. An empty answer under 1.5 s twice = older server, plain polling."],
            ["Patch from the feed", "Tasks page, drawer", "Rows in the feed's tasks[] are patched at once; a changes.task entry triggers a debounced (250 ms) reload; changes.team reloads bootstrap (member counts)."],
            ["Write answers applied directly", "Task drawer", "Every task write returns task_out; the drawer shows it without another GET. A 409 carries the current task, shown with the error."],
            ["Chat polling", "TeamChatPage", "Change-feed tick every 5-10 s; open conversation polls messages every chat_poll_interval_s (5 s)."],
            ["Announcements", "TeamAnnouncementsPage", "Interval of poll_interval_s (30 s)."],
            ["Job status", "Overview JobStatusWidget", "GET /api/jobs/status every 60 s."],
            ["Flyer send status", "FlyerDrawer / SendFlyerModal", "GET /api/flyers/{id} every 3 s while a send is waiting or going."],
            ["Invoice status", "Invoices", "GET /api/invoices/{id} after submit until WhatsApp / email settle."],
            ["Insights staleness", "insightsModel.staleText", "Shows the server's note (why a day is missing and when it arrives)."],
        ],
        widths=["20%", "26%", "54%"],
        cls="compact small",
    )
    d.fig(
        r"""
sequenceDiagram
  autonumber
  participant B1 as Browser A (Tasks page)
  participant API as FastAPI (one process)
  participant DB as SQLite
  participant B2 as Browser B / app
  B1->>API: GET /api/team/updates?since=214&wait=20&include=tasks
  Note over API: closes its DB session,<br/>awaits change_version (no thread held)
  B2->>API: POST /api/team/tasks/56/complete
  API->>DB: UPDATE tasks ... WHERE status='open'<br/>task_events + team_changes (one commit)
  DB-->>API: after_commit hook -> notify_changed()
  API-->>B2: task_out (done)
  API->>DB: re-run feed query for A (new short session)
  API-->>B1: cursor 215, changes.task [56 updated], tasks [task 56], badges
  B1->>B1: patch row 56 (Done by ...)
  B1->>API: next long-poll since=215
""",
        "Live task sync on the website (measured on a prod copy: a step tick showed in the other browser in 160 ms, 'Done by' in 175 ms).",
    )
