// Setup > Store settings (website ready A5; contract "Deferred"): the store
// admin edits the store's own non-secret details (name, address, contact,
// sender name, logo and colours, AI) - GET/PATCH
// /api/settings/store - the Label printing card (on / Request,
// docs/LABEL_PRINTING_FEATURE.md 3.1) and, when the platform offers it, asks for the
// store's own server (/api/settings/hosting). Owners/admins only (the route
// and the API are admin-only). On a single-store server the settings live
// with KloudC support, and the page says so.
import { useEffect, useMemo, useState } from "react";
import { getHostingStatus, getOwnStoreSettings, requestOwnServer, saveOwnStoreSettings } from "../clients/console.js";
import { platformErrorMessage } from "../platform/platformModel.js";
import {
  STORE_SETTING_GROUPS,
  draftErrors,
  hostingView,
  storeSettingsChanges,
  unavailableReason,
} from "../settings/storeSettingsModel.js";
import { useStore } from "../store/StoreContext.jsx";
import EmptyState from "../ui/EmptyState.jsx";
import PageHeader from "../ui/PageHeader.jsx";
import LabelPrintingCard from "../settings/LabelPrintingCard.jsx";
import "../platform/console.css";

const COLOR_RE = /^#[0-9a-fA-F]{6}$/;
const COLOR_HINT = "#RRGGBB";
// The colour picker's starting value when none is saved: the theme's own
// --primary token (read from the page, so no colour is written here).
function defaultPicker() {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue("--primary").trim();
    return COLOR_RE.test(v) ? v : "";
  } catch {
    return "";
  }
}

