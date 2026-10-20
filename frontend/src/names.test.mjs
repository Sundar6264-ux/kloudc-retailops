// Website ready A9: one name per thing (docs/WEBSITE_READY_CONTRACT.md 4).
// Retired names never appear on screen or in help; the comments that explain
// history are left alone.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const SRC = new URL("./", import.meta.url).pathname;
const HELP = new URL("../../content/help/", import.meta.url).pathname;

function files(dir, exts) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...files(p, exts));
    else if (exts.some((e) => name.endsWith(e)) && !name.endsWith(".test.mjs")) out.push(p);
  }
  return out;
}

// [retired, use instead]
const RETIRED = [
  [/Purchase Cart\b/, "Purchase Order Cart"],
  [/\bMark placed\b/i, "Order placed"],
  [/\b2FA\b/, "Two-step sign-in"],
  [/["'>][^"'<]*\bTOTP\b[^"'<]*["'<]/, "Two-step sign-in"],
  [/\bTwo-factor\b/i, "Two-step sign-in"],
  [/Missing Barcodes|Missing Vendors|Unassigned Items/, "sentence case: Missing barcodes / Missing vendors"],
  [/\bStore code\b/, "Store ID"],
  [/Export data\b/, "Export all data"],
];

const isComment = (line) => /^\s*(\/\/|\/\*|\*|\{\s*\/\*)/.test(line);

test("retired names stay retired in the website and help", () => {
  const hits = [];
  for (const f of [...files(SRC, [".jsx", ".js"]), ...files(HELP, [".json"])]) {
    const lines = readFileSync(f, "utf8").split("\n");
    lines.forEach((line, i) => {
      if (isComment(line)) return;
      for (const [re, instead] of RETIRED) {
        if (!re.test(line)) continue;
        // The one sentence that tells older app users their button's old name.
        if (/older app versions/.test(line)) continue;
        // Search aliases may keep an old word so people still find the page.
        if (f.endsWith("glossary.json") && /^\s*"[^"]*",?$/.test(line)) continue;
        hits.push(`${f.replace(SRC, "src/").replace(HELP, "help/")}:${i + 1} -> ${instead}`);
      }
    });
  }
  assert.deepEqual(hits, []);
});

test("section 4 names are used for the new pages and console tabs", async () => {
  const { FEATURE_ROUTES } = await import("./featureRoutes.js");
  const { PLATFORM_TABS } = await import("./platform/platformModel.js");
  const labels = new Set(FEATURE_ROUTES.map((r) => r.label));
  for (const n of ["Item vendors", "Data & usage", "Store settings", "Integrations", "Purchase Order Cart", "New order"]) {
    assert.ok(labels.has(n), n);
  }
  assert.deepEqual(PLATFORM_TABS.map((t) => t.label), ["Requests", "Stores", "Hosting", "Audit", "Masters", "Platform settings", "Help"]);
  const { STORE_DETAIL_TABS } = await import("./platform/consoleModel.js");
  assert.deepEqual(STORE_DETAIL_TABS.map((t) => t.label), ["Overview", "Usage & plan", "Settings", "Data", "Audit"]);
});
