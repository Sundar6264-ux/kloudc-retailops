// KloudC RetailOps website screenshot set (final pass, docs/DESIGN_SYSTEM.md
// 5.4): every top-level page at 1440, 1280 and 375 px in light and dark,
// plus the logged-out pages, the master console tabs, and the shell's
// popovers (user menu, search, help, phone menu). Also checks each page for
// sideways scroll at 375 px, a page heading, and crashes.
//
//   SCREENS_BASE=http://127.0.0.1:8891 SCREENS_LOGINS=<logins.json> \
//   SCREENS_OUT=<dir> node frontend/e2e/screens/screens.mjs
//
// SCREENS_LOGINS is the local preview's logins file (~/storeops/.preview/
// logins.json from ~/storeops/preview.sh): the DEMO store's admin (made-up
// data), the fake Toast store's owner (Insights, Integrations) and the
// master. Optional: SCREENS_WIDTHS=1280,375 SCREENS_THEMES=light ONLY=<substring>.
// Playwright comes from frontend/e2e/node_modules (as for the other suites).
import { chromium } from "playwright";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const BASE = process.env.SCREENS_BASE || "http://127.0.0.1:8870";
const OUT = process.env.SCREENS_OUT || "/tmp/retailops-screens";
const LOGINS = JSON.parse(readFileSync(process.env.SCREENS_LOGINS, "utf8"));
const WIDTHS = (process.env.SCREENS_WIDTHS || "1440,1280,375").split(",").map(Number);
const THEMES = (process.env.SCREENS_THEMES || "light,dark").split(",");
const ONLY = process.env.ONLY || "";

const login = (code, i = 0) => ({ code, username: LOGINS[code][i].username, password: LOGINS[code][i].password });

// [name, hash] per account. Names are file names.
const DEMO_PAGES = [
  ["home", "/"],
  ["daily-checklists", "/daily-checklists"],
  ["reorder", "/reorder"],
  ["new-order", "/supplier-projection"],
  ["vendor-calendar", "/vendor-calendar"],
  ["cart", "/purchase-order"],
  ["placed-orders", "/placed-orders"],
  ["fix-issues", "/fix-issues"],
  ["delivery-review", "/delivery-review"],
  ["invoices", "/invoices"],
  ["inventory-reports", "/inventory-reports"],
  ["reconciliation", "/reconciliation"],
  ["items-sold", "/items-sold"],
  ["team-tasks", "/team/tasks"],
  ["team-chat", "/team/chat"],
  ["team-news", "/team/announcements"],
  ["users", "/users"],
  ["teams", "/teams"],
  ["po-sections", "/po-sections"],
  ["vendor-contacts", "/vendor-contacts"],
  ["issue-leads", "/issue-leads"],
  ["checklist-builder", "/checklists/admin"],
  ["recurring-tasks", "/team/templates"],
  ["reported-messages", "/team/moderation"],
  ["help", "/help"],
  ["glossary", "/glossary"],
  ["change-password", "/change-password"],
];
const TOAST_PAGES = [
  ["open-checks", "/open-orders"],
  ["insights-daily", "/insights/daily"],
  ["insights-prep", "/insights/prep-forecast"],
  ["insights-busy-hours", "/insights/busy-hours"],
  ["integrations", "/settings/integrations"],
  ["inventory-from-pos", "/inventory-api"],
  ["flyers", "/flyers"],
];

const results = { shots: 0, sideways: [], noHeading: [], crashed: [], errors: [] };

async function newContext(browser, width, theme) {
  const ctx = await browser.newContext({
    viewport: { width, height: width < 640 ? 812 : 900 },
    colorScheme: theme,
    deviceScaleFactor: width < 640 ? 2 : 1,
  });
  await ctx.addInitScript((t) => {
    try {
      localStorage.setItem("kc.theme", t);
      localStorage.setItem("kc.nav.collapsed", "0");
    } catch {
      // ignore
    }
  }, theme);
  return ctx;
}

async function signIn(page, who) {
  const r = await page.request.post(`${BASE}/api/auth/login`, {
    data: { store_code: who.code, username: who.username, password: who.password },
  });
  if (!r.ok()) throw new Error(`login ${who.code}/${who.username}: ${r.status()}`);
}

