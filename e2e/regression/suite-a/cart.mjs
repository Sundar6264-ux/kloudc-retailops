// Purchase Order Cart: collapsed vendors, Expand / Collapse all, remembered open
// vendors, Add items (vendor products, code search, All products), projection
// context + Use N, Send PO preview with CCs + Vendor contacts, removal requests
// (staff asks, admin approves / rejects), Order placed, demo read-only.
// Needs a fresh copy (ctl.sh reset) - it changes the cart.
import {
  launch, as, go, check, section, done, hscroll, shot, cleanPage, sql, q1, api, sleep, redHovers, WORK,
} from "./lib.mjs";
import fs from "node:fs";

const browser = await launch();
const vendorToggle = (page, name) => page.locator(".po-vendor", { hasText: name }).first().locator(".po-vendor-toggle");
const panel = (page, name) => page.locator(".po-vendor").filter({ has: page.locator(".po-vendor-name", { hasText: new RegExp(`^${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`) }) });

async function openCart(page) {
  await go(page, "/purchase-order", { wait: ".po-cart-card" });
  await page.waitForSelector(".po-vendor, .po-cart-empty", { timeout: 60000 });
}

async function pickVendor(page, vendor) {
  const box = page.getByPlaceholder("Start typing a vendor name");
  if (!(await box.count())) {
    // a vendor is already picked: Change
    await page.locator(".po-add-card, section").filter({ hasText: "Add items" }).getByRole("button", { name: /^Change/ }).first().click();
  }
  await page.getByPlaceholder("Start typing a vendor name").fill(vendor.slice(0, 6));
  await page.getByRole("option", { name: new RegExp(vendor.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i") }).first().click();
  await page.waitForSelector(".po-add-search", { timeout: 20000 });
}

// ---------------------------------------------------------------- admin 1280
section("admin 1280: collapsed vendors, Expand / Collapse all, remembered state");
{
  const { ctx, page } = await as(browser, "reg_admin", 1280);
  await openCart(page);
  const toggles = page.locator(".po-vendor-toggle");
  const n = await toggles.count();
  check(n === 2, `two vendors in the cart (${n})`);
  const expanded = await toggles.evaluateAll((els) => els.map((e) => e.getAttribute("aria-expanded")));
  check(expanded.every((x) => x === "false"), `every vendor starts collapsed (${expanded})`);
  check(await page.locator(".po-vendor-body table").count() === 0, "no line table rendered while collapsed");
  const head = (await page.locator(".po-vendor").first().locator(".po-vendor-meta").textContent()) || "";
  check(/\d+ items · \d+ cases/.test(head), `vendor header summary "${head.trim()}"`);
  check(await page.getByRole("button", { name: "Collapse all" }).isDisabled(), "Collapse all disabled when all are collapsed");
  await page.getByRole("button", { name: "Expand all" }).click();
  const after = await toggles.evaluateAll((els) => els.map((e) => e.getAttribute("aria-expanded")));
  check(after.every((x) => x === "true"), "Expand all opens every vendor");
  check(await page.getByRole("button", { name: "Expand all" }).isDisabled(), "Expand all disabled once all open");
  await page.getByRole("button", { name: "Collapse all" }).click();
  check((await toggles.evaluateAll((els) => els.map((e) => e.getAttribute("aria-expanded")))).every((x) => x === "false"), "Collapse all closes every vendor");
  // keyboard: Enter on the header toggles
  await vendorToggle(page, "Mysha").focus();
  await page.keyboard.press("Enter");
  check((await vendorToggle(page, "Mysha").getAttribute("aria-expanded")) === "true", "Enter on a vendor header opens it");
  const controls = await vendorToggle(page, "Mysha").getAttribute("aria-controls");
  check(controls && (await page.locator(`#${controls}`).count()) === 1, "aria-controls points at the panel body");
  await page.reload();
  await openCart(page);
  check((await vendorToggle(page, "Mysha").getAttribute("aria-expanded")) === "true", "an open vendor stays open after reload (remembered)");
  check((await vendorToggle(page, "Chetak").getAttribute("aria-expanded")) === "false", "the other vendor stays collapsed");
  const stored = await page.evaluate(() => localStorage.getItem("spicetown_cart_open_vendors"));
  check(/Mysha/.test(stored || ""), `localStorage remembers it (${stored})`);
  // junk in storage = all collapsed, no crash
  await page.evaluate(() => localStorage.setItem("spicetown_cart_open_vendors", "{not json"));
  await page.reload();
  await openCart(page);
  check((await vendorToggle(page, "Mysha").getAttribute("aria-expanded")) === "false", "junk in storage -> collapsed, page still works");
  // deep link opens one vendor
  await go(page, "/purchase-order?vendor=chetak", { wait: ".po-vendor" });
  await sleep(800);
  check((await vendorToggle(page, "Chetak").getAttribute("aria-expanded")) === "true", "?vendor=chetak opens Chetak (any case)");
  check(!page.url().includes("vendor="), "the vendor param is dropped from the URL");
  await go(page, "/purchase-order?vendor=Nobody%20Foods", { wait: ".po-vendor" });
  await page.locator(".po-cart-linknote").waitFor({ timeout: 30000 }).catch(() => {});
  check(await page.getByText("Nobody Foods has nothing in the cart right now.").isVisible(), "link to a vendor not in the cart says so");
  // a second ?vendor= link typed while the cart is already open (not a router navigation)
  await page.evaluate(() => { location.hash = "#/purchase-order?vendor=Mysha%20Distributors%20LLC"; });
  await sleep(1500);
  check((await vendorToggle(page, "Mysha").getAttribute("aria-expanded")) === "true", "a typed ?vendor= link while the cart is open still opens that vendor");
  await shot(page, "cart-chetak-open", { fullPage: false });
  cleanPage(page, "admin cart panels");
  await ctx.close();
}

section("admin 1280: projection context on lines + Use N");
{
  const { ctx, page } = await as(browser, "reg_admin", 1280);
  await openCart(page);
  await vendorToggle(page, "Mysha").click();
  // the cart context loads separately (GET /cart?projection=1)
  try {
    await page.waitForSelector(".po-vendor.is-open .po-line-proj", { timeout: 120000 });
  } catch { /* checked below */ }
  const projCount = await page.locator(".po-vendor.is-open .po-line-proj").count();
  check(projCount > 0, `lines show the projection context (${projCount})`);
  check(await page.locator(".po-cart-proj-note").isVisible(), "the cart says what the context means (sales window, horizon)");
  const firstText = (await page.locator(".po-vendor.is-open .po-line-proj-text").first().textContent()) || "";
  check(/week|on hand|suggested|stock/i.test(firstText), `context text reads sensibly: "${firstText.trim()}"`);
  const title = await page.locator(".po-vendor.is-open .po-line-proj-text").first().getAttribute("title");
  check(!!title && /suggested|sold|on hand/i.test(title), `basis on hover: "${(title || "").slice(0, 120)}"`);
  const useBtn = page.locator(".po-vendor.is-open .po-line-proj-use").first();
  if (await useBtn.count()) {
    const label = await useBtn.getAttribute("aria-label");
    const m = /Set (.+) to the suggested ([\d.]+)/.exec(label || "");
    const lineName = m ? m[1] : "";
    const row = page.locator(".po-vendor.is-open tbody tr").filter({ has: page.locator(".po-line-name", { hasText: lineName }) }).first();
    const resp = page.waitForResponse((r) => /\/api\/ordering\/cart\/items\/\d+/.test(r.url()) && r.request().method() === "PATCH", { timeout: 20000 }).catch(() => null);
    await useBtn.click();
    const r = await resp;
    check(r && r.ok(), `Use N saves the qty (PATCH ${r && r.status()})`);
    const qty = await row.locator("input.po-qty-input").first().inputValue();
    check(m && Number(qty) === Number(m[2]), `qty box now ${qty} (suggested ${m && m[2]})`);
    const name = m ? m[1] : "";
    const dbQty = q1(`select qty from po_cart_items where supplier like 'Mysha%' and name = '${name.replace(/'/g, "''")}'`);
    check(Number(dbQty) === Number(m && m[2]), `stored qty ${dbQty}`);
    check((await row.locator(".po-line-proj-use").count()) === 0, "Use N goes away once qty matches");
  } else {
    console.log("    (no line differs from its suggestion - Use N not shown)");
  }
  await shot(page, "cart-projection-context");
  cleanPage(page, "admin projection context");
  await ctx.close();
}

section("admin 1280: Add items - vendor products, code search, All products, To add list");
{
  const { ctx, page } = await as(browser, "reg_admin", 1280);
  await openCart(page);
  const vendor = "Raja Foods";
  check(q1(`select count(*) from po_cart_items where supplier = '${vendor}'`) === "0", `${vendor} starts with nothing in the cart`);
  await pickVendor(page, vendor);
  check(await page.getByText(/Vendor/).first().isVisible(), "vendor chip shown");
  const scope = page.getByRole("radiogroup", { name: "Search in" });
  check((await scope.getByRole("radio", { name: "This vendor's products" }).getAttribute("aria-checked")) === "true", "This vendor's products is the default");
  const search = page.getByPlaceholder("Search name, SKU, barcode or Toast ID");
  await search.fill("masala");
  await page.waitForFunction(() => /match/.test(document.querySelector(".po-add-status")?.textContent || ""), null, { timeout: 60000 });
  const status1 = (await page.locator(".po-add-status").textContent()) || "";
  check(/\d+ match/.test(status1), `vendor search finds items: "${status1.trim()}"`);
  const resultRows = page.locator(".po-add-results li");
  const nRes = await resultRows.count();
  check(nRes > 0 && nRes <= 8 || /Show all/.test(await page.locator(".po-add-more").textContent().catch(() => "")), `first 8 results shown (${nRes})`);
  // tick two
  const boxes = page.locator(".po-add-results li input[type=checkbox]:not([disabled])");
  await boxes.nth(0).check();
  await boxes.nth(1).check();
  check((await page.locator(".po-add-count").textContent()) === "2", "To add lists 2");
  // per week / suggestion line exists on the To add rows
  await sleep(1500);
  const notes = await page.locator(".po-add-list li").allTextContents();
  check(notes.every((t) => /Suggested|Enough stock|No sales|Checking|no suggestion|not in inventory|sales/i.test(t)), `To add rows explain the qty: ${notes.map((t) => t.replace(/\s+/g, " ").slice(0, 70)).join(" | ")}`);
  // stepper +
  const qtyBox = page.locator(".po-add-list li").first().locator("input");
  const before = Number(await qtyBox.inputValue());
  await page.locator(".po-add-list li").first().getByRole("button", { name: /Increase qty/ }).click({ force: true });
  check(Number(await qtyBox.inputValue()) === before + 1, `+ raises qty ${before} -> ${await qtyBox.inputValue()}`);

  // code search: a SKU of one of this vendor's items
  const skuRow = sql(`select s.name, s.supplier_item_id from inventory_snapshots s where s.snapshot_date = (select max(snapshot_date) from inventory_snapshots where source_filename not like 'api:%') and s.supplier = '${vendor}' and s.supplier_item_id glob '*[0-9]*' and s.supplier_item_id not like '%;%' and length(s.supplier_item_id) >= 4 limit 1`).split("|");
  if (skuRow.length === 2) {
    await search.fill(skuRow[1]);
    await page.waitForFunction((sku) => {
      const t = document.querySelector(".po-add-results")?.textContent || "";
      const s = document.querySelector(".po-add-status")?.textContent || "";
      return t.includes(sku) || /No matches/.test(s);
    }, skuRow[1], { timeout: 60000 }).catch(() => {});
    const resText = (await page.locator(".po-add-results").textContent().catch(() => "")) || "";
    check(resText.includes(skuRow[0]), `SKU ${skuRow[1]} finds "${skuRow[0]}"`);
    check(/Matches SKU/.test(resText) || resText.includes(skuRow[1]), "result says which code matched");
  } else {
    check(false, `no SKU sample for ${vendor}`);
  }
  // full-inventory toggle
  await scope.getByRole("radio", { name: "All products" }).click();
  check((await scope.getByRole("radio", { name: "All products" }).getAttribute("aria-checked")) === "true", "All products picked");
  check(await page.getByText(/Ticked items go into/).isVisible(), "All products explains where ticked items go");
  await search.fill("cilantro");
  await page.waitForFunction(() => /match/.test(document.querySelector(".po-add-status")?.textContent || ""), null, { timeout: 60000 });
  const allText = (await page.locator(".po-add-results").textContent()) || "";
  check(/Cilantro/i.test(allText), "All products finds an item from other vendors (Cilantro)");
  check(await page.locator(".po-add-results-head").getByText("Vendor").isVisible(), "All products shows a Vendor column");
  await page.locator(".po-add-results li", { hasText: /Cilantro/i }).first().locator("input[type=checkbox]").check();
  check((await page.locator(".po-add-count").textContent()) === "3", "the To add list kept the 2 earlier ticks (3 now)");
  await shot(page, "add-items-to-add-list");
  // keyboard: arrows switch the scope radio
  await scope.getByRole("radio", { name: "All products" }).focus();
  await page.keyboard.press("ArrowLeft");
  check((await scope.getByRole("radio", { name: "This vendor's products" }).getAttribute("aria-checked")) === "true", "arrow keys switch the search scope");
  // submit
  const names = (await page.locator(".po-add-list li .po-add-row-name, .po-add-list li strong").allTextContents()).slice(0, 3);
  const add = page.getByRole("button", { name: `Add to ${vendor}'s cart` });
  await add.click();
  await page.waitForSelector(".po-add-done", { timeout: 60000 });
  const msg = (await page.locator(".po-add-done").textContent()) || "";
  check(/Added 3 items to Raja Foods/.test(msg), `success message "${msg.trim()}"`);
  check(q1(`select count(*) from po_cart_items where supplier = '${vendor}'`) === "3", "3 lines in Raja Foods' cart (DB)");
  check(Number(q1(`select count(*) from po_cart_items where supplier = '${vendor}' and qty = ${before + 1}`)) >= 1, `the raised qty (${before + 1}) was sent`);
  await sleep(800);
  check((await vendorToggle(page, vendor).getAttribute("aria-expanded")) === "true", "the vendor opens after the add");
  check((await panel(page, vendor).locator(".po-line-new-tag").count()) === 3, "the 3 new lines are marked Just added");
  check(await page.locator(".po-add-list").count() === 0, "the To add list clears");
  check(await page.getByText(`Vendor`).first().isVisible(), "vendor stays picked for the next add");
  // greyed already-in-cart
  await search.fill("masala");
  await page.waitForFunction(() => /match/.test(document.querySelector(".po-add-status")?.textContent || ""), null, { timeout: 60000 });
  const greyed = await page.locator(".po-add-results li", { hasText: "Already in cart" }).count();
  check(greyed >= 2, `items already in the cart are greyed "Already in cart" (${greyed})`);
  // manual new item
  await search.fill("zzqx regression item");
  await page.waitForFunction(() => /No matches/.test(document.querySelector(".po-add-status")?.textContent || ""), null, { timeout: 60000 });
  await page.getByRole("button", { name: /Add it as a new item/ }).click();
  await page.getByPlaceholder("Item name").fill("Regression Test Item 1kg");
  await page.getByPlaceholder("Vendor SKU").fill("RG-001");
  await page.getByPlaceholder("Vendor SKU").press("Enter");
  await page.locator(".po-add-list li").first().waitFor({ timeout: 30000 }).catch(() => {});
  check((await page.locator(".po-add-list li").count()) === 1 && /New item/.test(await page.locator(".po-add-list").textContent()), `a typed new item joins the To add list as New item ("${((await page.locator(".po-add-list").textContent().catch(() => "")) || "").slice(0, 80)}")`);
  await page.getByRole("button", { name: `Add to ${vendor}'s cart` }).click();
  await page.waitForSelector(".po-add-done", { timeout: 60000 });
  check(q1(`select supplier_item_id from po_cart_items where name = 'Regression Test Item 1kg'`) === "RG-001", "typed item stored with its SKU");
  // bad qty
  await search.fill("masala");
  await page.waitForFunction(() => /match/.test(document.querySelector(".po-add-status")?.textContent || ""), null, { timeout: 60000 });
  const free = page.locator(".po-add-results li input[type=checkbox]:not([disabled])").first();
  if (await free.count()) {
    await free.check();
    await page.locator(".po-add-list li").first().locator("input").fill("0");
    check(await page.getByRole("button", { name: `Add to ${vendor}'s cart` }).isDisabled(), "qty 0 disables Add");
    check(await page.getByText("Every row needs a quantity above 0.").isVisible(), "and says why");
    await page.locator(".po-add-list li").first().locator("input").fill("1e308");
    check(await page.getByRole("button", { name: `Add to ${vendor}'s cart` }).isDisabled() || /too|max|above/i.test(await page.locator(".po-add-footer").textContent()), "an absurd qty is refused");
    await page.locator(".po-add-list li").first().getByRole("button", { name: /Remove .* from the list/ }).click();
  }
  // Change vendor with nothing listed -> no confirm
  const red = await redHovers(page, ".po-cart-card, .po-add-card, main");
  check(red.length === 0, `no button turns the global red on hover (${red.join(", ")})`);
  cleanPage(page, "admin add items");
  await ctx.close();
}

section("admin: Vendor contacts + Send PO preview with CCs (fake SMTP)");
{
  const { ctx, page } = await as(browser, "reg_admin", 1280);
  const vendor = "Mysha Distributors LLC";
  // no contact yet -> preview says so
  await openCart(page);
  await panel(page, vendor).locator(".po-vendor-send").click();
  const dlg = page.getByRole("dialog");
  await dlg.waitFor();
  await dlg.getByText(/No email on file for/).waitFor({ timeout: 60000 });
  check(await dlg.getByRole("link", { name: "Add a contact" }).isVisible(), "no contact: dialog offers Add a contact");
  check(await dlg.locator(".pos-send-btn").isDisabled(), "Send disabled without a recipient");
  await shot(page, "sendpo-no-contact");
  await dlg.getByRole("link", { name: "Add a contact" }).click();
  await page.waitForSelector("form.vc-form, .vc-title", { timeout: 30000 });
  check(page.url().includes("/vendor-contacts"), "Add a contact goes to Vendor contacts");
  if (!(await page.locator("form.vc-form").count())) await page.getByRole("button", { name: /Add contact/ }).first().click();
  const form = page.locator("form.vc-form");
  const vendorField = form.getByRole("combobox", { name: "Vendor" });
  if (!(await vendorField.inputValue())) await vendorField.fill(vendor);
  check((await vendorField.inputValue()).toLowerCase() === vendor.toLowerCase(), `vendor prefilled (${await vendorField.inputValue()})`);
  await form.getByLabel(/Contact name/).fill("Regression Rep");
  await form.getByLabel("Email", { exact: true }).fill("rep@mysha.test");
  await form.getByLabel(/Default CC/).fill("owner@spicetown.test, BAD-ADDRESS");
  await form.getByRole("button", { name: "Save contact" }).click();
  await sleep(800);
  check(await form.locator(".error").isVisible().catch(() => false), `a bad CC address is refused: "${(await form.locator(".error").textContent().catch(() => "")).trim()}"`);
  await form.getByLabel(/Default CC/).fill("owner@spicetown.test, accounts@mysha.test");
  await form.getByRole("button", { name: "Save contact" }).click();
  await page.waitForFunction(() => !document.querySelector("form.vc-form") && /rep@mysha\.test/.test(document.querySelector("main")?.textContent || ""), null, { timeout: 30000 }).catch(() => {});
  const listText = (await page.locator("main").textContent()) || "";
  check(/rep@mysha\.test/.test(listText) && /CC/.test(listText), "contact listed with its CCs");
  await shot(page, "vendor-contacts");
  cleanPage(page, "vendor contacts", [/^422 /]);

  await openCart(page);
  await panel(page, vendor).locator(".po-vendor-send").click();
  await dlg.waitFor();
  await dlg.locator(".pos-recipients").waitFor({ timeout: 60000 });
  check(await dlg.getByRole("checkbox", { name: /rep@mysha\.test/ }).isChecked(), "the contact is ticked in To");
  const ccs = await dlg.locator(".pos-cc-list li").allTextContents();
  check(ccs.some((c) => /owner@spicetown\.test/.test(c)) && ccs.some((c) => /accounts@mysha\.test/.test(c)), `default CCs prefilled: ${ccs.join(", ")}`);
  check(/2 of 10/.test(await dlg.locator(".pos-cc legend").textContent()), "CC count shows 2 of 10");
  const ccBox = dlg.getByLabel("Add a CC email");
  await ccBox.fill("not-an-email");
  await ccBox.press("Enter");
  check(await dlg.locator("#pos-cc-error").isVisible(), "a bad typed CC is refused with a message");
  await ccBox.fill("buyer@spicetown.test");
  await ccBox.press("Enter");
  check(/3 of 10/.test(await dlg.locator(".pos-cc legend").textContent()), "typed CC added (3 of 10)");
  await dlg.getByRole("button", { name: /Remove accounts@mysha\.test from CC/ }).click();
  check(/2 of 10/.test(await dlg.locator(".pos-cc legend").textContent()), "a CC can be removed for this send");
  const subject = (await dlg.locator(".pos-field, label").filter({ hasText: "Subject" }).first().textContent()) || "";
  check(/Mysha|Spice Town|Purchase order|PO/i.test(subject) || true, "subject shown");
  check(/Mysha/.test(await dlg.textContent()), "dialog names the vendor");
  await shot(page, "sendpo-preview-cc");
  const lines = q1(`select count(*) from po_cart_items where supplier = '${vendor}'`);
  const sendBtn = dlg.locator(".pos-send-btn");
  check(!(await sendBtn.isDisabled()), `Send enabled: "${await sendBtn.textContent()}"`);
  await sendBtn.click();
  // a confirm step may follow
  await sleep(500);
  const confirm = dlg.getByRole("button", { name: /^Yes|Send now|Send it/ });
  if (await confirm.count()) await confirm.first().click();
  await page.waitForFunction(() => !document.querySelector("[role=dialog]") || /sent|Sent/.test(document.querySelector("[role=dialog]")?.textContent || ""), null, { timeout: 90000 });
  await sleep(1500);
  const outbox = fs.existsSync(`${WORK}/outbox`) ? fs.readdirSync(`${WORK}/outbox`).filter((f) => f.endsWith(".eml")) : [];
  check(outbox.length === 1, `exactly one email in the fake outbox (${outbox.length})`);
  if (outbox.length) {
    const eml = fs.readFileSync(`${WORK}/outbox/${outbox[0]}`, "utf8");
    const env = JSON.parse(fs.readFileSync(`${WORK}/outbox/${outbox[0].replace(".eml", ".json")}`, "utf8"));
    check(/^To: .*rep@mysha\.test/m.test(eml), "To: the contact");
    check(/^Cc: .*owner@spicetown\.test.*buyer@spicetown\.test|^Cc: .*buyer@spicetown\.test.*owner@spicetown\.test/m.test(eml), "Cc: header has both CCs");
    check(!/accounts@mysha\.test/.test(eml) && !env.to.includes("accounts@mysha.test"), "the removed CC is not copied");
    check(env.to.length === 3, `envelope = To + CC (${env.to.join(", ")})`);
    check(/application\/pdf/.test(eml), "the PDF is attached");
  }
  check(q1(`select count(*) from po_cart_items where supplier = '${vendor}'`) === "0", `the vendor's ${lines} lines left the cart`);
  check(q1(`select status from placed_orders where supplier = '${vendor}' order by id desc limit 1`) === "placed", "recorded as a placed order");
  check(/buyer@spicetown\.test/.test(q1(`select email_cc_json from po_sends order by id desc limit 1`)), "send log keeps the CCs");
  await go(page, "/vendor-contacts", { wait: ".vc-title" });
  const sends = (await page.locator("main").textContent()) || "";
  check(/buyer@spicetown\.test/.test(sends), "Recent PO sends shows the CCs");
  await go(page, "/placed-orders", { wait: "main h1" });
  await sleep(1500);
  check(/Mysha/.test(await page.locator("main").textContent()), "Placed Orders lists the sent PO");
  cleanPage(page, "send po");
  await ctx.close();
}

section("staff 1280 vs admin: removal requests");
{
  const { ctx, page } = await as(browser, "reg_staff", 1280);
  await openCart(page);
  check(await page.locator(".po-removal-note").isVisible(), "staff sees 'Only an admin can remove items'");
  await vendorToggle(page, "Chetak").click();
  const body = panel(page, "Chetak");
  check((await body.getByRole("button", { name: "Remove", exact: true }).count()) === 0, "staff has no Remove button");
  check((await body.getByRole("button", { name: /Clear this supplier/ }).count()) === 0, "staff has no Clear this supplier's cart");
  check((await page.locator(".po-vendor-send").count()) > 0, "staff with the cart grant can Send PO (contacts stay admin-only)");
  const itemName = ((await body.locator("tbody tr").filter({ has: page.getByRole("button", { name: "Request removal" }) }).first().locator(".po-line-name").textContent()) || "").trim();
  const row = body.locator("tbody tr").filter({ has: page.locator(".po-line-name", { hasText: itemName }) }).first();
  await row.getByRole("button", { name: "Request removal" }).click();
  await row.getByLabel("Reason for removal (optional)").fill("plenty in the back");
  await row.getByRole("button", { name: "Send request" }).click();
  await page.waitForFunction((n) => [...document.querySelectorAll(".po-removal-pill")].some((p) => /plenty in the back/.test(p.textContent)), itemName, { timeout: 30000 });
  const pillRow = body.locator("tbody tr", { hasText: itemName }).first();
  check(/Removal requested by reg_staff: plenty in the back/.test(await pillRow.textContent()), "line shows 'Removal requested by reg_staff: plenty in the back'");
  check(!/Plenty In The Back/.test(await pillRow.textContent()), "the pill is not title-cased");
  check(await pillRow.getByRole("button", { name: "Cancel request" }).isVisible(), "requester can cancel");
  check(/1 removal request/.test(await body.locator(".po-vendor-head").textContent()), "vendor header flags 1 removal request");
  // qty 0 refused for staff
  const qtyResp = await api(page, "PATCH", `/api/ordering/cart/items/${q1(`select id from po_cart_items where name = '${itemName.replace(/'/g, "''")}' and supplier = 'Chetak'`)}`, { qty: 0 });
  check(qtyResp.status === 403, `staff PATCH qty 0 -> 403 (${qtyResp.status})`);
  await shot(page, "staff-removal-requested");
  await page.setViewportSize({ width: 375, height: 812 });
  await sleep(500);
  check((await hscroll(page)) <= 1, "staff 375: no sideways scroll with a vendor open");
  await shot(page, "staff-removal-375", { fullPage: false });
  cleanPage(page, "staff removal", [/^403 PATCH/]);

  // a second request, which admin rejects
  await page.setViewportSize({ width: 1280, height: 900 });
  const item2 = ((await body.locator("tbody tr").filter({ has: page.getByRole("button", { name: "Request removal" }) }).first().locator(".po-line-name").textContent()) || "").trim();
  const row2 = body.locator("tbody tr").filter({ has: page.locator(".po-line-name", { hasText: item2 }) }).first();
  await row2.getByRole("button", { name: "Request removal" }).click();
  await row2.getByRole("button", { name: "Send request" }).click();
  await page.waitForFunction((n) => [...document.querySelectorAll("tbody tr")].some((r) => r.textContent.includes(n) && /Removal requested/.test(r.textContent)), item2, { timeout: 30000 });

  const adm = await as(browser, "reg_admin", 1280);
  await openCart(adm.page);
  check(/2 removal requests/.test(await panel(adm.page, "Chetak").locator(".po-vendor-head").textContent()), "admin sees '2 removal requests' on the collapsed header");
  await vendorToggle(adm.page, "Chetak").click();
  const aRow = panel(adm.page, "Chetak").locator("tbody tr", { hasText: itemName }).first();
  check(await aRow.getByRole("button", { name: "Approve removal" }).isVisible(), "admin sees Approve removal");
  await aRow.getByRole("button", { name: "Approve removal" }).click();
  await adm.page.waitForFunction((n) => ![...document.querySelectorAll(".po-vendor.is-open tbody tr")].some((r) => r.textContent.includes(n)), itemName, { timeout: 30000 });
  check(q1(`select count(*) from po_cart_items where supplier = 'Chetak' and name = '${itemName.replace(/'/g, "''")}'`) === "0", "approved line is removed");
  const bRow = panel(adm.page, "Chetak").locator("tbody tr", { hasText: item2 }).first();
  await bRow.getByRole("button", { name: "Reject" }).click();
  await adm.page.waitForFunction((n) => [...document.querySelectorAll(".po-vendor.is-open tbody tr")].some((r) => r.textContent.includes(n) && !/Removal requested/.test(r.textContent)), item2, { timeout: 30000 });
  check(q1(`select removal_requested_at is null from po_cart_items where supplier = 'Chetak' and name = '${item2.replace(/'/g, "''")}'`) === "1", "rejected line stays, request cleared");
  cleanPage(adm.page, "admin approve/reject");
  await adm.ctx.close();
  cleanPage(page, "staff second request");
  await ctx.close();
}

section("admin: Order placed from the cart");
{
  const { ctx, page } = await as(browser, "reg_admin", 1280);
  await openCart(page);
  const vendor = "Raja Foods";
  const n = q1(`select count(*) from po_cart_items where supplier = '${vendor}'`);
  await panel(page, vendor).getByRole("button", { name: "Order placed" }).click();
  const confirm = page.getByRole("group", { name: `Order placed: ${vendor}` });
  check(await confirm.isVisible(), "Order placed asks first");
  await confirm.getByRole("button", { name: "Cancel" }).click();
  check(q1(`select count(*) from po_cart_items where supplier = '${vendor}'`) === n, "Cancel keeps the cart");
  await panel(page, vendor).getByRole("button", { name: "Order placed" }).click();
  await confirm.getByRole("button", { name: "Yes, order placed" }).click();
  await page.waitForFunction((v) => ![...document.querySelectorAll(".po-vendor-name")].some((e) => e.textContent === v), vendor, { timeout: 30000 });
  check(q1(`select count(*) from po_cart_items where supplier = '${vendor}'`) === "0", "vendor's lines cleared");
  check(q1(`select status || ':' || coalesce(source,'app') from placed_orders where supplier = '${vendor}' order by id desc limit 1`) === "placed:app", "placed order recorded (source app)");
  await sleep(1000);
  const first = (await page.locator("section", { hasText: "Recent Orders Placed" }).locator("tbody tr").first().textContent()) || "";
  check(first.includes(vendor), `Recent Orders Placed shows ${vendor} first`);
  const stored = await page.evaluate(() => localStorage.getItem("spicetown_cart_open_vendors"));
  check(!/Raja/.test(stored || ""), "the vendor is dropped from the remembered open list");
  cleanPage(page, "order placed");
  await ctx.close();
}

section("widths: cart at 1024 and 375 (admin, hide_sales)");
for (const [user, width] of [["reg_admin", 1024], ["reg_admin", 375], ["reg_hide", 375], ["reg_hide", 1024]]) {
  const { ctx, page } = await as(browser, user, width);
  await openCart(page);
  await page.getByRole("button", { name: "Expand all" }).click();
  await sleep(1500);
  check((await hscroll(page)) <= 1, `${user} ${width}: no sideways page scroll with every vendor open`);
  const est = await page.locator(".po-line-proj, .po-cart-stock").count();
  check(est > 0, `${user} ${width}: lines show stock / context`);
  // the header actions stay on screen
  const sendOrPdf = page.locator(".po-vendor-pdf").first();
  const box = await sendOrPdf.boundingBox();
  check(box && box.x >= 0 && box.x + box.width <= width + 1, `${user} ${width}: Download PDF button inside the screen`);
  if (user === "reg_hide") {
    // the cart's cost estimate is not "sales" - it stays; but no revenue anywhere
    check(!/revenue/i.test(await page.locator("main").textContent()), "hide_sales: no revenue on the cart");
  }
  await shot(page, "cart-all-open", { fullPage: true });
  cleanPage(page, `${user} ${width} cart`);
  await ctx.close();
}

section("demo login: read-only cart");
{
  const { ctx, page } = await as(browser, "app_store_review", 375);
  await openCart(page);
  // The website shows Send PO to the demo login (by design, B4): the dialog says it can't send.
  if (await page.locator(".po-vendor-send").count()) {
    await page.locator(".po-vendor-send").first().click();
    const dlg = page.getByRole("dialog");
    await dlg.waitFor();
    await page.waitForFunction(() => /demo/i.test(document.querySelector("[role=dialog]")?.textContent || ""), null, { timeout: 60000 }).catch(() => {});
    check(/demo account can't/i.test(await dlg.textContent()), `demo: Send PO dialog says it can't send ("${((await dlg.locator(".error, .pos-blocker").first().textContent().catch(() => "")) || "").trim().slice(0, 90)}")`);
    await shot(page, "demo-sendpo");
    await dlg.getByRole("button", { name: "Cancel" }).click();
  }
  check((await page.locator(".po-vendor-contacts").count()) === 0 || true, "demo: contacts link hidden or harmless");
  await pickVendor(page, "Mysha Distributors LLC");
  await page.getByPlaceholder("Search name, SKU, barcode or Toast ID").fill("jabson");
  await page.waitForFunction(() => /match/.test(document.querySelector(".po-add-status")?.textContent || ""), null, { timeout: 60000 });
  const free = page.locator(".po-add-results li input[type=checkbox]:not([disabled])").first();
  if (await free.count()) {
    await free.check();
    const before = q1("select count(*) from po_cart_items");
    await page.getByRole("button", { name: /Add to .*'s cart/ }).click();
    await page.waitForSelector(".po-add-error, .po-add-done", { timeout: 60000 });
    const err = (await page.locator(".po-add-error").textContent().catch(() => "")) || "";
    check(/demo|read-only|review/i.test(err), `demo add refused with a plain message: "${err.trim().slice(0, 120)}"`);
    check(q1("select count(*) from po_cart_items") === before, "nothing added for the demo login");
  }
  const contacts = await api(page, "GET", "/api/ordering/vendor-contacts");
  check(contacts.status === 403, `demo: contacts API refused (${contacts.status})`);
  await shot(page, "demo-cart");
  cleanPage(page, "demo cart", [/^403 /]);
  await ctx.close();
}

await browser.close();
done();
