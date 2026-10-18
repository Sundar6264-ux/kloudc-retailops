import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  getChecklistSchema,
  getChecklistSchemas,
  getChecklistSchemaVersion,
  getChecklistSubmission,
  getChecklistSubmissions,
  listUsers,
  submitChecklist,
} from "../api";
import { useAuth } from "../AuthContext.jsx";
import InfoBlock from "./InfoBlock.jsx";
import SortableTh from "./SortableTh.jsx";
import { useTableControls } from "../useTableControls.js";
import { Term } from "../glossary.jsx";
import { formatRestaurantTime } from "../format.js";
import ChecklistForm from "./checklists/ChecklistForm.jsx";
import ChecklistAnswers from "./checklists/ChecklistAnswers.jsx";

// Rendering (legacy grid / questions / checkbox-group blocks and the
// builder's generic `items`), branching and validation live in
// ./checklists/ (shared with the Checklist builder's live preview).

// Extra "who/what" answers the backend lists next to "Submitted by"
// (submission `details`, e.g. [{label: "Chef", value: "RK"}]) as "Chef: RK".
function detailsText(item) {
  return (item.details || []).map((d) => `${d.label}: ${d.value}`).join(" · ");
}

// Store-local (the store's own time zone, format.js), whatever the viewer's device timezone.
function formatDateTime(iso) {
  return formatRestaurantTime(iso) || "-";
}

// "via public link" tag: filled on the logged-out checklist link page by the
// name shown (no app account).
export function PublicLinkTag({ item }) {
  if (!item?.via_public_link) return null;
  return (
    <Term k="checklist_public_link_submission">
      <span className="pill pill-info clp-tag">via public link</span>
    </Term>
  );
}

function SubmitterCell({ item }) {
  return (
    <>
      {item.submitted_by_name} <PublicLinkTag item={item} />
    </>
  );
}

function StatusPillChecklist({ allClear }) {
  return (
    <Term k={allClear ? "checklist_all_clear" : "checklist_issues"}>
      <span className={`pill ${allClear ? "pill-success" : "pill-failed"}`}>{allClear ? "All Clear" : "Issues"}</span>
    </Term>
  );
}

// `/schemas` returns {schemas: {type: schema}} (insertion order = sort order);
// accept a list too.
export function schemasToMap(res) {
  const raw = res?.schemas ?? res;
  if (Array.isArray(raw)) return Object.fromEntries(raw.map((s) => [s.checklist_type, s]));
  return raw || {};
}

