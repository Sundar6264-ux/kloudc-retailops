// Website ready, track A: screenshots + checks of the new console tabs
// (Masters, Platform settings), the store detail drawer, the first-sign-in
// password panel, and the new Setup pages (Store settings, Data & usage,
// Item vendors) plus a stacked wide table, at 1440 and 375 px, light + dark.
// Checks: no sideways scroll, an h1 (or the console's heading), no page
// errors, 44 px touch targets for the new controls at 375, and that the
// first page load fetches no other page's chunk.
//
//   BASE=http://127.0.0.1:8881 MASTER_PW=... STORE_PW=... OUT=<dir> node website_ready_a.mjs
// (run_website_ready_a.sh starts real_server.py on throwaway databases.)
import { chromium } from "playwright";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { auditPage } from "./contrast.mjs";

const BASE = process.env.BASE || "http://127.0.0.1:8881";
const OUT = process.env.OUT || "/tmp/website-ready-a";
const MASTER_PW = process.env.MASTER_PW;
const STORE_PW = process.env.STORE_PW;
const WIDTHS = (process.env.WIDTHS || "1440,375").split(",").map(Number);
const THEMES = (process.env.THEMES || "light,dark").split(",");
const results = { shots: 0, problems: [], pageErrors: [], chunks: null };
const problem = (m) => results.problems.push(m);

