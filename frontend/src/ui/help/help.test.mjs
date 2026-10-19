// Help, glossary and ⓘ info (docs/DESIGN_SYSTEM.md 4 and 5.3):
// - the content in ../content/help is valid and every link resolves,
// - every page id the website uses (PageHeader / InfoButton / FieldInfo /
//   openPanel / featureRoutes pageId) has info text in the registry,
// - every route in the menu has a registry page, every platform tab too,
// - every glossary link (content links, glossaryPath / HelpLink term=
//   literals) names a real term,
// - the model's helpers behave.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  LEGACY_SECTIONS,
  audienceFor,
  buildHelp,
  canOpenPageFor,
  fillDeep,
  glossaryPath,
  helpPath,
  mergeTerms,
  pageIdForPath,
  parseInline,
  plainText,
  searchHelp,
  storeWords,
  supportMailto,
  validateHelp,
  whoCanSee,
} from "./helpModel.js";
import { FEATURE_ROUTES } from "../../featureRoutes.js";
import { PLATFORM_TABS } from "../../platform/platformModel.js";

const here = dirname(fileURLToPath(import.meta.url));
const SRC = join(here, "../..");
const CONTENT = join(SRC, "../../content/help");
const GLOSSARY_PY = join(SRC, "../../backend/app/glossary.py");

const read = (f) => JSON.parse(readFileSync(join(CONTENT, f), "utf8"));

function loadRaw() {
  const articles = readdirSync(join(CONTENT, "articles"))
    .filter((f) => f.endsWith(".json"))
    .map((f) => read(`articles/${f}`));
  return {
    manifest: read("manifest.json"),
    pages: read("pages.json"),
    articles,
    gettingStarted: read("getting-started.json"),
    faq: read("faq.json"),
    troubleshooting: read("troubleshooting.json"),
    glossary: read("glossary.json"),
    glossaryCategories: read("glossary-categories.json"),
    contact: read("contact.json"),
  };
}

const RAW = loadRaw();
const HELP = buildHelp(RAW);

