import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  createChecklistDefinition,
  duplicateChecklistDefinition,
  listChecklistDefinitions,
  reorderChecklistDefinitions,
  updateChecklistDefinition,
} from "../../api";
import { formatRestaurantTime } from "../../format.js";
import { definitionsFromList, errorLines } from "./editorModel.js";
import WhatsAppGroupsPanel from "./WhatsAppGroupsPanel.jsx";

// Admin list of every checklist (active and inactive): open the editor,
// create, duplicate, activate/deactivate and reorder (the order staff see
// the tiles in).

function NewChecklistDialog({ onClose, onCreated }) {
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  async function create(e) {
    e.preventDefault();
    if (!title.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      const res = await createChecklistDefinition({ title: title.trim() });
      onCreated(res.checklist_type || res.definition?.checklist_type);
    } catch (e2) {
      setErr(errorLines(e2).join(" "));
      setBusy(false);
    }
  }
  return (
    <div className="clb-modal-backdrop" onClick={onClose}>
      <form className="clb-modal" role="dialog" aria-modal="true" aria-label="New checklist" onClick={(e) => e.stopPropagation()} onSubmit={create}>
        <div className="clb-modal-head">
          <h3>New checklist</h3>
          <button type="button" className="clb-icon-btn" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="clb-modal-body">
          <label className="clb-field">
            Title
            <input
              type="text"
              autoFocus
              value={title}
              maxLength={120}
              placeholder="e.g. Kitchen - Opening Checklist"
              onChange={(e) => setTitle(e.target.value)}
            />
          </label>
          <p className="muted">It starts as an unpublished draft. Staff only see it after you publish it.</p>
          {err && <p className="error">{err}</p>}
        </div>
        <div className="clb-modal-actions">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" disabled={busy || !title.trim()}>
            {busy ? "Creating…" : "Create"}
          </button>
        </div>
      </form>
    </div>
  );
}

