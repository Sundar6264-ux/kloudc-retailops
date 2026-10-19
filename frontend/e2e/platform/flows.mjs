// End-to-end platform flows in the browser against the REAL server
// (flows_server.py: multi mode, PostgreSQL, schedulers on, bootstrapped with
// the CLI, every outside service faked), at 1280 and 375 pixels, two stores
// at once:
//   sign-up (two stores) -> the master approves both on /#/platform -> each
//   pending page shows approved -> each owner signs in on the one login page
//   -> Settings > Integrations: Toast keys saved + Test connection -> that
//   store's catch-up sync runs with ITS keys (fake Toast log) -> the owner
//   adds a staff login on Users -> staff signs in (no Integrations, no
//   Users, no Notifications) -> the master suspends the store -> staff and
//   owner are refused, the other store's owner carries on -> resume.
// Exit code 1 on any failure. Screenshots: $PLATFORM_E2E_WORK/shots.
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

// E2E_PLAYWRIGHT: the playwright module to use when this tree has no node_modules.
const { chromium } = await import(process.env.E2E_PLAYWRIGHT || "playwright");

const BASE = `http://127.0.0.1:${process.env.MOCK_PORT || 8873}`;
const WORK = process.env.PLATFORM_E2E_WORK;
const MASTER_PW = process.env.MOCK_MASTER_PASSWORD;
const CALLS = join(WORK, "toast_calls.jsonl");
const SHOTS = join(WORK, "shots");
mkdirSync(SHOTS, { recursive: true });
const WIDTHS = [1280, 375];

const failures = [];
let checks = 0;
function check(ok, msg) {
  checks++;
  if (!ok) failures.push(msg);
  return ok;
}

// The scheduler spreads stores over 0-120 s (app/tenancy/jobs.py
// store_jitter_seconds); pick Store IDs that run early so the check is quick.
function jitter(code) {
  const d = createHash("sha256").update(code).digest();
  return d.readUInt32BE(0) % 121;
}
function quickCode(base) {
  for (const s of "ABCDEFGHJKLMNPQRSTUVWXYZ23456789") {
    for (const t of "ABCDEFGHJKLMNPQRSTUVWXYZ23456789") {
      const code = `${base}_${s}${t}`;
      if (jitter(code) <= 6) return code;
    }
  }
  return `${base}_ZZ`;
}

async function settle(page, ms = 150) {
  await page.waitForLoadState("networkidle", { timeout: 8000 }).catch(() => {});
  await page.waitForTimeout(ms);
}

async function noSideways(page, tag) {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  check(over <= 1, `${tag}: page scrolls sideways by ${over}px`);
}

async function shot(page, name) {
  await page.screenshot({ path: join(SHOTS, `${name}.png`), fullPage: false });
}