// Status term keys from the backend's glossary (the first argument of each
// _t(...)), plus the bundled concept terms: what /api/glossary serves.
function statusKeys() {
  const py = readFileSync(GLOSSARY_PY, "utf8");
  return [...py.matchAll(/_t\(\s*f?"([a-z][a-z0-9_]*)"/g)].map((m) => m[1]);
}
const GLOSSARY_KEYS = new Set([...statusKeys(), ...RAW.glossary.map((t) => t.key)]);

function sourceFiles(dir = SRC, out = []) {
  for (const name of readdirSync(dir)) {
    if (name === "node_modules" || name.startsWith(".")) continue;
    const p = join(dir, name);
    if (statSync(p).isDirectory()) sourceFiles(p, out);
    else if (/\.(jsx?|mjs)$/.test(name) && !name.endsWith(".test.mjs")) out.push(p);
  }
  return out;
}
const SOURCES = sourceFiles().map((p) => ({ path: p.slice(SRC.length + 1), text: readFileSync(p, "utf8") }));

// ------------------------------------------------------------------ content

test("the help content is valid and every reference resolves", () => {
  assert.ok(statusKeys().length > 200, "status keys found in glossary.py");
  const errors = validateHelp(HELP, { glossaryKeys: GLOSSARY_KEYS });
  assert.deepEqual(errors, []);
});

test("the validator really catches mistakes", () => {
  const broken = JSON.parse(JSON.stringify(RAW));
  broken.pages[0].info.summary = "See [this](page:nowhere) and [that](term:no_such_term) — {price}";
  broken.articles[0].related = ["no-such-guide"];
  const errors = validateHelp(buildHelp(broken), { glossaryKeys: GLOSSARY_KEYS });
  const text = errors.join("\n");
  assert.match(text, /unknown page link nowhere/);
  assert.match(text, /unknown term link no_such_term/);
  assert.match(text, /en\/em dash/);
  assert.match(text, /unknown placeholder \{price\}/);
  assert.match(text, /unknown related no-such-guide/);
});

test("every area has guides and every audience has a start track", () => {
  const groups = new Set(RAW.articles.map((a) => a.group));
  for (const g of ["basics", "home", "ordering", "stock", "sales", "team", "setup", "account", "master"]) {
    assert.ok(groups.has(g), `guides for ${g}`);
  }
  assert.deepEqual(Object.keys(RAW.gettingStarted).sort(), ["master", "owner", "staff"]);
  assert.ok(RAW.faq.length >= 30);
  assert.ok(RAW.troubleshooting.length >= 18);
});

// ------------------------------------------------------------------ the UI uses only registered pages

test("every page id the website uses has info text in the registry", () => {
  const used = new Map();
  const add = (id, where, section) => {
    if (!used.has(id)) used.set(id, []);
    used.get(id).push({ where, section });
  };
  for (const { path, text } of SOURCES) {
    for (const m of text.matchAll(/pageId=\{?"([a-z_]+)"\}?(?:\s+section=\{?"([a-z_]+)"\}?)?/g)) add(m[1], path, m[2]);
    for (const m of text.matchAll(/pageId:\s*"([a-z_]+)"/g)) add(m[1], path);
    for (const m of text.matchAll(/openPanel\(\s*"([a-z_]+)"/g)) add(m[1], path);
  }
  assert.ok(used.has("help"), "the scan finds the Help Center's own ⓘ");
  for (const [id, uses] of used) {
    const page = HELP.pages.get(id);
    assert.ok(page, `page id "${id}" (${uses[0].where}) is in content/help/pages.json`);
    assert.ok(page.info?.summary, `page "${id}" has an info summary`);
    for (const u of uses) {
      if (u.section) assert.ok(page.sections?.[u.section], `section "${u.section}" of "${id}" (${u.where})`);
    }
  }
});

test("every page in the menu and every platform tab has a registry page", () => {
  for (const r of FEATURE_ROUTES.filter((x) => !x.retired)) {
    const id = pageIdForPath(HELP, r.path);
    assert.ok(id, `route ${r.path} has a page in content/help/pages.json`);
    assert.equal(HELP.pages.get(id).web.route, r.path, `route ${r.path} maps to its own page, not a parent`);
    if (r.key || r.feature) {
      const page = HELP.pages.get(id);
      if (r.admin) assert.ok(page.gating.admin, `${id}: admins only like the route`);
    }
  }
  for (const t of PLATFORM_TABS) assert.ok(HELP.pages.has(`platform_${t.key}`), `platform tab ${t.key}`);
  for (const id of ["help", "glossary", "change_password", "no_access", "not_available", "login", "signup"]) {
    assert.ok(HELP.pages.has(id), id);
  }
});

test("every glossary link resolves", () => {
  const links = [];
  for (const { path, text } of SOURCES) {
    for (const m of text.matchAll(/glossaryPath\(\s*"([a-z_]+)"/g)) links.push([m[1], path]);
    for (const m of text.matchAll(/<HelpLink[^>]*\bterm="([a-z_]+)"/g)) links.push([m[1], path]);
    for (const m of text.matchAll(/\/glossary\?k=([a-z_]+)/g)) links.push([m[1], path]);
  }
  for (const [key, path] of links) assert.ok(GLOSSARY_KEYS.has(key), `${path}: glossary link to ${key}`);
  for (const p of RAW.pages) for (const k of p.terms) assert.ok(GLOSSARY_KEYS.has(k), `page ${p.id}: term ${k}`);
});

test("old Help section links open the guide that replaced them", () => {
  for (const [section, { article }] of Object.entries(LEGACY_SECTIONS)) {
    assert.ok(HELP.articles.has(article), `${section} -> ${article}`);
  }
  for (const { path, text } of SOURCES) {
    for (const m of text.matchAll(/state=\{\{\s*section:\s*"([a-z-]+)"/g)) {
      assert.ok(LEGACY_SECTIONS[m[1]], `${path}: old section ${m[1]}`);
    }
  }
});

// ------------------------------------------------------------------ helpers

test("inline marks: bold and the five link kinds", () => {
  const parts = parseInline("Open **Users**, [the guide](article:setup-users), [Lead time](term:lead_time) or [KloudC](https://kloudc.com).");
  assert.deepEqual(
    parts.filter((p) => p.type !== "text").map((p) => [p.type, p.kind || null, p.target || null]),
    [
      ["bold", null, null],
      ["link", "article", "setup-users"],
      ["link", "term", "lead_time"],
      ["link", "url", "https://kloudc.com"],
    ],
  );
  assert.equal(plainText("Open **Users** or [Help](page:help)."), "Open Users or Help.");
  assert.equal(parseInline("[x](javascript:alert(1))")[0].kind, "invalid");
});

test("store words fill every placeholder", () => {
  const words = storeWords({ posName: "Clover", storeName: "Market Kitchen" });
  const filled = fillDeep({ a: ["{pos} sales at {store}", { b: "Email {support_email}" }] }, words);
  assert.deepEqual(filled, { a: ["Clover sales at Market Kitchen", { b: "Email support@kloudc.com" }] });
  assert.equal(storeWords({})["{pos}"], "the POS");
});

test("help addresses", () => {
  assert.equal(helpPath(), "/help");
  assert.equal(helpPath({ tab: "faq" }), "/help?tab=faq");
  assert.equal(helpPath({ tab: "start" }), "/help?tab=start");
  assert.equal(helpPath({ article: "reorder", anchor: "lookback" }), "/help?a=reorder&s=lookback");
  assert.equal(glossaryPath("lead_time"), "/glossary?k=lead_time");
  assert.equal(pageIdForPath(HELP, "/insights/busy-hours"), "insights_busy_hours");
  assert.equal(pageIdForPath(HELP, "/checklists/admin/closing"), "checklist_builder");
  assert.equal(pageIdForPath(HELP, "/"), "home");
});

test("who can see a page", () => {
  assert.equal(whoCanSee(HELP.pages.get("users")), "Admins only.");
  assert.match(whoCanSee(HELP.pages.get("insights_loss_watch")), /^Admins only, with the \*\*Sales insights\*\* access/);
  assert.match(whoCanSee(HELP.pages.get("items_sold")), /hidden from users with Hide sales numbers/);
  assert.equal(whoCanSee(HELP.pages.get("login")), "Anyone, no sign-in needed.");
  assert.equal(whoCanSee(HELP.pages.get("platform_audit")), "KloudC master logins only.");
});

test("search finds pages, guides, answers and terms, and hides pages a user can't open", () => {
  const terms = mergeTerms([], RAW.glossary, storeWords({}));
  const staff = { user: { id: 2, is_admin: false }, hasFeature: (f) => f === "invoice_submission", canSeeSales: true };
  const all = searchHelp(HELP, "lead time", { terms });
  assert.ok(all.some((r) => r.kind === "article"), "a guide mentions lead time");
  const vendor = searchHelp(HELP, "vendor calendar", { terms });
  assert.equal(vendor[0].kind, "page");
  assert.equal(vendor[0].id, "vendor_calendar");
  const asStaff = searchHelp(HELP, "users", { terms, canOpenPage: canOpenPageFor(staff) });
  assert.ok(!asStaff.some((r) => r.kind === "page" && r.id === "users"), "staff don't see the Users page");
  assert.ok(searchHelp(HELP, "container", { terms }).some((r) => r.kind === "term" && r.id === "concept_container"));
  assert.deepEqual(searchHelp(HELP, "   ", { terms }), []);
});

test("page access mirrors the routes", () => {
  const admin = { user: { id: 1, is_admin: true }, hasFeature: () => true, canSeeSales: true };
  const hidden = { user: { id: 3, is_admin: false }, hasFeature: (f) => f === "items_sold" || f === "reorder_candidates", canSeeSales: false };
  const master = { user: { scope: "platform" } };
  const open = (auth, id) => canOpenPageFor(auth)(HELP.pages.get(id));
  assert.ok(open(admin, "users") && open(admin, "reorder") && !open(admin, "platform_stores"));
  assert.ok(open(hidden, "reorder") && !open(hidden, "items_sold") && !open(hidden, "users"));
  assert.ok(open(master, "platform_stores") && !open(master, "reorder"));
  assert.ok(open(null, "login") && !open(null, "reorder"));
  assert.equal(audienceFor(admin), "owner");
  assert.equal(audienceFor(hidden), "staff");
  assert.equal(audienceFor(master), "master");
});

test("support email: a template, never a password, nothing sent", () => {
  const href = supportMailto({ email: "support@kloudc.com", storeCode: "MAIN_STORE", page: "Reorder Candidates" });
  assert.ok(href.startsWith("mailto:support@kloudc.com?subject="));
  const body = decodeURIComponent(href.split("body=")[1]);
  assert.match(decodeURIComponent(href), /RetailOps support - MAIN_STORE/);
  assert.match(body, /Store ID: MAIN_STORE/);
  assert.match(body, /Never send your password/);
});

test("concept terms never repeat a status term's label", () => {
  const py = readFileSync(GLOSSARY_PY, "utf8");
  const statusLabels = new Set(
    [...py.matchAll(/_t\(\s*f?"[a-z0-9_]+",\s*f?"([^"]+)"/g)].map((m) => m[1].toLowerCase()),
  );
  for (const t of RAW.glossary) assert.ok(!statusLabels.has(t.label.toLowerCase()), `${t.key}: "${t.label}" is a status term already`);
});
