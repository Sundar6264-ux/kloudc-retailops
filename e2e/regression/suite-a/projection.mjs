// Supplier Projection: suggested qty boxes with their basis, Add to cart, in-cart
// rows (In cart: N + Update, never re-sent), weekly sales with Avg on hand, the
// qty column on screen at every width, hide-sales / viewer / demo.
// Needs a fresh copy (ctl.sh reset) - it changes the cart.
import { launch, as, go, check, section, done, hscroll, shot, cleanPage, q1, sleep, api } from "./lib.mjs";

const VENDOR = "Mysha Distributors LLC";
const browser = await launch();

async function run(page, vendor = VENDOR) {
  await go(page, "/supplier-projection", { wait: "select" });
  const sel = page.locator("label", { hasText: "Supplier" }).locator("select");
  await sel.selectOption(vendor);
  await page.getByRole("button", { name: "Generate projection" }).click();
  await page.waitForSelector(".sp-table tbody tr", { timeout: 240000 });
  await sleep(500);
}
// A projection row by its item name (the first text node of the name cell).
const rowOf = (page, name) =>
  page.locator(`xpath=//table[contains(@class,'sp-table')]/tbody/tr[td[contains(@class,'cell-name') and normalize-space(text()[1])='${name}']]`).first();
const nameOf = (row) => row.locator("td.cell-name").evaluate((td) => (td.firstChild?.textContent || "").trim());