export default function StoreSettingsPage() {
  const store = useStore();
  const [saved, setSaved] = useState(null);
  const [draft, setDraft] = useState({});
  const [unavailable, setUnavailable] = useState(null);
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(false);
  const [hosting, setHosting] = useState(null);

  useEffect(() => {
    getOwnStoreSettings()
      .then((r) => setSaved(r.settings || {}))
      .catch((err) => {
        const why = unavailableReason(err);
        if (why) setUnavailable(why);
        else setMessage({ tone: "bad", text: platformErrorMessage(err) });
        setSaved({});
      });
    getHostingStatus()
      .then(setHosting)
      .catch(() => setHosting(null));
  }, []);

  const errors = useMemo(() => draftErrors(draft), [draft]);
  const changes = storeSettingsChanges(saved, draft);
  const dirty = Object.keys(changes).length > 0;
  const valueOf = (f) => (f.key in draft ? draft[f.key] : saved?.[f.key] ?? (f.kind === "bool" ? false : ""));

  async function save(e) {
    e.preventDefault();
    if (Object.keys(errors).length || !dirty) return;
    setBusy(true);
    setMessage(null);
    try {
      const r = await saveOwnStoreSettings(changes);
      setSaved(r.settings || {});
      setDraft({});
      setMessage({ tone: "ok", text: "Saved. Reload a page to see the new name or colours everywhere." });
    } catch (err) {
      setMessage({ tone: "bad", text: platformErrorMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  if (unavailable) {
    return (
      <div data-testid="store-settings-page">
        <PageHeader pageId="store_settings" />
        <EmptyState icon="store" title={unavailable === "single" ? "Managed by KloudC support" : "Owners and admins only"} area="setup">
          {unavailable === "single"
            ? "This store runs on its own server, so its name, address and colours are set by KloudC support. Email support to change them."
            : "Ask your store's owner to change these settings."}
        </EmptyState>
      </div>
    );
  }

  return (
    <div data-testid="store-settings-page">
      <PageHeader pageId="store_settings" subtitle={store?.code ? `Store ID ${store.code} · this ID never changes` : undefined} />
      {message && (
        <div className={`pf-banner ${message.tone}`} role={message.tone === "bad" ? "alert" : "status"}>
          {message.text}
        </div>
      )}
      {saved === null ? (
        <p className="muted">Loading…</p>
      ) : (
        <form className="pf-settings-form kc-store-settings" onSubmit={save} noValidate aria-label="Store settings">
          {STORE_SETTING_GROUPS.map((g) => (
            <fieldset key={g.key} className="kc-fieldset">
              <legend className="pf-section-title">{g.title}</legend>
              {g.fields.map((f) => {
                const id = `ss-${f.key}`;
                const err = errors[f.key];
                const v = valueOf(f);
                const describedBy = [f.help ? `${id}-help` : null, err ? `${id}-error` : null].filter(Boolean).join(" ") || undefined;
                return (
                  <div className="pf-setting" key={f.key}>
                    <div className="pf-setting-text">
                      <label htmlFor={id} className="pf-setting-label">
                        {f.label}
                      </label>
                      {f.help && (
                        <p className="pf-setting-help" id={`${id}-help`}>
                          {f.help}
                        </p>
                      )}
                    </div>
                    <div className="pf-setting-control">
                      {f.kind === "bool" ? (
                        <label className="pf-switch">
                          <input id={id} type="checkbox" role="switch" checked={!!v} aria-describedby={describedBy} onChange={(e) => setDraft((d) => ({ ...d, [f.key]: e.target.checked }))} />
                          <span className="pf-switch-track" aria-hidden="true" />
                          <span>{v ? "On" : "Off"}</span>
                        </label>
                      ) : f.kind === "textarea" ? (
                        <textarea id={id} rows={3} value={v} maxLength={f.max} aria-invalid={!!err} aria-describedby={describedBy} onChange={(e) => setDraft((d) => ({ ...d, [f.key]: e.target.value }))} />
                      ) : f.kind === "color" ? (
                        <span className="kc-color-field">
                          <input type="color" aria-label={`${f.label} picker`} value={COLOR_RE.test(v) ? v : defaultPicker() || undefined} onChange={(e) => setDraft((d) => ({ ...d, [f.key]: e.target.value }))} />
                          <input id={id} type="text" value={v} placeholder={COLOR_HINT} aria-invalid={!!err} aria-describedby={describedBy} onChange={(e) => setDraft((d) => ({ ...d, [f.key]: e.target.value }))} />
                        </span>
                      ) : (
                        <input
                          id={id}
                          type={f.kind === "email" ? "email" : f.kind === "url" ? "url" : f.kind === "tel" ? "tel" : "text"}
                          value={v}
                          maxLength={f.max}
                          placeholder={f.kind === "url" ? "https://" : undefined}
                          aria-invalid={!!err}
                          aria-describedby={describedBy}
                          onChange={(e) => setDraft((d) => ({ ...d, [f.key]: e.target.value }))}
                        />
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
            </fieldset>
          ))}
          <div className="pf-settings-save">
            <button type="submit" disabled={!dirty || busy || Object.keys(errors).length > 0} data-testid="save-store-settings">
              {busy ? "Saving…" : "Save"}
            </button>
            {dirty && (
              <button type="button" className="btn-secondary" onClick={() => setDraft({})}>
                Undo changes
              </button>
            )}
          </div>
        </form>
      )}
      <LabelPrintingCard isAdmin />
      <OwnServer hosting={hosting} onRequested={() => getHostingStatus().then(setHosting).catch(() => {})} />
    </div>
  );
}

function OwnServer({ hosting, onRequested }) {
  const view = hostingView(hosting);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  if (!view.show) return null;
  return (
    <section className="pf-row kc-own-server" aria-labelledby="own-server-title">
      <h2 id="own-server-title" className="pf-section-title">
        Your own server
      </h2>
      {view.dedicated ? (
        <p className="pf-row-meta">This store already runs on its own server.</p>
      ) : view.pending ? (
        <p className="pf-row-meta">Your request is with KloudC ({view.pending.status}). Nothing moves until they confirm a time with you.</p>
      ) : (
        <form
          className="pf-inline-form"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError(null);
            try {
              await requestOwnServer({ reason: reason.trim() });
              onRequested();
            } catch (err) {
              setError(platformErrorMessage(err));
            } finally {
              setBusy(false);
            }
          }}
        >
          <label>
            Why do you want your own server? <span className="muted">(optional)</span>
            <input value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} />
          </label>
          <div className="pf-actions">
            <button type="submit" disabled={busy}>
              Ask KloudC
            </button>
          </div>
          {error && (
            <p className="pf-field-error" role="alert">
              {error}
            </p>
          )}
        </form>
      )}
    </section>
  );
}
