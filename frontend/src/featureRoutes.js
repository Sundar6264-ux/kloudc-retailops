import { posText, routeAvailable } from "./store/gating.js";

// Single source of truth for which route maps to which admin-grantable
// feature key (see backend/app/features.py - keep these in sync). What the
// STORE can have (POS capabilities, store features) is in store/gating.js,
// keyed the same way (ROUTES_BY_KEY); labels may hold "{pos}" for the
// store's POS name (routeLabel). Order
// matters: it's also the fallback order used to pick a landing page for a
// user who can't access "/" (Overview).
export const FEATURE_ROUTES = [
  { path: "/", label: "Home", feature: "overview", end: true, pageId: "home", area: "home", icon: "layout-dashboard" },
  { path: "/invoices", label: "Invoices", feature: "invoice_submission", pageId: "invoices", area: "stock", icon: "receipt" },
  { path: "/daily-checklists", label: "Daily Checklists", feature: "daily_checklists", pageId: "daily_checklists", area: "team", icon: "clipboard-check" },
  { path: "/open-orders", label: "Open Checks", feature: "open_orders", pageId: "open_checks", area: "sales", icon: "file-text" },
  { path: "/items-sold", label: "Items Sold", feature: "items_sold", pageId: "items_sold", area: "sales", icon: "chart-column" },
  { path: "/reorder", label: "Reorder Candidates", feature: "reorder_candidates", pageId: "reorder", area: "ordering", icon: "repeat" },
  // "New order" is the app's name for Supplier Projection too (Home tile).
  { path: "/supplier-projection", label: "New order", feature: "supplier_projection", pageId: "new_order", area: "ordering", icon: "trending-up" },
  { path: "/vendor-calendar", label: "Vendor Calendar", feature: "vendor_calendar", pageId: "vendor_calendar", area: "ordering", icon: "calendar" },
  { path: "/delivery-review", label: "Delivery Review", feature: "delivery_review", pageId: "delivery_review", area: "stock", icon: "truck" },
  // Retired (automation batch item 9): no menu entry and never a landing
  // page; the URL now redirects to Delivery Review (App.jsx). The API stays
  // for old app builds.
  { path: "/transfer-review", label: "Transfer Review", feature: "transfer_review", retired: true, pageId: "delivery_review", area: "stock", icon: "truck" },
  { path: "/inventory-reports", label: "Inventory Reports", feature: "inventory_reports", pageId: "inventory_reports", area: "stock", icon: "layers" },
  // Same grant as Inventory Reports (same data); `key` keeps the two apart in the sidebar.
  { path: "/fix-issues", label: "Fix Issues", feature: "inventory_reports", key: "fix_issues", pageId: "fix_issues", area: "stock", icon: "wrench" },
  { path: "/reconciliation", label: "Reconciliation", feature: "reconciliation", pageId: "reconciliation", area: "stock", icon: "scale" },
  { path: "/po-sections", label: "PO sections", feature: "category_edits", pageId: "po_sections", area: "setup", icon: "layers" },
  { path: "/purchase-order", label: "Purchase Order Cart", feature: "purchase_order_cart", pageId: "cart", area: "ordering", icon: "shopping-cart" },
  // Same grant as the cart; `key` keeps the two apart in the sidebar.
  { path: "/placed-orders", label: "Placed Orders", feature: "purchase_order_cart", key: "placed_orders", pageId: "placed_orders", area: "ordering", icon: "history" },
  // Who gets each vendor's PO from Send PO (automation item 8). Same grant as
  // the cart; never for the App Review demo login (real people's details).
  { path: "/vendor-contacts", label: "Vendor contacts", feature: "purchase_order_cart", key: "vendor_contacts", noDemo: true, pageId: "vendor_contacts", area: "setup", icon: "contact" },
  // Insights (docs/INSIGHTS_CONTRACT.md): reports from POS analytics, one
  // grant (`sales_insights`); `key` keeps the pages apart in the sidebar.
  // Loss watch and Odd checks are per-employee data: admins only. A "Hide
  // sales numbers" user gets 403 from the `salesOnly` ones and never sees
  // them (Busy hours answers them with order counts only). Listed in sidebar
  // order: the main section, then the "Also in {pos}" pages (NAV_GROUPS).
  { path: "/insights/daily", label: "Daily compare", feature: "sales_insights", key: "insights_compare", salesOnly: true, pageId: "insights_daily", area: "sales", icon: "chart-line" },
  { path: "/insights/loss-watch", label: "Loss watch", feature: "sales_insights", key: "insights_loss_watch", adminOnly: true, pageId: "insights_loss_watch", area: "sales", icon: "eye" },
  { path: "/insights/odd-checks", label: "Odd checks", feature: "sales_insights", key: "insights_odd_checks", adminOnly: true, pageId: "insights_odd_checks", area: "sales", icon: "flag" },
  { path: "/insights/prep-forecast", label: "Prep forecast", feature: "sales_insights", key: "insights_prep", pageId: "insights_prep", area: "sales", icon: "clipboard-check" },
  // Menu performance is split in two (owner, Oct 14): Rising / Falling / Not
  // sold here, Top / Bottom under "Also in {pos}". The old /insights/menu
  // redirects (menuPerformanceRedirect in insightsModel.js).
  { path: "/insights/menu-trends", label: "Menu trends", feature: "sales_insights", key: "insights_menu_trends", salesOnly: true, pageId: "insights_menu_trends", area: "sales", icon: "trending-up" },
  { path: "/insights/add-ons", label: "Add-ons", feature: "sales_insights", key: "insights_addons", salesOnly: true, pageId: "insights_addons", area: "sales", icon: "plus" },
  { path: "/insights/returning-customers", label: "Returning customers", feature: "sales_insights", key: "insights_returning", pageId: "insights_returning", area: "sales", icon: "repeat" },
  { path: "/insights/channels", label: "Sales channels", feature: "sales_insights", key: "insights_channels", salesOnly: true, pageId: "insights_channels", area: "sales", icon: "store" },
  { path: "/insights/check-trends", label: "Check size & guests", feature: "sales_insights", key: "insights_check_trends", salesOnly: true, pageId: "insights_check_size", area: "sales", icon: "receipt" },
  { path: "/insights/busy-hours", label: "Busy hours", feature: "sales_insights", key: "insights_heatmap", pageId: "insights_busy_hours", area: "sales", icon: "clock" },
  { path: "/insights/best-sellers", label: "Best & worst sellers", feature: "sales_insights", key: "insights_menu_sellers", salesOnly: true, pageId: "insights_best_sellers", area: "sales", icon: "chart-column" },
  // Team hub (docs/TEAM_HUB_DESIGN.md). Last so they never become someone's
  // landing page ahead of the pages above. Recurring schedules are
  // admin-only on top of the feature grant (`adminOnly`).
  { path: "/team/tasks", label: "Tasks", feature: "team_tasks", key: "team_tasks", pageId: "team_tasks", area: "team", icon: "list-checks" },
  { path: "/team/chat", label: "Chat", feature: "team_chat", key: "team_chat", pageId: "team_chat", area: "team", icon: "message-square" },
  { path: "/team/templates", label: "Recurring tasks", feature: "team_tasks", key: "team_templates", adminOnly: true, pageId: "team_templates", area: "setup", icon: "repeat" },
  // Announcements go to everyone they're sent to, no grant needed
  // (`everyone`); never a landing page (firstAccessiblePath skips it).
  // "News" in the menu, the same as the app's Team tab.
  { path: "/team/announcements", label: "News", feature: null, key: "team_announcements", everyone: true, pageId: "team_news", area: "team", icon: "megaphone" },
  { path: "/team/moderation", label: "Reported messages", feature: "team_chat", key: "team_moderation", adminOnly: true, pageId: "team_moderation", area: "setup", icon: "flag" },
  // Marketing flyers (docs/MARKETING_FLYERS_CONTRACT.md). Last, so it is only
  // the landing page for someone who has nothing else; never for the App
  // Review demo login (`noDemo` - the server refuses it there too).
  { path: "/flyers", label: "Flyers", feature: "marketing_flyers", noDemo: true, pageId: "flyers", area: "team", icon: "image" },
  // Admin setup pages: no feature grant, admins only (`admin`; their APIs
  // are require_admin). Never a landing page (no `feature`).
  { path: "/users", label: "Users", feature: null, key: "setup_users", admin: true, pageId: "users", area: "setup", icon: "user" },
  { path: "/teams", label: "Teams", feature: null, key: "setup_teams", admin: true, pageId: "teams", area: "setup", icon: "users" },
  { path: "/checklists/admin", label: "Checklist builder", feature: null, key: "setup_checklist_builder", admin: true, pageId: "checklist_builder", area: "setup", icon: "clipboard-check" },
  { path: "/issue-leads", label: "Category & vendor leads", feature: null, key: "setup_issue_leads", admin: true, pageId: "issue_leads", area: "setup", icon: "contact" },
  { path: "/inventory-api", label: "Inventory from {pos}", feature: null, key: "setup_inventory_api", admin: true, pageId: "inventory_from_pos", area: "setup", icon: "database" },
  // Settings -> Integrations (docs/PLATFORM_CONTRACT.md 13.5): the store's
  // POS, WhatsApp, email and AI connections.
  { path: "/settings/integrations", label: "Integrations", feature: null, key: "setup_integrations", admin: true, pageId: "integrations", area: "setup", icon: "plug" },
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

// The six areas (docs/DESIGN_SYSTEM.md 1.3): one identity colour each
// (styles/tokens.css --area-*), the same names and order on the website and
// in the app. Area colours mark WHERE you are, never a status.
export const AREAS = {
  home: { label: "Home", icon: "layout-dashboard" },
  ordering: { label: "Ordering", icon: "shopping-cart" },
  stock: { label: "Stock", icon: "package" },
  sales: { label: "Sales", icon: "chart-line" },
  team: { label: "Team", icon: "users" },
  setup: { label: "Setup", icon: "settings" },
  platform: { label: "Platform", icon: "building-2" },
  help: { label: "Help", icon: "life-buoy" },
};

// The Insights hub (DESIGN_SYSTEM 3.1): ONE menu link; the eleven pages are
// tile tabs on the hub (components/insights/InsightsHub.jsx), each keeping
// its own address. Main section first, then the pages that repeat the
// POS's own reports ("Also in {pos}").
export const INSIGHTS_MAIN_KEYS = [
  "insights_compare",
  "insights_loss_watch",
  "insights_odd_checks",
  "insights_prep",
  "insights_menu_trends",
  "insights_addons",
  "insights_returning",
];
export const INSIGHTS_HUB = {
  key: "insights",
  path: "/insights",
  label: "Insights",
  feature: "sales_insights",
  pageId: "insights",
  area: "sales",
  icon: "chart-line",
  hubOf: [...INSIGHTS_MAIN_KEYS, ...ALSO_IN_POS_KEYS],
};

// Purely presentational grouping for the sidebar - doesn't affect routing or
// feature-gating (FEATURE_ROUTES above stays the single source of truth for
// that; each entry here is just a lookup key into it). A group renders
// nothing if this user has none of its features, same "never show what
// isn't granted" rule the old flat list already followed, and a group with
// exactly one link this user can open renders as that plain link
// (buildSidebar). Final pass (DESIGN_SYSTEM 3.1): Home, Daily Checklists,
// then Ordering, Stock, Sales, Team and Setup - the same groups, names and
// order as the app. Setup shows small sub-headings (`sections`, no extra
// click).
export const NAV_GROUPS = [
  { type: "item", feature: "overview" },
  { type: "item", feature: "daily_checklists" },
  {
    type: "group",
    key: "ordering",
    label: "Ordering",
    area: "ordering",
    features: ["reorder_candidates", "supplier_projection", "vendor_calendar", "purchase_order_cart", "placed_orders"],
  },
  {
    type: "group",
    key: "stock",
    label: "Stock",
    area: "stock",
    features: ["fix_issues", "delivery_review", "invoice_submission", "inventory_reports", "reconciliation"],
  },
  { type: "group", key: "sales", label: "Sales", area: "sales", features: ["items_sold", "open_orders", "insights"] },
  {
    type: "group",
    key: "team",
    label: "Team",
    area: "team",
    features: ["team_tasks", "team_chat", "team_announcements", "marketing_flyers"],
  },
  {
    type: "group",
    key: "setup",
    label: "Setup",
    area: "setup",
    sections: [
      { label: "People", features: ["setup_users", "setup_teams"] },
      { label: "Connections", features: ["setup_integrations", "setup_inventory_api"] },
      { label: "Ordering setup", features: ["category_edits", "vendor_contacts", "setup_issue_leads"] },
      { label: "Team setup", features: ["setup_checklist_builder", "team_templates", "team_moderation"] },
    ],
  },
];

// Every key a group lists (flat, in order).
export function groupFeatures(group) {
  if (group.type === "item") return [group.feature];
  return group.sections ? group.sections.flatMap((s) => s.features) : group.features;
}

// Below the groups, for everyone: the label printer (another app, only when
// the store has one: its address is branding `labels_url`) and Help (its
// Glossary tab is /glossary, for anyone without Help). Change password and
// sign-out live in the top bar's user menu (USER_MENU_LINKS).
export const FOOTER_LINKS = [
  { key: "help", label: "Help", path: "/help", feature: "help", activePaths: ["/help", "/glossary"], icon: "life-buoy" },
  { key: "glossary", label: "Glossary", path: "/glossary", unlessFeature: "help", icon: "book" },
  { key: "labels", label: "Label Scanner", hrefFrom: "labelsUrl", external: true, icon: "scan-barcode" },
];

// The user menu's page links (top bar, components/TopBar.jsx).
export const USER_MENU_LINKS = [
  { key: "change_password", label: "Change password", path: "/change-password", icon: "lock" },
  { key: "help", label: "Help center", path: "/help", feature: "help", icon: "life-buoy" },
  { key: "glossary", label: "Glossary", path: "/glossary", icon: "book" },
];

export const ROUTES_BY_KEY = Object.fromEntries(FEATURE_ROUTES.map((r) => [r.key || r.feature, r]));
// What a menu key can point at: a route, or the Insights hub.
const NAV_TARGETS = { ...ROUTES_BY_KEY, [INSIGHTS_HUB.key]: INSIGHTS_HUB };

// Can this user open this page? `ctx` = { user, hasFeature, canSeeSales,
// store } from AuthContext. A page the store can't have (store/gating.js)
// is hidden for everyone, admins included. The Insights hub: when any of its
// pages is open to them.
export function routeVisible(route, ctx) {
  const { user, hasFeature, canSeeSales, store } = ctx;
  if (!route) return false;
  if (route.hubOf) return route.hubOf.some((k) => routeVisible(ROUTES_BY_KEY[k], ctx));
  if (!routeAvailable(route.key || route.feature, store)) return false;
  if (route.admin) return !!user?.is_admin;
  if (!(route.everyone || hasFeature(route.feature))) return false;
  if (route.adminOnly && !user?.is_admin) return false;
  if (route.salesOnly && !canSeeSales) return false;
  if (route.noDemo && user?.demo) return false;
  return true;
}

// The Insights hub's tiles this user can open: { main, alsoIn } route lists.
export function insightsTiles(ctx) {
  const pick = (keys) => keys.map((k) => ROUTES_BY_KEY[k]).filter((r) => routeVisible(r, ctx));
  return { main: pick(INSIGHTS_MAIN_KEYS), alsoIn: pick(ALSO_IN_POS_KEYS) };
}

// Where /insights lands: the tile this user opened last (remembered), when
// they can still open it; otherwise the first main-section page.
export function insightsLandingPath(remembered, ctx) {
  const { main, alsoIn } = insightsTiles(ctx);
  const all = [...main, ...alsoIn];
  if (remembered && all.some((r) => r.path === remembered)) return remembered;
  const home = insightsHomePath(ctx.canSeeSales);
  if (all.some((r) => r.path === home)) return home;
  return all[0]?.path || home;
}

// What the sidebar shows this user, in order: { type: "link", key, route,
// label } or { type: "group", key, label, area, items, sections: [{ label,
// items }] }. Empty groups (and empty sections) are left out; a group with
// one link is a link. Labels are this store's ("{pos}" filled in); render a
// route with routeLabel(route, store.posName).
export function buildSidebar(ctx) {
  const pos = ctx.store?.posName;
  const out = [];
  const pick = (keys) => keys.map((k) => NAV_TARGETS[k]).filter((r) => routeVisible(r, ctx));
  for (const entry of NAV_GROUPS) {
    if (entry.type === "item") {
      const route = NAV_TARGETS[entry.feature];
      if (routeVisible(route, ctx)) out.push({ type: "link", key: route.path, route, label: routeLabel(route, pos) });
      continue;
    }
    const sections = (entry.sections || [{ label: null, features: entry.features }])
      .map((sec) => ({ label: sec.label, items: pick(sec.features) }))
      .filter((sec) => sec.items.length > 0);
    const items = sections.flatMap((sec) => sec.items);
    if (items.length === 0) continue;
    if (items.length === 1) {
      out.push({ type: "link", key: items[0].path, route: items[0], label: routeLabel(items[0], pos) });
      continue;
    }
    out.push({ type: "group", key: entry.key, label: entry.label, area: entry.area, items, sections });
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

// The user menu's page links for this user (Help center needs Help).
export function userMenuLinks({ hasFeature }) {
  return USER_MENU_LINKS.filter((l) => !l.feature || hasFeature(l.feature));
}

// Is this route the current page? (A route also covers its sub-paths:
// /checklists/admin/closing is the Checklist builder; the hub covers every
// /insights/... page.)
export function routeIsHere(route, pathname) {
  if (route.end) return pathname === route.path;
  return pathname === route.path || pathname.startsWith(`${route.path}/`);
}

// The groups to open so the current page is never hidden inside a
// collapsed group (a bookmark, a link from another page).
export function groupsToOpen(entries, pathname) {
  return entries.filter((e) => e.type === "group" && e.items.some((r) => routeIsHere(r, pathname))).map((e) => e.key);
}

// Every page this user can open, for the top bar's search: { route, label,
// where } where `where` is "Ordering › Vendor Calendar" style. Insights
// tiles are listed under "Sales › Insights".
export function searchablePages(ctx) {
  const pos = ctx.store?.posName;
  const out = [];
  for (const entry of buildSidebar(ctx)) {
    if (entry.type === "link") {
      out.push({ route: entry.route, label: entry.label, where: AREAS[entry.route.area]?.label || "" });
      continue;
    }
    for (const r of entry.items) {
      out.push({ route: r, label: routeLabel(r, pos), where: entry.label });
    }
  }
  const { main, alsoIn } = insightsTiles(ctx);
  for (const r of [...main, ...alsoIn]) out.push({ route: r, label: routeLabel(r, pos), where: "Sales › Insights" });
  for (const l of footerLinks(ctx)) if (!l.external) out.push({ route: l, label: l.label, where: "Help" });
  for (const l of userMenuLinks(ctx)) if (!out.some((o) => o.route.path === l.path)) out.push({ route: l, label: l.label, where: "Account" });
  return out;
}

// The page meta (title, area, icon) for a path: the route that owns it.
export function routeForPath(pathname) {
  const all = [...FEATURE_ROUTES.filter((r) => !r.retired), INSIGHTS_HUB];
  let best = null;
  for (const r of all) {
    if (routeIsHere(r, pathname) && (!best || r.path.length > best.path.length)) best = r;
  }
  return best;
}

// Fallback page registry (title + area) by page id, from the routes: used
// when the help registry (content/help/pages.json, track H) has no entry.
export const PAGE_META = Object.fromEntries(
  [...FEATURE_ROUTES.filter((r) => !r.retired), INSIGHTS_HUB].map((r) => [r.pageId, { title: r.label, area: r.area, icon: r.icon, path: r.path }]),
);
Object.assign(PAGE_META, {
  help: { title: "Help Center", area: "help", icon: "life-buoy", path: "/help" },
  glossary: { title: "Glossary", area: "help", icon: "book", path: "/glossary" },
  change_password: { title: "Change password", area: null, icon: "lock", path: "/change-password" },
  no_access: { title: "No access yet", area: null, icon: "lock", path: "/no-access" },
  not_available: { title: "Not available for this store", area: null, icon: "info", path: "/not-available" },
  platform_requests: { title: "Requests", area: "platform", icon: "inbox" },
  platform_stores: { title: "Stores", area: "platform", icon: "store" },
  platform_hosting: { title: "Hosting", area: "platform", icon: "server" },
  platform_audit: { title: "Audit", area: "platform", icon: "scroll-text" },
});
