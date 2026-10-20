import { useCallback, useEffect, useMemo, useState } from "react";
import { deleteIntegration, listIntegrations, saveIntegration, testIntegration } from "../api";
import { useLocation } from "react-router-dom";
import {
  disconnectClover,
  getCloverAuthorizeUrl,
  getCloverStatus,
  saveCloverToken,
  testCloverConnection,
} from "../clients/clover.js";
import { formatRestaurantTime as formatDateTime } from "../format.js";
import {
  CLOVER_REGIONS,
  INTEGRATIONS,
  INTEGRATIONS_BY_KIND,
  cloverReturnMessage,
  cloverView,
  validateCloverManual,
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
import "../settings/trackD.css";
import PageHeader from "../ui/PageHeader.jsx";
import { InfoButton } from "../ui/helpBridge.jsx";

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
  const location = useLocation();
  const returned = useMemo(() => cloverReturnMessage(location.search), [location.search]);
  // Back from Clover's sign-in: open the Clover card.
  const wanted = returned && views.some((v) => v.kind === "clover") ? "clover" : open;
  const current = views.find((v) => v.kind === wanted) || views[0];

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
      <PageHeader pageId="integrations">
        <p className="muted">Connect your store's POS, messaging, email and AI. Saved keys are encrypted and never shown again.</p>
      </PageHeader>
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
      ) : current.kind === "clover" ? (
        <CloverCard key="clover" onChanged={load} disabled={!!loadError} returned={returned} />
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
  const [busy, setBusy] = useState(null); // "save" | "test" | "remove"
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

// Clover (website-ready track D3): Connect with Clover (OAuth, when the
// service has a Clover app) or a merchant API token, then Test connection
// and Disconnect. The token is write-only, like every other secret here.
function CloverCard({ onChanged, disabled, returned }) {
  const [status, setStatus] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [form, setForm] = useState({ merchant_id: "", api_token: "", region: "us" });
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(null); // "connect" | "save" | "test" | "disconnect"
  const [message, setMessage] = useState(returned || null);
  const [showToken, setShowToken] = useState(false);

  const load = useCallback(async () => {
    try {
      const s = await getCloverStatus();
      setStatus(s);
      setLoadError(null);
      setForm((f) => ({ ...f, merchant_id: s?.merchant_id || f.merchant_id, region: s?.region || f.region, api_token: "" }));
    } catch (err) {
      setLoadError(integrationErrorMessage(err));
      setStatus({});
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const view = cloverView(status);
  const tokenFormOpen = showToken || (!view.connected && !view.oauthEnabled) || (view.connected && view.mode === "token");

  async function run(kind, fn) {
    setBusy(kind);
    setMessage(null);
    try {
      await fn();
    } catch (err) {
      setMessage({ tone: "bad", text: integrationErrorMessage(err) });
    } finally {
      setBusy(null);
    }
  }

  const connect = () =>
    run("connect", async () => {
      const r = await getCloverAuthorizeUrl();
      if (r?.url) window.location.assign(r.url);
    });

  const save = () =>
    run("save", async () => {
      const { errors: errs, body } = validateCloverManual(form, view);
      setErrors(errs);
      if (Object.keys(errs).length) return;
      await saveCloverToken(body);
      setMessage({ tone: "ok", text: "Clover saved. Choose Test connection to check it." });
      await load();
      await onChanged();
    });

  const test = () =>
    run("test", async () => {
      const r = await testCloverConnection();
      const ok = r?.ok !== false;
      setMessage({ tone: ok ? "ok" : "bad", text: r?.message || (ok ? "Connection works." : "Connection failed.") });
      await load();
    });

  const disconnect = () =>
    run("disconnect", async () => {
      if (!window.confirm("Disconnect Clover? Sales and items stop syncing until you connect again.")) return;
      await disconnectClover();
      setMessage({ tone: "ok", text: "Clover disconnected." });
      await load();
      await onChanged();
    });

  const off = disabled || !!busy || !view.editable || !!loadError;

  return (
    <div className="card" data-testid="int-card-clover">
      <div className="pf-row-top" style={{ marginBottom: "0.4rem" }}>
        <h2 style={{ margin: 0 }}>Clover</h2>
        <Chip view={view} />
        {view.savedText && <span className="int-tile-saved">{view.savedText}</span>}
        <InfoButton pageId="integrations" section="clover" />
      </div>
      <p className="muted" style={{ marginTop: 0 }}>
        Your Clover POS: sales, items, stock and employees. Nothing is ever changed in Clover.
      </p>
      {loadError && <div className="pf-banner bad">{loadError}</div>}
      {status === null && <p className="muted">Loading…</p>}

      {status !== null && view.lines.length > 0 && (
        <dl className="int-facts" data-testid="clover-facts">
          {view.lines.map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      )}

      {status !== null && !view.editable && (
        <p className="muted">On this server, Clover is set in the server's settings file by whoever runs it.</p>
      )}

      {status !== null && view.editable && (
        <>
          {!(view.connected && view.mode === "token") && (
            <div className="int-actions">
              <button type="button" onClick={connect} disabled={off || !view.oauthEnabled} data-testid="clover-connect">
                {busy === "connect" ? "Opening Clover…" : view.connected ? "Connect again" : "Connect with Clover"}
              </button>
              {!view.oauthEnabled && (
                <span className="muted int-note" data-testid="clover-oauth-off">
                  Not set up on this service yet: use an API token below.
                </span>
              )}
              {view.oauthEnabled && !tokenFormOpen && (
                <button type="button" className="link-button" onClick={() => setShowToken(true)}>
                  Use an API token instead
                </button>
              )}
            </div>
          )}

          {tokenFormOpen && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                save();
              }}
              noValidate
            >
              <div className="int-form">
                <label className="int-field">
                  Merchant ID
                  <input
                    type="text"
                    value={form.merchant_id}
                    autoCapitalize="characters"
                    spellCheck={false}
                    aria-invalid={!!errors.merchant_id}
                    aria-describedby="clover-mid-hint"
                    onChange={(e) => setForm((f) => ({ ...f, merchant_id: e.target.value }))}
                    data-testid="clover-merchant-id"
                  />
                  <span id="clover-mid-hint" className={errors.merchant_id ? "field-error" : "muted field-hint"}>
                    {errors.merchant_id || "In Clover: Account & Setup > Merchants (13 letters and digits)."}
                  </span>
                </label>
                <label className="int-field">
                  API token
                  <input
                    type="password"
                    value={form.api_token}
                    autoComplete="new-password"
                    placeholder={view.connected && view.mode === "token" ? "saved - type to replace" : ""}
                    aria-invalid={!!errors.api_token}
                    aria-describedby="clover-token-hint"
                    onChange={(e) => setForm((f) => ({ ...f, api_token: e.target.value }))}
                    data-testid="clover-api-token"
                  />
                  <span id="clover-token-hint" className={errors.api_token ? "field-error" : "muted field-hint"}>
                    {errors.api_token || "In Clover: Account & Setup > API Tokens. Read-only permissions are enough."}
                  </span>
                </label>
                <label className="int-field">
                  Region
                  <select value={form.region} onChange={(e) => setForm((f) => ({ ...f, region: e.target.value }))} data-testid="clover-region">
                    {CLOVER_REGIONS.map((r) => (
                      <option key={r.value} value={r.value}>
                        {r.label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <div className="int-actions">
                <button type="submit" disabled={off} data-testid="clover-save">
                  {busy === "save" ? "Saving…" : "Save"}
                </button>
              </div>
            </form>
          )}

          {view.connected && (
            <div className="int-actions">
              <button type="button" className="btn-secondary" onClick={test} disabled={off} data-testid="int-test-clover">
                {busy === "test" ? "Testing…" : "Test connection"}
              </button>
              <button type="button" className="link-button link-danger" onClick={disconnect} disabled={off} data-testid="clover-disconnect">
                Disconnect
              </button>
            </div>
          )}
        </>
      )}

      {message && (
        <div className={`pf-banner ${message.tone}`} role="status" style={{ marginTop: "0.8rem" }} data-testid="int-message">
          {message.text}
        </div>
      )}
      {view.lastTestedAt && (
        <p className="muted int-meta">
          Last test {formatDateTime(view.lastTestedAt)}
          {view.lastTestMessage ? `: ${view.lastTestMessage.replace(/[.\s]+$/, "")}` : ""}.
        </p>
      )}
    </div>
  );
}
