// Website menu (final pass, docs/DESIGN_SYSTEM.md 3.1): area groups, one-link
// groups as links, Setup sub-headings, the Insights hub, the footer and user
// menu, search, two-click reach, and every old address still routed.
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  AREAS,
  FEATURE_ROUTES,
  INSIGHTS_HUB,
  NAV_GROUPS,
  PAGE_META,
  ROUTES_BY_KEY,
  buildSidebar,
  canBuildChecklists,
  firstAccessiblePath,
  footerLinks,
  groupFeatures,
  groupsToOpen,
  insightsLandingPath,
  insightsTiles,
  routeForPath,
  routeLabel,
  routeVisible,
  searchablePages,
  userMenuLinks,
} from "./featureRoutes.js";
import { BRANDING } from "./test/fixtures/branding.js";
import { normalizeBranding, storeView } from "./store/branding.js";
import { featureAvailable } from "./store/gating.js";

const here = dirname(fileURLToPath(import.meta.url));
const read = (rel) => readFileSync(join(here, rel), "utf8");

// The fake Toast store (every capability and store feature): the menu is the
// pre-M2 one. Per-store differences are in store/gating.test.mjs.
const TOAST_STORE = storeView(normalizeBranding(BRANDING.RETAILOPS_TEST_TOAST), null);

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
  entries.map((e) => (e.type === "link" ? e.label : { [e.label]: e.items.map((r) => routeLabel(r, "Toast")) }));

test("admin menu (final pass): Home, Daily Checklists, Ordering, Stock, Sales, Team, Setup", () => {
  assert.deepEqual(shape(buildSidebar(ctxFor(ADMIN))), [
    "Home",
    "Daily Checklists",
    { Ordering: ["Reorder Candidates", "New order", "Vendor Calendar", "Purchase Order Cart", "Placed Orders"] },
    { Stock: ["Fix Issues", "Delivery Review", "Invoices", "Inventory Reports", "Reconciliation"] },
    { Sales: ["Items Sold", "Open Checks", "Insights"] },
    { Team: ["Tasks", "Chat", "News", "Flyers"] },
    {
      Setup: [
        "Store settings",
        "Data & usage",
        "Users",
        "Teams",
        "Integrations",
        "Inventory from Toast",
        "PO sections",
        "Vendor contacts",
        "Item vendors",
        "Category & vendor leads",
        "Checklist builder",
        "Recurring tasks",
        "Reported messages",
      ],
    },
  ]);
  // Setup's small sub-headings (no extra click).
  const setup = buildSidebar(ctxFor(ADMIN)).find((e) => e.key === "setup");
  assert.deepEqual(
    setup.sections.map((s) => s.label),
    ["Store", "People", "Connections", "Ordering setup", "Team setup"],
  );
  // Every group carries its area (identity colour).
  for (const e of buildSidebar(ctxFor(ADMIN)).filter((x) => x.type === "group")) assert.ok(AREAS[e.area], e.key);
});

test("staff: a group with one page is a plain link; Invoices and Delivery Review under Stock", () => {
  const entries = buildSidebar(ctxFor(STAFF));
  assert.deepEqual(shape(entries), [
    "Home",
    "Daily Checklists",
    { Ordering: ["Reorder Candidates", "New order", "Vendor Calendar", "Purchase Order Cart", "Placed Orders"] },
    { Stock: ["Fix Issues", "Delivery Review", "Invoices", "Inventory Reports", "Reconciliation"] },
    // hide_sales: Items Sold is off and no Insights, so "Sales" holds only Open Checks -> a link.
    "Open Checks",
    { Team: ["Tasks", "Chat", "News"] },
    // Setup holds only Vendor contacts for staff -> a link.
    "Vendor contacts",
  ]);
  assert.equal(entries.find((e) => e.type === "link" && e.route.path === "/open-orders").type, "link");
});

test("staff who can see sales: Items Sold + Open Checks under 'Sales'", () => {
  const entries = buildSidebar(ctxFor({ ...STAFF, hide_sales: false }));
  const sales = entries.find((e) => e.key === "sales");
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
  assert.equal(entries.find((e) => e.key === "sales").label, "Sales");
});

test("flyers-only login: Team holds News + Flyers; nothing admin", () => {
  assert.deepEqual(shape(buildSidebar(ctxFor(FLYERS_ONLY))), [{ Team: ["News", "Flyers"] }]);
  assert.equal(firstAccessiblePath(ctxFor(FLYERS_ONLY).hasFeature), "/flyers");
});

