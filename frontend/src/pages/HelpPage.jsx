import { useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../AuthContext.jsx";
import GlossaryPage from "./GlossaryPage.jsx";
import { ReconciliationWorkedExample } from "../components/Reconciliation.jsx";
import { storeZoneCity } from "../format.js";
import { posText } from "../store/gating.js";

const SECTIONS = [
  { id: "overview", title: "Overview" },
  { id: "menu", title: "Finding your way (menu)" },
  { id: "invoices", title: "Invoices" },
  { id: "open-orders", title: "Open Checks" },
  { id: "items-sold", title: "Items Sold" },
  { id: "insights", title: "Insights" },
  { id: "reorder", title: "Reorder Candidates" },
  { id: "supplier-projection", title: "New order (Supplier Projection)" },
  { id: "placed-orders", title: "Placed Orders" },
  { id: "vendor-calendar", title: "Vendor Calendar" },
  { id: "fix-issues", title: "Fix Issues" },
  { id: "inventory-reports", title: "Inventory Reports" },
  { id: "reconciliation", title: "Reconciliation" },
  { id: "downloads", title: "Downloading reports" },
  { id: "categories", title: "PO sections" },
  { id: "daily-checklists", title: "Daily Checklists" },
  { id: "team", title: "Team: tasks, templates and chat" },
  { id: "flyers", title: "Flyers (Marketing)" },
  { id: "ask", title: "Ask Inventory Bot" },
  { id: "notifications", title: "Phone alerts (in the app)" },
  { id: "users", title: "Users (admin)" },
  { id: "integrations", title: "Integrations (admin)" },
  { id: "inventory-api", title: "Inventory from {pos} (admin)" },
  { id: "live-data", title: "What's live and what's on a schedule" },
  { id: "limitations", title: "What this doesn't do (yet)" },
];

// Two tabs: Help (this guide, needs the `help` grant) and Glossary
// (/glossary, for everyone - the whole page for anyone without Help).
export default function HelpPage({ tab = "help" }) {
  const { hasFeature, store } = useAuth();
  // "{pos}" in the text below is this store's POS name ("Toast", "Clover").
  const pos = store.posName || "the POS";
  const navigate = useNavigate();
  const canHelp = hasFeature("help");

  // Plain <a href="#id"> anchors don't work here: this app uses HashRouter,
  // which treats the URL hash as a route, not a same-page anchor - clicking
  // one would try to navigate to a "/overview" route instead of scrolling.
  // Scroll (and open the target <details>) with JS instead.
  function jumpTo(id) {
    const el = document.getElementById(id);
    if (!el) return;
    if (el.tagName === "DETAILS") el.open = true;
    el.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // A "Help" link elsewhere can open one section: <Link to="/help"
  // state={{ section: "insights" }}> (the Insights pages do).
  const location = useLocation();
  useEffect(() => {
    const id = location.state?.section;
    if (id) requestAnimationFrame(() => jumpTo(id));
  }, [location.state]);

  const tabs = (
    <div className="segmented help-tabs" role="tablist" aria-label="Help" style={{ margin: "0.35rem 0 0.6rem" }}>
      {canHelp && (
        <button
          type="button"
          role="tab"
          aria-selected={tab === "help"}
          className={tab === "help" ? "active" : undefined}
          onClick={() => navigate("/help")}
        >
          Help
        </button>
      )}
      <button
        type="button"
        role="tab"
        aria-selected={tab === "glossary"}
        className={tab === "glossary" ? "active" : undefined}
        onClick={() => navigate("/glossary")}
      >
        Glossary
      </button>
    </div>
  );

  if (tab === "glossary") {
    return (
      <>
        <div className="page-header">
          <h1>{canHelp ? "Help" : "Glossary"}</h1>
          {canHelp && tabs}
        </div>
        <GlossaryPage />
      </>
    );
  }

  return (
    <>
      <div className="page-header">
        <h1>Help</h1>
        {tabs}
        <p className="muted">What each page does, how the numbers are calculated, and how to use them.</p>
      </div>

      <section className="card">
        <p className="muted" style={{ margin: 0 }}>Jump to a section:</p>
        <div className="help-toc">
          {SECTIONS.map((s) => (
            <button key={s.id} type="button" onClick={() => jumpTo(s.id)}>
              {posText(s.title, pos)}
            </button>
          ))}
        </div>
      </section>

      <details className="help-section" id="overview">
        <summary>Overview</summary>
        <div className="help-section-body">
          <p>
            The daily-operations landing page. Two things live here: sync status and inventory
            upload.
          </p>
          <h4>Job status</h4>
          <p>
            Shows the last run of the two automatic/manual background jobs: <strong>Sales sync
            ({pos})</strong> - pulls the prior day's orders from {pos} automatically every night,
            and self-heals a missed night on the next run or the next service restart - and
            <strong> Inventory upload</strong> - the last time you uploaded a file below. A green
            pill means it finished successfully, yellow means it's currently running, red means it
            failed (click the detail text for why).
          </p>
          <h4>Upload today's inventory</h4>
          <p>
            Upload {pos}'s daily "retail items" export (CSV or XLSX), unchanged - don't
            re-save or edit it first, since columns are matched by their exact header text. It gets
            tagged with today's date automatically. You'll see a real upload progress bar, then a
            "Processing…" state while the server parses and saves potentially thousands of rows.
          </p>
          <p>
            <strong>Missing a day is fine.</strong> Every report that uses inventory (Reorder
            Candidates, Supplier Projection, Inventory Reports) always uses the{" "}
            <em>most recent</em> upload on file. If you upload June 1st and then don't upload again
            until June 5th, everything just quietly uses the June 1st numbers in between - no error,
            no data loss, just slightly stale on-hand counts and prices until the next upload lands.
          </p>
          <h4>"We received a delivery today"</h4>
          <p>
            If an invoice or return was already submitted today for a vendor (Invoice Submission
            tab), this box and that vendor are pre-checked automatically when the page loads - a
            note explains this above the vendor list. It's only a starting point: add or remove
            vendors freely, or uncheck the box entirely if no delivery actually happened today
            despite an invoice being submitted (e.g. it was for something ordered for later).
          </p>
          <p>
            Check this box before uploading and pick the vendor(s) that delivered. After the
            upload finishes, click <strong>Review delivery</strong> to open a page per vendor
            showing items likely from that delivery - anything new to the catalog, plus anything
            whose net change since the previous upload was more than 5 (a big drop is shown too,
            in case it's worth a look, just not pre-filled as a delivery amount). "Net change"
            means the on-hand count change <em>plus</em> whatever sold that same day, so a
            delivery isn't undercounted just because some of it already sold before you uploaded -
            e.g. 100 on hand yesterday, 400 delivered, 50 sold today shows a raw count change of
            only +350, but the suggested quantity correctly shows 400. That suggestion is still
            only a starting point - review and edit it, then <strong>Confirm &amp; log</strong> to
            write it to the purchase log (see Reconciliation below). Nothing is logged
            automatically; you can freely change any quantity, but if what you type doesn't match
            the suggested quantity, the field turns red - a reminder that you're overriding it, not
            a block on doing so. The Cost column shows that day's inventory file cost for each
            item - confirming logs it against the vendor you picked, which is what powers Vendor
            Price Comparison in Inventory Reports.
          </p>
          <h4>Suggested deliveries</h4>
          <p>
            <strong>Delivery Review</strong> (Ordering menu) now finds deliveries by itself and lists them under{" "}
            <strong>Suggested deliveries</strong>, one per vendor and upload - no need to tick a box on the upload first.
            Most come from {pos}: when a delivery is received in {pos}, the next inventory upload carries the receive time
            and {pos}'s receiving number (like IN378) on each item, and we group those items under the vendor most of them
            list (an item {pos} lists under another vendor still lands with the vendor that delivered it). On a day with
            only the nightly {pos} snapshot and no upload, a delivery shows instead as 3 or more of one vendor's items
            rising by more than sales explain (moves from the Container row, recounts up from below zero and small weighed
            changes are left out). The quantity is count now - count before + units sold in between. Check the lines and
            quantities, then <strong>Confirm &amp; log</strong> - it writes exactly what a manual review writes. A vendor
            whose delivery is already logged isn't suggested again; <strong>Not a delivery - dismiss</strong> hides one
            (Undo brings it back).
          </p>
          <h4>Container transfers (retired)</h4>
          <p>
            Transfer Review is retired and nothing reads the transfers it logged. Its old address now opens Delivery
            Review; the transfers already logged are kept, and old app builds still work. To tell the team about stock
            moved between the store and the container, upload a <strong>Transfer</strong> on Invoices.
          </p>
          <h4>Weekly Digest</h4>
          <p>
            Click <strong>Generate this week's digest</strong> for an auto-generated summary of the
            trailing 7 days - sales vs. last week, top reorder needs, dead stock, vendor savings
            opportunities, the biggest reconciliation variances, and barcode data
            quality counts. Every number comes from the same reports listed elsewhere in this Help
            page; Claude only writes the short summary sentence per section, it's never asked to
            compute or estimate a figure. It's generated on demand (not automatically on a
            schedule) and shown here only - nothing is emailed or sent anywhere. Requires{" "}
            <code>ANTHROPIC_API_KEY</code> in <code>backend/.env</code>, same as Ask Inventory Bot.
          </p>
        </div>
      </details>

      <details className="help-section" id="menu">
        <summary>Finding your way (menu)</summary>
        <div className="help-section-body">
          <p>
            The menu on the left has the same groups as the app's Store tab. You only see what you've been given access
            to, and a group with just one page for you shows as that page.
          </p>
          <ul>
            <li>
              <strong>Overview</strong>, <strong>Invoices</strong> and <strong>Daily Checklists</strong> at the top.
            </li>
            <li>
              <strong>Team</strong>: Tasks, Chat, News (announcements) and Flyers.
            </li>
            <li>
              <strong>Ordering</strong>: Reorder Candidates, New order (Supplier Projection), Vendor Calendar, Placed
              Orders and Delivery Review. The cart is the cart button at the top right of every page.
            </li>
            <li>
              <strong>Stock</strong>: Fix Issues, Inventory Reports and Reconciliation.
            </li>
            <li>
              <strong>Sales &amp; Insights</strong> (just <strong>Sales</strong> without Insights access): Items Sold
              (with the sales download), Open Checks and the Insights pages, with <strong>Also in {pos}</strong> folded
              away at the end.
            </li>
            <li>
              <strong>Setup</strong>: pages you set once and rarely change - Users, Teams, Recurring tasks, Reported
              messages, Checklist builder, PO sections, Vendor contacts, Category &amp; vendor leads, Inventory from
              {pos} and Integrations (most are for admins).
            </li>
            <li>
              Below the groups, for everyone: <strong>Label Scanner</strong> (opens the label app),{" "}
              <strong>Help</strong> (this page, with the <strong>Glossary</strong> tab) and{" "}
              <strong>Change password</strong>.
            </li>
          </ul>
          <p style={{ marginBottom: 0 }}>
            Old links and bookmarks still work: they open the page's current place.
          </p>
        </div>
      </details>

      <details className="help-section" id="invoices">
        <summary>Invoices</summary>
        <div className="help-section-body">
          <p>
            Replaces the old Google Form. Fill in the vendor, whether it's an invoice, a return,
            something else or a transfer, the total (optional), and upload the file (a PDF or a
            photo), then Submit.
          </p>
          <p>
            <strong>Transfer</strong> is for stock moved between the store and the container. Pick
            the direction (Store {"\u2192"} Container or Container {"\u2192"} Store); the vendor is
            optional (left blank it shows as {store.name}). A transfer goes to WhatsApp only, never to
            {pos}, is Completed once WhatsApp is sent, and never counts as a vendor delivery.
            (Untracked is no longer offered; older Untracked uploads still show as before.)
          </p>
          <p>
            <strong>Two things happen automatically after you submit:</strong> a WhatsApp message
            goes to the staff group with a link to the file, and an email goes to {pos}'s invoicing
            inbox with the file attached as a PDF (a photo is converted to PDF automatically if you
            didn't upload one directly) - for every type except a transfer, including "something
            else." {pos} turns
            that email into a draft bill on its own.
          </p>
          <p>
            <strong>Submit itself is instant</strong> - it only has to save the record and file.
            WhatsApp and the {pos} email happen right after, in the background, since either can
            take a while (WhatsApp especially, occasionally close to a couple of minutes). You'll
            see "Sending…" for each one until it resolves, updating live with no need to refresh -
            this is what actually fixes Submit feeling like it hangs.
          </p>
          <p>
            <strong>Looks closed in {pos}.</strong> Once an invoice has gone to {pos}, someone still has to enter it there
            and mark it closed here. When the next inventory upload shows {pos} received a delivery from that vendor after
            the invoice was uploaded (or, on a nightly-snapshot day, several of its items rose like a delivery), the invoice
            is listed under <strong>Looks closed in {pos}</strong> with the reason, and its row gets a{" "}
            <strong>Looks closed</strong> tag. <strong>Mark closed in {pos}</strong> is the same one tap as the circle in
            the Closed column; <strong>Not closed yet</strong> hides the suggestion. When a vendor has several open invoices,
            the oldest one is suggested first. Nothing is ever closed automatically.
          </p>
          <p style={{ marginBottom: 0 }}>
            <strong>The submission itself is always saved, even if a send fails</strong> (no
            internet blip, wrong credentials, etc. ever loses your submission) - each row in the
            list below shows "Sending…", "Sent", or "Failed" for WhatsApp and {pos} email
            independently, and a failed one gets a <strong>Resend</strong> button so it can be
            retried without filling the form out again.
          </p>
        </div>
      </details>

      <details className="help-section" id="open-orders">
        <summary>Open Checks</summary>
        <div className="help-section-body">
          <p>
            (This page used to be called Open Orders.) Checks {pos} currently has on file for a chosen date (defaults to today) that haven't
            been closed on the POS yet - a live look at what's still open right now, not a stored
            report.
          </p>
          <p>
            <strong>Tab</strong> is the name typed on the check's tab in {pos} - usually the
            customer's name - so you can tell whose check it is at a glance. An order split into
            several checks lists each different tab name once; it's blank when nobody named the tab.
            The search box matches tab names too, and the CSV has a Tab column.
          </p>
          <p>
            <strong>Nothing here is saved by this app.</strong> Every time you load this page or
            hit <em>Refresh</em>, it asks {pos} directly for that date's checks and shows whichever
            ones don't have a closed timestamp yet (and aren't voided or deleted). There's no button
            to close a check here - once it's actually closed on the POS, the next refresh simply
            stops showing it.
          </p>
          <p>
            <strong>Date and employee filters:</strong> picking a past date will normally show zero
            results, since every check from a finished day has already been closed by the time you'd
            look. The employee filter matches whoever the POS recorded as running that order
            ({pos}'s own "server" on the order) - it's who opened/owns the ticket, not necessarily
            who's about to close it.
          </p>
          <p>
            <strong>All-time</strong> shows every open check regardless of date - useful for catching
            a check that got left open for days or weeks and would never show up in the normal
            single-date view unless you happened to pick that exact date. This mode reads instantly
            from a cache this app keeps fresh, not a full live {pos} pull: today's checks are
            re-checked when you open the list and the last check is more than 2 minutes old (and every
            15 minutes in the background anyway), and the full order history (the only way to
            notice something stuck open on an older date) is fully re-swept once a day. If you need
            certainty right this second, <strong>Rescan now</strong> forces a real full re-scan on
            demand - genuinely slow (a minute or more, one {pos} request per historical date), which
            is exactly why it isn't what happens automatically on every page load.
          </p>
          <p>
            <strong>Order total and items</strong> shown per row come straight from that order's
            checks and line items on {pos}, same underlying data the rest of this app's sales
            reporting uses - each item on its own line, so a full order is readable at a glance.
          </p>
          <p>
            <strong>Two sections: "Needs a look" and "Scheduled for later."</strong> A check that's
            fully paid but still open isn't automatically a problem - {pos} charges an online/pickup
            order on placement and only closes it once someone marks it fulfilled, and a guest can
            pay tonight for a pickup scheduled tomorrow. A check only lands in "Scheduled for later"
            if it's paid AND its promised time hasn't arrived yet; the moment that promised time
            passes (or it was never paid to begin with), it moves into "Needs a look" instead - that's
            the section actually worth checking.
          </p>
          <p style={{ marginBottom: 0 }}>
            <strong>Download CSV</strong> saves the whole list for the chosen date (or All-time) and the
            "Opened by" choice. For users whose sales numbers are hidden, check totals are left blank here and
            in the download.
          </p>
        </div>
      </details>

      <details className="help-section" id="items-sold">
        <summary>Items Sold</summary>
        <div className="help-section-body">
          <p>
            Quantity and revenue sold, per item, for a chosen range - defaults to today, with the
            same Today / Last 7 days / Month / 3 months / Custom range picker used on Supplier
            Projection.
          </p>
          <p>
            <strong>How the total is computed, precisely:</strong> an item counts toward a date if
            it was actually <em>paid for</em> on that date - not whichever date {pos} internally
            files the order under. Revenue is the sum of each counted item's own price (after any
            per-item discount, before tax). Add-ons such as a spice level or extra cheese are already
            part of their item's price in {pos}, so they are not listed or counted again.
          </p>
          <p>
            <strong>Pay today for tomorrow's order = counts as today.</strong> A guest paying
            tonight for a pickup scheduled tomorrow shows up under <em>today</em>, since that's
            when the money actually came in - even though {pos} internally files that whole order
            under tomorrow's business date. An order that hasn't actually been paid yet doesn't
            count toward any date until it is.
          </p>
          <p>
            <strong>This is this app's own logic, and it will not always match the number on
            {pos}'s own dashboard - on purpose.</strong> {pos}'s dashboard instead groups by the
            calendar day it filed the order under (not payment date) and uses each check's official
            total (which nets out a check-level discount that isn't reflected in the sum of
            individual item prices). Both approaches were checked directly against real data on
            2026-09-01 and both are internally consistent - they just answer a genuinely different
            question ("what did we actually get paid for on this date" vs. "what's {pos}'s own
            headline total for this business date"). A small, expected gap between the two is not
            a sign of a sync problem.
          </p>
          <p>
            <strong>Today's date is pulled live from {pos}</strong> when you open the page (a pull
            less than a minute old is reused; Refresh pulls again), since the overnight sync only
            ever covers yesterday and earlier - any past date is served from the already-synced
            database instead, which is faster and doesn't re-hit {pos}'s API.
          </p>
          <p>
            <strong>Past days stay correct when {pos} changes them.</strong> A few times a day the
            app compares the last 14 days with {pos}'s own item totals ({pos} Analytics) and re-syncs
            any day that differs - a check voided or discounted the next morning, an item removed,
            a late payment. Admins see each day's comparison in the Sales accuracy card at the
            bottom of this page.
          </p>
          <p>
            <strong>Download CSV</strong> saves the whole range the table is showing. Users whose
            sales numbers are hidden don't get this page: it's gone from their menu (see Users).
          </p>
          <h4>Download sales data</h4>
          <p style={{ marginBottom: 0 }}>
            Under the table: a raw line-item export (every item sold, with price/tax/void status) for a date range, as
            CSV or PDF. It's a plain export, not an analysis. (It used to be on Inventory Reports.)
          </p>
        </div>
      </details>

      <details className="help-section" id="insights">
        <summary>Insights</summary>
        <div className="help-section-body">
          <p>
            Reports under <strong>Sales &amp; Insights</strong> in the menu, built from {pos}'s own Analytics (a separate
            {pos} reporting service, not the order sync the rest of the dashboard uses). An admin grants them with
            the <strong>Sales insights</strong> access on the Users page; Loss watch and Odd checks show names of
            employees and are for admins only. Users whose sales numbers are hidden get only the pages without
            dollar amounts: Prep forecast, Returning customers and Busy hours (as order counts).
          </p>
          <p>
            The main pages (Daily compare, Loss watch, Odd checks, Prep forecast, Menu trends, Add-ons and
            Returning customers) are things {pos}'s own reports don't give you. Four more sit at the end, in a
            group called <strong>Also in {pos}</strong>: Sales channels, Check size &amp; guests, Busy hours and
            Best &amp; worst sellers. They show the same numbers as {pos}'s own reports and are kept here for
            convenience, so you don't have to switch to {pos} to see them.
          </p>
          <p>
            <strong>Where the numbers come from:</strong> each page says "From {pos} Analytics, updated ..." at the
            top. A past day is read from {pos} once and saved; the last 14 days are read again each night (about
            4:30 AM) because {pos} keeps changing days after they close (a void or discount the next morning).
            Today's numbers are read live and reused for a minute or two. If {pos} doesn't answer, the page shows
            the last saved copy with a yellow note saying so. {pos}'s Analytics run 2-4 hours behind the
            registers, and they count by {pos}'s business day (which ends at 4 AM), so totals can differ by a few
            dollars from Items Sold, which counts by payment time.
          </p>
          <h4>Daily compare</h4>
          <p>
            One day (yesterday by default) next to the same weekday last week, the average of that weekday over
            the 4 weeks before, and the same day last year once there's a year of history. Today can be picked
            too: it's compared with the same hours of the earlier days. ▲ is higher, ▼ lower; a
            change under half a percent shows as =. The same line is added to the daily sales notification and
            the weekly digest.
          </p>
          <h4>Loss watch (admins)</h4>
          <p>
            Discounts, voids, refunds and estimated comps by day and by employee, plus the checks that stand out:
            well above that employee's usual (3 times their 30-day median), above a fixed amount, or comped 100%.
            When something is flagged, admins get one combined alert at 9 AM the next day (held during quiet
            hours). <strong>Acknowledge</strong> marks a flagged check as looked at. A flag is a prompt to look,
            not proof of a problem.
          </p>
          <h4>Odd checks (admins)</h4>
          <p>
            Checks open for more than 3 hours, changed more than an hour after they were paid (not online or
            delivery orders, which change when they're handed over), or with a refund of $25 or more. They're
            included in the same 9 AM alert and can be acknowledged the same way. Look a check up in {pos} by its
            number and date.
          </p>
          <h4>Prep forecast</h4>
          <p>
            How many of each kitchen item to expect on a chosen day (tomorrow by default, up to 14 days
            ahead): the same weekday over the last 8 weeks with the highest and lowest day left out, nudged by the
            last 4 weeks' trend (at most 20%), plus a low-high range (the middle half of those days) and the store's
            usual busiest hours that weekday. <strong>Print</strong> gives a clean prep sheet with a tick box per item;
            <strong> Copy as text</strong> copies the list for a message. It doesn't know about catering orders,
            holidays or weather.
          </p>
          <h4>Menu trends</h4>
          <p>
            The biggest risers and fallers in units against the same number of days just before (only items that
            sold at least 3 then), and kitchen items still on the menu that didn't sell at all in the
            period (Not sold). Filter to Kitchen (the prepared-food menus) or Grocery (the store's other
            menus). This used to be part of
            Menu performance, together with the top and bottom sellers, which are now under Best &amp; worst
            sellers.
          </p>
          <h4>Add-ons</h4>
          <p>
            Every modifier customers picked (spice level, extra cheese, a side): how often, the extra charged for
            paid ones, and the attach rate - how often it was picked per unit of the item it belongs to. Add-on
            money is already inside the item's own sales, so don't add the two.
          </p>
          <h4>Returning customers</h4>
          <p>
            How many different cards paid each week and how many of them came back (had paid before, or paid on
            two different days). Only a scrambled code
            of each card is kept (at most 400 days) - never the card number or a name. Cash customers aren't
            counted, and one person with two cards counts twice, so read it as a trend.
          </p>
          <h4>Also in {pos}</h4>
          <p>Same numbers as {pos}'s own reports - kept here for convenience.</p>
          <ul style={{ marginBottom: 0 }}>
            <li>
              <strong>Sales channels:</strong> weekly net sales split by how the order came in, from {pos}'s
              dining option: In-store (the counter), Pick up (the kiosk), Online ordering, DoorDash, Uber Eats and
              Catering; the rest, and anything past the five biggest, is Other. "Share" shows each channel's
              percentage of the week. The current week runs to yesterday and is marked *.
            </li>
            <li>
              <strong>Check size &amp; guests:</strong> average check (net sales per check) and orders and guests
              per day, as weekly averages or day by day. It shows whether sales moved because more people came or
              because each spent more.
            </li>
            <li>
              <strong>Busy hours:</strong> a grid of weekday by hour, darker = busier: each square is that hour's
              average net sales (or orders) over the last 4, 8 or 12 weeks. Point at a square, or tab into the grid
              and use the arrow keys, to read its numbers. The busiest and quietest open hours are listed under it.
            </li>
            <li style={{ marginBottom: 0 }}>
              <strong>Best &amp; worst sellers:</strong> the top and bottom items by net sales in the period, with
              the same Kitchen / Grocery filter as Menu trends.
            </li>
          </ul>
        </div>
      </details>

      <details className="help-section" id="reorder">
        <summary>Reorder Candidates</summary>
        <div className="help-section-body">
          <p>
            Items projected to run out before you'd likely be able to restock them, across every
            supplier at once, grouped by vendor - an early-warning list. For one supplier at a time
            with a draft PO to send, use Supplier Projection instead.
          </p>
          <h4>Urgency labels</h4>
          <p>
            "Lead time" below is the vendor's real days until its next delivery when it has a
            schedule on the Vendor Order Calendar, otherwise the lead time you type in on this page.
          </p>
          <ul>
            <li>
              <strong>Critical</strong> - sells at least 2 a week and is either truly empty (0 or
              less on hand, no sales in the last 7 days) or has 2 days or less left. You're losing
              sales now, or will be within a couple of days.
            </li>
            <li>
              <strong>Order now</strong> - runs out within the lead time, so it needs to go on this
              order. Includes empty items that sell 1-2 a week.
            </li>
            <li>
              <strong>Check count</strong> - shows 0 or less on hand but still sold in the last 7
              days, so the {pos} count is wrong (you can't sell what isn't there). Fix the count in
              {pos} before deciding how much to order.
            </li>
            <li>
              <strong>Can wait</strong> - runs out between the lead time and twice the lead time;
              it can go on the next order.
            </li>
            <li>
              <strong>Low priority</strong> - more than twice the lead time of stock, or empty and
              selling under 1 a week.
            </li>
            <li>
              A <strong>vendor</strong> takes its most urgent item's label, except that a vendor is
              Critical only with 3 or more Critical items, or with a Critical item that sells 7 or
              more a week (otherwise it shows as Order now). Each vendor also shows how many items
              fall under each label, e.g. "7 critical · 10 order now · 10 check count".
            </li>
          </ul>
          <h4>Adding to the Purchase Order Cart</h4>
          <p>
            With Purchase Order Cart access, each vendor's list has tick boxes and Add buttons, the same
            as the vendor screen in the app. Items always go into the cart of the vendor they're listed
            under here. An item {pos} lists under several vendors appears under just one of them
            (normally the one it was last received from) and goes into that vendor's cart only.
          </p>
          <ul>
            <li>
              <strong>Add 1 to cart</strong> (under an item's name) puts 1 of that item in the vendor's
              cart, like the + on an item in the app. Change the quantity in the cart.
            </li>
            <li>
              <strong>Add selected (N)</strong>: tick the items you want (the box in the header ticks
              every item listed), then check them in a panel before they're added.
            </li>
            <li>
              <strong>Add all Critical + Order now (N)</strong>: the vendor's Critical and Order now
              items, in the same panel, like the app's button of the same name.
            </li>
            <li>
              In the panel each item starts at its <strong>suggested quantity</strong>: the Shortfall,
              in units. It goes in as Case of 1, so 6 means 6 units; if you order by the case, change Qty
              and Case of in the cart. Change any quantity (more than 0, up to 100,000), or untick an item
              to leave it out, then click <strong>Add N items to cart</strong>. The whole list is added in
              one go.
            </li>
            <li>
              Items already in the vendor's cart show <strong>In cart ✓</strong> and are always left out,
              because adding one again would replace the quantity already in the cart. The message says how
              many were left out. Change their quantity on the cart page.
            </li>
            <li>
              The cart is shared with the app and everyone else. The page checks it again right before
              every add, so an item someone added from a phone meanwhile is left out, never overwritten,
              and the message says so.
            </li>
            <li>
              After adding you'll see "Added N items to the vendor's cart" with an{" "}
              <strong>Open cart</strong> link. It opens the Purchase Order Cart at that vendor, already
              expanded, with everything you added from this page marked <strong>Just added</strong> for a few
              seconds.
            </li>
            <li>
              Items under <strong>Unassigned</strong> have no vendor in {pos}, so they can't be added from
              here. Add them on the Purchase Order Cart under the vendor you order them from.
            </li>
            <li>The demo account can look around but can't add anything to the cart.</li>
          </ul>
          <h4>Hiding a Critical vendor from Today (admins)</h4>
          <p>
            A Critical vendor you've already dealt with can be taken off the app's Home{" "}
            <strong>Critical</strong> tile and the morning critical-vendors notification. It's hidden
            for everyone, not just you. It still shows here with a <strong>Hidden from Today</strong>{" "}
            tag, and its urgency doesn't change. On a Critical vendor's row, pick:
          </p>
          <ul>
            <li>
              <strong>Order already placed</strong>: the order went in another way (phone, WhatsApp, the
              vendor's rep). It's saved right away, like Mark placed but with no items, and the vendor shows
              as <strong>Ordered</strong> ("placed outside the app") until a delivery is recorded - so it
              leaves the Critical tile and the morning notifications by itself. Placed Orders lists it as
              Placed outside the app. <strong>Undo</strong> (on the message, the tag, or under Hidden from
              Today) removes it.
            </li>
            <li>
              <strong>Other</strong>: the same as Close with the reason Other (a note is needed).
            </li>
            <li>
              <strong>Close</strong>: it's handled. Pick a reason: ordered / will order, not ordering
              from this vendor right now, stock counts are wrong, or other (with a note). It comes
              back as soon as an item that wasn't critical when you closed it turns critical. The same
              items staying critical, or some of them restocking, keep it hidden. It comes back after
              14 days at the latest, so nothing stays hidden forever.
            </li>
            <li>
              <strong>Skip this week</strong>: it comes back when the next store week starts, Monday
              at midnight ({storeZoneCity()} time). On a Sunday, that's the next day.
            </li>
            <li>
              <strong>Remind me later</strong>:
              <ul>
                <li>
                  Later today: 4 hours from now, but never past 9 PM. After 5 PM it becomes
                  tomorrow 9 AM.
                </li>
                <li>Tomorrow 9 AM.</li>
                <li>In 3 days at 9 AM.</li>
                <li>Pick a date: 9 AM that day, up to 30 days ahead.</li>
              </ul>
            </li>
          </ul>
          <p>
            A note is optional. A newer action on the same vendor replaces the old one.
            <strong> Show again</strong> (on the tag, or under <strong>Hidden from Today</strong> at the top
            of the page) brings it back right away. So does <strong>Undo</strong> on the message you see
            after hiding. The Hidden from Today list shows who hid each vendor, when and why. Admins
            also see the last 60 days of history there. A hidden vendor that stops being critical
            simply isn't on the tile. If it turns critical again before its time is up, it stays hidden,
            except that a Close still comes back once something new turns critical.
          </p>
          <h4>How it's calculated</h4>
          <ul>
            <li>
              <strong>Avg/day</strong> - average daily quantity sold over the "Lookback (days)" you
              choose (a simple moving average; it doesn't account for seasonality or trends). Click it
              to see the week-by-week sales in that window, each with the item's average on hand that
              week (same as on Supplier Projection).
            </li>
            <li>
              <strong>Forecast (lead time)</strong> - Avg/day × the "Vendor lead time (days)" you
              type in (how long it typically takes that item to arrive once ordered - this is a
              manual estimate, not looked up automatically, since {pos}'s vendor data isn't
              accessible with the current account credentials).
            </li>
            <li>
              <strong>Shortfall</strong> - Forecast (lead time) minus current on-hand. Only items
              with a positive shortfall (projected to actually run short) are listed.
            </li>
          </ul>
          <p>
            Only items with a known on-hand count (present in the latest inventory upload with a
            real quantity) are considered - a prepared kitchen item that's never quantity-tracked in
            the retail export won't falsely show up as "out of stock." Nothing here is sent to a
            vendor: adding items only puts them in the shared Purchase Order Cart, where you check the
            order and make the PO yourself.
          </p>
        </div>
      </details>

      <details className="help-section" id="supplier-projection">
        <summary>New order (Supplier Projection)</summary>
        <div className="help-section-body">
          <p>
            The same idea as Reorder Candidates, but for one supplier at a time, over one
            projection duration you pick - built for actually putting an order together for that
            vendor.
          </p>
          <h4>How to use it</h4>
          <ol>
            <li>Pick a supplier.</li>
            <li>
              <strong>Lookback</strong> - the sales history window used to calculate average
              demand. Pick a preset (1 week, 2 weeks, 1 month, 3 months - 3 months is the default)
              or choose <strong>Custom range…</strong> to pick an exact start and end date from a
              calendar.
            </li>
            <li>
              <strong>Projection duration</strong> - how far out to project. Pick a preset (1
              week, 2 weeks, 3 weeks, 1 month, 2 months, 3 months) or choose{" "}
              <strong>Custom…</strong> to pick a target date from a calendar instead. This drives
              the "Need" column in the table.
            </li>
            <li>
              Click <strong>Generate projection</strong>. Every item that supplier is listed against
              in the latest inventory upload shows up, sorted by how much you'd need to order.
            </li>
            <li>
              <strong>Avg/day</strong> and <strong>Avg/week</strong> are the same underlying
              velocity, just at two scales - click either one to expand the actual week-by-week
              sales it's averaged from, so you're not trusting a single number blind. Each week also
              shows <strong>Avg on hand</strong>: the average of that week's daily stock counts (the
              morning count of each day, Container row included). Only days with a count are averaged
              - "(6 of 7 days)" means one day had none - and "-" means no day did. A week that sold
              little while the shelf sat near zero was a stockout, not low demand. Weeks with no sale
              are listed too, so you can see stock that didn't move. Fix Issues' item details show the
              same table for the last 12 weeks.
            </li>
            <li>
              <strong>Filter chips</strong> (above the search box) - All, Regular, Intermittent, Slow,
              Dead (Unclassified for items with no forecast type), Review and Needs order (Need above
              0). Types combine (Regular + Slow shows both); Review and Needs order narrow further, and
              all of it works with the search and sort. Each chip shows how many rows it would leave
              and has its own (i) definition; the choice is remembered for the browser session, but
              the Vendor Calendar and Overview "Order due" links always open the full list. The
              header checkbox still selects only the rows shown.
            </li>
            <li>
              <strong>Need</strong> - projected demand over the chosen duration minus current
              on-hand. An asterisk (*) means on-hand quantity isn't tracked for that item, so the
              number shown is just projected demand, not netted against stock.
            </li>
            <li>
              <strong>On hand</strong> automatically folds in that item's Container-location count
              when there is one - some items show up in the daily upload as two rows sharing one
              name: a regular priced row, and a second bare row (no price, no supplier) that's just
              the on-hand count sitting in a different storage location. Those are the same
              physical item, so their quantities are added together; a small "(+N container)" note
              next to the number shows how much of it came from that second row.
            </li>
            <li>
              Check the items you actually want to order. Each ticked item gets an{" "}
              <strong>Order qty</strong> box, filled in with its <strong>suggested order qty</strong>, and
              the numbers it comes from are written under its name - for example "sold ~14/week · on hand
              6 (avg 9) · next delivery Thu Oct 16 (order by Wed Oct 15) · suggested 10 for 1 month". The
              suggestion is the Need figure for the projection duration you picked (projected demand +
              safety buffer - on hand, a negative count counting as 0, rounded up to whole units), minus
              anything already in another vendor's cart for the same item. When the pack size is known
              (Case of above 1 on this vendor's cart line or on the last order placed with it) it is in
              cases, rounded up - an item already in this vendor's cart always uses that line's own Case of
              (1 = units). 0 means enough stock for that period. An item with no stock count is
              suggested its projected sales, and says the stock wasn't subtracted. "avg" is the average
              daily count over the last 4 weeks. When the next delivery has already been ordered, the
              note says "already ordered" instead of "order by", and an item on that order says how many
              are on order - the suggestion isn't lowered by it, so check it before adding more. Change any box, then click <strong>Add to cart</strong>:
              each item goes into the shared <strong>Purchase Order Cart</strong> at the quantity in its
              box (a ticked item at 0 is skipped and named). An item already in the cart is pre-ticked
              and shows <strong>In cart: N</strong>; its box starts at the cart quantity, Add to cart
              never changes it, and its own <strong>Update</strong> button sets the cart to the number in
              the box. Unticked rows show the suggestion in grey. It doesn't take you anywhere, so you can keep checking
              items from this supplier, switch to another supplier and add more, or come back and
              add to the same cart another day. The cart remembers everything until you remove it.
            </li>
          </ol>
          <h4>Purchase Order Cart</h4>
          <ul>
            <li>
              The cart page (the blue cart button at the top right opens it) has one panel per vendor. Every
              vendor starts <strong>collapsed</strong>: its header shows the vendor, how many items and cases
              (and units, when a case holds more than one), the estimated cost, and a{" "}
              <strong>removal request</strong> badge when someone has asked to take a line out. Click a header
              (or Tab to it and press Enter or Space) to open or close that vendor; <strong>Expand all</strong>{" "}
              and <strong>Collapse all</strong> are at the top of the list. The vendors you open stay open in
              this browser, until that vendor's order is placed. A link to one vendor's cart - "open the cart"
              after Order again on Placed Orders, or "View purchase order cart" on Supplier Projection - opens
              that vendor.
            </li>
            <li>
              <strong>Order placed</strong> and <strong>Download PDF</strong> are on every vendor's header, so they
              work without opening it. Download PDF gets the finished order document for just that vendor; the
              other vendors stay in the cart untouched, and a line left at 0 is skipped in the PDF but stays in
              the cart. Once the order has actually gone to the vendor, click Order placed and confirm - that
              records it on Placed Orders (see below) and empties that vendor's cart. A downloaded PDF alone
              shows there as "PDF only".
            </li>
            <li>
              Open a vendor to change its quantities (they save automatically): <strong>Qty</strong> is cases,{" "}
              <strong>Case of</strong> is units in one case, and the estimated cost is Qty × Case of × the
              item's cost on file. Each line shows <strong>In stock: N</strong> under its name - what's on hand
              in the latest inventory upload, including its Container row ("-" for an item added by hand that
              isn't in inventory). A moment after the cart opens, that changes to the line's projection, e.g.
              "~14/week · suggested 10 · on hand 6" - sales per week, the suggested order qty for 1 week (sales
              from the last 3 months, the same as Add items) and on hand; hover it (or tap the (i)) for the
              full reasoning. When the suggestion differs from the line's qty, <strong>Use 10</strong> sets it. Cost, the estimate and the stock figure are only for you: none of them appear
              on the PDF. <strong>Clear this supplier's cart</strong> (admins) is at the bottom of an open vendor.
            </li>
            <li>
              Only an <strong>admin</strong> can remove an item from the cart, set a quantity to 0, or clear a
              supplier's cart. Anyone else clicks <strong>Request removal</strong> on the line (a reason is
              optional); the line shows "Removal requested by ..." and admins get a notification. An admin
              then clicks <strong>Approve removal</strong> (the line is removed) or <strong>Reject</strong>{" "}
              (it stays). Whoever asked can <strong>Cancel request</strong>. Order placed still empties
              that supplier's cart for everyone.
            </li>
            <li>
              <strong>Add items</strong> (the card at the top of the cart) adds several items to one vendor's
              cart in one go. <strong>How this works</strong> on the card has a short version of this:
              <ul>
                <li>
                  Pick the <strong>Vendor</strong> first - it then shows as a chip with <strong>Change</strong>{" "}
                  next to it (when the cart holds just one vendor, that one is already picked). A name you type
                  that isn't a vendor in inventory can still be used, and is marked{" "}
                  <strong>Not a vendor in inventory</strong> so a misspelling doesn't start a cart section of its
                  own unnoticed. Changing the vendor while your list has items asks first.
                </li>
                <li>
                  <strong>This vendor's products</strong> (the default) searches that vendor's products only;{" "}
                  <strong>All products</strong> searches everything - each result then shows the vendor(s) it's
                  listed under, and whatever you tick still goes into the vendor you picked (the same as the
                  app). A Container row is never listed on its own; its count is in the item's in-stock figure.
                </li>
                <li>
                  Each result shows the item, the chosen vendor's SKU, how many are in stock, sales per week
                  and <strong>Need N</strong>, <strong>Enough</strong> or <strong>Already in cart</strong>. The
                  first 8 are listed; <strong>Show all</strong> lists the rest of the matches.
                </li>
                <li>
                  Tick as many as you need. They collect in the <strong>To add</strong> table under the search,
                  each with its stock, a note on where its quantity comes from, the quantity (type it, or use −
                  and +) and an ✕ to take it out again. You can change the search, or switch lists, and keep
                  ticking - the list stays.
                </li>
                <li>
                  Each quantity starts at the <strong>suggested order for 1 week</strong> - the projected need
                  from the last 90 days of sales minus what's in stock, the same suggestion the app makes -
                  shown in the note as "Suggested N for 1 week". "Enough stock for 1 week" means it probably
                  isn't needed, and it starts at 1. It also starts at 1 when there's no suggestion for the
                  item, and the note says why (no stock count on file, no sales projection for it, or it's
                  listed under another vendor) - or when you don't have Supplier Projection access. The
                  suggestion counts single units, so change it if you order that item by the case. Any quantity
                  you type or step is kept. A quantity can be at most 100,000.
                </li>
                <li>
                  The bar under the list shows how many items you're adding, and{" "}
                  <strong>Add to &lt;vendor&gt;'s cart</strong>; it stays in view while the list is long. It adds
                  the whole list at once and says "Added N items to &lt;vendor&gt;" - that vendor opens in the
                  cart below with the new lines highlighted for a few seconds (<strong>View in cart</strong>{" "}
                  scrolls there). The list then empties, while the vendor and the list you were searching stay,
                  ready for the next one. Just before adding, it checks the cart again: an item someone added to
                  that vendor's cart since you opened the page (on the phone, say) is left out, so its quantity
                  isn't replaced, and the message says so. If the add fails or the connection drops, it looks at
                  the cart and says exactly what got in - nothing in your list is lost, and nothing is added
                  twice.
                </li>
                <li>
                  An item already in that vendor's cart is greyed out and marked{" "}
                  <strong>Already in cart</strong>, and can't be ticked - adding it again would replace the
                  quantity already in the cart, so change it in the cart instead.
                </li>
                <li>
                  Not in inventory yet? When a search finds nothing, <strong>Add it as a new item</strong> lets
                  you type the name (and the vendor's SKU, if you have it) into the same list. With{" "}
                  <strong>This vendor's products</strong> selected, try <strong>Search all products</strong> first
                  - the item may be listed under another vendor. If the name you type is already an item in
                  inventory, it says so and offers to show it in the search, so it goes in with its SKU and
                  stock (or you can still add it as a new item).
                </li>
                <li>
                  Keyboard: <strong>Enter</strong> in the search ticks the top match, <strong>↓</strong> moves
                  into the results (Enter or Space ticks, ↑ ↓ move, and ↓ past the last one shown lists the
                  rest), <strong>Esc</strong> closes them; the arrow keys switch between the two lists. In To
                  add, ↑ ↓ in a quantity box change it by one.
                </li>
              </ul>
            </li>
          </ul>
          <h4>Also at and Similar</h4>
          <ul>
            <li>
              <strong>Also at</strong> (under an item's name) - the other vendors {pos} lists for
              that item, with each vendor's own item code when {pos} has it. Handy when this vendor
              is out or another one is cheaper.
            </li>
            <li>
              <strong>Similar</strong> (button under the item name, with a count) - other products in
              the store of the same kind: other brands and pack sizes of the same product first
              ("Same product, other brand", "Same product, different size"), then close variants
              ("Similar type"). E.g. Deep Shredded Coconut finds Daily Delight Grated Coconut and
              Laxmi Grated Coconut 400g / 1.2kg. Each shows on hand (shelf + container), price, cost,
              every vendor with its code and units sold in the last 30 days, with{" "}
              <strong>Add to cart</strong> (to its vendor - pick one when it has several).
            </li>
            <li>
              Similar products are matched by name inside the dashboard (no internet or AI): pack
              size and brand are taken off, spellings and Indian names are made the same (toor / tuvar
              / arhar, jeera / cumin, rava / sooji...), and the rest is compared. About 9 in 10 of the
              first five suggestions are right; it can miss a product whose name shares no product
              word (e.g. a brand-only name), and it can't fix an item {pos} has in the wrong category.
              Kitchen / catering items and Container rows are never listed.
            </li>
          </ul>
          <h4>About the exported PDF</h4>
          <p>
            It opens with the store's letterhead - logo, address, and phone number - then
            a table with just four columns: a serial number, the supplier code, the item name, and
            the quantity you entered.{" "}
            <strong>It is not a real {pos} purchase order</strong> - {pos}'s Purchasing API isn't
            accessible with the current account credentials, so nothing goes to {pos}. You can send it
            to the vendor yourself (email, print, WhatsApp), or email it from here with{" "}
            <strong>Send PO</strong> (below).
          </p>
          <h4>Send PO (email the vendor from the cart)</h4>
          <ul>
            <li>
              <strong>Send PO</strong> on a vendor opens a preview: who it goes to (the vendor's emails on{" "}
              <strong>Vendor contacts</strong> - those marked "Gets POs" are ticked), the subject, the
              message (add a note if you like) and the attached PDF, which is the same PDF as Download
              PDF (lines at 0 are left off). <strong>Preview PDF</strong> shows it before sending.
            </li>
            <li>
              Send emails it from the store's Gmail. Once it has gone out, the order is recorded as placed
              (exactly like <strong>Order placed</strong> - it shows on Placed Orders) and those lines leave
              the cart. A line added while it was sending stays in the cart.
            </li>
            <li>
              If it doesn't go out, nothing changes: the reason is shown and <strong>Try again</strong>{" "}
              sends it. Pressing Send twice (or a slow connection retrying) never emails twice.
            </li>
            <li>
              Rarely the connection breaks mid-send, so it isn't known whether the email left. The cart is
              left alone; check Gmail's Sent folder, then either <strong>It went out - record as
              placed</strong>, or tick that it isn't there and <strong>Send again</strong>.
            </li>
            <li>
              The To emails must be saved on Vendor contacts - nothing can be typed in there (CC, below,
              can take any email address). Only an admin
              adds or changes contacts (Ordering &gt; <strong>Vendor contacts</strong>, or Contacts on a
              vendor in the cart); anyone with the Purchase Order Cart can send. The demo login can't.
            </li>
            <li>
              WhatsApp is off for now: the store's WhatsApp sender is on a plan that can only message 5
              chats, so a PO to a new vendor chat would fail. Download the PDF and share it yourself.
            </li>
            <li>
              Every send (who, when, to whom, CC, sent or not) is listed under{" "}
              <strong>Recent PO sends</strong> on Vendor contacts.
            </li>
            <li>
              <strong>CC</strong>: a contact can have default CC emails (up to 10, on Vendor contacts). Send PO
              fills them in; remove one or add another for that send only (it must be a real email address).
              Everyone in CC gets a copy of the email.
            </li>
            <li>
              <strong>Search by code</strong>: every product search (cart Add items, Reorder, Supplier Projection,
              Fix Issues, Items Sold, PO sections, Store vs Container...) also finds an item by its vendor item code
              (SKU), barcode or {pos} item ID - the whole code, or part of it (3+ characters of a SKU, 4+ of a
              barcode). A result found by a code says so, e.g. "Matches SKU 2659M".
            </li>
          </ul>
          <p>
            <strong>Supplier code</strong> is the vendor's own SKU, sourced straight from your
            daily inventory upload when {pos} has it on file for that item/vendor - it's left
            blank on the PDF for items where {pos} itself doesn't have it recorded.
          </p>
        </div>
      </details>

      <details className="help-section" id="placed-orders">
        <summary>Placed Orders</summary>
        <div className="help-section-body">
          <p>
            A full history of purchase orders from the Purchase Order Cart, newest first. Open it from
            Ordering &gt; <strong>Placed Orders</strong>, or "See all placed orders" on the Purchase
            Order Cart. Filter by vendor, date range (store time) and status, or search by item name,
            code or vendor. Click an order to see its items.
          </p>
          <h4>Placed vs. PDF only</h4>
          <ul>
            <li>
              <strong>Placed</strong> - someone clicked <strong>Order placed</strong> on the Purchase
              Order Cart (web) or <strong>Mark placed</strong> (app). It records exactly what was in
              that vendor's cart.
            </li>
            <li>
              <strong>PDF only</strong> - a PO PDF was made (downloaded, or opened in the app to share
              on WhatsApp) but the order was never marked placed.
            </li>
          </ul>
          <p>
            <strong>No duplicates:</strong> making the PDF again within a day updates the same entry,
            and marking it placed within 2 days turns that entry into the Placed order - so a PDF plus
            Order placed is one order, not two. Clicking Order placed twice by accident also gives one
            order.
          </p>
          <p>
            <strong>Only Placed orders count as ordered.</strong> A PDF-only entry doesn't close the
            "Place the vendor order" task and isn't the vendor's "Last placed" date on Reorder
            Candidates. Sending the PDF on WhatsApp still needs Order placed / Mark placed to count.
            Recent Orders Placed on the cart (and on the app's Home) only lists Placed orders.
          </p>
          <h4>Order again</h4>
          <p style={{ marginBottom: 0 }}>
            Open an order and click <strong>Order again</strong> to put its items back into that
            vendor's cart with the same quantities (an item already in the cart is set to the order's
            quantity). Then review the cart as usual.
          </p>
        </div>
      </details>

      <details className="help-section" id="vendor-calendar">
        <summary>Vendor Calendar</summary>
        <div className="help-section-body">
          <p>
            Set each vendor's delivery day(s) and how many days ahead the order must go in, once; the calendar then shows
            every order-due and delivery day by itself.
          </p>
          <p>
            An <strong>Order due</strong> chip turns red with a count, e.g. "(7)", only when Supplier Projection says 5 or
            more items need ordering for that delivery - vendors have minimum orders, so 1-4 low items are not an order.
            Those show as "(2 low)" without the red, and the 9 AM reminder says "2 items low" instead of "to order".
          </p>
          <h4>Suggested schedules</h4>
          <p style={{ marginBottom: 0 }}>
            For a vendor with no schedule yet, the page reads the delivery days off the last 12 weeks of everything that
            records a delivery: invoice uploads and deliveries logged by hand (the delivery day itself), {pos}'s receiving
            records in the daily inventory files, Delivery Review confirms and stock rises (the day before). Pieces of one
            delivery (an invoice and its receiving the next day) count once. It suggests the weekday(s) most deliveries
            fall on, with the evidence ("Delivered Tue x6, Fri x5 in the last 12 weeks") and a confidence - High, Medium
            or Low - most confident first. The order day comes from when its orders were marked placed; with none on
            record it's yours to set (1 day before until you do). <strong>Add schedule</strong> saves it as shown,{" "}
            <strong>Edit</strong> lets you change the days and lead time first, <strong>Dismiss</strong> hides it (a
            different pattern later is suggested again). <strong>Add all suggestions</strong> lists them all to confirm
            and adds the ticked ones: High and Medium confidence start ticked, Low ones start unticked (tick any you
            want). A schedule repeats every week, so a vendor that delivers only every 2-4 weeks (or rarer) is marked
            "weekly isn't right" and starts unticked too. A vendor that already has a schedule is never
            changed - by Add, Edit or Add all - and if its recent deliveries fall on other days you only see a "Check"
            line. Below, <strong>Vendors without a schedule</strong> lists every other vendor {pos} has on an item (or
            that delivered lately) and why there's no suggestion (too few deliveries, scattered days, or dismissed), each
            with <strong>Set schedule</strong>.
          </p>
          <h4>Dealing with an order without opening it</h4>
          <p>
            Click an <strong>Order due</strong> chip for its menu. Besides <strong>Review order</strong>:
          </p>
          <ul>
            <li>
              <strong>Order already placed</strong> (admins): the order went in another way. It's recorded like
              Mark placed (no items) for that delivery, so the chip turns <strong>Ordered</strong>, and the app's
              Orders due tile, the order reminder and the vendor's Team order task leave it alone.
            </li>
            <li>
              <strong>Skip this week</strong>: no order for that delivery; the delivery shows as skipped.
            </li>
            <li>
              <strong>Other</strong> (admins, with a note): hides that one order for this cycle, like a skip, and
              the delivery shows the note.
            </li>
          </ul>
          <p style={{ marginBottom: 0 }}>
            Each one is for everyone and has <strong>Undo</strong> on the message afterwards, and later from the
            chip's menu. An order placed from the cart is undone on Placed Orders, not here. The app's Today card
            has the same choices on its Orders due and Critical tiles.
          </p>
        </div>
      </details>

      <details className="help-section" id="fix-issues">
        <summary>Fix Issues</summary>
        <div className="help-section-body">
          <p>
            Lists of inventory records that need attention, all from the latest inventory upload:
            barcode problems, duplicates, and stock problems (negative counts, shelves to refill,
            slow and dead items). Kitchen and catering menu items are left out, since they're
            made in the store and don't need barcodes or shelf stock. The count on each tile is how
            many are still open. Click an item's name for its details (price, cost, stock, sales, last
            sold, vendor and supplier code). Every list has a <strong>Download CSV</strong> button.
            For what a status like Slow, Dead or Review means, click the small (i) next to it for its
            definition, or open the Glossary tab of Help.
          </p>
          <h4>Sections, leads and daily tasks</h4>
          <p>
            Every list is grouped by <strong>PO section</strong> (the store aisle the purchase order uses), with
            the section's name small above its items. Filter by section(s), vendor, lead, or whether the item is
            in a lead's task. Each item shows its <strong>lead</strong>: the PO section's lead, else its vendor's
            lead, else the everything-else lead (an admin). Admins set the leads on{" "}
            <strong>Category & vendor leads</strong>, which starts from a proposal read from the owner's
            quick-reference document - nothing is assigned until it's saved there.
          </p>
          <p>
            Every day at the set time (10:00 AM unless an admin changes it) each lead gets <strong>one Team
            task</strong> with how many open items they have and a link to Fix Issues showing just theirs. It's
            due after the hours an admin sets (6 by default); overdue reminders work like any task. Items closed
            or fixed drop off the next day's task, which replaces the day before's.
          </p>
          <h4>Closing an issue</h4>
          <p>
            Click <strong>Close →</strong> on a row (or tick several rows and use Close selected). A
            panel slides in with a list of reasons made for that list - pick one. If you pick{" "}
            <strong>Other</strong>, type a few words about why. The item moves to that list's{" "}
            <strong>Closed</strong> view with the reason, who closed it and when (the Closed view shows
            the last 7 days). Anything closed by mistake can be reopened there.
          </p>
          <ul>
            <li>
              <strong>Not an issue</strong> (duplicates and false alarms): the item is never shown in
              that list again.
            </li>
            <li>
              <strong>"Fixed" reasons</strong> (Barcode added in {pos}, Count corrected in {pos},
              Merged / removed in {pos}, Moved stock to the shelf...): checked again on the next
              inventory upload. If the same problem is still there, the item <strong>comes
              back</strong> to the open list marked "Still showing after fix".
            </li>
            <li>
              <strong>Negative inventory and Refill from container come and go</strong>, so closing
              one covers that time only: once a later upload shows it cleared (count back at 0 or more,
              or the shelf above 5 or the container empty), the item comes back if it happens again. "Loose / weighed item"
              and "Item discontinued" on Negative inventory, and "Not an issue", stay closed.
            </li>
            <li>
              <strong>New item - giving it more time</strong> (Dead - newly arrived): "more time" means one
              week. The Closed view shows <strong>Back on</strong> with the date. After the week, if the item
              is still on the list (still no sale), it moves back to Open by itself, and one combined
              notification a day says how many items came back. If it sold or left the list in the
              meantime, it stays closed.
            </li>
            <li>Any other reason keeps the item closed until someone reopens it.</li>
          </ul>
          <p>
            The close panel shows what each reason does (and an (i) with its full definition) before you
            pick it.
          </p>
          <h4>Came back</h4>
          <p>
            An item that was closed and is back on the open list is marked <strong>Came back</strong>, with
            how: "more time ended", "still showing after fix" or "happened again", the date, and the
            reason it was closed with. Tiles show how many came back on each list, and the{" "}
            <strong>Came back only</strong> chip shows just those. Click the item's name to see how many
            times it was closed and its full close / came back history.
          </p>
          <h4>Closed report</h4>
          <p>
            The last tile shows everything closed from every list in a date range, grouped by day,
            with a count per reason at the top. Older closures from lists that were replaced (such as
            the old Dead inventory list) still show here.
          </p>
          <h4>Missing barcodes</h4>
          <p>
            Items with no barcode on file at all. Between uploads, this list and Invalid barcodes are
            re-checked every hour against {pos}'s menu, so an item fixed in {pos} drops off without
            waiting for the next upload (see Inventory from {pos}).
          </p>
          <h4>Invalid barcodes</h4>
          <p>
            Items with a barcode that fails validation - "invalid" means structurally malformed
            (wrong length, or fails the standard UPC-A/EAN-8/EAN-13/GTIN-14 check-digit formula
            every real barcode is built with). This can catch a typo or a truncated/garbled code,
            but it can't confirm a well-formed code is actually the right one registered to that
            product. An item listing several barcodes (common for different pack sizes of the same
            product) only shows up here if at least one of them fails.
          </p>
          <h4>Duplicates</h4>
          <p>
            Two or more priced items in {pos} with exactly the same name. The usual Container-location
            pair (one priced row plus one bare stock row) isn't counted - that's expected. Each name is
            one row here, with how many copies and their prices.
          </p>
          <h4>Negative inventory</h4>
          <p>
            Items whose on-hand count in the latest upload is below zero - not physically possible, so
            the {pos} count is wrong (items sold before the delivery was received in {pos}, a sale rung
            up under the wrong item, a miscount...). "Sold (30 days)" is units sold. Close with the reason
            that explains it, e.g. "Count corrected in {pos}" or "Sold before the delivery was received
            in {pos}". This used to be a report under Inventory Reports; it lives here now.
          </p>
          <h4>Refill from container</h4>
          <p>
            The shelf (priced) row shows <strong>5 or less</strong> while the same item's
            Container-location row still has stock - time to bring some out. Store qty and Container
            qty are shown side by side, lowest shelf first. Names with two priced rows are skipped
            (they're different products). "Moved stock to the shelf" is checked again on the next
            upload.
          </p>
          <h4>Slow moving - Review</h4>
          <p>
            Items the Supplier Projection forecast (its default 3-month window, counting only days the
            item was in stock) classes as <strong>Slow</strong> and also marks with the red{" "}
            <strong>Review</strong> tag. "Why review" says which: not enough history, a long stockout,
            a sales spike that was capped, an unusual trend, a negative on-hand count, or a forecast very
            different from the simple average. Only items with stock are listed: one at 0 on hand
            (shelf + container) is left out, and a negative count is on Negative inventory. Check the
            sales look right, then close it (e.g. "Reviewed - sales look right" or "Will order less").
          </p>
          <h4>Dead with stock</h4>
          <p>
            Items the same forecast classes as <strong>Dead</strong> (no sale in 60 days while in
            stock) that still have stock on hand (shelf + container above 0). Sorted by value tied up.
            It uses our own sales data and knows the days an item was out of stock, so an item that was
            simply sold out isn't called dead. This replaces the older "Dead inventory" list (from
            {pos}'s own 90-day sales figure).
          </p>
          <h4>Dead - zero stock</h4>
          <p>
            Items that aren't selling and have nothing left (shelf + container at exactly 0). An item
            gets on this list one of two ways: the forecast classes it <strong>Dead</strong> (no sale in
            60 days while in stock), or it hasn't sold in <strong>90 days</strong> (or has never sold).
            The second way catches items that sat at 0 for most of that time - they never had enough
            days in stock to be called Dead. Brand-new items still waiting for their first delivery are
            left out: an item counts as old if it has ever sold, or if it first showed up in the
            uploads (or was last received) at least 60 days before the latest upload. Decide what
            happens next: "Discontinued - won't reorder", "Deactivated in {pos}", "Seasonal - will
            restock" or "Reorder - it should sell" (these stay closed), or "Count corrected in {pos}"
            if there really is stock (checked again on the next upload). An item is never on this list
            and another one: Dead with stock, Dead - newly arrived and Slow moving - Review never list
            an item at 0, and a count below 0 (shelf or container) is on Negative inventory first.
            Items with no count in {pos} at all aren't listed.
          </p>
          <h4>Dead - newly arrived</h4>
          <p>
            <strong>Brand-new products only</strong>: an item that has never sold, arrived 5 to 30
            days before the latest upload, and has stock on hand. Arrival is the later of {pos}'s "last
            received" date and the day the item first showed up in the uploads. A restock of something
            that has sold before is never listed here, even if it hasn't sold since the delivery. After
            30 days it drops off this list; a new item still unsold by then normally shows up in Dead
            with stock instead.
          </p>
        </div>
      </details>

      <details className="help-section" id="inventory-reports">
        <summary>Inventory Reports</summary>
        <div className="help-section-body">
          <p>
            Three reports, checked against your latest inventory upload - pick one from the buttons at
            the top of the page. Every one has a{" "}
            <strong>Download CSV</strong> button. Barcode problems, duplicates, negative inventory and
            dead stock are on Fix Issues, where each item can be closed with a reason. (The old margin
            report has been removed.)
          </p>
          <h4>Download sales data</h4>
          <p>
            The raw sales export moved to <strong>Items Sold</strong> (under the table), next to the rest of the
            sales numbers.
          </p>
          <h4>Price change log</h4>
          <p>
            Every item whose price has changed recently, plus a search box to look up one item's
            full price/cost history regardless of whether it ever changed. Both work by comparing
            consecutive inventory uploads for the same item - so this is only as complete as your
            upload history. A single day on file has nothing to compare against yet (correctly
            shows "no changes," not an error). If you skip a few days between uploads, a price
            change in that gap still gets caught - it's just detected on the next upload rather
            than the day it actually happened.
          </p>
          <h4>Vendor price comparison</h4>
          <p>
            Flags an item where the vendor you most recently bought it from costs more than a
            different vendor already on file for that same item - the vendor you'd likely save
            money switching back to, shown alongside how much per unit.
          </p>
          <p>
            This one works differently from the others. The daily inventory file's{" "}
            <code>cost</code> field is a single blended number per item, and an item commonly lists
            several possible suppliers at once - there's no way to tell which of them that cost
            actually belongs to, so it can't be used to compare vendors directly. Instead, this
            report is built from real, vendor-attributed costs: every time you confirm a delivery
            in Delivery Review, that item's file cost is captured and tagged to the vendor you
            picked; a manually logged purchase with a cost entered works the same way. Once an item
            has a captured cost from two or more different vendors, it becomes comparable here.
          </p>
          <p>
            <strong>This starts out empty, and that's expected</strong> - it only has something to
            show once real vendor-tagged cost data exists, which builds up naturally as you use
            Delivery Review or log purchases with a cost.
          </p>
          <h4>Store vs Container</h4>
          <p>
            Only the products that have both a store (shelf) row and a Container row in {pos}, with
            Store, Container and Total stock side by side, plus a totals row. Rows are paired by exact
            name - the same pairing Supplier Projection's "+N container" and Fix Issues' Refill from
            container use, so the numbers match. Items <strong>Low on shelf</strong> (store 5 or less
            while the Container has stock) are listed first. Search by item, vendor or code, pick a
            vendor, and click a column to sort; Download CSV follows the search and vendor you picked.
          </p>
        </div>
      </details>

      <details className="help-section" id="reconciliation">
        <summary>Reconciliation</summary>
        <div className="help-section-body">
          <p>
            Purchased vs. sold vs. counted - the shrinkage/spoilage signal. For each item in a
            chosen window: <code>expected closing = opening count + purchased - sold</code>,
            compared against what was actually counted on the closing date. A negative variance
            means less is physically on hand than the math says there should be.
          </p>
          <h4>Why you have to log purchases by hand</h4>
          <p>
            {pos}'s Purchasing &amp; Receiving API isn't accessible with this account's current
            credentials, so there's no automatic feed of what was actually bought/received - "sold"
            (from {pos} orders) and "counted" (from your inventory uploads) are both automatic, but
            "purchased" only exists if you log it yourself below. The fastest way to do that is the{" "}
            <strong>"we received a delivery today"</strong> flow on the inventory upload (see
            Overview's help section) - it suggests quantities for you to confirm instead of typing
            each one from scratch. The form and CSV upload below are for anything that flow misses,
            or a past date.
          </p>
          <h4>Worked example</h4>
          <p>
            The worked example below isn't a canned one - it pulls one real item and its real opening count, real {pos}
            sales, and real next-count closing figure, over the most recent window your upload history actually
            supports, then walks through the exact formula with those real numbers. A "try it" field lets you type a
            hypothetical purchased quantity and see the expected closing and variance recalculate live, so you can see
            how logging a delivery would change the result before you start logging for real. It's only loaded when
            you open it.
          </p>
          {hasFeature("reconciliation") && <ReconciliationWorkedExample />}
          <h4>Log a purchase</h4>
          <p>
            Search for the real item (so it matches up correctly with sales/inventory data), enter
            the supplier, quantity received, and date - unit cost and notes are optional. Logged
            entries show up in the list below, where you can delete a mistaken one.
          </p>
          <h4>Bulk-logging from a CSV</h4>
          <p>
            For a whole vendor delivery at once, use "Bulk-log purchases from a CSV" under the
            form. Columns are matched by header name in any order - needed: an item column
            (<code>item name</code> or <code>item id</code>) and a <code>quantity</code> column;
            optional: <code>supplier</code>, <code>unit cost</code>, <code>received date</code>{" "}
            (rows with no date use the default date you pick before uploading), and{" "}
            <code>notes</code>. Item names have to match an existing inventory item exactly (not
            case-sensitive) - any row that can't be matched, or has a bad quantity/date, is skipped
            and listed after the upload instead of being guessed at.
          </p>
          <h4>Reading the report</h4>
          <p>
            <strong>Until purchase logging is routine, expect most items to show a variance</strong>{" "}
            - that's not real shrinkage, it just means nothing's been logged as purchased for that
            item yet, so any restocking that happened looks like unexplained loss. This report only
            becomes a meaningful signal once logging is consistent - treat early results as a
            reminder to log purchases, not a shrinkage report yet.
          </p>
          <h4>Invoices without a logged purchase</h4>
          <p>
            A different, simpler check using the Invoice Submission tab: for a chosen window, it
            flags any invoice or return submitted from a vendor with no purchase logged (Delivery
            Review or the manual form above) within a few days on either side - a real invoice that
            might have been forgotten. This only matches by <strong>vendor and date</strong>, never
            by item - an invoice's own item names won't reliably match inventory item names, but
            the vendor field is picked from the same supplier list used everywhere else in the app,
            so vendor matching is solid. Each flagged row links straight to the actual invoice file.
          </p>
        </div>
      </details>

      <details className="help-section" id="downloads">
        <summary>Downloading reports</summary>
        <div className="help-section-body">
          <p>
            Every report can be downloaded as a CSV file (opens in Excel or Google Sheets):
          </p>
          <ul>
            <li>
              <strong>Website:</strong> the <strong>Download CSV</strong> button on Items Sold, Open
              Checks (the date you picked, or All-time), the Reconciliation report, Invoices without a
              logged purchase and the purchase log, every Fix Issues list, and each Inventory Report
              (price change log, vendor price comparison, Store vs Container, sales export).
            </li>
            <li>
              <strong>App:</strong> the download icon (top right) on every report under More &gt;
              Reports - Items Sold, Open Checks, Vendor Price Comparison, Price Change Log,
              Reconciliation Report / Demo, Invoices Without a Logged Purchase, Log a Purchase, and the
              Fix Issues lists. It opens a preview with Share and Save.
            </li>
          </ul>
          <p style={{ marginBottom: 0 }}>
            The download always has the <strong>whole list</strong> for the dates or mode you picked,
            not just the rows a search box or sort shows on screen. On Fix Issues it's the whole Open or
            Closed list you're on; on the website's Open Checks it also follows the "Opened by" choice.
            The button is greyed out while the report is loading or when it's empty. Users whose sales numbers are hidden don't get sales
            downloads (see Users).
          </p>
        </div>
      </details>

      <details className="help-section" id="categories">
        <summary>PO sections</summary>
        <div className="help-section-body">
          <p>
            A <strong>PO section</strong> is the heading an item prints under on a vendor's purchase order
            PDF (for example "Spices - Whole" or "Frozen - Ice Cream"), so the vendor can pick the order
            aisle by aisle. Sections print A to Z with items sorted by name; lines with no section, like a
            hand-added item, print under <strong>Other</strong>, always last.
          </p>
          <p>
            <strong>Automatic</strong> is the section the dashboard picks from the item's category and name.
            The <strong>PO sections</strong> page (Reports &gt; PO sections; the old /categories address still
            works; app: More &gt; Product categories) changes it, for admins and anyone given the "Edit PO
            sections" access. On the <strong>Items</strong> tab, edit one item or tick several and set their
            section (pick one or type a new name to make a new section); <strong>Back to automatic</strong>{" "}
            undoes it. <strong>Unassigned / Other</strong> lists items that would print under Other.
          </p>
          <p>
            <strong>Vendor rules</strong> put every item from one vendor in a section, now and in future
            uploads (or "Move current items only" for a one-time move). An item's own rule wins over a vendor
            rule, and on a PO the rule for that PO's vendor is used first. The <strong>Sections</strong> tab
            shows every section with its item count and can rename a section, merge it into another, undo a
            rename, or move all of its current items elsewhere. <strong>PDF preview</strong> shows a vendor's
            current cart grouped exactly as its PDF.
          </p>
          <p>
            On the <strong>Purchase Order Cart</strong>, each vendor's lines are grouped under their PO
            sections in PDF order, and editors can change a line's section right there with the small
            "Section" link (a catalog item gets an item rule; a hand-added line keeps it on the line).
          </p>
          <p style={{ marginBottom: 0 }}>
            Every change records who made it and when (History), and the latest change can be undone. Only
            the PO section changes: the item's category is not edited here. Earlier category corrections
            (such as every item from one vendor moved to one category) still apply everywhere but
            are no longer editable from any page.
          </p>
        </div>
      </details>

      <details className="help-section" id="daily-checklists">
        <summary>Daily Checklists</summary>
        <div className="help-section-body">
          <p>
            The checklists staff fill in every night (for example a store closing checklist and the kitchen
            closing checklists), plus any new ones an admin builds. Each submission is saved under
            your login (you're never asked for your name) and a WhatsApp summary goes to the group set for
            that checklist.
          </p>
          <h4>Filling one in</h4>
          <p>
            Answer every question marked with a red *. "All as usual" fills the normal answer for every
            row you haven't answered yet in that section; answers you already gave aren't changed. Some
            questions take you to a different section depending on your answer, so the sections you see
            can change as you go. If something is missing or in the wrong format, Submit shows a red
            message under that question.
          </p>
          <p>
            Photo questions use your phone camera or photo library. Answers that count as a problem are
            marked ⚠️; picking one marks the submission <strong>Issues</strong> and lists it in the
            summary. When a checklist asks "Is there anything unusual?" as a required question, type "None"
            if nothing was.
          </p>
          <p>
            History shows every answer, drawn with the version of the checklist that was filled in, so an
            old submission still shows questions that were removed later.
          </p>
          <p>
            At 9:30 PM one "Closing checklist not done" alert lists whichever closing checklists are still
            missing tonight. On Overview, the Store closing tile shows the latest closing checklist; closing
            checklists that go to the kitchen WhatsApp group share a Kitchen closing tile.
          </p>
          <h4>Checklist builder (admins)</h4>
          <p>
            <strong>Checklist builder</strong> (Setup in the menu, or the button on Daily Checklists) works like
            Google Forms. Admins can create a checklist, duplicate one, turn it on or off, and change the
            order staff see them in.
          </p>
          <ul>
            <li>
              <strong>Questions</strong>: click a question to edit it and pick its type: short answer,
              paragraph, multiple choice, checkboxes, dropdown, linear scale, rating, multiple choice grid,
              checkbox grid, date, time or photo upload. You can also add a title with a description, an
              image, or a new section. Each option can flag an issue (Critical, Low Stock and so on).
            </li>
            <li>
              The ⋮ menu has the extras: description, response validation (numbers, text, length, how
              many boxes), "go to section based on answer", shuffle, "Other", flag boxes left unticked,
              and show the answer next to "Submitted by". Move questions with the arrows or by dragging
              them.
            </li>
            <li>
              The preview on the right shows exactly what staff see. You can fill it in to try the
              branching and the checks; nothing is sent.
            </li>
            <li>
              <strong>Save draft</strong> keeps your changes without staff seeing them.{" "}
              <strong>Publish</strong> lists what changed and makes it live as a new version. Leaving with
              unsaved changes asks first.
            </li>
            <li>
              <strong>Settings</strong>: active, counted as a closing checklist (9:30 PM alert and nightly
              task), which WhatsApp group gets the summary, and the "All as usual" button. These apply
              straight away.
            </li>
            <li>
              <strong>Versions</strong>: every publish is kept. View any old version, or revert to one,
              which publishes it again as a new version.
            </li>
            <li>
              <strong>Public link</strong>: a link for people without an app account (for example the
              part-time service staff) to fill in that one checklist and nothing else. Turn it on, set the
              names they pick from (or let them type a name), set how many submissions an hour it allows,
              then <strong>Copy link</strong> and send it to them. A submission through the link is saved
              and sent to WhatsApp just like one from the app, counts for the 9:30 PM alert and the nightly
              task, and shows in History with the person's name and "via public link". Turning the link
              off, changing its name or <strong>Make a new link</strong> stops the old link straight away
              (it then shows "This link is not active").
            </li>
          </ul>
          <p>
            Phones on an older app build can fill checklists made only of grids, single-choice questions
            and tick lists. A checklist using other question types shows "App build 6+" in the builder,
            and older phones are asked to update the app.
          </p>
        </div>
      </details>

      <details className="help-section" id="team">
        <summary>Team: tasks, templates and chat</summary>
        <div className="help-section-body">
          <h4>Tasks: list, board, overdue and stats</h4>
          <ul>
            <li>
              <strong>List</strong> groups open tasks into Overdue, Today and Upcoming. <strong>Board</strong> shows the same
              tasks as cards in three columns: <strong>To do</strong>, <strong>In progress</strong> and{" "}
              <strong>Done</strong>. Drag a card to another column, or use its buttons (Start, Back to To do, Done, Reopen).
              Moving a task that needs a photo or a note to Done opens it so you can add one.
            </li>
            <li>
              <strong>In progress</strong> only tells everyone someone has started; the task is still open, and anyone it's for
              can move it back. Reassigning a task puts it back in To do.
            </li>
            <li>
              <strong>Overdue only</strong> shows just the open tasks that are past their due time.
            </li>
            <li>
              <strong>Stats</strong> (admins) covers tasks created in the last 7, 14, 30 or 90 days, cancelled ones left out.
              Per person: tasks assigned to them and how many are done, everything they finished (their teams' tasks
              included), how many were on time (finished by the due time; no due time counts as on time) or late, overdue
              now, can't do, and the typical (median) time from created to done. Team tasks also get their own rows.
            </li>
          </ul>
          <h4>Recurring tasks and templates</h4>
          <ul>
            <li>
              On the website they're under <strong>Setup → Recurring tasks</strong> in the menu (admins).
            </li>
            <li>
              A template is a reusable task with optional <strong>steps</strong> (one per line, e.g. "Open store": lights on,
              count the till, unlock the doors). Every task made from it gets the steps as tick boxes.
            </li>
            <li>
              <strong>On a schedule</strong>: it creates tasks by itself (daily, weekdays, weekly or monthly). Use{" "}
              <strong>Someone else on some days?</strong> to give particular weekdays to a different person or team (e.g.
              Saturdays to one person); other days go to the template's own assignee.
            </li>
            <li>
              <strong>Only when I create it</strong>: saved without a schedule. Any template has{" "}
              <strong>Create task now</strong>, for its own assignee (today's weekday one, if set) or anyone you pick.
              Templates are for admins and people who can assign tasks.
            </li>
          </ul>
          <h4>Chat</h4>
          <ul>
            <li>
              <strong>Voice notes</strong>: in the app, tap the microphone, talk, then tap Send (up to 5
              minutes). On the website the 🎤 button shows only in browsers that can record the same AAC audio the phones
              play (Safari, recent Chrome and Edge); everyone can play voice notes.
            </li>
            <li>
              <strong>Documents</strong>: 📎 sends a PDF (up to 10 MB). Its name is shown; tap Open to read it.
            </li>
            <li>
              <strong>@mentions</strong>: type @ and pick a name. The person gets a "mentioned you" notification even in a
              chat they muted (they can switch Chat mentions off in the phone app: More → Notifications).
            </li>
            <li>
              <strong>Pinned messages</strong> stay at the top of a chat. In a team chat admins and shift leads (people who
              can assign tasks) pin; in a direct message either person can. Up to 20 per chat.
            </li>
            <li>
              <strong>Seen by</strong>: on your own message, ⋯ › Seen by lists who has read it and who hasn't yet.
              Announcements already show who has seen and acknowledged them (admins).
            </li>
            <li>
              <strong>Search</strong> finds messages (and PDF names) in the chats you can see: 🔍 above the list searches
              everything, Search in a chat searches just that one. Deleted messages are never found.
            </li>
            <li>
              The Team rules still apply to everything: report any message (voice notes and PDFs included) from its ⋯ menu,
              block someone from a direct message, and offensive words are hidden automatically in text and file names.
            </li>
          </ul>
        </div>
      </details>

      <details className="help-section" id="flyers">
        <summary>Flyers (Marketing)</summary>
        <div className="help-section-body">
          <p>
            Send a marketing flyer - a picture or a PDF - to the store's customer WhatsApp groups, and keep every flyer in
            one library. It's under <strong>Team → Flyers</strong> in the menu. Admins always have it; an admin can give
            it to staff on the Users page (<strong>Marketing flyers</strong>).
          </p>
          <h4>Sending a flyer</h4>
          <ul>
            <li>
              Drag the flyer onto the box, or click <strong>Choose a file</strong>. A picture (JPEG, PNG, WebP, or a HEIC
              photo from an iPhone) can be up to 25 MB; a PDF up to 20 MB.
            </li>
            <li>
              The title is filled in from the file name. Change it to something you'll recognise later. Then pick the type:
              one of the store's flyer types (for example Groceries or Others).
            </li>
            <li>
              The message is optional and goes with the flyer in WhatsApp (up to 1,000 characters). <code>*bold*</code>,{" "}
              <code>_italic_</code> and <code>~strikethrough~</code> work the way they do in WhatsApp.
            </li>
            <li>
              Tick the groups (<strong>Select all</strong> / <strong>None</strong>) and click <strong>Send…</strong>: a
              last check shows the flyer, the message and the group names, and <strong>Send to N groups</strong> sends it.
            </li>
            <li>
              It goes to one group at a time, a few seconds apart, so WhatsApp doesn't treat the store's number as spam.
              Each group shows <strong>Waiting</strong>, <strong>Sending…</strong>, <strong>Sent</strong> or{" "}
              <strong>Failed</strong> as it happens. You can close the window: sending carries on, and the
              phone app sends you a notification when it's done (not during your quiet hours).
            </li>
            <li>
              <strong>Save to library</strong> keeps the flyer without sending it, to send later.
            </li>
          </ul>
          <h4>Which groups get it</h4>
          <p>
            Only groups an admin has marked <strong>Use for flyers</strong> are listed (a "marketing group"), so a flyer
            can't go to a staff group by accident. Admins: open <strong>Checklist builder → WhatsApp groups</strong> (or{" "}
            <strong>Manage groups</strong> on the Flyers page) and tick <strong>Use for flyers</strong> for each customer
            group. A group is added there first, from the groups the store's WhatsApp number is in. A group that's turned
            off, or has no chat set up, is skipped.
          </p>
          <h4>The library</h4>
          <p>
            Every flyer, newest first. Filter by type with the chips (each shows how many there are) or search. Click a
            flyer to see it big, open or download the file, edit its title, type or message, delete it, send it to more
            groups, retry a group that failed, and see every send: when, by whom, and each group's result. A group the
            flyer already went to says so in the list.
          </p>
          <h4>Where the files are kept</h4>
          <p>
            On the store's server, in its <strong>flyers folder</strong>, with one folder per type.
            Pictures are saved as JPEG, sized for WhatsApp; PDFs are kept as they are. Each file is named with the date,
            the title and the flyer's number, for example "2026-10-04 Diwali Garlands (12).jpg". Changing a flyer's type
            moves its file to that type's folder. Deleting takes it out of the library and moves the file to a{" "}
            <strong>_Deleted</strong> folder there; it's never erased.
          </p>
          <h4>If a send fails</h4>
          <p>
            A failed group shows the reason. <strong>Retry</strong> sends to that group again, and{" "}
            <strong>Retry failed</strong> does all of them. If WhatsApp isn't connected on the server, Send is switched off
            and flyers can only be saved.
          </p>
          <p style={{ marginBottom: 0 }}>
            <strong>Coming later:</strong> posting the same flyer to the store's Facebook page, Instagram, Google Business
            Profile and TikTok.
          </p>
        </div>
      </details>

      <details className="help-section" id="ask">
        <summary>Ask Inventory Bot</summary>
        <div className="help-section-body">
          <p>
            The 💬 chat bubble in the bottom-right corner is on every page, not just this one - click
            it to ask a question in plain language and get an answer backed by the same reports as
            the rest of the app: reorder candidates, supplier projection, reconciliation, vendor
            price comparison, items sold, price history, dead stock, and barcode reports.
            It's powered by Claude (Anthropic's AI), given tools to look up each of those reports for
            real - it never guesses or estimates a number itself. Your conversation stays open as
            you navigate between pages, until you close the chat.
          </p>
          <h4>Strictly read-only</h4>
          <p>
            The bot cannot log a purchase, confirm a delivery, change a price, or write anything to
            the database - every tool it has access to only reads data. A misunderstood or
            ambiguous question can at worst give you a wrong or incomplete answer; it can't corrupt
            real inventory or purchase records.
          </p>
          <h4>Which model answers</h4>
          <p>
            A quick, cheap check decides how to route each question before it's answered: a direct
            single-lookup question ("how much cilantro do we have") is answered by a fast, cheap
            model, while a question that needs combining multiple reports or real reasoning ("why
            did sales drop this month", "which vendor should I switch to") is automatically handed
            to a stronger model instead. Each answer says which one actually ran, and which
            report(s) it checked, so you can see where the number came from.
          </p>
          <h4>Setup</h4>
          <p>
            Requires an Anthropic API key set as <code>ANTHROPIC_API_KEY</code> in{" "}
            <code>backend/.env</code>. If it isn't set, the chat shows a clear "not configured"
            error instead of a broken response - nothing else in the app depends on it.
          </p>
        </div>
      </details>

      <details className="help-section" id="notifications">
        <summary>Phone alerts (in the app)</summary>
        <div className="help-section-body">
          <p style={{ marginBottom: 0 }}>
            Which phone alerts you get is chosen in the phone app: <strong>More → Notifications</strong> (your own
            switches, Reset to recommended, and for admins the store defaults). The website has no alerts page.
          </p>
        </div>
      </details>

      <details className="help-section" id="users">
        <summary>Users (admin only)</summary>
        <div className="help-section-body">
          <p>
            Admin accounts can create new logins and reset anyone's password. There's{" "}
            <strong>no self-service "forgot password" flow anywhere, by design</strong> - if someone
            forgets their password, an admin resets it here. Resetting a password immediately signs
            that user out everywhere (their old session stops working right away).
          </p>
          <p>
            Any user - admin or not - can change their own password from{" "}
            <strong>Change password</strong> in the menu, as long as they know their current one.
          </p>
          <h4>Hide sales numbers</h4>
          <p>
            An admin can tick <strong>Hide sales numbers</strong> for a non-admin - when adding the
            user, or in the "Sales numbers" column on the Users page (the app's Users, in More › Setup, has
            the same switch). Only admins control it, and admins always see sales, so there's no switch
            for them. It takes effect on that person's next page load or app refresh, and unticking
            it brings everything back.
          </p>
          <p>
            <strong>What that person no longer sees:</strong> Today's / Yesterday's sales on Overview,
            Items Sold and its sales download (gone from the menu), open-check
            totals (screen and CSV), the weekly dollar column in an item's weekly breakdown and in Fix
            Issues item details (they see "14 sold", not "($62.16)"), the Weekly Digest's sales
            section, and the "N line items from M orders" detail on the sales sync status. The Ask bot
            declines sales questions. The server enforces this too, so it can't be worked around.
          </p>
          <p style={{ marginBottom: 0 }}>
            <strong>What stays:</strong> everything needed for ordering and stock - units per day and
            per week, Reorder and Supplier Projection quantities, reconciliation, delivery review and
            Fix Issues "last sold".
          </p>
        </div>
      </details>

      <details className="help-section" id="inventory-api">
        <summary>Inventory from {pos} (admin only)</summary>
        <div className="help-section-body">
          <p>
            The daily inventory file (CSV) can be replaced by an inventory snapshot pulled from {pos} itself each night
            at 4:05 AM: stock counts, prices, barcodes and categories come from {pos}, and the 7/30/90-day sales are
            counted from our own synced sales (through yesterday - {pos}'s own figures in the file run about two days
            behind). {pos} has no cost, vendor code or last-received date, so those are carried from the latest file.
            It is switched off until an admin turns it on with <strong>Use {pos} for inventory</strong> on the{" "}
            <strong>Inventory from {pos}</strong> page (it applies at once, no restart; every change is listed there with
            who and when). Until then the nightly run only compares what it built with that day's file.
          </p>
          <p>
            <strong>Readiness.</strong> Each nightly comparison is scored: on hand must match the file (or differ by
            exactly what sold after the file was made) on 99% of counted items, price and barcode on 99%, category on
            100%, with at most 10 on-hand differences left unexplained. Seven good nights in a row = ready, and admins
            get one push at 9 AM (only while it is off). A night that misses, or a nightly run that fails, starts the
            count again - the page shows which night and why. A night with no file to compare doesn't count either way.
            The numbers are server settings (INVENTORY_API_READY_*); the server setting INVENTORY_API_SNAPSHOTS is only
            the switch's starting value.
          </p>
          <p>
            <strong>The file still wins.</strong> A file uploaded for a day always replaces {pos}'s snapshot of that day,
            and the snapshot is never made for a day that already has a file. Upload a file now and then to refresh
            costs and vendor codes - the <strong>Inventory upload due</strong> alert reminds you once the last file is 7
            days old.
          </p>
          <p>
            <strong>Out of stock and negative counts.</strong> {pos} only says "out of stock" for anything at 0 or
            below, so those items show 0 - except on Negative inventory, which keeps the last file's negative count
            until the next file.
          </p>
          <p>
            <strong>Live stock</strong> (only with the switch on): Supplier Projection, Reorder Candidates (and the 9 AM
            critical vendors alert, Vendor Calendar order counts and the weekly digest built on them) and Fix Issues'
            Negative inventory and Refill from container use {pos}'s current count, at most 5 minutes old. If {pos}
            doesn't answer, they use the latest snapshot as before.
          </p>
          <p style={{ marginBottom: 0 }}>
            <strong>Barcodes between uploads.</strong> Every hour the app asks {pos} whether the menu changed and, if it
            did, re-reads the barcodes. Missing and invalid barcodes on Fix Issues then use them: an item fixed in {pos}
            leaves the list without waiting for the next file, and an item closed as fixed that still shows after
            {pos}'s next menu change comes back. {pos}'s menu only holds an item's first barcode; any extra barcodes
            come from the last file.
          </p>
        </div>
      </details>

      <details className="help-section" id="integrations">
        <summary>Integrations (admin)</summary>
        <div className="help-section-body">
          <p>
            <strong>Setup → Integrations</strong> is where a store admin connects the store's {pos} POS, WhatsApp,
            email (SMTP) and the AI key. Pick a tile, fill in the fields, <strong>Save</strong>, then{" "}
            <strong>Test connection</strong> - the chip turns green when it works.
          </p>
          <p style={{ marginBottom: 0 }}>
            Keys and passwords are encrypted and never shown again: a saved one shows as "saved •••• 1234". Leave a key
            box empty to keep the saved one, or type a new one to replace it. Clover's <strong>Connect</strong> button
            arrives with the next release.
          </p>
        </div>
      </details>

      <details className="help-section" id="live-data">
        <summary>What's live and what's on a schedule</summary>
        <div className="help-section-body">
          <p>
            Where {pos} lets the app ask quickly, a page asks {pos} when you open it (with a short
            cache so many people opening it at once don't flood {pos}). The scheduled jobs still run
            as a safety net.
          </p>
          <ul>
            <li>
              <strong>Live when you open it:</strong> today's Items Sold and the Overview sales tile
              (shared for 1 minute; Refresh asks again), today's part of Open Checks (re-checked when
              the last check is over 2 minutes old), and the suggested deliveries, "Looks closed in
              {pos}" and suggested schedules (worked out on request, 1-minute cache, and at once after
              an upload or a confirm).
            </li>
            <li>
              <strong>Live once an admin turns on Inventory from {pos}:</strong> on-hand counts on
              Reorder Candidates, Supplier Projection, Negative inventory and Refill from container
              ({pos}'s count, at most 5 minutes old).
            </li>
            <li>
              <strong>On a schedule:</strong> past days of sales (synced each night at 4 AM, then
              compared with {pos} and corrected at 6:30 AM, 12:30 PM, 5:30 PM and 10:30 PM once {pos}
              Analytics is set up), the barcode re-check (hourly), the nightly inventory snapshot from
              {pos} (4:05 AM, when turned on), the full Open Checks history sweep (once a day) and the
              alerts.
            </li>
            <li>
              <strong>Not live, and why:</strong> past days of sales come from the stored copy (a live
              pull would cost one {pos} request per day of the range); {pos}'s own Analytics run 2-4
              hours behind, so they are only used to check past days, never for today; deliveries
              received in {pos} only show up with the next inventory upload ({pos}'s live stock has no
              receiving numbers); cost, vendor codes and last-received dates have no {pos} API at all.
            </li>
          </ul>
        </div>
      </details>

      <details className="help-section" id="limitations">
        <summary>What this doesn't do (yet)</summary>
        <div className="help-section-body">
          <ul>
            <li>
              <strong>No automatic ordering.</strong> Nothing in this dashboard places an order on
              its own - Reorder Candidates (All Suppliers) and Supplier Projection are decision
              support. Send PO on the cart emails a vendor's PO only when someone presses Send, and
              the suggested deliveries, "Looks closed in {pos}" and suggested schedules all wait for a
              person to confirm.
            </li>
            <li>
              <strong>No real vendor cost or lead time.</strong> "Vendor lead time" on Reorder
              Candidates is a number you type in, not looked up - {pos}'s Purchasing API isn't
              accessible with the current account credentials.
            </li>
            <li>
              <strong>Forecasting is a simple average, not a smart prediction.</strong> Every demand
              number is "average daily sales over your chosen window" - it doesn't know about
              seasonality, holidays, or trends up/down.
            </li>
            <li>
              <strong>Inventory is only as fresh as the latest snapshot</strong> - your last upload,
              or {pos}'s nightly snapshot once an admin turns on Inventory from {pos}. With that switch
              on, Reorder, Supplier Projection, Negative inventory and Refill from container read
              {pos}'s live count instead (at most 5 minutes old). Cost, vendor codes and last-received
              dates still only come from an uploaded file.
            </li>
          </ul>
        </div>
      </details>
    </>
  );
}