async function settle(page) {
  await page.waitForLoadState("networkidle", { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(500);
}

async function shoot(page, dir, name, { check = true } = {}) {
  if (ONLY && !name.includes(ONLY)) return;
  mkdirSync(dir, { recursive: true });
  await page.screenshot({ path: join(dir, `${name}.png`) });
  results.shots++;
  if (!check) return;
  const width = page.viewportSize().width;
  const sx = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  if (width < 640 && sx > 1) results.sideways.push(`${name} @${width}: ${sx}px`);
  const h = await page.locator("h1").first().textContent().catch(() => "");
  if (!(h || "").trim()) results.noHeading.push(`${name} @${width}`);
  const crashed = await page.getByText(/something went wrong|unexpected application error/i).first().isVisible().catch(() => false);
  if (crashed) results.crashed.push(`${name} @${width}`);
}

async function storePages(browser, width, theme, who, pages, extras) {
  const ctx = await newContext(browser, width, theme);
  const page = await ctx.newPage();
  page.on("pageerror", (e) => results.errors.push(`${who.code} ${width} ${theme}: ${String(e).slice(0, 160)}`));
  await signIn(page, who);
  const dir = join(OUT, theme, String(width));
  for (const [name, hash] of pages) {
    if (ONLY && !name.includes(ONLY)) continue;
    await page.goto(`${BASE}/#${hash}`);
    await settle(page);
    await shoot(page, dir, name);
  }
  if (extras) await extras(page, dir, width);
  await ctx.close();
}

// The shell's popovers on Home.
async function shellExtras(page, dir, width) {
  await page.goto(`${BASE}/#/`);
  await settle(page);
  await page.click('[data-testid="user-menu"]');
  await page.waitForTimeout(250);
  await shoot(page, dir, "shell-user-menu", { check: false });
  await page.keyboard.press("Escape");
  await page.click(".topbar-search");
  await page.waitForTimeout(150);
  await page.keyboard.type("vendor");
  await page.waitForTimeout(250);
  await shoot(page, dir, "shell-search", { check: false });
  await page.keyboard.press("Escape");
  if (width < 768) {
    // Phones: page help is in the user menu (the top bar keeps search, cart, avatar).
    await page.click('[data-testid="user-menu"]');
    await page.click('[data-testid="page-help"]');
  } else {
    await page.click(".topbar-help");
  }
  await page.waitForTimeout(300);
  await shoot(page, dir, "shell-help-panel", { check: false });
  await page.keyboard.press("Escape");
  if (width < 1024) {
    await page.click(".sidebar-toggle");
    await page.waitForTimeout(350);
    await shoot(page, dir, "shell-phone-menu", { check: false });
    await page.click(".sidebar-toggle");
  } else {
    await page.click(".sidebar-collapse-btn");
    await page.waitForTimeout(300);
    await shoot(page, dir, "shell-rail", { check: false });
    await page.click(".sidebar-collapse-btn");
  }
}

async function loggedOut(browser, width, theme) {
  const ctx = await newContext(browser, width, theme);
  const page = await ctx.newPage();
  const dir = join(OUT, theme, String(width));
  for (const [name, hash] of [
    ["login", "/"],
    ["login-store", "/?store=DEMO"],
    ["signup", "/signup"],
    ["owner-setup", "/setup"],
  ]) {
    if (ONLY && !name.includes(ONLY)) continue;
    await page.goto(`${BASE}/#${hash}`);
    await settle(page);
    if (name === "login-store") {
      await page.fill('[data-testid="login-store-id"]', "DEMO");
      await page.locator('[data-testid="login-store-id"]').blur();
      await page.waitForTimeout(600);
    }
    await shoot(page, dir, name);
  }
  await ctx.close();
}

async function master(browser, width, theme) {
  const ctx = await newContext(browser, width, theme);
  const page = await ctx.newPage();
  await signIn(page, login("PLATFORM"));
  const dir = join(OUT, theme, String(width));
  await page.goto(`${BASE}/#/platform`);
  await settle(page);
  for (const tab of ["requests", "stores", "hosting", "audit", "help"]) {
    if (ONLY && !`platform-${tab}`.includes(ONLY)) continue;
    await page.click(`[data-testid="pf-tab-${tab}"]`);
    await settle(page);
    await shoot(page, dir, `platform-${tab}`, { check: false });
  }
  await ctx.close();
}

const browser = await chromium.launch();
try {
  for (const theme of THEMES) {
    for (const width of WIDTHS) {
      await loggedOut(browser, width, theme);
      await storePages(browser, width, theme, login("DEMO"), DEMO_PAGES, shellExtras);
      await storePages(browser, width, theme, login("RETAILOPS_TEST_TOAST"), TOAST_PAGES);
      await master(browser, width, theme);
      console.log(`done ${theme} ${width} (${results.shots} shots)`);
    }
  }
} finally {
  await browser.close();
}
writeFileSync(join(OUT, "result.json"), JSON.stringify(results, null, 2));
console.log(JSON.stringify({ ...results, errors: results.errors.slice(0, 20) }, null, 2));
process.exit(results.sideways.length || results.crashed.length ? 1 : 0);