async function newPage(browser, width) {
  const ctx = await browser.newContext({ viewport: { width, height: 860 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  return { ctx, page, errors };
}

async function login(page, code, user, pw) {
  await page.fill('[data-testid="login-store-id"]', code);
  await page.fill('input[name="username"]', user);
  await page.fill('input[type="password"]', pw);
  await page.click('button[type="submit"]');
  await settle(page);
}

async function menuText(page, width) {
  if (width < 900) {
    const t = page.locator(".sidebar-toggle");
    if (await t.isVisible()) await t.click();
  }
  for (let pass = 0; pass < 3; pass++) {
    const closed = page.locator('nav.sidebar button.sidebar-group-toggle[aria-expanded="false"]');
    const n = await closed.count();
    if (!n) break;
    for (let i = n - 1; i >= 0; i--) await closed.nth(i).click();
  }
  const text = await page.innerText("nav.sidebar");
  if (width < 900) {
    const t = page.locator(".sidebar-toggle");
    if (await t.isVisible()) await t.click();
  }
  return text;
}

function toastCalls() {
  try {
    return readFileSync(CALLS, "utf8")
      .split("\n")
      .filter(Boolean)
      .map((l) => JSON.parse(l));
  } catch {
    return [];
  }
}

async function waitFor(fn, ms, every = 1000) {
  const end = Date.now() + ms;
  for (;;) {
    const v = await fn();
    if (v || Date.now() > end) return v;
    await new Promise((r) => setTimeout(r, every));
  }
}

async function signup(browser, width, code, owner, pw) {
  const T = (s) => `${width} ${code}: ${s}`;
  const p = await newPage(browser, width);
  const { page } = p;
  await page.goto(`${BASE}/#/signup`);
  await page.waitForSelector('[data-testid="signup-code"]');
  await page.fill('input[name="store_name"]', `Flow ${code}`);
  await page.fill('[data-testid="signup-code"]', code);
  await page.waitForTimeout(600);
  check(await page.isVisible("text=Available"), T("Store ID available"));
  await page.fill('input[name="contact_name"]', "Flow Owner");
  await page.fill('input[name="contact_email"]', `${owner}@example.invalid`);
  await page.selectOption('select[name="timezone"]', "America/Chicago");
  await page.fill('input[name="owner_username"]', owner);
  const pws = page.locator('input[type="password"]');
  await pws.nth(0).fill(pw);
  await pws.nth(1).fill(pw);
  await page.check('input[name="accept_terms"]');
  await page.click('button[type="submit"]');
  await page.waitForSelector('[data-testid="signup-pending"]', { timeout: 8000 }).catch(() => {});
  check((await page.getAttribute('[data-testid="signup-pending"]', "data-state")) === "pending", T("pending after sign-up"));
  return p;
}

async function saveToast(page, code, T) {
  await page.goto(`${BASE}/#/settings/integrations`);
  await page.waitForSelector('[data-testid="int-tile-toast"]', { timeout: 10000 });
  await page.click('[data-testid="int-tile-toast"]');
  const card = page.locator('[data-testid="int-card-toast"]');
  const secret = `secret-${code}-${Date.now()}`;
  await card.getByRole("textbox", { name: "Client ID", exact: true }).fill(`cid-${code}`);
  await card.locator('[data-testid="secret-client_secret"]').fill(secret);
  await card.getByRole("textbox", { name: "Restaurant GUID", exact: true }).fill(`guid-${code}`);
  await card.locator('button[type="submit"]').click();
  await settle(page, 300);
  check((await card.innerText()).includes(`saved •••• ${secret.slice(-4)}`), T("Toast keys saved (last 4 only)"));
  await card.locator('[data-testid="int-test-toast"]').click();
  await settle(page, 300);
  check((await page.locator('[data-testid="int-card-toast"]').innerText()).includes("Connected"), T("Test connection -> Connected"));
  check(!(await page.content()).includes(secret), T("secret never on the page"));
}

async function run(browser, width) {
  const codeA = quickCode(`FA${width}`);
  const codeB = quickCode(`FB${width}`);
  const ownerA = `ann${width}`;
  const ownerB = `bob${width}`;
  const pwA = `alpha-pass-${width}-xyz`;
  const pwB = `bravo-pass-${width}-xyz`;
  const T = (s) => `${width}: ${s}`;

  // ---- two sign-ups ----
  const reqA = await signup(browser, width, codeA, ownerA, pwA);
  const reqB = await signup(browser, width, codeB, ownerB, pwB);

  // ---- the master approves both ----
  const master = await newPage(browser, width);
  {
    const { page } = master;
    await page.goto(`${BASE}/#/login`);
    await login(page, "platform", "owner", MASTER_PW);
    await page.waitForSelector('[data-testid="pf-tab-requests"]', { timeout: 8000 }).catch(() => {});
    check(new URL(page.url()).hash.startsWith("#/platform"), T("master on the console"));
    await page.click('[data-testid="pf-tab-requests"]');
    await settle(page);
    for (const code of [codeA, codeB]) {
      const row = page.locator(".pf-row", { hasText: code });
      check((await row.count()) === 1, T(`request ${code} listed`));
      await row.locator('button:has-text("Approve")').first().click();
      await row.locator("select").first().selectOption("generic");
      await row.locator('button:has-text("Approve and set up")').click();
      await settle(page, 300);
      check(await page.isVisible(`text=Approved - setting up ${code}.`), T(`approve ${code}`));
    }
    await shot(page, `flows-approved-${width}`);
  }

  // ---- each pending page turns approved; each owner signs in ----
  const owners = {};
  for (const [req, code, owner, pw] of [
    [reqA, codeA, ownerA, pwA],
    [reqB, codeB, ownerB, pwB],
  ]) {
    const { page } = req;
    const approved = await waitFor(async () => {
      await page.click("text=Check now").catch(() => {});
      await settle(page, 300);
      return (await page.getAttribute('[data-testid="signup-pending"]', "data-state")) === "approved";
    }, 90000, 2000);
    check(approved, T(`${code}: pending page shows approved`));
    // Provisioning finishes after "approved": wait for the store to answer.
    await waitFor(async () => (await fetch(`${BASE}/api/public/stores/${code}`)).status === 200, 90000, 1000);
    await page.click('[data-testid="pending-sign-in"]');
    await page.waitForSelector('[data-testid="login-store-id"]');
    check((await page.inputValue('[data-testid="login-store-id"]')) === code, T(`${code}: login prefilled`));
    await login(page, code.toLowerCase(), owner, pw);
    await page.waitForSelector("nav.sidebar", { timeout: 10000 }).catch(() => {});
    check(await page.isVisible("nav.sidebar"), T(`${code}: owner signed in`));
    owners[code] = req;
  }

  // ---- Integrations, then that store's sync with its own keys ----
  for (const code of [codeA, codeB]) {
    const { page } = owners[code];
    await saveToast(page, code, (s) => T(`${code}: ${s}`));
    await noSideways(page, T(`${code}: integrations`));
  }
  await shot(owners[codeA].page, `flows-integrations-${width}`);
  for (const code of [codeA, codeB]) {
    const synced = await waitFor(
      () => toastCalls().some((c) => c.kind === "get" && c.guid === `guid-${code}` && c.path.endsWith("/ordersBulk")),
      60000,
    );
    check(synced, T(`${code}: catch-up sync reached (fake) Toast after the keys were saved`));
    const mine = toastCalls().filter((c) => c.kind === "get" && c.guid === `guid-${code}`);
    check(mine.length > 0 && mine.every((c) => c.auth === `Bearer tok-cid-${code}`), T(`${code}: every call used its own keys`));
    const status = await waitFor(async () => {
      const s = await owners[code].page.evaluate(async () => (await fetch("/api/jobs/status", { credentials: "include" })).json());
      const sync = Array.isArray(s) ? s.find((j) => j.job_name === "toast_sales_sync") : null;
      return sync && sync.status === "success" ? sync : null;
    }, 30000);
    check(!!status, T(`${code}: sales sync job status success in the store`));
  }

  // ---- the owner adds a staff login; staff signs in ----
  const staffUser = `sam${width}`;
  const staffPw = `staff-pass-${width}`;
  {
    const { page } = owners[codeA];
    await page.goto(`${BASE}/#/users`);
    await page.waitForSelector("text=Add a user");
    const form = page.locator("section.card", { hasText: "Add a user" });
    await form.locator('input[type="text"]').first().fill(staffUser);
    await form.locator('input[type="password"]').first().fill(staffPw);
    await form.locator('button[type="submit"]').click();
    await settle(page, 300);
    check(await page.isVisible(`text=User "${staffUser}" created.`), T("staff login created"));
    await noSideways(page, T("users"));
  }
  const staff = await newPage(browser, width);
  {
    const { page } = staff;
    await page.goto(`${BASE}/#/login`);
    await login(page, codeA, staffUser, staffPw);
    await page.waitForSelector("nav.sidebar", { timeout: 10000 }).catch(() => {});
    check(await page.isVisible("nav.sidebar"), T("staff signed in"));
    const nav = await menuText(page, width);
    check(!/Integrations/.test(nav) && !/\bUsers\b/.test(nav), T("staff menu has no Integrations / Users"));
    check(!/Notifications/.test(nav), T("no Notifications link"));
    await page.goto(`${BASE}/#/notifications`);
    await settle(page);
    // "/" is the dashboard; a staff login with no tabs granted lands on "no access" from there.
    check(["#/", "#/no-access"].includes(new URL(page.url()).hash), T(`staff: /#/notifications -> dashboard (${page.url()})`));
    {
      const o = owners[codeA].page;
      await o.goto(`${BASE}/#/notifications`);
      await settle(o);
      check(new URL(o.url()).hash === "#/", T(`owner: /#/notifications -> dashboard (${o.url()})`));
    }
    await page.goto(`${BASE}/#/settings/integrations`);
    await settle(page);
    check(!(await page.isVisible('[data-testid="int-tile-toast"]')), T("staff can't open Integrations"));
    // The same staff login does not exist in the other store.
    await page.goto(`${BASE}/#/`);
    await settle(page);
    await shot(page, `flows-staff-${width}`);
  }

  // ---- the master suspends store A ----
  {
    const { page } = master;
    await page.click('[data-testid="pf-tab-stores"]');
    await settle(page);
    const row = page.locator(`[data-testid="store-${codeA}"]`);
    await row.locator('button:has-text("Suspend")').click();
    await row.locator("input").fill("Flow check: paused");
    await row.locator('button[type="submit"]').click();
    await settle(page, 300);
    check((await row.innerText()).includes("Suspended"), T("store A suspended"));
  }
  {
    // The owner, still on a page (no reload), opens another page: told why.
    const o = owners[codeA].page;
    await o.goto(`${BASE}/#/users`);
    await settle(o, 500);
    const body = await o.innerText("body");
    check(/suspended|paused/i.test(body) || (await o.isVisible('[data-testid="login-store-id"]')),
      T(`suspended mid-session: the page says so (${body.slice(0, 160).replace(/\s+/g, " ")})`));
    await shot(o, `flows-suspended-midsession-${width}`);
  }
  {
    // Staff (signed in) reloads: no store pages any more; signing in is refused.
    const { page } = staff;
    await page.reload();
    await settle(page, 300);
    const onLogin = await page.isVisible('[data-testid="login-store-id"]');
    check(onLogin, T(`suspended: staff sent to the login page (${page.url()})`));
    if (onLogin) {
      await login(page, codeA, staffUser, staffPw);
      check((await page.textContent('[data-testid="login-error"]'))?.includes("paused"), T("suspended: staff login refused"));
      await shot(page, `flows-suspended-${width}`);
    }
    // The owner (signed in) too.
    const o = owners[codeA].page;
    await o.reload();
    await settle(o, 300);
    check(await o.isVisible('[data-testid="login-store-id"]'), T("suspended: owner sent to the login page"));
    // The other store carries on.
    const b = owners[codeB].page;
    await b.reload();
    await settle(b, 300);
    check(await b.isVisible("nav.sidebar"), T("store B still signed in"));
    const meB = await b.evaluate(async () => (await fetch("/api/auth/me", { credentials: "include" })).status);
    check(meB === 200, T("store B /me 200"));
  }
  {
    const { page } = master;
    const row = page.locator(`[data-testid="store-${codeA}"]`);
    await row.locator('button:has-text("Resume")').click();
    await settle(page, 300);
    check((await row.innerText()).includes("Active"), T("store A resumed"));
    const s = staff.page;
    await login(s, codeA, staffUser, staffPw);
    await s.waitForSelector("nav.sidebar", { timeout: 10000 }).catch(() => {});
    check(await s.isVisible("nav.sidebar"), T("after resume staff signs in again"));
  }
  // ---- one browser, two tabs, two stores (one session cookie per browser) ----
  {
    const ctx = await browser.newContext({ viewport: { width, height: 860 } });
    const tab1 = await ctx.newPage();
    const errs = [];
    tab1.on("pageerror", (e) => errs.push(String(e)));
    await tab1.goto(`${BASE}/#/login`);
    await login(tab1, codeA, ownerA, pwA);
    await tab1.waitForSelector("nav.sidebar", { timeout: 10000 }).catch(() => {});
    const tab2 = await ctx.newPage();
    await tab2.goto(`${BASE}/#/login`);
    await tab2.waitForSelector('[data-testid="login-store-id"]', { timeout: 10000 }).catch(() => {});
    if (await tab2.isVisible('[data-testid="login-store-id"]')) {
      await login(tab2, codeB, ownerB, pwB);
    } else {
      // Already signed in (shared cookie): sign out first, as a person would.
      await tab2.waitForSelector("nav.sidebar", { timeout: 10000 }).catch(() => {});
      if (width < 900) {
        const t = tab2.locator(".sidebar-toggle");
        if (await t.isVisible()) await t.click();
      }
      await tab2.click(".sidebar-logout");
      await tab2.waitForSelector('[data-testid="login-store-id"]');
      await login(tab2, codeB, ownerB, pwB);
    }
    await tab2.waitForSelector("nav.sidebar", { timeout: 10000 }).catch(() => {});
    // Tab 1 still shows store A; its next request must not act on store B as A.
    await tab1.goto(`${BASE}/#/users`);
    await settle(tab1, 1500);
    const meCode = await tab1.evaluate(async () => (await (await fetch("/api/auth/me", { credentials: "include" })).json()).store_code);
    const shown = (await tab1.innerText("body")).includes(ownerA);
    check(meCode === codeB && !shown, T(`two tabs: tab 1 no longer shows store A's users (cookie store ${meCode})`));
    check(errs.length === 0, T(`two tabs: page errors ${errs.join(" | ")}`));
    await ctx.close();
  }

  for (const p of [reqA, reqB, master, staff]) {
    check(p.errors.length === 0, T(`page errors: ${p.errors.join(" | ")}`));
    await p.ctx.close();
  }
}

const browser = await chromium.launch();
try {
  for (const w of WIDTHS) await run(browser, w);
} finally {
  await browser.close();
}
const calls = toastCalls();
const result = { checks, failures, toast_calls: calls.length };
writeFileSync(join(WORK, "result.json"), JSON.stringify(result, null, 2));
console.log(JSON.stringify({ checks, failed: failures.length, toast_calls: calls.length }));
for (const f of failures) console.log(`FAIL ${f}`);
process.exit(failures.length ? 1 : 0);