section("admin 1280: suggestions, basis, add, in-cart update");
{
  const { ctx, page } = await as(browser, "reg_admin", 1280);
  await run(page);
  const total = await page.locator(".sp-table tbody tr:not(.sp-weekly-row)").count();
  check(total > 10, `projection lists ${total} items`);
  const note = (await page.locator(".sp-order-window").textContent().catch(() => "")) || "";
  check(/suggested order qty\s*i? for .+/i.test(note), `order window explained: "${note.trim().slice(0, 140)}"`);
  // in-cart rows: pre-ticked, "In cart: N", Update
  const inCartRows = page.locator(".sp-table tbody tr", { has: page.locator(".sp-qty-incart") });
  const nIn = await inCartRows.count();
  const dbIn = Number(q1(`select count(*) from po_cart_items where supplier = '${VENDOR}' and item_id is not null`));
  check(nIn > 0 && nIn <= dbIn, `in-cart rows show "In cart: N" (${nIn}, cart has ${dbIn})`);
  if (nIn) {
    const r = inCartRows.first();
    const txt = (await r.locator(".sp-qty-incart").textContent()) || "";
    check(/In cart: \d/.test(txt), `"${txt}"`);
    check(await r.getByRole("button", { name: "Update" }).isDisabled(), "Update disabled until the box changes");
    check(await r.locator(".sp-qty-basis").count() === 1, "in-cart row shows its basis line");
  }
  // a not-in-cart row with a numeric suggestion
  const cand = page.locator(".sp-table tbody tr", { has: page.locator(".sp-qty-hint") }).filter({ hasNotText: "(in cart)" });
  let picked = null;
  for (let i = 0; i < Math.min(await cand.count(), 40); i++) {
    const hint = ((await cand.nth(i).locator(".sp-qty-hint").textContent()) || "").trim();
    const nm = await nameOf(cand.nth(i));
    if (/^\d+(\.\d+)?$/.test(hint) && Number(hint) > 0 && !nm.includes("'")) {
      picked = { row: rowOf(page, nm), hint: Number(hint), name: nm };
      break;
    }
  }
  check(!!picked, `a row with a suggested qty (${picked && picked.hint})`);
  if (picked) {
    const itemName = picked.name;
    const hintTitle = await picked.row.locator(".sp-qty-hint").getAttribute("title");
    check(/sold|suggested|on hand/i.test(hintTitle || ""), `unticked hint has its basis on hover: "${(hintTitle || "").slice(0, 120)}"`);
    await picked.row.locator("input[type=checkbox]").check();
    const box = picked.row.locator("input.sp-qty-input");
    check(Number(await box.inputValue()) === picked.hint, `ticking fills the box with the suggestion (${await box.inputValue()})`);
    const basis = (await picked.row.locator(".sp-qty-basis").textContent()) || "";
    check(/sold ~?[\d.]+\/week|no sales/.test(basis) && /on hand|stock not subtracted/.test(basis) && /suggested|enough/.test(basis), `basis line: "${basis.trim()}"`);
    check(/\(avg [-\d.]+\)|avg/.test(basis) || /stock not subtracted/.test(basis), "basis has the on-hand average");
    await box.fill("3");
    const id = q1(`select item_id from inventory_snapshots where name = '${itemName.replace(/'/g, "''")}' limit 1`);
    await page.getByRole("button", { name: /^Add to cart \(\d+\)$/ }).click();
    await page.waitForFunction(() => /Added|added/.test(document.querySelector(".sp-toolbar ~ p.muted, p.muted")?.textContent || "") || document.body.textContent.includes("to the cart"), null, { timeout: 60000 }).catch(() => {});
    await sleep(1500);
    const stored = q1(`select qty from po_cart_items where supplier = '${VENDOR}' and item_id = '${id}'`);
    check(Number(stored) === 3, `added with the typed qty 3 (stored ${stored}, item ${itemName})`);
    // in-cart rows were NOT re-sent (their qty unchanged)
    const msg = (await page.locator("p.muted").filter({ hasText: /cart/ }).allTextContents()).join(" ");
    check(/Added|added/.test(msg), `add message: "${msg.slice(0, 160)}"`);
    const r2 = rowOf(page, itemName);
    await r2.locator(".sp-qty-incart").waitFor({ timeout: 30000 }).catch(() => {});
    check(/In cart: 3/.test(await r2.textContent()), "the row now says In cart: 3");
    await r2.locator("input.sp-qty-input").fill("5");
    await r2.getByRole("button", { name: "Update" }).click();
    await page.waitForFunction((n) => document.body.textContent.includes(`${n}: cart quantity set to 5`), itemName, { timeout: 30000 }).catch(() => {});
    check(Number(q1(`select qty from po_cart_items where supplier = '${VENDOR}' and item_id = '${id}'`)) === 5, "Update sets the cart qty to 5");
    // Add to cart with only in-cart rows ticked sends nothing
    const before = q1(`select group_concat(qty) from po_cart_items where supplier = '${VENDOR}'`);
    const addBtn = page.getByRole("button", { name: /^Add to cart \(\d+\)$/ });
    if (!(await addBtn.isDisabled())) {
      await addBtn.click();
      await sleep(1500);
      check(q1(`select group_concat(qty) from po_cart_items where supplier = '${VENDOR}'`) === before, "Add to cart never re-sends in-cart rows");
    }
  }
  await shot(page, "projection-admin");
  // weekly sales + avg on hand
  const avgBtn = page.locator(".sp-table tbody tr .sp-value-link").nth(1);
  await avgBtn.click();
  await page.waitForSelector(".sp-weekly-row table, .sp-weekly-row .error, .sp-weekly-row .muted", { timeout: 60000 });
  await page.waitForFunction(() => !/Loading weekly/.test(document.querySelector(".sp-weekly-row")?.textContent || ""), null, { timeout: 60000 });
  const weekly = (await page.locator(".sp-weekly-row").textContent()) || "";
  check(/Avg on hand/.test(weekly), "weekly breakdown has Avg on hand");
  check(/Revenue/.test(weekly), "admin sees Revenue in the breakdown");
  check(/\(\d of 7 days\)|-|\d/.test(weekly), "averages show (or '-' when none)");
  await shot(page, "projection-weekly");
  // filter chips + code search
  await page.getByRole("button", { name: /^Needs order \(\d+\)$/ }).click();
  const shown = await page.locator(".sp-table tbody tr:not(.sp-weekly-row)").count();
  check(shown <= total, `Needs order narrows the list (${shown})`);
  await page.getByRole("button", { name: "All", exact: true }).click();
  const sku = q1(`select supplier_item_id from inventory_snapshots where supplier like '%${VENDOR}%' and supplier_item_id glob '*[0-9]*' and supplier_item_id not like '%;%' and length(supplier_item_id) > 3 order by snapshot_date desc limit 1`);
  if (sku) {
    await page.getByPlaceholder("Search item, category, SKU or barcode…").fill(sku);
    await sleep(500);
    const n = await page.locator(".sp-table tbody tr:not(.sp-weekly-row)").count();
    check(n >= 1 && n < total, `search by SKU ${sku} narrows to ${n}`);
    await page.getByPlaceholder("Search item, category, SKU or barcode…").fill("");
  }
  // View purchase order cart opens this vendor
  await page.getByRole("button", { name: /View purchase order cart/ }).click();
  await page.waitForSelector(".po-vendor", { timeout: 60000 });
  await sleep(1000);
  const open = await page.locator(".po-vendor.is-open .po-vendor-name").allTextContents();
  check(open.includes(VENDOR), `View purchase order cart opens ${VENDOR} (${open})`);
  cleanPage(page, "projection admin");
  await ctx.close();
}

