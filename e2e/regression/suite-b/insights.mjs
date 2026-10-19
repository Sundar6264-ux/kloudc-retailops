// Suite B - Insights: every page per role, "Also in Toast" group, hide_sales sees no dollars anywhere (pages + APIs),
// admin-only pages, Loss watch acknowledge, Prep forecast print layout. node insights.mjs [width ...]
import { chromium, as, go, settle, hscroll, check, note, done, shot, api, mainText, sleep, BASE } from "./lib.mjs";

const widths = process.argv.slice(2).map(Number).filter(Boolean);
const WIDTHS = widths.length ? widths : [1280, 1024, 375];
const MAIN = ["/insights/daily", "/insights/loss-watch", "/insights/odd-checks", "/insights/prep-forecast", "/insights/menu-trends",
  "/insights/add-ons", "/insights/returning-customers"];
const ALSO = ["/insights/channels", "/insights/check-trends", "/insights/busy-hours", "/insights/best-sellers"];
const SALES_ONLY = ["/insights/daily", "/insights/menu-trends", "/insights/add-ons", "/insights/channels", "/insights/check-trends", "/insights/best-sellers"];
const ADMIN_ONLY = ["/insights/loss-watch", "/insights/odd-checks"];
const API = { daily: "compare", "loss-watch": "loss-watch", "odd-checks": "odd-checks", "prep-forecast": "prep-forecast",
  "menu-trends": "menu-performance", "add-ons": "addons", "returning-customers": "returning-customers", channels: "channels",
  "check-trends": "check-trends", "busy-hours": "hourly-heatmap", "best-sellers": "menu-performance" };
const MONEY = /\$\s?\d|\d\.\d\d\b(?!\s*%)/;
const MONEY_KEYS = /"(net_sales|avg_net_sales|max_net_sales|total_net|avg_check|discount|refund|void|total|comp_estimate|prev_net_sales)"\s*:\s*-?\d/;
const browser = await chromium.launch();

async function sidebarOpenInsights(page) {
  // "Sales & Insights" since the Oct 15 menu clean-up.
  const toggle = page.locator(".sidebar button.sidebar-group-toggle", { hasText: /Insights$/ }).first();
  if (await toggle.count() && (await toggle.getAttribute("aria-expanded")) !== "true") await toggle.click();
}

