// Label printing in the master console (docs/LABEL_PRINTING_FEATURE.md 3.2):
// the Feature requests list under sign-ups (Approve asks for the label site
// address; Decline needs a short note the store sees) and the Label printing
// row in a store's detail (on/off, address, options read-only in v1).
import { useCallback, useEffect, useState } from "react";
import {
  approveFeatureRequest,
  declineFeatureRequest,
  getStoreLabelPrinting,
  listFeatureRequests,
  setLabelPrinting,
} from "../clients/console.js";
import { labelsAddressError } from "../settings/storeSettingsModel.js";
import { Chip, Feedback, when } from "./ConsoleBits.jsx";
import { featureApproveBody, labelPrintingChip, matchesSearch, platformErrorMessage } from "./platformModel.js";

const SEARCH_FIELDS = ["store_code", "feature_label", "requested_by", "note"];

export function FeatureRequestsList({ search = "", onOpenCount }) {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const [message, setMessage] = useState(null);
  const load = useCallback(async () => {
    try {
      const r = await listFeatureRequests("requested");
      setRows(r?.requests || []);
      onOpenCount?.(r?.open_count ?? (r?.requests || []).length);
      setError(null);
    } catch (err) {
      setRows([]);
      setError(platformErrorMessage(err));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  const shown = (rows || []).filter((r) => matchesSearch(r, search, SEARCH_FIELDS));
  return (
    <section className="kc-feature-requests" aria-labelledby="feature-requests-title" data-testid="feature-requests">
      <h2 id="feature-requests-title" className="pf-section-title">
        Feature requests
      </h2>
      <Feedback message={message} />
      {error && <Feedback message={{ tone: "bad", text: error }} />}
      {rows === null ? (
        <p className="muted">Loading…</p>
      ) : shown.length === 0 ? (
        <p className="muted pf-empty">No feature requests waiting.</p>
      ) : (
        <div className="pf-list">
          {shown.map((r) => (
            <FeatureRequestRow
              key={r.id}
              req={r}
              onDone={async (text, tone = "ok") => {
                setMessage({ text, tone });
                await load();
              }}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function FeatureRequestRow({ req, onDone }) {
  const [mode, setMode] = useState(null); // "approve" | "decline"
  const [form, setForm] = useState({ labelsUrl: "", note: "" });
  const [stored, setStored] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (mode !== "approve") return;
    getStoreLabelPrinting(req.store_code)
      .then((lp) => {
        setStored(lp?.url || null);
        setForm((f) => ({ ...f, labelsUrl: f.labelsUrl || lp?.url || "" }));
      })
      .catch(() => setStored(null));
  }, [mode, req.store_code]);

  async function act(fn) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(platformErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const typed = form.labelsUrl.trim();
  const addressError = typed && typed !== stored ? labelsAddressError(typed) : null;

  return (
    <div className="pf-row" data-testid={`feature-request-${req.id}`}>
      <div className="pf-row-top">
        <span className="pf-row-title">{req.feature_label || req.feature}</span>
        <span className="pf-code">{req.store_code}</span>
        <Chip chip={{ tone: "info", label: "Asked" }} />
      </div>
      <div className="pf-row-meta">{[req.requested_by, when(req.created_at)].filter(Boolean).join(" · ")}</div>
      {req.note && <div className="pf-row-meta">“{req.note}”</div>}
      {!mode && (
        <div className="pf-actions">
          <button type="button" onClick={() => setMode("approve")} data-testid={`feature-approve-${req.id}`}>
            Approve
          </button>
          <button type="button" className="btn-secondary" onClick={() => setMode("decline")}>
            Decline
          </button>
        </div>
      )}
      {mode === "approve" && (
        <form
          className="pf-inline-form"
          onSubmit={(e) => {
            e.preventDefault();
            const out = featureApproveBody(form, stored);
            if (out.error) return setError(out.error);
            if (addressError) return setError(addressError);
            act(async () => {
              await approveFeatureRequest(req.id, out.body);
              await onDone(`Label printing is on for ${req.store_code}.`);
            });
          }}
        >
          <label>
            Label site address
            <input
              type="url"
              value={form.labelsUrl}
              placeholder="https://labels-<name>.kloudc.com"
              onChange={(e) => setForm((f) => ({ ...f, labelsUrl: e.target.value }))}
              data-testid={`feature-approve-url-${req.id}`}
            />
          </label>
          <label>
            Note (optional)
            <input value={form.note} maxLength={1000} onChange={(e) => setForm((f) => ({ ...f, note: e.target.value }))} />
          </label>
          <div className="pf-actions">
            <button type="submit" disabled={busy || (!typed && !stored)} data-testid={`feature-approve-confirm-${req.id}`}>
              {busy ? "Turning on…" : "Approve and turn on"}
            </button>
            <button type="button" className="btn-secondary" onClick={() => setMode(null)} disabled={busy}>
              Cancel
            </button>
          </div>
        </form>
      )}
      {mode === "decline" && (
        <form
          className="pf-inline-form"
          onSubmit={(e) => {
            e.preventDefault();
            act(async () => {
              await declineFeatureRequest(req.id, form.note.trim());
              await onDone(`Declined for ${req.store_code}.`, "");
            });
          }}
        >
          <label style={{ gridColumn: "1 / -1" }}>
            Note for the store
            <input
              value={form.note}
              maxLength={1000}
              onChange={(e) => setForm((f) => ({ ...f, note: e.target.value }))}
              placeholder="The store sees this on its Label printing card"
            />
          </label>
          <div className="pf-actions">
            <button type="submit" disabled={busy || !form.note.trim()}>
              {busy ? "Declining…" : "Decline"}
            </button>
            <button type="button" className="btn-secondary" onClick={() => setMode(null)} disabled={busy}>
              Cancel
            </button>
          </div>
        </form>
      )}
      {error && (
        <p className="error" style={{ margin: 0 }} role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

export function LabelPrintingRow({ code }) {
  const [lp, setLp] = useState(null);
  const [url, setUrl] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);
  const load = useCallback(() => {
    getStoreLabelPrinting(code)
      .then((v) => setLp(v || { state: "off", url: null, options: {}, open_request_id: null }))
      .catch((err) => setMessage({ tone: "bad", text: platformErrorMessage(err) }));
  }, [code]);
  useEffect(() => load(), [load]);

  async function save(changes, done) {
    setBusy(true);
    setMessage(null);
    try {
      await setLabelPrinting(code, changes);
      setUrl(null);
      setMessage({ tone: "ok", text: done });
      load();
    } catch (err) {
      setMessage({ tone: "bad", text: platformErrorMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  if (!lp) return message ? <Feedback message={message} /> : null;
  const on = lp.state !== "off";
  const typed = url ?? lp.url ?? "";
  const urlError = url !== null && url.trim() ? labelsAddressError(url) : null;
  const options = lp.options && Object.keys(lp.options).length ? JSON.stringify(lp.options, null, 1) : null;
  return (
    <div className="kc-label-printing-row" data-testid="store-label-printing">
      <h3 className="pf-section-title">Label printing</h3>
      <dl className="pf-facts">
        <div>
          <dt>State</dt>
          <dd>
            <Chip chip={labelPrintingChip(lp.state)} />
            {lp.open_request_id ? <span className="pf-row-meta"> Asked - see Requests</span> : null}
          </dd>
        </div>
        <div>
          <dt>Switch</dt>
          <dd>
            <label className="pf-switch">
              <input
                type="checkbox"
                role="switch"
                checked={on}
                disabled={busy}
                aria-label="Label printing on or off"
                onChange={(e) => save({ enabled: e.target.checked }, e.target.checked ? "Label printing turned on." : "Label printing turned off.")}
              />
              <span className="pf-switch-track" aria-hidden="true" />
              <span>{on ? "On" : "Off"}</span>
            </label>
          </dd>
        </div>
        <div>
          <dt>Address</dt>
          <dd>
            <form
              className="pf-inline-form"
              onSubmit={(e) => {
                e.preventDefault();
                if (url === null || urlError) return;
                save({ url }, "Address saved.");
              }}
            >
              <input type="url" value={typed} placeholder="https://labels-<name>.kloudc.com" aria-label="Label site address" onChange={(e) => setUrl(e.target.value)} />
              {url !== null && (
                <button type="submit" className="btn-secondary" disabled={busy || !!urlError}>
                  Save address
                </button>
              )}
            </form>
            {urlError && <p className="pf-field-error">{urlError}</p>}
          </dd>
        </div>
        {options && (
          <div>
            <dt>Choices</dt>
            <dd>
              <pre className="pf-code">{options}</pre>
            </dd>
          </div>
        )}
      </dl>
      <Feedback message={message} />
    </div>
  );
}
