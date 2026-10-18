// The other Suite A pages: Inventory from Toast (switch + readiness), Delivery Review
// suggestions, Invoices (Transfer type, Looks closed in Toast), Items Sold + Sales
// accuracy, Open Checks (tab names, from the mock Toast), PO sections, Placed Orders
// (outside orders, Order again). Needs a fresh copy (ctl.sh reset).
import { launch, as, go, check, section, done, hscroll, shot, cleanPage, q1, sql, sleep, waitDb, api, today, WORK } from "./lib.mjs";
import fs from "node:fs";

const browser = await launch();
const esc = (s) => s.replace(/'/g, "''");

section("Inventory from Toast: switch on / off, readiness, live counts on pages");
{
  const { ctx, page } = await as(browser, "reg_admin", 1280);
  await go(page, "/inventory-api");
  await page.locator(".invapi-switch-card").waitFor({ timeout: 120000 });
  await page.waitForFunction(() => !/Loading…/.test(document.querySelector(".invapi-switch-card")?.textContent || "Loading…"), null, { timeout: 120000 });
  // the status (readiness, live screens) is a separate, slower request
  const t0 = Date.now();
  await page.getByRole("heading", { name: "Readiness" }).waitFor({ timeout: 180000 }).catch(() => {});
  console.log(`    status (readiness) in ${((Date.now() - t0) / 1000).toFixed(1)}s`);
  const card = page.locator(".invapi-switch-card");
  check(/Off/.test(await card.locator(".pill").textContent()), "switch starts Off (env default)");
  check(/Readiness: \d+ \/ \d+ nights/.test(await card.textContent()), `readiness line: "${((await card.textContent()) || "").match(/Readiness[^]*?nights( - ready)?/)?.[0]}"`);
  check(await page.getByRole("heading", { name: "Readiness" }).isVisible(), "Readiness card shown");
  await shot(page, "invapi-off");
  await card.getByRole("button", { name: "Turn on…" }).click();
  const dlg = page.getByRole("dialog", { name: "Turn on Inventory from Toast?" });
  await dlg.waitFor();
  const dtext = (await dlg.textContent()) || "";
  check(/takes effect right away/.test(dtext) && /current count/.test(dtext), "dialog says what changes");
  check((await dlg.locator(".invapi-list .invapi-list li").count()) >= 3, "dialog lists the screens that read live counts");
  await dlg.getByPlaceholder(/7 good nights/).fill("regression test");
  await shot(page, "invapi-dialog");
  await dlg.getByRole("button", { name: "Turn on" }).click();
  await dlg.waitFor({ state: "detached", timeout: 60000 });
  await page.waitForFunction(() => /\bOn\b/.test(document.querySelector(".invapi-switch-card .pill")?.textContent || ""), null, { timeout: 60000 }).catch(() => {});
  check(/On/.test(await card.locator(".pill").textContent()), "switch now On");
  check(/"value":\s*true|true/.test(q1("select value from setting_changes order by id desc limit 1")), "stored in setting_changes");
  check(/regression test/.test(await page.locator("main").textContent()), "switch history shows the note");
  // a page that reads the live overlay says so
  await go(page, "/supplier-projection");
  await page.locator("label", { hasText: "Supplier" }).locator("select").selectOption("Mysha Distributors LLC");
  await page.getByRole("button", { name: "Generate projection" }).click();
  await page.waitForSelector(".sp-table tbody tr", { timeout: 240000 });
  const note = (await page.locator("main").textContent()) || "";
  check(/live count|Toast's live|live stock/i.test(note), "Supplier Projection says on hand is Toast's live count");
  const calls = fs.readFileSync(`${WORK}/toast_calls.jsonl`, "utf8");
  check(/\/stock\/v1\/inventory/.test(calls), "the live overlay asked the (mock) Toast stock API");
  await go(page, "/inventory-api");
  await page.locator(".invapi-switch-card").waitFor({ timeout: 120000 });
  await page.getByRole("button", { name: "Turn off" }).click();
  await page.waitForFunction(() => /\bOff\b/.test(document.querySelector(".invapi-switch-card .pill")?.textContent || ""), null, { timeout: 60000 }).catch(() => {});
  check(/Off/.test(await page.locator(".invapi-switch-card .pill").textContent()), "Turn off is immediate");
  cleanPage(page, "inventory api admin");
  await ctx.close();
  const staff = await as(browser, "reg_staff", 1280);
  const r = await api(staff.page, "PUT", "/api/inventory-api/switch", { on: true });
  check(r.status === 403 || r.status === 404 || r.status === 405, `staff cannot flip the switch (${r.status})`);
  await staff.ctx.close();
}

section("Delivery Review: suggested deliveries - confirm & log, dismiss + undo");
{
  const { ctx, page } = await as(browser, "reg_admin", 1280);
  await go(page, "/delivery-review");
  await page.waitForFunction(() => !/Looking through the latest uploads/.test(document.body.textContent), null, { timeout: 240000 });
  // widest window, so there is something to confirm on any copy
  const win = page.getByLabel("How many uploads to look at");
  const opts = await win.locator("option").evaluateAll((os) => os.map((o) => o.value));
  await win.selectOption(opts[opts.length - 1]);
  await page.waitForFunction(() => !/Looking through the latest uploads/.test(document.body.textContent), null, { timeout: 240000 });
  await sleep(500);
  const groups = page.locator(".sugg-group-head");
  const n = await groups.count();
  if (n === 0) check(/Nothing to confirm up to/.test(await page.locator("main").textContent()), "no suggestions: says nothing to confirm (and how many are already logged)");
  else check(n >= 1, `suggested deliveries listed (${n})`);
  if (n >= 2) {
    await groups.nth(0).click();
    const panel = page.locator(".sugg-group").first();
    const confirm = page.getByRole("button", { name: /^Confirm & log \d+ items?/ }).first();
    await confirm.waitFor({ timeout: 30000 });
    const label = await confirm.textContent();
    const k = Number(/log (\d+)/.exec(label)[1]);
    const before = Number(q1("select count(*) from purchase_log"));
    await shot(page, "delivery-suggestion-open");
    await confirm.click();
    const after = Number(await waitDb("select count(*) from purchase_log", before + k, 60000));
    check(after === before + k, `Confirm & log wrote ${after - before} purchase_log rows (button said ${k})`);
    const left = await groups.count();
    check(left === n - 1, `the confirmed suggestion leaves the list (${left})`);
    // dismiss + undo
    await groups.nth(0).click();
    await page.getByRole("button", { name: "Not a delivery - dismiss" }).first().click();
    await page.locator(".sugg-notice, [role=status]").filter({ hasText: /Undo|dismiss/i }).first().waitFor({ timeout: 60000 }).catch(() => {});
    check((await groups.count()) === n - 2, "dismissed suggestion hidden");
    const undo = page.getByRole("button", { name: "Undo" }).first();
    if (await undo.count()) {
      await undo.click();
      await page.waitForFunction((m) => document.querySelectorAll(".sugg-group-head").length === m, n - 1, { timeout: 60000 }).catch(() => {});
      check((await groups.count()) === n - 1, "Undo brings it back");
    }
  }
  check((await hscroll(page)) <= 1, "no sideways scroll");
  cleanPage(page, "delivery review");
  await ctx.close();
  const demo = await as(browser, "app_store_review", 375);
  await go(demo.page, "/delivery-review");
  await demo.page.waitForFunction(() => !/Looking through the latest uploads/.test(document.body.textContent), null, { timeout: 240000 });
  const c = demo.page.getByRole("button", { name: /^Confirm & log/ }).first();
  if (await demo.page.locator(".sugg-group-head").count()) {
    await demo.page.locator(".sugg-group-head").first().click();
    check(await c.isDisabled(), "demo: Confirm & log disabled");
  }
  check((await hscroll(demo.page)) <= 1, "demo 375: no sideways scroll");
  await shot(demo.page, "delivery-demo-375");
  cleanPage(demo.page, "delivery review demo");
  await demo.ctx.close();
}

section("Invoices: Transfer type, Looks closed in Toast");
{
  const { ctx, page } = await as(browser, "reg_staff", 1280);
  await go(page, "/invoices");
  await page.locator(".invoice-type-options").first().waitFor({ timeout: 120000 });
  await page.locator(".invoice-type-option", { hasText: "Transfer" }).click();
  check(/optional for a transfer/.test(await page.locator("main").textContent()), "Transfer makes the vendor optional");
  const dirs = await page.locator(".invoice-type-options").nth(1).locator(".invoice-type-option").allTextContents();
  check(dirs.length === 2 && dirs.every((d) => /Store|Container/.test(d)), `direction choices: ${dirs.join(" | ")}`);
  await page.locator(".invoice-type-options").nth(1).locator(".invoice-type-option").first().click();
  // a small real JPEG
  const jpg = `${WORK}/transfer.jpg`;
  if (!fs.existsSync(jpg)) {
    fs.writeFileSync(jpg, Buffer.from("/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA=", "base64"));
  }
  await page.locator("input[type=file]").first().setInputFiles(jpg);
  const before = Number(q1("select count(*) from invoice_submissions"));
  await page.getByRole("button", { name: /^Submit/ }).first().click();
  const after = Number(await waitDb("select count(*) from invoice_submissions", before + 1, 90000));
  check(after === before + 1, "transfer submitted");
  check(q1("select submission_type || '|' || coalesce(transfer_direction,'') from invoice_submissions order by id desc limit 1").startsWith("transfer|"), "stored as a transfer with its direction");
  await sleep(1500);
  check(/Transfer: (Store|Container)/.test(await page.locator("main").textContent()), "the list shows 'Transfer: Store -> Container'");
  await shot(page, "invoices-transfer");
  cleanPage(page, "invoices transfer");
  await ctx.close();

  const adm = await as(browser, "reg_admin", 1280);
  await go(adm.page, "/invoices");
  await sleep(2500);
  const card = adm.page.locator(".sugg-card", { hasText: "Looks closed in Toast" });
  if (await card.count()) {
    const rows = card.locator(".sugg-close-row");
    const n = await rows.count();
    check(n >= 1, `Looks closed in Toast lists ${n}`);
    const v0 = (await rows.first().locator("strong").textContent()).trim();
    await rows.first().getByRole("button", { name: "Not closed yet" }).click();
    await card.locator(".sugg-notice").waitFor({ timeout: 30000 });
    check(new RegExp(`Hid the suggestion for ${v0.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`).test(await card.textContent()), "Not closed yet hides it with Undo");
    await card.getByRole("button", { name: "Undo" }).click();
    await adm.page.waitForFunction((m) => document.querySelectorAll(".sugg-close-row").length === m, n, { timeout: 30000 }).catch(() => {});
    check((await rows.count()) === n, "Undo brings it back");
    await rows.first().getByRole("button", { name: "Mark closed in Toast" }).click();
    await adm.page.waitForFunction((m) => document.querySelectorAll(".sugg-close-row").length === m - 1, n, { timeout: 30000 }).catch(() => {});
    check((await rows.count()) === n - 1, "Mark closed in Toast takes it off the list");
    await shot(adm.page, "invoices-close-sugg");
  } else {
    console.log("    (no 'Looks closed in Toast' suggestions on this copy)");
  }
  cleanPage(adm.page, "invoices admin");
  await adm.ctx.close();
}

section("Items Sold + Sales accuracy");
{
  // Items Sold is nothing but sales: a Hide sales user doesn't get it even when granted
  const { ctx, page } = await as(browser, "reg_hide", 1280);
  await go(page, "/items-sold");
  check(!page.url().includes("/items-sold"), `hide_sales: Items Sold redirects (${page.url().split("#")[1]})`);
  check((await api(page, "GET", "/api/sales/items-sold")).status >= 400, "hide_sales: the Items Sold API refuses");
  await ctx.close();
}
for (const [user, width] of [["reg_admin", 1280], ["reg_staff", 375]]) {
  const { ctx, page } = await as(browser, user, width);
  await go(page, "/items-sold");
  await page.locator(".stat-card-label", { hasText: "Items sold" }).waitFor({ timeout: 120000 }).catch(() => {});
  await sleep(1000);
  const text = (await page.locator("main").textContent()) || "";
  check(/Items sold/.test(text), `${user}: Items Sold loads today (live from the mock Toast)`);
  if (user === "reg_hide") check(!/Revenue/.test(text) && !/\$\d/.test(text), "hide_sales: no revenue anywhere on Items Sold");
  else check(/Revenue/.test(text), `${user}: revenue shown`);
  if (user === "reg_admin") {
    check(/Sales accuracy/.test(text), "admin: Sales accuracy card");
    // last 7 days range from stored data
    const sel = page.locator("select").first();
    const opts = await sel.locator("option").allTextContents();
    const seven = opts.find((o) => /7 days/i.test(o));
    if (seven) {
      await sel.selectOption({ label: seven });
      await sleep(2500);
      check(Number(((await page.locator(".stat-card").first().textContent()) || "").replace(/\D/g, "")) > 0, "a 7-day range has sales");
    }
    // code search on Items Sold
    const sku = q1(`select s.supplier_item_id from orders o join inventory_snapshots s on s.item_id = o.item_guid where o.business_date = date('now','-2 day') and s.supplier_item_id glob '*[0-9]*' and s.supplier_item_id not like '%;%' and length(s.supplier_item_id) > 3 limit 1`);
    const box = page.locator("input.table-search").first();
    if (sku && (await box.count())) {
      await box.fill(sku);
      await sleep(600);
      const rows = await page.locator("main table tbody tr").count();
      check(rows >= 1, `Items Sold finds rows by SKU ${sku} (${rows})`);
    }
  } else {
    check(!/Sales accuracy/.test(text), `${user}: no Sales accuracy card`);
  }
  check((await hscroll(page)) <= 1, `${user} ${width}: no sideways scroll`);
  await shot(page, "items-sold");
  cleanPage(page, `${user} items sold`, user === "reg_hide" ? [/^403 /] : []);
  await ctx.close();
}

section("Open Checks: tab names from Toast");
for (const [user, width] of [["reg_staff", 1280], ["reg_admin", 375]]) {
  const { ctx, page } = await as(browser, user, width);
  await go(page, "/open-orders");
  const allTime = page.getByLabel("All-time");
  if (await allTime.isChecked()) await allTime.uncheck();
  await page.waitForFunction(() => /Ravi Kumar|No open checks/.test(document.body.textContent), null, { timeout: 120000 }).catch(() => {});
  const text = (await page.locator("main").textContent()) || "";
  check(/Ravi Kumar/.test(text), `${user}: open check #41 shows tab "Ravi Kumar"`);
  check(/Priya/.test(text) && !/Priya, priya/i.test(text), "check #42: 'Priya' once (case-insensitive de-dupe)");
  check(!/Priya, 42|42, Priya/.test(text), "a tab named like the check number is left out");
  check(!/reg-paid-1|#43\b/.test(text), "the paid order is not an open check");
  const search = page.getByPlaceholder("Search check #, tab name or employee…");
  if (await search.count()) {
    await search.fill("ravi");
    await sleep(400);
    const rows = await page.locator("main table tbody tr").count();
    check(rows === 1, `search by tab name finds 1 (${rows})`);
  }
  check((await hscroll(page)) <= 1, `${user} ${width}: no sideways scroll`);
  await shot(page, "open-checks");
  cleanPage(page, `${user} open checks`);
  await ctx.close();
}

section("PO sections");
{
  const { ctx, page } = await as(browser, "reg_admin", 1280);
  await go(page, "/po-sections");
  await page.waitForSelector("main table", { timeout: 120000 });
  const tabs = await page.getByRole("tablist", { name: "Show" }).getByRole("tab").allTextContents();
  check(tabs.length >= 2, `PO sections views: ${tabs.join(" | ")}`);
  await page.getByLabel("Vendor", { exact: true }).selectOption("Chetak").catch(() => {});
  await sleep(1500);
  const rows = await page.locator("main table tbody tr").count();
  check(rows > 0, `vendor filter Chetak lists ${rows}`);
  const sku = q1(`select supplier_item_id from inventory_snapshots where supplier = 'Chetak' and supplier_item_id glob '*[0-9]*' and supplier_item_id not like '%;%' and length(supplier_item_id) > 3 order by snapshot_date desc limit 1`);
  const box = page.locator("main input[type=text]").first();
  if (sku && (await box.count())) {
    await box.fill(sku);
    await sleep(2000);
    check((await page.locator("main table tbody tr").count()) >= 1, `PO sections finds Chetak SKU ${sku}`);
  }
  await shot(page, "po-sections");
  cleanPage(page, "po sections");
  await ctx.close();
}

section("Placed Orders: outside orders, Order again, filters");
{
  const { ctx, page } = await as(browser, "reg_admin", 1280);
  // an "Order already placed" row via the API (as the admin)
  const r = await api(page, "POST", "/api/order-actions", { vendor: "Babco", action: "placed" });
  check(r.status === 200, `order action placed for Babco (${r.status})`);
  await go(page, "/placed-orders");
  await page.waitForSelector("main table tbody tr", { timeout: 120000 });
  const first = page.locator("main table tbody tr").first();
  const t = (await first.textContent()) || "";
  check(/Babco/.test(t) && /Placed outside the app/.test(t), `newest row is Babco 'Placed outside the app': "${t.replace(/\s+/g, " ").slice(0, 120)}"`);
  check(!/\b0 items?\b/.test(t), "an outside order never reads '0 items'");
  await first.locator("button.sp-value-link").click();
  await page.locator(".placed-outside-detail").waitFor({ timeout: 30000 }).catch(() => {});
  check(/reg_admin/.test((await page.locator(".placed-outside-detail").textContent().catch(() => "")) || ""), `expanded outside order says who marked it: "${((await page.locator(".placed-outside-detail").textContent().catch(() => "")) || "").trim().slice(0, 100)}"`);
  check((await page.getByRole("button", { name: "Order again" }).count()) === 0, "an outside order has no Order again (nothing to re-add)");
  await first.locator("button.sp-value-link").click();
  await page.getByRole("tab", { name: "PDF only" }).click();
  await sleep(1500);
  await page.getByRole("tab", { name: "Placed" }).click();
  await sleep(1500);
  // Order again on a cart order
  await page.locator("main table tbody tr").filter({ hasNotText: "outside" }).first().locator("button.sp-value-link").click();
  const again = page.getByRole("button", { name: "Order again" }).first();
  await again.waitFor({ timeout: 30000 }).catch(() => {});
  if (await again.count()) {
    const before = Number(q1("select count(*) from po_cart_items"));
    await again.click();
    await page.getByText(/Added to .*'s cart - /).first().waitFor({ timeout: 60000 }).catch(() => {});
    check(Number(q1("select count(*) from po_cart_items")) > before, "Order again puts items back in the cart");
    const link = page.getByRole("link", { name: "open the cart" }).first();
    if (await link.count()) {
      await link.click();
      await page.waitForSelector(".po-vendor", { timeout: 60000 });
      await sleep(1000);
      check((await page.locator(".po-vendor.is-open").count()) === 1, "open the cart opens that vendor");
    }
  } else {
    check(false, "an Order again button");
  }
  cleanPage(page, "placed orders");
  await ctx.close();
  for (const [user, width] of [["reg_staff", 375], ["app_store_review", 1024]]) {
    const s = await as(browser, user, width);
    await go(s.page, "/placed-orders");
    await s.page.waitForSelector("main table tbody tr", { timeout: 120000 });
    check((await hscroll(s.page)) <= 1, `${user} ${width}: no sideways scroll`);
    await shot(s.page, "placed-orders");
    cleanPage(s.page, `${user} placed orders`);
    await s.ctx.close();
  }
}

await browser.close();
done();
