import { useEffect, useId, useState } from "react";
import { setCartLineSection } from "../api";

// Shared bits for PO sections (the heading a product prints under on the
// vendor PO PDF): used by the PO sections page and the Purchase Order Cart.

export const OTHER = "Other";

// Plain code-point order of the lower-cased text - what the backend's
// Python sort does (localeCompare orders punctuation such as "&" / "-"
// differently, so the cart could disagree with the PDF).
function compareText(a, b) {
  const x = (a || "").trim().toLowerCase();
  const y = (b || "").trim().toLowerCase();
  return x < y ? -1 : x > y ? 1 : 0;
}

// Same order as the PDF: alphabetical (case-insensitive), Other last.
export function compareSections(a, b) {
  const ao = a === OTHER;
  const bo = b === OTHER;
  if (ao !== bo) return ao ? 1 : -1;
  return compareText(a, b);
}

// [[section, lines], ...] in PDF order; lines sorted by name like the PDF.
export function groupBySection(lines, sectionOf = (l) => l.section) {
  const groups = new Map();
  for (const line of lines) {
    const section = sectionOf(line) || OTHER;
    if (!groups.has(section)) groups.set(section, []);
    groups.get(section).push(line);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => compareSections(a, b))
    .map(([section, rows]) => [
      section,
      [...rows].sort((x, y) => compareText(x.name, y.name)),
    ]);
}

// A text box with the existing sections as suggestions: pick one or type a
// new name (a new section is made just by using it).
export function SectionInput({ value, onChange, sections, placeholder = "Pick or type a new section", autoFocus, ariaLabel }) {
  const listId = useId();
  return (
    <>
      <input
        className="ps-section-input"
        list={listId}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        autoFocus={autoFocus}
        aria-label={ariaLabel || "Section"}
        maxLength={80}
      />
      <datalist id={listId}>
        {(sections || []).map((s) => (
          <option key={s} value={s} />
        ))}
      </datalist>
    </>
  );
}

const SOURCE_TEXT = {
  item: "item rule",
  vendor: "vendor rule",
  automatic: "automatic",
  line: "set on this line",
  other: "no section",
};

// Inline "Section" control for one cart line: shows the section, and for
// editors a small edit box that saves immediately (catalog items get an
// item rule; hand-added lines keep it on the line).
export function CartLineSection({ line, sections, canEdit, onSaved, onError }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(line.section || "");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!editing) setValue(line.section === OTHER ? "" : line.section || "");
  }, [line.section, editing]);

  async function save(next) {
    setBusy(true);
    try {
      await setCartLineSection(line.id ?? line.cart_item_id, next.trim() || null);
      setEditing(false);
      onSaved?.();
    } catch (err) {
      onError?.(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (!canEdit) return null;
  if (!editing) {
    return (
      <button
        type="button"
        className="link-button ps-line-edit"
        onClick={() => setEditing(true)}
        title={line.section_source ? `Section: ${line.section} (${SOURCE_TEXT[line.section_source] || line.section_source})` : undefined}
      >
        Section
      </button>
    );
  }
  const clearLabel = line.item_id ? "Back to automatic" : "No section (Other)";
  return (
    <form
      className="ps-line-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (value.trim()) save(value);
      }}
    >
      <SectionInput value={value} onChange={setValue} sections={sections} autoFocus ariaLabel={`Section for ${line.name}`} />
      <button type="submit" disabled={busy || !value.trim()}>
        Save
      </button>
      <button type="button" className="link-button" disabled={busy} onClick={() => save("")}>
        {clearLabel}
      </button>
      <button type="button" className="link-button" disabled={busy} onClick={() => setEditing(false)}>
        Cancel
      </button>
    </form>
  );
}
