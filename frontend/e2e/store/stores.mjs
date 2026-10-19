// Per-store website checks (multi-store M2, MT9; docs/M2_CONTRACT.md 6, 7).
// Throwaway servers from run.sh - the fake Toast store and the fake Clover
// store (every role), plus the DEMO store (CSV upload, its review login)
// when DEMO_PORT is set - at 1280 and 375 pixels:
//   - login page and signed-in pages show the store's name, logo and colour;
//     the document title is the store's;
//   - times are the store's zone (Team > Tasks says "store time (<city>)");
//   - pages the store can't have are not in the menu, and their URLs
//     redirect to "Not available for this store";
//   - Label Scanner only with a labels site, "Also in <pos>" only with
//     Insights;
//   - no origin-store name anywhere, and a Clover store never says "Toast".
// Exit code 1 on any failure. Screenshots: $STORE_E2E_WORK/shots.
import { chromium } from "playwright";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { brandCssVars } from "../../src/store/branding.js";
import { firstOriginNameLine, hasOriginName } from "../../src/test/originNames.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const WORK = process.env.STORE_E2E_WORK;
const STORES = [
  { code: "RETAILOPS_TEST_TOAST", port: Number(process.env.TOAST_PORT || 8861), city: "Chicago" },
  { code: "RETAILOPS_TEST_CLOVER", port: Number(process.env.CLOVER_PORT || 8862), city: "Los Angeles" },
];
// The DEMO store (MT13): run_demo.py's own generated data, signed in as the
// App Review login with the password run.sh gave it.
if (process.env.DEMO_PORT) {
  STORES.push({ code: "DEMO", port: Number(process.env.DEMO_PORT), city: "New York", roles: ["appreview"], password: process.env.DEMO_REVIEW_PASSWORD });
}
const ROLES = ["e2e_admin", "e2e_staff", "e2e_hide", "app_store_review"];
const WIDTHS = [1280, 375];
const SHOTS = join(WORK, "shots");
mkdirSync(SHOTS, { recursive: true });

const failures = [];
// Hits that come from the server's own data or messages (an API response on
// that page holds the same text) - reported separately: they belong to the
// server batches (settings, store pack), not to the website.
const serverHits = [];
const notes = [];
let checks = 0;
function check(ok, msg) {
  checks++;
  if (!ok) failures.push(msg);
}

async function expandMenu(page, width) {
  if (width < 900) {
    const toggle = page.locator(".sidebar-toggle");
    if (await toggle.isVisible()) await toggle.click();
  }
  // Open every group and nested group so every link is in the DOM.
  for (let pass = 0; pass < 3; pass++) {
    const closed = page.locator('nav.sidebar button.sidebar-group-toggle[aria-expanded="false"]');
    const n = await closed.count();
    if (!n) break;
    for (let i = n - 1; i >= 0; i--) await closed.nth(i).click();
  }
}

async function menuLinks(page) {
  return page.$$eval("nav.sidebar a", (as) =>
    as.map((a) => ({ href: a.getAttribute("href") || "", text: (a.textContent || "").trim(), external: a.target === "_blank" })),
  );
}

async function settle(page) {
  await page.waitForLoadState("networkidle", { timeout: 8000 }).catch(() => {});
  await page.waitForTimeout(150);
}

