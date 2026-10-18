import { useEffect, useState } from "react";
import { useAuth } from "../AuthContext.jsx";
import {
  getChatSettings,
  getNotificationDefaults,
  getNotificationPrefs,
  getNotificationVolume,
  getRecentNotifications,
  resetNotificationDefaults,
  resetNotificationPrefs,
  setHidePreviews,
  setNotificationDefault,
  setNotificationPref,
} from "../api";
import { formatRestaurantTime } from "../format.js";
import {
  ROLE_LABELS,
  applyLocal,
  changedCount,
  choiceOptions,
  choiceToEnabled,
  confirmOffText,
  defaultNeedsConfirm,
  groupDefaults,
  groupItems,
  hasOwnChoices,
  isChanged,
  needsConfirm,
  overrideCount,
  recommendedWord,
  storeChoice,
} from "../components/notifications/notificationsModel.js";
import "./NotificationSettingsPage.css";

// One place for everyone's phone alerts, the same switches as the app's
// More > Notifications (both read and write /api/notifications/prefs, so a
// change on one shows on the other). Admins also get the store defaults per
// alert and role at the bottom. The App Review demo login can flip its own
// switches but isn't offered "Reset to recommended" (the server refuses it).
export default function NotificationSettingsPage() {
  const { user, hasFeature } = useAuth();
  const [prefs, setPrefs] = useState(null);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(() => new Set());
  const [resetting, setResetting] = useState(false);

  useEffect(() => {
    getNotificationPrefs()
      .then(setPrefs)
      .catch((err) => setError(err.message));
  }, []);

  async function toggle(item) {
    const next = !item.enabled;
    if (needsConfirm(item, next) && !window.confirm(confirmOffText(item))) return;
    const before = prefs;
    setError(null);
    setPrefs((p) => applyLocal(p, item.key, next));
    setSaving((s) => new Set(s).add(item.key));
    try {
      setPrefs(await setNotificationPref(item.key, next));
    } catch (err) {
      setPrefs(before);
      setError(err.message);
    } finally {
      setSaving((s) => {
        const n = new Set(s);
        n.delete(item.key);
        return n;
      });
    }
  }

  async function reset() {
    if (!window.confirm("Put every alert back to the recommended setting? Your own choices are forgotten.")) return;
    setResetting(true);
    setError(null);
    try {
      setPrefs(await resetNotificationPrefs());
    } catch (err) {
      setError(err.message);
    } finally {
      setResetting(false);
    }
  }

  const groups = groupItems(prefs);
  const changed = changedCount(prefs?.items);
  const hasChat = hasFeature("team_chat");

  return (
    <>
      <div className="page-header">
        <h1>Notifications</h1>
        <p className="muted">
          Which alerts reach your phone in the Spice Town app. Changes save straight away and show in the app too.
        </p>
      </div>

      {error && <p className="error">{error}</p>}
      {!prefs && !error && <p className="muted">Loading…</p>}

      {prefs && (
        <section className="card ns-summary">
          <p style={{ margin: 0 }}>
            {changed === 0
              ? "You're on the recommended settings."
              : `${changed} alert${changed === 1 ? "" : "s"} switched differently from the recommended settings.`}
          </p>
          {hasOwnChoices(prefs.items) && !user?.demo && (
            <button type="button" className="btn-secondary ns-reset" onClick={reset} disabled={resetting}>
              {resetting ? "Resetting…" : "Reset to recommended"}
            </button>
          )}
        </section>
      )}

      {groups.map((g) => (
        <section className="card" key={g.key}>
          <h2>{g.label}</h2>
          {g.description && <p className="muted ns-group-desc">{g.description}</p>}
          <ul className="ns-list">
            {g.items.map((item) => (
              <li className="ns-row" key={item.key}>
                <div className="ns-row-text">
                  <span className="ns-row-label">{item.label}</span>
                  {item.description && <span className="muted ns-row-desc">{item.description}</span>}
                  {isChanged(item) && (
                    <span className="ns-row-changed">Recommended: {recommendedWord(item.default).toLowerCase()}</span>
                  )}
                </div>
                <Switch
                  on={!!item.enabled}
                  label={item.label}
                  disabled={saving.has(item.key)}
                  onClick={() => toggle(item)}
                />
              </li>
            ))}
            {g.key === "team" && hasChat && <HidePreviewsRow />}
          </ul>
        </section>
      ))}

      {prefs && <RecentPushes />}
      {user?.is_admin && <StoreDefaults />}
    </>
  );
}

function Switch({ on, label, disabled, onClick }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      className={`ns-switch${on ? " is-on" : ""}`}
      disabled={disabled}
      onClick={onClick}
    >
      <span className="ns-switch-knob" aria-hidden="true" />
    </button>
  );
}

// Chat's "Hide message text in notifications" (also in Chat settings).
function HidePreviewsRow() {
  const [hide, setHide] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    getChatSettings()
      .then((s) => setHide(!!s.hide_previews))
      .catch((err) => setError(err.message));
  }, []);

  async function toggle() {
    const next = !hide;
    setHide(next);
    setError(null);
    try {
      const s = await setHidePreviews(next);
      setHide(!!s.hide_previews);
    } catch (err) {
      setHide(!next);
      setError(err.message);
    }
  }

  return (
    <li className="ns-row">
      <div className="ns-row-text">
        <span className="ns-row-label">Hide message text</span>
        <span className="muted ns-row-desc">Chat notifications say "New message from …" instead of the message.</span>
        {error && <span className="error ns-row-desc">{error}</span>}
      </div>
      <Switch on={!!hide} label="Hide message text" disabled={hide === null} onClick={toggle} />
    </li>
  );
}

