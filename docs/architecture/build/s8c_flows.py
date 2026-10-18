from doc_core import Raw, esc


def render(d, ctx):
    d.h(2, "8.22", "Marketing flyers")
    d.fig(
        r"""
sequenceDiagram
  autonumber
  actor A as Admin / flyers user (never demo)
  participant C as W Team Flyers / A Team Flyers
  participant API as /api/flyers
  participant FS as ~/Spice Town Flyers/Category/
  participant DB as SQLite
  participant Q as flyer_sender worker thread
  participant WA as Whapi
  A->>C: upload image / PDF + caption, category
  C->>API: POST /api/flyers (validate type and size, sha256, thumbnail)
  API->>FS: write file
  API->>DB: flyers
  A->>C: Send to groups (marketing WhatsApp groups)
  C->>API: POST /api/flyers/{id}/send {groups, idempotency_key}
  API->>DB: flyer_sends rows (queued), one per group
  API->>Q: enqueue ids
  API-->>C: batch (queued)
  loop one group at a time, 3-6 s random pause
    Q->>WA: upload media once, then /messages/image or /document with caption
    Q->>DB: status sent / failed / ambiguous (reconciled via message list)
  end
  Q->>Q: flyer_sent push (opt-in)
  C->>API: GET /api/flyers/{id} every 3 s while sending
""",
        "Flyer upload and WhatsApp send. Retry: POST /api/flyers/{id}/sends/{send_id}/retry.",
    )

    d.h(2, "8.23", "Insights (Toast Analytics): two-step reports, cache, budget, catch-up")
    d.fig(
        r"""
sequenceDiagram
  autonumber
  participant C as W Insights page / A Insights screen
  participant API as GET /api/insights/page
  participant IS as services/insights.py
  participant ID as insights_data
  participant DB as analytics_cache / call log
  participant ERA as Toast Analytics (ERA)
  C->>API: e.g. /loss-watch?days=30
  API->>IS: compute from the cache
  IS->>DB: rows per business date for the needed reports
  opt today is in range
    IS->>ERA: live today (cached 90 s)
  end
  IS-->>API: numbers + envelope stale, filling, coverage, note
  API-->>C: shows the note when stale (which day, why, when it arrives)
  opt dates missing
    API->>ID: request_fill (one background fill, max 12 calls, budget-limited)
    ID->>ERA: POST /era/v1/kind/range (report request)
    ERA-->>ID: report GUID
    ID->>ERA: GET /era/v1/kind/guid until ready
    ID->>DB: analytics_cache per date, analytics_call_log
  end
""",
        "An Insights page read and its background fill.",
    )
    d.table(
        ["Report", "Kept (days)", "Used by"],
        [
            ["metrics_day", "91", "Daily compare (and 4-week average), check trends, weekly digest, Morning summary sales line"],
            ["metrics_dining", "84", "Sales channels"],
            ["metrics_hour", "84", "Busy hours heatmap, prep forecast"],
            ["menu_item", "112", "Menu trends, best and worst sellers"],
            ["menu_modifier", "56", "Add-ons"],
            ["check (daily budget 30)", "30", "Loss watch, Odd checks (admins)"],
            ["guest payments (daily budget 10)", "84", "Returning customers (card fingerprints stored only as an HMAC)"],
        ],
        widths=["28%", "12%", "60%"],
        cls="compact",
    )
    d.ul(
        [
            "<b>Nightly 04:30</b>: yesterday's reports, re-pulls of the last 14 days (check: 7), some older history within per-report call caps, the menu map, then Loss watch / Odd checks flags; purge (guest 400 days, check 120, cache 400, call log 7). Job status: success / partial (budget) / failed, with warnings.",
            "<b>Reserve</b>: the last 2 closed days always come first; older days only spend what is above the reserve (check 4, guest 2), so a backfill or a hand-run can't leave the newest day waiting 24 h.",
            "<b>Catch-up</b>: hourly at :20 from 05:20 to 23:20; reads the cache first and calls Toast only for a missing newest day while the budget has room.",
            "<b>Unavailable</b>: Toast answering 404/409 repeatedly marks the date unavailable (not a $0 day); the nightly retries it once a night.",
            "<b>Access</b>: feature sales_insights; Loss watch and Odd checks admin only; hide_sales gets counts only (heatmap) or 403 for sales-only pages.",
        ]
    )

    d.h(2, "8.24", "Ask Inventory Bot")
    d.fig(
        r"""
sequenceDiagram
  autonumber
  actor U as Person
  participant C as W chat widget / A Ask bot
  participant API as POST /api/ask
  participant AB as ask_bot
  participant AN as Anthropic API
  participant R as read-only report functions
  U->>C: question (+ history)
  C->>API: {question, history} (app waits up to 120 s)
  alt demo login
    API-->>C: fixed answer, no Anthropic call
  else
    API->>AB: ask
    AB->>AN: claude-haiku-4-5 classify SIMPLE or COMPLEX
    AB->>AN: Haiku (simple) or claude-sonnet-5 (complex) with tools
    loop tool calls
      AN->>AB: tool_use (e.g. reorder_candidates)
      AB->>R: run the existing report (no writes)
      R-->>AB: rows
      AB->>AN: tool_result
    end
    AN-->>AB: answer
    API-->>C: answer, escalated flag
  end
""",
        "Ask bot. Tools: list_vendors, reorder_candidates, supplier_projection, items_sold (not for hide_sales), reconciliation, dead_stock, missing / invalid barcodes, price_changed_items, price_history, vendor_price_comparison, delivery_candidates.",
    )

    d.h(2, "8.25", "Weekly digest")
    d.p(
        "Overview &rsaquo; Weekly Digest (kept by the owner): <code>POST /api/weekly-digest/generate</code> gathers the "
        "trailing 7 days from the existing services (reorder, dead stock, vendor price comparison, reconciliation, "
        "barcode quality, sales vs the prior week - left out for hide_sales) and makes <b>one</b> "
        "<code>claude-opus-5</code> call to write the narrative. Generated on demand, not stored. Demo: canned text."
    )

    d.h(2, "8.26", "Open checks")
    d.fig(
        r"""
sequenceDiagram
  autonumber
  participant J as Jobs (04:15 full, every few min today)
  participant OO as open_orders
  participant T as Toast ordersBulk
  participant DB as open_order_cache
  participant C as W Open Checks / A Open Checks, Home tile
  J->>OO: refresh_open_orders_cache(full or today)
  OO->>T: ordersBulk per business date (full: every date since sales began)
  OO->>DB: replace cache rows (server names from labor employees, 10 min cache)
  C->>OO: GET /api/orders/open (today) or /open/all-time
  alt today's scan older than 2 min
    OO->>OO: refresh today on a daemon thread (Oct 15)
  end
  OO-->>C: cached rows at once (total_amount null for hide_sales)
  C->>OO: POST /open/all-time/rescan (full re-read, 1-3 min), GET /open/export (CSV)
""",
        "Open checks (orders still open in Toast).",
    )
