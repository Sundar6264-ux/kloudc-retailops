// Store branding helpers (docs/M2_CONTRACT.md section 2) and the store time
// zone (format.js).   node --test src/store/branding.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { BRANDING } from "../test/fixtures/branding.js";
import {
  PRODUCT_DEFAULTS,
  brandCssVars,
  contrastRatio,
  documentTitle,
  normalizeBranding,
  readableOnWhiteText,
  storeView,
} from "./branding.js";
import {
  DEFAULT_STORE_TIME_ZONE,
  formatRestaurantTime,
  getStoreTimeZone,
  lazyStoreFormat,
  setStoreTimeZone,
  storeDateStr,
  storeZoneCity,
} from "../format.js";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "../../..");

test("a store's branding becomes the store object", () => {
  const s = normalizeBranding(BRANDING.RETAILOPS_TEST_TOAST);
  assert.equal(s.loaded, true);
  assert.equal(s.code, "RETAILOPS_TEST_TOAST");
  assert.equal(s.name, "Test Toast Market");
  assert.equal(s.shortName, "Toast Test");
  assert.equal(s.productName, "KloudC RetailOps");
  assert.equal(s.company, "KloudC LLC");
  assert.equal(s.primaryColor, "#1D4ED8");
  assert.equal(s.timezone, "America/Chicago");
  assert.equal(s.posName, "Toast");
  assert.equal(s.labelsUrl, "https://labels-retailops-test-toast.retailops.invalid");
  assert.deepEqual(s.storeFeatures, ["ap_inbox", "labels", "whatsapp", "ai"]);
  assert.equal(normalizeBranding(BRANDING.DEMO).demo, true);
  assert.equal(normalizeBranding(BRANDING.RETAILOPS_TEST_CLOVER).labelsUrl, null);
});

test("no branding (older server, network error): product defaults, nothing assumed", () => {
  const s = normalizeBranding(null);
  assert.equal(s.loaded, false);
  assert.equal(s.name, "KloudC RetailOps");
  assert.equal(s.shortName, "KloudC RetailOps");
  assert.equal(s.primaryColor, PRODUCT_DEFAULTS.primaryColor);
  assert.equal(s.logoUrl, "/branding/logo.png");
  assert.equal(s.capabilities, null);
  assert.equal(s.storeFeatures, null);
  assert.equal(s.timezone, null);
  // Bad values fall back too.
  const bad = normalizeBranding({ name: "  ", primary_color: "red", capabilities: "all" });
  assert.equal(bad.name, "KloudC RetailOps");
  assert.equal(bad.primaryColor, PRODUCT_DEFAULTS.primaryColor);
  assert.equal(bad.capabilities, null);
});

test("document title: the store's name with the product, or the product alone", () => {
  assert.equal(documentTitle(normalizeBranding(BRANDING.RETAILOPS_TEST_CLOVER)), "Test Clover Grocery - KloudC RetailOps");
  assert.equal(documentTitle(normalizeBranding(null)), "KloudC RetailOps");
});

test("brand colours: white button text always keeps 4.5:1", () => {
  for (const code of Object.keys(BRANDING)) {
    const vars = brandCssVars(BRANDING[code].primary_color, BRANDING[code].secondary_color);
    assert.ok(contrastRatio(vars["--accent"], "#FFFFFF") >= 4.5, code);
    assert.equal(vars["--brand"], BRANDING[code].primary_color.toUpperCase());
    assert.match(vars["--accent-rgb"], /^\d+, \d+, \d+$/);
  }
  // Dark enough already: unchanged. Too light (a pale yellow): darkened.
  assert.equal(readableOnWhiteText("#1D4ED8"), "#1D4ED8");
  const fixed = readableOnWhiteText("#FDE047");
  assert.notEqual(fixed, "#FDE047");
  assert.ok(contrastRatio(fixed, "#FFFFFF") >= 4.5);
});

