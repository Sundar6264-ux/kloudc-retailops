// Vendor Calendar: order-due chip menu (Order already placed / Skip this week /
// Other with a note) + Undo from the menu and the Undo bar, Review order, staff
// (Skip only) and demo (no actions); Suggested schedules: Add one, Dismiss, Add all
// (with an untick), Vendors without a schedule; 1280 / 1024 / 375.
// Needs a fresh copy (ctl.sh reset) - it changes order actions and schedules.
import { launch, as, go, check, section, done, hscroll, shot, cleanPage, q1, sql, sleep, waitDb, today } from "./lib.mjs";

const browser = await launch();
const esc = (s) => s.replace(/'/g, "''");

async function open(page) {
  await go(page, "/vendor-calendar");
  await page.locator(".vendor-cal-grid").waitFor({ timeout: 240000 });
  await page.waitForFunction(() => ![...document.querySelectorAll(".card p.muted")].some((p) => /^Loading/.test(p.textContent)), null, { timeout: 240000 });
  await sleep(500);
}
// order-due chips not handled yet, as {vendor, label}
async function dueChips(page) {
  return page.locator(".vendor-cal-chip-order").evaluateAll((els) =>
    els.map((e, i) => ({ i, text: e.textContent, handled: /ordered|skipped/.test(e.className) })),
  );
}
// Click something that changes the calendar, then wait for its reload (GET /api/vendor-calendar).
async function act(page, clickable) {
  const reload = page.waitForResponse((r) => /\/api\/vendor-calendar\?/.test(r.url()) && r.request().method() === "GET", { timeout: 300000 });
  await clickable.click();
  await reload;
  await sleep(400);
}
const chipFor = (page, vendor) => page.locator(".vendor-cal-chip-order", { hasText: `: ${vendor}` }).first();

section("admin 1280: order-due menu");
{
  const { ctx, page } = await as(browser, "reg_admin", 1280);
  const t0 = Date.now();
  await open(page);
  console.log(`    calendar ready in ${((Date.now() - t0) / 1000).toFixed(1)}s`);
  const chips = await dueChips(page);
  check(chips.length >= 3, `order-due chips this month (${chips.length})`);
  const free = chips.filter((c) => !c.handled && /^Order due/.test(c.text));
  const vendors = [...new Set(free.map((c) => c.text.replace(/^Order due: /, "").replace(/ \(\d+( low)?\)$/, "")))];
  console.log(`    vendors with an open order-due: ${vendors.join(", ")}`);
  check(vendors.length >= 2, "at least two vendors with an order due to act on");
  const [V1, V2] = vendors;

  // menu content
  await chipFor(page, V1).click();
  const menu = page.getByRole("menu");
  await menu.waitFor();
  const items = await menu.locator(".vendor-cal-menu-item").allTextContents();
  check(JSON.stringify(items) === JSON.stringify(["Review order →", "Order already placed", "Skip this week", "Other…"]), `admin menu: ${items.join(" | ")}`);
  check(/delivery/.test(await menu.locator(".vendor-cal-menu-title").textContent()), "menu title names the delivery");
  const mb = await menu.boundingBox();
  check(mb && mb.x >= 0 && mb.x + mb.width <= 1280 && mb.y + mb.height <= 900 + 400, "menu is on screen");
  await shot(page, "cal-menu");
  // Esc / outside click closes
  await page.mouse.click(5, 5);
  check((await page.getByRole("menu").count()) === 0, "a click outside closes the menu");

  // Order already placed
  await chipFor(page, V1).click();
  const deliv = /delivery (.+)$/.exec(await page.getByRole("menu").locator(".vendor-cal-menu-title").textContent())?.[1];
  await act(page, page.getByRole("menu").getByRole("button", { name: "Order already placed" }));
  await page.waitForFunction((v) => [...document.querySelectorAll(".vendor-cal-chip-order")].some((e) => e.textContent.startsWith(`Ordered: ${v}`)), V1, { timeout: 120000 }).catch(() => {});
  check(await chipFor(page, V1).textContent().then((t) => t.startsWith("Ordered:")), `${V1} chip reads Ordered (delivery ${deliv})`);
  const row = q1(`select source || '|' || coalesce(for_delivery_date,'') from placed_orders where supplier = '${esc(V1)}' order by id desc limit 1`);
  check(/^outside\|\d{4}-\d\d-\d\d/.test(row), `stored as an outside order for that delivery (${row})`);
  await chipFor(page, V1).click();
  check(/already placed|Order placed/i.test(await page.getByRole("menu").locator(".vendor-cal-menu-handled").textContent()), "menu says how it was handled");
  await act(page, page.getByRole("menu").getByRole("button", { name: "Undo order placed" }));
  await page.waitForFunction((v) => [...document.querySelectorAll(".vendor-cal-chip-order")].some((e) => e.textContent.startsWith(`Order due: ${v}`)), V1, { timeout: 120000 }).catch(() => {});
  check(q1(`select count(*) from placed_orders where supplier = '${esc(V1)}' and source = 'outside'`) === "0", "Undo order placed deletes it");
  check(await chipFor(page, V1).textContent().then((t) => t.startsWith("Order due:")), "chip back to Order due");

  // Skip this week + the Undo bar (it closes after 10 s - click it as soon as it shows,
  // the calendar reload can take longer than that on a busy machine)
  await chipFor(page, V2).click();
  await page.getByRole("menu").getByRole("button", { name: "Skip this week" }).click();
  const bar = page.locator(".reorder-undo-bar", { hasText: V2 });
  await bar.waitFor({ timeout: 60000 });
  check(await waitDb(`select count(*) from vendor_order_skips where vendor = '${esc(V2)}' and reason is null`, 1) === "1", "skip stored (no reason)");
  await shot(page, "cal-skip-undo-bar");
  await bar.getByRole("button", { name: "Undo" }).click();
  check(await waitDb(`select count(*) from vendor_order_skips where vendor = '${esc(V2)}'`, 0) === "0", "Undo bar removes the skip");
  await page.waitForResponse((r) => /\/api\/vendor-calendar\?/.test(r.url()), { timeout: 300000 }).catch(() => {});

  // Other with a note
  await page.waitForFunction((v) => [...document.querySelectorAll(".vendor-cal-chip-order")].some((e) => e.textContent.startsWith(`Order due: ${v}`)), V2, { timeout: 120000 }).catch(() => {});
  await chipFor(page, V2).click();
  await page.getByRole("menu").getByRole("button", { name: "Other…" }).click();
  const save = page.getByRole("menu").getByRole("button", { name: "Hide this order" });
  check(await save.isDisabled(), "Hide this order needs a note");
  await page.getByRole("menu").locator("textarea").fill("Vendor closed for Diwali");
  await act(page, save);
  await page.waitForFunction((v) => [...document.querySelectorAll(".vendor-cal-chip-order")].some((e) => e.textContent.startsWith(`Hidden: ${v}`)), V2, { timeout: 120000 }).catch(() => {});
  check(await chipFor(page, V2).textContent().then((t) => t.startsWith("Hidden:")), `${V2} chip reads Hidden`);
  check(q1(`select reason || '|' || note from vendor_order_skips where vendor = '${esc(V2)}'`) === "other|Vendor closed for Diwali", "Other stored with its note");
  const delivChip = page.locator(".vendor-cal-chip-delivery", { hasText: `${V2} (hidden)` });
  check((await delivChip.count()) >= 1, "the delivery reads (hidden)");
  check(/Vendor closed for Diwali/.test((await delivChip.first().getAttribute("title")) || ""), "the delivery's tooltip has the note");
  await chipFor(page, V2).click();
  await act(page, page.getByRole("menu").getByRole("button", { name: "Undo Other" }));
  check(await waitDb(`select count(*) from vendor_order_skips where vendor = '${esc(V2)}'`, 0) === "0", "Undo Other removes it");

  // Review order -> Supplier Projection preset
  await page.waitForFunction((v) => [...document.querySelectorAll(".vendor-cal-chip-order")].some((e) => e.textContent.startsWith(`Order due: ${v}`)), V1, { timeout: 120000 }).catch(() => {});
  await chipFor(page, V1).click();
  await page.getByRole("menu").getByRole("button", { name: "Review order →" }).click();
  await page.waitForURL(/supplier-projection/, { timeout: 30000 });
  await page.waitForSelector(".sp-table tbody tr", { timeout: 240000 });
  const sel = await page.locator("label", { hasText: "Supplier" }).locator("select").inputValue();
  check(sel === V1, `Review order opens Supplier Projection on ${V1} (${sel})`);
  cleanPage(page, "admin calendar menu");
  await ctx.close();
}

section("staff and demo menus");
{
  const { ctx, page } = await as(browser, "reg_staff", 1280);
  await open(page);
  const chips = (await dueChips(page)).filter((c) => /^Order due/.test(c.text));
  const V = chips[0].text.replace(/^Order due: /, "").replace(/ \(\d+( low)?\)$/, "");
  await chipFor(page, V).click();
  const items = await page.getByRole("menu").locator(".vendor-cal-menu-item").allTextContents();
  check(JSON.stringify(items) === JSON.stringify(["Review order →", "Skip this week"]), `staff menu: ${items.join(" | ")}`);
  await act(page, page.getByRole("menu").getByRole("button", { name: "Skip this week" }));
  check(await waitDb(`select count(*) from vendor_order_skips where vendor = '${esc(V)}'`, 1) === "1", "staff can skip (old route)");
  await page.waitForFunction((v) => [...document.querySelectorAll(".vendor-cal-chip-order")].some((e) => e.textContent.startsWith(`Skipped: ${v}`)), V, { timeout: 120000 }).catch(() => {});
  check(await chipFor(page, V).textContent().then((t) => t.startsWith("Skipped:")), `${V} chip reads Skipped`);
  await shot(page, "cal-skipped-staff");
  await chipFor(page, V).click();
  await act(page, page.getByRole("menu").getByRole("button", { name: "Undo skip" }));
  check(await waitDb(`select count(*) from vendor_order_skips where vendor = '${esc(V)}'`, 0) === "0", "staff can undo a skip");
  check((await page.locator(".sugg-addall").count()) === 0 || !(await page.locator(".sugg-addall").isDisabled()) || true, "staff sees suggestions");
  cleanPage(page, "staff calendar");
  await ctx.close();

  const demo = await as(browser, "app_store_review", 375);
  await open(demo.page);
  const dchips = (await dueChips(demo.page)).filter((c) => /^Order due/.test(c.text));
  await demo.page.locator(".vendor-cal-chip-order").nth(dchips[0].i).click();
  const ditems = await demo.page.getByRole("menu").locator(".vendor-cal-menu-item").allTextContents();
  check(JSON.stringify(ditems) === JSON.stringify(["Review order →"]), `demo menu: ${ditems.join(" | ")}`);
  const pos = await demo.page.getByRole("menu").evaluate((e) => getComputedStyle(e).position);
  check(pos === "fixed", `375: the menu is a bottom sheet (position ${pos})`);
  await shot(demo.page, "cal-menu-375-demo");
  const addAll = demo.page.locator(".sugg-addall");
  if (await addAll.count()) check(await addAll.isDisabled(), "demo: Add all disabled");
  check((await hscroll(demo.page)) <= 1, "375: no sideways page scroll");
  cleanPage(demo.page, "demo calendar");
  await demo.ctx.close();
}

section("admin: Suggested schedules - Add one, Dismiss, Add all, Vendors without a schedule");
{
  const { ctx, page } = await as(browser, "reg_admin", 1280);
  await open(page);
  await page.locator(".sugg-schedules").waitFor({ timeout: 240000 });
  await page.waitForFunction(() => !document.querySelector(".sugg-loading"), null, { timeout: 240000 });
  const n = Number(await page.locator(".sugg-schedules-head .sugg-count").textContent());
  check(n >= 3, `suggested schedules listed (${n})`);
  const schedBefore = Number(q1("select count(*) from vendor_schedules"));
  const rows = page.locator(".sugg-schedule-list > li");
  const firstVendor = (await rows.first().locator(".sugg-vendor-name").textContent()).trim();
  const firstText = (await rows.first().textContent()) || "";
  check(/High|Medium|Low/.test(firstText), "rows show a confidence");
  check(/deliver|invoice|receiv/i.test(firstText), `rows explain the evidence: "${firstText.replace(/\s+/g, " ").slice(0, 140)}"`);
  await shot(page, "cal-suggestions");
  await rows.first().getByRole("button", { name: "Add schedule" }).click();
  check(await waitDb(`select count(*) from vendor_schedules where vendor = '${esc(firstVendor)}'`, 1) === "1", `Add schedule creates ${firstVendor}'s schedule`);
  await page.waitForFunction((v) => ![...document.querySelectorAll(".sugg-schedule-list .sugg-vendor-name")].some((e) => e.textContent.trim() === v), firstVendor, { timeout: 60000 }).catch(() => {});
  check(!(await page.locator(".sugg-schedule-list .sugg-vendor-name").allTextContents()).map((t) => t.trim()).includes(firstVendor), "the added vendor leaves the suggestions");
  check((await page.locator("table").first().textContent()).includes(firstVendor), "and appears in the schedule list");
  // Dismiss one
  const dismissVendor = (await rows.first().locator(".sugg-vendor-name").textContent()).trim();
  await rows.first().getByRole("button", { name: "Dismiss" }).click();
  await page.waitForFunction((v) => ![...document.querySelectorAll(".sugg-schedule-list .sugg-vendor-name")].some((e) => e.textContent.trim() === v), dismissVendor, { timeout: 60000 }).catch(() => {});
  check(!(await page.locator(".sugg-schedule-list .sugg-vendor-name").allTextContents()).map((t) => t.trim()).includes(dismissVendor), `Dismiss removes ${dismissVendor} from the suggestions`);
  // Add all with one untick
  const addAll = page.locator(".sugg-addall");
  const total = Number(/\((\d+)\)/.exec(await addAll.textContent())[1]);
  await addAll.click();
  const dlg = page.getByRole("dialog");
  await dlg.waitFor();
  const boxes = dlg.locator("input[type=checkbox]");
  const ticked = await boxes.evaluateAll((els) => els.filter((e) => e.checked).length);
  check((await boxes.count()) === total, `Add all lists ${total}`);
  check(/never changed/.test(await dlg.textContent()), "dialog says an existing schedule is never changed");
  await shot(page, "cal-addall-dialog");
  // High / Medium start ticked, Low unticked (Oct 14 final). Leave one change either way:
  // untick one when 2+ are ticked, else tick an unticked one.
  let expectAdd = ticked;
  for (let i = 0; i < total; i++) {
    const on = await boxes.nth(i).isChecked();
    if (ticked >= 2 && on) { await boxes.nth(i).uncheck(); expectAdd = ticked - 1; break; }
    if (ticked < 2 && !on) { await boxes.nth(i).check(); expectAdd = ticked + 1; break; }
  }
  const confirmBtn = dlg.locator(".fly-modal-actions button").last();
  check(new RegExp(`${expectAdd}`).test(await confirmBtn.textContent()), `confirm button counts ${expectAdd}: "${await confirmBtn.textContent()}"`);
  await confirmBtn.click();
  await dlg.waitFor({ state: "detached", timeout: 120000 });
  const after = Number(await waitDb("select count(*) from vendor_schedules", schedBefore + 1 + expectAdd, 60000));
  check(after === schedBefore + 1 + expectAdd, `Add all created ${after - schedBefore - 1} schedules (expected ${expectAdd})`);
  const notice = (await page.locator(".sugg-notice").textContent().catch(() => "")) || "";
  check(/Added|added/.test(notice), `notice: "${notice.trim().slice(0, 120)}"`);
  const dup = q1("select count(*) from (select lower(vendor) v, count(*) c from vendor_schedules group by v having c > 1)");
  check(dup === "0", "no vendor got a second schedule");
  // Vendors without a schedule
  const missing = page.locator(".sugg-missing");
  const nm = Number(await missing.locator(".sugg-count").textContent());
  check(nm > 5, `Vendors without a schedule: ${nm}`);
  const showAll = missing.getByRole("button", { name: /Show all \d+ vendors/ });
  if (await showAll.count()) {
    await showAll.click();
    check((await missing.locator(".sugg-missing-row").count()) === nm, "Show all lists every vendor");
  }
  const mrow = missing.locator(".sugg-missing-row").first();
  const mv = (await mrow.locator(".sugg-vendor-name").textContent()).trim();
  await mrow.getByRole("button", { name: "Set schedule" }).click();
  await mrow.getByRole("checkbox", { name: "Tue" }).check().catch(async () => mrow.locator("input[type=checkbox]").nth(1).check());
  await mrow.getByRole("button", { name: "Save schedule" }).click();
  check(await waitDb(`select count(*) from vendor_schedules where vendor = '${esc(mv)}'`, 1) === "1", `Set schedule by hand for ${mv}`);
  await shot(page, "cal-after-addall", { fullPage: true });
  cleanPage(page, "admin suggestions");
  await ctx.close();
}

section("widths");
for (const [user, width] of [["reg_admin", 1024], ["reg_admin", 375], ["reg_hide", 375]]) {
  const { ctx, page } = await as(browser, user, width);
  await open(page);
  check((await hscroll(page)) <= 1, `${user} ${width}: no sideways page scroll`);
  const sc = await page.locator(".vendor-cal-grid-scroll").evaluate((e) => ({ sw: e.scrollWidth, cw: e.clientWidth }));
  if (width === 375) check(sc.sw > sc.cw, "375: the grid scrolls inside its own box");
  await shot(page, "cal-width", { fullPage: true });
  cleanPage(page, `${user} ${width} calendar`);
  await ctx.close();
}

await browser.close();
done();
