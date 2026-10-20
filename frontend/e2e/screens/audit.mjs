// Contrast + overflow audit over the same pages as screens.mjs (review aid).
//   SCREENS_BASE=... SCREENS_LOGINS=... node frontend/e2e/screens/audit.mjs
import { chromium } from "playwright";
import { auditPage } from "./contrast.mjs";
import { readFileSync } from "node:fs";

const BASE = process.env.SCREENS_BASE || "http://127.0.0.1:8870";
const LOGINS = JSON.parse(readFileSync(process.env.SCREENS_LOGINS, "utf8"));
const THEMES = (process.env.SCREENS_THEMES || "light,dark").split(",");
const WIDTHS = (process.env.SCREENS_WIDTHS || "1280,375").split(",").map(Number);
const pagesFor = {
  DEMO: ["/", "/daily-checklists", "/reorder", "/supplier-projection", "/vendor-calendar", "/purchase-order", "/placed-orders", "/fix-issues", "/delivery-review", "/invoices", "/inventory-reports", "/reconciliation", "/items-sold", "/team/tasks", "/team/chat", "/team/announcements", "/users", "/teams", "/po-sections", "/vendor-contacts", "/issue-leads", "/checklists/admin", "/team/templates", "/team/moderation", "/help", "/glossary", "/change-password", "/settings/store", "/settings/data", "/setup/item-vendors"],
  RETAILOPS_TEST_TOAST: ["/open-orders", "/insights/daily", "/insights/prep-forecast", "/insights/busy-hours", "/settings/integrations", "/inventory-api", "/flyers"],
  RETAILOPS_TEST_CLOVER: ["/settings/integrations", "/setup/item-vendors", "/settings/data"],
  PLATFORM: ["/platform"],
  "": ["/", "/signup", "/setup"],
};

// AUDIT_ONLY=/setup/item-vendors re-checks just the routes containing it.
const ONLY = process.env.AUDIT_ONLY || "";
for (const k of Object.keys(pagesFor)) pagesFor[k] = pagesFor[k].filter((r) => !ONLY || r.includes(ONLY));

const browser = await chromium.launch();
for (const theme of THEMES) for (const width of WIDTHS) for (const [code, routes] of Object.entries(pagesFor)) {
  const ctx = await browser.newContext({ viewport: { width, height: 900 }, colorScheme: theme });
  await ctx.addInitScript((t) => { try { localStorage.setItem("kc.theme", t); } catch {} }, theme);
  const page = await ctx.newPage();
  if (code) {
    const who = LOGINS[code][0];
    await page.request.post(`${BASE}/api/auth/login`, { data: { store_code: code, username: who.username, password: who.password } });
  }
  for (const route of routes) {
    await page.goto(`${BASE}/#${route}`);
    await page.waitForLoadState("networkidle", { timeout: 10000 }).catch(() => {});
    await page.waitForTimeout(400);
    const tabs = code === "PLATFORM" ? ["requests", "stores", "hosting", "audit", "masters", "settings", "help"] : [null];
    for (const tab of tabs) {
      if (tab) { await page.click(`[data-testid="pf-tab-${tab}"]`); await page.waitForTimeout(500); }
      const res = await page.evaluate(auditPage);
      const uniq = [...new Set(res.low.map((s) => s.replace(/"[^"]*"/, "").trim()))];
      if (uniq.length || res.over.length) {
        console.log(`== ${theme} ${width} ${code || "(out)"} ${route}${tab ? " " + tab : ""}`);
        for (const s of res.low.filter((s, i, a) => a.findIndex((x) => x.replace(/"[^"]*"/, "") === s.replace(/"[^"]*"/, "")) === i).slice(0, 25)) console.log("  low " + s);
        for (const s of res.over.slice(0, 10)) console.log("  over " + s);
      }
    }
  }
  await ctx.close();
}
await browser.close();