function ChecklistFormCard({ schema: initialSchema, onSubmitted, onCancel }) {
  const { user } = useAuth();
  // The hub's copy can be old (an admin may have published since the page
  // loaded), so the form re-reads the current version when it opens. Answers
  // already typed are kept when the schema is swapped (ChecklistForm keeps its
  // state; answers to questions that no longer exist aren't sent).
  const [schema, setSchema] = useState(initialSchema);
  useEffect(() => {
    let cancelled = false;
    getChecklistSchema(initialSchema.checklist_type)
      .then((fresh) => {
        if (!cancelled && fresh && fresh.version !== initialSchema.version) setSchema(fresh);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [initialSchema]);
  return (
    <section className="card">
      <ChecklistForm
        schema={schema}
        header={
          <>
            <button type="button" className="link-button" onClick={onCancel}>
              ← Choose a different checklist
            </button>
            <h2>{schema.title}</h2>
            {schema.description && <p className="muted">{schema.description}</p>}
            <p className="muted">
              Submitting as <strong>{user?.username}</strong>
            </p>
          </>
        }
        onSubmit={async (answers) => {
          let data;
          try {
            data = await submitChecklist(schema.checklist_type, answers, schema.version);
          } catch (err) {
            // 409 = the checklist was changed since it was opened and these
            // answers don't fit the new version: load the new version (typed
            // answers stay) and show the server's message.
            if (err.status === 409) {
              try {
                setSchema(await getChecklistSchema(schema.checklist_type));
              } catch {
                // keep the old form; the server's message still says what to do
                throw err;
              }
              throw new Error(
                "This checklist was changed since you opened it. The new version is shown now - your answers are kept. Answer any new questions and submit again."
              );
            }
            throw err;
          }
          onSubmitted?.();
          return data;
        }}
        renderResult={(result) => (
          <>
            <h2>Submitted</h2>
            <p>
              <StatusPillChecklist allClear={result.all_clear} />
            </p>
            {result.all_clear ? (
              <p className="success">No issues reported.</p>
            ) : (
              <pre className="checklist-issue-summary">{result.issue_summary}</pre>
            )}
            <button type="button" onClick={onCancel}>
              Back to Daily Checklists
            </button>
          </>
        )}
      />
    </section>
  );
}

function ChecklistHistory({ schemas, filters, onFiltersChange, onBack, onSelect }) {
  const { user } = useAuth();
  const isAdmin = !!user?.is_admin;
  const [items, setItems] = useState(null);
  const [error, setError] = useState(null);
  const [employees, setEmployees] = useState([]);

  useEffect(() => {
    if (!isAdmin) return;
    listUsers()
      .then(setEmployees)
      .catch(() => setEmployees([]));
  }, [isAdmin]);

  useEffect(() => {
    setItems(null);
    getChecklistSubmissions({
      checklistType: filters.checklistType || undefined,
      employeeUserId: isAdmin && filters.employeeUserId ? Number(filters.employeeUserId) : undefined,
      status: filters.status || undefined,
      startDate: filters.startDate || undefined,
      endDate: filters.endDate || undefined,
      limit: 200,
    })
      .then((res) => setItems(res.items))
      .catch((err) => setError(err.message));
  }, [filters, isAdmin]);

  const { search, setSearch, sortKey, sortDir, toggleSort, rows } = useTableControls(items || [], {
    searchKeys: ["checklist_title", "submitted_by_name"],
    defaultSortKey: "created_at",
    defaultSortDir: "desc",
  });

  function setFilter(key, value) {
    onFiltersChange({ ...filters, [key]: value });
  }
  // Staff see their own submissions plus public-link ones (by other people),
  // so the "Submitted by" column shows whenever there can be others.
  const showSubmitter = isAdmin || (items || []).some((i) => i.via_public_link);

  return (
    <section className="card">
      <button type="button" className="link-button" onClick={onBack}>
        ← Back to Daily Checklists
      </button>
      <h2>History</h2>
      <div className="recon-form" style={{ borderBottom: "none", paddingBottom: 0 }}>
        <label className="recon-form-field">
          Checklist
          <select value={filters.checklistType} onChange={(e) => setFilter("checklistType", e.target.value)}>
            <option value="">All</option>
            {Object.entries(schemas).map(([key, s]) => (
              <option key={key} value={key}>
                {s.title}
              </option>
            ))}
          </select>
        </label>
        <label className="recon-form-field">
          Status
          <select value={filters.status} onChange={(e) => setFilter("status", e.target.value)}>
            <option value="">All</option>
            <option value="all_clear">All Clear</option>
            <option value="issues">Issues</option>
          </select>
        </label>
        {isAdmin && (
          <label className="recon-form-field">
            Employee
            <select value={filters.employeeUserId} onChange={(e) => setFilter("employeeUserId", e.target.value)}>
              <option value="">All</option>
              <option value="0">Public link (no account)</option>
              {employees.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.username}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="recon-form-field">
          From
          <input type="date" value={filters.startDate} onChange={(e) => setFilter("startDate", e.target.value)} />
        </label>
        <label className="recon-form-field">
          To
          <input type="date" value={filters.endDate} onChange={(e) => setFilter("endDate", e.target.value)} />
        </label>
      </div>
      <input
        type="text"
        placeholder="Search checklist, submitted by…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        style={{ margin: "0.75rem 0" }}
      />
      {error && <p className="error">{error}</p>}
      {items === null && !error && <p className="muted">Loading…</p>}
      {items !== null && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <SortableTh sortKey="created_at" currentSortKey={sortKey} sortDir={sortDir} onSort={toggleSort}>
                  Date
                </SortableTh>
                <SortableTh sortKey="checklist_title" currentSortKey={sortKey} sortDir={sortDir} onSort={toggleSort}>
                  Checklist
                </SortableTh>
                {showSubmitter && (
                  <SortableTh sortKey="submitted_by_name" currentSortKey={sortKey} sortDir={sortDir} onSort={toggleSort}>
                    Submitted by
                  </SortableTh>
                )}
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((item) => (
                <tr key={item.id}>
                  <td>{formatDateTime(item.created_at)}</td>
                  <td>
                    {item.checklist_title}
                    {detailsText(item) && <div className="muted checklist-details">{detailsText(item)}</div>}
                  </td>
                  {showSubmitter && (
                    <td>
                      <SubmitterCell item={item} />
                    </td>
                  )}
                  <td>
                    <StatusPillChecklist allClear={item.all_clear} />
                  </td>
                  <td>
                    <button type="button" className="link-button" onClick={() => onSelect(item.id)}>
                      View
                    </button>
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={showSubmitter ? 5 : 4} className="muted">
                    No submissions{search ? " match your search" : ""}.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function ChecklistDetail({ id, schemas, onBack }) {
  const [item, setItem] = useState(null);
  const [schema, setSchema] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setItem(null);
    setSchema(null);
    setError(null);
    getChecklistSubmission(id)
      .then(async (sub) => {
        if (cancelled) return;
        setItem(sub);
        // Draw the answers with the version this was submitted against (an
        // answer to a question removed since still shows its question).
        let s = sub.schema || null;
        if (!s) {
          try {
            s = await getChecklistSchemaVersion(sub.checklist_type, sub.definition_version);
          } catch {
            s = schemas?.[sub.checklist_type] || null;
          }
        }
        if (!cancelled) setSchema(s || false);
      })
      .catch((err) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
    };
  }, [id, schemas]);

  return (
    <section className="card">
      <button type="button" className="link-button" onClick={onBack}>
        ← Back
      </button>
      {error && <p className="error">{error}</p>}
      {!item && !error && <p className="muted">Loading…</p>}
      {item && (
        <>
          <h2>{item.checklist_title}</h2>
          <p>
            <strong>Submitted by:</strong> {item.submitted_by_name} <PublicLinkTag item={item} />
          </p>
          {(item.details || []).map((d) => (
            <p key={d.label}>
              <strong>{d.label}:</strong> {d.value}
            </p>
          ))}
          <p>
            <strong>Submitted:</strong> {formatDateTime(item.created_at)}
            {item.definition_version != null && <span className="muted"> · version {item.definition_version}</span>}
          </p>
          <p>
            <StatusPillChecklist allClear={item.all_clear} />
          </p>
          {item.all_clear ? (
            <p className="success">No issues were reported.</p>
          ) : (
            <>
              <h3>Issue Summary</h3>
              <pre className="checklist-issue-summary">{item.issue_summary}</pre>
            </>
          )}
          <h3 style={{ marginTop: "1.25rem" }}>Answers</h3>
          {schema === null ? (
            <p className="muted">Loading…</p>
          ) : (
            <ChecklistAnswers schema={schema || null} answers={item.answers} media={item.media} />
          )}
        </>
      )}
    </section>
  );
}

function ChecklistHub({ schemas, recent, recentError, onOpenForm, onOpenHistory, onSelectSubmission }) {
  const { user } = useAuth();
  return (
    <>
      <section className="card">
        <div className="clb-hub-head">
          <h2>Daily Checklists</h2>
          {user?.is_admin && (
            <Link to="/checklists/admin" className="button-link btn-secondary clb-hub-builder">
              Checklist builder
            </Link>
          )}
        </div>
        <InfoBlock brief="The store's daily checklists. Answers are saved in this app, and checklists set up for it post a summary to the staff WhatsApp group automatically. Admins create and edit the checklists in the Checklist builder.">
          Each submission is tied to your logged-in account - you're never asked to type your own
          name. Admins see every submission from every employee; everyone else sees their own, plus
          any sent through a checklist's public link (marked "via public link", with the name the
          person picked).
        </InfoBlock>
        <div className="checklist-hub-tiles">
          {Object.entries(schemas).map(([key, schema]) => (
            <button key={key} type="button" className="checklist-tile" onClick={() => onOpenForm(key)}>
              <span className="checklist-tile-title">{schema.title}</span>
              <span className="muted">Start today's checklist</span>
            </button>
          ))}
        </div>
      </section>

      <section className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h2>Recent Submissions</h2>
          <button type="button" className="link-button" onClick={onOpenHistory}>
            History →
          </button>
        </div>
        {recentError && <p className="error">{recentError}</p>}
        {recent === null && !recentError && <p className="muted">Loading…</p>}
        {recent && recent.length === 0 && <p className="muted">No submissions yet.</p>}
        {recent && recent.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Checklist</th>
                  <th>Submitted by</th>
                  <th>Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {recent.map((item) => (
                  <tr key={item.id}>
                    <td>{formatDateTime(item.created_at)}</td>
                    <td>
                      {item.checklist_title}
                      {detailsText(item) && <div className="muted checklist-details">{detailsText(item)}</div>}
                    </td>
                    <td>
                      <SubmitterCell item={item} />
                    </td>
                    <td>
                      <StatusPillChecklist allClear={item.all_clear} />
                    </td>
                    <td>
                      <button type="button" className="link-button" onClick={() => onSelectSubmission(item.id)}>
                        View
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}

const DEFAULT_HISTORY_FILTERS = { checklistType: "", employeeUserId: "", status: "", startDate: "", endDate: "" };

export default function DailyChecklists() {
  const [schemas, setSchemas] = useState(null);
  const [schemasError, setSchemasError] = useState(null);
  const [recent, setRecent] = useState(null);
  const [recentError, setRecentError] = useState(null);
  const [historyFilters, setHistoryFilters] = useState(DEFAULT_HISTORY_FILTERS);
  // { mode: "hub" } | { mode: "form", checklistType } | { mode: "history" } |
  // { mode: "detail", id, from: "hub" | "history" }
  const [view, setView] = useState({ mode: "hub" });

  function loadRecent() {
    getChecklistSubmissions({ limit: 5 })
      .then((res) => setRecent(res.items))
      .catch((err) => setRecentError(err.message));
  }

  useEffect(() => {
    getChecklistSchemas()
      .then((res) => setSchemas(schemasToMap(res)))
      .catch((err) => setSchemasError(err.message));
    loadRecent();
  }, []);

  if (schemasError) return <p className="error">{schemasError}</p>;
  if (!schemas) return <p className="muted">Loading…</p>;

  if (view.mode === "form") {
    return (
      <ChecklistFormCard
        schema={schemas[view.checklistType]}
        onSubmitted={loadRecent}
        onCancel={() => setView({ mode: "hub" })}
      />
    );
  }

  if (view.mode === "history") {
    return (
      <ChecklistHistory
        schemas={schemas}
        filters={historyFilters}
        onFiltersChange={setHistoryFilters}
        onBack={() => setView({ mode: "hub" })}
        onSelect={(id) => setView({ mode: "detail", id, from: "history" })}
      />
    );
  }

  if (view.mode === "detail") {
    return <ChecklistDetail id={view.id} schemas={schemas} onBack={() => setView({ mode: view.from })} />;
  }

  return (
    <ChecklistHub
      schemas={schemas}
      recent={recent}
      recentError={recentError}
      onOpenForm={(checklistType) => setView({ mode: "form", checklistType })}
      onOpenHistory={() => setView({ mode: "history" })}
      onSelectSubmission={(id) => setView({ mode: "detail", id, from: "hub" })}
    />
  );
}
