// Website ready A7 + A8: help per POS (same rule as the server's
// app/help_content.py only_for_pos) and "your POS" for a CSV-only store.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { buildHelp, contentForPos, fillDeep, onlyForPos, storeWords } from "./helpModel.js";
import { storeView, normalizeBranding } from "../../store/branding.js";

const HELP_DIR = new URL("../../../../content/help/", import.meta.url);
const read = (name) => JSON.parse(readFileSync(new URL(name, HELP_DIR), "utf8"));
const RAW = {
  manifest: read("manifest.json"),
  pages: read("pages.json"),
  articles: readdirSync(new URL("articles/", HELP_DIR)).map((f) => read(`articles/${f}`)),
  gettingStarted: read("getting-started.json"),
  faq: read("faq.json"),
  troubleshooting: read("troubleshooting.json"),
  glossary: read("glossary.json"),
  glossaryCategories: read("glossary-categories.json"),
  contact: read("contact.json"),
};
const HELP = buildHelp(RAW);

const SYNTHETIC = {
  pages: [{ id: "a" }, { id: "b", pos: ["toast"], info: { summary: "Toast Purchasing API" } }],
  blocks: [{ type: "p", text: "for all" }, { type: "note", pos: ["toast"], text: "Toast Purchasing API orders" }],
};

test("entries for another POS are dropped", () => {
  for (const pos of ["clover", "csv"]) {
    const out = JSON.stringify(onlyForPos(SYNTHETIC, pos));
    assert.ok(!out.includes("Toast Purchasing API"), pos);
    assert.ok(out.includes("for all"));
  }
  assert.ok(JSON.stringify(onlyForPos(SYNTHETIC, "toast")).includes("Toast Purchasing API"));
});

test("an unknown POS (signed out / master) keeps every entry, labelled", () => {
  const out = onlyForPos(SYNTHETIC, null);
  assert.equal(out.pages[1].pos_label, "Toast only");
  assert.equal(out.blocks[1].pos_label, "Toast only");
  assert.equal(out.blocks[0].pos_label, undefined);
});

function rendered(pos, posName) {
  const c = contentForPos(HELP, pos);
  const words = storeWords({ posName });
  return JSON.stringify(fillDeep({ p: c.pageList, a: c.articleList, f: c.faq, t: c.troubleshooting, g: c.conceptList, s: c.gettingStarted }, words));
}

test("a CSV-only store never reads 'CSV upload' or a POS brand note", () => {
  const view = storeView(normalizeBranding(null), { pos_provider: "csv", pos_name: "CSV upload", store_timezone: "America/Chicago" });
  assert.equal(view.posName, "your POS");
  assert.equal(view.posProvider, "csv");
  assert.equal(view.timezone, "America/Chicago");
  const text = rendered("csv", view.posName);
  assert.ok(!text.includes("CSV upload"));
  assert.ok(!text.includes("{pos}"));
  assert.ok(!text.includes("Toast API client ID"));
  assert.ok(text.includes("No POS connection"));
});

test("Clover sees no Toast-only entry; Toast sees its own", () => {
  assert.ok(!rendered("clover", "Clover").includes("Toast API client ID"));
  assert.ok(rendered("toast", "Toast").includes("Toast API client ID"));
  assert.ok(!rendered("toast", "Toast").includes("No POS connection"));
});
