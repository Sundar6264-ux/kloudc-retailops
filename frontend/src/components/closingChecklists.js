// Overview "closing" tiles: which checklists they follow. A store's closing
// checklists are its own (store pack / checklist builder, settings.closing),
// never a fixed list of checklist types - GET /api/checklists/schemas gives
// the active ones in the admins' order. The ones posting to the "kitchen"
// WhatsApp group share the Kitchen closing tile; the rest share the Store
// closing tile. Pure (no React): tested by closingChecklists.test.mjs.

function named(schema) {
  const name = [schema.short_name, schema.title].find((v) => typeof v === "string" && v.trim());
  return { type: schema.checklist_type, name: name ? name.trim() : schema.checklist_type };
}

// `schemas`: the `schemas` object of GET /api/checklists/schemas (keyed by
// type, in order). Returns [{ key, label, checklists: [{ type, name }] }],
// at most one tile per group and no tile for a group with no checklist.
export function closingTileGroups(schemas) {
  const list = Object.values(schemas && typeof schemas === "object" ? schemas : {}).filter(
    (s) => s && typeof s.checklist_type === "string" && s.settings && s.settings.closing === true,
  );
  const kitchen = list.filter((s) => s.settings.whatsapp_group === "kitchen").map(named);
  const store = list.filter((s) => s.settings.whatsapp_group !== "kitchen").map(named);
  const groups = [];
  if (store.length) groups.push({ key: "store", label: "Store closing", checklists: store });
  if (kitchen.length) groups.push({ key: "kitchen", label: "Kitchen closing", checklists: kitchen });
  return groups;
}
