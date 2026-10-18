// Capability and store-feature gating for the website (docs/M2_CONTRACT.md
// section 6). The ONE place that says which page, link or control needs
// which POS capability (M1 `capabilities`, from /api/auth/me or branding) or
// store feature (`store_features`: ap_inbox, labels, whatsapp, ai).
//
// A page is shown when all hold: the user's grant (server-filtered
// allowed_features), the role rules in featureRoutes.js (admin, adminOnly,
// salesOnly, noDemo), and the requirements below. A direct link to a page
// the store can't have opens "Not available for this store"
// (pages/NotAvailablePage.jsx), never a crash or a blank page. The server's
// 409 stays the backstop - this only hides what would fail.
//
// Pure module (no React): tested by gating.test.mjs with
// `node --test src/store/gating.test.mjs`.

// Every Insights page reads POS analytics reports.
const INSIGHTS = { capabilities: ["analytics_reports"] };
const INSIGHTS_CARDS = { capabilities: ["analytics_reports", "card_fingerprints"] };

// Keyed like featureRoutes.js ROUTES_BY_KEY (route `key` or `feature`), plus
// the footer "labels" link. A key not listed works for every store.
export const ROUTE_REQUIREMENTS = {
  open_orders: { capabilities: ["open_orders"] },
  insights_compare: INSIGHTS,
  insights_loss_watch: INSIGHTS_CARDS,
  insights_odd_checks: INSIGHTS,
  insights_prep: INSIGHTS,
  insights_menu_trends: INSIGHTS,
  insights_addons: INSIGHTS,
  insights_returning: INSIGHTS_CARDS,
  insights_channels: INSIGHTS,
  insights_check_trends: INSIGHTS,
  insights_heatmap: INSIGHTS,
  insights_menu_sellers: INSIGHTS,
  setup_inventory_api: { capabilities: ["catalog", "stock_levels"] },
  // Footer "Label Scanner": only when the store has a labels site (its href).
  labels: { storeFeatures: ["labels"] },
};

// Grant-level (feature key, app/features.py) requirements. hasFeature()
// applies these, so an admin - who holds every grant - still loses what the
// store can't do (the server already drops them from a non-admin's
// allowed_features: features_for_store + FEATURE_STORE_FEATURES).
export const FEATURE_REQUIREMENTS = {
  open_orders: { capabilities: ["open_orders"] },
  sales_insights: { capabilities: ["analytics_reports"] },
  // The Inventory Bot (ChatWidget) is the store's AI switch.
  ask_bot: { storeFeatures: ["ai"] },
};

// Parts of a page: the page stays, the control goes.
export const ELEMENT_REQUIREMENTS = {
  // Inventory Reports: the upload card.
  inventory_upload: { capabilities: ["csv_catalog_import"] },
  // Inventory Reports: the Price change log tab.
  price_change_log: { capabilities: ["catalog"] },
  // Items Sold: the live "Today" choice.
  items_sold_today: { capabilities: ["live_today_sales"] },
  // Fix Issues / Reconciliation / Delivery Review: "received" evidence.
  received_evidence: { capabilities: ["receiving_records"] },
  // Overview: the sales-accuracy badge.
  sales_accuracy_badge: { capabilities: ["day_totals"] },
  // Invoices: "Email to AP inbox" and "Mark AP closed".
  invoice_ap_inbox: { storeFeatures: ["ap_inbox"] },
  // WhatsApp sends: invoice chat, flyers, checklist group picker, Send PO.
  whatsapp: { storeFeatures: ["whatsapp"] },
  // Sidebar "Also in <pos>" subgroup.
  also_in_pos: { capabilities: ["analytics_reports"] },
  // Inventory from <pos>: "Check barcodes now" (the menu re-check reads the
  // POS menu's change stamp; the server answers 409 without it).
  menu_recheck: { capabilities: ["catalog_change_stamp"] },
};

// The contract table's rows, for the coverage test.
export const CONTRACT_ROWS = {
  routes: Object.keys(ROUTE_REQUIREMENTS),
  features: Object.keys(FEATURE_REQUIREMENTS),
  elements: Object.keys(ELEMENT_REQUIREMENTS),
};

// `store` = { capabilities, storeFeatures } (StoreContext's store view).
// null / undefined means "not told" (an older server with no capability
// list, or no branding yet): treated as everything on, which is exactly the
// pre-M2 behaviour - the server still refuses what it can't do. The one
// exception is `labels`: there is nothing to link to without a labels URL.
export function meetsRequirement(req, store) {
  if (!req) return true;
  const caps = store?.capabilities;
  const feats = store?.storeFeatures;
  for (const c of req.capabilities || []) {
    if (Array.isArray(caps) && !caps.includes(c)) return false;
  }
  for (const f of req.storeFeatures || []) {
    if (f === "labels" && !store?.labelsUrl) return false;
    if (Array.isArray(feats) && !feats.includes(f)) return false;
  }
  return true;
}

export function routeAvailable(routeKey, store) {
  return meetsRequirement(ROUTE_REQUIREMENTS[routeKey], store);
}

export function featureAvailable(featureKey, store) {
  return meetsRequirement(FEATURE_REQUIREMENTS[featureKey], store);
}

export function elementAvailable(name, store) {
  if (!(name in ELEMENT_REQUIREMENTS)) throw new Error(`Unknown gated element: ${name}`);
  return meetsRequirement(ELEMENT_REQUIREMENTS[name], store);
}

// The POS name for text built outside React (row facts, labels made in
// event handlers): set from branding at boot and from /me after login
// (StoreContext / AuthContext), like format.js's store time zone.
let currentPos = null;
export function setCurrentPosName(name) {
  currentPos = typeof name === "string" && name.trim() ? name.trim() : null;
}
export function currentPosName() {
  return currentPos || "the POS";
}

// "{pos}" -> the store's POS name ("Toast", "Clover"). A store with no POS
// name yet reads "the POS".
export function posText(text, posName) {
  return String(text).replace(/\{pos\}/g, posName || "the POS");
}
