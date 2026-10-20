// KloudC RetailOps website - the "website ready" flows as screenshots, for the
// final design review: the store detail drawer's five sections, the Stores
// row's Archive confirm, Item vendors (Edit, Import CSV), Data & usage after
// Export all data, and Connect with Clover. Runs against a local preview
// (~/storeops/preview.sh) - fakes only, nothing outside is contacted.
//
//   SCREENS_BASE=http://127.0.0.1:8873 SCREENS_LOGINS=<logins.json> \
//   SCREENS_OUT=<dir> node frontend/e2e/screens/flows.mjs
//
// Optional: SCREENS_WIDTHS=1440,375 SCREENS_THEMES=light,dark. Exits 1 when a
// page shows sideways scroll on a phone, crashes, or throws.
import { chromium } from "playwright";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const BASE = process.env.SCREENS_BASE || "http://127.0.0.1:8870";
const OUT = process.env.SCREENS_OUT || "/tmp/retailops-flows";
const LOGINS = JSON.parse(readFileSync(process.env.SCREENS_LOGINS, "utf8"));
const WIDTHS = (process.env.SCREENS_WIDTHS || "1440,1280,375").split(",").map(Number);
const THEMES = (process.env.SCREENS_THEMES || "light,dark").split(",");

const results = { shots: 0, sideways: [], crashed: [], errors: [], missing: [] }; // crashed: also layout breaks
const who = (code) => ({ code, username: LOGINS[code][0].username, password: LOGINS[code][0].password });

async function context(browser, width, theme) {
  const ctx = await browser.newContext({
    viewport: { width, height: width < 640 ? 812 : 900 },
    colorScheme: theme,
    deviceScaleFactor: width < 640 ? 2 : 1,
  });
  await ctx.addInitScript((t) => {
    try {
      localStorage.setItem("kc.theme", t);
    } catch {
      // ignore
    }
  }, theme);
  return ctx;
}

async function signIn(page, w) {
  const r = await page.request.post(`${BASE}/api/auth/login`, {
    data: { store_code: w.code, username: w.username, password: w.password },
  });
  if (!r.ok()) throw new Error(`login ${w.code}: ${r.status()}`);
}