test("store time zone: set once, used by every formatter, bad zones ignored", () => {
  const moment = "2026-11-02T05:30:00Z"; // 11:30 PM Nov 1 in Chicago, 12:30 AM Nov 2 in New York
  assert.equal(getStoreTimeZone(), DEFAULT_STORE_TIME_ZONE);
  const lazy = lazyStoreFormat("en-US", { hour: "numeric", minute: "2-digit" });
  assert.equal(storeDateStr(moment), "2026-11-02");
  assert.equal(lazy.format(new Date(moment)), "12:30 AM");
  setStoreTimeZone("America/Chicago");
  assert.equal(storeDateStr(moment), "2026-11-01");
  assert.equal(lazy.format(new Date(moment)), "11:30 PM");
  assert.match(formatRestaurantTime(moment), /CST/);
  assert.equal(storeZoneCity(), "Chicago");
  setStoreTimeZone("Not/AZone");
  assert.equal(getStoreTimeZone(), "America/Chicago");
  setStoreTimeZone("America/Los_Angeles");
  assert.equal(storeZoneCity(), "Los Angeles");
  setStoreTimeZone(DEFAULT_STORE_TIME_ZONE);
});

test("storeView: /me's POS facts on top of branding", () => {
  const s = storeView(normalizeBranding(BRANDING.RETAILOPS_TEST_TOAST), { store_code: "RETAILOPS_TEST_TOAST", pos_name: "Toast" });
  assert.equal(s.name, "Test Toast Market");
  assert.equal(s.posName, "Toast");
  assert.equal(s.capabilities.length, BRANDING.RETAILOPS_TEST_TOAST.capabilities.length);
});

test("web fixtures match the server's committed branding bodies (field names) and each other", () => {
  for (const code of Object.keys(BRANDING)) {
    const e2e = JSON.parse(readFileSync(join(here, `../../e2e/fixtures/branding-${code}.json`), "utf8"));
    assert.deepEqual(e2e, BRANDING[code], `e2e fixture ${code} = src fixture`);
    const server = join(repo, `backend/tests/fixtures/stores/${code}.branding.json`);
    if (!existsSync(server)) continue; // generated by the settings batch (MT7)
    const body = JSON.parse(readFileSync(server, "utf8"));
    assert.deepEqual(Object.keys(body).sort(), Object.keys(BRANDING[code]).sort(), `${code} field names`);
  }
});

test("store links and images are http(s) or a path on this site, never javascript:/data:", () => {
  const b = normalizeBranding({
    labels_url: "javascript:alert(document.domain)",
    logo_url: "data:image/svg+xml,<svg onload=alert(1)>",
    icon_url: "//evil.example.net/i.png",
    legal: { privacy_url: " JaVaScRiPt:alert(1)", terms_url: "https://kloudc.com/terms", support_url: "/help" },
  });
  assert.equal(b.labelsUrl, null);
  assert.equal(b.logoUrl, PRODUCT_DEFAULTS.logoPath);
  assert.equal(b.iconUrl, PRODUCT_DEFAULTS.iconPath);
  assert.equal(b.legal.privacyUrl, null);
  assert.equal(b.legal.termsUrl, "https://kloudc.com/terms");
  assert.equal(b.legal.supportUrl, "/help");
  assert.equal(normalizeBranding({ labels_url: "https://labels.alpha.example.com" }).labelsUrl, "https://labels.alpha.example.com");
});

// Store ID privacy (2026-10): a shared server's neutral body before sign-in.
test("a neutral (product) body is marked, a store's own or an older body is not", async () => {
  const { normalizeBranding } = await import("./branding.js");
  assert.equal(normalizeBranding({ name: "KloudC RetailOps", store_code: "ALPHA", store_specific: false }).storeSpecific, false);
  assert.equal(normalizeBranding({ name: "Alpha Market", store_code: "ALPHA", store_specific: true }).storeSpecific, true);
  assert.equal(normalizeBranding({ name: "Alpha Market", store_code: "ALPHA" }).storeSpecific, true);
});

test("a public checklist link names its store from the form, never from branding", async () => {
  const { linkStore } = await import("./branding.js");
  assert.deepEqual(linkStore({ store: { name: " Alpha Market ", short_name: "Alpha", logo_url: "/branding/logo.png" } }), {
    name: "Alpha Market",
    shortName: "Alpha",
    logoUrl: "/branding/logo.png",
  });
  assert.deepEqual(linkStore({ store: { name: "Alpha Market" } }), { name: "Alpha Market", shortName: "Alpha Market", logoUrl: null });
  assert.equal(linkStore({}), null);
  assert.equal(linkStore(null), null);
  assert.equal(linkStore({ store: { name: "  " } }), null);
});
