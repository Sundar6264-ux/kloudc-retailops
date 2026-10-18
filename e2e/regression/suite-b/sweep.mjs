// Every Suite B page x role x width: screenshot + console / API errors + no page h-scroll + where it landed.
// node sweep.mjs [roles] [widths] [only-page-names]
import { chromium, as, settle, hscroll, check, done, SHOTS, BASE, ignorable, ROLES, WIDTHS } from "./lib.mjs";
import { PAGES } from "./sweep-pages.mjs";

const roles = process.argv[2] ? process.argv[2].split(",") : ROLES;
const widths = process.argv[3] ? process.argv[3].split(",").map(Number) : WIDTHS;
const only = process.argv[4] ? process.argv[4].split(",") : null;
const browser = await chromium.launch();
const summary = [];
for (const role of roles) for (const width of widths) {
  const { ctx, page } = await as(browser, role, width);
  for (const [name, path] of PAGES) {
    if (only && !only.includes(name)) continue;
    page.errors.length = 0; page.bad.length = 0;
    const t0 = Date.now();
    await page.goto(`${BASE}/#${path}`);
    await settle(page);
    const landed = page.url().replace(BASE, "").replace("/#", "");
    const hs = await hscroll(page);
    const tag = `${role}-${name}-${width}`;
    await page.screenshot({ path: `${SHOTS}/sweep-${tag}.png` });
    const errs = page.errors.filter((e) => !ignorable(e) && !/Failed to load resource/.test(e));
    const bad = page.bad.filter((e) => !ignorable(e));
    summary.push(`${tag}\t${landed}\t${bad.join(" ; ")}`);
    console.log(`${tag}: ${((Date.now() - t0) / 1000).toFixed(1)}s landed ${landed} hscroll=${hs}${bad.length ? " api>=400: " + bad.slice(0, 4).join(" ; ") : ""}`);
    check(hs <= 1, `${tag} no page h-scroll (${hs})`);
    check(errs.length === 0, `${tag} console clean ${errs.slice(0, 3).join(" ; ")}`);
    check(bad.length === 0, `${tag} no failed API calls ${bad.slice(0, 3).join(" ; ")}`);
  }
  await ctx.close();
}
await browser.close();
(await import("node:fs")).writeFileSync(`${SHOTS}/../sweep-summary.tsv`, summary.join("\n") + "\n");
done();
