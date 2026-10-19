import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  discardChecklistDraft,
  getChecklistDefinition,
  getChecklistDefinitionVersion,
  getChecklistEditorMeta,
  publishChecklistDefinition,
  revertChecklistDefinition,
  saveChecklistDraft,
  updateChecklistDefinition,
} from "../../api";
import { formatRestaurantTime } from "../../format.js";
import ChecklistForm from "./ChecklistForm.jsx";
import ItemEditor from "./ItemEditor.jsx";
import PublicLinkPanel from "./PublicLinkPanel.jsx";
import WhatsAppGroupsPanel from "./WhatsAppGroupsPanel.jsx";
import { useStoreAllows } from "../../store/StoreContext.jsx";
import {
  allIds,
  blankItem,
  changeType,
  cloneItem,
  definitionFromDetail,
  diffSchemas,
  draftPayload,
  draftProblems,
  errorLines,
  idsInErrors,
  issueTagsFromMeta,
  toEditable,
} from "./editorModel.js";
import { DEFAULT_ISSUE_TAGS, SUBMIT_TARGET, newId } from "./model.js";
import Icon from "../../ui/icons.jsx";

// The Checklist builder's editor (route /checklists/admin/:type). Questions
// tab = Google Forms-style canvas with a live preview beside it; Settings =
// closing alert / WhatsApp group / All as usual / active; Versions = history
// with view and revert. Edits live in a local draft until "Save draft";
// "Publish" makes them what staff fill in.

const clone = (x) => JSON.parse(JSON.stringify(x));

// Unsaved editor changes per checklist type ({baseline, draft}) - see the
// unsaved-changes guard in ChecklistEditor.
const unsavedEdits = new Map();

function locate(draft, itemId) {
  for (let s = 0; s < draft.sections.length; s += 1) {
    const i = (draft.sections[s].items || []).findIndex((it) => it.id === itemId);
    if (i >= 0) return { s, i };
  }
  return null;
}

