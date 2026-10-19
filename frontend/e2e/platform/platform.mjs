// Website platform checks (docs/PLATFORM_CONTRACT.md 14, 18.2 D) against
// mock_server.mjs, at 1280 and 375 pixels:
//   - one login page: Store ID + Username + Password, wrong password,
//     suspended store, store moved (sent to its own server), lockout, the
//     Store ID remembered;
//   - no Notifications link anywhere; /#/notifications goes to the dashboard;
//   - Settings -> Integrations: save (secret never shown again), Test
//     connection, Clover "Connect" placeholder;
//   - sign-up -> pending; the master approves on /#/platform; the pending
//     page shows approved; the new store's owner signs in;
//   - master console tabs (Hosting off state, Audit), no sideways scroll.
// Exit code 1 on any failure. Screenshots: $PLATFORM_E2E_WORK/shots.
import { chromium } from "playwright";
import { createHmac } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { hasOriginName } from "../../src/test/originNames.mjs";

const BASE = `http://127.0.0.1:${process.env.MOCK_PORT || 8871}`;
const WORK = process.env.PLATFORM_E2E_WORK;
const STORE_PW = process.env.MOCK_STORE_PASSWORD;
const MASTER_PW = process.env.MOCK_MASTER_PASSWORD;
const SHOTS = join(WORK, "shots");
mkdirSync(SHOTS, { recursive: true });
const WIDTHS = [1280, 375];
// The real server (run_real.sh) differs from the mock in two set-ups: its
// packs (no grocery_us) and dedicated hosting switched on (MOVED_MART needs it).
const APPROVE_PACK = process.env.E2E_APPROVE_PACK || "grocery_us";
const HOSTING_ON = process.env.E2E_HOSTING_ENABLED === "1";

const failures = [];
let checks = 0;
function check(ok, msg) {
  checks++;
  if (!ok) failures.push(msg);
}

async function settle(page, ms = 150) {
  await page.waitForLoadState("networkidle", { timeout: 8000 }).catch(() => {});
  await page.waitForTimeout(ms);
}

async function noSideways(page, tag) {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  check(over <= 1, `${tag}: page scrolls sideways by ${over}px`);
}

// The origin store's name never shows on a KloudC RetailOps page (M2 name audit):
// checked on every screen this suite photographs (text, title, alt text).
async function noOriginName(page, tag) {
  const text = await page
    .evaluate(() =>
      [document.title, document.body ? document.body.innerText : "",
       ...Array.from(document.querySelectorAll("[alt],[title],[placeholder],[aria-label]"), (e) =>
         [e.getAttribute("alt"), e.getAttribute("title"), e.getAttribute("placeholder"), e.getAttribute("aria-label")].join(" "))].join("\n"))
    .catch(() => "");
  check(!hasOriginName(text), `${tag}: page shows the origin store's name`);
}

async function shot(page, name) {
  await noOriginName(page, name);
  await page.screenshot({ path: join(SHOTS, `${name}.png`), fullPage: false });
}

async function login(page, code, user, pw) {
  await page.fill('[data-testid="login-store-id"]', code);
  await page.fill('input[name="username"]', user);
  await page.fill('input[type="password"]', pw);
  await page.click('button[type="submit"]');
  await settle(page);
}

