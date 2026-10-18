import { posText, routeAvailable } from "./store/gating.js";

// Single source of truth for which route maps to which admin-grantable
// feature key (see backend/app/features.py - keep these in sync). What the
// STORE can have (POS capabilities, store features) is in store/gating.js,
// keyed the same way (ROUTES_BY_KEY); labels may hold "{pos}" for the
// store's POS name (routeLabel). Order
// matters: it's also the fallback order used to pick a landing page for a
// user who can't access "/" (Overview).
export const FEATURE_ROUTES = [
  { path: "/", label: "Overview", feature: "overview", end: true },
  { path: "/invoices", label: "Invoices", feature: "invoice_submission" },
  { path: "/daily-checklists", label: "Daily Checklists", feature: "daily_checklists" },
  { path: "/open-orders", label: "Open Checks", feature: "open_orders" },
  { path: "/items-sold", label: "Items Sold", feature: "items_sold" },
  { path: "/reorder", label: "Reorder Candidates", feature: "reorder_candidates" },
  // "New order" is the app's name for Supplier Projection too (Home tile).
  { path: "/supplier-projection", label: "New order", feature: "supplier_projection" },
  { path: "/vendor-calendar", label: "Vendor Calendar", feature: "vendor_calendar" },
  { path: "/delivery-review", label: "Delivery Review", feature: "delivery_review" },
  // Retired (automation batch item 9): no menu entry and never a landing
  // page; the URL now redirects to Delivery Review (App.jsx). The API stays
  // for old app builds.
  { path: "/transfer-review", label: "Transfer Review", feature: "transfer_review", retired: true },
  { path: "/inventory-reports", label: "Inventory Reports", feature: "inventory_reports" },
  // Same grant as Inventory Reports (same data); `key` keeps the two apart in the sidebar.
  { path: "/fix-issues", label: "Fix Issues", feature: "inventory_reports", key: "fix_issues" },
  { path: "/reconciliation", label: "Reconciliation", feature: "reconciliation" },
  { path: "/po-sections", label: "PO sections", feature: "category_edits" },
  { path: "/purchase-order", label: "Purchase Order Cart", feature: "purchase_order_cart" },
  // Same grant as the cart; `key` keeps the two apart in the sidebar.
  { path: "/placed-orders", label: "Placed Orders", feature: "purchase_order_cart", key: "placed_orders" },
  // Who gets each vendor's PO from Send PO (automation item 8). Same grant as
  // the cart; never for the App Review demo login (real people's details).
  { path: "/vendor-contacts", label: "Vendor contacts", feature: "purchase_order_cart", key: "vendor_contacts", noDemo: true },
  // Insights (docs/INSIGHTS_CONTRACT.md): reports from POS analytics, one
  // grant (`sales_insights`); `key` keeps the pages apart in the sidebar.
  // Loss watch and Odd checks are per-employee data: admins only. A "Hide
  // sales numbers" user gets 403 from the `salesOnly` ones and never sees
  // them (Busy hours answers them with order counts only). Listed in sidebar
  // order: the main section, then the "Also in {pos}" pages (NAV_GROUPS).
  { path: "/insights/daily", label: "Daily compare", feature: "sales_insights", key: "insights_compare", salesOnly: true },
  { path: "/insights/loss-watch", label: "Loss watch", feature: "sales_insights", key: "insights_loss_watch", adminOnly: true },
  { path: "/insights/odd-checks", label: "Odd checks", feature: "sales_insights", key: "insights_odd_checks", adminOnly: true },
  { path: "/insights/prep-forecast", label: "Prep forecast", feature: "sales_insights", key: "insights_prep" },
  // Menu performance is split in two (owner, Oct 14): Rising / Falling / Not
  // sold here, Top / Bottom under "Also in {pos}". The old /insights/menu
  // redirects (menuPerformanceRedirect in insightsModel.js).
  { path: "/insights/menu-trends", label: "Menu trends", feature: "sales_insights", key: "insights_menu_trends", salesOnly: true },
  { path: "/insights/add-ons", label: "Add-ons", feature: "sales_insights", key: "insights_addons", salesOnly: true },
  { path: "/insights/returning-customers", label: "Returning customers", feature: "sales_insights", key: "insights_returning" },
  { path: "/insights/channels", label: "Sales channels", feature: "sales_insights", key: "insights_channels", salesOnly: true },
  { path: "/insights/check-trends", label: "Check size & guests", feature: "sales_insights", key: "insights_check_trends", salesOnly: true },
  { path: "/insights/busy-hours", label: "Busy hours", feature: "sales_insights", key: "insights_heatmap" },
  { path: "/insights/best-sellers", label: "Best & worst sellers", feature: "sales_insights", key: "insights_menu_sellers", salesOnly: true },
  // Team hub (docs/TEAM_HUB_DESIGN.md). Last so they never become someone's
  // landing page ahead of the pages above. Recurring schedules are
  // admin-only on top of the feature grant (`adminOnly`).
  { path: "/team/tasks", label: "Tasks", feature: "team_tasks", key: "team_tasks" },
  { path: "/team/chat", label: "Chat", feature: "team_chat", key: "team_chat" },
  { path: "/team/templates", label: "Recurring tasks", feature: "team_tasks", key: "team_templates", adminOnly: true },
  // Announcements go to everyone they're sent to, no grant needed
  // (`everyone`); never a landing page (firstAccessiblePath skips it).
  // "News" in the menu, the same as the app's Team tab.
  { path: "/team/announcements", label: "News", feature: null, key: "team_announcements", everyone: true },
  { path: "/team/moderation", label: "Reported messages", feature: "team_chat", key: "team_moderation", adminOnly: true },
  // Marketing flyers (docs/MARKETING_FLYERS_CONTRACT.md). Last, so it is only
  // the landing page for someone who has nothing else; never for the App
  // Review demo login (`noDemo` - the server refuses it there too).
  { path: "/flyers", label: "Flyers", feature: "marketing_flyers", noDemo: true },
  // Admin setup pages: no feature grant, admins only (`admin`; their APIs
  // are require_admin). Never a landing page (no `feature`).
  { path: "/users", label: "Users", feature: null, key: "setup_users", admin: true },
  { path: "/teams", label: "Teams", feature: null, key: "setup_teams", admin: true },
  { path: "/checklists/admin", label: "Checklist builder", feature: null, key: "setup_checklist_builder", admin: true },
  { path: "/issue-leads", label: "Category & vendor leads", feature: null, key: "setup_issue_leads", admin: true },
  { path: "/inventory-api", label: "Inventory from {pos}", feature: null, key: "setup_inventory_api", admin: true },
];