// Loaded only when opened.
function RecentPushes() {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState(null);
  const [error, setError] = useState(null);

  function load(e) {
    setOpen(e.currentTarget.open);
    if (!e.currentTarget.open || items) return;
    getRecentNotifications(30)
      .then(setItems)
      .catch((err) => setError(err.message));
  }

  return (
    <details className="card ns-details" onToggle={load}>
      <summary>Your recent notifications</summary>
      {error && <p className="error">{error}</p>}
      {open && !items && !error && <p className="muted">Loading…</p>}
      {items && items.length === 0 && <p className="muted">None in the last 180 days.</p>}
      {items && items.length > 0 && (
        <ul className="ns-recent">
          {items.map((r, i) => (
            <li key={i}>
              <span className="muted ns-recent-when">{r.sent_at ? formatRestaurantTime(r.sent_at) : ""}</span>
              <span>{r.title || r.label}</span>
              {r.title && <span className="muted ns-recent-kind">{r.label}</span>}
            </li>
          ))}
        </ul>
      )}
    </details>
  );
}

// Admins: what staff and admins get when they haven't chosen themselves.
// Loaded only when opened. Each change is recorded on the server.
function StoreDefaults() {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  function load(e) {
    setOpen(e.currentTarget.open);
    if (!e.currentTarget.open || state) return;
    getNotificationDefaults()
      .then(setState)
      .catch((err) => setError(err.message));
  }

  async function change(item, role, choice) {
    const who = ROLE_LABELS[role] || role;
    if (defaultNeedsConfirm(item, role, choice) && !window.confirm(`Turn "${item.label}" off for ${who.toLowerCase()} by default?`)) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setState(await setNotificationDefault(item.key, role, choiceToEnabled(choice)));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function resetAll() {
    if (!window.confirm("Put every store default back to recommended? People's own choices stay.")) return;
    setBusy(true);
    setError(null);
    try {
      setState(await resetNotificationDefaults());
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const overrides = overrideCount(state);

  return (
    <details className="card ns-details" onToggle={load}>
      <summary>Store defaults (admins)</summary>
      <p className="muted ns-group-desc">
        What staff and admins get when they haven't chosen for themselves. A person's own choice always wins.
      </p>
      {error && <p className="error">{error}</p>}
      {open && !state && !error && <p className="muted">Loading…</p>}
      {state && (
        <>
          <div className="ns-summary-row">
            <p style={{ margin: 0 }}>
              {overrides === 0 ? "All recommended." : `${overrides} changed from recommended.`}
              {state.changed_by && (
                <span className="muted">
                  {" "}
                  Last change by {state.changed_by}
                  {state.changed_at ? `, ${formatRestaurantTime(state.changed_at)}` : ""}.
                </span>
              )}
            </p>
            {overrides > 0 && (
              <button type="button" className="btn-secondary ns-reset" onClick={resetAll} disabled={busy}>
                Reset store defaults
              </button>
            )}
          </div>
          {groupDefaults(state).map((g) => (
            <div className="ns-defaults-group" key={g.key}>
              <h3>{g.label}</h3>
              <ul className="ns-list">
                {g.items.map((item) => (
                  <li className="ns-row ns-default-row" key={item.key}>
                    <div className="ns-row-text">
                      <span className="ns-row-label">{item.label}</span>
                      {item.admin_only && <span className="muted ns-row-desc">Admins only</span>}
                    </div>
                    <div className="ns-role-selects">
                      {(item.roles || []).map((role) => (
                        <label key={role} className="ns-role-select">
                          <span className="muted">{ROLE_LABELS[role] || role}</span>
                          <select
                            value={storeChoice(item, role)}
                            disabled={busy}
                            onChange={(e) => change(item, role, e.target.value)}
                          >
                            {choiceOptions(item, role).map((o) => (
                              <option key={o.value} value={o.value}>
                                {o.label}
                              </option>
                            ))}
                          </select>
                        </label>
                      ))}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          ))}
          {(state.history || []).length > 0 && (
            <details className="ns-history">
              <summary>Changes</summary>
              <ul className="ns-recent">
                {state.history.map((h) => (
                  <li key={h.id}>
                    <span className="muted ns-recent-when">{h.changed_at ? formatRestaurantTime(h.changed_at) : ""}</span>
                    <span>{h.note}</span>
                    <span className="muted ns-recent-kind">{h.changed_by}</span>
                  </li>
                ))}
              </ul>
            </details>
          )}
          <PushVolume />
        </>
      )}
    </details>
  );
}

function PushVolume() {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  function load(e) {
    setOpen(e.currentTarget.open);
    if (!e.currentTarget.open || data) return;
    getNotificationVolume(14)
      .then(setData)
      .catch((err) => setError(err.message));
  }

  return (
    <details className="ns-history" onToggle={load}>
      <summary>Pushes per person (last 14 days)</summary>
      {error && <p className="error">{error}</p>}
      {open && !data && !error && <p className="muted">Loading…</p>}
      {data && data.users.length === 0 && <p className="muted">No pushes in the last {data.days} days.</p>}
      {data && data.users.length > 0 && (
        <ul className="ns-recent">
          {data.users.map((u) => (
            <li key={u.user_id}>
              <span>{u.username || `User ${u.user_id}`}</span>
              <span className="muted ns-recent-kind">
                {u.total} in {data.days} days ({u.per_day} a day)
              </span>
            </li>
          ))}
        </ul>
      )}
    </details>
  );
}
