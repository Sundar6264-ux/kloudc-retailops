// Pure pieces of the shell: Appearance (theme.js), top-bar search
// (searchModel.js) and the KloudC support helpers (kloudc.js).
import { test } from "node:test";
import assert from "node:assert/strict";
import { effectiveTheme, normalizeTheme, readTheme, themeAttribute, THEME_KEY, writeTheme } from "./theme.js";
import { helpResultPath, rankPages } from "./searchModel.js";
import { initials, KLOUDC, KLOUDC_LINKS, supportMailto } from "./kloudc.js";

function mem() {
  const data = {};
  return { data, getItem: (k) => data[k] ?? null, setItem: (k, v) => (data[k] = String(v)), removeItem: (k) => delete data[k] };
}

test("appearance: System by default, Light/Dark remembered under kc.theme", () => {
  const s = mem();
  assert.equal(readTheme(s), "system");
  assert.equal(writeTheme(s, "dark"), "dark");
  assert.equal(s.data[THEME_KEY], "dark");
  assert.equal(readTheme(s), "dark");
  assert.equal(writeTheme(s, "system"), "system");
  assert.equal(s.data[THEME_KEY], undefined, "System clears the key (the OS decides)");
  assert.equal(normalizeTheme("purple"), "system");
  assert.equal(themeAttribute("system"), null);
  assert.equal(themeAttribute("light"), "light");
  assert.equal(effectiveTheme("system", true), "dark");
  assert.equal(effectiveTheme("light", true), "light");
  const broken = { getItem: () => { throw new Error("off"); }, setItem: () => { throw new Error("off"); } };
  assert.equal(readTheme(broken), "system");
  assert.equal(writeTheme(broken, "dark"), "dark");
});

test("search: label prefix first, then word starts, then anywhere; every word must match", () => {
  const pages = [
    { label: "Vendor Calendar", where: "Ordering" },
    { label: "Vendor contacts", where: "Setup" },
    { label: "Inventory Reports", where: "Stock" },
    { label: "Reorder Candidates", where: "Ordering" },
    { label: "Category & vendor leads", where: "Setup" },
  ];
  assert.deepEqual(rankPages(pages, "vendor").map((p) => p.label), ["Vendor Calendar", "Vendor contacts", "Category & vendor leads"]);
  // "order" is inside "Reorder" (label) but only in where Vendor Calendar lives.
  assert.deepEqual(rankPages(pages, "order").map((p) => p.label), ["Reorder Candidates", "Vendor Calendar"]);
  assert.deepEqual(rankPages(pages, "vendor setup").map((p) => p.label), ["Vendor contacts", "Category & vendor leads"]);
  assert.equal(rankPages(pages, "").length, pages.length);
  assert.deepEqual(rankPages(pages, "zzz"), []);
});

test("search: help results open the Help Center / Glossary addresses", () => {
  assert.equal(helpResultPath({ kind: "article", id: "reorder" }), "/help?a=reorder");
  assert.equal(helpResultPath({ kind: "faq", id: "store-id" }), "/help?tab=faq&s=store-id");
  assert.equal(helpResultPath({ kind: "trouble", id: "cant-sign-in" }), "/help?tab=troubleshooting&s=cant-sign-in");
  assert.equal(helpResultPath({ kind: "term", id: "concept_lead_time" }), "/glossary?k=concept_lead_time");
  assert.equal(helpResultPath({ kind: "page", id: "x" }), null);
  assert.equal(helpResultPath(null), null);
});

test("KloudC facts, initials and the support email (never auto-sent, never a password)", () => {
  assert.equal(KLOUDC.email, "support@kloudc.com");
  assert.equal(KLOUDC_LINKS.privacy, "https://kloudc.com/kloudc-privacy-policy/");
  assert.ok(!("terms" in KLOUDC_LINKS), "no Terms link until a Terms page exists");
  assert.equal(initials("avery.b"), "AB");
  assert.equal(initials("dana"), "DA");
  assert.equal(initials(""), "?");
  const m = supportMailto({ storeCode: "MAIN_STORE", page: "Vendor Calendar" });
  assert.match(m, /^mailto:support@kloudc\.com\?subject=RetailOps%20support%20-%20MAIN_STORE&body=/);
  const body = decodeURIComponent(m.split("body=")[1]);
  assert.match(body, /Page: Vendor Calendar/);
  assert.match(body, /Never send your password/);
});
