// Suite B shared helpers: logins from users.json, settle/no-h-scroll checks, a copy-only sqlite helper.
import { chromium } from "../../node_modules/playwright/index.mjs";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
export { chromium };
export const W = process.env.SUITE_B_WORK || "/tmp/retailops-suiteB";
export const BASE = `http://127.0.0.1:${process.env.SUITE_B_PORT || 8831}`;
export const DB = `${W}/prodcopy.db`;
export const SHOTS = `${W}/shots`;
fs.mkdirSync(SHOTS, { recursive: true });
export const USERS = JSON.parse(fs.readFileSync(`${W}/users.json`, "utf8"));
export const ROLES = ["sb_admin", "sb_staff", "sb_hide", "sb_lead", "demo"];
export const WIDTHS = [1280, 1024, 375];
export const failures = [];
export const notes = [];
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export function check(cond, msg) {
  console.log(cond ? "  ok:" : "  FAIL:", msg);
  if (!cond) failures.push(msg);
  return cond;
}
export function note(msg) { console.log("  note:", msg); notes.push(msg); }
export function sql(q) {
  if (!DB.startsWith(W) || (process.env.ORIGIN_LIVE_DIR && DB.startsWith(process.env.ORIGIN_LIVE_DIR))) throw new Error("not the copy");
  return execFileSync("sqlite3", ["-separator", "|", DB, q], { encoding: "utf8" }).trim();
}
// Live Toast calls fail in the sandbox (Toast is blank on purpose).
export const ignorable = (s) => /items-sold|open-orders|orders\/open|today-sales|sales-accuracy|\/live/.test(s);
export async function as(browser, user, width, opts = {}) {
  const ctx = await browser.newContext({ viewport: { width, height: width < 600 ? 812 : 900 }, ...opts });
  if (user) await ctx.addCookies([{ name: "session_token", value: USERS[user].token, url: BASE }]);
  const page = await ctx.newPage();
  page.errors = [];
  page.on("pageerror", (e) => page.errors.push("pageerror " + String(e)));
  page.on("console", (m) => m.type() === "error" && page.errors.push("console " + m.text()));
  page.bad = [];
  page.on("response", (r) => {
    if (r.url().includes("/api/") && r.status() >= 400) page.bad.push(`${r.status()} ${r.request().method()} ${r.url().replace(BASE, "")}`);
  });
  page.on("dialog", (d) => d.accept());
  return { ctx, page };
}
// page.goto to a new #/route is same-document: wait for the page's own content, not networkidle.
export async function settle(page, ms = 60000) {
  await sleep(500);
  try { await page.waitForLoadState("networkidle", { timeout: 15000 }); } catch { /* polling pages */ }
  try {
    await page.waitForFunction(() => {
      const els = [...document.querySelectorAll("body *")].filter((e) => e.children.length === 0);
      return !els.some((e) => /^\s*(Loading|Calculating|Working)\b[^\n]{0,80}?(…|\.\.\.)/.test(e.textContent || "") && e.offsetParent !== null);
    }, null, { timeout: ms, polling: 400 });
  } catch { console.log("    (still loading after", ms / 1000, "s)"); }
  await sleep(400);
}
export async function go(page, path) {
  if (page.url() === `${BASE}/#${path}`) await page.reload();
  else await page.goto(`${BASE}/#${path}`);
  await settle(page);
}
export async function hscroll(page) {
  return page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
}
// Text of the main content (sidebar excluded).
export async function mainText(page) {
  return page.evaluate(() => (document.querySelector("main") || document.body).innerText);
}
export async function api(page, method, path, body) {
  if (!page.url().startsWith(BASE)) await page.goto(`${BASE}/#/no-access`);
  return page.evaluate(async ([m, p, b]) => {
    const r = await fetch(p, { method: m, credentials: "include", headers: b ? { "Content-Type": "application/json" } : {}, body: b ? JSON.stringify(b) : undefined });
    let j = null; try { j = await r.json(); } catch { /* empty */ }
    return { status: r.status, body: j };
  }, [method, path, body]);
}
export async function shot(page, name, full = false) {
  await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: full });
}
export function done() {
  if (notes.length) console.log(`\nNOTES:\n- ${notes.join("\n- ")}`);
  console.log(failures.length ? `\n${failures.length} FAILURES:\n- ${failures.join("\n- ")}` : "\nALL OK");
  process.exitCode = failures.length ? 1 : 0;
}
