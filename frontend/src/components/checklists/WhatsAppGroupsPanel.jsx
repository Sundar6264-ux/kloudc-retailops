import { useCallback, useEffect, useState } from "react";
import {
  addWhatsAppGroup,
  deleteWhatsAppGroup,
  listAvailableWhatsAppGroups,
  listWhatsAppGroups,
  updateWhatsAppGroup,
} from "../../api";
import { useAuth } from "../../AuthContext.jsx";
import { errorLines } from "./editorModel.js";

// "Manage WhatsApp groups" (admin): the groups a checklist's summary can be
// posted to (docs/CHECKLIST_BUILDER_APP_AND_GROUPS_CONTRACT.md, section 1).
// Opened from the builder list and from a checklist's Settings tab. Lists
// every group with the checklists that use it; add one from the groups the
// store's WhatsApp number is in; rename; turn off / on; delete when unused.
// Choosing the groups for flyers is admin-only (the server refuses it for a
// "Build checklists" user): the "Use for flyers" box is read-only for them,
// and a flyer group can't be turned off or deleted.

const FLYER_GROUPS_ADMIN_NOTE = "Only admins choose flyer groups";

function GroupRow({ group, busy, onRun, canChooseFlyers }) {
  const [renaming, setRenaming] = useState(false);
  const [label, setLabel] = useState(group.label);
  const usedBy = group.used_by || [];

  function saveName(e) {
    e.preventDefault();
    const next = label.trim();
    if (!next || next === group.label) {
      setRenaming(false);
      return;
    }
    onRun(`rename-${group.key}`, () => updateWhatsAppGroup(group.key, { label: next })).then((ok) => ok && setRenaming(false));
  }

  function toggle() {
    if (
      group.active &&
      usedBy.length &&
      !window.confirm(
        `Turn off "${group.label}"? ${usedBy.join(", ")} will stop posting to WhatsApp until you choose another group for ${
          usedBy.length === 1 ? "it" : "them"
        }.`
      )
    )
      return;
    onRun(`active-${group.key}`, () => updateWhatsAppGroup(group.key, { active: !group.active }));
  }

  function remove() {
    if (!window.confirm(`Delete "${group.label}"? Checklists can't post to it any more.`)) return;
    onRun(`delete-${group.key}`, () => deleteWhatsAppGroup(group.key));
  }

  // "Use for flyers" (docs/MARKETING_FLYERS_CONTRACT.md, section 5): only
  // these groups are offered on the Flyers page. Shown when the server
  // sends the field.
  const hasMarketing = typeof group.marketing === "boolean";
  // Shown at once while the change saves; dropped when the list catches up
  // (or the save fails, which puts the box back).
  const [marketingShown, setMarketingShown] = useState(null);
  useEffect(() => {
    if (marketingShown !== null && group.marketing === marketingShown) setMarketingShown(null);
  }, [group.marketing, marketingShown]);
  const marketing = marketingShown ?? group.marketing;
  function toggleMarketing(on) {
    if (
      on &&
      group.builtin &&
      !window.confirm(`"${group.label}" is a staff group. Let marketing flyers be sent to it?`)
    )
      return;
    setMarketingShown(on);
    onRun(`marketing-${group.key}`, () => updateWhatsAppGroup(group.key, { marketing: on })).then(
      (ok) => !ok && setMarketingShown(null),
    );
  }
  const flyersSkip = marketing && (!group.active || group.configured === false);
  // A non-admin can't change who gets flyers: not the box, and not turning
  // a flyer group off / on or deleting it.
  const flyerLocked = !canChooseFlyers && group.marketing === true;

  return (
    <li className={`wag-row${group.active ? "" : " wag-row-off"}`}>
      <div className="wag-row-main">
        {renaming ? (
          <form className="wag-rename" onSubmit={saveName}>
            <input
              type="text"
              aria-label="Group name"
              value={label}
              maxLength={60}
              autoFocus
              onChange={(e) => setLabel(e.target.value)}
            />
            <button type="submit" disabled={busy != null || !label.trim()}>
              Save
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => {
                setLabel(group.label);
                setRenaming(false);
              }}
            >
              Cancel
            </button>
          </form>
        ) : (
          <div className="wag-name">
            <strong>{group.label}</strong>
            {group.builtin && <span className="pill pill-neutral clb-pill-sm">Built-in</span>}
            {!group.active && <span className="pill pill-warn clb-pill-sm">Off</span>}
            {group.active && group.configured === false && (
              <span className="pill pill-warn clb-pill-sm" title="No chat id is set for this group, so nothing is posted">
                Not set up
              </span>
            )}
          </div>
        )}
        <div className="muted wag-sub">
          {group.chat_id_masked
            ? `Chat ${group.chat_id_masked}`
            : group.builtin
              ? "Chat id not set in the server settings"
              : "No chat id"}
          {" · "}
          {usedBy.length ? `Used by ${usedBy.join(", ")}` : "Not used by any checklist"}
        </div>
        {hasMarketing && !renaming && (
          <div className="wag-flyers">
            <label className="wag-flyers-toggle">
              <input
                type="checkbox"
                checked={marketing}
                disabled={busy != null || !canChooseFlyers}
                title={canChooseFlyers ? undefined : FLYER_GROUPS_ADMIN_NOTE}
                onChange={(e) => toggleMarketing(e.target.checked)}
              />
              <span>{busy === `marketing-${group.key}` ? "Saving…" : "Use for flyers"}</span>
            </label>
            {flyersSkip && (
              <span className="muted wag-sub wag-flyers-note">
                Flyers skip it until it's {!group.active ? "turned on" : "set up with a chat id"}.
              </span>
            )}
          </div>
        )}
      </div>
      {!renaming && (
        <div className="wag-actions">
          <button type="button" className="link-button" disabled={busy != null} onClick={() => setRenaming(true)}>
            Rename
          </button>
          <button
            type="button"
            className={`link-button${group.active ? " link-danger" : ""}`}
            disabled={busy != null || flyerLocked}
            title={flyerLocked ? `${FLYER_GROUPS_ADMIN_NOTE}. This group gets flyers.` : undefined}
            onClick={toggle}
          >
            {busy === `active-${group.key}` ? "Saving…" : group.active ? "Turn off" : "Turn on"}
          </button>
          {!group.builtin && (
            <button
              type="button"
              className="link-button link-danger"
              disabled={busy != null || usedBy.length > 0 || flyerLocked}
              title={
                flyerLocked
                  ? `${FLYER_GROUPS_ADMIN_NOTE}. This group gets flyers.`
                  : usedBy.length
                    ? "Choose another group for the checklists that use it first"
                    : undefined
              }
              onClick={remove}
            >
              {busy === `delete-${group.key}` ? "Deleting…" : "Delete"}
            </button>
          )}
        </div>
      )}
    </li>
  );
}

