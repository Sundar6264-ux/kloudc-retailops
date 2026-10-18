from doc_core import Raw, code, esc


def render(d, ctx):
    m = ctx["meta"]
    d.h(1, "1", "Overview and glossary")
    d.h(2, "1.1", "What the system is")
    d.p(
        "Spice Town is a South Asian grocery, halal meat and prepared-food store in Vernon, CT that sells through "
        "<b>Toast POS / Toast Retail</b>. This system is the store's own operations platform. It reads everything "
        "the store sells from Toast, keeps a day-by-day copy of the inventory, and works out what to order, from "
        "whom and how much. It also runs the store's paperwork and team work: purchase orders, deliveries, "
        "invoices, inventory clean-up (Fix Issues), daily checklists, team tasks, chat, announcements, marketing "
        "flyers, push notifications and sales insights."
    )
    d.p(
        "There are three ways in, all served by <b>one FastAPI process on one Mac mini</b> in the store's office:"
    )
    d.ul(
        [
            "<b>Website</b> (React single-page app) at <code>https://ops.spicetown.shop</code>, signed in with an httpOnly "
            "session cookie. Admins use it for setup and for the larger pages.",
            "<b>Mobile app</b> (Flutter, iOS and Android, bundle <code>com.spicetown.app</code>, version "
            f"<code>{esc(m['app_version'])}</code>), signed in with a bearer token kept in the Keychain / Keystore, "
            "optionally unlocked with Face ID. Five tabs: Home, Store, Scan, Team, More.",
            "<b>Public checklist links</b> (<code>/c/&lt;slug&gt;-&lt;code&gt;</code>) for part-time staff without an "
            "account: one checklist form, no login, heavily rate limited.",
        ]
    )
    d.p(
        "A separate <b>labels app</b> (Flask, <code>labels.spicetown.shop</code>) prints shelf labels on a Brother "
        "QL-810W. It shares the dashboard's login by reading the dashboard's <code>user_sessions</code> table "
        "read-only. Nothing in this system writes to Toast: every Toast call is a read (the API scopes granted have "
        "no write and no purchasing access)."
    )
    d.h(2, "1.2", "System context")
    d.fig(
        r"""
flowchart TB
  subgraph People
    direction LR
    ADM(["Owner / admins"])
    STF(["Staff"])
    PT(["Part-time staff<br/>no account"])
    REV(["App Review<br/>demo login"])
  end
  subgraph Clients
    direction LR
    WEB["Website<br/>React SPA"]
    APP["Mobile app<br/>Flutter iOS / Android"]
    PUB["Public checklist page<br/>/c/slug-code"]
  end
  ADM --> WEB
  ADM --> APP
  STF --> APP
  STF --> WEB
  PT --> PUB
  REV --> APP
  WEB --> CF
  APP --> CF
  PUB --> CF
  CF["Cloudflare edge: TLS, DNS<br/>ops / labels .spicetown.shop"]
  CF -->|"cloudflared tunnel"| API
  CF --> LBL
  subgraph MAC["Mac mini in the store office"]
    direction LR
    API["FastAPI / uvicorn :8000<br/>API + website + schedulers"]
    DB[("SQLite<br/>spicetown.db")]
    LBL["Labels app :8080"]
    PRN["Brother QL-810W<br/>label printer"]
  end
  API <--> DB
  LBL -. "read-only sessions" .-> DB
  LBL --> PRN
  subgraph TOASTG["Toast (read only)"]
    direction TB
    TOAST["Standard API<br/>orders, menus, stock, labor"]
    ERA["Analytics API (ERA)<br/>reports"]
  end
  subgraph MSG["Messages out"]
    direction TB
    WHAPI["Whapi<br/>WhatsApp groups"]
    GMAIL["Gmail SMTP<br/>Toast inbox, vendor POs"]
    FCM["Firebase FCM<br/>to APNs / Android"]
  end
  ANT["Anthropic API<br/>Ask bot, digest"]
  TOAST ~~~ ERA
  WHAPI ~~~ GMAIL ~~~ FCM
  API --> TOAST
  API --> ERA
  API --> WHAPI
  API --> GMAIL
  API --> FCM
  API --> ANT
""",
        "System context: people, clients, the one server, and every outside system it talks to (push notifications return to phones through Firebase - section 7.5).",
    )
    d.table(
        ["Outside system", "What it is used for", "Direction"],
        [
            ["Toast Standard API (ws-api.toasttab.com)", "Orders (sales sync, open checks, live today), menus (barcodes, prices, categories), stock (live on-hand), labor employees", "Read only"],
            ["Toast Analytics API (ERA)", "Insights reports, sales-accuracy re-pull (day totals), Loss watch / Odd checks check data", "Read only, own credentials"],
            ["Whapi Cloud", "WhatsApp: invoice notices, checklist summaries, marketing flyers, (PO WhatsApp, off)", "Send; list groups"],
            ["Gmail SMTP (SSL 465)", "Invoice PDFs to Toast's invoice inbox; Send PO to vendors with CC", "Send"],
            ["Firebase Cloud Messaging", "Every push notification (APNs on iOS)", "Send"],
            ["Anthropic API", "Ask Inventory Bot (Haiku 4.5, escalates to Sonnet 5), Weekly Digest (Opus 5)", "Request / answer"],
            ["Cloudflare (Tunnel + DNS)", "Public HTTPS for ops / labels / flowers subdomains, no open ports", "Inbound"],
            ["Tailscale", "Dev server only (tailnet-only HTTPS :8443 to :8001)", "Dev only"],
            ["CUPS + Brother QL-810W", "Label printing (labels app)", "Local"],
            ["GitHub", "Code (spicetownbackend org, private); labels catalog CSV via a GitHub Action", "Pull"],
        ],
        widths=["30%", "55%", "15%"],
    )

    d.h(2, "1.3", "Repositories, versions and the state this document describes")
    d.table(
        ["Item", "Value"],
        [
            ["Website + backend repo", Raw(f"<code>~/spicetown-dev/website</code>, branch <code>ops-fixes</code>, HEAD <code>{esc(m['web_head'])}</code>")],
            ["Mobile repo", Raw(f"<code>~/spicetown-dev/mobile</code>, branch <code>ops-fixes</code>, HEAD <code>{esc(m['app_head'])}</code>, pubspec <code>{esc(m['app_version'])}</code>")],
            ["Production checkout", Raw("<code>~/spicetown-backend</code> (branch main) - live at <code>8dd644e</code>, DB at alembic <code>0056</code> when this was written")],
            ["This document describes", Raw(f"the code after the Oct 15 batch (alembic head <code>{esc(m['alembic_head'])}</code>). Deploy steps: <code>~/spicetown-dev/DEPLOY_OCT15.md</code>")],
            ["Size of the code", Raw(f"{m['routes']} HTTP routes, {m['tables']} tables (+ alembic_version), {m['modules']} backend modules, {m['web_pages']} website pages / shells, {m['app_screens']} app screen files, {m['repos']} app repositories")],
            ["Generated", esc(m["generated"])],
        ],
        widths=["28%", "72%"],
    )
    d.note(
        "How this document was made: the route list, table list, column lists, notification catalog, demo allowlist, "
        "configuration names, migration chain, module purposes, the website page &rarr; endpoint table and the app "
        "screen &rarr; repository &rarr; endpoint tables are <b>generated from the code</b> by "
        "<code>docs/architecture/build/</code> (see its README). Row counts come from a read-only query of the "
        "production database. Everything else was written from the code, the batch contracts in <code>docs/</code> "
        "and the discovery reports, with the code winning where they disagree. No secret values appear anywhere."
    )

    d.h(2, "1.4", "Glossary")
    terms = [
        ("Business date", "Toast's store day. The store's day closes out at 04:00 America/New_York; jobs that need yesterday's final numbers run after 04:00."),
        ("Selection / modifier", "One line item on a Toast check (a selection). A modifier (spice level, add-on) is itself a selection nested under its parent; stored with parent_selection_guid. 'Real sold items' always filter parent_selection_guid IS NULL."),
        ("Inventory snapshot", "One row per item per day in inventory_snapshots, from the Toast 'retail items' CSV/XLSX upload or (when switched on) built from the Toast API at 04:05. snapshot_date D holds the opening count of day D."),
        ("Container row", "A second bare row of the same item tracking stock kept in the storage container; paired with the priced store row (store_container_pairs)."),
        ("Supplier / vendor", "Free text from Toast's supplier field; can list several vendors separated by ';'."),
        ("Reorder candidate / tier", "An item whose forecast demand over the vendor lead time is above on-hand. Vendors are grouped with an urgency tier (critical, soon, ...)."),
        ("Critical vendor", "A vendor with at least one critical item; shown on Home's red Critical tile and in the Morning summary. Admins can Close / Skip this week / Remind me later (critical_vendor_dismissals)."),
        ("Supplier Projection ('New order')", "Per-vendor demand projection with an availability-adjusted forecast and a suggested order quantity."),
        ("Order due", "A date on the Vendor Calendar when an order must be placed for the next delivery (from vendor_schedules weekday + lead days)."),
        ("PO cart", "One shared store cart of purchase-order lines (po_cart_items), grouped by vendor and by PO section."),
        ("PO section", "The aisle-like heading a product prints under on the vendor PO PDF (automatic from category, editable with po_section_rules)."),
        ("Placed order", "A row in placed_orders: an order sent or recorded (status pdf / placed; source cart / outside / sent)."),
        ("Fix Issues", "Inventory-hygiene lists (missing / invalid barcodes, duplicates, dead stock, slow review, negative, refill) that staff close with a reason."),
        ("Lead / lead task", "The person responsible for a PO section or vendor's Fix Issues items; each lead gets one daily Team task, titled 'Fix Issues: N left of M'."),
        ("Team change feed", "team_changes: every Team write appends a row; clients ask GET /api/team/updates?since=<cursor> for what changed."),
        ("Long-poll", "GET /api/team/updates with wait=1..20: the server holds the request until something the caller can see changes, or the wait ends."),
        ("Feature key", "One of 20 admin-grantable access keys (features.py). Admins have every key."),
        ("hide_sales", "Per-user flag: no dollar figures, no Items Sold, no sales-only Insights."),
        ("can_assign", "Per-user flag: a non-admin may assign tasks to other people."),
        ("Demo login", "The App Review account (app_store_review, users.id 8): sees what its features allow, may only write to its own sandbox; nothing it does leaves the server."),
        ("Morning summary", "One 09:00 push per admin listing critical vendors, orders due, loss watch, Fix Issues items back, inventory upload, yesterday's sales and open checks."),
        ("Quiet hours", "22:00-07:00 store time: scheduled alerts are not sent."),
        ("Store defaults", "Admin-set default on/off per alert per role (staff / admin), stored in setting_changes; a person's own choice always wins."),
        ("Report cache", "In-process cache for the heavy Today report and Vendor Calendar counts (Oct 15), cleared by any committed write to the tables they read."),
        ("ERA", "Toast's Analytics API ('enterprise reporting'): a report is requested (POST) and fetched later (GET) - two steps."),
        ("Whapi", "The WhatsApp Business gateway used to post to the store's WhatsApp groups (plan: sandbox, 5 chats)."),
    ]
    d.table(["Term", "Meaning"], [[Raw(f"<b>{esc(a)}</b>"), b] for a, b in terms], widths=["24%", "76%"], cls="compact")
