import { useContext, useRef, useState } from "react";
import { uploadChecklistPhoto } from "../../api";
import ChecklistImage from "./ChecklistImage.jsx";
import { ChecklistUploadContext } from "./uploadContext.js";
import {
  CHECKBOX_DONE,
  GRID_TYPES,
  OTHER_PREFIX,
  allAsUsualEnabled,
  encodeArray,
  isLegacyCheckboxes,
  isLegacyFormat,
  isOtherValue,
  parseArray,
  rowKey,
  scaleRange,
  sectionFills,
  shuffled,
} from "./model.js";

// Renders a checklist's sections (legacy blocks and contract items) as staff
// fill them. Stateless: the parent owns `answers` ({key: string}) and passes
// `setAnswer(key, value|null)` and `mergeAnswers(obj)`. Used by the staff form
// and by the builder's live preview, so both look exactly the same.

function Required({ on }) {
  return on ? <span className="clf-required" aria-label="required"> *</span> : null;
}

function ItemHead({ item }) {
  return (
    <>
      <div className="checklist-row-label">
        {item.title || <span className="muted">Untitled question</span>}
        <Required on={item.required} />
      </div>
      {item.description && <p className="muted clf-desc">{item.description}</p>}
    </>
  );
}

function FieldError({ message }) {
  return message ? (
    <p className="error clf-error" role="alert">
      {message}
    </p>
  ) : null;
}

function optionClass(selected, issue) {
  return ["checklist-option", selected && (issue ? "checklist-option-selected-issue" : "checklist-option-selected-clear")]
    .filter(Boolean)
    .join(" ");
}

