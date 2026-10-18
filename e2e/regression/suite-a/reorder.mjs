// Reorder Candidates: add to cart (Add 1, Add selected, Add all Critical + Order now,
// Open cart -> Just added), critical-vendor actions (Order already placed + Undo,
// Skip this week, Remind me later, Close, Other + Undo), Hidden from Today panel,
// staff / hide_sales / demo, 1024 + 375.
// Needs a fresh copy (ctl.sh reset) - it changes the cart and the dismissals.
import { launch, as, go, check, section, done, hscroll, shot, cleanPage, q1, sql, sleep, waitDb } from "./lib.mjs";

const browser = await launch();
const esc = (s) => s.replace(/'/g, "''");

async function calculate(page) {
  await go(page, "/reorder");
  await page.getByRole("button", { name: "Calculate" }).waitFor({ timeout: 120000 });
  await page.getByRole("button", { name: "Calculate" }).click();
  await page.waitForSelector(".reorder-vendor", { timeout: 300000 });
  await sleep(500);
}
const vendorBox = (page, vendor) =>
  page.locator("details.reorder-vendor").filter({ has: page.locator(".reorder-vendor-name", { hasText: new RegExp(`^${vendor.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`) }) }).first();
async function openVendor(page, vendor) {
  const d = vendorBox(page, vendor);
  if (!(await d.evaluate((e) => e.open))) await d.locator(".reorder-vendor-name").click();
  await d.locator(".reorder-vendor-detail").waitFor();
  return d;
}
// vendors as the page lists them: [{vendor, tier}]
async function vendorList(page) {
  return page.locator("details.reorder-vendor").evaluateAll((els) =>
    els.map((e) => ({
      vendor: e.querySelector(".reorder-vendor-name")?.textContent || "",
      pill: e.querySelector("summary .pill")?.textContent || "",
      hide: !!e.querySelector(".reorder-hide-actions"),
    })),
  );
}

let critVendors = [];
section("admin 1280: add to cart");
{
  const { ctx, page } = await as(browser, "reg_admin", 1280);
  const t0 = Date.now();
  await calculate(page);
  console.log(`    Calculate took ${((Date.now() - t0) / 1000).toFixed(1)}s`);
  const vendors = await vendorList(page);
  critVendors = vendors.filter((v) => v.hide).map((v) => v.vendor);
  check(vendors.length > 5, `report lists ${vendors.length} vendors`);
  check(critVendors.length >= 2, `admins get Hide-from-Today actions on critical vendors (${critVendors.length})`);
  check(await page.locator(".rc-cart-howto, p", { hasText: "Add 1 to cart" }).first().isVisible(), "how adding works is explained");
  // a vendor not in the cart with several items
  const inCart = new Set(sql("select distinct lower(supplier) from po_cart_items").split("\n"));
  const target = vendors.find((v) => !inCart.has(v.vendor.toLowerCase()) && v.vendor !== "Unassigned" && !v.hide && /Order now|Critical/.test(v.pill)) || vendors.find((v) => !inCart.has(v.vendor.toLowerCase()) && v.vendor !== "Unassigned");
  console.log(`    target vendor: ${target.vendor} (${target.pill})`);
  const d = await openVendor(page, target.vendor);
  const addOne = d.locator(".rc-cart-add-one").first();
  const oneLabel = await addOne.getAttribute("aria-label");
  const oneName = /^Add 1 (.+) to /.exec(oneLabel)[1];
  await addOne.click();
  await d.locator(".rc-cart-result").first().waitFor({ timeout: 60000 });
  check(/Added|added/.test(await d.locator(".rc-cart-result").first().textContent()), `Add 1 message: "${(await d.locator(".rc-cart-result").first().textContent()).trim()}"`);
  check(q1(`select qty from po_cart_items where supplier = '${esc(target.vendor)}' and name = '${esc(oneName)}'`) === "1.0", "Add 1 puts qty 1 in that vendor's cart");
  check(await d.getByRole("button", { name: `In cart - already in ${target.vendor}'s cart, change it in the cart` }).first().isVisible(), "the row now shows In cart ✓ (disabled)");
  // tick two others -> Add selected review panel
  const ticks = d.locator("input[type=checkbox][aria-label^='Select ']:not([aria-label^='Select all']):not([disabled])");
  const nT = await ticks.count();
  if (nT >= 2) {
    await ticks.nth(0).check();
    await ticks.nth(1).check();
    await d.getByRole("button", { name: "Add selected (2)" }).click();
    const panel = page.locator(".rc-cart-review, form").filter({ hasText: `Add to ${target.vendor}'s cart` }).first();
    await panel.waitFor();
    await sleep(350);
    const qtys = await panel.locator("input[type=number]").evaluateAll((els) => els.map((e) => e.value));
    const sugg = await panel.locator(".rc-cart-review-suggested").allTextContents();
    check(qtys.length === 2, `review panel lists the 2 ticked items (${qtys}) suggested ${sugg}`);
    check(/Shortfall, in units/.test(await panel.textContent()), "panel says the qty is the Shortfall in units");
    await panel.locator("input[type=number]").first().fill("4");
    await shot(page, "reorder-review-panel");
    await panel.getByRole("button", { name: /^Add 2 items to cart$/ }).click();
    await d.locator(".rc-cart-result", { hasText: /Added 2 items/ }).waitFor({ timeout: 60000 }).catch(() => {});
    const n = await waitDb(`select count(*) from po_cart_items where supplier = '${esc(target.vendor)}'`, 3);
    check(n === "3", `3 lines in ${target.vendor}'s cart now (${n})`);
    check(q1(`select count(*) from po_cart_items where supplier = '${esc(target.vendor)}' and qty = 4 and case_of = 1`) === "1", "the edited qty (4 units, Case of 1) was stored");
  } else {
    check(false, `not enough items to tick on ${target.vendor}`);
  }
  // Add all Critical + Order now on another vendor not in the cart (same report)
  const inCart2 = new Set(sql("select distinct lower(supplier) from po_cart_items").split("\n"));
  const v2 = (await vendorList(page)).find((v) => !inCart2.has(v.vendor.toLowerCase()) && v.vendor !== "Unassigned" && /Critical|Order now/.test(v.pill) && !critVendors.slice(0, 3).includes(v.vendor));
  if (v2) {
    const d2 = await openVendor(page, v2.vendor);
    const allBtn = d2.getByRole("button", { name: /^Add all Critical \+ Order now \(\d+\)$/ });
    if (await allBtn.count()) {
      const n = Number(/\((\d+)\)/.exec(await allBtn.textContent())[1]);
      await allBtn.click();
      const panel = page.locator("form").filter({ hasText: `Add to ${v2.vendor}'s cart` }).first();
      await panel.waitFor();
      check((await panel.locator("li.rc-cart-review-row").count()) === n, `Add all lists its ${n} items`);
      // untick one, set a bad qty on another
      if (n >= 2) {
        await panel.locator("li.rc-cart-review-row").nth(0).locator("input[type=checkbox]").uncheck();
        await panel.locator("li.rc-cart-review-row").nth(1).locator("input[type=number]").fill("0");
        check(await panel.getByRole("button", { name: /^Add \d+ items? to cart$/ }).isDisabled(), "a 0 qty disables Add");
        await panel.locator("li.rc-cart-review-row").nth(1).locator("input[type=number]").fill("2");
      }
      await panel.getByRole("button", { name: /^Add \d+ items? to cart$/ }).click();
      await d2.locator(".rc-cart-result").first().waitFor({ timeout: 60000 });
      const expected = n >= 2 ? n - 1 : n;
      check(q1(`select count(*) from po_cart_items where supplier = '${esc(v2.vendor)}'`) === String(expected), `Add all added ${expected} (one unticked)`);
      const note = (await d2.locator(".rc-cart-bar").textContent()) || "";
      check(/Add all Critical \+ Order now \(1\)|in the cart/.test(note) || n < 2, `bar updates after the add: "${note.replace(/\s+/g, " ").slice(0, 100)}"`);
    }
  }
  {
    // Open cart -> vendor open + Just added
    await vendorBox(page, target.vendor).locator(".rc-cart-open").click();
    await page.waitForSelector(".po-vendor", { timeout: 60000 });
    await sleep(1500);
    const open = await page.locator(".po-vendor.is-open .po-vendor-name").allTextContents();
    check(open.includes(target.vendor), `Open cart opens ${target.vendor} (${open})`);
    const marks = await page.locator(".po-vendor.is-open .po-line-new-tag").count();
    check(marks === 3, `the 3 lines added from Reorder are marked Just added (${marks})`);
    await shot(page, "cart-from-reorder");
  }
  cleanPage(page, "admin reorder add");
  await ctx.close();
}

section("admin 1280: critical actions - Order already placed + Undo, Skip, Remind me later, Close, Other");
{
  const { ctx, page } = await as(browser, "reg_admin", 1280);
  await calculate(page);
  const crit = (await vendorList(page)).filter((v) => v.hide).map((v) => v.vendor);
  check(crit.length >= 4, `at least 4 critical vendors to act on (${crit.length})`);
  const [A, B, C, D] = crit;
  // Order already placed
  const boxA = vendorBox(page, A);
  await boxA.getByRole("button", { name: "Order already placed" }).click();
  await page.locator(".reorder-undo-bar", { hasText: A }).waitFor({ timeout: 60000 });
  check(/order marked as already placed/.test(await page.locator(".reorder-undo-bar").textContent()), "Undo bar says the order is marked as placed");
  check(/Ordered/.test(await boxA.locator("summary").textContent()), `${A} now shows Ordered`);
  check(/placed outside the app/.test(await boxA.locator("summary").textContent()), "with 'placed outside the app'");
  check(q1(`select source from placed_orders where supplier = '${esc(A)}' order by id desc limit 1`) === "outside", "placed_orders row source outside");
  check((await boxA.locator(".reorder-hide-actions").count()) === 0, "no hide actions on an Ordered vendor");
  await page.locator("details.reorder-hidden-panel summary").click();
  check(/marked as already placed/.test(await page.locator(".reorder-hidden-panel").textContent()), "Hidden from Today panel lists it under marked as already placed");
  await shot(page, "reorder-placed-outside");
  // Undo from the summary tag
  await boxA.locator(".reorder-outside-tag").getByRole("button", { name: "Undo" }).click();
  await page.waitForFunction((v) => {
    const d = [...document.querySelectorAll("details.reorder-vendor")].find((e) => e.querySelector(".reorder-vendor-name")?.textContent === v);
    return d && !/placed outside/.test(d.querySelector("summary").textContent);
  }, A, { timeout: 60000 });
  check(q1(`select count(*) from placed_orders where supplier = '${esc(A)}' and source = 'outside'`) === "0", "Undo deletes the outside order");
  check((await boxA.locator(".reorder-hide-actions").count()) === 1, `${A} is critical again with its actions`);

  // Skip this week
  await vendorBox(page, B).getByRole("button", { name: "Skip this week" }).click();
  const drawer = page.getByRole("dialog", { name: `Hide ${B} from Today` });
  await drawer.waitFor();
  await sleep(350);
  check(await drawer.getByRole("radio", { name: /Skip this week/ }).isChecked(), "drawer opens on Skip this week");
  check(/Hidden for everyone/.test(await drawer.textContent()), "drawer says it's for everyone and Reorder still lists it");
  await shot(page, "reorder-hide-drawer");
  await drawer.getByRole("button", { name: "Hide from Today" }).click();
  await drawer.waitFor({ state: "detached", timeout: 60000 });
  await page.locator(".reorder-undo-bar", { hasText: B }).waitFor({ timeout: 60000 });
  check(await waitDb(`select kind from critical_vendor_dismissals where vendor = '${esc(B)}' and cleared_at is null`, "skip_week") === "skip_week", "skip_week stored");
  check(/Hidden|Until/i.test(await vendorBox(page, B).locator("summary").textContent()), `${B} shows a hidden chip`);
  await page.locator(".reorder-undo-bar", { hasText: B }).getByRole("button", { name: "Undo" }).click();
  await page.waitForFunction(() => !document.querySelector(".reorder-undo-bar") || /no longer|shown again|back/i.test(document.querySelector(".reorder-undo-bar").textContent), null, { timeout: 30000 }).catch(() => {});
  check(await waitDb(`select count(*) from critical_vendor_dismissals where vendor = '${esc(B)}' and cleared_at is null`, 0) === "0", "Undo ends the skip");

  // Remind me later -> tomorrow
  await vendorBox(page, C).getByRole("button", { name: "Remind me later" }).click();
  const dC = page.getByRole("dialog", { name: `Hide ${C} from Today` });
  await dC.waitFor();
  await dC.getByRole("radio", { name: /Tomorrow/ }).check();
  await dC.getByRole("radio", { name: /Pick a date/ }).check();
  check(await dC.getByRole("button", { name: "Hide from Today" }).isDisabled(), "Pick a date needs a date first");
  await dC.getByRole("radio", { name: /Tomorrow/ }).check();
  await dC.getByRole("button", { name: "Hide from Today" }).click();
  await dC.waitFor({ state: "detached", timeout: 60000 });
  await waitDb(`select count(*) from critical_vendor_dismissals where vendor = '${esc(C)}' and cleared_at is null`, 1);
  check(q1(`select kind from critical_vendor_dismissals where vendor = '${esc(C)}' and cleared_at is null`) === "snooze", "snooze stored");

  // Close needs a reason; Other needs a note
  await vendorBox(page, D).getByRole("button", { name: "Other", exact: true }).click();
  const dD = page.getByRole("dialog", { name: `Hide ${D} from Today` });
  await dD.waitFor();
  check(await dD.getByRole("radio", { name: "Other" }).isChecked(), "Other opens Close with reason Other");
  check(await dD.getByRole("button", { name: "Hide from Today" }).isDisabled(), "Other needs a note before saving");
  await dD.locator("textarea").fill("Owner ordering this one by phone");
  await dD.getByRole("button", { name: "Hide from Today" }).click();
  await dD.waitFor({ state: "detached", timeout: 60000 });
  await waitDb(`select count(*) from critical_vendor_dismissals where vendor = '${esc(D)}' and cleared_at is null`, 1);
  check(q1(`select kind || ':' || reason from critical_vendor_dismissals where vendor = '${esc(D)}' and cleared_at is null`) === "close:other", "close with reason other stored");
  // Hidden from Today panel: Show again
  await page.reload();
  await calculate(page);
  const panel = page.locator("details.reorder-hidden-panel");
  const title = (await panel.locator(".reorder-hidden-title").textContent()) || "";
  check(/Hidden from Today \(2\)/.test(title), `panel counts 2 hidden (${title.trim()})`);
  await panel.locator("summary").click();
  const lis = panel.locator(".reorder-hidden-list li");
  check(/Owner ordering this one by phone/.test(await panel.textContent()), "the note shows in the panel");
  check(/History/.test(await panel.textContent()), "admins see the history");
  await shot(page, "reorder-hidden-panel");
  await lis.filter({ hasText: C }).getByRole("button", { name: "Show again" }).click();
  await page.waitForFunction(() => /Hidden from Today \(1\)/.test(document.querySelector(".reorder-hidden-title")?.textContent || ""), null, { timeout: 60000 }).catch(() => {});
  check(q1(`select count(*) from critical_vendor_dismissals where vendor = '${esc(C)}' and cleared_at is null`) === "0", "Show again ends the snooze");
  cleanPage(page, "admin critical actions");
  await ctx.close();
}

section("staff / hide_sales / demo / widths");
for (const [user, width] of [["reg_staff", 1280], ["reg_staff", 375], ["reg_hide", 1024], ["app_store_review", 375], ["app_store_review", 1280], ["reg_admin", 375], ["reg_admin", 1024]]) {
  const { ctx, page } = await as(browser, user, width);
  await calculate(page);
  check((await hscroll(page)) <= 1, `${user} ${width}: no sideways page scroll`);
  const hideActs = await page.locator(".reorder-hide-actions").count();
  if (user === "reg_admin") check(hideActs > 0, `${user} ${width}: admin sees hide actions`);
  else check(hideActs === 0, `${user} ${width}: no hide / Order already placed actions`);
  // open the first vendor with items and look at it
  const first = page.locator("details.reorder-vendor").nth(1);
  await first.locator(".reorder-vendor-name").click();
  await sleep(500);
  if (user === "app_store_review") {
    check(/Demo account: you can look around/.test(await page.locator("main").textContent()), "demo is told adding does nothing");
    const add1 = first.locator(".rc-cart-add-one").first();
    if (await add1.count()) {
      const before = q1("select count(*) from po_cart_items");
      await add1.click();
      await sleep(2500);
      check(q1("select count(*) from po_cart_items") === before, "demo: Add 1 adds nothing");
      const res = (await first.locator(".rc-cart-result").textContent().catch(() => "")) || "";
      check(/demo|can't change|look around/i.test(res), `demo: plain message "${res.trim().slice(0, 100)}"`);
    }
  }
  if (width === 375) {
    const box = await first.locator(".rc-cart-btn").first().boundingBox().catch(() => null);
    check(!box || (box.x >= 0 && box.x + box.width <= width + 1), `${user} 375: cart buttons fit the screen`);
  }
  await shot(page, "reorder-vendor-open", { fullPage: false });
  // weekly sales from Avg/day (W3)
  const avg = first.locator("button[title='Show weekly sales and stock']").first();
  if (await avg.count()) {
    await avg.click();
    await page.waitForFunction(() => /Avg on hand|No sales/.test(document.querySelector(".reorder-vendors")?.textContent || ""), null, { timeout: 60000 }).catch(() => {});
    const t = (await first.textContent()) || "";
    check(/Avg on hand/.test(t), `${user} ${width}: weekly breakdown with Avg on hand`);
    if (user !== "reg_admin" && user !== "reg_staff") check(!/Revenue/.test(t), `${user}: no Revenue column`);
  }
  cleanPage(page, `${user} ${width} reorder`, user === "app_store_review" ? [/^403 POST \/api\/ordering\/cart/] : []);
  await ctx.close();
}

await browser.close();
done();
