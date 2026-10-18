// Shared helpers for Suite A (ordering & stock pages). Every spec talks only to the
// throwaway server started by ctl.sh (REG_PORT, default 8830) on the prod COPY.
import { chromium } from "../../node_modules/playwright/index.mjs";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

export { chromium };
export const PORT = process.env.REG_PORT || "8830";
export const BASE = `http://127.0.0.1:${PORT}`;
export const WORK = process.env.REG_WORK || `${process.env.HOME}/.claude/jobs/e26a1611/tmp/suiteA`;
export const DB = `${WORK}/copy.db`;
export const USERS = JSON.parse(fs.readFileSync(`${WORK}/users.json`, "utf8"));
export const SPEC = path.basename(process.argv[1] || "spec", ".mjs");
export const SHOTS = `${WORK}/shots/${SPEC}`;
fs.mkdirSync(SHOTS, { recursive: true });
export const WIDTHS = (process.env.REG_WIDTHS || "1280,1024,375").split(",").map(Number);
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------- results ----------
export const failures = [];
let passes = 0;
export function check(cond, msg) {
  if (cond) passes += 1;
  else failures.push(msg);
  console.log(cond ? "  ok:" : "  FAIL:", msg);
  return !!cond;
}
export function section(name) {
  console.log(`\n== ${name}`);
}
export function done() {
  const summary = { spec: SPEC, passes, failures };
  fs.writeFileSync(`${WORK}/result-${SPEC}.json`, JSON.stringify(summary, null, 1));
  console.log(failures.length ? `\n${SPEC}: ${passes} ok, ${failures.length} FAILED:\n- ${failures.join("\n- ")}` : `\n${SPEC}: ALL ${passes} OK`);
  process.exitCode = failures.length ? 1 : 0;
}

// ---------- the copy ----------
// sqlite3 on the COPY only (refuses anything outside REG_WORK).
export function sql(q) {
  if (!DB.startsWith(WORK + "/") || DB.includes("spicetown-backend")) throw new Error("not the copy");
  return execFileSync("sqlite3", ["-separator", "|", DB, q], { encoding: "utf8" }).trim();
}
export const q1 = (q) => sql(q).split("\n")[0] || "";
export function today() {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(new Date());
}

// ---------- sessions ----------
// Responses the sandbox can't give: Toast Analytics (blank creds) - pages must cope.
export const ignorable = (s) => /insights|analytics|sales-accuracy\/run/.test(s);

export async function as(browser, user, width = 1280, opts = {}) {
  const ctx = await browser.newContext({
    viewport: { width, height: width < 600 ? 812 : 900 },
    deviceScaleFactor: 1,
    ...opts,
  });
  const u = USERS[user];
  if (!u) throw new Error(`unknown user ${user}`);
  const r = await ctx.request.post(`${BASE}/api/auth/login`, { data: { username: user, password: u.password } });
  if (!r.ok()) throw new Error(`login ${user}: ${r.status()}`);
  const page = await ctx.newPage();
  page.errors = [];
  page.bad = [];
  page.on("pageerror", (e) => page.errors.push("pageerror " + String(e)));
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    const t = m.text();
    // a failed fetch logs "Failed to load resource" - counted through page.bad instead
    if (/Failed to load resource/.test(t)) return;
    page.errors.push("console " + t);
  });
  page.on("response", (r) => {
    const url = r.url();
    if (url.includes("/api/") && r.status() >= 400) {
      page.bad.push(`${r.status()} ${r.request().method()} ${url.replace(BASE, "")}`);
    }
  });
  page.user = user;
  page.width = width;
  return { ctx, page };
}

// Same-document hash navigations resolve at once: wait for the page's own content.
export async function go(page, hash, { wait = null, ms = 240000 } = {}) {
  await page.goto(`${BASE}/#${hash}`);
  await settle(page, ms);
  if (wait) await page.waitForSelector(wait, { timeout: ms });
}

export async function settle(page, ms = 240000) {
  await sleep(400);
  try {
    await page.waitForFunction(
      () => {
        const els = [...document.querySelectorAll("body *")].filter((e) => e.children.length === 0);
        const busy = els.some(
          (e) => /^\s*(Loading|Calculating|Working|Checking|Looking)\b[^]{0,40}[.…]+\s*$/.test(e.textContent || "") && e.offsetParent !== null,
        );
        // Fix Issues tiles show "…" until the summary is in
        const tiles = [...document.querySelectorAll(".issue-tab-count")].some((e) => e.textContent.trim() === "…");
        return !busy && !tiles;
      },
      null,
      { timeout: ms, polling: 400 },
    );
  } catch {
    console.log(`    (still loading after ${ms / 1000}s)`);
  }
  await sleep(300);
}

export async function hscroll(page) {
  return page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
}

let shotN = 0;
export async function shot(page, name, opts = {}) {
  shotN += 1;
  const file = `${SHOTS}/${String(shotN).padStart(3, "0")}-${name}-${page.user || ""}-${page.width || ""}.png`;
  await page.screenshot({ path: file, fullPage: opts.fullPage ?? false, ...opts });
  return file;
}

// Fail on page errors and on unexpected 4xx/5xx (allowed: a list of regexes).
export function cleanPage(page, label, allowed = []) {
  const bad = page.bad.filter((b) => !ignorable(b) && !allowed.some((re) => re.test(b)));
  check(page.errors.length === 0, `${label}: no console/page errors${page.errors.length ? " - " + page.errors.slice(0, 3).join(" | ") : ""}`);
  check(bad.length === 0, `${label}: no unexpected API errors${bad.length ? " - " + bad.slice(0, 5).join(" | ") : ""}`);
  page.errors.length = 0;
  page.bad.length = 0;
}

// The global red hover (gotcha 18): hovering an enabled button must not paint it accent red.
export async function redHovers(page, scope = "main") {
  const btns = await page.$$(`${scope} button:not([disabled])`);
  const red = [];
  for (const b of btns.slice(0, 60)) {
    if (!(await b.isVisible())) continue;
    try {
      await b.hover({ timeout: 1000 });
    } catch {
      continue;
    }
    const bg = await b.evaluate((e) => getComputedStyle(e).backgroundColor);
    const cls = await b.evaluate((e) => e.className + " " + (e.textContent || "").trim().slice(0, 30));
    if (/rgb\(191, 35, 52\)/.test(bg) && !/primary|danger|accent/.test(cls)) red.push(cls);
  }
  return red;
}

// API call as the page's user (shares the cookie jar).
export async function api(page, method, url, data) {
  const r = await page.context().request.fetch(`${BASE}${url}`, { method, data, failOnStatusCode: false });
  let body = null;
  try {
    body = await r.json();
  } catch {
    /* not json */
  }
  return { status: r.status(), body };
}

export async function launch() {
  return chromium.launch({ args: ["--disable-gpu"] });
}

// Poll the copy until a query returns `expected` (or timeout); returns the last value.
export async function waitDb(query, expected, ms = 30000) {
  const t0 = Date.now();
  let v = q1(query);
  while (v !== String(expected) && Date.now() - t0 < ms) {
    await sleep(300);
    v = q1(query);
  }
  return v;
}
