import ChecklistImage from "./ChecklistImage.jsx";
import {
  CHECKBOX_DONE,
  DISPLAY_TYPES,
  GRID_TYPES,
  answerKeys,
  isLegacyCheckboxes,
  issueTagForValue,
  normalizeSchema,
  parseArray,
  rowKey,
} from "./model.js";

// Read-only answers of one submission, drawn with the schema version it was
// submitted against (falls back to the current one). Multi-value answers
// (JSON array strings) show as lists, grids per row, photos as thumbnails.
// Answer keys the schema doesn't know are listed at the end, so nothing a
// staff member sent is ever hidden.

function Answer({ value, options, media }) {
  if (value == null || value === "") return <span className="muted">No answer</span>;
  const arr = parseArray(value);
  if (arr) {
    if (!arr.length) return <span className="muted">No answer</span>;
    return (
      <ul className="cla-list">
        {arr.map((v, i) => (
          <li key={i} className={issueTagForValue(options, v) ? "cla-issue" : undefined}>
            {v}
          </li>
        ))}
      </ul>
    );
  }
  const issue = issueTagForValue(options, value);
  return <span className={issue ? "cla-issue" : undefined}>{value}</span>;
}

function PhotoAnswer({ value, media }) {
  const ids = parseArray(value) || [];
  if (!ids.length) return <span className="muted">No photos</span>;
  return (
    <div className="clf-thumbs">
      {ids.map((id) => (
        <div key={id} className="clf-thumb">
          <ChecklistImage media={media?.[id]} mediaId={id} alt="Uploaded photo" />
        </div>
      ))}
    </div>
  );
}

function Row({ label, children }) {
  return (
    <div className="cla-row">
      <div className="cla-q">{label}</div>
      <div className="cla-a">{children}</div>
    </div>
  );
}

function sectionKeys(section) {
  return (section.items || []).flatMap(answerKeys);
}

export default function ChecklistAnswers({ schema: rawSchema, answers, media }) {
  const answerMap = answers || {};
  const used = new Set();
  const take = (k) => {
    used.add(k);
    return answerMap[k];
  };
  const schema = rawSchema ? normalizeSchema(rawSchema) : null;

  const sections = schema
    ? schema.sections.map((section) => {
        const rows = [];
        for (const grid of section.grids) {
          if (grid.title) rows.push(<h4 key={`gt-${grid.id || grid.title}`} className="cla-sub">{grid.title}</h4>);
          for (const row of grid.rows) {
            rows.push(
              <Row key={row.id} label={row.label}>
                <Answer value={take(row.id)} options={grid.options} />
              </Row>
            );
          }
        }
        for (const q of section.questions) {
          rows.push(
            <Row key={q.id} label={q.label}>
              <Answer value={take(q.id)} options={q.options} />
            </Row>
          );
        }
        for (const g of section.checkboxGroups) {
          const ticked = g.items.filter((it) => take(it.id) === CHECKBOX_DONE);
          const unticked = g.items.filter((it) => answerMap[it.id] !== CHECKBOX_DONE);
          rows.push(
            <Row key={g.id} label={g.title}>
              {ticked.length > 0 && (
                <ul className="cla-list">
                  {ticked.map((it) => (
                    <li key={it.id}>{it.label}</li>
                  ))}
                </ul>
              )}
              {unticked.length > 0 && (
                <>
                  <div className="muted cla-note">Not ticked:</div>
                  <ul className="cla-list">
                    {unticked.map((it) => (
                      <li key={it.id} className={g.unticked_tag ? "cla-issue" : undefined}>
                        {it.label}
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </Row>
          );
        }
        for (const item of section.items || []) {
          if (DISPLAY_TYPES.has(item.type)) continue;
          if (GRID_TYPES.has(item.type)) {
            rows.push(<h4 key={`t-${item.id}`} className="cla-sub">{item.title}</h4>);
            for (const row of item.rows || []) {
              rows.push(
                <Row key={rowKey(item, row)} label={row.title}>
                  <Answer value={take(rowKey(item, row))} options={item.options} />
                </Row>
              );
            }
          } else if (isLegacyCheckboxes(item)) {
            const opts = item.options || [];
            const ticked = opts.filter((o) => take(o.id) === CHECKBOX_DONE);
            const unticked = opts.filter((o) => answerMap[o.id] !== CHECKBOX_DONE);
            rows.push(
              <Row key={item.id} label={item.title}>
                {ticked.length > 0 && (
                  <ul className="cla-list">
                    {ticked.map((o) => (
                      <li key={o.id}>{o.value}</li>
                    ))}
                  </ul>
                )}
                {unticked.length > 0 && (
                  <>
                    <div className="muted cla-note">Not ticked:</div>
                    <ul className="cla-list">
                      {unticked.map((o) => (
                        <li key={o.id} className={item.unticked_tag ? "cla-issue" : undefined}>
                          {o.value}
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </Row>
            );
          } else if (item.legacy_role === "confirmation") {
            const v = take(item.id);
            rows.push(
              <Row key={item.id} label="Confirmation">
                {v ? "Confirmed" : <span className="muted">Not confirmed</span>}
              </Row>
            );
          } else if (item.type === "file_upload") {
            rows.push(
              <Row key={item.id} label={item.title}>
                <PhotoAnswer value={take(item.id)} media={media} />
              </Row>
            );
          } else {
            rows.push(
              <Row key={item.id} label={item.title}>
                <Answer value={take(item.id)} options={item.options} />
              </Row>
            );
          }
        }
        // A branch the staff member never went down: no answer at all.
        const skipped = Boolean(section.items) && !rows.length
          ? false
          : Boolean(section.items) && sectionKeys(section).every((k) => answerMap[k] == null || answerMap[k] === "");
        return { section, rows: skipped ? [] : rows };
      })
    : [];

  const tail = [];
  if (schema?.issues_field) {
    tail.push(
      <Row key="issues" label={schema.issues_field.label}>
        <Answer value={take(schema.issues_field.id)} />
      </Row>
    );
  }
  if (schema?.confirmation) {
    const v = take(schema.confirmation.id);
    tail.push(
      <Row key="confirm" label="Confirmation">
        {v ? "Confirmed" : <span className="muted">Not confirmed</span>}
      </Row>
    );
  }
  const leftovers = Object.keys(answerMap).filter((k) => !used.has(k));

  return (
    <div className="cla">
      {sections.map(({ section, rows }) =>
        rows.length ? (
          <div key={section.key} className="cla-section">
            {section.title && <h3 className="cla-section-title">{section.title}</h3>}
            {rows}
          </div>
        ) : null
      )}
      {tail.length > 0 && <div className="cla-section">{tail}</div>}
      {leftovers.length > 0 && (
        <div className="cla-section">
          <h3 className="cla-section-title">{schema ? "Other answers" : "Answers"}</h3>
          {leftovers.map((k) => (
            <Row key={k} label={k}>
              <Answer value={answerMap[k]} />
            </Row>
          ))}
        </div>
      )}
    </div>
  );
}
