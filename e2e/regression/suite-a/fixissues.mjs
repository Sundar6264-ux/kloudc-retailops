// Fix Issues (every list incl. Dead - zero stock, grouped by PO section, filters
// section / vendor / lead / task, code search, details drawer, close / bulk close /
// reopen, Closed report) + Category & vendor leads (save, Run now, the lead's view).
// Needs a fresh copy (ctl.sh reset) - it closes issues and saves leads.
import { launch, as, go, check, section, done, hscroll, shot, cleanPage, q1, sql, sleep, waitDb, USERS } from "./lib.mjs";

const browser = await launch();
const TABS = ["Missing barcodes", "Invalid barcodes", "Duplicates", "Negative inventory", "Refill from container",
  "Dead with stock", "Dead - zero stock", "Dead - newly arrived", "Slow moving - Review"];

async function openFI(page, hash = "/fix-issues") {
  await go(page, hash);
  await page.locator(".issue-tabs").waitFor({ timeout: 240000 });
  await page.waitForFunction(() => !/…/.test(document.querySelector(".issue-tab-count")?.textContent || "…"), null, { timeout: 240000 });
}
async function pickTab(page, label) {
  await page.locator(".issue-tab", { has: page.locator(".issue-tab-label", { hasText: new RegExp(`^${label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`) }) }).click();
  await page.waitForFunction(() => {
    const card = document.querySelector(".issue-card");
    return card && !/^\s*Loading/.test(card.textContent) && (card.querySelector("table") || /all clear|Nothing|matches/.test(card.textContent));
  }, null, { timeout: 240000 });
  await sleep(300);
}
const rowsIn = (page) => page.locator(".issue-card table.issue-table tbody tr:not(.issue-group-header)").filter({ has: page.locator(".issue-close-btn, button:has-text('Reopen')") });

section("leads page: save the proposal + one change, Run now");
{
  const { ctx, page } = await as(browser, "reg_admin", 1280);
  await go(page, "/issue-leads");
  await page.locator(".leads-table").waitFor({ timeout: 240000 });
  // Prod saved its leads on Oct 14: on a copy taken after that the page starts saved (no "Not saved" rows).
  const savedBefore = Number(q1("select count(*) from issue_leads"));
  const notSaved = await page.locator(".leads-table .pill", { hasText: "Not saved" }).count();
  if (savedBefore === 0) {
    check(/Nothing is assigned yet/.test(await page.locator("main").textContent()), "before saving: 'Nothing is assigned yet'");
    check(notSaved > 30, `proposal rows marked Not saved (${notSaved})`);
  } else {
    check(!/Nothing is assigned yet/.test(await page.locator("main").textContent()), `leads already saved on the copy (${savedBefore}): no 'Nothing is assigned yet'`);
  }
  const proposal = (await page.locator(".leads-proposal").first().textContent()) || "";
  check(/Document:|Guess|Not in the document/.test(proposal), `proposal line explains itself: "${proposal.trim().slice(0, 100)}"`);
  // give reg_lead the biggest section
  const rows = await page.locator(".leads-table tbody tr").evaluateAll((trs) => trs.map((tr) => ({ name: tr.querySelector(".cell-name")?.firstChild?.textContent?.trim(), items: Number(tr.querySelector(".num")?.textContent || 0) })));
  const big = rows.filter((r) => r.name).sort((a, b) => b.items - a.items)[0];
  await page.getByLabel(`Lead for ${big.name}`).selectOption(String(USERS.reg_lead.id));
  const saveBtn = page.locator(".leads-actions button").first();
  const label = (await saveBtn.textContent()) || "";
  check(/Save \d+ changes?/.test(label), `one Save for everything shown: "${label}"`);
  await saveBtn.click();
  await page.waitForFunction(() => /Saved/.test(document.querySelector(".leads-actions")?.textContent || ""), null, { timeout: 120000 });
  const saved = Number(q1("select count(*) from issue_leads"));
  check(saved >= notSaved, `rows saved in issue_leads (${saved})`);
  check(q1(`select user_id from issue_leads where kind = 'section' and display_name = '${big.name.replace(/'/g, "''")}'`) === String(USERS.reg_lead.id), `${big.name} -> reg_lead`);
  await shot(page, "leads-saved");
  // Run now (confirm dialog)
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: /Run now/ }).click();
  await page.waitForFunction(() => /Done: \d+ created/.test(document.body.textContent), null, { timeout: 240000 }).catch(() => {});
  const doneMsg = (await page.locator("text=/Done: \\d+ created/").textContent().catch(() => "")) || "";
  check(/Done: \d+ created/.test(doneMsg), `Run now: "${doneMsg}"`);
  const task = q1(`select title from tasks where source_type = 'issue_leads' and assignee_user_id = ${USERS.reg_lead.id}`);
  check(/Fix Issues: (\d+ open items? for you|\d+ left of \d+)/.test(task), `reg_lead got a task "${task}"`);
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: /Run now/ }).click();
  await page.waitForFunction(() => /Done: 0 created, \d+ updated/.test(document.body.textContent), null, { timeout: 240000 }).catch(() => {});
  check(q1(`select count(*) from tasks where source_type = 'issue_leads' and assignee_user_id = ${USERS.reg_lead.id}`) === "1", "Run now twice: still one task per lead (updated)");
  cleanPage(page, "leads page");
  await ctx.close();
}

