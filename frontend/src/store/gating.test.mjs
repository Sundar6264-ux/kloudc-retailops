// Capability / store-feature gating (docs/M2_CONTRACT.md section 6): every
// row of the contract table is present, and for each fake store (section 7)
// the pages, footer links and page controls an admin gets are exactly the
// expected ones.
//   node --test src/store/gating.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { FEATURE_ROUTES, ROUTES_BY_KEY, buildSidebar, firstAccessiblePath, footerLinks, routeVisible } from "../featureRoutes.js";
import { BRANDING } from "../test/fixtures/branding.js";
import { normalizeBranding, storeView } from "./branding.js";
import {
  ELEMENT_REQUIREMENTS,
  FEATURE_REQUIREMENTS,
  ROUTE_REQUIREMENTS,
  elementAvailable,
  featureAvailable,
  meetsRequirement,
  posText,
  routeAvailable,
} from "./gating.js";

const storeOf = (code) => storeView(normalizeBranding(BRANDING[code]), null);
const TOAST = storeOf("STOREOPS_TEST_TOAST");
const CLOVER = storeOf("STOREOPS_TEST_CLOVER");
const DEMO = storeOf("DEMO");

// The same rules as AuthContext.hasFeature (admin: every grant, minus what
// the store can't have).
function adminCtx(store) {
  const user = { username: "admin", is_admin: true, allowed_features: [] };
  const hasFeature = (key) => featureAvailable(key, store);
  return { user, hasFeature, canSeeSales: true, store };
}

const ALL_INSIGHTS = [
  "insights_compare",
  "insights_loss_watch",
  "insights_odd_checks",
  "insights_prep",
  "insights_menu_trends",
  "insights_addons",
  "insights_returning",
  "insights_channels",
  "insights_check_trends",
  "insights_heatmap",
  "insights_menu_sellers",
];

test("every row of the contract's gating table is declared", () => {
  // Routes / links (website column).
  assert.deepEqual(ROUTE_REQUIREMENTS.open_orders, { capabilities: ["open_orders"] });
  for (const k of ALL_INSIGHTS) {
    assert.ok(ROUTE_REQUIREMENTS[k].capabilities.includes("analytics_reports"), k);
  }
  for (const k of ["insights_returning", "insights_loss_watch"]) {
    assert.deepEqual(ROUTE_REQUIREMENTS[k].capabilities, ["analytics_reports", "card_fingerprints"], k);
  }
  assert.deepEqual(ROUTE_REQUIREMENTS.setup_inventory_api, { capabilities: ["catalog", "stock_levels"] });
  assert.deepEqual(ROUTE_REQUIREMENTS.labels, { storeFeatures: ["labels"] });
  // Every insights_* route key in featureRoutes.js is covered.
  const insightKeys = FEATURE_ROUTES.map((r) => r.key || r.feature).filter((k) => k.startsWith("insights_"));
  assert.deepEqual(insightKeys.sort(), [...ALL_INSIGHTS].sort());
  for (const k of Object.keys(ROUTE_REQUIREMENTS)) {
    assert.ok(ROUTES_BY_KEY[k] || k === "labels", `${k} is a route key or the labels link`);
  }
  // Grants.
  assert.deepEqual(FEATURE_REQUIREMENTS.ask_bot, { storeFeatures: ["ai"] });
  assert.deepEqual(FEATURE_REQUIREMENTS.sales_insights, { capabilities: ["analytics_reports"] });
  assert.deepEqual(FEATURE_REQUIREMENTS.open_orders, { capabilities: ["open_orders"] });
  // Partial (element) rows.
  assert.deepEqual(ELEMENT_REQUIREMENTS, {
    inventory_upload: { capabilities: ["csv_catalog_import"] },
    price_change_log: { capabilities: ["catalog"] },
    items_sold_today: { capabilities: ["live_today_sales"] },
    received_evidence: { capabilities: ["receiving_records"] },
    sales_accuracy_badge: { capabilities: ["day_totals"] },
    invoice_ap_inbox: { storeFeatures: ["ap_inbox"] },
    whatsapp: { storeFeatures: ["whatsapp"] },
    also_in_pos: { capabilities: ["analytics_reports"] },
    menu_recheck: { capabilities: ["catalog_change_stamp"] },
  });
});

