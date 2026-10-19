import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  getIssueLeadPreview,
  getIssueLeads,
  runIssueLeadTasksNow,
  saveIssueLeadSettings,
  saveIssueLeads,
} from "../api";
import InfoBlock from "../components/InfoBlock.jsx";
import { basisLabel, buildSaveBody, changeCount, pickerValue } from "../components/issueLeads/issueLeadsModel.js";
import { formatRestaurantTime } from "../format.js";
import PageHeader from "../ui/PageHeader.jsx";

// Admin page: who leads each PO section and each vendor for Fix Issues, and
// the daily Team task each lead gets (W4, docs/BATCH_OCT14_CONTRACT.md).
// Backend: services/issue_leads.py (require_admin on every route).

function PersonSelect({ value, people, onChange, label }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}>
      <option value="">No lead</option>
      {value && !people.some((p) => String(p.user_id) === value) && <option value={value}>Removed user</option>}
      {people.map((p) => (
        <option key={p.user_id} value={String(p.user_id)}>
          {p.name}
          {p.has_tasks ? "" : " (no Tasks access)"}
        </option>
      ))}
    </select>
  );
}

function ProposalNote({ row }) {
  const p = row.proposal;
  if (!p) return row.note ? <div className="leads-proposal">{row.note}</div> : <div className="leads-proposal">Not in the document.</div>;
  return (
    <div className="leads-proposal">
      Document: {p.doc_lead}
      {p.name ? (p.name !== p.doc_lead ? ` (${p.name})` : "") : " - no matching user"} · {basisLabel(p)}
      {p.why ? ` · ${p.why}` : ""}
    </div>
  );
}

