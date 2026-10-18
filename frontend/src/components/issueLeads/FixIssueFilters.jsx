import { useEffect, useId, useRef, useState } from "react";
import {
  LEAD_NONE,
  TASK_ANY,
  TASK_ASSIGNED,
  TASK_NOT_ASSIGNED,
  activeFilterCount,
  leadOptions,
  sectionOptions,
  vendorOptions,
} from "./issueLeadsModel.js";

// Fix Issues filters (W4): PO section(s), vendor, lead and whether a lead's
// daily task lists the item. `rows` are the current tab's rows before these
// filters, so every option shows how many rows it would leave.
export default function FixIssueFilters({ rows, savedLeads, filters, onChange, shownCount }) {
  const sections = sectionOptions(rows);
  const vendors = vendorOptions(rows);
  const { leads, none } = leadOptions(rows, savedLeads);
  const set = (patch) => onChange({ ...filters, ...patch });
  const active = activeFilterCount(filters);

  return (
    <div className="fi-filters" role="group" aria-label="Filter the list">
      <SectionPicker sections={sections} value={filters.sections} onChange={(v) => set({ sections: v })} />
      <select
        className="fi-filter-select"
        aria-label="Vendor"
        value={filters.vendor}
        onChange={(e) => set({ vendor: e.target.value })}
      >
        <option value="">All vendors</option>
        {filters.vendor && !vendors.some((v) => v.name === filters.vendor) && (
          <option value={filters.vendor}>{filters.vendor} (0)</option>
        )}
        {vendors.map((v) => (
          <option key={v.name} value={v.name}>
            {v.name} ({v.count})
          </option>
        ))}
      </select>
      <select className="fi-filter-select" aria-label="Lead" value={filters.lead} onChange={(e) => set({ lead: e.target.value })}>
        <option value="">All leads</option>
        {filters.lead && filters.lead !== LEAD_NONE && !leads.some((l) => l.id === filters.lead) && (
          <option value={filters.lead}>That lead (0)</option>
        )}
        {leads.map((l) => (
          <option key={l.id} value={l.id}>
            {l.name} ({l.count})
          </option>
        ))}
        <option value={LEAD_NONE}>No lead ({none})</option>
      </select>
      <select className="fi-filter-select" aria-label="Task" value={filters.task} onChange={(e) => set({ task: e.target.value })}>
        <option value={TASK_ANY}>Task or not</option>
        <option value={TASK_ASSIGNED}>In a lead's task</option>
        <option value={TASK_NOT_ASSIGNED}>Not in a task yet</option>
      </select>
      {active > 0 && shownCount != null && (
        <span className="muted fi-filter-count">
          {shownCount} of {rows.length} shown
        </span>
      )}
      {active > 0 && (
        <button
          type="button"
          className="link-button fi-filter-clear"
          onClick={() => onChange({ sections: [], vendor: "", lead: "", task: TASK_ANY })}
        >
          Clear filters ({active})
        </button>
      )}
    </div>
  );
}

function SectionPicker({ sections, value, onChange }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const listId = useId();

  useEffect(() => {
    if (!open) return undefined;
    function onDoc(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    }
    function onKey(e) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const picked = value || [];
  const label = picked.length === 0 ? "All sections" : picked.length === 1 ? picked[0] : `${picked.length} sections`;
  const toggle = (name) => onChange(picked.includes(name) ? picked.filter((s) => s !== name) : [...picked, name]);
  // A picked section with no rows left on this tab still shows (so it can be unticked).
  const all = [...sections, ...picked.filter((p) => !sections.some((s) => s.name === p)).map((name) => ({ name, count: 0 }))];

  return (
    <div className="fi-section-picker" ref={ref}>
      <button
        type="button"
        className={`fi-filter-button${picked.length ? " active" : ""}`}
        aria-expanded={open}
        aria-controls={listId}
        onClick={() => setOpen((v) => !v)}
      >
        {label} ▾
      </button>
      {open && (
        <div className="fi-section-menu" id={listId} role="group" aria-label="PO sections">
          <div className="fi-section-menu-head">
            <span className="muted">PO sections</span>
            {picked.length > 0 && (
              <button type="button" className="link-button" onClick={() => onChange([])}>
                Clear
              </button>
            )}
          </div>
          {all.length === 0 && <p className="muted">Nothing on this list.</p>}
          {all.map((s) => (
            <label key={s.name} className="fi-section-option">
              <input type="checkbox" checked={picked.includes(s.name)} onChange={() => toggle(s.name)} />
              <span>{s.name}</span>
              <span className="muted">{s.count}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
