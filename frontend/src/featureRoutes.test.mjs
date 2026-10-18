// Website menu (Oct 15 nav clean-up): groups, one-link groups as links, Setup
// declared in NAV_GROUPS, the footer, and every old address still routed.
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  FEATURE_ROUTES,
  NAV_GROUPS,
  ROUTES_BY_KEY,
  buildSidebar,
  firstAccessiblePath,
  footerLinks,
  groupsToOpen,
  routeLabel,
  routeVisible,
} from "./featureRoutes.js";
import { BRANDING } from "./test/fixtures/branding.js";
import { normalizeBranding, storeView } from "./store/branding.js";
import { featureAvailable } from "./store/gating.js";

const here = dirname(fileURLToPath(import.meta.url));
const read = (rel) => readFileSync(join(here, rel), "utf8");

// The fake Toast store (every capability and store feature): the menu is the
// pre-M2 one. Per-store differences are in store/gating.test.mjs.
const TOAST_STORE = storeView(normalizeBranding(BRANDING.STOREOPS_TEST_TOAST), null);

// The same rules as AuthContext.hasFeature / canSeeSales.
function ctxFor(user, store = TOAST_STORE) {
  const hasFeature = (key) => {
    if (!user) return false;
    if (!featureAvailable(key, store)) return false;
    if (user.is_admin) return true;
    if (key === "items_sold" && user.hide_sales) return false;
    return (user.allowed_features || []).includes(key);
  };
  return { user, hasFeature, canSeeSales: !!user && (user.is_admin || !user.hide_sales), store };
}

const ADMIN = { username: "owner", is_admin: true, allowed_features: [] };
// Prod staff (users 9/10/12): hide_sales, no Insights.
const STAFF = {
  username: "staff",
  is_admin: false,
  hide_sales: true,
  allowed_features: [
    "overview", "invoice_submission", "daily_checklists", "open_orders", "items_sold", "reorder_candidates",
    "supplier_projection", "vendor_calendar", "delivery_review", "inventory_reports", "reconciliation",
    "purchase_order_cart", "team_tasks", "team_chat", "help", "ask_bot",
  ],
};
// App Review demo login (users.id 8): staff features, no insights/flyers/category edits.
const DEMO = { ...STAFF, username: "app_store_review", demo: true, hide_sales: false };
const FLYERS_ONLY = { username: "spc_muse", is_admin: false, allowed_features: ["marketing_flyers"] };

const shape = (entries) =>
  entries.map((e) =>
    e.type === "link"
      ? e.label
      : { [e.label]: [...e.items.map((r) => routeLabel(r, "Toast")), ...e.subgroups.map((s) => ({ [s.label]: s.items.map((r) => routeLabel(r, "Toast")) }))] },
  );

test("admin menu: 3 top links, Team, Ordering, Stock, Sales & Insights, Setup", () => {
  assert.deepEqual(shape(buildSidebar(ctxFor(ADMIN))), [
    "Overview",
    "Invoices",
    "Daily Checklists",
    { Team: ["Tasks", "Chat", "News", "Flyers"] },
    { Ordering: ["Reorder Candidates", "New order", "Vendor Calendar", "Placed Orders", "Delivery Review"] },
    { Stock: ["Fix Issues", "Inventory Reports", "Reconciliation"] },
    {
      "Sales & Insights": [
        "Items Sold",
        "Open Checks",
        "Daily compare",
        "Loss watch",
        "Odd checks",
        "Prep forecast",
        "Menu trends",
        "Add-ons",
        "Returning customers",
        { "Also in Toast": ["Sales channels", "Check size & guests", "Busy hours", "Best & worst sellers"] },
      ],
    },
    {
      Setup: [
        "Users",
        "Teams",
        "Recurring tasks",
        "Reported messages",
        "Checklist builder",
        "PO sections",
        "Vendor contacts",
        "Category & vendor leads",
        "Inventory from Toast",
      ],
    },
  ]);
});

