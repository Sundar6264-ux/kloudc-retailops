// The System panel (track C) in the master console against the real server
// (system_server.py): the four tiles, failed jobs with Retry, workers, at
// 1440 and 375 px in light and dark. Checks: no sideways scroll, no element
// wider than the screen, 44 px tap targets on a phone, Retry queues the job
// again, nothing secret on screen. Exit code 1 on any failure.
import { mkdirSync } from "node:fs";
import { join } from "node:path";

const { chromium } = await import(process.env.E2E_PLAYWRIGHT || "playwright");

const BASE = `http://127.0.0.1:${process.env.MOCK_PORT || 8883}`;
const MASTER_PW = process.env.MOCK_MASTER_PASSWORD;
const SHOTS = process.env.SYSTEM_SHOTS;
mkdirSync(SHOTS, { recursive: true });

const failures = [];
let checks = 0;
function check(ok, msg) {
  checks++;
  if (!ok) failures.push(msg);
  return ok;
}

async function settle(page, ms = 250) {
  await page.waitForLoadState("networkidle", { timeout: 8000 }).catch(() => {});
  await page.waitForTimeout(ms);
}

async function openPanel(page, width, theme) {
  await page.goto(`${BASE}/`);
  await page.evaluate((t) => {
    localStorage.setItem("kc.theme", t);
    localStorage.removeItem("kc.platform.system.jobs");
  }, theme);
  await page.goto(`${BASE}/#/login`);
  await page.waitForSelector('[data-testid="login-store-id"]');
  await page.fill('[data-testid="login-store-id"]', "PLATFORM");
  await page.fill('input[name="username"]', "owner");
  await page.fill('input[type="password"]', MASTER_PW);
  await page.click('button[type="submit"]');
  await page.waitForURL(/#\/platform/, { timeout: 15000 });
  await settle(page);
  await page.getByRole("tab", { name: /Platform settings/ }).click();
  await page.waitForSelector('[data-testid="system-tile-storage"] .ss-tile-value:not(:empty)');
  await page.waitForSelector('[data-testid^="job-"]');
  await settle(page);
}

async function layoutChecks(page, tag, width) {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  check(over <= 1, `${tag}: page scrolls sideways by ${over}px`);
  const wide = await page.evaluate(() => {
    const out = [];
    for (const el of document.querySelectorAll('[data-testid="system-panel"] *')) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.right > window.innerWidth + 1) out.push(el.className || el.tagName);
    }
    return out.slice(0, 5);
  });
  check(wide.length === 0, `${tag}: elements past the screen edge: ${wide.join(", ")}`);
  if (width < 640) {
    const small = await page.evaluate(() => {
      const out = [];
      // The shared ⓘ buttons ("About ...") are sized by the help components
      // (track A's 44 px pass); every other button here is checked.
      for (const el of document.querySelectorAll('[data-testid="system-panel"] button:not([aria-label^="About"])')) {
        const r = el.getBoundingClientRect();
        if (r.width && (r.height < 43.5 || r.width < 43.5)) out.push(`${el.textContent.trim() || el.getAttribute("aria-label")} ${Math.round(r.width)}x${Math.round(r.height)}`);
      }
      return out;
    });
    check(small.length === 0, `${tag}: tap targets under 44 px: ${small.join("; ")}`);
  }
  const text = await page.textContent('[data-testid="system-panel"]');
  check(!/placeholder-secret|placeholder-key-id|postgresql/.test(text), `${tag}: a secret or URL is on screen`);
}

const browser = await chromium.launch();
try {
  for (const width of [1440, 375]) {
    for (const theme of ["light", "dark"]) {
      const tag = `system-${width}-${theme}`;
      const ctx = await browser.newContext({ viewport: { width, height: width < 640 ? 812 : 900 }, colorScheme: theme });
      const page = await ctx.newPage();
      const errors = [];
      page.on("pageerror", (e) => errors.push(String(e)));
      await openPanel(page, width, theme);
      const tiles = await page.$$eval(".ss-tile-label", (els) => els.map((e) => e.textContent.trim()));
      check(JSON.stringify(tiles) === JSON.stringify(["Storage", "Background jobs", "Workers", "Live updates"]), `${tag}: tiles ${tiles}`);
      const storage = await page.textContent('[data-testid="system-tile-storage"]');
      check(storage.includes("kloudc-store-files"), `${tag}: storage tile shows the bucket`);
      const jobsTile = await page.textContent('[data-testid="system-tile-jobs"]');
      check(/2 need attention/.test(jobsTile), `${tag}: jobs tile counts failed + retrying (${jobsTile})`);
      const workersTile = await page.textContent('[data-testid="system-tile-workers"]');
      check(/1 running/.test(workersTile) && /not responding/.test(workersTile), `${tag}: workers tile (${workersTile})`);
      const live = await page.textContent('[data-testid="system-tile-live"]');
      check(/Across processes/.test(live), `${tag}: live updates connected (${live})`);
      const rows = await page.$$('[data-testid^="job-"]');
      check(rows.length === 2, `${tag}: Needs attention lists 2 jobs (got ${rows.length})`);
      await layoutChecks(page, tag, width);
      await page.screenshot({ path: join(SHOTS, `${tag}.png`), fullPage: true });
      check(errors.length === 0, `${tag}: page errors ${errors.join(" | ")}`);
      await ctx.close();
    }
  }
  // Retry (last: it changes the queue the screens above show).
  {
    const tag = "system-1440-light";
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "light" });
    const page = await ctx.newPage();
    await openPanel(page, 1440, "light");
    // Retry the failed export: it is queued again and leaves the list.
    const before = await page.$$('[data-testid^="job-"]');
    await page.getByRole("button", { name: /Retry Data export/ }).click();
    await page.waitForSelector(".pf-banner.ok");
    const banner = await page.textContent(".pf-banner.ok");
    check(/Data export is queued again/.test(banner), `${tag}: retry banner (${banner})`);
    await settle(page);
    const after = await page.$$('[data-testid^="job-"]');
    check(after.length === before.length - 1, `${tag}: the retried job left Needs attention`);
    await page.getByRole("button", { name: "Waiting" }).click();
    await settle(page);
    const waiting = await page.textContent('[data-testid="system-panel"] .ss-list');
    check(/Data export/.test(waiting), `${tag}: the retried job is Waiting`);
    await page.screenshot({ path: join(SHOTS, `system-1440-light-after-retry.png`), fullPage: true });
    // The filter is remembered.
    await page.reload();
    await page.getByRole("tab", { name: /Platform settings/ }).click();
    await settle(page);
    const pressed = await page.getAttribute('.ss-filter[aria-pressed="true"]', "aria-pressed");
    const label = await page.textContent('.ss-filter[aria-pressed="true"]');
    check(pressed === "true" && label.trim() === "Waiting", `${tag}: filter remembered (${label})`);
    // ⓘ opens the System help.
    await page.locator('[data-testid="system-panel"] .ss-head button').first().click();
    await settle(page, 300);
    const help = await page.textContent("body");
    check(/Failed\*?\*? jobs stopped after their last try|jobs stopped after their last try/.test(help), `${tag}: ⓘ shows the System help`);
    await page.screenshot({ path: join(SHOTS, `system-1440-light-info.png`), fullPage: false });
    await ctx.close();
  }
} finally {
  await browser.close();
}
console.log(`${checks - failures.length}/${checks} checks passed`);
if (failures.length) {
  console.log(failures.map((f) => `FAIL ${f}`).join("\n"));
  process.exit(1);
}