// The Insights pages that only repeat the POS's own reports (owner, Oct 14):
// sales channels, check size & guests, busy hours and top / bottom sellers.
// Shown together after the main Insights pages, with this note. "{pos}" is
// the store's POS name (posText).
export const ALSO_IN_POS = {
  label: "Also in {pos}",
  note: "Same numbers as {pos}'s own reports - kept here for convenience.",
};
export const ALSO_IN_POS_KEYS = ["insights_channels", "insights_check_trends", "insights_heatmap", "insights_menu_sellers"];

// A route's label for this store ("Inventory from {pos}" -> "Inventory from Clover").
export function routeLabel(route, posName) {
  return route ? posText(route.label, posName) : "";
}

// Where "/insights" (and an Insights page someone can't open) lands: the
// first main-section page this user can see. Daily compare needs sales
// numbers; a "Hide sales numbers" user starts at Prep forecast.
export function insightsHomePath(canSeeSales) {
  return canSeeSales ? "/insights/daily" : "/insights/prep-forecast";
}

export function firstAccessiblePath(hasFeature, store) {
  const match = FEATURE_ROUTES.find(
    (r) => r.feature && !r.retired && hasFeature(r.feature) && routeAvailable(r.key || r.feature, store),
  );
  return match ? match.path : "/no-access";
}

// Purely presentational grouping for the sidebar - doesn't affect routing or
// feature-gating (FEATURE_ROUTES above stays the single source of truth for
// that; each entry here is just a lookup key into it). A group renders
// nothing if this user has none of its features, same "never show what
// isn't granted" rule the old flat list already followed, and a group with
// exactly one link this user can open renders as that plain link
// (buildSidebar). Oct 15 nav clean-up: the same groups as the app's Store
// tab (Ordering / Stock / Sales), every admin page under Setup.
export const NAV_GROUPS = [
  { type: "item", feature: "overview" },
  { type: "item", feature: "invoice_submission" },
  { type: "item", feature: "daily_checklists" },
  { type: "group", key: "team", label: "Team", features: ["team_tasks", "team_chat", "team_announcements", "marketing_flyers"] },
  {
    type: "group",
    key: "ordering",
    label: "Ordering",
    features: ["reorder_candidates", "supplier_projection", "vendor_calendar", "placed_orders", "delivery_review"],
  },
  { type: "group", key: "stock", label: "Stock", features: ["fix_issues", "inventory_reports", "reconciliation"] },
  {
    type: "group",
    key: "sales_insights",
    label: "Sales & Insights",
    // Someone without any Insights page (staff, the demo) sees "Sales".
    labelNeeds: "sales_insights",
    fallbackLabel: "Sales",
    features: [
      "items_sold",
      "open_orders",
      "insights_compare",
      "insights_loss_watch",
      "insights_odd_checks",
      "insights_prep",
      "insights_menu_trends",
      "insights_addons",
      "insights_returning",
    ],
    // Pages that repeat the POS's own reports: a collapsed group of their own
    // at the end of Insights, so the section leads with what the POS can't do.
    subgroups: [{ key: "insights_also_toast", label: ALSO_IN_POS.label, note: ALSO_IN_POS.note, features: ALSO_IN_POS_KEYS }],
  },
  {
    type: "group",
    key: "setup",
    label: "Setup",
    features: [
      "setup_users",
      "setup_teams",
      "team_templates",
      "team_moderation",
      "setup_checklist_builder",
      "category_edits",
      "vendor_contacts",
      "setup_issue_leads",
      "setup_inventory_api",
    ],
  },
];

