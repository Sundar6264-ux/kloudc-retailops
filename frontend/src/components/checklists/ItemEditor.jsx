import { useRef, useState } from "react";
import { uploadTeamMedia } from "../../api";
import ChecklistImage from "./ChecklistImage.jsx";
import {
  BRANCH_TYPES,
  CHOICE_TYPES,
  DISPLAY_TYPES,
  GRID_TYPES,
  ITEM_TYPES,
  OTHER_TYPES,
  SUBMIT_TARGET,
  TYPE_LABELS,
  isLegacyCheckboxes,
  newId,
} from "./model.js";
import { SUMMARY_TYPES } from "./editorModel.js";
import Icon from "../../ui/icons.jsx";

// One question card in the builder, Google Forms style: click to edit; the
// selected card shows the full editor, the others a compact summary.

const VALIDATION_RULES = {
  number: [
    ["gt", "Greater than"],
    ["gte", "Greater than or equal to"],
    ["lt", "Less than"],
    ["lte", "Less than or equal to"],
    ["eq", "Equal to"],
    ["neq", "Not equal to"],
    ["between", "Between"],
    ["not_between", "Not between"],
    ["is_number", "Is number"],
    ["whole_number", "Whole number"],
  ],
  text: [
    ["contains", "Contains"],
    ["not_contains", "Doesn't contain"],
    ["email", "Email address"],
    ["url", "Web address (URL)"],
  ],
  length: [
    ["max_chars", "Maximum character count"],
    ["min_chars", "Minimum character count"],
  ],
  regex: [
    ["contains", "Contains"],
    ["not_contains", "Doesn't contain"],
    ["matches", "Matches"],
    ["not_matches", "Doesn't match"],
  ],
  checkbox_count: [
    ["at_least", "Select at least"],
    ["at_most", "Select at most"],
    ["exactly", "Select exactly"],
  ],
};
const NO_VALUE_RULES = new Set(["is_number", "whole_number", "email", "url"]);
const KINDS_BY_TYPE = {
  short_text: [
    ["number", "Number"],
    ["text", "Text"],
    ["length", "Length"],
    ["regex", "Regular expression"],
  ],
  paragraph: [
    ["length", "Length"],
    ["regex", "Regular expression"],
  ],
};

function typeIcon(type) {
  return (
    {
      short_text: "✎",
      paragraph: "≡",
      multiple_choice: "◉",
      checkboxes: "☑",
      dropdown: "▾",
      linear_scale: "⋯",
      rating: "★",
      multiple_choice_grid: "▦",
      checkbox_grid: "▦",
      date: "📅",
      time: "🕒",
      file_upload: "⇪",
      title_text: "T",
      image: "🖼",
    }[type] || "?"
  );
}

function Toggle({ checked, onChange, label, id }) {
  return (
    <label className="clb-toggle" htmlFor={id}>
      <span>{label}</span>
      <input id={id} type="checkbox" role="switch" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="clb-toggle-track" aria-hidden="true" />
    </label>
  );
}

function IconButton({ label, onClick, disabled, children, danger }) {
  return (
    <button
      type="button"
      className={`clb-icon-btn${danger ? " clb-icon-danger" : ""}`}
      title={label}
      aria-label={label}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </button>
  );
}

function move(list, from, to) {
  if (to < 0 || to >= list.length) return list;
  const next = [...list];
  const [x] = next.splice(from, 1);
  next.splice(to, 0, x);
  return next;
}

function BranchSelect({ value, onChange, sections, sectionIndex, label }) {
  return (
    <select className="clb-branch-select" aria-label={label} value={value || ""} onChange={(e) => onChange(e.target.value || null)}>
      <option value="">Continue to next section</option>
      {sections.map((s, i) =>
        i <= sectionIndex ? null : (
          <option key={s.id} value={s.id}>
            Go to section {i + 1}
            {s.title ? ` (${s.title})` : ""}
          </option>
        )
      )}
      <option value={SUBMIT_TARGET}>Submit form</option>
    </select>
  );
}