test("staff: a group with one page is a plain link; no Insights means 'Sales'", () => {
  const entries = buildSidebar(ctxFor(STAFF));
  assert.deepEqual(shape(entries), [
    "Overview",
    "Invoices",
    "Daily Checklists",
    { Team: ["Tasks", "Chat", "News"] },
    { Ordering: ["Reorder Candidates", "New order", "Vendor Calendar", "Placed Orders", "Delivery Review"] },
    { Stock: ["Fix Issues", "Inventory Reports", "Reconciliation"] },
    // hide_sales: Items Sold is off, so "Sales" holds only Open Checks -> a link.
    "Open Checks",
    // Setup holds only Vendor contacts for staff -> a link.
    "Vendor contacts",
  ]);
  assert.equal(entries.find((e) => e.type === "link" && e.route.path === "/open-orders").type, "link");
});

test("staff who can see sales: Items Sold + Open Checks under 'Sales'", () => {
  const entries = buildSidebar(ctxFor({ ...STAFF, hide_sales: false }));
  const sales = entries.find((e) => e.key === "sales_insights");
  assert.equal(sales.label, "Sales");
  assert.deepEqual(sales.items.map((r) => r.label), ["Items Sold", "Open Checks"]);
});

test("App Review demo: no Insights, Flyers or Vendor contacts, no Setup group", () => {
  const entries = buildSidebar(ctxFor(DEMO));
  const flat = JSON.stringify(shape(entries));
  for (const gone of ["Insights", "Flyers", "Vendor contacts", "Setup", "Users", "Reported messages", "Recurring tasks"]) {
    assert.ok(!flat.includes(gone), `${gone} not in the demo menu`);
  }
  assert.ok(flat.includes("Tasks") && flat.includes("Chat") && flat.includes("News"));
  assert.equal(entries.find((e) => e.key === "sales_insights").label, "Sales");
});

test("flyers-only login: Team holds News + Flyers; nothing admin", () => {
  assert.deepEqual(shape(buildSidebar(ctxFor(FLYERS_ONLY))), [{ Team: ["News", "Flyers"] }]);
  assert.equal(firstAccessiblePath(ctxFor(FLYERS_ONLY).hasFeature), "/flyers");
});

test("Setup pages are declared, admin-only and never a landing page", () => {
  const setup = NAV_GROUPS.find((g) => g.key === "setup");
  for (const key of ["setup_users", "setup_teams", "setup_checklist_builder", "setup_issue_leads", "setup_inventory_api"]) {
    assert.ok(setup.features.includes(key), key);
    const r = ROUTES_BY_KEY[key];
    assert.equal(r.feature, null);
    assert.equal(routeVisible(r, ctxFor(ADMIN)), true);
    assert.equal(routeVisible(r, ctxFor(STAFF)), false);
    assert.equal(routeVisible(r, ctxFor({ ...STAFF, allowed_features: [...STAFF.allowed_features, key] })), false);
  }
  // A user with nothing still lands on /no-access, not an admin page.
  assert.equal(firstAccessiblePath(ctxFor({ is_admin: false, allowed_features: [] }).hasFeature), "/no-access");
  assert.equal(firstAccessiblePath(ctxFor(ADMIN).hasFeature), "/");
});

test("every menu key points at a route, and every non-retired page is in the menu once", () => {
  const keys = NAV_GROUPS.flatMap((g) =>
    g.type === "item" ? [g.feature] : [...g.features, ...(g.subgroups || []).flatMap((s) => s.features)],
  );
  for (const k of keys) assert.ok(ROUTES_BY_KEY[k], k);
  assert.equal(new Set(keys).size, keys.length, "no page listed twice");
  const menuless = FEATURE_ROUTES.filter((r) => !keys.includes(r.key || r.feature)).map((r) => r.path);
  // The cart is the floating button; Transfer Review is retired.
  assert.deepEqual(menuless.sort(), ["/purchase-order", "/transfer-review"]);
});

