import { useCallback, useEffect, useMemo, useState } from "react";
import { connectClover, deleteIntegration, listIntegrations, saveIntegration, testIntegration } from "../api";
import { formatRestaurantTime as formatDateTime } from "../format.js";
import {
  INTEGRATIONS,
  INTEGRATIONS_BY_KIND,
  OPEN_CARD_KEY,
  buildPayload,
  containsSecret,
  formFromView,
  hasChanges,
  integrationErrorMessage,
  integrationViews,
} from "../settings/integrationsModel.js";
import { useStore } from "../store/StoreContext.jsx";
import "../platform/platform.css";

function readOpen() {
  try {
    const v = localStorage.getItem(OPEN_CARD_KEY);
    return INTEGRATIONS_BY_KIND[v] ? v : INTEGRATIONS[0].kind;
  } catch {
    return INTEGRATIONS[0].kind;
  }
}

function Chip({ view }) {
  return <span className={`pf-chip tone-${view.tone}`}>{view.statusLabel}</span>;
}

// Settings -> Integrations (docs/PLATFORM_CONTRACT.md 8.3, 9, 13.5): the
// store admin connects Toast, Clover, WhatsApp, email and AI. One screen:
// the five as tiles, the chosen one's form below (the choice is remembered).
// Secrets are write-only - saved values show as "saved •••• 1234".
export default function IntegrationsPage() {
  const [rows, setRows] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [open, setOpen] = useState(readOpen);

  const load = useCallback(async () => {
    try {
      const r = await listIntegrations();
      setRows(Array.isArray(r) ? r : r?.integrations || []);
      setLoadError(null);
    } catch (err) {
      setLoadError(integrationErrorMessage(err));
      setRows([]);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const { posName } = useStore();
  const views = useMemo(() => integrationViews(rows, posName), [rows, posName]);
  const current = views.find((v) => v.kind === open) || views[0];

  function choose(kind) {
    setOpen(kind);
    try {
      localStorage.setItem(OPEN_CARD_KEY, kind);
    } catch {
      // not remembered
    }
  }

  return (
    <div>
      <div className="page-header">
        <h1>Integrations</h1>
        <p className="muted">Connect your store's POS, messaging, email and AI. Saved keys are encrypted and never shown again.</p>
      </div>
      {loadError && <div className="pf-banner bad" style={{ marginBottom: "0.8rem" }}>{loadError}</div>}
      <div className="int-tiles" role="tablist" aria-label="Integrations">
        {views.map((v) => (
          <button
            key={v.kind}
            type="button"
            role="tab"
            aria-selected={v.kind === current.kind}
            className={`int-tile${v.kind === current.kind ? " active" : ""}`}
            data-testid={`int-tile-${v.kind}`}
            onClick={() => choose(v.kind)}
          >
            <span className="int-tile-name">{v.label}</span>
            <Chip view={v} />
            {v.savedText && <span className="int-tile-saved">{v.savedText}</span>}
          </button>
        ))}
      </div>
      {rows === null ? (
        <p className="muted">Loading…</p>
      ) : (
        <IntegrationCard key={current.kind} view={current} onChanged={load} disabled={!!loadError} />
      )}
    </div>
  );
}

function IntegrationCard({ view, onChanged, disabled }) {
  const spec = INTEGRATIONS_BY_KIND[view.kind];
  const [form, setForm] = useState(() => formFromView(spec, view));
  const [clear, setClear] = useState([]);
  const [busy, setBusy] = useState(null); // "save" | "test" | "remove" | "connect"
  const [message, setMessage] = useState(null); // {tone, text}

  // A save, test or remove brings a new view: the form starts from it again
  // (secret boxes empty), the message above stays.
  const viewStamp = `${view.configured}:${view.updatedAt || ""}`;
  useEffect(() => {
    setForm(formFromView(spec, view));
    setClear([]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewStamp]);

  const changed = hasChanges(spec, view, form, clear);
  // The secret whose last 4 the server shows ("saved •••• 1234").
  const primarySecret = spec.fields.find((f) => f.secret)?.key;

  function typedSecrets() {
    return Object.fromEntries(spec.fields.filter((f) => f.secret && form[f.key]).map((f) => [f.key, form[f.key]]));
  }

  // Never show a server reply that would echo a typed secret (the server
  // never does; this is a belt-and-braces check).
  function safeText(text) {
    return containsSecret(text, typedSecrets()) ? "Done." : text;
  }

  async function run(kind, fn) {
    setBusy(kind);
    setMessage(null);
    try {
      await fn();
    } catch (err) {
      setMessage({ tone: "bad", text: err.field ? err.message : integrationErrorMessage(err) });
    } finally {
      setBusy(null);
    }
  }

  const save = () =>
    run("save", async () => {
      await saveIntegration(spec.kind, buildPayload(spec, form, { clear }));
      setMessage({ tone: "ok", text: `${spec.label} saved.` });
      setClear([]);
      await onChanged();
    });

  const test = () =>
    run("test", async () => {
      // Unsaved changes are tested as typed; otherwise what's saved.
      const body = changed ? buildPayload(spec, form, { clear }) : undefined;
      const r = await testIntegration(spec.kind, body);
      const ok = r?.ok !== false && r?.status !== "failed";
      const text = r?.message || r?.last_test_message || (ok ? "Connection works." : "Connection failed.");
      setMessage({ tone: ok ? "ok" : "bad", text: safeText(text) });
      if (!changed) await onChanged();
    });

  const remove = () =>
    run("remove", async () => {
      if (!window.confirm(`Remove the saved ${spec.label} connection? Anything that uses it stops working.`)) return;
      await deleteIntegration(spec.kind);
      setMessage({ tone: "ok", text: `${spec.label} removed.` });
      await onChanged();
    });

  const connect = () =>
    run("connect", async () => {
      const r = await connectClover();
      if (r?.authorize_url) window.location.assign(r.authorize_url);
    });

  return (
    <div className="card" data-testid={`int-card-${spec.kind}`}>
      <div className="pf-row-top" style={{ marginBottom: "0.4rem" }}>
        <h2 style={{ margin: 0 }}>{view.label}</h2>
        <Chip view={view} />
        {view.savedText && <span className="int-tile-saved">{view.savedText}</span>}
      </div>
      <p className="muted" style={{ marginTop: 0 }}>
        {spec.blurb}
      </p>

      {spec.connect ? (
        <div className="int-actions">
          <button type="button" onClick={connect} disabled={disabled || !!busy} data-testid="clover-connect">
            {busy === "connect" ? "Connecting…" : "Connect Clover"}
          </button>
          <span className="muted" style={{ fontSize: "0.82rem" }}>
            Coming with M3 - you'll sign in to Clover and approve KloudC RetailOps.
          </span>
        </div>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (changed) save();
          }}
        >
          <div className="int-form">
            {spec.fields.map((f) => (
              <Field
                key={f.key}
                field={f}
                value={form[f.key]}
                saved={f.secret && view.configured}
                primary={f.key === primarySecret}
                cleared={clear.includes(f.key)}
                onChange={(v) => setForm((s) => ({ ...s, [f.key]: v }))}
                onClear={() => setClear((c) => (c.includes(f.key) ? c.filter((k) => k !== f.key) : [...c, f.key]))}
              />
            ))}
          </div>
          <div className="int-actions">
            <button type="submit" disabled={disabled || !changed || !!busy}>
              {busy === "save" ? "Saving…" : "Save"}
            </button>
            {spec.testable && (
              <button
                type="button"
                className="btn-secondary"
                onClick={test}
                disabled={disabled || !!busy || (!view.configured && !changed)}
                data-testid={`int-test-${spec.kind}`}
              >
                {busy === "test" ? "Testing…" : "Test connection"}
              </button>
            )}
            {view.configured && (
              <button type="button" className="link-button link-danger" onClick={remove} disabled={disabled || !!busy}>
                Remove
              </button>
            )}
          </div>
        </form>
      )}

      {message && (
        <div className={`pf-banner ${message.tone}`} role="status" style={{ marginTop: "0.8rem" }} data-testid="int-message">
          {message.text}
        </div>
      )}
      {(view.lastTestedAt || view.updatedAt) && (
        <p className="muted int-meta">
          {view.lastTestedAt && (
            <>
              Last test {formatDateTime(view.lastTestedAt)}
              {view.lastTestMessage ? `: ${view.lastTestMessage.replace(/[.\s]+$/, "")}` : ""}.{" "}
            </>
          )}
          {view.updatedAt && (
            <>
              Saved {formatDateTime(view.updatedAt)}
              {view.updatedBy ? ` by ${String(view.updatedBy).split(":").pop()}` : ""}.
            </>
          )}
        </p>
      )}
    </div>
  );
}

function Field({ field, value, saved, primary, cleared, onChange, onClear }) {
  if (field.type === "checkbox") {
    return (
      <label className="int-field int-field-check">
        <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />
        {field.label}
      </label>
    );
  }
  if (field.type === "json") {
    return (
      <label className="int-field full">
        {field.label}
        <textarea value={value} placeholder={field.placeholder} onChange={(e) => onChange(e.target.value)} spellCheck={false} />
        {field.hint && <span className="muted field-hint">{field.hint}</span>}
      </label>
    );
  }
  if (field.secret) {
    return (
      <label className="int-field">
        {field.label}
        <input
          type="password"
          value={value}
          autoComplete="new-password"
          placeholder={cleared ? "will be removed" : saved ? (primary ? "saved - type to replace" : "empty keeps what's saved") : ""}
          onChange={(e) => onChange(e.target.value)}
          data-testid={`secret-${field.key}`}
        />
        {saved && !value && (
          <button type="button" className="link-button" style={{ width: "auto", alignSelf: "flex-start", padding: 0 }} onClick={onClear}>
            {cleared ? "Keep the saved one" : primary ? "Remove the saved one" : "Clear this one"}
          </button>
        )}
      </label>
    );
  }
  return (
    <label className="int-field">
      {field.label}
      <input
        type={field.type === "number" ? "number" : "text"}
        value={value}
        placeholder={field.placeholder}
        onChange={(e) => onChange(e.target.value)}
        spellCheck={false}
      />
    </label>
  );
}