function OptionsEditor({ item, onChange, issueTags, sections, sectionIndex, isGridColumns, takenIds }) {
  const options = item.options || [];
  const branching = BRANCH_TYPES.has(item.type) && item.go_to_section != null;
  const marker = item.type === "checkboxes" || item.type === "checkbox_grid" ? "☐" : item.type === "dropdown" ? null : "○";
  const lastRef = useRef(null);

  function setOptions(next, extra = {}) {
    onChange({ ...item, options: next, ...extra });
  }

  function rename(idx, value) {
    const old = options[idx].value;
    const next = options.map((o, i) => (i === idx ? { ...o, value } : o));
    let go = item.go_to_section;
    if (go && old in go) {
      go = { ...go };
      go[value] = go[old];
      delete go[old];
    }
    setOptions(next, go !== item.go_to_section ? { go_to_section: go } : {});
  }

  function add() {
    const used = new Set(options.map((o) => o.value));
    let n = options.length + 1;
    const base = isGridColumns ? "Column" : "Option";
    while (used.has(`${base} ${n}`)) n += 1;
    const opt = { value: `${base} ${n}`, issue_tag: null };
    // A converted checkbox group answers per box id, so a new box needs one.
    if (isLegacyCheckboxes(item)) opt.id = newId("o", takenIds());
    setOptions([...options, opt]);
    requestAnimationFrame(() => lastRef.current?.select());
  }

  function remove(idx) {
    const old = options[idx].value;
    let go = item.go_to_section;
    if (go && old in go) {
      go = { ...go };
      delete go[old];
    }
    setOptions(
      options.filter((_, i) => i !== idx),
      go !== item.go_to_section ? { go_to_section: go } : {}
    );
  }

  return (
    <div className="clb-options">
      {options.map((opt, idx) => (
        <div key={idx} className="clb-option-row">
          <span className="clb-option-marker" aria-hidden="true">
            {marker || `${idx + 1}.`}
          </span>
          <input
            ref={idx === options.length - 1 ? lastRef : undefined}
            type="text"
            className="clb-option-input"
            aria-label={`${isGridColumns ? "Column" : "Option"} ${idx + 1}`}
            value={opt.value}
            onChange={(e) => rename(idx, e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                add();
              }
            }}
          />
          <select
            className="clb-issue-select"
            aria-label="Issue flag"
            title="Choosing this answer flags the submission as an issue"
            value={opt.issue_tag || ""}
            onChange={(e) => setOptions(options.map((o, i) => (i === idx ? { ...o, issue_tag: e.target.value || null } : o)))}
          >
            <option value="">No issue</option>
            {Object.entries(issueTags).map(([tag, label]) => (
              <option key={tag} value={tag}>
                ⚠ {label}
              </option>
            ))}
          </select>
          {branching && (
            <BranchSelect
              label={`After "${opt.value}", go to`}
              value={item.go_to_section[opt.value]}
              sections={sections}
              sectionIndex={sectionIndex}
              onChange={(target) => {
                const go = { ...item.go_to_section };
                if (target) go[opt.value] = target;
                else delete go[opt.value];
                onChange({ ...item, go_to_section: go });
              }}
            />
          )}
          <span className="clb-option-actions">
            <IconButton label="Move option up" disabled={idx === 0} onClick={() => setOptions(move(options, idx, idx - 1))}>
              ↑
            </IconButton>
            <IconButton
              label="Move option down"
              disabled={idx === options.length - 1}
              onClick={() => setOptions(move(options, idx, idx + 1))}
            >
              ↓
            </IconButton>
            <IconButton label="Remove option" danger disabled={options.length <= 1} onClick={() => remove(idx)}>
              ×
            </IconButton>
          </span>
        </div>
      ))}
      {OTHER_TYPES.has(item.type) && item.other_option && (
        <div className="clb-option-row">
          <span className="clb-option-marker" aria-hidden="true">
            {marker}
          </span>
          <span className="clb-other-preview">Other...</span>
          <span className="clb-option-actions">
            <IconButton label='Remove "Other"' danger onClick={() => onChange({ ...item, other_option: false })}>
              ×
            </IconButton>
          </span>
        </div>
      )}
      <div className="clb-add-option">
        <button type="button" className="link-button" onClick={add}>
          Add {isGridColumns ? "column" : "option"}
        </button>
        {OTHER_TYPES.has(item.type) && !item.other_option && (
          <>
            <span className="muted"> or </span>
            <button type="button" className="link-button" onClick={() => onChange({ ...item, other_option: true })}>
              add "Other"
            </button>
          </>
        )}
      </div>
    </div>
  );
}