function AvailableList({ busy, onRun }) {
  const [state, setState] = useState(null); // {items, error} | null while loading
  const [adding, setAdding] = useState(null); // chat_id being named
  const [label, setLabel] = useState("");

  const load = useCallback((refresh) => {
    setState(null);
    listAvailableWhatsAppGroups(refresh)
      .then((res) => setState({ items: res.items || [], error: res.error || null }))
      .catch((e) => setState({ items: [], error: errorLines(e).join(" ") }));
  }, []);

  useEffect(() => {
    load(false);
  }, [load]);

  async function add(e, chatId) {
    e.preventDefault();
    const ok = await onRun(`add-${chatId}`, () => addWhatsAppGroup(chatId, label.trim()));
    if (ok) {
      setAdding(null);
      load(false);
    }
  }

  return (
    <div className="wag-available">
      <div className="wag-available-head">
        <h4>Available on WhatsApp</h4>
        <button type="button" className="link-button" onClick={() => load(true)} disabled={state == null}>
          Refresh
        </button>
      </div>
      <p className="muted wag-sub">The groups the store's WhatsApp number is in. Add one to let checklists post to it.</p>
      {state == null && <p className="muted">Loading…</p>}
      {state?.error && <p className="error">{state.error}</p>}
      {state && !state.error && state.items.length === 0 && <p className="muted">No groups found.</p>}
      {state && state.items.length > 0 && (
        <ul className="wag-list">
          {state.items.map((g) => (
            <li key={g.chat_id} className="wag-row">
              <div className="wag-row-main">
                <strong>{g.name || "(no name)"}</strong>
                {g.already_added_key && <div className="muted wag-sub">Already added</div>}
              </div>
              {!g.already_added_key && adding !== g.chat_id && (
                <div className="wag-actions">
                  <button
                    type="button"
                    className="link-button"
                    disabled={busy != null}
                    onClick={() => {
                      setAdding(g.chat_id);
                      setLabel(g.name || "");
                    }}
                  >
                    Add…
                  </button>
                </div>
              )}
              {adding === g.chat_id && (
                <form className="wag-rename wag-add" onSubmit={(e) => add(e, g.chat_id)}>
                  <input
                    type="text"
                    aria-label="Name shown in the checklist settings"
                    placeholder="Name shown in the checklist settings"
                    value={label}
                    maxLength={60}
                    autoFocus
                    onChange={(e) => setLabel(e.target.value)}
                  />
                  <button type="submit" disabled={busy != null || !label.trim()}>
                    {busy === `add-${g.chat_id}` ? "Adding…" : "Add"}
                  </button>
                  <button type="button" className="btn-secondary" onClick={() => setAdding(null)}>
                    Cancel
                  </button>
                </form>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function WhatsAppGroupsPanel({ onClose, onChanged }) {
  const { user } = useAuth();
  const canChooseFlyers = Boolean(user?.is_admin);
  const [groups, setGroups] = useState(null);
  const [error, setError] = useState(null);
  const [unsupported, setUnsupported] = useState(false);
  const [actionError, setActionError] = useState(null);
  const [busy, setBusy] = useState(null);

  const load = useCallback(() => {
    listWhatsAppGroups()
      .then((res) => setGroups(res.items || []))
      .catch((e) => {
        if (e?.status === 404 || e?.status === 405) setUnsupported(true);
        else setError(errorLines(e).join(" "));
      });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Runs one change; true when it worked.
  async function run(key, fn) {
    setBusy(key);
    setActionError(null);
    try {
      await fn();
      load();
      onChanged?.();
      return true;
    } catch (e) {
      setActionError(errorLines(e).join(" "));
      return false;
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="clb-modal-backdrop" onClick={onClose}>
      <div
        className="clb-modal clb-modal-wide"
        role="dialog"
        aria-modal="true"
        aria-label="WhatsApp groups"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="clb-modal-head">
          <h3>WhatsApp groups</h3>
          <button type="button" className="clb-icon-btn" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="clb-modal-body">
          <p className="muted wag-sub">
            Each checklist can post its summary to one of these groups (Settings tab of the checklist).
          </p>
          {groups?.some((g) => typeof g.marketing === "boolean") && (
            <p className="muted wag-sub wag-flyers-hint">
              Flyers (Team → Flyers) only go to groups with <strong>Use for flyers</strong> ticked, and everyone in
              those groups sees them. Tick your customer groups; leave staff groups off.
            </p>
          )}
          {!canChooseFlyers && groups?.some((g) => typeof g.marketing === "boolean") && (
            <p className="muted wag-sub wag-flyers-admin-note">{FLYER_GROUPS_ADMIN_NOTE}.</p>
          )}
          {unsupported && <p className="muted">This server doesn't support managing WhatsApp groups yet.</p>}
          {error && <p className="error">{error}</p>}
          {actionError && (
            <p className="error" role="alert">
              {actionError}
            </p>
          )}
          {!groups && !error && !unsupported && <p className="muted">Loading…</p>}
          {groups && (
            <ul className="wag-list">
              {groups.map((g) => (
                <GroupRow
                  key={`${g.key}-${g.label}`}
                  group={g}
                  busy={busy}
                  onRun={run}
                  canChooseFlyers={canChooseFlyers}
                />
              ))}
            </ul>
          )}
          {groups && <AvailableList busy={busy} onRun={run} />}
        </div>
        <div className="clb-modal-actions">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