function Modal({ title, onClose, children, wide }) {
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="clb-modal-backdrop" onClick={onClose}>
      <div
        className={`clb-modal${wide ? " clb-modal-wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="clb-modal-head">
          <h3>{title}</h3>
          <button type="button" className="clb-icon-btn" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

// Staff see the preview exactly like the real form; nothing is sent.
function PreviewPane({ schema, onReset }) {
  return (
    <div className="clb-preview-inner">
      <ChecklistForm
        schema={schema}
        preview
        submitLabel="Submit (preview)"
        header={
          <>
            <div className="clb-preview-head">
              <span className="pill pill-info">Preview</span>
              <button type="button" className="link-button" onClick={onReset}>
                Clear answers
              </button>
            </div>
            <h2>{schema.title || "Untitled checklist"}</h2>
            {schema.description && <p className="muted">{schema.description}</p>}
          </>
        }
        onSubmit={async (answers) => answers}
        renderResult={(answers, _sent, again) => (
          <div>
            <span className="pill pill-success">Preview passed</span>
            <p className="muted" style={{ marginTop: "0.6rem" }}>
              Every required answer was given. This is what would be saved (nothing was sent):
            </p>
            <pre className="checklist-issue-summary clb-preview-json">{JSON.stringify(answers, null, 2)}</pre>
            <button type="button" className="btn-secondary" onClick={again}>
              Fill again
            </button>
          </div>
        )}
      />
    </div>
  );
}

// Before the groups table (old servers / meta failed to load): the two built-ins.
const DEFAULT_WHATSAPP_GROUPS = [
  { key: "store", label: "Store staff group" },
  { key: "kitchen", label: "Kitchen staff group" },
];

function SettingsPanel({ definition, settings, onSaved, groups, canManageGroups, onGroupsChanged }) {
  // The group picker is for a store with WhatsApp (store feature).
  const whatsapp = useStoreAllows("whatsapp");
  const [managing, setManaging] = useState(false);
  const [form, setForm] = useState({
    active: definition.active !== false,
    short_name: definition.short_name || "",
    closing: Boolean(settings.closing),
    whatsapp_group: settings.whatsapp_group || "",
    all_as_usual: settings.all_as_usual !== false,
  });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [err, setErr] = useState(null);
  const set = (patch) => {
    setMsg(null);
    setForm((f) => ({ ...f, ...patch }));
  };

  async function save() {
    setBusy(true);
    setErr(null);
    try {
      const body = {
        active: form.active,
        short_name: form.short_name.trim() || null,
        settings: {
          ...settings,
          closing: form.closing,
          whatsapp_group: form.whatsapp_group || null,
          all_as_usual: form.all_as_usual,
        },
      };
      const res = await updateChecklistDefinition(definition.checklist_type, body);
      setMsg("Settings saved.");
      onSaved?.(res);
    } catch (e) {
      setErr(errorLines(e).join(" "));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="clb-settings card">
      <h2>Settings</h2>
      <p className="muted">These apply straight away; they don't need publishing.</p>
      <div className="clb-setting">
        <div>
          <strong>Active</strong>
          <p className="muted">Inactive checklists are hidden from staff. Past submissions stay.</p>
        </div>
        <input type="checkbox" role="switch" aria-label="Active" checked={form.active} onChange={(e) => set({ active: e.target.checked })} />
      </div>
      <div className="clb-setting">
        <div>
          <strong>Closing checklist</strong>
          <p className="muted">Counted in the 9:30 PM "closing checklist missing" alert and the nightly task.</p>
        </div>
        <input type="checkbox" role="switch" aria-label="Closing checklist" checked={form.closing} onChange={(e) => set({ closing: e.target.checked })} />
      </div>
      <div className="clb-setting">
        <div>
          <strong>"All as usual" button</strong>
          <p className="muted">Lets staff fill the normal answer for every unanswered row in a section.</p>
        </div>
        <input
          type="checkbox"
          role="switch"
          aria-label="All as usual button"
          checked={form.all_as_usual}
          onChange={(e) => set({ all_as_usual: e.target.checked })}
        />
      </div>
      {whatsapp && (
      <label className="clb-setting">
        <div>
          <strong>WhatsApp group</strong>
          <p className="muted">Where the summary is posted after each submission.</p>
        </div>
        <select value={form.whatsapp_group} onChange={(e) => set({ whatsapp_group: e.target.value })}>
          <option value="">None (no WhatsApp message)</option>
          {groups.map((g) => (
            <option key={g.key} value={g.key}>
              {g.label}
            </option>
          ))}
          {form.whatsapp_group && !groups.some((g) => g.key === form.whatsapp_group) && (
            <option value={form.whatsapp_group}>{form.whatsapp_group} (turned off)</option>
          )}
        </select>
      </label>
      )}
      {whatsapp && canManageGroups && (
        <p className="clb-setting-link">
          <button type="button" className="link-button" onClick={() => setManaging(true)}>
            Manage WhatsApp groups
          </button>
        </p>
      )}
      {managing && <WhatsAppGroupsPanel onClose={() => setManaging(false)} onChanged={onGroupsChanged} />}
      <label className="clb-setting">
        <div>
          <strong>Short name</strong>
          <p className="muted">Used where the full title is too long (tiles, nightly task).</p>
        </div>
        <input type="text" value={form.short_name} maxLength={60} onChange={(e) => set({ short_name: e.target.value })} />
      </label>
      {err && <p className="error">{err}</p>}
      {msg && <p className="success">{msg}</p>}
      <button type="button" onClick={save} disabled={busy}>
        {busy ? "Saving…" : "Save settings"}
      </button>
    </div>
  );
}

function VersionsPanel({ checklistType, versions, currentVersion, dirty, onReverted }) {
  const [viewing, setViewing] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(null);

  async function view(n) {
    setErr(null);
    try {
      const res = await getChecklistDefinitionVersion(checklistType, n);
      setViewing({ n, schema: res.schema || res });
    } catch (e) {
      setErr(errorLines(e).join(" "));
    }
  }

  async function revert(n) {
    if (dirty && !window.confirm("You have unsaved changes in the editor. Reverting discards them. Continue?")) return;
    if (!window.confirm(`Publish version ${n} again as a new version? Staff will fill it from now on.`)) return;
    setBusy(n);
    setErr(null);
    try {
      onReverted(await revertChecklistDefinition(checklistType, n));
    } catch (e) {
      setErr(errorLines(e).join(" "));
    } finally {
      setBusy(null);
    }
  }

  const sorted = [...versions].sort((a, b) => (b.version ?? 0) - (a.version ?? 0));
  return (
    <div className="card clb-versions">
      <h2>Version history</h2>
      <p className="muted">Every publish makes a new version. Past submissions always show the version they were filled in.</p>
      {err && <p className="error">{err}</p>}
      {!sorted.length && <p className="muted">Not published yet.</p>}
      {sorted.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Version</th>
                <th>Published</th>
                <th>By</th>
                <th>Note</th>
                <th className="num">Submissions</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((v) => (
                <tr key={v.version}>
                  <td>
                    v{v.version}
                    {(v.current || v.version === currentVersion) && <span className="pill pill-success clb-pill-sm">Current</span>}
                    {v.min_app_build && <span className="pill pill-warn clb-pill-sm">App build {v.min_app_build}+</span>}
                  </td>
                  <td>{formatRestaurantTime(v.created_at) || "-"}</td>
                  <td>{v.created_by || v.created_by_name || "-"}</td>
                  <td className="cell-wrap">{v.note || <span className="muted">-</span>}</td>
                  <td className="num">{v.submission_count ?? "-"}</td>
                  <td className="clb-row-actions">
                    <button type="button" className="link-button" onClick={() => view(v.version)}>
                      View
                    </button>
                    {v.version !== currentVersion && (
                      <button type="button" className="link-button" disabled={busy != null} onClick={() => revert(v.version)}>
                        {busy === v.version ? "Reverting…" : "Revert to this"}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {viewing && (
        <Modal title={`Version ${viewing.n}`} onClose={() => setViewing(null)} wide>
          <div className="clb-modal-body">
            <PreviewPane schema={viewing.schema} onReset={() => setViewing({ ...viewing })} />
          </div>
        </Modal>
      )}
    </div>
  );
}

function PublishDialog({ published, draft, busy, error, onCancel, onConfirm }) {
  const [note, setNote] = useState("");
  const lines = useMemo(() => diffSchemas(published, draft), [published, draft]);
  return (
    <Modal title="Publish changes" onClose={onCancel}>
      <div className="clb-modal-body">
        <p className="muted">Staff will fill this version from now on. Submissions already made keep their old version.</p>
        <h4 className="clb-sub-head">What changed</h4>
        <ul className="clb-diff">
          {lines.map((l, i) => (
            <li key={i}>{l}</li>
          ))}
        </ul>
        <label className="clb-field">
          Note (optional)
          <input type="text" value={note} maxLength={200} placeholder="e.g. Added fryer oil check" onChange={(e) => setNote(e.target.value)} />
        </label>
        {error && (
          <ul className="error clb-error-list">
            {error.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
        )}
      </div>
      <div className="clb-modal-actions">
        <button type="button" className="btn-secondary" onClick={onCancel}>
          Cancel
        </button>
        <button type="button" onClick={() => onConfirm(note)} disabled={busy}>
          {busy ? "Publishing…" : "Publish"}
        </button>
      </div>
    </Modal>
  );
}

function SectionHeader({ section, index, count, onChange, onMove, onDelete, onDuplicate, selected, onSelect, hasError }) {
  return (
    <div
      className={`clb-section-card${selected ? " clb-selected" : ""}${hasError ? " clb-error-card" : ""}`}
      data-section-id={section.id}
      onClick={() => onSelect(section.id)}
    >
      {count > 1 && <div className="clb-section-tab">Section {index + 1} of {count}</div>}
      <input
        type="text"
        className="clb-section-title"
        aria-label={`Section ${index + 1} title`}
        placeholder={count > 1 ? "Untitled section" : "Section title (optional)"}
        value={section.title || ""}
        onChange={(e) => onChange({ ...section, title: e.target.value || null })}
      />
      <textarea
        className="clb-desc-input"
        rows={1}
        aria-label={`Section ${index + 1} description`}
        placeholder="Description (optional)"
        value={section.description || ""}
        onChange={(e) => onChange({ ...section, description: e.target.value || null })}
      />
      <div className="clb-section-actions">
        <button type="button" className="clb-icon-btn" title="Move section up" aria-label="Move section up" disabled={index === 0} onClick={() => onMove(-1)}>
          ↑
        </button>
        <button
          type="button"
          className="clb-icon-btn"
          title="Move section down"
          aria-label="Move section down"
          disabled={index === count - 1}
          onClick={() => onMove(1)}
        >
          ↓
        </button>
        <button type="button" className="clb-icon-btn" title="Duplicate section" aria-label="Duplicate section" onClick={onDuplicate}>
          <Icon name="copy" size={16} />
        </button>
        <button
          type="button"
          className="clb-icon-btn clb-icon-danger"
          title="Delete section"
          aria-label="Delete section"
          disabled={count <= 1}
          onClick={onDelete}
        >
          <Icon name="trash-2" size={16} />
        </button>
      </div>
    </div>
  );
}

function AddBar({ onAdd }) {
  return (
    <div className="clb-add-bar" role="toolbar" aria-label="Add">
      <button type="button" className="btn-secondary clb-add-btn" onClick={() => onAdd("multiple_choice")}>
        + Question
      </button>
      <button type="button" className="btn-secondary clb-add-btn" onClick={() => onAdd("title_text")}>
        + Title and description
      </button>
      <button type="button" className="btn-secondary clb-add-btn" onClick={() => onAdd("image")}>
        + Image
      </button>
      <button type="button" className="btn-secondary clb-add-btn" onClick={() => onAdd("__section__")}>
        + Section
      </button>
    </div>
  );
}

export default function ChecklistEditor({ checklistType }) {
  const [loadError, setLoadError] = useState(null);
  const [info, setInfo] = useState(null); // {definition, published, draft, versions, issueTags}
  const [draft, setDraft] = useState(null);
  const [baseline, setBaseline] = useState(null);
  const [tab, setTab] = useState("questions");
  const [mobilePane, setMobilePane] = useState("edit");
  const [selected, setSelected] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saveErrors, setSaveErrors] = useState(null);
  const [conflict, setConflict] = useState(false); // last save refused: someone else saved
  const [notice, setNotice] = useState(null);
  const [publishOpen, setPublishOpen] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [publishErrors, setPublishErrors] = useState(null);
  const [previewKey, setPreviewKey] = useState(0);
  const [showProblems, setShowProblems] = useState(false);
  const drag = useRef(null);
  const [dropHint, setDropHint] = useState(null);

  const [issueTags, setIssueTags] = useState(DEFAULT_ISSUE_TAGS);
  // Active WhatsApp groups from meta; canManage = the server has the groups
  // table (meta groups carry `builtin`), else the Manage button is hidden.
  const [waGroups, setWaGroups] = useState({ list: DEFAULT_WHATSAPP_GROUPS, canManage: false });

  // A definition detail (GET / PUT / publish / revert all return one) becomes
  // the editor's state; the draft restarts from what the server stored. On
  // the first load, unsaved edits left behind by leaving the page (browser
  // Back can't be blocked) come back if the checklist hasn't changed since.
  const applyDetail = useCallback(
    (res, { restore = false } = {}) => {
      const parsed = definitionFromDetail(res);
      const editable = toEditable(clone(parsed.editable || parsed.published || { title: "", sections: [] }));
      if (!editable.sections.length) editable.sections.push({ id: newId("s"), title: null, description: null, items: [] });
      // A saved draft may not carry the title / description (a new or
      // duplicated checklist's first draft): use the definition's.
      if (editable.title == null) editable.title = res?.title || "";
      if (editable.description == null) editable.description = res?.description || null;
      const base = JSON.stringify(editable);
      const stash = restore ? unsavedEdits.get(checklistType) : null;
      unsavedEdits.delete(checklistType);
      setInfo(parsed);
      if (stash && stash.baseline === base) {
        setDraft(stash.draft);
        setNotice("Your unsaved changes were kept. Save or publish them, or discard them.");
      } else {
        setDraft(editable);
      }
      setBaseline(base);
      setSaveErrors(null);
      setConflict(false);
    },
    [checklistType]
  );

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      applyDetail(await getChecklistDefinition(checklistType), { restore: true });
    } catch (e) {
      setLoadError(errorLines(e).join(" "));
    }
  }, [checklistType, applyDetail]);

  const applyGroups = useCallback((meta) => {
    const list = Array.isArray(meta?.whatsapp_groups) ? meta.whatsapp_groups : null;
    if (list) setWaGroups({ list, canManage: list.some((g) => typeof g.builtin === "boolean") });
  }, []);

  const reloadGroups = useCallback(() => {
    getChecklistEditorMeta()
      .then(applyGroups)
      .catch(() => {});
  }, [applyGroups]);

  useEffect(() => {
    getChecklistEditorMeta()
      .then((meta) => {
        setIssueTags(issueTagsFromMeta(meta));
        applyGroups(meta);
      })
      .catch(() => setIssueTags(DEFAULT_ISSUE_TAGS));
  }, [applyGroups]);

  useEffect(() => {
    load();
  }, [load]);

  const dirty = draft != null && baseline != null && JSON.stringify(draft) !== baseline;

  // Keep unsaved edits outside the component, so leaving the page by a way
  // that can't be stopped (browser Back / Forward) doesn't lose them.
  useEffect(() => {
    if (dirty) unsavedEdits.set(checklistType, { baseline, draft });
    else if (baseline != null) unsavedEdits.delete(checklistType);
  }, [dirty, draft, baseline, checklistType]);

  // Unsaved-changes guard: closing the tab, in-app links (HashRouter has no
  // navigation blocker, so link clicks are caught before they navigate) and
  // browser Back / Forward (popstate can't be cancelled: on "stay" the
  // history position is put back and the edits above are restored).
  useEffect(() => {
    if (!dirty) return undefined;
    const leave = () => {
      if (!window.confirm("You have unsaved changes. Leave without saving?")) return false;
      unsavedEdits.delete(checklistType);
      return true;
    };
    const onBeforeUnload = (e) => {
      e.preventDefault();
      e.returnValue = "";
    };
    const onClick = (e) => {
      const a = e.target.closest?.("a[href]");
      if (!a || a.target === "_blank") return;
      const href = a.getAttribute("href") || "";
      if (!href.startsWith("#/") && !href.startsWith("/")) return;
      if (!leave()) {
        e.preventDefault();
        e.stopPropagation();
      }
    };
    // HashRouter keeps the entry index in history.state.idx.
    const startIdx = window.history.state?.idx;
    let returning = false;
    const onPopState = (e) => {
      if (returning) {
        returning = false;
        return;
      }
      if (leave()) return;
      const newIdx = e.state?.idx;
      // An entry the router didn't create (typed / pasted URL) has no idx:
      // assume Back, the usual case.
      const delta = typeof startIdx === "number" && typeof newIdx === "number" ? startIdx - newIdx : 1;
      if (delta !== 0) {
        returning = true;
        window.history.go(delta);
      }
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    window.addEventListener("popstate", onPopState);
    document.addEventListener("click", onClick, true);
    return () => {
      window.removeEventListener("beforeunload", onBeforeUnload);
      window.removeEventListener("popstate", onPopState);
      document.removeEventListener("click", onClick, true);
    };
  }, [dirty, checklistType]);

  const problems = useMemo(() => (draft ? draftProblems(draft) : []), [draft]);
  const errorIds = useMemo(() => {
    const ids = new Set();
    if (showProblems) problems.forEach((p) => p.id && ids.add(p.id));
    if (saveErrors && draft) idsInErrors(saveErrors, draft).forEach((id) => ids.add(id));
    return ids;
  }, [problems, showProblems, saveErrors, draft]);

  const previewSchema = useMemo(() => {
    if (!draft) return null;
    return {
      ...draft,
      settings: { ...(info?.definition?.settings || {}), ...(draft.settings || {}) },
    };
  }, [draft, info]);

  function update(fn) {
    setNotice(null);
    setDraft((prev) => {
      const next = clone(prev);
      fn(next);
      return next;
    });
  }

  const takenIds = () => allIds(draft);

  // --- item ops ---
  function addAfterSelection(type) {
    const taken = allIds(draft);
    let s = draft.sections.length - 1;
    let i = (draft.sections[s].items || []).length;
    const loc = selected ? locate(draft, selected) : null;
    if (loc) {
      s = loc.s;
      i = loc.i + 1;
    } else if (selected) {
      const si = draft.sections.findIndex((x) => x.id === selected);
      if (si >= 0) {
        s = si;
        i = 0;
      }
    }
    if (type === "__section__") {
      const id = newId("s", taken);
      update((d) => {
        const moved = d.sections[s].items.splice(i);
        d.sections.splice(s + 1, 0, { id, title: null, description: null, items: moved });
      });
      setSelected(id);
      return;
    }
    const item = blankItem(type, taken);
    update((d) => {
      d.sections[s].items = d.sections[s].items || [];
      d.sections[s].items.splice(i, 0, item);
    });
    setSelected(item.id);
    requestAnimationFrame(() => {
      document.querySelector(`[data-item-id="${item.id}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
  }

  function setItem(itemId, next) {
    update((d) => {
      const loc = locate(d, itemId);
      if (loc) d.sections[loc.s].items[loc.i] = next;
    });
  }

  function deleteItem(itemId) {
    const loc = locate(draft, itemId);
    update((d) => {
      const l = locate(d, itemId);
      if (l) d.sections[l.s].items.splice(l.i, 1);
    });
    if (loc) {
      const items = draft.sections[loc.s].items;
      const neighbour = items[loc.i + 1] || items[loc.i - 1];
      setSelected(neighbour ? neighbour.id : draft.sections[loc.s].id);
    }
  }

  function duplicateItem(itemId) {
    const loc = locate(draft, itemId);
    if (!loc) return;
    const copy = cloneItem(draft.sections[loc.s].items[loc.i], allIds(draft));
    update((d) => d.sections[loc.s].items.splice(loc.i + 1, 0, copy));
    setSelected(copy.id);
  }

  function moveItem(itemId, delta) {
    update((d) => {
      const loc = locate(d, itemId);
      if (!loc) return;
      const items = d.sections[loc.s].items;
      const j = loc.i + delta;
      if (j >= 0 && j < items.length) {
        [items[loc.i], items[j]] = [items[j], items[loc.i]];
      } else if (delta < 0 && loc.s > 0) {
        const [it] = items.splice(loc.i, 1);
        d.sections[loc.s - 1].items.push(it);
      } else if (delta > 0 && loc.s < d.sections.length - 1) {
        const [it] = items.splice(loc.i, 1);
        d.sections[loc.s + 1].items.unshift(it);
      }
    });
  }

  function moveItemTo(itemId, sectionId, index = null) {
    update((d) => {
      const loc = locate(d, itemId);
      if (!loc) return;
      const [it] = d.sections[loc.s].items.splice(loc.i, 1);
      const target = d.sections.find((x) => x.id === sectionId);
      if (!target) return;
      target.items = target.items || [];
      target.items.splice(index == null ? target.items.length : Math.min(index, target.items.length), 0, it);
    });
  }

  // --- section ops ---
  function setSection(sIdx, next) {
    update((d) => {
      d.sections[sIdx] = next;
    });
  }

  function moveSection(sIdx, delta) {
    update((d) => {
      const j = sIdx + delta;
      if (j < 0 || j >= d.sections.length) return;
      [d.sections[sIdx], d.sections[j]] = [d.sections[j], d.sections[sIdx]];
    });
  }

  function deleteSection(sIdx) {
    const sec = draft.sections[sIdx];
    const n = (sec.items || []).length;
    let keepItems = false;
    if (n > 0) {
      keepItems = window.confirm(
        `Section ${sIdx + 1} has ${n} question${n === 1 ? "" : "s"}. OK = move them to the section above/below and delete the section. Cancel = keep the section.`
      );
      if (!keepItems) return;
    } else if (!window.confirm(`Delete section ${sIdx + 1}?`)) {
      return;
    }
    update((d) => {
      const [removed] = d.sections.splice(sIdx, 1);
      const into = d.sections[Math.max(0, sIdx - 1)];
      if (keepItems && into) {
        if (sIdx === 0) into.items = [...removed.items, ...(into.items || [])];
        else into.items = [...(into.items || []), ...removed.items];
      }
      // Branches pointing at the deleted section fall back to "next section".
      for (const s of d.sections) {
        if (s.next_section === removed.id) delete s.next_section;
        for (const it of s.items || []) {
          if (!it.go_to_section) continue;
          for (const [k, v] of Object.entries(it.go_to_section)) if (v === removed.id) delete it.go_to_section[k];
        }
      }
    });
    setSelected(null);
  }

  function duplicateSection(sIdx) {
    const taken = allIds(draft);
    const copy = clone(draft.sections[sIdx]);
    copy.id = newId("s", taken);
    taken.add(copy.id);
    copy.title = copy.title ? `${copy.title} (copy)` : "";
    copy.items = (copy.items || []).map((it) => cloneItem(it, taken));
    update((d) => d.sections.splice(sIdx + 1, 0, copy));
    setSelected(copy.id);
  }

  // --- drag and drop (collapsed cards) ---
  function dragPropsFor(itemId, sectionId, index) {
    return {
      draggable: selected !== itemId,
      onDragStart: (e) => {
        drag.current = { itemId };
        e.dataTransfer.effectAllowed = "move";
        try {
          e.dataTransfer.setData("text/plain", itemId);
        } catch {
          // some browsers refuse custom data; the ref carries it
        }
      },
      onDragEnd: () => {
        drag.current = null;
        setDropHint(null);
      },
      onDragOver: (e) => {
        if (!drag.current || drag.current.itemId === itemId) return;
        e.preventDefault();
        const rect = e.currentTarget.getBoundingClientRect();
        const after = e.clientY > rect.top + rect.height / 2;
        setDropHint({ itemId, after });
      },
      onDrop: (e) => {
        if (!drag.current) return;
        e.preventDefault();
        const rect = e.currentTarget.getBoundingClientRect();
        const after = e.clientY > rect.top + rect.height / 2;
        const moving = drag.current.itemId;
        drag.current = null;
        setDropHint(null);
        if (moving === itemId) return;
        update((d) => {
          const from = locate(d, moving);
          if (!from) return;
          const [it] = d.sections[from.s].items.splice(from.i, 1);
          const to = locate(d, itemId);
          const target = d.sections.find((x) => x.id === sectionId);
          const idx = to ? to.i + (after ? 1 : 0) : index;
          target.items.splice(idx, 0, it);
        });
      },
    };
  }

  // --- save / publish ---
  // The draft this editor started from: the server refuses a save or
  // publish with 409 when someone else changed it since (two admins).
  const draftBase = () => info?.definition?.draft_updated_at ?? null;

  async function saveDraft({ quiet = false } = {}) {
    setSaving(true);
    setSaveErrors(null);
    setConflict(false);
    setNotice(null);
    try {
      applyDetail(await saveChecklistDraft(checklistType, draftPayload(draft, { base: draftBase() })));
      if (!quiet) setNotice("Draft saved. Staff still see the published version until you publish.");
      return true;
    } catch (e) {
      const lines = errorLines(e);
      setSaveErrors(lines);
      setConflict(e?.status === 409);
      setShowProblems(true);
      return false;
    } finally {
      setSaving(false);
    }
  }

  function openPublish() {
    setShowProblems(true);
    setPublishErrors(null);
    if (problems.length) {
      setSaveErrors(null);
      document.querySelector(".clb-problems")?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      return;
    }
    setPublishOpen(true);
  }

  async function publish(note) {
    setPublishing(true);
    setPublishErrors(null);
    try {
      const res = await publishChecklistDefinition(checklistType, {
        ...draftPayload(draft, { base: draftBase() }),
        note: note || null,
        base_version: info.definition.current_version ?? null,
      });
      const before = info.definition.current_version ?? null;
      setPublishOpen(false);
      applyDetail(res);
      setNotice(
        res.current_version === before
          ? `Published the title / description. The questions didn't change, so it's still version ${before}.`
          : `Published version ${res.current_version}. Staff fill it from now on.`
      );
    } catch (e) {
      if (e?.status === 409) {
        // Someone else published / saved meanwhile: offer the reload.
        setPublishOpen(false);
        setSaveErrors(errorLines(e));
        setConflict(true);
      } else {
        setPublishErrors(errorLines(e));
      }
    } finally {
      setPublishing(false);
    }
  }

  async function discard() {
    if (dirty) {
      if (!window.confirm("Throw away your unsaved changes?")) return;
      setDraft(JSON.parse(baseline));
      setSaveErrors(null);
      return;
    }
    if (!window.confirm("Delete the saved draft and go back to the published version?")) return;
    try {
      applyDetail(await discardChecklistDraft(checklistType));
      setNotice("Draft discarded.");
    } catch (e) {
      setSaveErrors(errorLines(e));
    }
  }

  if (loadError) {
    return (
      <section className="card">
        <Link to="/checklists/admin">← All checklists</Link>
        <p className="error">{loadError}</p>
      </section>
    );
  }
  if (!draft || !info) return <p className="muted">Loading…</p>;

  const def = info.definition;
  const publishedVersion = def.current_version ?? null;
  const hasServerDraft = Boolean(def.has_draft);
  const neverPublished = publishedVersion == null;
  let status;
  if (dirty) status = { cls: "pill-warn", text: "Unsaved changes" };
  else if (neverPublished) status = { cls: "pill-warn", text: "Draft, not published" };
  else if (hasServerDraft) status = { cls: "pill-info", text: `Draft saved · live: v${publishedVersion}` };
  else status = { cls: "pill-success", text: `Published v${publishedVersion}` };

  const sections = draft.sections;

  return (
    <div className={`clb-editor clb-pane-${mobilePane}`}>
      <div className="clb-topbar">
        <div className="clb-topbar-left">
          <Link to="/checklists/admin" className="clb-back">
            ← All checklists
          </Link>
          <div className="clb-topbar-title">
            <strong>{draft.title || "Untitled checklist"}</strong>
            <span className={`pill ${status.cls}`}>{status.text}</span>
            {def.active === false && <span className="pill pill-neutral">Inactive</span>}
          </div>
        </div>
        <div className="clb-topbar-actions">
          {(dirty || (hasServerDraft && !neverPublished)) && (
            <button type="button" className="link-button link-danger" onClick={discard}>
              {dirty ? "Discard changes" : "Discard draft"}
            </button>
          )}
          <button type="button" className="btn-secondary" onClick={() => saveDraft()} disabled={saving || !dirty}>
            {saving ? "Saving…" : dirty ? "Save draft" : "Saved"}
          </button>
          <button type="button" onClick={openPublish} disabled={saving || (!dirty && !hasServerDraft && !neverPublished)}>
            Publish
          </button>
        </div>
      </div>
      <div className="clb-tabs" role="tablist">
        {[
          ["questions", "Questions"],
          ["settings", "Settings"],
          ["public", "Public link"],
          ["versions", `Versions${info.versions.length ? ` (${info.versions.length})` : ""}`],
        ].map(([k, l]) => (
          <button key={k} type="button" role="tab" aria-selected={tab === k} className={`clb-tab${tab === k ? " clb-tab-on" : ""}`} onClick={() => setTab(k)}>
            {l}
          </button>
        ))}
        {tab === "questions" && (
          <div className="clb-pane-switch" role="group" aria-label="Show">
            <button type="button" className={`clb-tab${mobilePane === "edit" ? " clb-tab-on" : ""}`} onClick={() => setMobilePane("edit")}>
              Edit
            </button>
            <button type="button" className={`clb-tab${mobilePane === "preview" ? " clb-tab-on" : ""}`} onClick={() => setMobilePane("preview")}>
              Preview
            </button>
          </div>
        )}
      </div>

      {notice && <p className="success clb-notice">{notice}</p>}
      {saveErrors && (
        <div className="clb-problems clb-problems-server" role="alert">
          <strong>{conflict ? "Someone else changed this checklist:" : "The server didn't accept this checklist:"}</strong>
          <ul>
            {saveErrors.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
          {conflict && (
            <button
              type="button"
              className="btn-secondary"
              onClick={() => {
                if (dirty && !window.confirm("Reloading throws away your unsaved changes here. Reload?")) return;
                setConflict(false);
                setNotice(null);
                load();
              }}
            >
              Reload their version
            </button>
          )}
        </div>
      )}

      {tab === "settings" && (
        <SettingsPanel
          definition={def}
          settings={def.settings || {}}
          groups={waGroups.list}
          canManageGroups={waGroups.canManage}
          onGroupsChanged={reloadGroups}
          onSaved={(res) => {
            // Settings don't touch the questions; keep any unsaved edits.
            setInfo(definitionFromDetail(res));
          }}
        />
      )}
      {tab === "public" && <PublicLinkPanel checklistType={checklistType} active={def.active !== false} />}
      {tab === "versions" && (
        <VersionsPanel
          checklistType={checklistType}
          versions={info.versions}
          currentVersion={publishedVersion}
          dirty={dirty}
          onReverted={(res) => {
            applyDetail(res);
            setNotice(`Reverted: version ${res.current_version} (a copy of the old one) is live now.`);
          }}
        />
      )}

      {tab === "questions" && (
        <div className="clb-workspace">
          <div className="clb-canvas" onClick={(e) => e.target === e.currentTarget && setSelected(null)}>
            {showProblems && problems.length > 0 && (
              <div className="clb-problems" role="alert">
                <strong>Fix these before publishing:</strong>
                <ul>
                  {problems.map((p, i) => (
                    <li key={i}>
                      {p.id ? (
                        <button
                          type="button"
                          className="link-button"
                          onClick={() => {
                            setSelected(p.id);
                            requestAnimationFrame(() =>
                              document
                                .querySelector(`[data-item-id="${p.id}"], [data-section-id="${p.id}"]`)
                                ?.scrollIntoView({ block: "center", behavior: "smooth" })
                            );
                          }}
                        >
                          {p.text}
                        </button>
                      ) : (
                        p.text
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <div className={`clb-form-head${selected === "__head__" ? " clb-selected" : ""}`} onClick={() => setSelected("__head__")}>
              <input
                type="text"
                className="clb-form-title"
                aria-label="Checklist title"
                placeholder="Checklist title"
                value={draft.title || ""}
                onChange={(e) => update((d) => (d.title = e.target.value))}
              />
              <textarea
                className="clb-desc-input"
                rows={2}
                aria-label="Checklist description"
                placeholder="Description shown to staff at the top"
                value={draft.description || ""}
                onChange={(e) => update((d) => (d.description = e.target.value))}
              />
            </div>

            {sections.map((section, sIdx) => (
              <div key={section.id} className="clb-section">
                <SectionHeader
                  section={section}
                  index={sIdx}
                  count={sections.length}
                  selected={selected === section.id}
                  hasError={errorIds.has(section.id)}
                  onSelect={setSelected}
                  onChange={(next) => setSection(sIdx, next)}
                  onMove={(delta) => moveSection(sIdx, delta)}
                  onDelete={() => deleteSection(sIdx)}
                  onDuplicate={() => duplicateSection(sIdx)}
                />
                {selected === section.id && <AddBar onAdd={addAfterSelection} />}
                {(section.items || []).length === 0 && (
                  <div
                    className="clb-empty-section"
                    onDragOver={(e) => drag.current && e.preventDefault()}
                    onDrop={(e) => {
                      if (!drag.current) return;
                      e.preventDefault();
                      const moving = drag.current.itemId;
                      drag.current = null;
                      moveItemTo(moving, section.id, 0);
                    }}
                  >
                    No questions in this section yet.{" "}
                    <button
                      type="button"
                      className="link-button"
                      onClick={() => {
                        setSelected(section.id);
                        const item = blankItem("multiple_choice", allIds(draft));
                        update((d) => d.sections[sIdx].items.push(item));
                        setSelected(item.id);
                      }}
                    >
                      Add a question
                    </button>
                  </div>
                )}
                {(section.items || []).map((item, iIdx) => (
                  <div
                    key={item.id}
                    className={
                      dropHint?.itemId === item.id ? (dropHint.after ? "clb-drop-after" : "clb-drop-before") : undefined
                    }
                  >
                    <ItemEditor
                      item={item}
                      index={iIdx}
                      count={section.items.length}
                      sections={sections}
                      sectionIndex={sIdx}
                      issueTags={issueTags}
                      selected={selected === item.id}
                      hasError={errorIds.has(item.id)}
                      onSelect={setSelected}
                      onChange={(next) => setItem(item.id, next)}
                      onChangeType={(type) => setItem(item.id, changeType(item, type, allIds(draft)))}
                      onDelete={() => deleteItem(item.id)}
                      onDuplicate={() => duplicateItem(item.id)}
                      onMove={(delta) => moveItem(item.id, delta)}
                      onMoveToSection={(sid) => moveItemTo(item.id, sid)}
                      takenIds={takenIds}
                      dragProps={dragPropsFor(item.id, section.id, iIdx)}
                    />
                    {selected === item.id && <AddBar onAdd={addAfterSelection} />}
                  </div>
                ))}
                {sections.length > 1 && (
                  <div className="clb-after-section">
                    <label>
                      After section {sIdx + 1}
                      <select
                        value={section.next_section || ""}
                        onChange={(e) =>
                          setSection(sIdx, (() => {
                            const next = { ...section };
                            if (e.target.value) next.next_section = e.target.value;
                            else delete next.next_section;
                            return next;
                          })())
                        }
                      >
                        <option value="">{sIdx === sections.length - 1 ? "Submit form" : "Continue to next section"}</option>
                        {sections.map((s, i) =>
                          i <= sIdx + 1 ? null : (
                            <option key={s.id} value={s.id}>
                              Go to section {i + 1}
                              {s.title ? ` (${s.title})` : ""}
                            </option>
                          )
                        )}
                        {sIdx < sections.length - 1 && <option value={SUBMIT_TARGET}>Submit form</option>}
                      </select>
                    </label>
                  </div>
                )}
              </div>
            ))}
            {!selected && <AddBar onAdd={addAfterSelection} />}
          </div>
          <aside className="clb-preview" aria-label="Live preview">
            <div className="clb-preview-scroll card">
              <PreviewPane key={previewKey} schema={previewSchema} onReset={() => setPreviewKey((k) => k + 1)} />
            </div>
          </aside>
        </div>
      )}

      {publishOpen && (
        <PublishDialog
          published={info.published}
          draft={draftPayload(draft)}
          busy={publishing}
          error={publishErrors}
          onCancel={() => setPublishOpen(false)}
          onConfirm={publish}
        />
      )}
    </div>
  );
}