function RowsEditor({ item, onChange, taken }) {
  const rows = item.rows || [];
  const set = (next) => onChange({ ...item, rows: next });
  return (
    <div className="clb-options">
      {rows.map((row, idx) => (
        <div key={row.id} className="clb-option-row">
          <span className="clb-option-marker" aria-hidden="true">
            {idx + 1}.
          </span>
          <input
            type="text"
            className="clb-option-input"
            aria-label={`Row ${idx + 1}`}
            value={row.title}
            onChange={(e) => set(rows.map((r, i) => (i === idx ? { ...r, title: e.target.value } : r)))}
          />
          <span className="clb-option-actions">
            <IconButton label="Move row up" disabled={idx === 0} onClick={() => set(move(rows, idx, idx - 1))}>
              ↑
            </IconButton>
            <IconButton label="Move row down" disabled={idx === rows.length - 1} onClick={() => set(move(rows, idx, idx + 1))}>
              ↓
            </IconButton>
            <IconButton label="Remove row" danger disabled={rows.length <= 1} onClick={() => set(rows.filter((_, i) => i !== idx))}>
              ×
            </IconButton>
          </span>
        </div>
      ))}
      <div className="clb-add-option">
        <button
          type="button"
          className="link-button"
          onClick={() => set([...rows, { id: newId("r", taken()), title: `Row ${rows.length + 1}` }])}
        >
          Add row
        </button>
      </div>
    </div>
  );
}

