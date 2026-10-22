// node --test src/ui/help/helpCssHover.test.mjs (no deps).
// index.css paints every hovered <button> solid brand colour
// (`button:hover:not(:disabled) { background: var(--primary-hover) }`,
// gotcha #18). A Help Center button style whose hover rule doesn't set its
// own background turns into a red / green block with dark text when the
// mouse rests on it (seen on the Help tiles and the Glossary area tabs).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const CSS = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "help.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

// .kc-btn is never used alone: each variant (-primary, -secondary, -ghost)
// sets its own hover background.
const ALWAYS_WITH_VARIANT = new Set([".kc-btn:hover:not(:disabled)"]);

test("every Help hover rule sets its own background (not the global red fill)", () => {
  const withBackground = new Set();
  const hoverSelectors = new Set();
  for (const m of CSS.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selectors = m[1].split(",").map((s) => s.trim().replace(/\s+/g, " "));
    for (const sel of selectors) {
      if (!/:hover:not\(:disabled\)/.test(sel)) continue;
      hoverSelectors.add(sel);
      if (/(^|;|\s)background(-color)?\s*:/.test(m[2])) withBackground.add(sel);
    }
  }
  assert.ok(hoverSelectors.has(".kc-tiletab:hover:not(:disabled)"));
  assert.ok(hoverSelectors.has(".kc-area-tab:hover:not(:disabled)"));
  const missing = [...hoverSelectors].filter((s) => !withBackground.has(s) && !ALWAYS_WITH_VARIANT.has(s));
  assert.deepEqual(missing, []);
});
