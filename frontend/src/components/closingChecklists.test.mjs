// node --test src/components/closingChecklists.test.mjs (no deps).
import assert from "node:assert/strict";
import test from "node:test";

import { closingTileGroups } from "./closingChecklists.js";

const schema = (type, extra = {}, settings = {}) => ({
  checklist_type: type,
  title: `${type} title`,
  short_name: null,
  settings: { closing: true, whatsapp_group: "store", ...settings },
  sections: [],
  ...extra,
});

test("a new store's pack checklist (generic daily_closing) gets the Store closing tile", () => {
  const groups = closingTileGroups({
    daily_closing: schema("daily_closing", { title: "Daily Closing Checklist", short_name: "Closing" }),
  });
  assert.deepEqual(groups, [
    { key: "store", label: "Store closing", checklists: [{ type: "daily_closing", name: "Closing" }] },
  ]);
});

test("kitchen-group checklists share the Kitchen closing tile, in the server's order", () => {
  const groups = closingTileGroups({
    front: schema("front", { short_name: "Front" }),
    line: schema("line", { short_name: "Line" }, { whatsapp_group: "kitchen" }),
    prep: schema("prep", { short_name: "Prep" }, { whatsapp_group: "kitchen" }),
  });
  assert.deepEqual(
    groups.map((g) => [g.key, g.checklists.map((c) => c.type)]),
    [
      ["store", ["front"]],
      ["kitchen", ["line", "prep"]],
    ],
  );
});

test("checklists that are not closing ones, or broken entries, are left out", () => {
  const groups = closingTileGroups({
    opening: schema("opening", {}, { closing: false }),
    temp: schema("temp", { settings: undefined }),
    junk: null,
    nogroup: schema("nogroup", { short_name: "  " }, { whatsapp_group: null }),
  });
  assert.deepEqual(groups, [
    { key: "store", label: "Store closing", checklists: [{ type: "nogroup", name: "nogroup title" }] },
  ]);
});

test("no closing checklists (or no answer yet) means no closing tiles", () => {
  assert.deepEqual(closingTileGroups({}), []);
  assert.deepEqual(closingTileGroups(null), []);
  assert.deepEqual(closingTileGroups(undefined), []);
});