async function newPage(browser, width) {
  const ctx = await browser.newContext({ viewport: { width, height: 860 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  return { ctx, page, errors };
}

async function openMenu(page, width) {
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
}

async function logout(page, width) {
  await page.goto(`${BASE}/#/`);
  await settle(page);
  if (width < 900) {
    const t = page.locator(".sidebar-toggle");
    if (await t.isVisible()) await t.click();
  }
  await page.click(".sidebar-logout");
  await page.waitForSelector('[data-testid="login-store-id"]');
}

async function run(browser, width) {
  const T = (s) => `${width}: ${s}`;

  // ---- login: errors, success, remembered Store ID ----
  {
    const { ctx, page, errors } = await newPage(browser, width);
    await page.goto(`${BASE}/#/login`);
    await page.waitForSelector('[data-testid="login-store-id"]');
    check((await page.inputValue('[data-testid="login-store-id"]')) === "", T("Store ID starts empty on a new browser"));
    check(await page.isVisible("text=Request access"), T("login links to sign-up"));
    await noSideways(page, T("login"));
    await shot(page, `login-${width}`);

    await login(page, "demo-grocery", "owner", "wrong-password");
    check((await page.textContent('[data-testid="login-error"]'))?.includes("Incorrect store ID, username or password"), T("wrong password message"));
    check((await page.inputValue('[data-testid="login-store-id"]')) === "DEMO_GROCERY", T("Store ID normalised on blur"));
    check((await page.textContent('[data-testid="login-store-name"]'))?.trim() === "Demo Grocery", T("store name from lookup"));

    await login(page, "PAUSED_MART", "owner", STORE_PW);
    check((await page.textContent('[data-testid="login-error"]'))?.includes("paused"), T("suspended store message"));
    await shot(page, `login-suspended-${width}`);

    await login(page, "NOPE_STORE", "owner", STORE_PW);
    check((await page.textContent('[data-testid="login-error"]'))?.includes("Incorrect store ID"), T("unknown store: same message"));

    // store moved: the browser goes to the store's own server
    await page.route("https://moved.retailops.invalid/**", (r) => r.fulfill({ status: 200, contentType: "text/html", body: "<p>moved</p>" }));
    await login(page, "MOVED_MART", "owner", STORE_PW);
    await page.waitForURL(/moved\.retailops\.invalid/, { timeout: 5000 }).catch(() => {});
    check(page.url() === "https://moved.retailops.invalid/#/login?store=MOVED_MART", T(`store_moved redirect (${page.url()})`));
    await page.goto(`${BASE}/#/login`);
    await page.waitForSelector('[data-testid="login-store-id"]');

    // sign in
    await login(page, "DEMO_GROCERY", "owner", STORE_PW);
    await page.waitForSelector('[data-testid="store-name"]', { timeout: 10000 }).catch(() => {});
    check(await page.isVisible("nav.sidebar"), T("signed in: the dashboard"));
    check((await page.evaluate(() => localStorage.getItem("retailops_last_store"))) === "DEMO_GROCERY", T("last Store ID remembered"));
    await openMenu(page, width);
    const nav = await page.innerText("nav.sidebar");
    check(!/Notifications/.test(nav), T("no Notifications link in the menu"));
    check(/Integrations/.test(nav), T("Integrations in Setup for an admin"));
    await shot(page, `dashboard-menu-${width}`);
    if (width < 900) await page.click(".sidebar-toggle"); // close the phone menu

    await page.goto(`${BASE}/#/notifications`);
    await settle(page);
    check(new URL(page.url()).hash === "#/", T(`/#/notifications redirects to the dashboard (${page.url()})`));

    // ---- integrations ----
    const seen = [];
    page.on("response", async (r) => {
      if (r.url().includes("/api/settings/integrations")) {
        try {
          seen.push(await r.text());
        } catch {
          // ignore
        }
      }
    });
    await page.goto(`${BASE}/#/settings/integrations`);
    await page.waitForSelector('[data-testid="int-tile-toast"]');
    await page.click('[data-testid="int-tile-toast"]');
    const card = page.locator('[data-testid="int-card-toast"]');
    if (!(await card.innerText()).includes("Not set up")) {
      // Saved by the run at the other width: Remove (confirmed) first.
      page.once("dialog", (d) => d.accept());
      await card.getByRole("button", { name: "Remove", exact: true }).click();
      await settle(page, 300);
      check((await page.textContent('[data-testid="int-message"]'))?.includes("removed"), T("Remove"));
    }
    check((await card.innerText()).includes("Not set up"), T("Toast starts not set up"));
    const SECRET = "toast-secret-value-7781";
    await card.getByRole("textbox", { name: "Client ID", exact: true }).fill("client-abc");
    await card.locator('[data-testid="secret-client_secret"]').fill(SECRET);
    await card.getByRole("textbox", { name: "Restaurant GUID", exact: true }).fill("guid-123");
    await card.locator('button[type="submit"]').click();
    await settle(page, 300);
    const card2 = page.locator('[data-testid="int-card-toast"]');
    const text2 = await card2.innerText();
    check(text2.includes("saved •••• 7781"), T("saved secret shows only its last 4"));
    check(text2.includes("Saved - not tested"), T("status after save"));
    check((await card2.locator('[data-testid="secret-client_secret"]').inputValue()) === "", T("secret box empty after save"));
    await card2.locator('[data-testid="int-test-toast"]').click();
    await settle(page, 300);
    check((await page.locator('[data-testid="int-card-toast"]').innerText()).includes("Connected"), T("Test connection -> Connected"));
    check(!(await page.content()).includes(SECRET), T("the secret never appears on the page"));
    check(!seen.some((t) => t.includes(SECRET)), T("no integrations response carries the secret"));
    await noSideways(page, T("integrations"));
    await shot(page, `integrations-toast-${width}`);
    check(!(await page.isVisible('[data-testid="int-tile-clover"]')), T("a Toast store has no Clover tile"));

    // A Clover store: its Connect placeholder, and never a Toast tile.
    await logout(page, width);
    await login(page, "CLOVER_MART", "owner", STORE_PW);
    await page.goto(`${BASE}/#/settings/integrations`);
    await page.waitForSelector('[data-testid="int-tile-clover"]');
    check(!(await page.isVisible('[data-testid="int-tile-toast"]')), T("a Clover store has no Toast tile"));
    check(!/Toast/.test(await page.innerText("main")), T("a Clover store's Integrations never says Toast"));
    await page.click('[data-testid="int-tile-clover"]');
    await page.click('[data-testid="clover-connect"]');
    await settle(page);
    check((await page.textContent('[data-testid="int-message"]'))?.includes("M3"), T("Clover Connect placeholder"));
    await shot(page, `integrations-clover-${width}`);

    // Remembered Store ID after sign-out
    await logout(page, width);
    await login(page, "DEMO_GROCERY", "owner", STORE_PW);
    await logout(page, width);
    check((await page.inputValue('[data-testid="login-store-id"]')) === "DEMO_GROCERY", T("Store ID prefilled after sign-out"));
    // Lockout after 5 failures for this user
    for (let i = 0; i < 6; i++) await login(page, "DEMO_GROCERY", "staff", "nope-nope");
    check((await page.textContent('[data-testid="login-error"]'))?.includes("Too many sign-in tries"), T("lockout message"));
    check(errors.length === 0, T(`page errors: ${errors.join(" | ")}`));
    await ctx.close();
  }

  // ---- sign-up -> pending -> master approves -> approved -> sign in ----
  const code = `NEW_STORE_${width}`;
  const owner = `pat${width}`;
  const ownerPw = `owner-pass-${width}-xyz`;
  const requester = await newPage(browser, width);
  {
    const { page } = requester;
    await page.goto(`${BASE}/#/signup`);
    await page.waitForSelector('[data-testid="signup-code"]');
    await page.fill('input[name="store_name"]', `New Store ${width}`);
    check((await page.inputValue('[data-testid="signup-code"]')) === `NEW_STORE_${width}`, T("Store ID suggested from the name"));
    await page.fill('[data-testid="signup-code"]', "demo_grocery");
    await page.waitForTimeout(700);
    check(await page.isVisible("text=That Store ID is taken."), T("taken Store ID shown live"));
    await page.fill('[data-testid="signup-code"]', code);
    await page.waitForTimeout(700);
    check(await page.isVisible("text=Available"), T("free Store ID shown as available"));
    await page.fill('input[name="contact_name"]', "Pat Example");
    await page.fill('input[name="contact_email"]', "pat@example.invalid");
    await page.selectOption('select[name="timezone"]', "America/Chicago");
    await page.fill('input[name="owner_username"]', owner);
    const pws = page.locator('input[type="password"]');
    await pws.nth(0).fill("short");
    await page.click('button[type="submit"]');
    check(await page.isVisible("text=At least 10 characters."), T("password rule shown"));
    await pws.nth(0).fill(ownerPw);
    await pws.nth(1).fill(ownerPw);
    await page.check('input[name="accept_terms"]');
    await noSideways(page, T("signup"));
    await shot(page, `signup-${width}`);
    await page.click('button[type="submit"]');
    await page.waitForSelector('[data-testid="signup-pending"]', { timeout: 8000 }).catch(() => {});
    check((await page.getAttribute('[data-testid="signup-pending"]', "data-state")) === "pending", T("pending page after sign-up"));
    await shot(page, `signup-pending-${width}`);
  }

  {
    const { ctx, page, errors } = await newPage(browser, width);
    await page.goto(`${BASE}/#/login`);
    await login(page, "platform", "owner", MASTER_PW);
    await page.waitForSelector('[data-testid="pf-tab-requests"]', { timeout: 8000 }).catch(() => {});
    check(new URL(page.url()).hash.startsWith("#/platform"), T(`master lands on the console (${page.url()})`));
    await page.click('[data-testid="pf-tab-requests"]');
    await settle(page);
    const row = page.locator(".pf-row", { hasText: code });
    check((await row.count()) === 1, T("the request is listed"));
    await row.locator('button:has-text("Approve")').first().click();
    await row.locator('select').first().selectOption(APPROVE_PACK);
    await shot(page, `platform-approve-${width}`);
    await row.locator('button:has-text("Approve and set up")').click();
    await settle(page, 300);
    check(await page.isVisible(`text=Approved - setting up ${code}.`), T("approve confirmation"));

    await page.click('[data-testid="pf-tab-stores"]');
    await settle(page);
    check(await page.isVisible(`[data-testid="store-${code}"]`), T("new store in Stores"));
    check((await page.locator(`[data-testid="store-PAUSED_MART"]`).innerText()).includes("Suspended"), T("status chip"));
    await page.locator(`[data-testid="store-PAUSED_MART"] button:has-text("Resume")`).click();
    await settle(page, 300);
    check((await page.locator(`[data-testid="store-PAUSED_MART"]`).innerText()).includes("Active"), T("resume"));
    await page.locator(`[data-testid="store-PAUSED_MART"] button:has-text("Suspend")`).click();
    await page.locator(`[data-testid="store-PAUSED_MART"] input`).fill("Paused again for the check");
    await page.locator(`[data-testid="store-PAUSED_MART"] button[type="submit"]`).click();
    await settle(page, 300);
    check((await page.locator(`[data-testid="store-PAUSED_MART"]`).innerText()).includes("Suspended"), T("suspend"));
    await noSideways(page, T("platform stores"));
    await shot(page, `platform-stores-${width}`);

    await page.click('[data-testid="pf-tab-hosting"]');
    await settle(page);
    if (HOSTING_ON) check(!(await page.isVisible('[data-testid="hosting-disabled"]')), T("Hosting tab: on"));
    else check(await page.isVisible('[data-testid="hosting-disabled"]'), T("Hosting tab: off state"));
    await page.click('[data-testid="pf-tab-audit"]');
    await settle(page);
    check(await page.isVisible("text=signup.approved"), T("audit lists the approval"));
    await shot(page, `platform-audit-${width}`);
    await page.click('button[aria-label="About this screen"]');
    check(await page.isVisible(".pf-info"), T("info icon opens the hint"));

    // a master never sees store pages
    await page.goto(`${BASE}/#/settings/integrations`);
    await settle(page);
    check(new URL(page.url()).hash === "#/platform", T("master is kept on the console"));
    // the console remembers the last tab
    await page.reload();
    await page.waitForSelector('[data-testid="pf-tab-audit"]');
    check((await page.getAttribute('[data-testid="pf-tab-audit"]', "aria-selected")) === "true", T("last tab remembered"));
    check(errors.length === 0, T(`console page errors: ${errors.join(" | ")}`));
    await ctx.close();
  }

  {
    const { ctx, page, errors } = requester;
    await page.click("text=Check now");
    await settle(page, 300);
    check((await page.getAttribute('[data-testid="signup-pending"]', "data-state")) === "approved", T("pending page shows approved"));
    await shot(page, `signup-approved-${width}`);
    await page.click('[data-testid="pending-sign-in"]');
    await page.waitForSelector('[data-testid="login-store-id"]');
    check((await page.inputValue('[data-testid="login-store-id"]')) === code, T("login prefilled with the new Store ID"));
    await login(page, code, owner, ownerPw);
    await page.waitForSelector("nav.sidebar", { timeout: 8000 }).catch(() => {});
    check(await page.isVisible("nav.sidebar"), T("the new store's owner signs in"));
    check(errors.length === 0, T(`requester page errors: ${errors.join(" | ")}`));
    await ctx.close();
  }
}

// ---- real server only (run_real.sh): owner set-up link and two-step sign-in ----
function totpNow(secretB32) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const ch of secretB32.replace(/[\s=]/g, "").toUpperCase()) bits += alphabet.indexOf(ch).toString(2).padStart(5, "0");
  const key = Buffer.from(bits.match(/.{8}/g).map((b) => parseInt(b, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 1000 / 30)));
  const h = createHmac("sha1", key).update(counter).digest();
  const o = h[h.length - 1] & 15;
  return String((h.readUInt32BE(o) & 0x7fffffff) % 1_000_000).padStart(6, "0");
}

async function realOnly(browser) {
  const T = (s) => `real: ${s}`;
  // A master makes a one-time set-up link for DEMO_GROCERY's owner (its
  // staff user is locked out by the lockout check above).
  const ml = await fetch(`${BASE}/api/auth/mobile-login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ store_code: "PLATFORM", username: "owner", password: MASTER_PW }),
  }).then((r) => r.json());
  const link = await fetch(`${BASE}/api/platform/stores/DEMO_GROCERY/owner-setup-link`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${ml.token}` },
    body: JSON.stringify({ username: "owner" }),
  }).then((r) => r.json());
  check(typeof link.setup_url === "string" && link.setup_url.includes("/#/setup?"), T(`set-up link made (${link.setup_url})`));
  {
    const { ctx, page, errors } = await newPage(browser, 375);
    await page.goto(link.setup_url.replace(/^https?:\/\/[^/]+/, BASE));
    await page.waitForSelector('[data-testid="owner-setup"]');
    check((await page.getAttribute('[data-testid="owner-setup"]', "data-state")) === "form", T("set-up page shows the form"));
    const pws = page.locator('input[type="password"]');
    const NEWPW = `owner-new-pass-${Date.now()}`;
    await pws.nth(0).fill(NEWPW);
    await pws.nth(1).fill(NEWPW);
    await noSideways(page, T("set-up page"));
    await shot(page, "owner-setup-375");
    await page.click('button[type="submit"]');
    await page.waitForSelector('[data-testid="setup-sign-in"]', { timeout: 8000 }).catch(() => {});
    check((await page.getAttribute('[data-testid="owner-setup"]', "data-state")) === "done", T("password set"));
    await page.click('[data-testid="setup-sign-in"]');
    await page.waitForSelector('[data-testid="login-store-id"]');
    check((await page.inputValue('[data-testid="login-store-id"]')) === "DEMO_GROCERY", T("login prefilled after set-up"));
    await login(page, "DEMO_GROCERY", "owner", NEWPW);
    await page.waitForSelector("nav.sidebar", { timeout: 8000 }).catch(() => {});
    check(await page.isVisible("nav.sidebar"), T("the owner signs in with the new password"));
    // The link works once.
    await page.goto(link.setup_url.replace(/^https?:\/\/[^/]+/, BASE));
    check(errors.length === 0, T(`set-up page errors: ${errors.join(" | ")}`));
    await ctx.close();
  }
  // Two-step sign-in: switch it on in the console, then sign in with a code.
  {
    const { ctx, page, errors } = await newPage(browser, 1280);
    await page.goto(`${BASE}/#/login`);
    await login(page, "platform", "owner", MASTER_PW);
    await page.waitForSelector('[data-testid="pf-totp-toggle"]', { timeout: 8000 });
    await page.click('[data-testid="pf-totp-toggle"]');
    await page.click('[data-testid="pf-totp"] button:has-text("Set up two-step sign-in")');
    await page.waitForSelector('[data-testid="pf-totp-secret"]');
    const secret = (await page.textContent('[data-testid="pf-totp-secret"]')) || "";
    await page.fill('[data-testid="pf-totp-code"]', totpNow(secret));
    await page.click('[data-testid="pf-totp"] button:has-text("Switch on")');
    await settle(page, 300);
    check((await page.textContent('[data-testid="pf-totp"]'))?.includes("Two-step sign-in is on"), T("two-step switched on"));
    await shot(page, "platform-totp-1280");
    await page.click('button:has-text("Log out")');
    await page.waitForSelector('[data-testid="login-store-id"]');
    await login(page, "PLATFORM", "owner", MASTER_PW);
    await page.waitForSelector('[data-testid="login-totp"]', { timeout: 8000 }).catch(() => {});
    check(await page.isVisible('[data-testid="login-totp"]'), T("login asks for the 6-digit code"));
    check((await page.textContent('[data-testid="login-error"]'))?.includes("6-digit code"), T("the code prompt says why"));
    await page.fill('[data-testid="login-totp"]', "000000");
    await page.click('button[type="submit"]');
    await settle(page);
    check((await page.textContent('[data-testid="login-error"]'))?.includes("Incorrect"), T("a wrong code is refused"));
    await page.fill('[data-testid="login-totp"]', totpNow(secret));
    await page.click('button[type="submit"]');
    await page.waitForSelector('[data-testid="pf-tab-requests"]', { timeout: 8000 }).catch(() => {});
    check(new URL(page.url()).hash.startsWith("#/platform"), T("master signs in with the code"));
    check(errors.length === 0, T(`console page errors: ${errors.join(" | ")}`));
    await ctx.close();
  }
}

const browser = await chromium.launch();
try {
  for (const w of WIDTHS) await run(browser, w);
  if (process.env.E2E_REAL === "1") await realOnly(browser);
} finally {
  await browser.close();
}
const result = { checks, failures };
writeFileSync(join(WORK, "result.json"), JSON.stringify(result, null, 2));
console.log(JSON.stringify({ checks, failed: failures.length }));
for (const f of failures) console.log(`FAIL ${f}`);
process.exit(failures.length ? 1 : 0);