function ScaleEditor({ item, onChange }) {
  const sc = item.scale || {};
  const setScale = (patch) => onChange({ ...item, scale: { ...sc, ...patch } });
  if (item.type === "rating") {
    return (
      <div className="clb-inline-fields">
        <label className="clb-field">
          Stars
          <select value={sc.max ?? 5} onChange={(e) => setScale({ min: 1, max: Number(e.target.value) })}>
            {[3, 4, 5, 6, 7, 8, 9, 10].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
        <span className="clb-stars-preview" aria-hidden="true">
          {"★".repeat(sc.max ?? 5)}
        </span>
      </div>
    );
  }
  return (
    <div className="clb-scale-editor">
      <div className="clb-inline-fields">
        <label className="clb-field">
          From
          <select value={sc.min ?? 1} onChange={(e) => setScale({ min: Number(e.target.value) })}>
            {[0, 1].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
        <label className="clb-field">
          to
          <select value={sc.max ?? 5} onChange={(e) => setScale({ max: Number(e.target.value) })}>
            {[2, 3, 4, 5, 6, 7, 8, 9, 10].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="clb-inline-fields">
        <label className="clb-field clb-grow">
          Label for {sc.min ?? 1} (optional)
          <input type="text" value={sc.min_label || ""} onChange={(e) => setScale({ min_label: e.target.value })} />
        </label>
        <label className="clb-field clb-grow">
          Label for {sc.max ?? 5} (optional)
          <input type="text" value={sc.max_label || ""} onChange={(e) => setScale({ max_label: e.target.value })} />
        </label>
      </div>
    </div>
  );
}

function ValidationEditor({ item, onChange }) {
  const isBoxes = item.type === "checkboxes";
  const v = item.validation || (isBoxes ? { kind: "checkbox_count", rule: "at_least", value: 1 } : { kind: "number", rule: "gt", value: "" });
  const set = (patch) => onChange({ ...item, validation: { ...v, ...patch } });
  const kinds = isBoxes ? [["checkbox_count", "Number of ticks"]] : KINDS_BY_TYPE[item.type] || [];
  const rules = VALIDATION_RULES[v.kind] || [];
  const between = v.rule === "between" || v.rule === "not_between";
  const val = Array.isArray(v.value) ? v.value : [v.value ?? "", ""];
  return (
    <div className="clb-validation">
      <div className="clb-inline-fields">
        {!isBoxes && (
          <select
            aria-label="Validation type"
            value={v.kind}
            onChange={(e) => set({ kind: e.target.value, rule: VALIDATION_RULES[e.target.value][0][0], value: "" })}
          >
            {kinds.map(([k, l]) => (
              <option key={k} value={k}>
                {l}
              </option>
            ))}
          </select>
        )}
        <select aria-label="Validation rule" value={v.rule} onChange={(e) => set({ rule: e.target.value, value: between ? "" : v.value })}>
          {rules.map(([k, l]) => (
            <option key={k} value={k}>
              {l}
            </option>
          ))}
        </select>
        {!NO_VALUE_RULES.has(v.rule) &&
          (between ? (
            <>
              <input
                type="number"
                aria-label="From"
                className="clb-num-input"
                value={val[0]}
                onChange={(e) => set({ value: [e.target.value === "" ? "" : Number(e.target.value), val[1]] })}
              />
              <span className="muted">and</span>
              <input
                type="number"
                aria-label="To"
                className="clb-num-input"
                value={val[1]}
                onChange={(e) => set({ value: [val[0], e.target.value === "" ? "" : Number(e.target.value)] })}
              />
            </>
          ) : (
            <input
              type={["number", "length", "checkbox_count"].includes(v.kind) ? "number" : "text"}
              aria-label={v.kind === "regex" ? "Pattern" : "Value"}
              className={["number", "length", "checkbox_count"].includes(v.kind) ? "clb-num-input" : "clb-grow-input"}
              placeholder={v.kind === "regex" ? "Pattern" : v.kind === "text" ? "Text" : "Number"}
              value={Array.isArray(v.value) ? v.value[0] : v.value ?? ""}
              onChange={(e) =>
                set({
                  value:
                    ["number", "length", "checkbox_count"].includes(v.kind) && e.target.value !== ""
                      ? v.kind === "number"
                        ? Number(e.target.value)
                        : Math.max(0, Math.round(Number(e.target.value)))
                      : e.target.value,
                })
              }
            />
          ))}
      </div>
      <input
        type="text"
        className="clb-grow-input"
        aria-label="Custom error text"
        placeholder="Custom error text (optional)"
        value={v.message || ""}
        onChange={(e) => set({ message: e.target.value })}
      />
    </div>
  );
}

function ImageEditor({ item, onChange }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  async function upload(file) {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const media = await uploadTeamMedia(file);
      onChange({ ...item, image_url: media.url });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="clb-image-editor">
      {item.image_url && <ChecklistImage url={item.image_url} alt={item.title || "Checklist image"} className="clb-image-preview" />}
      <div className="clb-inline-fields">
        <input type="file" accept="image/*" aria-label="Upload image" disabled={busy} onChange={(e) => upload(e.target.files?.[0])} />
        {busy && <span className="muted">Uploading…</span>}
      </div>
      <label className="clb-field">
        Or image address
        <input
          type="url"
          placeholder="https://..."
          value={item.image_url || ""}
          onChange={(e) => onChange({ ...item, image_url: e.target.value })}
        />
      </label>
      {error && <p className="error">{error}</p>}
    </div>
  );
}

function Summary({ item }) {
  if (item.type === "title_text") {
    return item.description ? <p className="muted clb-summary-text">{item.description}</p> : null;
  }
  if (item.type === "image") {
    return item.image_url ? (
      <ChecklistImage url={item.image_url} alt={item.title || "Checklist image"} className="clb-image-thumb" />
    ) : (
      <p className="muted clb-summary-text">No image yet</p>
    );
  }
  if (CHOICE_TYPES.has(item.type)) {
    const opts = (item.options || []).map((o) => (o.issue_tag ? `⚠ ${o.value}` : o.value));
    if (item.other_option) opts.push("Other");
    return <p className="muted clb-summary-text">{opts.join(" · ")}</p>;
  }
  if (GRID_TYPES.has(item.type)) {
    return (
      <p className="muted clb-summary-text">
        {(item.rows || []).length} rows × {(item.options || []).map((o) => o.value).join(" / ")}
      </p>
    );
  }
  if (item.type === "linear_scale") {
    const sc = item.scale || {};
    return (
      <p className="muted clb-summary-text">
        {sc.min ?? 1} to {sc.max ?? 5}
        {sc.min_label || sc.max_label ? ` (${sc.min_label || ""} ... ${sc.max_label || ""})` : ""}
      </p>
    );
  }
  if (item.type === "rating") return <p className="muted clb-summary-text">{"★".repeat(item.scale?.max ?? 5)}</p>;
  if (item.type === "file_upload") {
    const n = item.file?.max_files || 1;
    return <p className="muted clb-summary-text">Up to {n} {n === 1 ? "photo" : "photos"}</p>;
  }
  return null;
}

export default function ItemEditor({
  item,
  index,
  count,
  sections,
  sectionIndex,
  issueTags,
  selected,
  hasError,
  onSelect,
  onChange,
  onChangeType,
  onDelete,
  onDuplicate,
  onMove,
  onMoveToSection,
  takenIds,
  dragProps,
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const display = DISPLAY_TYPES.has(item.type);
  const showDescription = item.description != null;
  const cardCls = [
    "clb-item",
    selected && "clb-selected",
    hasError && "clb-error-card",
  ]
    .filter(Boolean)
    .join(" ");

  if (!selected) {
    return (
      <div
        className={cardCls}
        data-item-id={item.id}
        role="button"
        tabIndex={0}
        onClick={() => onSelect(item.id)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && e.target === e.currentTarget) onSelect(item.id);
        }}
        {...dragProps}
      >
        <div className="clb-drag-handle" aria-hidden="true" title="Drag to move">
          ⋮⋮
        </div>
        <div className="clb-item-summary">
          <div className="clb-item-summary-title">
            {item.title || <span className="muted">{display ? TYPE_LABELS[item.type] : "Untitled question"}</span>}
            {item.required && <span className="clf-required"> *</span>}
          </div>
          <span className="clb-type-chip">
            <span aria-hidden="true">{typeIcon(item.type)}</span> {TYPE_LABELS[item.type] || item.type}
          </span>
          {item.go_to_section && <span className="clb-type-chip clb-chip-branch">Branches</span>}
        </div>
        <Summary item={item} />
      </div>
    );
  }

  const menuItems = [];
  menuItems.push({
    key: "desc",
    label: "Description",
    on: showDescription,
    toggle: () => onChange({ ...item, description: showDescription ? null : "" }),
  });
  if (["short_text", "paragraph", "checkboxes"].includes(item.type)) {
    menuItems.push({
      key: "validation",
      label: "Response validation",
      on: Boolean(item.validation),
      toggle: () =>
        onChange({
          ...item,
          validation: item.validation
            ? null
            : item.type === "checkboxes"
              ? { kind: "checkbox_count", rule: "at_least", value: 1, message: "" }
              : item.type === "paragraph"
                ? // a paragraph only allows length/regex rules - a number rule
                  // made publish fail ("validation kind 'number' isn't available")
                  { kind: "length", rule: "max_chars", value: "", message: "" }
                : { kind: "number", rule: "gt", value: 0, message: "" },
        }),
    });
  }
  if (BRANCH_TYPES.has(item.type) && sectionIndex < sections.length - 1) {
    menuItems.push({
      key: "branch",
      label: "Go to section based on answer",
      on: item.go_to_section != null,
      toggle: () => onChange({ ...item, go_to_section: item.go_to_section != null ? null : {} }),
    });
  }
  if (CHOICE_TYPES.has(item.type) || GRID_TYPES.has(item.type)) {
    menuItems.push({ key: "shuffle", label: CHOICE_TYPES.has(item.type) ? "Shuffle option order" : "Shuffle row order", on: Boolean(item.shuffle), toggle: () => onChange({ ...item, shuffle: !item.shuffle }) });
  }
  if (["short_text", "paragraph"].includes(item.type)) {
    menuItems.push({
      key: "flag",
      label: "Flag as an issue if answered",
      on: Boolean(item.flag_if_answered),
      toggle: () =>
        onChange(
          item.flag_if_answered
            ? { ...item, flag_if_answered: false, flag_tag: null, nothing_to_report_ok: false }
            : { ...item, flag_if_answered: true, flag_tag: "issue", nothing_to_report_ok: true }
        ),
    });
  }
  if (item.type === "checkboxes") {
    menuItems.push({
      key: "unticked",
      label: "Flag boxes left unticked",
      on: Boolean(item.unticked_tag),
      toggle: () => onChange({ ...item, unticked_tag: item.unticked_tag ? null : "not_done" }),
    });
  }
  if (GRID_TYPES.has(item.type)) {
    menuItems.push({
      key: "one_per_column",
      label: "Limit to one response per column",
      on: Boolean(item.one_per_column),
      toggle: () => onChange({ ...item, one_per_column: !item.one_per_column }),
    });
  }
  if (SUMMARY_TYPES.has(item.type)) {
    menuItems.push({
      key: "summary",
      label: "Show answer next to \"Submitted by\"",
      on: item.summary_label != null,
      toggle: () => onChange({ ...item, summary_label: item.summary_label != null ? null : item.title || "" }),
    });
  }

  return (
    <div className={cardCls} data-item-id={item.id} {...dragProps}>
      <div className="clb-drag-handle" aria-hidden="true" title="Drag to move">
        ⋮⋮
      </div>
      <div className="clb-item-head">
        <textarea
          className="clb-title-input"
          rows={1}
          aria-label={display ? "Title" : "Question"}
          placeholder={display ? (item.type === "image" ? "Image title (optional)" : "Title") : "Question"}
          value={item.title || ""}
          onChange={(e) => onChange({ ...item, title: e.target.value })}
          autoFocus={!item.title}
        />
        <select
          className="clb-type-select"
          aria-label="Question type"
          value={item.type}
          onChange={(e) => onChangeType(e.target.value)}
        >
          {ITEM_TYPES.filter((t) => t.group !== "display" || t.type === item.type || display).map((t) => (
            <option key={t.type} value={t.type}>
              {typeIcon(t.type)}  {t.label}
            </option>
          ))}
        </select>
      </div>
      {(showDescription || item.type === "title_text") && (
        <textarea
          className="clb-desc-input"
          rows={2}
          aria-label="Description"
          placeholder="Description"
          value={item.description || ""}
          onChange={(e) => onChange({ ...item, description: e.target.value })}
        />
      )}

      <div className="clb-item-body">
        {CHOICE_TYPES.has(item.type) && (
          <OptionsEditor
            item={item}
            onChange={onChange}
            issueTags={issueTags}
            sections={sections}
            sectionIndex={sectionIndex}
            takenIds={takenIds}
          />
        )}
        {GRID_TYPES.has(item.type) && (
          <div className="clb-grid-editor">
            <div>
              <h5 className="clb-sub-head">Rows</h5>
              <RowsEditor item={item} onChange={onChange} taken={takenIds} />
            </div>
            <div>
              <h5 className="clb-sub-head">Columns</h5>
              <OptionsEditor
                item={item}
                onChange={onChange}
                issueTags={issueTags}
                sections={sections}
                sectionIndex={sectionIndex}
                takenIds={takenIds}
                isGridColumns
              />
            </div>
          </div>
        )}
        {(item.type === "linear_scale" || item.type === "rating") && <ScaleEditor item={item} onChange={onChange} />}
        {item.type === "short_text" && <div className="clb-fake-input">Short answer text</div>}
        {item.type === "paragraph" && <div className="clb-fake-input clb-fake-para">Long answer text</div>}
        {item.type === "date" && <div className="clb-fake-input">Month, day, year</div>}
        {item.type === "time" && <div className="clb-fake-input">Time</div>}
        {item.type === "file_upload" && (
          <div className="clb-inline-fields">
            <label className="clb-field">
              Maximum number of photos
              <select
                value={item.file?.max_files || 1}
                onChange={(e) => onChange({ ...item, file: { types: ["image"], ...(item.file || {}), max_files: Number(e.target.value) } })}
              >
                {[1, 2, 3, 5, 10].map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
            <span className="muted clb-hint">Photos only (JPEG, PNG, HEIC). Staff can take them with the phone camera.</span>
          </div>
        )}
        {item.type === "image" && <ImageEditor item={item} onChange={onChange} />}
        {item.validation && ["short_text", "paragraph", "checkboxes"].includes(item.type) && (
          <ValidationEditor item={item} onChange={onChange} />
        )}
        {item.flag_if_answered && (
          <div className="clb-extra">
            <label className="clb-field">
              Any answer flags the submission as
              <select value={item.flag_tag || "issue"} onChange={(e) => onChange({ ...item, flag_tag: e.target.value })}>
                {Object.entries(issueTags).map(([tag, label]) => (
                  <option key={tag} value={tag}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label className="delivery-checkbox clb-check">
              <input
                type="checkbox"
                checked={Boolean(item.nothing_to_report_ok)}
                onChange={(e) => onChange({ ...item, nothing_to_report_ok: e.target.checked })}
              />
              "None", "No", "All good" and similar don't count as an issue
            </label>
          </div>
        )}
        {item.type === "checkboxes" && item.unticked_tag && (
          <div className="clb-extra">
            <label className="clb-field">
              Each box left unticked is flagged as
              <select value={item.unticked_tag} onChange={(e) => onChange({ ...item, unticked_tag: e.target.value })}>
                {Object.entries(issueTags).map(([tag, label]) => (
                  <option key={tag} value={tag}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          </div>
        )}
        {item.summary_label != null && (
          <div className="clb-extra">
            <label className="clb-field">
              Label next to "Submitted by" (history, WhatsApp)
              <input
                type="text"
                maxLength={40}
                placeholder="e.g. Chef"
                value={item.summary_label}
                onChange={(e) => onChange({ ...item, summary_label: e.target.value })}
              />
            </label>
          </div>
        )}
        {item.one_per_column && <p className="muted clb-hint">Each column can be picked for only one row.</p>}
        {item.legacy_role && (
          <p className="muted clb-hint">
            {item.legacy_role === "confirmation"
              ? "Final confirmation box from the original checklist (the app shows it as a tick box)."
              : "The original \"anything unusual?\" box (shown at the end of the form)."}
          </p>
        )}
      </div>

      <div className="clb-item-footer">
        <div className="clb-footer-left">
          <IconButton label="Move up" disabled={index === 0 && sectionIndex === 0} onClick={() => onMove(-1)}>
            ↑
          </IconButton>
          <IconButton
            label="Move down"
            disabled={index === count - 1 && sectionIndex === sections.length - 1}
            onClick={() => onMove(1)}
          >
            ↓
          </IconButton>
        </div>
        <div className="clb-footer-right">
          <IconButton label="Duplicate" onClick={onDuplicate}>
            <Icon name="copy" size={16} />
          </IconButton>
          <IconButton label="Delete" danger onClick={onDelete}>
            <Icon name="trash-2" size={16} />
          </IconButton>
          {!display && (
            <>
              <span className="clb-footer-sep" aria-hidden="true" />
              <Toggle id={`req-${item.id}`} label="Required" checked={Boolean(item.required)} onChange={(v) => onChange({ ...item, required: v })} />
            </>
          )}
          <div className="clb-menu-wrap">
            <IconButton label="More options" onClick={() => setMenuOpen((o) => !o)}>
              ⋮
            </IconButton>
            {menuOpen && (
              <>
                <div className="clb-menu-backdrop" onClick={() => setMenuOpen(false)} />
                <div className="clb-menu" role="menu">
                  <div className="clb-menu-label">Show</div>
                  {menuItems.map((m) => (
                    <button
                      key={m.key}
                      type="button"
                      role="menuitemcheckbox"
                      aria-checked={m.on}
                      className="clb-menu-item"
                      onClick={() => {
                        m.toggle();
                        setMenuOpen(false);
                      }}
                    >
                      <span className="clb-menu-check">{m.on ? "✓" : ""}</span>
                      {m.label}
                    </button>
                  ))}
                  {sections.length > 1 && (
                    <>
                      <div className="clb-menu-label">Move to section</div>
                      {sections.map((s, i) =>
                        i === sectionIndex ? null : (
                          <button
                            key={s.id}
                            type="button"
                            role="menuitem"
                            className="clb-menu-item"
                            onClick={() => {
                              onMoveToSection(s.id);
                              setMenuOpen(false);
                            }}
                          >
                            <span className="clb-menu-check" />
                            Section {i + 1}
                            {s.title ? `: ${s.title}` : ""}
                          </button>
                        )
                      )}
                    </>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
