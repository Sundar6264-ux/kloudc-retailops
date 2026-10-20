// Website ready A10: route-level code splitting. Reads the last `vite build`
// (dist/.vite/manifest.json; skipped when there is no build): no chunk over
// 500 kB, React in its own vendor chunk, and the entry loads no page - the
// first page fetches only its own chunk.
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync, statSync } from "node:fs";

const DIST = new URL("../dist/", import.meta.url);
const MANIFEST = new URL(".vite/manifest.json", DIST);
const built = existsSync(MANIFEST);
const manifest = built ? JSON.parse(readFileSync(MANIFEST, "utf8")) : {};
const entry = Object.values(manifest).find((c) => c.isEntry);

test("no chunk is over 500 kB", { skip: !built && "no build" }, () => {
  for (const c of Object.values(manifest)) {
    const size = statSync(new URL(c.file, DIST)).size;
    assert.ok(size <= 500 * 1024, `${c.file}: ${Math.round(size / 1024)} kB`);
  }
});

test("React and the router are a vendor chunk of their own", { skip: !built && "no build" }, () => {
  const vendor = Object.values(manifest).filter((c) => /vendor-react/.test(c.file));
  assert.equal(vendor.length, 1);
  assert.ok((entry.imports || []).some((k) => /vendor-react/.test(manifest[k]?.file || k)));
});

test("the entry loads no page; every page is lazy", { skip: !built && "no build" }, () => {
  const eager = (entry.imports || []).map((k) => manifest[k]?.src || k);
  assert.ok(!eager.some((src) => /src\/pages\//.test(src)), eager.join(", "));
  const lazy = (entry.dynamicImports || []).map((k) => manifest[k]?.src || k);
  for (const page of ["src/pages/Overview.jsx", "src/pages/LoginPage.jsx", "src/pages/PlatformConsolePage.jsx",
    "src/pages/StoreDataPage.jsx", "src/pages/ItemVendorsPage.jsx", "src/ui/help/HelpCenter.jsx"]) {
    assert.ok(lazy.includes(page), `${page} is not its own chunk`);
  }
});

test("App.jsx imports no page eagerly (source check, no build needed)", () => {
  const app = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");
  assert.ok(!/^import \w+ from "\.\/pages\//m.test(app), "use React.lazy for pages");
  assert.ok(/path="\/settings\/data"/.test(app) && /path="\/setup\/item-vendors"/.test(app));
});
