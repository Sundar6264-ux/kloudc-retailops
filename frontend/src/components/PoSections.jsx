import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  bulkResetSection,
  bulkSetSection,
  createSectionVendorRule,
  deactivateSectionVendorRule,
  getSectionCartPreview,
  getSectionChanges,
  getSectionHistory,
  getSectionItems,
  getSectionOptions,
  getSectionVendorRules,
  moveItemsToSection,
  renameSection,
  resetItemSection,
  setItemSection,
  undoLastSectionChange,
  undoSectionRename,
} from "../api";
import { formatRestaurantTime } from "../format";
import InfoBlock from "./InfoBlock.jsx";
import { CartLineSection, OTHER, SectionInput, groupBySection } from "./SectionControls.jsx";

// PO sections: the heading each product prints under on the vendor PO PDF.
// Backend: routers/po_sections.py (served on /api/categories/*). Toast
// categories are not edited here (or anywhere) any more.

const LIMIT = 200;

const SHOW_OPTIONS = [
  ["", "All"],
  ["changed", "Changed"],
  ["item", "Item rules"],
  ["vendor", "Vendor rules"],
  ["toast", "Automatic"],
];

function whoWhen(by, at) {
  if (!by && !at) return "-";
  return [by, at ? formatRestaurantTime(at) : null].filter(Boolean).join(", ");
}

function catalogText(item) {
  return [item.item_category_group, item.item_category, item.item_subcategory].filter(Boolean).join(" / ");
}

