// The website is StoreOps, not one store's dashboard (docs/M2_CONTRACT.md
// section 9.2, website part): no store's name, place or domain in the
// source, and the store's time zone only ever comes from branding.
//   node --test src/store/noStoreNames.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const frontend = join(here, "../..");
// Spelled in pieces so the server's name scan (M2 contract 9.2)
// doesn't count this file.
const NAMES = new RegExp(["spice[ _]?town", "stre" + "et[ _]kitchen", "ver" + "non", "spice" + "town\\.shop", "mac mini"].join("|"), "i");

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    if (name === "node_modules") return [];
    const p = join(dir, name);
    return statSync(p).isDirectory() ? files(p) : [p];
  });
}

// This file (it holds the pattern) is the only one left out.
const self = fileURLToPath(import.meta.url);
const sources = [...files(join(frontend, "src")), join(frontend, "index.html")].filter(
  (p) => /\.(jsx?|mjs|css|html|json)$/.test(p) && p !== self,
);

test("no store names, places or domains in the website source", () => {
  const hits = [];
  for (const p of sources) {
    readFileSync(p, "utf8")
      .split("\n")
      .forEach((line, i) => {
        if (NAMES.test(line)) hits.push(`${relative(frontend, p)}:${i + 1}: ${line.trim().slice(0, 100)}`);
      });
  }
  assert.deepEqual(hits, []);
});

test("no hard-coded store time zone outside format.js", () => {
  const hits = sources
    .filter((p) => !p.endsWith("format.js") && !p.endsWith(".test.mjs") && !p.includes("/fixtures/"))
    .filter((p) => /["']America\/[A-Za-z_]+["']/.test(readFileSync(p, "utf8")))
    .map((p) => relative(frontend, p));
  assert.deepEqual(hits, []);
});

// A clock time ("9:42 PM") is the store's, never the viewing device's: a
// toLocaleString / toLocaleTimeString call that shows an hour must pass the
// store's zone (format.js inStoreZone, or an explicit timeZone).
test("clock times are formatted in the store's time zone", () => {
  const hits = [];
  for (const p of sources.filter((f) => /\.(jsx?|mjs)$/.test(f) && !f.endsWith(".test.mjs"))) {
    const text = readFileSync(p, "utf8");
    const re = /toLocale(?:String|TimeString)\(/g;
    let m;
    while ((m = re.exec(text))) {
      // The call's argument list (to its matching parenthesis).
      let depth = 1;
      let i = m.index + m[0].length;
      for (; i < text.length && depth; i++) depth += text[i] === "(" ? 1 : text[i] === ")" ? -1 : 0;
      const args = text.slice(m.index + m[0].length, i);
      if (/\bhour\b/.test(args) && !/inStoreZone|timeZone/.test(args)) {
        hits.push(`${relative(frontend, p)}:${text.slice(0, m.index).split("\n").length}`);
      }
    }
  }
  assert.deepEqual(hits, []);
});
