// Every Suite A page x role x width: loads, lands where access says, no console /
// page errors, no unexpected API errors, no sideways page scroll, a screenshot each.
//   node sweep.mjs [role,...] [width,...]
import { launch, as, go, check, section, done, hscroll, shot, cleanPage, WIDTHS, USERS, sleep } from "./lib.mjs";

const PAGES = [
  { hash: "/purchase-order", feature: "purchase_order_cart", heading: /Purchase Order Cart/i },
  { hash: "/supplier-projection", feature: "supplier_projection", heading: /New order/i },
  { hash: "/reorder", feature: "reorder_candidates", heading: /Reorder Candidates/i },
  { hash: "/vendor-calendar", feature: "vendor_calendar", heading: /Vendor (Order )?Calendar/i },
  { hash: "/fix-issues", feature: "inventory_reports", heading: /Fix Issues/i },
  { hash: "/issue-leads", admin: true, heading: /Category & vendor leads/i },
  { hash: "/inventory-api", admin: true, heading: /Inventory from Toast/i },
  { hash: "/delivery-review", feature: "delivery_review", heading: /Delivery/i },
  { hash: "/invoices", feature: "invoice_submission", heading: /Invoice/i },
  { hash: "/items-sold", feature: "items_sold", heading: /Items Sold/i },
  { hash: "/open-orders", feature: "open_orders", heading: /Open Checks/i },
  { hash: "/po-sections", feature: "category_edits", heading: /PO sections/i },
  { hash: "/placed-orders", feature: "purchase_order_cart", heading: /Placed Orders/i },
  { hash: "/vendor-contacts", feature: "purchase_order_cart", noDemo: true, heading: /Vendor contacts/i },
];

const roles = (process.argv[2] || "reg_admin,reg_staff,reg_hide,app_store_review,reg_viewer,reg_lead").split(",");
const widths = process.argv[3] ? process.argv[3].split(",").map(Number) : WIDTHS;

const browser = await launch();
for (const role of roles) {
  for (const width of widths) {
    section(`${role} @ ${width}`);
    const { ctx, page } = await as(browser, role, width);
    const me = await (await ctx.request.get(`${process.env.REG_BASE || "http://127.0.0.1:" + (process.env.REG_PORT || 8830)}/api/auth/me`)).json();
    const feats = new Set(me.allowed_features || []);
    const demo = !!me.demo || USERS[role].role === "demo";
    for (const p of PAGES) {
      const allowed = p.admin ? me.is_admin : (me.is_admin || feats.has(p.feature)) && !(p.noDemo && demo);
      const t0 = Date.now();
      await go(page, p.hash);
      const url = page.url();
      const landed = url.includes(`#${p.hash}`);
      const h1 = (await page.locator("main h1, main h2, .page-header h1, h1").first().textContent().catch(() => "")) || "";
      if (allowed) {
        check(landed && p.heading.test(h1), `${role} ${width} ${p.hash}: opens (h1 "${h1.trim().slice(0, 40)}")`);
      } else if (p.noDemo && demo && landed) {
        // the page may open and say the demo account can't use it (the API refuses it)
        check(/demo account can't/i.test(await page.locator("main").textContent()), `${role} ${width} ${p.hash}: demo told it can't use the page`);
      } else {
        check(!landed || !p.heading.test(h1), `${role} ${width} ${p.hash}: no access -> redirected (${url.split("#")[1]})`);
      }
      const hs = await hscroll(page);
      check(hs <= 1, `${role} ${width} ${p.hash}: no sideways page scroll (${hs}px)`);
      await sleep(250);
      await shot(page, p.hash.replace(/\W+/g, "_"), { fullPage: true });
      cleanPage(page, `${role} ${width} ${p.hash}`, [
        // Hide-sales users get 403 from sales-only endpoints; pages hide those parts.
        ...(USERS[role].role === "hide_sales" || role === "app_store_review" || role === "reg_lead" ? [/^403 GET \/api\/(sales|insights)/] : []),
      ]);
      const secs = ((Date.now() - t0) / 1000).toFixed(1);
      if (secs > 20) console.log(`    slow: ${p.hash} ${secs}s`);
    }
    await ctx.close();
  }
}
await browser.close();
done();