test("footer: Label Scanner, Help (or Glossary without Help), Notifications, Change password", () => {
  assert.deepEqual(
    footerLinks(ctxFor(ADMIN)).map((l) => l.label),
    ["Label Scanner ↗", "Help", "Notifications", "Change password"],
  );
  assert.deepEqual(
    footerLinks(ctxFor(FLYERS_ONLY)).map((l) => l.label),
    ["Label Scanner ↗", "Glossary", "Notifications", "Change password"],
  );
  const help = footerLinks(ctxFor(ADMIN)).find((l) => l.key === "help");
  assert.deepEqual(help.activePaths, ["/help", "/glossary"]);
});

test("the group holding the current page opens (both levels for Also in Toast)", () => {
  const entries = buildSidebar(ctxFor(ADMIN));
  assert.deepEqual(groupsToOpen(entries, "/insights/busy-hours"), ["sales_insights", "insights_also_toast"]);
  assert.deepEqual(groupsToOpen(entries, "/checklists/admin/closing"), ["setup"]);
  assert.deepEqual(groupsToOpen(entries, "/delivery-review"), ["ordering"]);
  assert.deepEqual(groupsToOpen(entries, "/"), []);
  // A prefix that isn't a sub-path doesn't count.
  assert.deepEqual(groupsToOpen(entries, "/reorder-old"), []);
});

test("every old address still has a route (bookmarks)", () => {
  const app = read("App.jsx");
  const paths = [
    ...FEATURE_ROUTES.map((r) => r.path),
    "/transfer-review",
    "/categories",
    "/price-changes",
    "/barcode-reports",
    "/insights",
    "/insights/menu",
    "/help",
    "/glossary",
    "/change-password",
    "/notifications",
    "/no-access",
    "/checklists/admin/:type",
  ];
  for (const p of paths) {
    if (p === "/" || p.startsWith("/insights/")) continue; // "/" and the Insights map are routed separately
    assert.ok(app.includes(`path="${p}"`), `route for ${p}`);
  }
  assert.match(app, /path="\/transfer-review" element={<Navigate to="\/delivery-review" replace \/>}/);
  assert.match(app, /path="\/glossary" element={<HelpPage tab="glossary" \/>}/);
});

test("retired Transfer Review UI is gone; Quick actions gone; worked example not loaded on Reconciliation", () => {
  assert.ok(!existsSync(join(here, "components/TransferReview.jsx")));
  assert.ok(!existsSync(join(here, "pages/TransferReviewPage.jsx")));
  assert.ok(!read("pages/Overview.jsx").includes("<QuickActions"));
  assert.ok(read("pages/Overview.jsx").includes("<WeeklyDigest"), "Weekly Digest stays on Overview");
  const recon = read("components/Reconciliation.jsx");
  const main = recon.slice(recon.indexOf("export default function Reconciliation"));
  assert.ok(!/<ReconciliationWorkedExample|<ReconciliationDemo/.test(main));
  assert.match(recon, /if \(!opened\) return;/);
  assert.ok(read("pages/HelpPage.jsx").includes("<ReconciliationWorkedExample"));
});

test("sales export lives on Items Sold", () => {
  assert.ok(read("pages/ItemsSoldPage.jsx").includes("<SalesDownload"));
  assert.ok(read("pages/InventoryReportsPage.jsx").includes('!hasFeature("items_sold") && <SalesDownload'));
});

test("Help's section headings match its jump list, and no text names a menu group that's gone", () => {
  const help = read("pages/HelpPage.jsx");
  const list = help.slice(help.indexOf("const SECTIONS"), help.indexOf("];"));
  for (const [, id, title] of list.matchAll(/\{ id: "([^"]+)", title: "([^"]+)" \}/g)) {
    const at = help.indexOf(`id="${id}"`);
    assert.ok(at > 0, `section ${id} exists`);
    const summary = help.slice(at).match(/<summary>([^<]+)<\/summary>/)[1];
    assert.equal(summary.replace(/&amp;/g, "&").replace(/ only\)$/, ")"), title, `section ${id}`);
  }
  assert.ok(!/Manage Users/.test(help), "the app's Manage Users is now Users");
  assert.ok(!/Marketing →/.test(read("components/checklists/WhatsAppGroupsPanel.jsx")), "Flyers are under Team now");
});
