from doc_core import Raw, esc


def render(d, ctx):
    d.h(1, "9", "Performance, limits, operations and change log")
    d.h(2, "9.1", "Measured performance (Oct 15 batch)")
    d.p(
        "Measured in-process (handlers called directly, JSON encode included) on a <code>.backup</code> copy of "
        "production (383 MB, 124 k order lines, 649 k snapshot rows), Toast and scheduler off, on the live server "
        "under load (numbers +-20 %). HTTP checks on throwaway servers agreed."
    )
    d.table(
        ["Request", "Before", "After"],
        [
            ["Critical Skip this week (cold process / warm)", "3,945 / 1,636-1,879 ms", "139 / 2-3 ms"],
            ["Critical Remind me later", "1,632-2,402 ms", "2 ms"],
            ["Skip while Home's two reports compute", "3,903-6,600 ms", "142-277 ms"],
            ["Critical Close (report cached / not)", "1,922-2,187 ms", "2 ms / 1.1-1.8 s"],
            ["GET reorder-candidates 30/7 (cached / first after a change)", "1,700-1,935 ms", "57-96 ms (691 KB) / 1,774 ms"],
            ["GET reorder-candidates critical_only=1", "(691 KB)", "5-6 ms, about 53 KB"],
            ["GET reorder-candidates 90/14 (cached / first)", "3,212-3,662 ms", "117-120 / 2,923 ms"],
            ["GET vendor-calendar today..tomorrow (cached / first)", "1,883-2,468 ms", "4 / 893 ms"],
            ["GET vendor-calendar 20 days (cached / first)", "2,995-3,524 ms", "4-5 / 577 ms"],
            ["GET orders/open/all-time with a stale today scan", "877 ms", "6 ms"],
            ["Opening-counts query, all items 30 days (index 0057)", "1,173 ms", "333 ms"],
            ["Long-poll wake after a task is created elsewhere", "-", "30 ms; 60 waiting long-polls, a normal GET still 6 ms"],
            ["Website: other browser sees a step tick / Done by / Reopen", "30 s timer", "160 / 175 / 156 ms"],
            ["App: Critical Skip sheet closes after the tap (SE / iPad)", "after the POST (seconds)", "174 / 223 ms"],
            ["App Home first frame", "entrance animation up to about 1.1 s", "first frame"],
            ["Source sync close (issue_leads first / later, checklist, order / invoice)", "next 5-minute tick", "31 / 1.4 ms, 8 ms, 0.3 ms"],
            ["Migrations 0057-0059 on a prod copy", "-", "up 2.1 s, down 0.6 s, up again 1.0 s"],
        ],
        widths=["52%", "24%", "24%"],
        cls="compact",
    )
    d.p("Unchanged (not cached): Supplier Projection, cart with projection context (about 1 s), Fix Issues summary and lists (forecast cached 10 min).")

    d.h(2, "9.2", "Known limits and risks")
    d.table(
        ["Area", "Limit / risk", "Mitigation today"],
        [
            ["Single process", "Report cache, locks (20 module-level), Analytics rate limiter and task long-poll are per process; --workers would break them", "Never add --workers; runbook and skill say so"],
            ["Single machine", "One Mac mini, one SQLite file, no off-machine backup", "Manual .backup before migrations; watchdog restarts daemons"],
            ["Toast scopes", "No Purchasing API (403), no write access; cost / margin only from the CSV", "PO PDFs and Send PO are our own; CSV remains the cost source"],
            ["Toast Analytics", "check + guest share Toast's 60/day; our client caps at 55/day; budgets 30 / 10", "Reserve for newest days, hourly catch-up, honest notes"],
            ["Whapi plan", "Sandbox: 5 chats; a new vendor chat answers 402", "PO WhatsApp off (PO_WHATSAPP_ENABLED=false)"],
            ["Logs", "No rotation (error log about 14 MB and growing)", "-"],
            ["Login", "No attempt throttling on public login routes", "-"],
            ["Teams", "Managers, Grocery, Kitchen have no members", "Empty-team guard: 400 on assign; '(no members)' in pickers"],
            ["Lead progress", "Items dropping off without a Fix Issues close count only at the next morning run", "By design (no 6 s recompute per tick)"],
            ["Morning summary", "Computes its own critical-vendor report (not the speed cache yet)", "Runs once a day"],
            ["App Home", "Pull-to-refresh keeps Critical / Orders due for 2 min (owner call to shorten)", "Server answers cached reads in ms"],
            ["Old app builds 5-9", "No long-poll, no grouped Notifications, full Critical report", "Server stays additive; old paths kept"],
            ["Legacy surface", "Capacitor code in the website, retired transfer routes, vendors_reference table", "Kept for compatibility; no menu entries"],
        ],
        widths=["16%", "48%", "36%"],
        cls="compact small",
    )

    d.h(2, "9.3", "Operations runbook pointers")
    d.table(
        ["Task", "Where / how"],
        [
            ["Deploy the Oct 15 batch", "~/spicetown-dev/DEPLOY_OCT15.md (sections A-C, owner's one sudo command, read-only checks, rollback, app release G)"],
            ["Earlier deploys and patterns", "~/spicetown-dev/DEPLOY_OCT14.md, website/README.md, the project skill (.claude/skills/spicetown/SKILL.md)"],
            ["Restart the backend", "sudo launchctl kickstart -k system/com.spicetown.backend (if it hangs: bootout + bootstrap the plist)"],
            ["Is it up?", "curl https://ops.spicetown.shop/api/health -> 200; ps for ONE uvicorn process on :8000"],
            ["Logs", "~/spicetown-backend/backend/logs/backend.error.log (Python logging), backend.log, watchdog.log"],
            ["Job health", "Overview job status widget, or read-only: select job_name, status, started_at from job_runs order by id desc"],
            ["Back up the DB", "sqlite3 'file:.../spicetown.db?mode=ro' '.backup spicetown.db.bak-<ts>' then pragma integrity_check"],
            ["Migrations", "cd backend && .venv312/bin/alembic heads / current / upgrade head (additive only)"],
            ["Website only change", "npm run build (dist) - no restart needed"],
            ["Notifications not arriving", "Notifications page: Recent notifications; admins: Store defaults and Pushes per person; push_deliveries table; Firebase file configured?"],
            ["Insights day missing", "The page's note says which day and when; job_runs insights_nightly / insights_catchup detail; never hand-run fills before the 04:00 closeout"],
            ["Inventory from Toast readiness", "Inventory from Toast page (nightly comparisons, readiness streak, switch)"],
            ["Dev server", "~/spicetown-dev/up.sh / down.sh (:8001, tailnet :8443, schedulers off)"],
            ["Tests", "backend: .venv/bin/pytest (3.9) and .venv312; website: node --test (git ls-files *.test.mjs), vite build; app: flutter analyze, flutter test --concurrency=2"],
            ["Regression suites", "website/e2e/regression suite-a and suite-b (Playwright on a throwaway prod copy)"],
        ],
        widths=["26%", "74%"],
        cls="compact",
    )

    d.h(2, "9.4", "Change log of the Oct 15 batch")
    d.table(
        ["Commit", "Area", "What changed"],
        [
            ["707fdd8", "Speed (server) + 0057", "Report cache (Today report, supplier list, calendar counts), instant Skip / Remind me later, critical_only=1, one-pass Vendor Calendar, open checks served from cache with a background refresh, SQLite pragmas, covering index"],
            ["ff4f7f4", "Insights", "Honest 'note' and coverage.missing_dates / pending / retry_at, call reserve for the newest days, hourly catch-up job, channels 12 weeks, unavailable marker, truthful job status"],
            ["61831ad", "Notifications (server) + 0058", "'Recommended' defaults per role, store defaults with history, one 09:00 Morning summary, prefs reset, recent, volume, push_deliveries log, quieter logs, task_due split (task_due_soon, fix_issues_task_due)"],
            ["291c28f", "Team tasks (server) + 0059", "Long-poll change feed (wait, include=tasks), conditional writes + expected_updated_at + 409 with task, steps editing, bulk, filters, task-summary, schedule_text, lead progress 'N left of M' closing at 0, source sync, closing-checklist tasks stopped and leftovers closed as Missed, empty-team guard, pushes after the response, task_done / task_comment (opt-in)"],
            ["4ed742c", "Team tasks (website)", "One shared live feed, drawer Edit / Reassign / steps, action button, filters, bulk, My team today, empty-team states"],
            ["1f20fb7", "Website navigation + Notifications page", "Menu groups Team / Ordering / Stock / Sales & Insights / Setup, Help with Glossary tab, Quick actions and Transfer Review removed, Notifications page with store defaults and volume"],
            ["f670bc8", "Final regression", "Migration chain test, suite updates, contract, skill notes"],
            ["d951492 (app)", "Navigation + speed", "Store 3 groups / 15 tools, More Setup, Help tabs, Scan History icon, no Home animation, optimistic Critical sheet, critical_only, cart qty applied in place, 2-minute heavy-report rule"],
            ["e3b522c (app)", "Notifications screen", "Grouped switches, essential confirm, Reset, Store defaults, Hide message text in Team card"],
            ["2f4412c (app)", "Team tasks", "Long-poll sync, onMessage poll, task patches, Can't do section, search and filters, My team today, bulk, steps editing, 409 handling, schedules screen"],
        ],
        widths=["14%", "22%", "64%"],
        cls="compact small",
    )
    d.p(
        "Test totals at the end of the batch: backend 2,190 passed / 8 skipped (3.9) and 2,191 / 8 (3.12) with 2 "
        "time-of-day tests failing identically before the batch; website node 208/208 and vite build; Suite A and "
        "Suite B all checks passing; new focused pass 113 checks at 1280 and 375 px; app flutter analyze clean, "
        "5,200 tests passed, 6 skipped."
    )
    d.p(
        "Owner decisions still open: 'Invoice failed' and 'Cart removal requests' on by default; members of Managers, "
        "Grocery and Kitchen; the app's 2-minute Home refresh rule; the next app build number (above the last one "
        "uploaded to App Store Connect); retaking the 01-home and 02-store App Store screenshots."
    )