// A row of selectable buttons, not a dropdown - several real answer choices
// are long phrases ("Running Low (Good for a day)") a narrow dropdown clips.
export function ChoiceRow({ label, options, selected, onChange, multi = false }) {
  const selectedSet = multi ? new Set(parseArray(selected) || []) : null;
  return (
    <div className="checklist-row">
      {label != null && <div className="checklist-row-label">{label}</div>}
      <div className="checklist-row-options">
        {options.map((opt) => {
          const isSelected = multi ? selectedSet.has(opt.value) : selected === opt.value;
          const isIssue = Boolean(opt.issue_tag);
          return (
            <button
              key={opt.value}
              type="button"
              className={optionClass(isSelected, isIssue)}
              aria-pressed={isSelected}
              onClick={() => {
                if (!multi) return onChange(opt.value);
                const next = new Set(selectedSet);
                if (isSelected) next.delete(opt.value);
                else next.add(opt.value);
                // keep the option order, not the click order
                return onChange(encodeArray(options.map((o) => o.value).filter((v) => next.has(v))));
              }}
            >
              {isIssue && <span aria-hidden="true">⚠️ </span>}
              {opt.value}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function useOrderedOptions(item, seed) {
  const options = item.options || [];
  return item.shuffle ? shuffled(options, seed + (item.id || "").length * 7919) : options;
}

function MultipleChoiceItem({ item, value, setAnswer, seed }) {
  const options = useOrderedOptions(item, seed);
  const otherSelected = isOtherValue(value) || value === "Other:";
  const otherText = otherSelected ? String(value).slice(OTHER_PREFIX.length) : "";
  return (
    <>
      <div className="checklist-row-options">
        {options.map((opt) => {
          const isSelected = value === opt.value;
          return (
            <button
              key={opt.value}
              type="button"
              className={optionClass(isSelected, Boolean(opt.issue_tag))}
              aria-pressed={isSelected}
              onClick={() => setAnswer(item.id, opt.value)}
            >
              {opt.issue_tag && <span aria-hidden="true">⚠️ </span>}
              {opt.value}
            </button>
          );
        })}
        {item.other_option && (
          <button
            type="button"
            className={optionClass(otherSelected, false)}
            aria-pressed={otherSelected}
            onClick={() => !otherSelected && setAnswer(item.id, OTHER_PREFIX)}
          >
            Other
          </button>
        )}
      </div>
      {item.other_option && otherSelected && (
        <input
          type="text"
          className="clf-other-input"
          aria-label="Other answer"
          placeholder="Type your answer"
          value={otherText}
          autoFocus
          onChange={(e) => setAnswer(item.id, OTHER_PREFIX + e.target.value)}
        />
      )}
    </>
  );
}

function CheckboxesItem({ item, value, setAnswer, seed }) {
  const options = useOrderedOptions(item, seed);
  const values = parseArray(value) || [];
  const other = values.find((v) => isOtherValue(v) || v === "Other:");
  const plain = new Set(values.filter((v) => v !== other));
  function write(nextPlain, nextOther) {
    const ordered = (item.options || []).map((o) => o.value).filter((v) => nextPlain.has(v));
    if (nextOther != null) ordered.push(nextOther);
    setAnswer(item.id, encodeArray(ordered));
  }
  return (
    <div className="clf-checkbox-list">
      {options.map((opt) => (
        <label key={opt.value} className="delivery-checkbox checklist-checkbox-item">
          <input
            type="checkbox"
            checked={plain.has(opt.value)}
            onChange={(e) => {
              const next = new Set(plain);
              if (e.target.checked) next.add(opt.value);
              else next.delete(opt.value);
              write(next, other);
            }}
          />
          <span>
            {opt.value}
            {opt.issue_tag && <span className="clf-issue-hint"> (flags an issue)</span>}
          </span>
        </label>
      ))}
      {item.other_option && (
        <div className="clf-other-row">
          <label className="delivery-checkbox checklist-checkbox-item">
            <input
              type="checkbox"
              checked={other != null}
              onChange={(e) => write(plain, e.target.checked ? OTHER_PREFIX : null)}
            />
            Other:
          </label>
          <input
            type="text"
            className="clf-other-input"
            aria-label="Other answer"
            value={other ? other.slice(OTHER_PREFIX.length) : ""}
            onChange={(e) => write(plain, e.target.value || other != null ? OTHER_PREFIX + e.target.value : null)}
          />
        </div>
      )}
    </div>
  );
}

function DropdownItem({ item, value, setAnswer, seed }) {
  const options = useOrderedOptions(item, seed);
  return (
    <select
      className="clf-select"
      aria-label={item.title || "Choose"}
      value={value ?? ""}
      onChange={(e) => setAnswer(item.id, e.target.value || null)}
    >
      <option value="">Choose...</option>
      {options.map((opt) => (
        <option key={opt.value} value={opt.value}>
          {opt.value}
        </option>
      ))}
    </select>
  );
}

function ScaleItem({ item, value, setAnswer }) {
  const nums = scaleRange(item);
  const sc = item.scale || {};
  if (item.type === "rating") {
    const current = Number(value) || 0;
    return (
      <div className="clf-rating" role="radiogroup" aria-label={item.title || "Rating"}>
        {nums.map((n) => (
          <button
            key={n}
            type="button"
            role="radio"
            aria-checked={current === n}
            aria-label={`${n} of ${nums.length}`}
            className={`clf-star${n <= current ? " clf-star-on" : ""}`}
            onClick={() => setAnswer(item.id, String(n))}
          >
            ★
          </button>
        ))}
        {current > 0 && <span className="muted clf-rating-value">{current} / {nums.length}</span>}
      </div>
    );
  }
  return (
    <div className="clf-scale">
      {sc.min_label && <span className="clf-scale-label">{sc.min_label}</span>}
      <div className="clf-scale-nums" role="radiogroup" aria-label={item.title || "Scale"}>
        {nums.map((n) => {
          const selected = value === String(n);
          return (
            <button
              key={n}
              type="button"
              role="radio"
              aria-checked={selected}
              className={optionClass(selected, false) + " clf-scale-num"}
              onClick={() => setAnswer(item.id, String(n))}
            >
              {n}
            </button>
          );
        })}
      </div>
      {sc.max_label && <span className="clf-scale-label">{sc.max_label}</span>}
    </div>
  );
}

// Photos go through the Team media upload (POST /api/team/media), which
// validates and re-encodes them. The answer is a JSON array of the uploads'
// ids (contract); `mediaCache` keeps each upload's url for the thumbnails.
function FileUploadItem({ item, value, setAnswer, mediaCache, preview }) {
  const ids = parseArray(value) || [];
  const max = Math.max(1, Number(item.file?.max_files) || 1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const inputRef = useRef(null);
  const upload = useContext(ChecklistUploadContext) || uploadChecklistPhoto;

  async function onFiles(files) {
    setError(null);
    const room = max - ids.length;
    const list = Array.from(files || []).slice(0, room);
    if (!list.length) return;
    if (preview) {
      setError("Preview only - photos aren't uploaded here.");
      return;
    }
    setBusy(true);
    const added = [];
    try {
      for (const f of list) {
        const media = await upload(f);
        mediaCache.current[String(media.id)] = media;
        added.push(String(media.id));
      }
    } catch (err) {
      setError(err.status === 403 ? "You don't have permission to upload photos. Ask an admin." : err.message);
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
      if (added.length) setAnswer(item.id, encodeArray([...ids, ...added]));
    }
  }

  return (
    <div className="clf-upload">
      {ids.length > 0 && (
        <div className="clf-thumbs">
          {ids.map((id) => (
            <div key={id} className="clf-thumb">
              <ChecklistImage media={mediaCache.current[id]} mediaId={id} alt="Uploaded photo" />
              <button
                type="button"
                className="link-button link-danger"
                onClick={() => setAnswer(item.id, encodeArray(ids.filter((x) => x !== id)))}
              >
                Remove
              </button>
            </div>
          ))}
        </div>
      )}
      {ids.length < max && (
        <input
          ref={inputRef}
          type="file"
          accept="image/*"
          multiple={max - ids.length > 1}
          disabled={busy}
          aria-label={item.title || "Upload photo"}
          onChange={(e) => onFiles(e.target.files)}
        />
      )}
      <p className="muted clf-hint">
        {busy ? "Uploading…" : `Photos only, up to ${max} ${max === 1 ? "photo" : "photos"}.`}
      </p>
      <FieldError message={error} />
    </div>
  );
}

function GridItem({ item, answers, setAnswer, seed }) {
  const multi = item.type === "checkbox_grid";
  const rows = item.shuffle ? shuffled(item.rows || [], seed + (item.id || "").length * 104729) : item.rows || [];
  return (
    <div className="clf-grid">
      {rows.map((row) => (
        <ChoiceRow
          key={row.id}
          label={row.title}
          options={item.options || []}
          multi={multi}
          selected={answers[rowKey(item, row)]}
          onChange={(v) => setAnswer(rowKey(item, row), v)}
        />
      ))}
    </div>
  );
}

// A checkbox group converted from the original checklists: one "Done"
// answer per ticked box, keyed by the box's id (what app build 5 sends).
function LegacyBoxes({ item, answers, setAnswer }) {
  return (
    <>
      {(item.options || []).map((opt) => (
        <label key={opt.id} className="delivery-checkbox checklist-checkbox-item">
          <input
            type="checkbox"
            checked={answers[opt.id] === CHECKBOX_DONE}
            onChange={(e) => setAnswer(opt.id, e.target.checked ? CHECKBOX_DONE : null)}
          />
          {opt.value}
        </label>
      ))}
    </>
  );
}

export function ItemField({ item, answers, setAnswer, error, seed, mediaCache, preview }) {
  const value = answers[item.id];
  // The original checklists' "anything unusual?" box and final confirmation,
  // drawn exactly as before.
  if (item.legacy_role === "issues_field") {
    return (
      <label
        className={`recon-form-field recon-form-field-wide${error ? " clf-has-error" : ""}`}
        style={{ marginTop: "1rem" }}
        data-item={item.id}
      >
        {item.title}
        {item.required ? (
          <textarea
            name="issues_text"
            rows={3}
            value={value ?? ""}
            onChange={(e) => setAnswer(item.id, e.target.value || null)}
            placeholder={item.hint || "Required"}
          />
        ) : (
          <input
            type="text"
            value={value ?? ""}
            onChange={(e) => setAnswer(item.id, e.target.value || null)}
            placeholder={item.hint || "Optional - describe anything not covered above"}
          />
        )}
        <FieldError message={error} />
      </label>
    );
  }
  if (item.legacy_role === "confirmation") {
    const label = item.options?.[0]?.value || item.title;
    return (
      <div className={error ? "clf-has-error" : undefined} data-item={item.id}>
        <label className="delivery-checkbox" style={{ marginTop: "1rem" }}>
          <input type="checkbox" checked={value != null} onChange={(e) => setAnswer(item.id, e.target.checked ? label : null)} />
          {item.title}
        </label>
        <FieldError message={error} />
      </div>
    );
  }
  if (item.type === "title_text") {
    return (
      <div className="clf-title-block" data-item={item.id}>
        {item.title && <h4 className="clf-title-text">{item.title}</h4>}
        {item.description && <p className="muted clf-desc">{item.description}</p>}
      </div>
    );
  }
  if (item.type === "image") {
    return (
      <figure className="clf-image-block" data-item={item.id}>
        {item.title && <figcaption className="checklist-row-label">{item.title}</figcaption>}
        {item.image_url ? (
          <ChecklistImage url={item.image_url} alt={item.title || "Checklist image"} className="clf-image" />
        ) : (
          <p className="muted">No image yet.</p>
        )}
        {item.description && <p className="muted clf-desc">{item.description}</p>}
      </figure>
    );
  }
  if (GRID_TYPES.has(item.type)) {
    return (
      <div className={`clf-item clf-grid-item${error ? " clf-has-error" : ""}`} data-item={item.id}>
        <h4 className="checklist-grid-title">
          {item.title}
          <Required on={item.required && !isLegacyFormat(item)} />
        </h4>
        {item.description && <p className="muted clf-desc">{item.description}</p>}
        <GridItem item={item} answers={answers} setAnswer={setAnswer} seed={seed} />
        <FieldError message={error} />
      </div>
    );
  }
  if (item.type === "checkboxes") {
    return (
      <fieldset className={`checklist-checkbox-group clf-item${error ? " clf-has-error" : ""}`} data-item={item.id}>
        <legend className="checklist-grid-title">
          {item.title}
          {isLegacyCheckboxes(item) ? (
            item.required && <span className="muted"> (tick every task you did)</span>
          ) : (
            <Required on={item.required} />
          )}
        </legend>
        {item.description && <p className="muted clf-desc">{item.description}</p>}
        {isLegacyCheckboxes(item) ? (
          <LegacyBoxes item={item} answers={answers} setAnswer={setAnswer} />
        ) : (
          <CheckboxesItem item={item} value={value} setAnswer={setAnswer} seed={seed} />
        )}
        <FieldError message={error} />
      </fieldset>
    );
  }
  let body;
  switch (item.type) {
    case "short_text":
      body = (
        <input
          type="text"
          className="clf-text"
          aria-label={item.title || "Answer"}
          value={value ?? ""}
          placeholder="Your answer"
          onChange={(e) => setAnswer(item.id, e.target.value || null)}
        />
      );
      break;
    case "paragraph":
      body = (
        <textarea
          className="clf-textarea"
          rows={3}
          aria-label={item.title || "Answer"}
          value={value ?? ""}
          placeholder="Your answer"
          onChange={(e) => setAnswer(item.id, e.target.value || null)}
        />
      );
      break;
    case "multiple_choice":
      body = <MultipleChoiceItem item={item} value={value} setAnswer={setAnswer} seed={seed} />;
      break;
    case "dropdown":
      body = <DropdownItem item={item} value={value} setAnswer={setAnswer} seed={seed} />;
      break;
    case "linear_scale":
    case "rating":
      body = <ScaleItem item={item} value={value} setAnswer={setAnswer} />;
      break;
    case "date":
      body = (
        <input
          type="date"
          className="clf-date"
          aria-label={item.title || "Date"}
          value={value ?? ""}
          onChange={(e) => setAnswer(item.id, e.target.value || null)}
        />
      );
      break;
    case "time":
      body = (
        <input
          type="time"
          className="clf-date"
          aria-label={item.title || "Time"}
          value={value ?? ""}
          onChange={(e) => setAnswer(item.id, e.target.value || null)}
        />
      );
      break;
    case "file_upload":
      body = (
        <FileUploadItem item={item} value={value} setAnswer={setAnswer} mediaCache={mediaCache} preview={preview} />
      );
      break;
    default:
      body = <p className="muted">This question type ({item.type}) isn't supported here yet.</p>;
  }
  const clearable = !item.required && value != null && ["multiple_choice", "linear_scale", "rating"].includes(item.type);
  return (
    <div className={`checklist-row clf-item${error ? " clf-has-error" : ""}`} data-item={item.id}>
      <ItemHead item={item} />
      {body}
      {clearable && (
        <button type="button" className="link-button clf-clear" onClick={() => setAnswer(item.id, null)}>
          Clear selection
        </button>
      )}
      <FieldError message={error} />
    </div>
  );
}

// A single-choice legacy question rendered as a dropdown (`input: "dropdown"`).
function LegacyDropdown({ question, selected, onChange }) {
  return (
    <label className="recon-form-field checklist-dropdown-question" data-question={question.id}>
      <span className="checklist-row-label">
        {question.label}
        {question.required && <span className="error"> *</span>}
      </span>
      <select name={question.id} value={selected ?? ""} onChange={(e) => onChange(e.target.value || null)}>
        <option value="">Choose...</option>
        {(question.options || []).map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.value}
          </option>
        ))}
      </select>
    </label>
  );
}

function AllAsUsual({ onClick }) {
  return (
    <button type="button" className="btn-secondary" onClick={onClick}>
      ✓ All as usual
    </button>
  );
}

export default function ChecklistFields({
  schema,
  visibleKeys,
  answers,
  setAnswer,
  mergeAnswers,
  errors,
  seed,
  mediaCache,
  preview = false,
  confirmFill = true,
}) {
  const visible = new Set(visibleKeys);
  const showAllAsUsual = allAsUsualEnabled(schema);

  function toggleBox(id, checked) {
    setAnswer(id, checked ? CHECKBOX_DONE : null);
  }

  function fillSectionAsUsual(section) {
    const fills = sectionFills(section, answers);
    const count = Object.keys(fills).length;
    if (count === 0) return;
    const examples = [...new Set(Object.values(fills))]
      .slice(0, 3)
      .map((v) => `"${v}"`)
      .join(", ");
    const ok =
      !confirmFill ||
      window.confirm(
        `Fill the normal answer (${examples}) for ${count} unanswered item(s) in ` +
          `"${section.title || "this section"}"? Items you've already answered aren't changed.`
      );
    if (ok) mergeAnswers(fills);
  }

  return (
    <>
      {schema.sections.map((section, sIdx) => {
        if (!visible.has(section.key)) return null;
        const unanswered = showAllAsUsual ? Object.keys(sectionFills(section, answers)).length : 0;
        return (
          <div key={section.key} className="checklist-section" data-section={section.key}>
            {section.title && (
              <>
                {sIdx > 0 || schema.sections.length > 1 ? <hr /> : null}
                <div className="checklist-section-header">
                  <h3>{section.title}</h3>
                  {unanswered > 0 && <AllAsUsual onClick={() => fillSectionAsUsual(section)} />}
                </div>
                {section.description && <p className="muted clf-section-desc">{section.description}</p>}
              </>
            )}
            {!section.title && unanswered > 0 && (
              <div className="checklist-section-header">
                <span />
                <AllAsUsual onClick={() => fillSectionAsUsual(section)} />
              </div>
            )}
            {!section.title && section.description && <p className="muted clf-section-desc">{section.description}</p>}
            {section.grids.map((grid, gIdx) => (
              <div key={gIdx}>
                {grid.title && <h4 className="checklist-grid-title">{grid.title}</h4>}
                {grid.rows.map((row) => (
                  <div key={row.id} className={errors[row.id] ? "clf-has-error" : undefined}>
                    <ChoiceRow
                      label={row.label}
                      options={grid.options}
                      selected={answers[row.id]}
                      onChange={(value) => setAnswer(row.id, value)}
                    />
                  </div>
                ))}
              </div>
            ))}
            {section.questions.map((q) =>
              q.input === "dropdown" ? (
                <LegacyDropdown key={q.id} question={q} selected={answers[q.id]} onChange={(v) => setAnswer(q.id, v)} />
              ) : (
                <div key={q.id} className={errors[q.id] ? "clf-has-error" : undefined}>
                  <ChoiceRow
                    label={q.label}
                    options={q.options || []}
                    selected={answers[q.id]}
                    onChange={(value) => setAnswer(q.id, value)}
                  />
                </div>
              )
            )}
            {section.checkboxGroups.map((group) => (
              <fieldset
                key={group.id}
                className={`checklist-checkbox-group${errors[group.id] ? " clf-has-error" : ""}`}
                data-group={group.id}
              >
                <legend className="checklist-grid-title">
                  {group.title}
                  {group.required && <span className="muted"> (tick every task you did)</span>}
                </legend>
                {group.items.map((it) => (
                  <label key={it.id} className="delivery-checkbox checklist-checkbox-item">
                    <input
                      type="checkbox"
                      checked={answers[it.id] === CHECKBOX_DONE}
                      onChange={(e) => toggleBox(it.id, e.target.checked)}
                    />
                    {it.label}
                  </label>
                ))}
                <FieldError message={errors[group.id]} />
              </fieldset>
            ))}
            {(section.items || []).map((item) => (
              <ItemField
                key={item.id}
                item={item}
                answers={answers}
                setAnswer={setAnswer}
                error={errors[item.id]}
                seed={seed}
                mediaCache={mediaCache}
                preview={preview}
              />
            ))}
          </div>
        );
      })}
      {schema.issues_field && (
        <label
          className={`recon-form-field recon-form-field-wide${errors[schema.issues_field.id] ? " clf-has-error" : ""}`}
          style={{ marginTop: "1rem" }}
        >
          {schema.issues_field.label}
          {schema.issues_field.required ? (
            // A required paragraph answer (the FSA closing's "anything
            // unusual?"): "None" is fine and isn't flagged.
            <textarea
              name="issues_text"
              rows={3}
              value={answers[schema.issues_field.id] ?? ""}
              onChange={(e) => setAnswer(schema.issues_field.id, e.target.value || null)}
              placeholder={schema.issues_field.hint || "Required"}
            />
          ) : (
            <input
              type="text"
              value={answers[schema.issues_field.id] ?? ""}
              onChange={(e) => setAnswer(schema.issues_field.id, e.target.value || null)}
              placeholder="Optional - describe anything not covered above"
            />
          )}
        </label>
      )}
      {schema.confirmation && (
        <label className="delivery-checkbox" style={{ marginTop: "1rem" }}>
          <input
            type="checkbox"
            checked={answers[schema.confirmation.id] != null}
            onChange={(e) => setAnswer(schema.confirmation.id, e.target.checked ? schema.confirmation.label : null)}
          />
          {schema.confirmation.label}
        </label>
      )}
    </>
  );
}