function LeadRows({ rows, kind, nameKey, edits, setEdit, people }) {
  return (
    <div className="table-wrap">
      <table className="leads-table">
        <thead>
          <tr>
            <th>{kind === "section" ? "PO section" : "Vendor"}</th>
            <th className="num">Items</th>
            <th>Lead</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const key = `${kind}:${row[nameKey]}`;
            const value = pickerValue(row, edits, key);
            const savedValue = row.saved ? (row.lead?.user_id != null ? String(row.lead.user_id) : "") : null;
            const changed = savedValue === null || value !== savedValue;
            return (
              <tr key={key} className={changed ? "leads-changed" : undefined}>
                <td className="cell-name">
                  {row[nameKey]}
                  {!row.saved && <span className="pill pill-warn" style={{ marginLeft: "0.4rem" }}>Not saved</span>}
                </td>
                <td className="num">{row.items}</td>
                <td>
                  <PersonSelect value={value} people={people} onChange={(v) => setEdit(key, v)} label={`Lead for ${row[nameKey]}`} />
                  <ProposalNote row={row} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function DocumentCard({ doc }) {
  return (
    <section className="card">
      <div className="card-header">
        <h3 style={{ margin: 0 }}>From the owner's document</h3>
      </div>
      <p className="muted" style={{ marginTop: 0 }}>
        {doc.title}, page 1. Leads are matched to users by name; vendors to the vendor names in the inventory.
      </p>
      <div className="leads-grid">
        {[...doc.leads, doc.fallback].map((l) => (
          <div key={l.key} className="leads-doc-card">
            <h4>
              {l.name} <span className="muted">- {l.title}</span>
            </h4>
            <div>
              User:{" "}
              {l.user ? <strong>{l.user.name}</strong> : <span className="error">no matching user - unassigned</span>}
            </div>
            {l.departments && <div className="muted">{l.departments}</div>}
            {l.vendors?.length > 0 && (
              <div style={{ marginTop: "0.35rem" }}>
                Vendors:{" "}
                {l.vendors.map((v, i) => (
                  <span key={v.name}>
                    {i > 0 && ", "}
                    {v.matched.length ? (
                      v.matched.join(" / ") === v.name ? v.name : `${v.name} → ${v.matched.join(" / ")}`
                    ) : (
                      <span className="error">{v.name} (not in inventory)</span>
                    )}
                  </span>
                ))}
              </div>
            )}
            {!l.vendors?.length && !l.departments && <div className="muted">Everything else (no section or vendor lead).</div>}
          </div>
        ))}
      </div>
    </section>
  );
}

function TaskSettings({ page, onSaved }) {
  const s = page.settings;
  const [form, setForm] = useState(s);
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState(null);
  const [previewing, setPreviewing] = useState(false);
  const [running, setRunning] = useState(false);
  useEffect(() => setForm(page.settings), [page.settings]);

  const leadPeople = useMemo(() => {
    const ids = new Set();
    for (const r of [...page.sections, ...page.vendors, page.fallback]) if (r.lead?.user_id != null) ids.add(r.lead.user_id);
    return page.people.filter((p) => ids.has(p.user_id));
  }, [page]);

  const set = (patch) => setForm((f) => ({ ...f, ...patch }));

  async function save() {
    setBusy(true);
    setMsg(null);
    try {
      const out = await saveIssueLeadSettings(form);
      setMsg({ ok: true, text: "Saved." });
      onSaved(out.settings);
    } catch (err) {
      setMsg({ ok: false, text: err.message });
    } finally {
      setBusy(false);
    }
  }

  async function loadPreview() {
    setPreviewing(true);
    setMsg(null);
    try {
      setPreview(await getIssueLeadPreview());
    } catch (err) {
      setMsg({ ok: false, text: err.message });
    } finally {
      setPreviewing(false);
    }
  }

  async function runNow() {
    if (!window.confirm("Create (or update) today's Fix Issues task for every lead now?")) return;
    setRunning(true);
    setMsg(null);
    try {
      const r = await runIssueLeadTasksNow();
      setMsg({ ok: true, text: `Done: ${r.created} created, ${r.updated} updated, ${r.closed} older closed.` });
      onSaved();
    } catch (err) {
      setMsg({ ok: false, text: err.message });
    } finally {
      setRunning(false);
    }
  }

  const hoursFor = (obj, key) => (obj?.[key] != null ? String(obj[key]) : "");
  const setMap = (field, key, value) =>
    setForm((f) => {
      const next = { ...(f[field] || {}) };
      if (value === "") delete next[key];
      else next[key] = Number(value);
      return { ...f, [field]: next };
    });
  const typesOff = new Set(form.types_off || []);

  return (
    <section className="card">
      <div className="card-header">
        <h3 style={{ margin: 0 }}>Daily lead tasks</h3>
      </div>
      <p className="muted" style={{ marginTop: 0 }}>
        Every day at the set time each lead gets one Team task with their open Fix Issues items and a link to their
        list. Running again the same day updates it; tomorrow's list replaces it. Only admins set the hours.
      </p>
      <div className="leads-settings">
        <label className="leads-check">
          <input type="checkbox" checked={!!form.enabled} onChange={(e) => set({ enabled: e.target.checked })} /> Create the tasks
          every day
        </label>
        <label>
          Time (store time)
          <input type="time" min="06:00" max="20:00" value={form.time} onChange={(e) => set({ time: e.target.value })} />
        </label>
        <label>
          Hours to complete (default)
          <input type="number" min="1" max="72" step="1" value={form.default_hours} onChange={(e) => set({ default_hours: e.target.value })} />
        </label>
      </div>
      {leadPeople.length > 0 && (
        <>
          <h4>Hours per lead (optional)</h4>
          <div className="leads-settings">
            {leadPeople.map((p) => (
              <label key={p.user_id}>
                {p.name}
                <input
                  type="number"
                  min="1"
                  max="72"
                  placeholder="Use the type / default"
                  value={hoursFor(form.hours_by_user, String(p.user_id))}
                  onChange={(e) => setMap("hours_by_user", String(p.user_id), e.target.value)}
                />
              </label>
            ))}
          </div>
        </>
      )}
      <h4>Per issue type</h4>
      <p className="muted" style={{ marginTop: 0 }}>
        A lead's own hours win; otherwise the shortest hours among the types in their task; otherwise the default.
      </p>
      <div className="leads-settings">
        {page.issue_types.map((t) => (
          <label key={t.key}>
            <span>
              <input
                type="checkbox"
                style={{ width: "auto", marginRight: "0.35rem" }}
                checked={!typesOff.has(t.key)}
                onChange={(e) =>
                  set({ types_off: e.target.checked ? [...typesOff].filter((k) => k !== t.key) : [...typesOff, t.key] })
                }
              />
              {t.label}
            </span>
            <input
              type="number"
              min="1"
              max="72"
              placeholder="Default"
              aria-label={`Hours for ${t.label}`}
              value={hoursFor(form.hours_by_type, t.key)}
              onChange={(e) => setMap("hours_by_type", t.key, e.target.value)}
            />
          </label>
        ))}
      </div>
      <div className="leads-actions" style={{ marginTop: "0.9rem" }}>
        <button type="button" disabled={busy} onClick={save}>
          {busy ? "Saving…" : "Save task settings"}
        </button>
        <button type="button" className="btn-secondary" disabled={previewing} onClick={loadPreview}>
          {previewing ? "Counting…" : "Preview the next tasks"}
        </button>
        <button type="button" className="btn-secondary" disabled={running || !page.saved} onClick={runNow}>
          {running ? "Running…" : "Run now"}
        </button>
        {msg && <span className={msg.ok ? "success" : "error"}>{msg.text}</span>}
      </div>
      {page.settings_changed && (
        <p className="muted">
          Settings last changed by {page.settings_changed.changed_by}, {formatRestaurantTime(page.settings_changed.changed_at)}.
        </p>
      )}
      {page.last_run && (
        <p className="muted">
          Last run: {page.last_run.day} ({page.last_run.status}){page.last_run.detail ? ` - ${page.last_run.detail}` : ""}
        </p>
      )}
      {preview && (
        <div style={{ marginTop: "0.5rem" }}>
          <h4>If the tasks were made now</h4>
          {preview.leads.length === 0 && <p className="muted">No lead has anything open (or no leads are saved yet).</p>}
          {preview.leads.map((l) => (
            <div key={l.user_id} className="leads-preview-row">
              <span>
                <strong>{l.name}</strong>: {l.count} item{l.count === 1 ? "" : "s"}, due in {l.hours} h
              </span>
              <Link to={`/fix-issues?lead=${l.user_id}`}>Open their list</Link>
            </div>
          ))}
          <div className="leads-preview-row">
            <span>
              No lead: {preview.unassigned} item{preview.unassigned === 1 ? "" : "s"}
            </span>
            <Link to="/fix-issues?lead=none">Open</Link>
          </div>
        </div>
      )}
    </section>
  );
}

export default function IssueLeadsPage() {
  const [page, setPage] = useState(null);
  const [error, setError] = useState(null);
  const [edits, setEdits] = useState({});
  const [tab, setTab] = useState("sections");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState(null);

  const load = useCallback(() => {
    setError(null);
    return getIssueLeads()
      .then((d) => {
        setPage(d);
        setEdits({});
      })
      .catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const setEdit = (key, value) => setEdits((e) => ({ ...e, [key]: value }));
  const body = page ? buildSaveBody(page, edits, { includeUnsaved: true }) : null;
  const pending = body ? changeCount(body) : 0;

  async function save() {
    setSaving(true);
    setMsg(null);
    try {
      const out = await saveIssueLeads(body);
      setPage(out);
      setEdits({});
      setMsg({ ok: true, text: `Saved ${out.saved_count} lead${out.saved_count === 1 ? "" : "s"}.` });
    } catch (err) {
      setMsg({ ok: false, text: err.message });
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <PageHeader pageId="issue_leads">
        <p className="muted">Who follows up each PO section and vendor in Fix Issues.</p>
      </PageHeader>
      <InfoBlock brief="An item belongs to its PO section's lead; with no section lead, to its vendor's lead; otherwise to the everything-else lead (an admin).">
        PO sections are the store aisles the purchase order groups by (edit them on the PO sections page). Fix Issues
        groups every list by section and can be filtered by lead. Nothing is assigned until this page is saved: the
        leads shown before that are a proposal read from the owner's quick-reference document - "From the document"
        when the document names the department, "Guess - check" when it doesn't. Choose "No lead" to leave a section
        to its vendors' leads.
      </InfoBlock>
      {error && <p className="error">{error}</p>}
      {!page && !error && <p className="muted">Loading…</p>}
      {page && (
        <>
          {!page.saved && (
            <p className="fi-lead-banner">
              Nothing is assigned yet. Review the proposal below, change anything that's wrong, then save.
            </p>
          )}
          <section className="card">
            <div className="leads-tabs" role="tablist">
              {[
                ["sections", `PO sections (${page.sections.length})`],
                ["vendors", `Vendors (${page.vendors.length})`],
                ["fallback", "Everything else"],
              ].map(([k, label]) => (
                <button
                  key={k}
                  type="button"
                  role="tab"
                  aria-selected={tab === k}
                  className={`leads-tab${tab === k ? " active" : ""}`}
                  onClick={() => setTab(k)}
                >
                  {label}
                </button>
              ))}
            </div>
            {tab === "sections" && (
              <LeadRows rows={page.sections} kind="section" nameKey="section" edits={edits} setEdit={setEdit} people={page.people} />
            )}
            {tab === "vendors" && (
              <LeadRows rows={page.vendors} kind="vendor" nameKey="vendor" edits={edits} setEdit={setEdit} people={page.people} />
            )}
            {tab === "fallback" && (
              <div>
                <p>Items whose section and vendors have no lead go to:</p>
                <PersonSelect
                  value={pickerValue(page.fallback, edits, "fallback")}
                  people={page.people}
                  onChange={(v) => setEdit("fallback", v)}
                  label="Everything-else lead"
                />
                <ProposalNote row={page.fallback} />
              </div>
            )}
            {page.stale?.length > 0 && (
              <p className="muted">
                Saved but no longer in the inventory: {page.stale.map((s) => `${s.name} (${s.lead?.name || "no lead"})`).join(", ")}.
              </p>
            )}
            <div className="leads-actions" style={{ marginTop: "0.9rem" }}>
              <button type="button" disabled={saving || pending === 0} onClick={save}>
                {saving ? "Saving…" : pending ? `Save ${pending} change${pending === 1 ? "" : "s"}` : "Saved"}
              </button>
              {Object.keys(edits).length > 0 && (
                <button type="button" className="btn-secondary" onClick={() => setEdits({})}>
                  Undo my edits
                </button>
              )}
              {msg && <span className={msg.ok ? "success" : "error"}>{msg.text}</span>}
            </div>
          </section>
          <TaskSettings
            page={page}
            onSaved={(settings) => (settings ? setPage((p) => ({ ...p, settings })) : load())}
          />
          <DocumentCard doc={page.document} />
        </>
      )}
    </>
  );
}