const pageKeys = FEATURE_ROUTES.filter((r) => !r.retired).map((r) => r.key || r.feature);
const hiddenPages = (store) => pageKeys.filter((k) => !routeVisible(ROUTES_BY_KEY[k], adminCtx(store)));
const elementsOn = (store) => Object.keys(ELEMENT_REQUIREMENTS).filter((e) => elementAvailable(e, store));

test("Toast store: every page, the Label Scanner, every control", () => {
  assert.deepEqual(hiddenPages(TOAST), []);
  assert.ok(footerLinks(adminCtx(TOAST)).some((l) => l.key === "labels" && l.href === BRANDING.STOREOPS_TEST_TOAST.labels_url));
  assert.deepEqual(elementsOn(TOAST), Object.keys(ELEMENT_REQUIREMENTS));
  assert.equal(featureAvailable("ask_bot", TOAST), true);
});

test("Clover store: no Insights, no labels, no AP / WhatsApp / AI", () => {
  assert.deepEqual(hiddenPages(CLOVER).sort(), [...ALL_INSIGHTS].sort());
  assert.ok(!footerLinks(adminCtx(CLOVER)).some((l) => l.key === "labels"));
  assert.deepEqual(elementsOn(CLOVER), ["price_change_log", "items_sold_today"]);
  assert.equal(featureAvailable("ask_bot", CLOVER), false);
  assert.equal(routeAvailable("open_orders", CLOVER), true);
  assert.equal(routeAvailable("setup_inventory_api", CLOVER), true);
});

test("DEMO (CSV) store: no open checks, Insights or POS inventory page; upload stays", () => {
  assert.deepEqual(hiddenPages(DEMO).sort(), ["open_orders", "setup_inventory_api", ...ALL_INSIGHTS].sort());
  assert.ok(!footerLinks(adminCtx(DEMO)).some((l) => l.key === "labels"));
  assert.deepEqual(elementsOn(DEMO), ["inventory_upload"]);
  assert.equal(featureAvailable("ask_bot", DEMO), false);
});

test("sidebar labels follow the POS name", () => {
  const flat = (entries) =>
    JSON.stringify(entries.map((e) => (e.type === "link" ? e.label : [e.label, e.subgroups.map((s) => s.label)])));
  const toast = buildSidebar(adminCtx(TOAST));
  const setup = toast.find((e) => e.key === "setup");
  assert.ok(setup.items.some((r) => r.key === "setup_inventory_api"));
  assert.match(flat(toast), /Also in Toast/);
  const clover = buildSidebar(adminCtx(CLOVER));
  assert.doesNotMatch(flat(clover), /Toast|Insights|Also in/);
  // Clover: "Sales & Insights" has no Insights pages, so it reads "Sales".
  assert.equal(clover.find((e) => e.key === "sales_insights").label, "Sales");
  assert.equal(posText("Inventory from {pos}", "Clover"), "Inventory from Clover");
  assert.equal(posText("Also in {pos}", null), "Also in the POS");
});

test("landing page skips pages the store can't have", () => {
  const onlyOpenChecks = (key) => key === "open_orders" && featureAvailable(key, DEMO);
  assert.equal(firstAccessiblePath(onlyOpenChecks, DEMO), "/no-access");
  assert.equal(firstAccessiblePath(adminCtx(CLOVER).hasFeature, CLOVER), "/");
});

test("an older server that says nothing keeps every page (pre-M2 behaviour), but no labels link", () => {
  const unknown = storeView(normalizeBranding(null), null);
  assert.equal(unknown.capabilities, null);
  assert.deepEqual(hiddenPages(unknown), []);
  assert.equal(meetsRequirement(ROUTE_REQUIREMENTS.labels, unknown), false);
  assert.equal(elementAvailable("whatsapp", unknown), true);
});

test("/api/auth/me wins over branding for capabilities and store features", () => {
  const me = { pos_name: "Toast", capabilities: ["open_orders"], store_features: ["whatsapp"] };
  const s = storeView(normalizeBranding(BRANDING.STOREOPS_TEST_CLOVER), me);
  assert.equal(s.posName, "Toast");
  assert.deepEqual(s.capabilities, ["open_orders"]);
  assert.equal(elementAvailable("whatsapp", s), true);
  // /me without the M2 key: branding's list stays.
  const old = storeView(normalizeBranding(BRANDING.STOREOPS_TEST_CLOVER), { pos_name: "Clover", capabilities: ["catalog"] });
  assert.deepEqual(old.storeFeatures, []);
});

test("unknown element names are a programming error", () => {
  assert.throws(() => elementAvailable("no_such_control", TOAST), /Unknown gated element/);
});