export default function ChecklistAdminList() {
  const navigate = useNavigate();
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [busy, setBusy] = useState(null);
  const [creating, setCreating] = useState(false);
  const [managingGroups, setManagingGroups] = useState(false);

  const load = useCallback(() => {
    listChecklistDefinitions()
      .then((res) => {
        const list = definitionsFromList(res);
        list.sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0));
        setRows(list);
      })
      .catch((e) => setError(errorLines(e).join(" ")));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function run(key, fn) {
    setBusy(key);
    setActionError(null);
    try {
      await fn();
      load();
    } catch (e) {
      setActionError(errorLines(e).join(" "));
    } finally {
      setBusy(null);
    }
  }

  function reorder(idx, delta) {
    const j = idx + delta;
    if (j < 0 || j >= rows.length) return;
    const next = [...rows];
    [next[idx], next[j]] = [next[j], next[idx]];
    setRows(next);
    run("order", () => reorderChecklistDefinitions(next.map((d) => d.checklist_type)));
  }

  return (
    <>
      <section className="card">
        <div className="clb-list-head">
          <div>
            <h2>Checklists</h2>
            <p className="muted">
              Create and edit the checklists staff fill in on the website and the app. Changes are drafts until you
              publish them; every publish is kept as a version.
            </p>
          </div>
          <div className="clb-list-head-actions">
            <button type="button" className="btn-secondary" onClick={() => setManagingGroups(true)}>
              WhatsApp groups
            </button>
            <button type="button" onClick={() => setCreating(true)}>
              + New checklist
            </button>
          </div>
        </div>
        {error && <p className="error">{error}</p>}
        {actionError && <p className="error">{actionError}</p>}
        {!rows && !error && <p className="muted">Loading…</p>}
        {rows && rows.length === 0 && <p className="muted">No checklists yet.</p>}
        {rows && rows.length > 0 && (
          <div className="table-wrap">
            <table className="clb-list-table">
              <thead>
                <tr>
                  <th>Order</th>
                  <th>Checklist</th>
                  <th>Status</th>
                  <th>Closing</th>
                  <th className="num">Version</th>
                  <th className="num">Submissions</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((d, idx) => {
                  const version = d.current_version ?? null;
                  const settings = d.settings || {};
                  return (
                    <tr key={d.checklist_type} className={d.active === false ? "clb-row-inactive" : undefined}>
                      <td className="clb-order-cell">
                        <button
                          type="button"
                          className="clb-icon-btn"
                          aria-label={`Move ${d.title} up`}
                          disabled={idx === 0 || busy != null}
                          onClick={() => reorder(idx, -1)}
                        >
                          ↑
                        </button>
                        <button
                          type="button"
                          className="clb-icon-btn"
                          aria-label={`Move ${d.title} down`}
                          disabled={idx === rows.length - 1 || busy != null}
                          onClick={() => reorder(idx, 1)}
                        >
                          ↓
                        </button>
                      </td>
                      <td className="cell-name">
                        <Link to={`/checklists/admin/${encodeURIComponent(d.checklist_type)}`}>{d.title}</Link>
                        <div className="muted clb-slug">
                          {d.checklist_type}
                          {d.updated_at && ` · edited ${formatRestaurantTime(d.updated_at)}`}
                        </div>
                      </td>
                      <td>
                        <span className={`pill ${d.active === false ? "pill-neutral" : "pill-success"}`}>
                          {d.active === false ? "Inactive" : "Active"}
                        </span>
                        {version == null && <span className="pill pill-warn clb-pill-sm">Not published</span>}
                        {version != null && d.has_draft && <span className="pill pill-info clb-pill-sm">Draft changes</span>}
                        {d.min_app_build && (
                          <span className="pill pill-warn clb-pill-sm" title="Phones on an older app build see 'Update the app' for this checklist">
                            App build {d.min_app_build}+
                          </span>
                        )}
                      </td>
                      <td>{settings.closing ? "Yes" : <span className="muted">-</span>}</td>
                      <td className="num">
                        {version != null ? `v${version}` : "-"}
                        {d.version_count != null && <div className="muted clb-slug">{d.version_count} total</div>}
                      </td>
                      <td className="num">
                        {d.submission_count ?? "-"}
                        {d.last_submission_at && <div className="muted clb-slug">last {formatRestaurantTime(d.last_submission_at)}</div>}
                      </td>
                      <td className="clb-row-actions">
                        <Link to={`/checklists/admin/${encodeURIComponent(d.checklist_type)}`}>Edit</Link>
                        <button
                          type="button"
                          className="link-button"
                          disabled={busy != null}
                          onClick={() =>
                            run(`dup-${d.checklist_type}`, async () => {
                              const res = await duplicateChecklistDefinition(d.checklist_type);
                              const t = res.checklist_type || res.definition?.checklist_type;
                              if (t) navigate(`/checklists/admin/${encodeURIComponent(t)}`);
                            })
                          }
                        >
                          {busy === `dup-${d.checklist_type}` ? "Duplicating…" : "Duplicate"}
                        </button>
                        <button
                          type="button"
                          className={`link-button${d.active === false ? "" : " link-danger"}`}
                          disabled={busy != null}
                          onClick={() => {
                            if (
                              d.active !== false &&
                              !window.confirm(`Hide "${d.title}" from staff? Past submissions stay. You can turn it back on any time.`)
                            )
                              return;
                            run(`act-${d.checklist_type}`, () =>
                              updateChecklistDefinition(d.checklist_type, { active: d.active === false })
                            );
                          }}
                        >
                          {d.active === false ? "Activate" : "Deactivate"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {managingGroups && <WhatsAppGroupsPanel onClose={() => setManagingGroups(false)} onChanged={load} />}
      {creating && (
        <NewChecklistDialog
          onClose={() => setCreating(false)}
          onCreated={(type) => {
            setCreating(false);
            if (type) navigate(`/checklists/admin/${encodeURIComponent(type)}`);
            else load();
          }}
        />
      )}
    </>
  );
}