test("Setup pages are declared, admin-only and never a landing page", () => {
  const setup = NAV_GROUPS.find((g) => g.key === "setup");
  for (const key of ["setup_users", "setup_teams", "setup_issue_leads", "setup_inventory_api", "setup_integrations"]) {
    assert.ok(groupFeatures(setup).includes(key), key);
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

test('Checklist builder: admins and "Build checklists" only, never the demo login', () => {
  const setup = NAV_GROUPS.find((g) => g.key === "setup");
  assert.ok(groupFeatures(setup).includes("setup_checklist_builder"));
  const route = ROUTES_BY_KEY.setup_checklist_builder;
  assert.equal(route.feature, "checklist_builder");
  const builder = { ...STAFF, username: "builder", allowed_features: [...STAFF.allowed_features, "checklist_builder"] };
  const builderOnly = { username: "builder_only", is_admin: false, allowed_features: ["checklist_builder"] };
  // Shown with the key (and for admins); hidden without it and for the demo login.
  assert.equal(canBuildChecklists(ctxFor(ADMIN)), true);
  assert.equal(canBuildChecklists(ctxFor(builder)), true);
  assert.equal(canBuildChecklists(ctxFor(builderOnly)), true);
  assert.equal(canBuildChecklists(ctxFor(STAFF)), false);
  assert.equal(canBuildChecklists(ctxFor({ ...DEMO, allowed_features: [...DEMO.allowed_features, "checklist_builder"] })), false);
  // A DEMO store's admin (every login there is a demo login) keeps it, as
  // when the builder was admin-only (the server lets admins through first).
  assert.equal(canBuildChecklists(ctxFor({ ...ADMIN, demo: true })), true);
  const setupItems = (user) => {
    const entry = buildSidebar(ctxFor(user)).find((e) => e.key === "setup" || e.route?.key?.startsWith("setup_"));
    if (!entry) return [];
    return entry.type === "group" ? entry.items.map((r) => r.key) : [entry.route.key];
  };
  assert.ok(setupItems(builder).includes("setup_checklist_builder"));
  assert.ok(!setupItems(STAFF).includes("setup_checklist_builder"));
  // The key opens nothing else in Setup (Users, Teams and the rest stay admin-only).
  assert.deepEqual(setupItems(builder).filter((k) => k.startsWith("setup_")), ["setup_checklist_builder"]);
  // Someone who can open nothing else lands on the builder, not "no access".
  assert.equal(firstAccessiblePath(ctxFor(builderOnly).hasFeature), "/checklists/admin");
  // The route guard and the Daily Checklists button use the same check.
  assert.match(read("App.jsx"), /canBuild \? <ChecklistAdminPage \/>/);
  assert.match(read("components/DailyChecklists.jsx"), /canBuildChecklists\(auth\) &&/);
});

test("every menu key points at a route, every page is in the menu once (Insights pages via the hub)", () => {
  const keys = NAV_GROUPS.flatMap(groupFeatures);
  for (const k of keys) assert.ok(ROUTES_BY_KEY[k] || k === INSIGHTS_HUB.key, k);
  assert.equal(new Set(keys).size, keys.length, "no page listed twice");
  const reach = new Set([...keys, ...INSIGHTS_HUB.hubOf]);
  const menuless = FEATURE_ROUTES.filter((r) => !reach.has(r.key || r.feature)).map((r) => r.path);
  // Only the retired Transfer Review (it redirects to Delivery Review).
  assert.deepEqual(menuless.sort(), ["/transfer-review"]);
  // The hub lists every Insights page once.
  const insights = FEATURE_ROUTES.filter((r) => r.feature === "sales_insights").map((r) => r.key).sort();
  assert.deepEqual([...INSIGHTS_HUB.hubOf].sort(), insights);
});

test("every page has a page id, an area and an icon (registry, search, header)", () => {
  for (const r of FEATURE_ROUTES.filter((x) => !x.retired)) {
    assert.ok(r.pageId, r.path);
    assert.ok(AREAS[r.area], `${r.path} area`);
    assert.ok(r.icon, `${r.path} icon`);
    assert.equal(PAGE_META[r.pageId].path, r.path);
  }
  assert.equal(routeForPath("/checklists/admin/closing").pageId, "checklist_builder");
  assert.equal(routeForPath("/insights/busy-hours").pageId, "insights_busy_hours");
  assert.equal(routeForPath("/").pageId, "home");
});

test("footer: Help (or Glossary without Help) and Label printing; account links in the user menu", () => {
  assert.deepEqual(
    footerLinks(ctxFor(ADMIN)).map((l) => l.label),
    ["Help", "Label printing"],
  );
  assert.deepEqual(
    footerLinks(ctxFor(FLYERS_ONLY)).map((l) => l.label),
    ["Glossary", "Label printing"],
  );
  // Label printing opens through the one-time hand-off page, never the
  // label site's address directly (docs/LABEL_PRINTING_FEATURE.md 3.4).
  const labels = footerLinks(ctxFor(ADMIN)).find((l) => l.key === "labels");
  assert.equal(labels.href, "#/label-printing/open");
  assert.equal(labels.hrefFrom, undefined);
  const help = footerLinks(ctxFor(ADMIN)).find((l) => l.key === "help");
  assert.deepEqual(help.activePaths, ["/help", "/glossary"]);
  assert.deepEqual(userMenuLinks(ctxFor(ADMIN)).map((l) => l.label), ["Change password", "Help center", "Glossary"]);
  assert.deepEqual(userMenuLinks(ctxFor(FLYERS_ONLY)).map((l) => l.label), ["Change password", "Glossary"]);
});

test("the group holding the current page opens", () => {
  const entries = buildSidebar(ctxFor(ADMIN));
  assert.deepEqual(groupsToOpen(entries, "/insights/busy-hours"), ["sales"]);
  assert.deepEqual(groupsToOpen(entries, "/checklists/admin/closing"), ["setup"]);
  assert.deepEqual(groupsToOpen(entries, "/delivery-review"), ["stock"]);
  assert.deepEqual(groupsToOpen(entries, "/invoices"), ["stock"]);
  assert.deepEqual(groupsToOpen(entries, "/"), []);
  // A prefix that isn't a sub-path doesn't count.
  assert.deepEqual(groupsToOpen(entries, "/reorder-old"), []);
});

test("every page is two clicks away: a top link, or a group's link, or a hub tile", () => {
  for (const user of [ADMIN, STAFF, DEMO, FLYERS_ONLY, { ...STAFF, hide_sales: false, allowed_features: [...STAFF.allowed_features, "sales_insights"] }]) {
    const ctx = ctxFor(user);
    const entries = buildSidebar(ctx);
    const clicks = new Map();
    for (const e of entries) {
      if (e.type === "link") clicks.set(e.route.path, 1);
      else for (const r of e.items) clicks.set(r.path, 2);
    }
    // Insights hub: its link is 1-2 clicks, each tile one more - but /insights
    // lands on a page directly, and every tile sits on that page.
    const { main, alsoIn } = insightsTiles(ctx);
    for (const r of [...main, ...alsoIn]) clicks.set(r.path, Math.max(clicks.get("/insights") || 2, 2));
    for (const r of FEATURE_ROUTES.filter((x) => !x.retired && routeVisible(x, ctx))) {
      assert.ok((clicks.get(r.path) || 99) <= 2, `${user.username}: ${r.path}`);
    }
  }
});

test("Insights hub: remembered tile when still allowed, else the first main page", () => {
  const ctx = ctxFor(ADMIN);
  assert.equal(insightsLandingPath("/insights/busy-hours", ctx), "/insights/busy-hours");
  assert.equal(insightsLandingPath(null, ctx), "/insights/daily");
  const hide = ctxFor({ ...STAFF, allowed_features: [...STAFF.allowed_features, "sales_insights"] });
  // A remembered sales-only page is not open to a "Hide sales numbers" user.
  assert.equal(insightsLandingPath("/insights/daily", hide), "/insights/prep-forecast");
  assert.deepEqual(insightsTiles(hide).main.map((r) => r.label), ["Prep forecast", "Returning customers"]);
  assert.deepEqual(insightsTiles(hide).alsoIn.map((r) => r.label), ["Busy hours"]);
});

test("search lists every page this user can open, with where it lives", () => {
  const pages = searchablePages(ctxFor(ADMIN));
  const byPath = Object.fromEntries(pages.map((p) => [p.route.path, p]));
  assert.equal(byPath["/vendor-calendar"].where, "Ordering");
  assert.equal(byPath["/insights/busy-hours"].where, "Sales › Insights");
  assert.equal(byPath["/change-password"].where, "Account");
  assert.ok(!searchablePages(ctxFor(STAFF)).some((p) => p.route.path === "/users"), "no admin pages for staff");
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
  // The Notifications page left the website (docs/PLATFORM_CONTRACT.md P8).
  assert.match(app, /path="\/notifications" element={<Navigate to="\/" replace \/>}/);
  assert.match(app, /path="\/glossary" element={<HelpRoute tab="glossary" \/>}/);
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
  // The live worked example is in the Reconciliation guide (ui/help, content/help).
  assert.ok(read("ui/help/HelpCenter.jsx").includes("<ReconciliationWorkedExample"));
});

test("sales export lives on Items Sold", () => {
  assert.ok(read("pages/ItemsSoldPage.jsx").includes("<SalesDownload"));
  assert.ok(read("pages/InventoryReportsPage.jsx").includes('!hasFeature("items_sold") && <SalesDownload'));
});

test("Help names no menu group that's gone", () => {
  // Help text lives in content/help (one source for the website, the app and
  // the server); its links and ids are checked by ui/help/help.test.mjs.
  const dir = join(here, "../../content/help");
  const files = ["pages.json", "faq.json", "troubleshooting.json", "getting-started.json"].map((f) => join(dir, f));
  const help = files.map((f) => readFileSync(f, "utf8")).join("\n");
  assert.ok(!/Manage Users/.test(help), "the app's Manage Users is now Users");
  assert.ok(!/Sales & Insights/.test(help), "the menu group is Sales now");
  assert.ok(!/Marketing →/.test(read("components/checklists/WhatsAppGroupsPanel.jsx")), "Flyers are under Team now");
});

test("WhatsApp groups: only admins choose flyer groups", () => {
  // The server refuses a "Build checklists" user who changes "Use for flyers"
  // (or turns off / deletes a flyer group); the panel says so up front.
  const panel = read("components/checklists/WhatsAppGroupsPanel.jsx");
  assert.ok(panel.includes('"Only admins choose flyer groups"'));
  assert.ok(panel.includes("const canChooseFlyers = Boolean(user?.is_admin)"));
  assert.ok(panel.includes("disabled={busy != null || !canChooseFlyers}"), "the box is read-only for a non-admin");
  assert.ok(panel.includes("{!canChooseFlyers && groups?.some("), "the one-line note shows for a non-admin");
  assert.ok((panel.match(/flyerLocked/g) || []).length >= 4, "Turn off and Delete are locked on a flyer group");
});

test("Delete a checklist: admins only, only one never filled in", async () => {
  const { checklistDeleteAction, checklistDeleteConfirmText } = await import("./components/checklists/model.js");
  const fresh = { title: "Oops", can_delete: true, builtin: false, submission_count: 0 };
  const used = { title: "Deli", can_delete: false, builtin: false, submission_count: 12 };
  const once = { ...used, submission_count: 1 };
  const original = { title: "Closing", can_delete: false, builtin: true, submission_count: 300 };
  const admin = { is_admin: true };
  const builder = { is_admin: false, allowed_features: ["checklist_builder"] };
  const demoAdmin = { is_admin: true, demo: true };

  assert.deepEqual(checklistDeleteAction(fresh, admin), { canDelete: true, hint: null });
  // The Delete button is hidden for everyone who isn't an admin.
  for (const who of [builder, demoAdmin, { is_admin: false }, null]) {
    assert.deepEqual(checklistDeleteAction(fresh, who), { canDelete: false, hint: null });
    assert.deepEqual(checklistDeleteAction(used, who), { canDelete: false, hint: null });
  }
  // A used one: Turn off, with why there's no Delete.
  assert.equal(checklistDeleteAction(used, admin).canDelete, false);
  assert.equal(checklistDeleteAction(used, admin).hint, "Filled in 12 times, so it can't be deleted - turn it off instead.");
  assert.equal(checklistDeleteAction(once, admin).hint, "Filled in 1 time, so it can't be deleted - turn it off instead.");
  assert.equal(checklistDeleteAction(original, admin).canDelete, false);
  // An older server never says can_delete: no Delete at all.
  assert.deepEqual(checklistDeleteAction({ title: "X", submission_count: 0 }, admin), { canDelete: false, hint: null });

  assert.equal(checklistDeleteConfirmText(fresh), 'Delete "Oops"? It has never been filled in. This can\'t be undone.');

  const list = read("components/checklists/ChecklistAdminList.jsx");
  assert.ok(list.includes("const del = checklistDeleteAction(d, user);"), "the list asks who may delete");
  assert.ok(list.includes("{del.canDelete && ("), "Delete only when allowed");
  assert.ok(list.includes("window.confirm(checklistDeleteConfirmText(d))"), "and it asks first");
  assert.ok(list.includes('{d.active === false ? "Turn on" : "Turn off"}'), "Turn off stays for everyone");
  assert.ok(read("api.js").includes("export async function deleteChecklistDefinition("));
});