for (const width of WIDTHS) {
  console.log(`\n== Insights @${width}`);
  // ---- admin: every page lands where it should, data-source line, Also in Toast tag on the four
  {
    const { ctx, page } = await as(browser, "sb_admin", width);
    for (const p of [...MAIN, ...ALSO]) {
      await go(page, p);
      const landed = page.url().replace(BASE + "/#", "");
      const txt = await mainText(page);
      check(landed === p, `admin lands on ${p} (${landed}) @${width}`);
      check(/Toast Analytics|From Toast/i.test(txt), `${p} has the data-source line @${width}`);
      const also = ALSO.includes(p);
      check(also === /Same numbers as Toast's own reports/.test(txt), `${p} ${also ? "shows" : "has no"} the Also in Toast note @${width}`);
      check((await hscroll(page)) <= 1, `${p} no h-scroll @${width}`);
      check(!/NaN|undefined|Infinity/.test(txt), `${p} no NaN/undefined @${width}`);
      if (ADMIN_ONLY.includes(p) && width >= 1280) {
        const over = await page.locator(".table-wrap").first().evaluate((w) => w.querySelector("table").scrollWidth - w.clientWidth).catch(() => 0);
        check(over <= 1, `${p} flagged table fits (Status / Acknowledge not cut off, ${over}px over) @${width}`);
      }
      await shot(page, `ins-admin${p.replace("/insights", "").replace(/\//g, "-")}-${width}`);
    }
    // sidebar: Also in Toast collapsed by default; opening shows the note + 4 links (desktop sidebar only)
    if (width >= 1024) {
      const fresh = await as(browser, "sb_admin", width);
      await go(fresh.page, "/insights/daily");
      await sidebarOpenInsights(fresh.page);
      const sub0 = fresh.page.locator(".sidebar-subgroup-toggle", { hasText: "Also in Toast" });
      check(await sub0.count() === 1 && (await sub0.getAttribute("aria-expanded")) === "false", `Also in Toast collapsed by default (fresh session) @${width}`);
      await fresh.ctx.close();
      await go(page, "/insights/daily");
      await sidebarOpenInsights(page);
      const sub = page.locator(".sidebar-subgroup-toggle", { hasText: "Also in Toast" });
      if ((await sub.getAttribute("aria-expanded")) === "true") await sub.click();
      await sub.click();
      const subTxt = await page.locator(".sidebar-subgroup").innerText();
      check(/Same numbers as Toast's own reports/.test(subTxt) && /Sales channels/.test(subTxt) && /Best & worst sellers/.test(subTxt), `opening Also in Toast shows the note and its pages @${width}`);
      await go(page, "/insights/busy-hours");
      check((await page.locator(".sidebar-subgroup-toggle").getAttribute("aria-expanded")) === "true", `a link into Also in Toast opens both levels @${width}`);
      await shot(page, `ins-sidebar-${width}`);
      // old /insights/menu redirects
      await go(page, "/insights/menu?tab=top");
      check(page.url().includes("/insights/best-sellers"), `/insights/menu?tab=top -> Best & worst sellers (${page.url().replace(BASE, "")}) @${width}`);
      await go(page, "/insights/menu");
      check(page.url().includes("/insights/menu-trends"), `/insights/menu -> Menu trends @${width}`);
    }
    // loss watch acknowledge (first un-acked flagged check)
    await go(page, "/insights/loss-watch");
    const ackBtn = page.getByRole("button", { name: /^Acknowledge/ }).first();
    if (await ackBtn.count()) {
      const lw = (await api(page, "GET", "/api/insights/loss-watch?days=30")).body;
      const before = (lw?.flagged_checks || []).filter((f) => f.acked).length;
      await ackBtn.click();
      await sleep(1500);
      const lw2 = (await api(page, "GET", "/api/insights/loss-watch?days=30")).body;
      const after = (lw2?.flagged_checks || []).filter((f) => f.acked).length;
      check(after === before + 1, `Acknowledge stores the ack (${before} -> ${after}) @${width}`);
      check(await page.getByText("Acknowledged").count() > 0, `row shows Acknowledged @${width}`);
      await shot(page, `ins-loss-acked-${width}`);
    } else note(`no un-acked flagged check to acknowledge @${width}`);
    // prep forecast print layout
    if (width === 1280) {
      await go(page, "/insights/prep-forecast");
      check(await page.getByRole("button", { name: "Print" }).count() === 1, "Prep forecast has Print");
      await page.emulateMedia({ media: "print" });
      await sleep(400);
      const vis = await page.evaluate(() => ({
        sidebar: !!document.querySelector(".sidebar") && getComputedStyle(document.querySelector(".sidebar")).display !== "none",
        controls: [...document.querySelectorAll(".ins-no-print")].some((e) => getComputedStyle(e).display !== "none"),
        rows: document.querySelectorAll(".ins-printable tbody tr").length,
      }));
      check(!vis.sidebar && !vis.controls, `print hides the sidebar and controls (${JSON.stringify(vis)})`);
      await page.pdf({ path: `${process.env.SUITE_B_WORK || "/tmp/retailops-suiteB"}/shots/prep-forecast-print.pdf`, format: "Letter" });
      await shot(page, "ins-prep-print-1280", true);
      await page.emulateMedia({ media: "screen" });
    }
    await ctx.close();
  }

  // ---- staff (sales_insights, not admin): admin pages hidden + refused
  {
    const { ctx, page } = await as(browser, "sb_staff", width);
    for (const p of ADMIN_ONLY) {
      await go(page, p);
      check(!page.url().includes(p), `staff: ${p} redirects (${page.url().replace(BASE, "")}) @${width}`);
      const r = await api(page, "GET", `/api/insights/${API[p.split("/").pop()]}`);
      check(r.status === 403, `staff: /api/insights/${API[p.split("/").pop()]} 403 (${r.status}) @${width}`);
    }
    for (const p of ["/insights/daily", "/insights/busy-hours"]) {
      await go(page, p);
      check(page.url().includes(p), `staff opens ${p} @${width}`);
    }
    if (width >= 1024) {
      await sidebarOpenInsights(page);
      const sb = await page.locator(".sidebar").innerText();
      check(!/Loss watch|Odd checks/.test(sb), `staff sidebar has no Loss watch / Odd checks @${width}`);
    }
    await ctx.close();
  }

  // ---- hide_sales: no dollars anywhere it can go
  {
    const { ctx, page } = await as(browser, "sb_hide", width);
    await go(page, "/insights");
    check(page.url().endsWith("/insights/prep-forecast"), `hide: /insights lands on Prep forecast (${page.url().replace(BASE, "")}) @${width}`);
    for (const p of [...MAIN, ...ALSO]) {
      await go(page, p);
      const landed = page.url().replace(BASE + "/#", "");
      const allowed = !SALES_ONLY.includes(p) && !ADMIN_ONLY.includes(p);
      check(allowed ? landed === p : landed !== p, `hide: ${p} ${allowed ? "opens" : "redirects"} (${landed}) @${width}`);
      const txt = await mainText(page);
      const m = txt.match(MONEY);
      check(!m, `hide: no dollar amounts on ${landed} ${m ? `("${txt.slice(Math.max(0, m.index - 40), m.index + 20).replace(/\n/g, " ")}")` : ""} @${width}`);
      await shot(page, `ins-hide${landed.replace("/insights", "").replace(/[/?=]/g, "-")}-${width}`);
    }
    for (const p of ["/", "/team/tasks", "/daily-checklists", "/items-sold"]) {
      await go(page, p);
      const txt = await mainText(page);
      const m = txt.match(/\$\s?\d/);
      check(!m, `hide: no dollar amounts on ${p} ${m ? `("${txt.slice(Math.max(0, m.index - 40), m.index + 20).replace(/\n/g, " ")}")` : ""} @${width}`);
    }
    if (width === 1280) {
      for (const [k, ep] of Object.entries(API)) {
        const r = await api(page, "GET", `/api/insights/${ep}`);
        const s = JSON.stringify(r.body || {});
        if (["busy-hours", "prep-forecast", "returning-customers"].includes(k)) {
          check(r.status === 200 && !MONEY_KEYS.test(s), `hide: /api/insights/${ep} answers without money fields (${r.status}${MONEY_KEYS.test(s) ? " " + s.match(MONEY_KEYS)[0] : ""})`);
        } else check(r.status === 403, `hide: /api/insights/${ep} refused (${r.status})`);
      }
    }
    await ctx.close();
  }

  // ---- demo: no Insights at all
  {
    const { ctx, page } = await as(browser, "demo", width);
    await go(page, "/insights/daily");
    check(!page.url().includes("/insights"), `demo: Insights not reachable (${page.url().replace(BASE, "")}) @${width}`);
    if (width === 1280) {
      const r = await api(page, "GET", "/api/insights/prep-forecast");
      check(r.status === 403, `demo: insights API refused (${r.status})`);
      const sb = await page.locator(".sidebar").innerText();
      check(!/Insights/.test(sb), "demo: no Insights in the sidebar");
    }
    await ctx.close();
  }
}
await browser.close();
done();
