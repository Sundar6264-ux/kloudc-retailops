from doc_core import Raw, esc


def render(d, ctx):
    d.h(1, "8", "End-to-end flows")
    d.p(
        "Each flow below follows one function from the person's tap to the database, the outside systems and back, and "
        "names what other screens see afterwards. Paths are relative to <code>https://ops.spicetown.shop</code>; "
        "<code>W</code> = website, <code>A</code> = app."
    )

    # ---------------------------------------------------------------- login
    d.h(2, "8.1", "Sign in (website)")
    d.fig(
        r"""
sequenceDiagram
  autonumber
  actor U as Person
  participant W as Website (LoginPage)
  participant API as FastAPI /api/auth
  participant DB as SQLite
  U->>W: username + password
  W->>API: POST /api/auth/login
  API->>DB: SELECT users WHERE lower(username)
  API->>API: bcrypt.checkpw
  alt wrong
    API-->>W: 401 Incorrect username or password
  else right
    API->>DB: INSERT user_sessions (token, 14 days)
    API-->>W: Set-Cookie session_token (httpOnly, Secure, Lax, .spicetown.shop) + me
    W->>API: GET /api/auth/me (cookie)
    API-->>W: effective features, is_admin, hide_sales, demo
    W->>W: buildSidebar, open ?next= or the first page this user can see
  end
""",
        "Website sign-in. The app's sign-in with Face ID is Figure 3 in section 3.1.",
    )
    d.ul(
        [
            "Landing page: <code>firstAccessiblePath</code> walks FEATURE_ROUTES in order (Overview first) and picks the first granted, non-retired page; nothing granted = <code>/no-access</code>.",
            "Admin actions that end sessions: set-password and delete user delete every <code>user_sessions</code> row of that user; their next request gets 401 and both clients return to login.",
        ]
    )

    # ---------------------------------------------------------------- sales sync
    d.h(2, "8.2", "Sales sync, accuracy re-pull and live 'today'")
    d.fig(
        r"""
sequenceDiagram
  autonumber
  participant SCH as Scheduler 04:00 / startup / POST /api/jobs/sales-sync/run
  participant SS as sales_sync.sync_sales_for_date
  participant T as Toast Standard API
  participant DB as SQLite
  participant RC as report_cache
  SCH->>DB: dates in last 14 days without a successful toast_sales_sync run
  loop each missing business date
    SS->>DB: INSERT job_runs (running)
    SS->>T: POST /authentication/v1/authentication/login (token cached)
    SS->>T: GET /orders/v2/ordersBulk?businessDate=YYYYMMDD (all pages, 429 back-off)
    SS->>SS: flatten checks -> selections + nested modifiers (parent_selection_guid)
    SS->>DB: upsert orders by toast_selection_guid, prune selections Toast deleted
    SS->>DB: job_runs success / failed with detail
    DB-->>RC: commit on orders -> cache entries end
  end
  SCH->>SCH: warm_fix_issues_cache(refresh=True)
""",
        "Nightly sales sync (idempotent; a missed night is picked up by the next run or the next restart).",
    )
    d.ul(
        [
            "<b>Accuracy</b>: at 06:30, 12:30, 17:30 and 22:30 <code>sales_accuracy.run_sales_repull</code> asks Toast Analytics for each of the last 14 days' totals, compares them with <code>orders</code>, and re-syncs only the dates that differ (Toast keeps editing closed days: voids, discounts, late payments). Admins see the result on Items Sold (<code>GET /api/sales/accuracy</code>, <code>POST /api/sales/accuracy/run</code>).",
            "<b>Today</b> is never stored: Items Sold 'today' and Overview's sales tile pull Toast live through <code>items_sold.py</code> (one call, shared for 60 s by every page asking).",
            "<b>Revenue rule</b>: a sale counts on its payment date (paid_at), not when it was opened; modifiers count inside their parent.",
            "<b>Consumers</b>: Items Sold, forecast / reorder / projection (through the report cache), Fix Issues 'last sold', weekly digest, Insights compare, delivery suggestions, reconciliation.",
        ]
    )

    # ---------------------------------------------------------------- inventory
    d.h(2, "8.3", "Inventory upload (Toast CSV / XLSX)")
    d.fig(
        r"""
sequenceDiagram
  autonumber
  actor U as Staff
  participant C as W Overview upload or A Upload Inventory
  participant API as POST /api/inventory/upload
  participant P as inventory_parser
  participant DB as SQLite
  participant BG as BackgroundTasks
  U->>C: choose the 'retail items' export
  C->>API: multipart file (XHR with progress)
  API->>DB: job_runs inventory_upload (running)
  API->>P: parse_and_store(file)
  P->>P: match columns by header name (COLUMN_MAP), keep only the mapped columns
  P->>DB: replace inventory_snapshots for snapshot_date (also an API-built one)
  API->>DB: job_runs success + counts, ignored columns
  API-->>C: summary (rows, date, warnings)
  API->>BG: warm_fix_issues_cache, rerun_after_upload
  BG->>BG: before 09:00: compare this CSV with the 04:05 Toast build once
""",
        "Manual inventory upload. The commit ends the report cache, so Reorder / Calendar / Today recompute on the next read.",
    )
    d.h(2, "8.4", "Inventory from Toast (04:05 snapshot, comparison, switch, live stock)")
    d.fig(
        r"""
sequenceDiagram
  autonumber
  participant J as 04:05 inventory_api_snapshot
  participant T as Toast Standard API
  participant IA as services/inventory_api.py
  participant DB as SQLite
  participant N as 09:00 Morning summary
  J->>DB: wait (up to 20 min) for yesterday's sales sync
  IA->>T: GET /stock/v1/inventory
  IA->>T: GET /menus/v2/menus (sku = barcode, plu = supplier, salesCategory)
  IA->>DB: 7 / 30 / 90-day sales computed from orders
  IA->>IA: build rows in the CSV's shape (cost etc. carried from the last CSV)
  alt switch on (setting_changes) and no CSV for today
    IA->>DB: INSERT inventory_snapshots (source = Toast API)
  end
  IA->>DB: job_runs detail.comparison vs today's CSV (qty, price, barcode, category)
  Note over IA: readiness = 7 nights in a row meeting the thresholds
  N->>DB: read readiness streak
  N-->>N: 'Inventory from Toast is ready' line for admins (once per streak)
""",
        "Inventory from Toast. Production has the switch off: the job only records comparisons.",
    )
    d.ul(
        [
            "<b>Switch</b>: admins flip 'Use Toast for inventory' on the Inventory from Toast page (<code>PUT /api/inventory-api/switch</code>); stored with history in <code>setting_changes</code>; effective at once. INVENTORY_API_SNAPSHOTS is only its starting value.",
            "<b>Live stock overlay</b> (switch on): Supplier Projection, Reorder and Fix Issues read Toast's current on-hand on top of the latest snapshot (cached 300 s; a refresh ends the report cache).",
            "<b>Hourly menu re-check</b>: metadata first, full menu only if <code>lastUpdated</code> changed -> <code>toast_menu_items</code> (barcodes for search and Fix Issues).",
            "<b>Compare now</b> (<code>GET /api/inventory-api/compare?refresh=1</code>, once per 120 s) builds the Toast set in memory; it never writes inventory.",
        ]
    )

    # ---------------------------------------------------------------- forecast
    d.h(2, "8.5", "Forecast, Reorder Candidates and Supplier Projection")
    d.fig(
        r"""
sequenceDiagram
  autonumber
  participant C as W Reorder / A Reorder, Home Critical tile
  participant API as GET /api/ordering/reorder-candidates
  participant RC as report_cache
  participant RO as reorder.compute_reorder_candidates
  participant DB as SQLite
  participant CD as critical_dismissals
  C->>API: lookback=30, lead=7 (Home tile adds critical_only=1)
  API->>RC: cached_report(lookback, lead, as_of, method)
  alt cached and valid
    RC-->>API: groups (same object, never mutated)
  else first read after a data change
    RC->>RO: compute once (single-flight)
    RO->>DB: orders (non-modifier) for the window, latest inventory_snapshots + container rows
    RO->>DB: category_overrides, placed_orders + vendor_order_skips (already ordered)
    RO->>RO: per-item demand vs on-hand over lead time, vendor urgency tiers
    RO-->>RC: candidates + vendor groups
  end
  API->>CD: annotate copies with active dismissals (hidden, until)
  API-->>C: vendors, tiers, items (critical_only: critical vendors only, about 53 KB instead of 691 KB)
""",
        "Reorder Candidates through the Oct 15 report cache (57-96 ms cached vs about 1.8 s computed).",
    )
    d.fig(
        r"""
sequenceDiagram
  autonumber
  participant C as W New order / A New order
  participant API as GET /api/ordering/supplier-projection
  participant SP as supplier_projection
  participant DF as demand_forecast
  participant OS as order_suggestion
  participant DB as SQLite
  C->>API: supplier, horizon or custom dates, lookback
  API->>SP: compute_supplier_projection
  SP->>DB: latest snapshot rows for the vendor (multi-vendor supplier field split) + container quantities
  SP->>DF: build_models: availability-adjusted demand (days actually on the shelf)
  DF->>DB: daily snapshots + daily sales per item
  SP->>OS: attach_to_projection: next delivery from vendor_schedules, case size from cart history
  OS-->>SP: suggested qty per item (Need turned into cases)
  API-->>C: items with demand, on hand, need, suggested qty, review flags, live stock line
  C->>C: item drill-down: GET /api/ordering/item-weekly-sales, /similar-items
  C->>C: Add to cart -> section 8.8
""",
        "Supplier Projection ('New order') with the suggested quantity.",
    )

    # ---------------------------------------------------------------- today
    d.h(2, "8.6", "Home Today card (app)")
    d.fig(
        r"""
sequenceDiagram
  autonumber
  participant H as Home screen
  participant P as today_providers
  participant API as FastAPI
  participant RC as report_cache
  H->>P: build (first frame shows cached values or skeletons)
  par heavy reports (kept 2 min on refresh)
    P->>API: GET /api/ordering/reorder-candidates?lookback=30&lead=7&critical_only=1
    API->>RC: cached Today report, critical vendors only
    API-->>P: about 53 KB
    P->>API: GET /api/vendor-calendar?start=today&end=tomorrow
    API->>RC: cached need-to-order counts
    API-->>P: order-due events
  and light reads
    P->>API: GET /api/team/tasks (mine) + change feed
    P->>API: GET /api/orders/open
    P->>API: GET /api/invoices
    P->>API: GET /api/checklists/schemas + GET /api/checklists (today)
  end
  P-->>H: six tiles fill in as each answer arrives
  H->>P: tap Skip / Placed (admin) - applied locally at once, POST in background
""",
        "Home Today card load: every tile loads on its own; nothing blocks the first frame.",
    )
    d.table(
        ["Tile", "Source", "Actions from the tile"],
        [
            ["My tasks", "Team change feed + GET /api/team/tasks (mine, open, due today / overdue)", "Open task; complete"],
            ["Orders due", "GET /api/vendor-calendar?start=today&end=tomorrow (report cache counts)", "Sheet: Order already placed, Skip this week, Other, Undo -> POST / DELETE /api/order-actions (admins)"],
            ["Critical", "GET /api/ordering/reorder-candidates?lookback=30&lead=7&critical_only=1, kept 10 min", "Sheet: Close, Skip this week, Remind me later (POST /api/reorder/critical-dismissals), Order already placed; Show again"],
            ["Open checks", "GET /api/orders/open (today)", "Open Open Checks"],
            ["Invoices", "GET /api/invoices (failed sends, and sent invoices not yet closed in Toast, using each row's close_suggested flag)", "Resend; Close in Toast"],
            ["Checklists", "GET /api/checklists/schemas + GET /api/checklists (today) after the due time", "Open the checklist form"],
        ],
        widths=["14%", "46%", "40%"],
        cls="compact",
    )
    d.ul(
        [
            "Pull-to-refresh / Refresh all keep the two heavy reports (Critical, Orders due) if they loaded under 2 minutes ago; a failed load is always asked again.",
            "Order actions apply on the phone at once (local changes provider); 'Order already placed' from the Critical sheet marks the vendor's next order-due in place without reloading the calendar (Oct 15).",
            "The website shows the same information on Overview (stat tiles, Today's schedule) and on Reorder / Vendor Calendar.",
        ]
    )

    # ---------------------------------------------------------------- critical
    d.h(2, "8.7", "Critical vendors: Skip this week / Remind me later / Close")
    d.fig(
        r"""
sequenceDiagram
  autonumber
  actor A as Admin
  participant APP as App Critical sheet
  participant API as POST /api/reorder/critical-dismissals
  participant CD as critical_dismissals.create_dismissal
  participant RC as report_cache
  participant DB as SQLite
  participant SS as source_sync
  A->>APP: Skip this week (vendor)
  APP->>APP: close the sheet at once, hide vendor (provisional), Undo offered
  APP->>API: kind=skip_week, vendor, reason (background)
  API->>CD: create
  alt kind = close
    CD->>RC: Today report (cached or computed) for the current critical item ids
  else skip_week / snooze
    CD->>RC: only if already cached, else vendor spelling from the cached supplier list
  end
  CD->>DB: INSERT critical_vendor_dismissals (until end of week / later)
  API-->>APP: dismissal (+ warning or null)
  API->>SS: task_sources_changed('order') -> order auto tasks 'Skipped this week (Critical)'
  APP->>APP: swap provisional for the server's dismissal (or put the vendor back if refused)
""",
        "Critical sheet action: 1.6-3.9 s before Oct 15, now 2-3 ms warm (139 ms cold process); the sheet closes 174-223 ms after the tap.",
    )

    # ---------------------------------------------------------------- calendar
    d.h(2, "8.8", "Vendor calendar, order actions and schedule suggestions")
    d.fig(
        r"""
sequenceDiagram
  autonumber
  actor A as Admin
  participant C as W Vendor Calendar / A Vendor Calendar, Home Orders due
  participant API as FastAPI
  participant VC as vendor_calendar
  participant DB as SQLite
  A->>C: set schedule (delivery weekdays, lead days)
  C->>API: POST /api/vendor-calendar/schedules
  API->>DB: vendor_schedules (commit ends report cache)
  C->>API: GET /api/vendor-calendar?start&end
  API->>VC: order-due and delivery events per vendor
  VC->>DB: vendor_order_skips, placed_orders (ordered for delivery), cart lines
  VC->>VC: need_to_order_counts via report_cache (one-pass project_suppliers)
  API-->>C: events with need counts, skipped / ordered state
  A->>C: Order already placed / Skip / Other
  C->>API: POST /api/order-actions {vendor, action, delivery_date, note}
  API->>DB: placed_orders (placed, source outside) or vendor_order_skips
  API->>API: source_sync('order') closes the order task
""",
        "Vendor calendar and today's order actions (Undo: DELETE /api/order-actions/placed/{id} or /skip).",
    )
    d.ul(
        [
            "<b>Schedule suggestions</b>: <code>GET /api/vendor-calendar/schedule-suggestions</code> infers weekdays from invoices, placed orders, the purchase log, snapshots and Toast receipts, with a confidence, and lists vendors without a schedule; Accept / Edit / Dismiss, or <b>Add all</b> (<code>POST .../accept-all</code>). It never changes a schedule by itself.",
            "Vendors have minimum orders: an order-due with fewer than 5 items needing order is not urgent (same rule as Reorder's VENDOR_MIN_ORDER_ITEMS).",
            "Consumers: Home Orders due, the Morning summary's 'Order today / tomorrow' line, the (off) order auto-task rule, Reorder's 'already ordered'.",
        ]
    )

    # ---------------------------------------------------------------- PO
    d.h(2, "8.9", "PO cart -> PDF -> Send PO (email + CC, WhatsApp) -> Placed orders")
    d.fig(
        r"""
sequenceDiagram
  autonumber
  actor S as Staff / admin
  participant C as W cart page / A Purchase Order Cart
  participant API as FastAPI /api/ordering
  participant DB as SQLite
  participant PDF as po_export (reportlab)
  participant G as Gmail SMTP
  participant WA as Whapi (off by config)
  S->>C: Add from New order / Reorder / Fix Issues / search
  C->>API: POST /cart/items {supplier, item_id, qty, case_of}
  API->>DB: po_cart_items (one shared store cart)
  S->>C: change qty (PATCH /cart/items/{id}) - app applies it in place
  S->>C: Send PO (vendor)
  C->>API: GET /po-send/preview (+ preview.pdf) - recipients from vendor_contacts, CC
  S->>C: confirm recipients, CC, note
  C->>API: POST /po-send {supplier, idempotency_key, recipients, cc, note, channels}
  API->>DB: po_sends row (unique idempotency_key)
  API->>PDF: build_po_pdf grouped by PO sections, sorted by name
  API->>G: SMTP_SSL 465, 30 s timeout, PDF attached, CC
  opt PO_WHATSAPP_ENABLED
    API->>WA: POST /messages/document
  end
  API->>DB: placed_orders (placed, source sent) + clear those cart lines
  API-->>C: send result (502 with the send row if email failed - nothing placed)
""",
        "Send PO. Alternatives: Download PDF (POST /purchase-order/export-pdf records a 'pdf' placed order) and Mark placed (POST /cart/mark-placed snapshots and clears).",
    )
    d.ul(
        [
            "<b>Removal</b>: admins delete a line; staff request removal (<code>POST /cart/items/{id}/removal-request</code>), admins approve / reject; the 10-minute <code>cart_removal_request</code> push tells admins.",
            "<b>History</b>: <code>GET /api/ordering/orders</code> (Placed Orders), <code>POST /orders/{id}/order-again</code> puts lines back in the cart. Home shows the 3 latest (Recent orders).",
            "<b>PO sections</b>: automatic from category (with category_overrides), edited on PO sections (<code>/api/categories/*</code>, item / vendor / rename rules); the cart preview shows the effect.",
            "Cart with projection context (<code>GET /cart?projection=1</code>, about 1 s): the app re-reads it only when a line is added / removed / re-cased or a shared item's qty changes (2 s debounce).",
        ]
    )

    # ---------------------------------------------------------------- deliveries
    d.h(2, "8.10", "Deliveries, reconciliation and transfers")
    d.fig(
        r"""
sequenceDiagram
  autonumber
  actor S as Staff
  participant C as W / A Delivery Review
  participant API as /api/reconciliation
  participant DS as delivery_suggestions
  participant DR as delivery_review
  participant DB as SQLite
  C->>API: GET /suggested-deliveries
  API->>DS: compare consecutive snapshots + sales in between + Toast receipts
  DS-->>C: suggested deliveries per vendor (never writes)
  S->>C: open a vendor (or pick one manually)
  C->>API: GET /delivery-candidates?vendor
  API->>DR: items new or with a big swing; suggested qty = count diff + sold that day
  S->>C: confirm real quantities and unit costs
  C->>API: POST /delivery-confirm
  API->>DB: purchase_log rows (qty + captured unit cost)
  Note over DB: feeds Reconciliation, Vendor price comparison,<br/>schedule suggestions, invoice gaps
""",
        "Delivery Review. Dismissing a suggestion writes suggestion_dismissals (Undo deletes it).",
    )
    d.ul(
        [
            "<b>Reconciliation</b>: expected_closing = opening + purchased - sold; variance = counted - expected (shrinkage / unlogged purchases). Purchase log by form, CSV/XLSX import, or Delivery Review. 'Invoices without a logged purchase' compares invoices with the log by vendor and date. Worked example lives in Help.",
            "<b>Transfers</b> (Store <-> Container): the page is retired (redirects to Delivery Review); <code>/api/transfers/*</code> and <code>location_transfers</code> stay for old app builds.",
        ]
    )