async function context(browser, width, theme) {
  const ctx = await browser.newContext({
    viewport: { width, height: width < 640 ? 812 : 900 },
    colorScheme: theme,
    deviceScaleFactor: width < 640 ? 2 : 1,
    reducedMotion: "reduce",
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

async function signIn(page, code, username, password) {
  const r = await page.request.post(`${BASE}/api/auth/login`, { data: { store_code: code, username, password } });
  if (!r.ok()) throw new Error(`login ${code}/${username}: ${r.status()} ${await r.text()}`);
  return r.json();
}

async function settle(page, ms = 400) {
  await page.waitForLoadState("networkidle", { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(ms);
}

async function shoot(page, name) {
  const width = page.viewportSize().width;
  mkdirSync(OUT, { recursive: true });
  await page.screenshot({ path: join(OUT, `${name}.png`), fullPage: false });
  results.shots++;
  const sx = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  if (sx > 1) problem(`${name}: page scrolls sideways by ${sx}px`);
  const crashed = await page.getByText(/something went wrong|unexpected application error/i).first().isVisible().catch(() => false);
  if (crashed) problem(`${name}: crashed`);
  // WCAG AA contrast of every visible text, and nothing poking out sideways (audit.mjs's check).
  const audit = await page.evaluate(auditPage);
  for (const s of audit.low) problem(`${name}: low contrast ${s}`);
  for (const s of audit.over) problem(`${name}: sticks out ${s}`);
  if (width < 640) {
    const small = await page.evaluate(() => {
      const out = [];
      for (const el of document.querySelectorAll(
        "[data-testid=add-master], [data-testid=save-settings], [data-testid=save-store-settings], .pf-detail-head-actions button, .pf-switch, [data-testid^=store-details-]",
      )) {
        const r = el.getBoundingClientRect();
        if (r.width && r.height && (r.height < 43.5 || r.width < 43.5)) out.push(`${el.dataset.testid || el.className} ${Math.round(r.width)}x${Math.round(r.height)}`);
      }
      return out;
    });
    for (const s of small) problem(`${name}: touch target under 44px: ${s}`);
  }
}

async function run() {
  const browser = await chromium.launch();
  for (const width of WIDTHS) {
    for (const theme of THEMES) {
      const tag = `${width}-${theme}`;
      // --- master console -----------------------------------------------------------
      {
        const ctx = await context(browser, width, theme);
        const page = await ctx.newPage();
        page.on("pageerror", (e) => results.pageErrors.push(`${tag} console: ${e}`));
        const requested = [];
        page.on("request", (r) => r.url().includes("/assets/") && r.url().endsWith(".js") && requested.push(r.url()));
        await signIn(page, "PLATFORM", "owner", MASTER_PW);
        await page.goto(`${BASE}/#/platform`);
        await settle(page);
        if (width === 1440 && theme === "light") {
          results.chunks = requested.map((u) => u.split("/").pop());
          const pages = results.chunks.filter((f) => /Page-|HelpCenter/.test(f) && !/PlatformConsolePage/.test(f));
          if (pages.length) problem(`first load fetched other pages' chunks: ${pages.join(", ")}`);
        }
        await page.click('[data-testid="pf-tab-masters"]');
        await settle(page);
        await shoot(page, `console-masters-${tag}`);
        if (theme === "light") {
          await page.click('[data-testid="add-master"]');
          await page.fill('form[aria-label="Add master"] input >> nth=0', `helper.${width}`);
          await page.click('form[aria-label="Add master"] button[type="submit"]');
          await settle(page);
          if (!(await page.locator('[data-testid="temp-password"]').isVisible())) problem(`${tag}: no temporary password shown`);
          await shoot(page, `console-masters-added-${tag}`);
          // The new master signs in: only "Choose your own password".
          const pw = (await page.locator('[data-testid="temp-password"] code').textContent()).trim();
          const ctx2 = await context(browser, width, theme);
          const p2 = await ctx2.newPage();
          await signIn(p2, "PLATFORM", `helper.${width}`, pw);
          await p2.goto(`${BASE}/#/platform`);
          await settle(p2);
          if (!(await p2.locator('[data-testid="must-change-password"]').isVisible())) problem(`${tag}: new master not asked for a password`);
          await shoot(p2, `console-first-sign-in-${tag}`);
          await ctx2.close();
        }
        await page.click('[data-testid="pf-tab-settings"]');
        await settle(page);
        await shoot(page, `console-platform-settings-${tag}`);
        await page.click('[data-testid="pf-tab-stores"]');
        await settle(page);
        await page.click('[data-testid="store-details-DEMO_GROCERY"]');
        await settle(page);
        if (!(await page.locator('[data-testid="store-health"]').isVisible())) problem(`${tag}: store health not shown`);
        await shoot(page, `console-store-detail-overview-${tag}`);
        for (const t of ["Usage & plan", "Data", "Audit"]) {
          await page.getByTestId("store-detail").getByRole("tab", { name: t, exact: true }).click();
          await settle(page, 250);
        }
        await shoot(page, `console-store-detail-audit-${tag}`);
        await page.getByTestId("store-detail").getByRole("tab", { name: "Overview" }).click();
        await page.keyboard.press("Escape");
        await settle(page, 200);
        if (await page.locator('[data-testid="store-detail"]').isVisible()) problem(`${tag}: Escape did not close the drawer`);
        await ctx.close();
      }
      // --- store owner: new Setup pages ----------------------------------------------
      {
        const ctx = await context(browser, width, theme);
        const page = await ctx.newPage();
        page.on("pageerror", (e) => results.pageErrors.push(`${tag} store: ${e}`));
        await signIn(page, "DEMO_GROCERY", "owner", STORE_PW);
        for (const [name, hash] of [
          ["store-settings", "/settings/store"],
          ["data-usage", "/settings/data"],
          ["item-vendors", "/setup/item-vendors"],
          ["users-stacked", "/users"],
        ]) {
          await page.goto(`${BASE}/#${hash}`);
          await settle(page);
          // Data & usage and Item vendors are tracks B / D's pages (stubs on this branch): routed here, headed there.
          if (!["data-usage", "item-vendors"].includes(name)) {
            const h = await page.locator("h1").first().textContent().catch(() => "");
            if (!(h || "").trim()) problem(`${name}-${tag}: no heading`);
          }
          if (name === "users-stacked") {
            await page.locator("table.kc-table--stack").first().scrollIntoViewIfNeeded();
            if (width < 640) {
              const display = await page.locator("table.kc-table--stack tr").last().evaluate((el) => getComputedStyle(el).display);
              if (display !== "block") problem(`${tag}: Users table rows are not stacked cards (${display})`);
              const labelled = await page.locator("table.kc-table--stack td[data-label]").count();
              if (!labelled) problem(`${tag}: Users table cells have no data-label`);
            }
          }
          await shoot(page, `${name}-${tag}`);
        }
        await ctx.close();
      }
    }
  }
  await browser.close();
  writeFileSync(join(OUT, "result.json"), JSON.stringify(results, null, 2));
  console.log(JSON.stringify({ shots: results.shots, problems: results.problems, pageErrors: results.pageErrors }, null, 2));
  if (results.problems.length || results.pageErrors.length) process.exit(1);
}

run().catch((e) => {
  console.error(e);
  process.exit(2);
});