section("qty column on screen at 1024 / 375, hide_sales, viewer, demo");
for (const [user, width] of [["reg_admin", 1024], ["reg_staff", 375], ["reg_hide", 1280], ["reg_hide", 375], ["reg_viewer", 1280], ["app_store_review", 375]]) {
  const { ctx, page } = await as(browser, user, width);
  await run(page);
  const hs = await hscroll(page);
  check(hs <= 1, `${user} ${width}: no sideways page scroll (${hs})`);
  // tick the first row; its qty box must be inside the viewport without scrolling the table
  const first = page.locator(".sp-table tbody tr:not(.sp-weekly-row)").filter({ hasNotText: "(in cart)" }).first();
  await first.locator("input[type=checkbox]").check();
  const box = await first.locator("input.sp-qty-input").boundingBox();
  check(box && box.x >= 0 && box.x + box.width <= width, `${user} ${width}: the Order qty box is on screen (x=${box && Math.round(box.x)})`);
  await first.scrollIntoViewIfNeeded();
  await shot(page, "projection-ticked", { fullPage: false });
  await page.locator(".sp-table tbody tr .sp-value-link").first().click();
  await page.waitForFunction(() => !/Loading weekly/.test(document.querySelector(".sp-weekly-row")?.textContent || "x"), null, { timeout: 60000 }).catch(() => {});
  const weekly = (await page.locator(".sp-weekly-row").textContent().catch(() => "")) || "";
  if (user === "reg_hide" || user === "app_store_review") {
    check(!/Revenue|\$\d/.test(weekly), `${user}: no revenue in the weekly breakdown`);
  }
  check(/Avg on hand/.test(weekly), `${user} ${width}: Avg on hand shown`);
  if (user === "app_store_review") {
    const before = q1("select count(*) from po_cart_items");
    await page.getByRole("button", { name: /^Add to cart \(\d+\)$/ }).click();
    await sleep(2500);
    check(q1("select count(*) from po_cart_items") === before, "demo: nothing added");
    const err = (await page.locator(".error").allTextContents()).join(" ");
    check(/demo|read-only|review/i.test(err), `demo: plain refusal "${err.slice(0, 120)}"`);
  }
  if (user === "reg_viewer") {
    // no purchase_order_cart: the cart link would bounce them
    const link = page.getByRole("button", { name: /View purchase order cart/ });
    check((await link.count()) === 0, "viewer (no cart page access): no 'View purchase order cart' link that bounces");
  }
  await shot(page, "projection-weekly-open", { fullPage: false });
  cleanPage(page, `${user} ${width} projection`, user === "app_store_review" ? [/^403 POST \/api\/ordering\/cart/] : []);
  await ctx.close();
}

await browser.close();
done();