section("admin 1280: every list loads, grouped by PO section");
{
  const { ctx, page } = await as(browser, "reg_admin", 1280);
  await openFI(page);
  const labels = await page.locator(".issue-tab-label").allTextContents();
  check(JSON.stringify(labels.slice(0, TABS.length)) === JSON.stringify(TABS), `tabs: ${labels.join(" | ")}`);
  for (const t of TABS) {
    await pickTab(page, t);
    const n = await rowsIn(page).count();
    const headers = await page.locator(".fi-section-header .fi-section-name").allTextContents();
    const count = (await page.locator(".issue-tab", { hasText: t }).locator(".issue-tab-count").textContent()) || "";
    if (n > 0) check(headers.length > 0, `${t} (${count}): grouped by PO section (${headers.length} groups, e.g. "${(headers[0] || "").trim()}")`);
    else check(/all clear|Nothing/.test(await page.locator(".issue-card").textContent()), `${t}: empty list says so`);
    if (t === "Dead - zero stock" && n) {
      await shot(page, "fi-dead-zero");
      await rowsIn(page).first().locator(".issue-item-link").click();
      const det = page.getByRole("dialog", { name: /details$/ });
      await det.waitFor();
      const facts = (await det.textContent()) || "";
      check(/Why it's listed/.test(facts) && /No sale in 90\+ days|Never sold|no sales in 60 days/i.test(facts), "Dead - zero stock: details say why it's listed");
      await det.getByRole("button", { name: "Close details" }).click();
    }
  }
  // filters on Dead with stock (biggest list)
  await pickTab(page, "Dead with stock");
  const filterCount = page.locator(".fi-filter-count");
  const all = await rowsIn(page).count();
  await page.locator(".fi-section-picker > button").click();
  const opt = page.locator(".fi-section-option").first();
  const secName = (await opt.locator("span").first().textContent()).trim();
  const secCount = Number((await opt.locator("span.muted").textContent()).trim());
  await opt.locator("input").check();
  await page.keyboard.press("Escape");
  await sleep(400);
  const shownTxt = (await filterCount.textContent().catch(() => "")) || "";
  check(new RegExp(`^${secCount} of \\d+ shown`).test(shownTxt.trim()), `section filter "${secName}": ${shownTxt.trim()} (expected ${secCount})`);
  const groupNames = (await page.locator(".fi-section-header .fi-section-name").allTextContents()).map((s) => s.replace(/\(\d+\)/, "").trim());
  check(groupNames.length === 1 && groupNames[0] === secName, `only that section is listed (${groupNames.join(", ")})`);
  await page.getByRole("button", { name: /Clear filters/ }).click();
  // vendor filter
  const vendorOpts = await page.getByLabel("Vendor", { exact: true }).locator("option").allTextContents();
  const v = vendorOpts.find((o) => /\(\d+\)$/.test(o) && !/All vendors/.test(o));
  const vName = v.replace(/ \(\d+\)$/, "");
  await page.getByLabel("Vendor", { exact: true }).selectOption({ label: v });
  await sleep(300);
  check(new RegExp(`^${v.match(/\((\d+)\)$/)[1]} of`).test((await filterCount.textContent()).trim()), `vendor filter ${v}: ${(await filterCount.textContent()).trim()}`);
  await page.getByRole("button", { name: /Clear filters/ }).click();
  // lead + task filters
  const leadOpts = await page.getByLabel("Lead", { exact: true }).locator("option").allTextContents();
  check(leadOpts.some((o) => /^reg_lead \(\d+\)$/.test(o)), `lead filter lists reg_lead (${leadOpts.join(", ")})`);
  const regOpt = leadOpts.find((o) => /^reg_lead/.test(o));
  if (regOpt) {
    await page.getByLabel("Lead", { exact: true }).selectOption({ label: regOpt });
    await sleep(500);
    const lines = await page.locator(".fi-lead-line").allTextContents();
    check(lines.length > 0 && lines.every((l) => /reg_lead/.test(l)), "every row shown is reg_lead's");
    check(lines.some((l) => /In task/.test(l)), "rows in the lead's task say In task");
    await page.getByLabel("Task", { exact: true }).selectOption({ label: "Not in a task yet" });
    await sleep(300);
    check(/^0 of/.test((await filterCount.textContent().catch(() => "0 of")).trim()) || (await rowsIn(page).count()) === 0 || /Nothing here matches/.test(await page.locator(".issue-card").textContent()), "reg_lead + Not in a task yet: nothing (all in today's task)");
    await page.getByRole("button", { name: /Clear filters/ }).click();
  }
  check((await rowsIn(page).count()) === all, "Clear filters restores the list");
  // code search
  const sku = q1(`select p.supplier_item_id from inventory_snapshots p where p.snapshot_date = (select max(snapshot_date) from inventory_snapshots) and p.supplier_item_id glob '*[0-9]*' and p.supplier_item_id not like '%;%' and length(p.supplier_item_id) > 4 and p.item_id in (select item_id from inventory_snapshots) limit 1`);
  await pickTab(page, "Missing barcodes");
  const name0 = (await rowsIn(page).first().locator(".issue-item-link").textContent()).trim();
  const id0 = q1(`select item_id from inventory_snapshots where name = '${name0.replace(/'/g, "''")}' order by snapshot_date desc limit 1`);
  await page.getByPlaceholder("Search name, SKU or barcode…").fill(id0.slice(0, 8));
  await sleep(500);
  const hits = await rowsIn(page).locator(".issue-item-link").allTextContents();
  check(hits.map((h) => h.trim()).includes(name0), `search by Toast id prefix ${id0.slice(0, 8)} finds ${name0}`);
  await page.getByPlaceholder("Search name, SKU or barcode…").fill("");
  // details drawer
  await rowsIn(page).first().locator(".issue-item-link").click();
  const det = page.getByRole("dialog", { name: /details$/ });
  await det.waitFor();
  await sleep(400);
  check(/Price and cost|Stock|Identifiers/.test(await det.textContent()), "details drawer shows product facts");
  await det.locator("text=Weekly sales").first().waitFor({ timeout: 60000 }).catch(() => {});
  check(/Weekly sales/.test(await det.textContent()), "details drawer has Weekly sales");
  await shot(page, "fi-details");
  await det.getByRole("button", { name: "Close details" }).click();
  cleanPage(page, "admin lists");
  await ctx.close();
}

section("close / bulk close / reopen / Closed report");
{
  const { ctx, page } = await as(browser, "reg_staff", 1280);
  await openFI(page);
  await pickTab(page, "Missing barcodes");
  const before = Number((await page.locator(".issue-tab", { hasText: "Missing barcodes" }).locator(".issue-tab-count").textContent()).trim());
  const r0 = rowsIn(page).first();
  const n0 = (await r0.locator(".issue-item-link").textContent()).trim();
  const OTHER_Q = "select count(*) from issue_resolutions where issue_type = 'missing_barcode' and reason = 'other'";
  const otherBase = Number(q1(OTHER_Q));
  await r0.getByRole("button", { name: "Close →" }).click();
  const drawer = page.locator("form.issue-drawer");
  await drawer.waitFor();
  await sleep(350);
  check(await drawer.getByRole("button", { name: "Close issue" }).isDisabled(), "Close issue needs a reason");
  const reasons = await drawer.locator(".issue-reason").allTextContents();
  check(reasons.length >= 3 && reasons.some((r) => /Other/.test(r)), `reasons offered (${reasons.length})`);
  await shot(page, "fi-close-drawer");
  await drawer.locator(".issue-reason", { hasText: "Other" }).locator("input").check();
  check(await drawer.getByRole("button", { name: "Close issue" }).isDisabled(), "Other needs details");
  await drawer.locator("textarea").fill("Regression: barcode added on the shelf tag");
  await drawer.getByRole("button", { name: "Close issue" }).click();
  await drawer.waitFor({ state: "detached", timeout: 60000 });
  // Relative to what the copy already holds (prod has its own "other" closes).
  check(await waitDb(OTHER_Q, otherBase + 1) === String(otherBase + 1), "close stored with reason other");
  await page.waitForFunction((b) => Number(document.querySelector(".issue-tab.active .issue-tab-count")?.textContent) === b - 1, before, { timeout: 60000 }).catch(() => {});
  check(Number((await page.locator(".issue-tab.active .issue-tab-count").textContent()).trim()) === before - 1, "tile count drops by 1");
  check(!(await rowsIn(page).locator(".issue-item-link").allTextContents()).map((s) => s.trim()).includes(n0), "row leaves the open list");
  // bulk close 2
  await rowsIn(page).nth(0).locator("input[type=checkbox]").check();
  await rowsIn(page).nth(1).locator("input[type=checkbox]").check();
  await page.getByRole("button", { name: "Close 2 selected" }).click();
  await drawer.waitFor();
  await drawer.locator(".issue-reason").first().locator("input").check();
  if (await drawer.locator("textarea").count()) await drawer.locator("textarea").fill("bulk");
  await drawer.getByRole("button", { name: "Close 2" }).click();
  await drawer.waitFor({ state: "detached", timeout: 60000 });
  check(await waitDb("select count(*) from issue_resolutions where issue_type = 'missing_barcode' and closed_at > datetime('now','-1 hour')", 3) === "3", "bulk close stored 2 more");
  // Closed view + reopen
  await page.locator(".issue-toolbar .segmented").getByRole("tab", { name: /^Closed/ }).click();
  await page.waitForFunction(() => document.querySelector(".issue-card table") || /Nothing closed/.test(document.querySelector(".issue-card")?.textContent || ""), null, { timeout: 60000 });
  const closedNames = (await page.locator(".issue-card .issue-item-link").allTextContents()).map((s) => s.trim());
  check(closedNames.includes(n0), "closed item listed under Closed with its reason");
  check(/Regression: barcode added/.test(await page.locator(".issue-card").textContent()), "closed row shows the detail");
  const reopenRow = page.locator(".issue-card table.issue-table tbody tr", { hasText: n0 }).first();
  await reopenRow.getByRole("button", { name: "Reopen" }).click();
  check(await waitDb(OTHER_Q, otherBase) === String(otherBase), "Reopen deletes the resolution");
  // Closed report
  await page.locator(".issue-tab", { hasText: "Closed report" }).click();
  await page.locator(".issue-report-day, .issue-report-summary").first().waitFor({ timeout: 60000 }).catch(() => {});
  check(/Missing barcodes|closed/i.test(await page.locator(".issue-card").textContent()), "Closed report lists today's closes");
  await shot(page, "fi-closed-report");
  cleanPage(page, "staff close flows");
  await ctx.close();
}

section("lead's view, hide_sales, demo, widths");
{
  const lead = await as(browser, "reg_lead", 375);
  await openFI(lead.page, `/fix-issues?lead=${USERS.reg_lead.id}`);
  check(/Showing\s+reg_lead's items/.test(await lead.page.locator(".fi-lead-banner").textContent().catch(() => "")), "?lead= shows the lead's banner");
  check((await hscroll(lead.page)) <= 1, "lead 375: no sideways scroll");
  await shot(lead.page, "fi-lead-375", { fullPage: false });
  await pickTab(lead.page, "Dead with stock");
  await lead.page.locator(".issue-card").scrollIntoViewIfNeeded();
  await shot(lead.page, "fi-lead-375-list", { fullPage: false });
  // the lead's task links to the list
  const taskId = q1(`select id from tasks where source_type = 'issue_leads' and assignee_user_id = ${USERS.reg_lead.id}`);
  check(/fix-issues\?lead=/.test(sql(`select description from tasks where id = ${taskId}`)), "task text links to the lead's Fix Issues list");
  cleanPage(lead.page, "lead view");
  await lead.ctx.close();

  for (const [user, width] of [["reg_hide", 1024], ["app_store_review", 375], ["reg_admin", 375], ["reg_admin", 1024]]) {
    const { ctx, page } = await as(browser, user, width);
    await openFI(page);
    await pickTab(page, "Negative inventory");
    check((await hscroll(page)) <= 1, `${user} ${width}: no sideways scroll`);
    if (user === "app_store_review") {
      const r = rowsIn(page).first();
      if (await r.count()) {
        await r.getByRole("button", { name: "Close →" }).click();
        const drawer = page.locator("form.issue-drawer");
        await drawer.locator(".issue-reason").first().locator("input").check();
        if (await drawer.locator("textarea").count()) await drawer.locator("textarea").fill("demo");
        const before = q1("select count(*) from issue_resolutions");
        await drawer.locator("button[type=submit]").click();
        await sleep(2000);
        check(q1("select count(*) from issue_resolutions") === before, "demo: close refused");
        check(/demo/i.test((await drawer.locator(".error").textContent().catch(() => "")) || ""), "demo: plain message in the panel");
        await drawer.locator(".issue-drawer-actions").getByRole("button", { name: "Cancel" }).click();
      }
    }
    await shot(page, "fi-negative", { fullPage: false });
    cleanPage(page, `${user} ${width} fix issues`, user === "app_store_review" ? [/^403 POST \/api\/issues/] : []);
    await ctx.close();
  }
}

await browser.close();
done();