async function settle(page, ms = 400) {
  await page.waitForLoadState("networkidle", { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(ms);
}

async function shoot(page, dir, name) {
  mkdirSync(dir, { recursive: true });
  await page.screenshot({ path: join(dir, `${name}.png`) });
  results.shots++;
  const width = page.viewportSize().width;
  const sx = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  if (width < 640 && sx > 1) results.sideways.push(`${name} @${width}: ${sx}px`);
  const crashed = await page.getByText(/something went wrong|unexpected application error/i).first().isVisible().catch(() => false);
  if (crashed) results.crashed.push(`${name} @${width}`);
}

async function tryClick(page, selector, label) {
  const el = typeof selector === "string" ? page.locator(selector).first() : selector;
  if (!(await el.count())) {
    results.missing.push(label);
    return false;
  }
  await el.click();
  return true;
}

async function master(browser, width, theme, dir) {
  const ctx = await context(browser, width, theme);
  const page = await ctx.newPage();
  page.on("pageerror", (e) => results.errors.push(`master ${width} ${theme}: ${String(e).slice(0, 160)}`));
  await signIn(page, who("PLATFORM"));
  await page.goto(`${BASE}/#/platform`);
  await settle(page);
  await page.click('[data-testid="pf-tab-stores"]');
  await settle(page);
  if (await tryClick(page, '[data-testid="store-details-RETAILOPS_TEST_CLOVER"]', "store details")) {
    await settle(page);
    // A long Store ID must not push Close off a phone screen.
    const close = await page.getByRole("button", { name: "Close store detail" }).boundingBox();
    if (!close || close.x + close.width > width) results.crashed.push(`drawer Close off screen @${width}`);
    for (const [key, label] of [
      ["overview", "Overview"],
      ["usage", "Usage & plan"],
      ["settings", "Settings"],
      ["data", "Data"],
      ["audit", "Audit"],
    ]) {
      await page.locator('[data-testid="store-detail"]').getByRole("tab", { name: label, exact: true }).click();
      await settle(page);
      await shoot(page, dir, `console-detail-${key}`);
    }
    await page.keyboard.press("Escape");
    await settle(page, 200);
  }
  // Archive: the confirm on the row (nothing is archived).
  const row = page.locator('[data-testid="store-RETAILOPS_TEST_TOAST"]');
  if (await tryClick(page, row.getByRole("button", { name: "Archive" }), "archive button")) {
    await settle(page, 200);
    await shoot(page, dir, "console-archive-confirm");
  }
  await ctx.close();
}

async function store(browser, width, theme, dir) {
  const ctx = await context(browser, width, theme);
  const page = await ctx.newPage();
  page.on("pageerror", (e) => results.errors.push(`store ${width} ${theme}: ${String(e).slice(0, 160)}`));
  await signIn(page, who("DEMO"));
  // Item vendors (DEMO: made-up items): Edit one item, then Import CSV.
  await page.goto(`${BASE}/#/setup/item-vendors`);
  await settle(page);
  await shoot(page, dir, "item-vendors");
  if (await tryClick(page, '[data-testid^="iv-edit-"]', "item vendors edit")) {
    await settle(page, 300);
    await shoot(page, dir, "item-vendors-edit");
    await page.keyboard.press("Escape");
    await settle(page, 200);
  }
  if (await tryClick(page, '[data-testid="iv-import-open"]', "item vendors import")) {
    await settle(page, 300);
    await shoot(page, dir, "item-vendors-import");
  }
  await ctx.close();

  // Data & usage, then Export all data (a test store; the file stays local).
  const ctx1 = await context(browser, width, theme);
  const page1 = await ctx1.newPage();
  page1.on("pageerror", (e) => results.errors.push(`toast ${width} ${theme}: ${String(e).slice(0, 160)}`));
  await signIn(page1, who("RETAILOPS_TEST_TOAST"));
  await dataUsage(page1, width, theme, dir);
  await ctx1.close();
  await clover(browser, width, theme, dir);
}

async function dataUsage(page, width, theme, dir) {
  await page.goto(`${BASE}/#/settings/data`);
  await settle(page);
  await shoot(page, dir, "data-usage");
  if (width === WIDTHS[0] && theme === THEMES[0]) {
    if (await tryClick(page, '[data-testid="export-all"]', "export all")) {
      await settle(page, 2500);
      await shoot(page, dir, "data-usage-export");
    }
  } else {
    await shoot(page, dir, "data-usage-export");
  }
}

async function clover(browser, width, theme, dir) {
  const ctx2 = await context(browser, width, theme);
  const page2 = await ctx2.newPage();
  page2.on("pageerror", (e) => results.errors.push(`clover ${width} ${theme}: ${String(e).slice(0, 160)}`));
  await signIn(page2, who("RETAILOPS_TEST_CLOVER"));
  await page2.goto(`${BASE}/#/settings/integrations`);
  await settle(page2);
  await shoot(page2, dir, "clover-connect");
  await ctx2.close();
}

const browser = await chromium.launch();
try {
  for (const theme of THEMES) {
    for (const width of WIDTHS) {
      const dir = join(OUT, theme, String(width));
      await master(browser, width, theme, dir);
      await store(browser, width, theme, dir);
      console.log(`done ${theme} ${width} (${results.shots} shots)`);
    }
  }
} finally {
  await browser.close();
}
writeFileSync(join(OUT, "flows-result.json"), JSON.stringify(results, null, 2));
console.log(JSON.stringify(results, null, 2));
process.exit(results.sideways.length || results.crashed.length || results.errors.length ? 1 : 0);
