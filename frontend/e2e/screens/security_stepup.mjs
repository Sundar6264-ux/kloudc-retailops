// Console > Masters with two-step sign-in on (docs/SECURITY_REVIEW.md SR-07):
// adding or removing a master asks for the acting master's current code,
// and a code works once. Against the REAL server (../platform/real_server.py):
//   BASE=http://127.0.0.1:8893 MASTER_PW=... OUT=<dir> node security_stepup.mjs
import { createHmac } from "node:crypto";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright";

const BASE = process.env.BASE || "http://127.0.0.1:8893";
const MASTER_PW = process.env.MASTER_PW;
const OUT = process.env.OUT || "/tmp/security-stepup";
const problems = [];
let shots = 0;
const problem = (s) => problems.push(s);

function totpAt(secretB32, step) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const ch of secretB32.replace(/[\s=]/g, "").toUpperCase()) bits += alphabet.indexOf(ch).toString(2).padStart(5, "0");
  const key = Buffer.from(bits.match(/.{8}/g).map((b) => parseInt(b, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(step));
  const h = createHmac("sha1", key).update(counter).digest();
  const o = h[h.length - 1] & 15;
  return String((h.readUInt32BE(o) & 0x7fffffff) % 1_000_000).padStart(6, "0");
}
const stepNow = () => Math.floor(Date.now() / 1000 / 30);
const used = new Set();
// A code for a step not used yet (each step works once per purpose).
async function freshCode(secret, purpose) {
  for (;;) {
    const s = stepNow();
    if (!used.has(`${purpose}:${s}`)) {
      used.add(`${purpose}:${s}`);
      return totpAt(secret, s);
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
}

async function shoot(page, name) {
  mkdirSync(OUT, { recursive: true });
  await page.screenshot({ path: join(OUT, `${name}.png`), fullPage: false });
  shots++;
  const sx = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  if (sx > 1) problem(`${name}: page scrolls sideways by ${sx}px`);
}

async function settle(page, ms = 400) {
  await page.waitForLoadState("networkidle", { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(ms);
}

const browser = await chromium.launch();
let secret = null;
try {
  for (const [width, theme] of [
    [1440, "light"],
    [375, "dark"],
  ]) {
    const tag = `${width}-${theme}`;
    const ctx = await browser.newContext({ viewport: { width, height: width === 375 ? 812 : 900 }, colorScheme: theme, reducedMotion: "reduce" });
    await ctx.addInitScript((t) => {
      try {
        localStorage.setItem("kc.theme", t);
      } catch {
        // ignore
      }
    }, theme);
    const page = await ctx.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    const login = { store_code: "PLATFORM", username: "owner", password: MASTER_PW };
    if (secret) login.totp = await freshCode(secret, "login");
    const r = await page.request.post(`${BASE}/api/auth/login`, { data: login });
    if (!r.ok()) throw new Error(`login: ${r.status()} ${await r.text()}`);
    if (!secret) {
      // The two-step set-up through the API with a bearer token (the page keeps its cookie session).
      const mob = await (await page.request.post(`${BASE}/api/auth/mobile-login`, { data: login })).json();
      const auth = { Authorization: `Bearer ${mob.token}` };
      const res = await page.request.post(`${BASE}/api/platform/users/me/totp/setup`, { data: {}, headers: auth });
      const setup = await res.json();
      if (!setup.secret) throw new Error(`two-step set-up: ${res.status()} ${JSON.stringify(setup)}`);
      secret = setup.secret;
      const en = await page.request.post(`${BASE}/api/platform/users/me/totp/enable`, {
        data: { pending: setup.pending, code: totpAt(secret, stepNow()) },
        headers: auth,
      });
      if (!en.ok()) throw new Error(`enable two-step: ${en.status()} ${await en.text()}`);
    }
    await page.goto(`${BASE}/#/platform`);
    await settle(page);
    await page.click('[data-testid="pf-tab-masters"]');
    await settle(page);
    await page.click('[data-testid="add-master"]');
    const form = page.locator('form[aria-label="Add master"]');
    await form.locator("input >> nth=0").fill(`helper.${width}`);
    if (!(await form.locator('[data-testid="stepup-code"]').isVisible())) problem(`${tag}: no two-step code field when adding`);
    await form.locator('button[type="submit"]').click();
    await settle(page, 200);
    if (!(await form.getByRole("alert").isVisible())) problem(`${tag}: adding without a code was not refused`);
    await shoot(page, `stepup-add-needs-code-${tag}`);
    const code = await freshCode(secret, "step_up");
    await form.locator('[data-testid="stepup-code"]').fill(code);
    await form.locator('button[type="submit"]').click();
    await settle(page);
    if (!(await page.locator('[data-testid="temp-password"]').isVisible())) problem(`${tag}: master not added with a good code`);
    await shoot(page, `stepup-added-${tag}`);
    await page.getByRole("button", { name: "Done" }).click();
    await settle(page);
    // Remove with the SAME code: refused (a code works once).
    const row = page.locator(`[data-testid="master-helper.${width}"]`);
    await row.getByRole("button", { name: "Remove" }).click();
    await row.locator('[data-testid="stepup-code"]').fill(code);
    await row.locator(".pf-confirm").getByRole("button", { name: "Remove" }).click();
    await settle(page);
    const alert = row.getByRole("alert");
    if (!(await alert.isVisible()) || !/not right/i.test(await alert.textContent())) problem(`${tag}: a used code was accepted again`);
    await shoot(page, `stepup-remove-replay-refused-${tag}`);
    await row.locator('[data-testid="stepup-code"]').fill(await freshCode(secret, "step_up"));
    await row.locator(".pf-confirm").getByRole("button", { name: "Remove" }).click();
    await settle(page);
    if (await page.locator(`[data-testid="master-helper.${width}"]`).count()) problem(`${tag}: master not removed with a fresh code`);
    if (errors.length) problem(`${tag}: page errors: ${errors.join(" | ")}`);
    await ctx.close();
  }
} finally {
  await browser.close();
}
console.log(JSON.stringify({ shots, problems }, null, 1));
process.exit(problems.length ? 1 : 0);