async function runStore(browser, store) {
  const base = `http://127.0.0.1:${store.port}`;
  const brand = JSON.parse(readFileSync(join(here, `../fixtures/branding-${store.code}.json`), "utf8"));
  const served = await (await fetch(`${base}/api/app/branding`)).json();
  for (const k of ["name", "short_name", "timezone", "primary_color", "pos_name", "labels_url"]) {
    check(JSON.stringify(served[k]) === JSON.stringify(brand[k]), `${store.code}: served branding ${k}=${served[k]} != fixture ${brand[k]}`);
  }
  const users = store.password ? await signIn(base, store.roles, store.password) : JSON.parse(readFileSync(join(WORK, store.code, "users.json"), "utf8"));
  const accent = brandCssVars(brand.primary_color, brand.secondary_color)["--accent"].toLowerCase();
  // Clover and the CSV-only DEMO store: no Insights, no labels site, never "Toast".
  const notToast = brand.pos_name !== "Toast";
  const isDemo = Boolean(brand.demo);
  const title = `${brand.name} - KloudC RetailOps`;

  for (const width of WIDTHS) {
    // ---- logged out: the login page is the store's ----
    {
      const ctx = await browser.newContext({ viewport: { width, height: 900 } });
      const page = await ctx.newPage();
      await page.goto(`${base}/`);
      await page.waitForSelector('[data-testid="login-store-name"]');
      check((await page.textContent('[data-testid="login-store-name"]')).trim() === brand.name, `${store.code} ${width}: login name`);
      check((await page.title()) === title, `${store.code} ${width}: login title "${await page.title()}"`);
      const body = await page.innerText("body");
      check(!hasOriginName(body), `${store.code} ${width}: origin-store name on the login page`);
      await page.screenshot({ path: join(SHOTS, `${store.code}-login-${width}.png`) });
      await ctx.close();
    }

    for (const role of store.roles || ROLES) {
      const tag = `${store.code} ${role} ${width}`;
      const ctx = await browser.newContext({ viewport: { width, height: 900 } });
      await ctx.addCookies([{ name: users.cookie, value: users.users[role].token, domain: "127.0.0.1", path: "/" }]);
      const page = await ctx.newPage();
      const errors = [];
      page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
      page.on("pageerror", (e) => errors.push(String(e)));
      const failed5xx = [];
      let apiText = [];
      page.on("response", async (r) => {
        if (r.status() >= 500) failed5xx.push(`${r.status()} ${r.url().replace(base, "")}`);
        if (r.url().startsWith(`${base}/api/`)) {
          try {
            apiText.push(strings(await r.json()).join("\n"));
          } catch {
            // not JSON
          }
        }
      });

      await page.goto(`${base}/#/`);
      await page.waitForSelector('[data-testid="store-name"]', { timeout: 15000 });
      await settle(page);
      check((await page.title()) === title, `${tag}: title "${await page.title()}"`);
      check((await page.textContent('[data-testid="store-name"]')).trim() === brand.short_name, `${tag}: sidebar store name`);
      const cssAccent = (await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--accent"))).trim().toLowerCase();
      check(cssAccent === accent, `${tag}: --accent ${cssAccent} != ${accent}`);
      const logoOk = await page.$eval('[data-testid="store-logo"]', (el) => el.tagName === "IMG" ? el.complete && el.naturalWidth > 0 : true);
      check(logoOk, `${tag}: logo did not load`);
      await page.screenshot({ path: join(SHOTS, `${store.code}-${role}-${width}-home.png`) });

      await expandMenu(page, width);
      const links = await menuLinks(page);
      const internal = [...new Set(links.filter((l) => l.href.startsWith("#/")).map((l) => l.href))];
      const menuText = links.map((l) => l.text).join(" | ") + " | " + (await page.innerText("nav.sidebar"));
      const labelLink = links.find((l) => l.text.startsWith("Label Scanner"));
      if (notToast) {
        check(!internal.some((h) => h.startsWith("#/insights")), `${tag}: Insights in a Clover menu`);
        check(!labelLink, `${tag}: Label Scanner on a store without labels`);
        check(!/Toast/i.test(menuText), `${tag}: "Toast" in the Clover menu`);
        check(!/Also in/i.test(menuText), `${tag}: "Also in" group without Insights`);
        if (role === "e2e_admin") check(internal.includes("#/inventory-api") && /Inventory from Clover/.test(menuText), `${tag}: Inventory from Clover missing`);
        if (isDemo) check(!internal.includes("#/inventory-api"), `${tag}: Inventory from POS on a CSV-only store`);
      } else {
        check(labelLink && labelLink.external, `${tag}: Label Scanner missing`);
        if (labelLink) {
          const href = await page.$eval("nav.sidebar a[target=_blank]", (a) => a.href);
          check(href.startsWith(brand.labels_url), `${tag}: Label Scanner href ${href}`);
        }
        if (role === "e2e_admin") {
          check(internal.includes("#/insights/daily") && /Also in Toast/i.test(menuText), `${tag}: Insights / Also in Toast missing`);
          check(internal.includes("#/inventory-api") && /Inventory from Toast/.test(menuText), `${tag}: Inventory from Toast missing`);
        }
      }
      check(!hasOriginName(menuText), `${tag}: origin-store name in the menu`);

      // ---- gated URLs redirect (Clover), or open (Toast admin) ----
      const gated = ["#/insights/daily", "#/insights/returning-customers", "#/insights/busy-hours", "#/insights"];
      for (const hash of gated) {
        await page.goto(`${base}/${hash}`);
        await settle(page);
        const url = page.url();
        if (notToast) {
          check(url.includes("#/not-available"), `${tag}: ${hash} -> ${url} (expected not-available)`);
          check(await page.isVisible('[data-testid="not-available"]'), `${tag}: ${hash} shows no Not available page`);
          if (hash === "#/insights/daily" && role === "e2e_admin") {
            await page.screenshot({ path: join(SHOTS, `${store.code}-${role}-${width}-not-available.png`) });
          }
        } else if (role === "e2e_admin") {
          check(!url.includes("not-available"), `${tag}: ${hash} -> ${url} on a Toast store`);
        }
      }

      // ---- Overview closing tiles follow the store's own closing checklists ----
      if (role === "e2e_admin") {
        await page.goto(`${base}/#/`);
        await settle(page);
        const tiles = await page.$$eval(".stat-tile-label", (els) => els.map((e) => e.textContent.trim()));
        check(tiles.includes("Store closing"), `${tag}: Overview has no Store closing tile (${tiles.join(", ")})`);
        check(!tiles.includes("Kitchen closing"), `${tag}: Overview shows a Kitchen closing tile for a pack without one`);
        check(!tiles.includes("Closing checklists"), `${tag}: Overview closing tiles did not load`);
        check(tiles.includes("Today's sales") === brand.capabilities.includes("live_today_sales"), `${tag}: Today's sales tile vs live_today_sales`);
      }

      // ---- store time zone ----
      await page.goto(`${base}/#/team/tasks`);
      await settle(page);
      if (page.url().includes("#/team/tasks")) {
        const t = await page.innerText("main");
        check(t.includes(`store time (${store.city})`), `${tag}: Team > Tasks doesn't say store time (${store.city})`);
      }

      // ---- sweep every page in this role's menu ----
      // (#/notifications is gone from the website: it redirects to the dashboard.)
      const sweep = [...internal, "#/glossary", "#/help"];
      for (const hash of sweep) {
        apiText = [];
        await page.goto(`${base}/${hash}`);
        await settle(page);
        const text = await page.innerText("body");
        const fromServer = (snippet) => apiText.some((t) => t.includes(snippet));
        const report = (word, re) => {
          for (const m of text.matchAll(re)) {
            const line = m[0].trim();
            const msg = `${tag}: "${word}" on ${hash}: ...${line.slice(0, 120)}...`;
            // A server string shows up whole or cut by the page; 20 characters
            // around the hit are enough to recognise it.
            const i = line.search(new RegExp(word, "i"));
            const before = line.slice(Math.max(0, i - 12), i + word.length);
            const after = line.slice(i, i + word.length + 12);
            (fromServer(before) || fromServer(after) ? serverHits : failures).push(msg);
          }
        };
        // Origin-store names are matched by hash (src/test/originNames.mjs):
        // the whole line is the snippet.
        const nameLine = firstOriginNameLine(text);
        if (nameLine) {
          const msg = `${tag}: origin-store name on ${hash}: ...${nameLine.trim().slice(0, 120)}...`;
          (fromServer(nameLine.trim().slice(0, 20)) ? serverHits : failures).push(msg);
        }
        if (notToast) report("Toast", /[^\n]{0,40}\bToast\b[^\n]{0,40}/g);
        checks++;
        if (width === 375) {
          const sideways = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
          if (sideways > 2) notes.push(`${tag}: ${hash} scrolls sideways by ${sideways}px`);
        }
      }
      if (errors.length) notes.push(`${tag}: console errors: ${[...new Set(errors)].slice(0, 5).join(" || ")}`);
      if (failed5xx.length) notes.push(`${tag}: 5xx: ${[...new Set(failed5xx)].slice(0, 8).join(", ")}`);
      await ctx.close();
    }
  }
}

// A real sign-in (POST /api/auth/login) for stores whose logins the harness
// didn't make; returns the same shape as seed.py's users.json.
async function signIn(base, roles, password) {
  const out = { cookie: "", users: {} };
  for (const role of roles) {
    const r = await fetch(`${base}/api/auth/login`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ username: role, password }),
    });
    if (r.status !== 200) throw new Error(`sign-in ${role} at ${base}: ${r.status}`);
    const [pair] = r.headers.getSetCookie()[0].split(";");
    const i = pair.indexOf("=");
    out.cookie = pair.slice(0, i);
    out.users[role] = { token: pair.slice(i + 1) };
  }
  return out;
}

// Every string value in a JSON body.
function strings(v, out = []) {
  if (typeof v === "string") out.push(v);
  else if (Array.isArray(v)) v.forEach((x) => strings(x, out));
  else if (v && typeof v === "object") Object.values(v).forEach((x) => strings(x, out));
  return out;
}

const browser = await chromium.launch();
try {
  for (const store of STORES) await runStore(browser, store);
} finally {
  await browser.close();
}
const summary = { checks, failures: failures.length, server_text: serverHits.length, notes: notes.length };
writeFileSync(join(WORK, "result.json"), JSON.stringify({ summary, failures, serverHits, notes }, null, 1));
console.log(JSON.stringify(summary));
for (const f of failures) console.log("FAIL", f);
for (const s of [...new Set(serverHits.map((h) => h.replace(/^\S+ \S+ \d+: /, "")))]) console.log("SERVER", s);
for (const n of notes.slice(0, 40)) console.log("NOTE", n);
process.exit(failures.length ? 1 : 0);
