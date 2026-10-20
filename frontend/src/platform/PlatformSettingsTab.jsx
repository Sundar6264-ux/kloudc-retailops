// Console > Platform settings (website ready A3): the editable platform
// settings (I5), saved in the console and audited; a change works at once.
// Security switches stay on the server. Below: the System section (track C's
// <SystemStatusPanel />).
import { useEffect, useMemo, useState } from "react";
import { getPlatformSettings, resetPlatformSetting, savePlatformSettings } from "../clients/console.js";
import Icon from "../ui/icons.jsx";
import { Feedback } from "./ConsoleBits.jsx";
import { PLAN_LABELS, SOURCE_LABELS, settingError, settingsChanges } from "./consoleModel.js";
import SystemStatusPanel from "./panels/SystemStatusPanel.jsx";
import { platformErrorMessage } from "./platformModel.js";

export default function PlatformSettingsTab() {
  const [settings, setSettings] = useState(null);
  const [draft, setDraft] = useState({});
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState({});

  async function load() {
    try {
      const r = await getPlatformSettings();
      setSettings(r.settings || []);
      setDraft({});
    } catch (err) {
      setSettings([]);
      setMessage({ tone: "bad", text: platformErrorMessage(err) });
    }
  }
  useEffect(() => {
    load();
  }, []);

  const valueOf = (s) => (s.key in draft ? draft[s.key] : s.value);
  const errors = useMemo(() => {
    const out = {};
    for (const s of settings || []) {
      if (!(s.key in draft)) continue;
      const e = settingError(s, draft[s.key]);
      if (e) out[s.key] = e;
    }
    return out;
  }, [settings, draft]);
  const changes = settingsChanges(settings, draft);
  const dirty = Object.keys(changes).length > 0;

  async function save(e) {
    e.preventDefault();
    if (Object.keys(errors).length) return;
    setBusy(true);
    setMessage(null);
    setServerError({});
    try {
      const r = await savePlatformSettings(changes);
      setSettings(r.settings || []);
      setDraft({});
      const n = (r.changed || []).length;
      setMessage({ tone: "ok", text: n ? `Saved ${n} change${n > 1 ? "s" : ""}. It works now.` : "Nothing changed." });
    } catch (err) {
      const field = err?.body?.field;
      if (field) setServerError({ [field]: platformErrorMessage(err) });
      setMessage({ tone: "bad", text: platformErrorMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  async function reset(key) {
    setBusy(true);
    try {
      const r = await resetPlatformSetting(key);
      setSettings(r.settings || []);
      setDraft((d) => {
        const n = { ...d };
        delete n[key];
        return n;
      });
      setMessage({ tone: "ok", text: "Back to the server's own value." });
    } catch (err) {
      setMessage({ tone: "bad", text: platformErrorMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="pf-settings" data-testid="platform-settings-tab">
      <Feedback message={message} />
      {settings === null ? (
        <p className="muted">Loading…</p>
      ) : (
        <form className="pf-settings-form" onSubmit={save} noValidate aria-label="Platform settings">
          {settings.map((s) => {
            const id = `ps-${s.key}`;
            const err = errors[s.key] || serverError[s.key];
            const v = valueOf(s);
            return (
              <div className="pf-setting" key={s.key} data-testid={id}>
                <div className="pf-setting-text">
                  <label htmlFor={id} className="pf-setting-label">
                    {s.label}
                  </label>
                  <p className="pf-setting-help" id={`${id}-help`}>
                    {s.help}
                  </p>
                  <span className={`pf-chip tone-${s.source === "console" ? "info" : "neutral"}`}>{SOURCE_LABELS[s.source] || s.source}</span>
                </div>
                <div className="pf-setting-control">
                  {s.kind === "bool" ? (
                    <label className="pf-switch">
                      <input
                        id={id}
                        type="checkbox"
                        role="switch"
                        checked={!!v}
                        aria-describedby={`${id}-help`}
                        onChange={(e) => setDraft((d) => ({ ...d, [s.key]: e.target.checked }))}
                      />
                      <span className="pf-switch-track" aria-hidden="true" />
                      <span>{v ? "On" : "Off"}</span>
                    </label>
                  ) : s.kind === "choice" ? (
                    <select id={id} value={v} aria-describedby={`${id}-help`} onChange={(e) => setDraft((d) => ({ ...d, [s.key]: e.target.value }))}>
                      {(s.choices || []).map((c) => (
                        <option key={c} value={c}>
                          {PLAN_LABELS[c] || c}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      id={id}
                      type={s.kind === "int" ? "number" : s.kind === "email" ? "email" : s.kind === "url" ? "url" : "text"}
                      inputMode={s.kind === "int" ? "numeric" : undefined}
                      min={s.min}
                      max={s.max}
                      value={v ?? ""}
                      placeholder={s.kind === "url" ? "https://" : undefined}
                      aria-invalid={!!err}
                      aria-describedby={`${id}-help${err ? ` ${id}-error` : ""}`}
                      onChange={(e) => setDraft((d) => ({ ...d, [s.key]: e.target.value }))}
                    />
                  )}
                  {s.source === "console" && (
                    <button type="button" className="link-button" disabled={busy} onClick={() => reset(s.key)}>
                      Reset
                    </button>
                  )}
                  {err && (
                    <p className="pf-field-error" id={`${id}-error`} role="alert">
                      {err}
                    </p>
                  )}
                </div>
              </div>
            );
          })}
          <div className="pf-settings-save">
            <button type="submit" disabled={!dirty || busy || Object.keys(errors).length > 0} data-testid="save-settings">
              {busy ? "Saving…" : "Save"}
            </button>
            {dirty && (
              <button type="button" className="btn-secondary" onClick={() => setDraft({})}>
                Undo changes
              </button>
            )}
            <span className="pf-row-meta">
              <Icon name="shield" size={14} /> Security switches are set on the server, not here.
            </span>
          </div>
        </form>
      )}
      <section className="pf-system" aria-labelledby="pf-system-title">
        <h3 id="pf-system-title" className="pf-section-title">
          System
        </h3>
        <SystemStatusPanel />
      </section>
    </div>
  );
}