// Below the groups, for everyone: the label printer (another app, only when
// the store has one: its address is branding `labels_url`), Help (its
// Glossary tab is /glossary, for anyone without Help), Notifications and
// Change password. Log out is in the sidebar footer.
export const FOOTER_LINKS = [
  { key: "labels", label: "Label Scanner ↗", hrefFrom: "labelsUrl", external: true },
  { key: "help", label: "Help", path: "/help", feature: "help", activePaths: ["/help", "/glossary"] },
  { key: "glossary", label: "Glossary", path: "/glossary", unlessFeature: "help" },
  { key: "notifications", label: "Notifications", path: "/notifications" },
  { key: "change_password", label: "Change password", path: "/change-password" },
];

export const ROUTES_BY_KEY = Object.fromEntries(FEATURE_ROUTES.map((r) => [r.key || r.feature, r]));

// Can this user open this page? `ctx` = { user, hasFeature, canSeeSales,
// store } from AuthContext. A page the store can't have (store/gating.js)
// is hidden for everyone, admins included.
export function routeVisible(route, { user, hasFeature, canSeeSales, store }) {
  if (!route) return false;
  if (!routeAvailable(route.key || route.feature, store)) return false;
  if (route.admin) return !!user?.is_admin;
  if (!(route.everyone || hasFeature(route.feature))) return false;
  if (route.adminOnly && !user?.is_admin) return false;
  if (route.salesOnly && !canSeeSales) return false;
  if (route.noDemo && user?.demo) return false;
  return true;
}

// What the sidebar shows this user, in order: { type: "link", key, route,
// label } or { type: "group", key, label, items, subgroups: [{ key, label,
// note, items }] }. Empty groups are left out; a group with one link is a
// link. Labels are this store's ("{pos}" filled in); render a route with
// routeLabel(route, store.posName).
export function buildSidebar(ctx) {
  const pos = ctx.store?.posName;
  const out = [];
  for (const entry of NAV_GROUPS) {
    if (entry.type === "item") {
      const route = ROUTES_BY_KEY[entry.feature];
      if (routeVisible(route, ctx)) out.push({ type: "link", key: route.path, route, label: routeLabel(route, pos) });
      continue;
    }
    const pick = (keys) => keys.map((k) => ROUTES_BY_KEY[k]).filter((r) => routeVisible(r, ctx));
    const items = pick(entry.features);
    const subgroups = (entry.subgroups || [])
      .map((sub) => ({ key: sub.key, label: posText(sub.label, pos), note: posText(sub.note, pos), items: pick(sub.features) }))
      .filter((sub) => sub.items.length > 0);
    const all = [...items, ...subgroups.flatMap((sub) => sub.items)];
    if (all.length === 0) continue;
    if (all.length === 1) {
      out.push({ type: "link", key: all[0].path, route: all[0], label: routeLabel(all[0], pos) });
      continue;
    }
    const label =
      entry.labelNeeds && !all.some((r) => r.feature === entry.labelNeeds) ? entry.fallbackLabel : entry.label;
    out.push({ type: "group", key: entry.key, label, items, subgroups });
  }
  return out;
}

// The footer links this user gets (Help or, without it, Glossary; Label
// Scanner only when the store has a labels site), with `href` filled in.
export function footerLinks({ hasFeature, store }) {
  return FOOTER_LINKS.filter(
    (l) =>
      (!l.feature || hasFeature(l.feature)) &&
      !(l.unlessFeature && hasFeature(l.unlessFeature)) &&
      routeAvailable(l.key, store),
  ).map((l) => (l.hrefFrom ? { ...l, href: store?.[l.hrefFrom] } : l));
}

// The groups (and nested group) to open so the current page is never hidden
// inside a collapsed group (a bookmark, a link from another page).
export function groupsToOpen(entries, pathname) {
  const isHere = (r) => (r.end ? pathname === r.path : pathname === r.path || pathname.startsWith(`${r.path}/`));
  const open = [];
  for (const e of entries) {
    if (e.type !== "group") continue;
    if (e.items.some(isHere)) open.push(e.key);
    for (const sub of e.subgroups) {
      if (sub.items.some(isHere)) open.push(e.key, sub.key);
    }
  }
  return open;
}
