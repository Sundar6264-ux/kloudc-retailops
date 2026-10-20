// Screenshots of track B's screens from the harness build, at 1440 and 375 px
// in light and dark, with a sideways-scroll and console-error check.
//   HARNESS_URL=http://127.0.0.1:8882/ SHOTS_OUT=<dir> node e2e/data-harness/shots.mjs
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import { join } from "node:path";

const URL_BASE = process.env.HARNESS_URL || "http://127.0.0.1:8882/";
const OUT = process.env.SHOTS_OUT || "/tmp/so-wrb-shots";
mkdirSync(OUT, { recursive: true });
const VIEWS = [
  ["data-usage", "view=page"],
  ["data-usage-custom-plan", "view=page&plan=custom&empty=1"],
  ["data-usage-single-store", "view=page&single=1"],
  ["console-usage-plan", "view=usage"],
  ["console-data-scheduled", "view=data&off=scheduled"],
  ["console-data-active", "view=data&off=active"],
  ["console-data-deleted", "view=data&off=deleted"],
];
const problems = [];
const browser = await chromium.launch();
for (const theme of ["light", "dark"]) {
  for (const width of [1440, 375]) {
    const ctx = await browser.newContext({ viewport: { width, height: width === 375 ? 812 : 900 }, colorScheme: theme });
    for (const [name, query] of VIEWS) {
      const page = await ctx.newPage();
      page.on("pageerror", (e) => problems.push(`${name} ${width} ${theme}: ${e.message}`));
      page.on("console", (m) => m.type() === "error" && problems.push(`${name} ${width} ${theme}: console ${m.text()}`));
      await page.goto(`${URL_BASE}?${query}&theme=${theme}`);
      await page.waitForTimeout(400);
      if (name === "console-data-scheduled") {
        await page.getByRole("button", { name: "Delete store data now" }).click();
        await page.waitForTimeout(150);
      }
      const wide = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      if (wide > 0) problems.push(`${name} ${width} ${theme}: sideways scroll ${wide}px`);
      const h = await page.evaluate(() => !!document.querySelector("h1, h2"));
      if (!h) problems.push(`${name} ${width} ${theme}: no heading`);
      await page.screenshot({ path: join(OUT, `${name}-${width}-${theme}.png`), fullPage: true });
      await page.close();
    }
    await ctx.close();
  }
}
await browser.close();
console.log(problems.length ? `PROBLEMS:\n${problems.join("\n")}` : `OK: ${VIEWS.length * 4} screenshots, no sideways scroll, no errors`);
process.exit(problems.length ? 1 : 0);
