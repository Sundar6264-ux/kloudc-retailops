// Suite B - Users (feature list incl. Sales insights + Marketing flyers, grant one and see it take effect), Help,
// Glossary, Overview per role (hide_sales: no dollars), no em dashes on the suite's pages, red-hover check.
// node admin.mjs [width ...]
import { chromium, as, go, settle, hscroll, check, note, done, shot, api, mainText, sleep, USERS, BASE } from "./lib.mjs";
import { PAGES } from "./sweep-pages.mjs";

const widths = process.argv.slice(2).map(Number).filter(Boolean);
const WIDTHS = widths.length ? widths : [1280, 1024, 375];
const browser = await chromium.launch();

for (const width of WIDTHS) {
  console.log(`\n== Admin pages @${width}`);
  const { ctx, page } = await as(browser, "sb_admin", width);
  // ---- Users
  await go(page, "/users");
  const feats = (await api(page, "GET", "/api/auth/features")).body?.features || [];
  check(feats.some((f) => f.key === "sales_insights" && f.label === "Insights") &&
    feats.some((f) => f.key === "marketing_flyers" && f.label === "Flyers"), `feature list has Insights + Flyers @${width}`);
  const row = page.locator("tr", { hasText: "sb_chat" }).first();
  await row.getByRole("button", { name: /Edit access/ }).click();
  const boxes = page.locator("label", { hasText: /^Insights$/ });
  check(await boxes.count() >= 1 && await page.locator("label", { hasText: /^Flyers$/ }).count() >= 1, `Edit access shows both checkboxes @${width}`);
  await shot(page, `users-edit-${width}`);
  check((await hscroll(page)) <= 1, `users page no h-scroll @${width}`);
  // grant Sales insights to sb_chat, then take it away again
  const cb = page.locator("tr", { hasText: "sb_chat" }).locator("..").locator("label", { hasText: /^Insights$/ }).locator("input").first();
  const target = (await cb.count()) ? cb : page.locator("label", { hasText: /^Insights$/ }).locator("input").last();
  await target.check();
  await page.getByRole("button", { name: "Save access" }).click();
  await sleep(1500);
  const chat = await as(browser, "sb_chat", width);
  const r1 = await api(chat.page, "GET", "/api/insights/prep-forecast");
  check(r1.status === 200, `granted Insights takes effect for sb_chat (${r1.status}) @${width}`);
  await chat.ctx.close();
  // restore
  await page.reload(); await settle(page);
  await page.locator("tr", { hasText: "sb_chat" }).first().getByRole("button", { name: /Edit access/ }).click();
  await page.locator("label", { hasText: /^Insights$/ }).locator("input").last().uncheck();
  await page.getByRole("button", { name: "Save access" }).click();
  await sleep(1000);
  const me = (await api(page, "GET", "/api/auth/users")).body;
  const sc = (me?.users || me || []).find?.((u) => u.username === "sb_chat");
  check(sc && !(sc.allowed_features || []).includes("sales_insights"), `access restored for sb_chat @${width}`);

  // ---- Help + Glossary
  await go(page, "/help");
  const help = await mainText(page);
  for (const s of ["Insights", "Daily Checklists", "Team: tasks, templates and chat", "Flyers (Marketing)", "Users (admin only)"]) {
    check(help.includes(s), `Help has "${s}" @${width}`);
  }
  await page.locator("details#insights > summary").click();
  await sleep(300);
  const ins = await page.locator("details#insights").innerText();
  check(/Also in Toast/.test(ins), `Help > Insights explains Also in Toast @${width}`);
  await page.locator("details#flyers > summary").click();
  await sleep(300);
  check((await hscroll(page)) <= 1, `help no h-scroll @${width}`);
  await shot(page, `help-${width}`);
  await go(page, "/glossary");
  const gs = page.locator("input.glossary-search");
  for (const q of ["Attach rate", "Marketing group", "Flyer", "Returning", "Loss watch"]) {
    await gs.fill(q);
    await sleep(400);
    const t = await mainText(page);
    check(!/Nothing matches/.test(t) && t.toLowerCase().includes(q.toLowerCase()), `glossary finds "${q}" @${width}`);
  }
  await gs.fill("");
  check((await hscroll(page)) <= 1, `glossary no h-scroll @${width}`);
  await shot(page, `glossary-${width}`);

  // ---- no em / en dashes on this suite's pages (house style), admin view
  if (width === 1280) {
    for (const [name, path] of PAGES) {
      await go(page, path);
      const t = await mainText(page);
      const i = t.search(/[—–]|&mdash;/);
      check(i < 0, `no em/en dash on ${name} ${i >= 0 ? JSON.stringify(t.slice(Math.max(0, i - 40), i + 30)) : ""}`);
    }
  }
  await ctx.close();

  // ---- Overview per role
  for (const role of ["sb_staff", "sb_hide", "sb_lead", "demo"]) {
    const o = await as(browser, role, width);
    await go(o.page, "/");
    const t = await mainText(o.page);
    check(!/NaN|undefined/.test(t), `${role} overview has no NaN/undefined @${width}`);
    if (role === "sb_hide" || role === "demo") {
      // sales dollars only: a vendor invoice's amount (Last invoice card) is a cost, not sales
      const lines = t.split("\n");
      const bad = lines.filter((l, i) => /\$\s?\d/.test(l) && !lines.slice(Math.max(0, i - 3), i).some((x) => /invoice/i.test(x)));
      check(bad.length === 0, `${role} overview shows no sales dollar amounts ${JSON.stringify(bad.slice(0, 3))} @${width}`);
    }
    check((await hscroll(o.page)) <= 1, `${role} overview no h-scroll @${width}`);
    await shot(o.page, `overview-${role}-${width}`);
    await o.ctx.close();
  }
}

// ---- custom buttons on the suite's pages never turn the global red on hover (gotcha 18)
{
  const { ctx, page } = await as(browser, "sb_admin", 1280);
  const RED = "rgb(185, 28, 28)";
  for (const [name, path] of PAGES) {
    await go(page, path);
    const btns = await page.locator("main button:visible").all();
    const reds = [];
    for (const b of btns.slice(0, 40)) {
      const cls = (await b.getAttribute("class")) || "";
      if (!cls || /(^|\s)(btn-primary|danger)(\s|$)/.test(cls)) continue;
      const before = await b.evaluate((e) => getComputedStyle(e).backgroundColor);
      if (/^rgb\((2[0-2]\d|19\d), (2\d|3\d|4\d), (3\d|4\d|5\d|6\d)\)$/.test(before)) continue; // already a red primary
      try { await b.hover({ timeout: 1500 }); } catch { continue; }
      const after = await b.evaluate((e) => getComputedStyle(e).backgroundColor);
      if (after !== before && /^rgb\((1[6-9]\d|2[0-2]\d), (1\d|2\d|3\d|4\d), (1\d|2\d|3\d|4\d|5\d)\)$/.test(after)) reds.push(`${cls}: ${after}`);
    }
    check(reds.length === 0, `no red hover on custom buttons on ${name} ${reds.slice(0, 3).join(" ; ")}`);
  }
  void RED;
  await ctx.close();
}
await browser.close();
done();