function SourceBadge({ item }) {
  if (item.source === "item") return <span className="pill pill-info">Item rule</span>;
  if (item.source === "vendor") return <span className="pill pill-warn">Vendor rule: {item.rule?.match_value}</span>;
  return <span className="pill pill-neutral">Automatic</span>;
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

// --------------------------------------------------------------------------
// Items
// --------------------------------------------------------------------------

function ItemsTab({ options, sectionNames, onChanged, initialSection }) {
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [vendor, setVendor] = useState("");
  const [section, setSection] = useState(initialSection || "");
  const [show, setShow] = useState("");
  const [data, setData] = useState({ items: [], total: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(() => new Set());
  const [editing, setEditing] = useState(null);
  const [editValue, setEditValue] = useState("");
  const [bulkValue, setBulkValue] = useState("");
  const [busy, setBusy] = useState(false);
  const requestId = useRef(0);

  useEffect(() => {
    const handle = setTimeout(() => setSearch(searchInput.trim()), 300);
    return () => clearTimeout(handle);
  }, [searchInput]);

  const load = useCallback(async () => {
    const id = ++requestId.current;
    setLoading(true);
    setError(null);
    try {
      const res = await getSectionItems({ q: search, vendor, section, source: show, limit: LIMIT });
      if (id !== requestId.current) return;
      setData(res);
    } catch (err) {
      if (id === requestId.current) setError(err.message);
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }, [search, vendor, section, show]);

  useEffect(() => {
    load();
  }, [load]);

  function applyRows(rows) {
    const byId = Object.fromEntries(rows.map((r) => [r.item_id, r]));
    setData((d) => ({ ...d, items: d.items.map((i) => byId[i.item_id] || i) }));
  }

  async function run(fn, success) {
    setBusy(true);
    setError(null);
    try {
      const res = await fn();
      if (res.items) applyRows(res.items);
      if (res.item) applyRows([res.item]);
      onChanged(success(res));
      return true;
    } catch (err) {
      setError(err.message);
      return false;
    } finally {
      setBusy(false);
    }
  }

  function startEdit(item) {
    setEditing(item.item_id);
    setEditValue(item.section || "");
  }

  async function saveEdit(item) {
    const ok = await run(
      () => setItemSection(item.item_id, editValue),
      (res) => (res.changed ? `${item.name} now prints under ${res.item.section}.` : `${item.name} is already in that section.`),
    );
    if (ok) setEditing(null);
  }

  function resetItem(item, toAutomatic) {
    return run(
      () => resetItemSection(item.item_id, toAutomatic),
      (res) =>
        toAutomatic
          ? `${item.name} is back to its automatic section (${res.item?.section || item.automatic_section}).`
          : `${item.name} now follows the ${item.vendor_rule?.match_value} vendor rule.`,
    );
  }

  const selectedIds = [...selected];

  async function applyBulk() {
    const ok = await run(
      () => bulkSetSection(selectedIds, bulkValue),
      (res) => `Moved ${res.changed} of ${plural(selectedIds.length, "item")} to ${bulkValue.trim()}.`,
    );
    if (ok) {
      setSelected(new Set());
      setBulkValue("");
    }
  }

  async function resetBulk() {
    if (!window.confirm(`Put ${plural(selectedIds.length, "item")} back in their automatic section?`)) return;
    const ok = await run(
      () => bulkResetSection(selectedIds, true),
      (res) => `${res.changed} of ${plural(selectedIds.length, "item")} back to automatic.`,
    );
    if (ok) setSelected(new Set());
  }

  function toggle(itemId) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(itemId)) next.delete(itemId);
      else next.add(itemId);
      return next;
    });
  }

  const allShownSelected = data.items.length > 0 && data.items.every((i) => selected.has(i.item_id));
  function toggleAll() {
    setSelected((prev) => {
      const next = new Set(prev);
      if (allShownSelected) data.items.forEach((i) => next.delete(i.item_id));
      else data.items.forEach((i) => next.add(i.item_id));
      return next;
    });
  }

  const otherCount = (options.sections || []).find((s) => s.section === OTHER)?.items ?? 0;

  // The server sorts by name; list them the way the PO prints them - under
  // a heading per section (A-Z, Other last), then by name.
  const groups = useMemo(() => groupBySection(data.items), [data.items]);

  return (
    <>
      <div className="pc-toolbar">
        <input
          type="text"
          className="table-search"
          placeholder="Search name, item id, SKU or barcode…"
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
        />
        <select value={vendor} onChange={(e) => setVendor(e.target.value)} aria-label="Vendor">
          <option value="">All vendors</option>
          {(options.vendors || []).map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
        <select value={section} onChange={(e) => setSection(e.target.value)} aria-label="Section">
          <option value="">All sections</option>
          {sectionNames.map((s) => (
            <option key={s} value={s}>
              {s === OTHER ? "Unassigned / Other" : s}
            </option>
          ))}
        </select>
        <button
          type="button"
          className={section === OTHER ? "" : "btn-secondary"}
          onClick={() => setSection(section === OTHER ? "" : OTHER)}
          title="Items that print under Other on the PO (no section)"
        >
          Unassigned / Other ({otherCount})
        </button>
        <div className="segmented" role="tablist" aria-label="Show">
          {SHOW_OPTIONS.map(([value, label]) => (
            <button
              key={value || "all"}
              type="button"
              role="tab"
              aria-selected={show === value}
              className={show === value ? "active" : ""}
              onClick={() => setShow(value)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {selectedIds.length > 0 && (
        <div className="pc-bulk">
          <strong>{selectedIds.length} selected</strong>
          <SectionInput value={bulkValue} onChange={setBulkValue} sections={sectionNames} ariaLabel="Section for selected items" />
          <div className="pc-bulk-actions">
            <button type="button" disabled={busy || !bulkValue.trim()} onClick={applyBulk}>
              Set section for {selectedIds.length}
            </button>
            <button type="button" className="btn-secondary" disabled={busy} onClick={resetBulk}>
              Back to automatic
            </button>
            <button type="button" className="link-button" onClick={() => setSelected(new Set())}>
              Clear selection
            </button>
          </div>
        </div>
      )}

      {error && <p className="error">{error}</p>}
      {loading && <p className="muted">Loading…</p>}
      {!loading && data.items.length === 0 && !error && <p className="muted">No items match.</p>}

      {!loading && data.items.length > 0 && (
        <>
          <p className="muted pc-count">
            {plural(data.total, "item")}
            {data.items.length < data.total ? ` - showing the first ${data.items.length}, search to narrow down` : ""}
          </p>
          <div className="table-wrap">
            <table className="pc-table pc-items-table">
              <thead>
                <tr>
                  <th style={{ width: "2rem" }}>
                    <input type="checkbox" aria-label="Select all shown" checked={allShownSelected} onChange={toggleAll} />
                  </th>
                  <th>Item</th>
                  <th>Vendor</th>
                  <th>Now</th>
                  <th>Automatic</th>
                  <th>Changed by</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {groups.map(([groupSection, rows]) => [
                  <tr key={`h-${groupSection}`} className="pc-group-heading">
                    <td colSpan={7}>
                      {groupSection} <span className="muted">({rows.length})</span>
                    </td>
                  </tr>,
                  ...rows.map((item) => {
                    const isEditing = editing === item.item_id;
                    const changed = item.source !== "toast";
                    const sameAsAutomatic = item.automatic_section === item.section;
                    return (
                      <tr key={item.item_id} className={changed ? "pc-row-changed" : undefined}>
                        <td>
                          <input
                            type="checkbox"
                            aria-label={`Select ${item.name}`}
                            checked={selected.has(item.item_id)}
                            onChange={() => toggle(item.item_id)}
                          />
                        </td>
                        <td className="cell-wrap">
                          <div className="pc-name">{item.name}</div>
                          {catalogText(item) && (
                            <div className="muted pc-sub" title="The item's category (read-only)">
                              {catalogText(item)}
                            </div>
                          )}
                        </td>
                        <td className="cell-wrap muted pc-sub">{item.supplier || "No vendor"}</td>
                        <td className="cell-wrap">
                          {isEditing ? (
                            <form
                              className="ps-inline-form"
                              onSubmit={(e) => {
                                e.preventDefault();
                                if (editValue.trim()) saveEdit(item);
                              }}
                            >
                              <SectionInput value={editValue} onChange={setEditValue} sections={sectionNames} autoFocus />
                              <div className="pc-actions" style={{ justifyContent: "flex-start" }}>
                                <button type="submit" disabled={busy || !editValue.trim()}>
                                  Save
                                </button>
                                <button type="button" className="link-button" onClick={() => setEditing(null)}>
                                  Cancel
                                </button>
                              </div>
                            </form>
                          ) : (
                            <>
                              <div>{item.section}</div>
                              <SourceBadge item={item} />
                            </>
                          )}
                        </td>
                        <td className="cell-wrap muted" title={sameAsAutomatic ? `Automatic: ${item.automatic_section}` : undefined}>
                          {sameAsAutomatic ? "Same" : item.automatic_section}
                        </td>
                        <td className="muted pc-sub">{item.rule ? whoWhen(item.rule.created_by, item.rule.created_at) : "-"}</td>
                        <td>
                          {!isEditing && (
                            <div className="pc-actions">
                              <button type="button" className="btn-secondary" onClick={() => startEdit(item)}>
                                Edit
                              </button>
                              {item.source === "item" && item.vendor_rule && (
                                <button
                                  type="button"
                                  className="link-button"
                                  disabled={busy}
                                  onClick={() => resetItem(item, false)}
                                  title={`Use the ${item.vendor_rule.match_value} vendor rule (${item.vendor_rule.category}) again`}
                                >
                                  Use vendor rule
                                </button>
                              )}
                              {changed && (
                                <button type="button" className="link-button" disabled={busy} onClick={() => resetItem(item, true)}>
                                  Back to automatic
                                </button>
                              )}
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  }),
                ])}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}

// --------------------------------------------------------------------------
// Sections
// --------------------------------------------------------------------------

function SectionRow({ s, sectionNames, busy, onRename, onMove, onUndoRename, onShowItems }) {
  const [mode, setMode] = useState(null); // rename | move
  const [value, setValue] = useState("");
  const exists = sectionNames.some((n) => n.toLowerCase() === value.trim().toLowerCase() && n !== s.section);
  const kind = s.is_other ? "Lines with no section" : s.automatic ? "Automatic" : "Added";

  function open(next) {
    setMode(next);
    setValue("");
  }

  return (
    <tr className={s.items === 0 ? "pc-row-inactive" : undefined}>
      <td className="cell-wrap">
        <div className="pc-name">{s.section}</div>
        <div className="muted pc-sub">
          {kind}
          {s.item_rules ? ` - ${plural(s.item_rules, "item rule")}` : ""}
          {s.vendor_rules ? ` - ${plural(s.vendor_rules, "vendor rule")}` : ""}
        </div>
        {s.renamed_from.map((r) => (
          <div key={r.rule_id} className="muted pc-sub">
            {r.to === s.section ? "Renamed from" : "Includes"} {r.from}{" "}
            <button type="button" className="link-button" disabled={busy} onClick={() => onUndoRename(r)}>
              Undo rename
            </button>
          </div>
        ))}
      </td>
      <td className="num">
        {s.items > 0 ? (
          <button type="button" className="link-button" onClick={() => onShowItems(s.section)} title="Show these items">
            {s.items}
          </button>
        ) : (
          0
        )}
      </td>
      <td>
        {mode ? (
          <form
            className="ps-inline-form"
            onSubmit={async (e) => {
              e.preventDefault();
              if (!value.trim()) return;
              const ok = mode === "rename" ? await onRename(s, value.trim(), exists) : await onMove(s, value.trim());
              if (ok) setMode(null);
            }}
          >
            <SectionInput
              value={value}
              onChange={setValue}
              sections={sectionNames.filter((n) => n !== s.section && n !== OTHER)}
              placeholder={mode === "rename" ? "New name, or a section to merge into" : "Move current items to"}
              autoFocus
            />
            <div className="pc-actions" style={{ justifyContent: "flex-start" }}>
              <button type="submit" disabled={busy || !value.trim()}>
                {mode === "rename" ? (exists ? `Merge into ${value.trim()}` : "Rename") : "Move items"}
              </button>
              <button type="button" className="link-button" onClick={() => setMode(null)}>
                Cancel
              </button>
            </div>
            <div className="muted pc-sub">
              {mode === "rename"
                ? exists
                  ? "Merge: every item in this section (now and later) prints under the other section."
                  : "Rename: every item in this section (now and later) prints under the new name."
                : "Only the items in it now get moved (item rules). Rename or merge to cover future items too."}
            </div>
          </form>
        ) : (
          <div className="pc-actions">
            {!s.is_other && (
              <button type="button" className="btn-secondary" disabled={busy} onClick={() => open("rename")}>
                Rename / merge
              </button>
            )}
            {s.items > 0 && (
              <button type="button" className="link-button" disabled={busy} onClick={() => open("move")}>
                Move all items
              </button>
            )}
          </div>
        )}
      </td>
    </tr>
  );
}

function SectionsTab({ options, sectionNames, onChanged, onShowItems }) {
  const [filter, setFilter] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const sections = options.sections || [];
  const shown = sections.filter((s) => !filter.trim() || s.section.toLowerCase().includes(filter.trim().toLowerCase()));

  async function run(fn, success) {
    setBusy(true);
    setError(null);
    try {
      const res = await fn();
      onChanged(success(res));
      return true;
    } catch (err) {
      setError(err.message);
      return false;
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <p className="muted" style={{ marginTop: 0 }}>
        Every section the PO can print, with how many items print under it now. Rename a section, or merge it into
        another by typing that section's name. Renames also cover items added later. To add a new section, just type
        its name when you set an item's section.
      </p>
      <div className="pc-toolbar">
        <input
          type="text"
          className="table-search"
          placeholder="Find a section…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        <span className="muted pc-sub">{plural(sections.length, "section")}</span>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="table-wrap">
        <table className="pc-table">
          <thead>
            <tr>
              <th>Section</th>
              <th className="num">Items</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {shown.map((s) => (
              <SectionRow
                key={s.section}
                s={s}
                sectionNames={sectionNames}
                busy={busy}
                onShowItems={onShowItems}
                onRename={(row, to, merge) =>
                  run(
                    () => renameSection(row.section, to),
                    (res) =>
                      merge
                        ? `${row.section} merged into ${res.to_section || to}.`
                        : `${row.section} renamed to ${res.to_section || to}.`,
                  )
                }
                onMove={(row, to) =>
                  run(
                    () => moveItemsToSection({ from_section: row.section }, to),
                    (res) => `Moved ${res.changed} of ${plural(res.matched, "item")} from ${row.section} to ${to}.`,
                  )
                }
                onUndoRename={(r) =>
                  run(
                    () => undoSectionRename(r.rule_id),
                    () => `Rename undone: ${r.from} is back.`,
                  )
                }
              />
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

// --------------------------------------------------------------------------
// Vendor rules
// --------------------------------------------------------------------------

function VendorRulesTab({ options, sectionNames, onChanged, refreshKey }) {
  const [rules, setRules] = useState([]);
  const [showInactive, setShowInactive] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [vendor, setVendor] = useState("");
  const [section, setSection] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await getSectionVendorRules(showInactive);
      setRules(res.items);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [showInactive]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  async function run(fn, success) {
    setBusy(true);
    setError(null);
    try {
      const res = await fn();
      onChanged(success(res));
      await load();
      return true;
    } catch (err) {
      setError(err.message);
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function addRule(e) {
    e.preventDefault();
    const ok = await run(
      () => createSectionVendorRule(vendor.trim(), section.trim(), note.trim()),
      (rule) => `Every ${rule.match_value} item (${rule.items} now) prints under ${rule.category}.`,
    );
    if (ok) {
      setVendor("");
      setSection("");
      setNote("");
    }
  }

  async function moveNow() {
    const ok = await run(
      () => moveItemsToSection({ vendor: vendor.trim() }, section.trim()),
      (res) => `Moved ${res.changed} of ${plural(res.matched, "item")} from ${vendor.trim()} to ${section.trim()}.`,
    );
    if (ok) {
      setVendor("");
      setSection("");
    }
  }

  function turnOff(rule) {
    if (!window.confirm(`Turn off the ${rule.match_value} rule? Its items go back to their automatic section (or their own item rule).`))
      return;
    run(
      () => deactivateSectionVendorRule(rule.id),
      () => `The ${rule.match_value} rule is off.`,
    );
  }

  const ready = vendor.trim() && section.trim() && !busy;

  return (
    <>
      <p className="muted" style={{ marginTop: 0 }}>
        A vendor rule puts every item from that vendor (now and in future uploads) in one section. An item's own rule
        wins over it. On a PO, the rule for the PO's own vendor is used first.
      </p>
      {error && <p className="error">{error}</p>}
      <label className="pc-inline-check">
        <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} /> Show
        turned-off rules
      </label>
      {loading ? (
        <p className="muted">Loading…</p>
      ) : rules.length === 0 ? (
        <p className="muted">No vendor rules.</p>
      ) : (
        <div className="table-wrap">
          <table className="pc-table">
            <thead>
              <tr>
                <th>Vendor</th>
                <th>Section</th>
                <th className="num">Items</th>
                <th>Added by</th>
                <th>Note</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rules.map((rule) => (
                <tr key={rule.id} className={rule.active ? undefined : "pc-row-inactive"}>
                  <td className="cell-name">{rule.match_value}</td>
                  <td className="cell-wrap">{rule.category}</td>
                  <td className="num">{rule.active ? rule.items : "-"}</td>
                  <td className="muted pc-sub">{whoWhen(rule.created_by, rule.created_at)}</td>
                  <td className="cell-wrap muted pc-sub">{rule.note || "-"}</td>
                  <td>
                    <div className="pc-actions">
                      {rule.active ? (
                        <button type="button" className="btn-secondary" disabled={busy} onClick={() => turnOff(rule)}>
                          Turn off
                        </button>
                      ) : (
                        <span className="muted pc-sub">Off ({whoWhen(rule.updated_by, rule.updated_at)})</span>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <form className="pc-rule-form" onSubmit={addRule}>
        <h3>A vendor's items</h3>
        <label className="pc-vendor-field">
          <span>Vendor</span>
          <input list="ps-rule-vendors" value={vendor} onChange={(e) => setVendor(e.target.value)} placeholder="Vendor name" />
          <datalist id="ps-rule-vendors">
            {(options.vendors || []).map((v) => (
              <option key={v} value={v} />
            ))}
          </datalist>
        </label>
        <label className="pc-vendor-field">
          <span>Section</span>
          <SectionInput value={section} onChange={setSection} sections={sectionNames.filter((n) => n !== OTHER)} />
        </label>
        <label className="pc-vendor-field">
          <span>Note (optional, for the rule)</span>
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why" />
        </label>
        <div className="pc-actions" style={{ justifyContent: "flex-start" }}>
          <button type="submit" disabled={!ready}>
            Add rule (now and future items)
          </button>
          <button type="button" className="btn-secondary" disabled={!ready} onClick={moveNow}>
            Move current items only
          </button>
        </div>
      </form>
    </>
  );
}

// --------------------------------------------------------------------------
// PDF preview
// --------------------------------------------------------------------------

function PreviewTab({ sectionNames, onChanged, refreshKey }) {
  const [supplier, setSupplier] = useState("");
  const [data, setData] = useState({ suppliers: [], sections: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await getSectionCartPreview(supplier);
      setData(res);
      if (!supplier && res.suppliers.length > 0) setSupplier(res.suppliers[0].supplier);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [supplier]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  let no = 0;
  return (
    <>
      <p className="muted" style={{ marginTop: 0 }}>
        A vendor's current cart, grouped exactly as its PO PDF prints: sections A to Z, Other last, items by name.
      </p>
      <div className="pc-toolbar">
        <select value={supplier} onChange={(e) => setSupplier(e.target.value)} aria-label="Cart vendor">
          {data.suppliers.length === 0 && <option value="">No vendors in the cart</option>}
          {data.suppliers.map((s) => (
            <option key={s.supplier} value={s.supplier}>
              {s.supplier} ({plural(s.lines, "line")})
            </option>
          ))}
        </select>
        <button type="button" className="btn-secondary" onClick={load}>
          Refresh
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {loading && <p className="muted">Loading…</p>}
      {!loading && supplier && data.sections.length === 0 && <p className="muted">This vendor's cart is empty.</p>}
      {!loading && data.sections.length > 0 && (
        <div className="table-wrap">
          <table className="pc-table ps-preview">
            <thead>
              <tr>
                <th className="num">S.No.</th>
                <th>Item</th>
                <th>Supplier code</th>
                <th className="num">Qty (cases)</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.sections.map((group) => [
                <tr key={`h-${group.section}`} className="ps-section-heading">
                  <td colSpan={5}>{group.section}</td>
                </tr>,
                ...group.lines.map((line) => {
                  no += 1;
                  return (
                    <tr key={line.cart_item_id}>
                      <td className="num">{no}</td>
                      <td className="cell-wrap">{line.name}</td>
                      <td className="muted">{line.supplier_item_id || "-"}</td>
                      <td className="num">{line.qty}</td>
                      <td>
                        <CartLineSection
                          line={{ id: line.cart_item_id, item_id: line.item_id, name: line.name, section: group.section }}
                          sections={sectionNames.filter((n) => n !== OTHER)}
                          canEdit
                          onSaved={() => onChanged(`Section saved for ${line.name}.`)}
                          onError={setError}
                        />
                      </td>
                    </tr>
                  );
                }),
              ])}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

// --------------------------------------------------------------------------
// History
// --------------------------------------------------------------------------

const HISTORY_LIMIT = 100;

function ruleFor(r) {
  if (r.rule_type === "vendor") return `Vendor: ${r.match_value}`;
  if (r.rule_type === "rename") return `Section: ${r.match_value}`;
  return r.item_name || r.match_value;
}

function ruleWhat(r) {
  if (r.rule_type === "rename") return `Renamed to ${r.section}`;
  return r.section;
}

function HistoryTab({ onUndo, refreshKey }) {
  const [rows, setRows] = useState([]);
  const [changes, setChanges] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [h, c] = await Promise.all([getSectionHistory({ limit: HISTORY_LIMIT }), getSectionChanges(15)]);
      setRows(h.items);
      setChanges(c.items);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  async function undo(change) {
    if (!window.confirm(`Undo the last change? (${change.summary})`)) return;
    setBusy(true);
    setError(null);
    try {
      await onUndo(change.batch);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <p className="muted">Loading…</p>;
  return (
    <>
      {error && <p className="error">{error}</p>}
      <h3 className="ps-h3">Recent changes</h3>
      {changes.length === 0 ? (
        <p className="muted">No changes yet.</p>
      ) : (
        <div className="table-wrap">
          <table className="pc-table">
            <thead>
              <tr>
                <th>Change</th>
                <th>Made by</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {changes.map((c) => (
                <tr key={c.batch}>
                  <td className="cell-wrap">{c.summary}</td>
                  <td className="muted pc-sub">{whoWhen(c.by, c.at)}</td>
                  <td>
                    {c.can_undo && (
                      <div className="pc-actions">
                        <button type="button" className="btn-secondary" disabled={busy} onClick={() => undo(c)}>
                          Undo last change
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h3 className="ps-h3">Rule history</h3>
      {rows.length === 0 ? (
        <p className="muted">No rules yet.</p>
      ) : (
        <div className="table-wrap">
          {rows.length >= HISTORY_LIMIT && <p className="muted pc-sub">Showing the latest {rows.length} rules.</p>}
          <table className="pc-table">
            <thead>
              <tr>
                <th>For</th>
                <th>Section</th>
                <th>Made by</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className={r.active ? undefined : "pc-row-inactive"}>
                  <td className="cell-wrap">{ruleFor(r)}</td>
                  <td className="cell-wrap">{ruleWhat(r)}</td>
                  <td className="muted pc-sub">{whoWhen(r.created_by, r.created_at)}</td>
                  <td className="muted pc-sub">
                    {r.active ? "In use" : `Replaced / off (${whoWhen(r.updated_by, r.updated_at)})`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

// --------------------------------------------------------------------------
// Page
// --------------------------------------------------------------------------

const TABS = [
  ["items", "Items"],
  ["sections", "Sections"],
  ["vendors", "Vendor rules"],
  ["preview", "PDF preview"],
  ["history", "History"],
];

export default function PoSections() {
  const [tab, setTab] = useState("items");
  const [options, setOptions] = useState({ combos: [], vendors: [], sections: [] });
  const [optionsError, setOptionsError] = useState(null);
  const [message, setMessage] = useState(null);
  const [undoError, setUndoError] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [itemsSection, setItemsSection] = useState("");
  const [itemsKey, setItemsKey] = useState(0);

  const reloadOptions = useCallback(() => {
    getSectionOptions()
      .then((res) => {
        setOptions(res);
        setOptionsError(null);
      })
      .catch((err) => setOptionsError(err.message));
  }, []);

  useEffect(() => {
    reloadOptions();
  }, [reloadOptions]);

  const sectionNames = useMemo(() => (options.sections || []).map((s) => s.section), [options.sections]);

  // After any change: show what happened (with Undo) and refresh counts.
  const onChanged = useCallback(
    (text) => {
      setMessage(text);
      setUndoError(null);
      reloadOptions();
      setRefreshKey((k) => k + 1);
    },
    [reloadOptions],
  );

  const undo = useCallback(
    async (batch) => {
      let target = batch;
      if (!target) {
        const recent = await getSectionChanges(1);
        target = recent.items[0]?.batch;
        if (!target) throw new Error("Nothing to undo.");
      }
      const res = await undoLastSectionChange(target);
      setMessage(`Undone: ${res.summary}.`);
      setUndoError(null);
      reloadOptions();
      setRefreshKey((k) => k + 1);
      setItemsKey((k) => k + 1);
    },
    [reloadOptions],
  );

  function showItems(section) {
    setItemsSection(section);
    setItemsKey((k) => k + 1);
    setTab("items");
  }

  return (
    <section className="card">
      <InfoBlock brief="Choose the heading each product prints under on vendor PO PDFs.">
        <p style={{ marginTop: 0 }}>
          A <strong>PO section</strong> is the heading an item prints under on a vendor's purchase order, so the
          vendor can pick the order aisle by aisle. <strong>Automatic</strong> is the section picked from the item's
          category and name. Set a different section for one item, several items, or every item from a vendor (a
          vendor rule). An item's own rule wins over a vendor rule. Type a new name to make a new section.
        </p>
        <p style={{ marginBottom: 0 }}>
          The <strong>Sections</strong> tab renames or merges sections. Lines with no section print under{" "}
          <strong>Other</strong>, always last. Only the PO section changes here: the item's category stays as it is.
          Every change records who made it and when, and the last change can be undone (History).
        </p>
      </InfoBlock>
      {optionsError && <p className="error">{optionsError}</p>}
      {message && (
        <p className="pc-message ps-message">
          {message}{" "}
          <button
            type="button"
            className="link-button"
            onClick={() => undo(null).catch((err) => setUndoError(err.message))}
          >
            Undo last change
          </button>
        </p>
      )}
      {undoError && <p className="error">{undoError}</p>}
      <div className="segmented pc-tabs" role="tablist" aria-label="PO sections tabs">
        {TABS.map(([value, label]) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={tab === value}
            className={tab === value ? "active" : ""}
            onClick={() => setTab(value)}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === "items" && (
        <ItemsTab
          key={itemsKey}
          options={options}
          sectionNames={sectionNames}
          onChanged={onChanged}
          initialSection={itemsSection}
        />
      )}
      {tab === "sections" && (
        <SectionsTab options={options} sectionNames={sectionNames} onChanged={onChanged} onShowItems={showItems} />
      )}
      {tab === "vendors" && (
        <VendorRulesTab options={options} sectionNames={sectionNames} onChanged={onChanged} refreshKey={refreshKey} />
      )}
      {tab === "preview" && <PreviewTab sectionNames={sectionNames} onChanged={onChanged} refreshKey={refreshKey} />}
      {tab === "history" && <HistoryTab onUndo={undo} refreshKey={refreshKey} />}
    </section>
  );
}
