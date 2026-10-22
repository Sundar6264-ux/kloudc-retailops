// The Label printing card (docs/LABEL_PRINTING_FEATURE.md 3.1): on (the
// address, editable by an admin, and Open label printing), off with Request,
// requested (Cancel request) or declined (the note and Ask again). Used on
// Store settings and on /#/label-printing/open.
import { useCallback, useEffect, useState } from "react";
import {
  cancelLabelPrintingRequest,
  getLabelPrinting,
  openLabelsHandoff,
  requestLabelPrinting,
  saveOwnStoreSettings,
} from "../clients/console.js";
import { formatRestaurantTime } from "../format.js";
import { platformErrorMessage } from "../platform/platformModel.js";
import { LABEL_PRINTING_TEXT, labelPrintingView, labelsAddressError } from "./storeSettingsModel.js";

export function useLabelPrinting() {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const reload = useCallback(() => {
    return getLabelPrinting()
      .then((s) => {
        setStatus(s);
        setError(null);
        return s;
      })
      .catch((err) => {
        setError(platformErrorMessage(err));
        setStatus({ state: "off", can_request: false, request: null, failed: true });
        return null;
      });
  }, []);
  useEffect(() => {
    reload();
  }, [reload]);
  return { status, error, reload };
}

export async function openLabelPrinting() {
  const r = await openLabelsHandoff();
  if (r?.url) window.location.assign(r.url);
}

export default function LabelPrintingCard({ isAdmin, initial = null }) {
  const own = useLabelPrinting();
  const status = own.status || initial;
  const view = labelPrintingView(status);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);
  const [address, setAddress] = useState(null);

  async function act(fn, done) {
    setBusy(true);
    setMessage(null);
    try {
      await fn();
      if (done) setMessage({ tone: "ok", text: done });
      await own.reload();
    } catch (err) {
      setMessage({ tone: "bad", text: platformErrorMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  const typed = address ?? view.url ?? "";
  const addressError = address !== null ? labelsAddressError(address) : null;

  return (
    <section className="pf-row kc-label-printing" aria-labelledby="label-printing-title" data-testid="label-printing-card" data-state={view.kind}>
      <h2 id="label-printing-title" className="pf-section-title">
        Label printing
      </h2>
      {own.error && <p className="pf-row-meta">{own.error}</p>}
      {view.kind === "loading" && <p className="muted">Loading…</p>}
      {view.kind === "on" && (
        <>
          <p className="pf-row-meta">{LABEL_PRINTING_TEXT.on}</p>
          {isAdmin ? (
            <form
              className="pf-inline-form"
              onSubmit={(e) => {
                e.preventDefault();
                if (addressError || address === null) return;
                act(async () => {
                  await saveOwnStoreSettings({ labels_url: address.trim() });
                  setAddress(null);
                }, "Saved.");
              }}
            >
              <label>
                Label site address
                <input
                  type="url"
                  value={typed}
                  placeholder="https://"
                  aria-invalid={!!addressError}
                  onChange={(e) => setAddress(e.target.value)}
                  data-testid="labels-address"
                />
              </label>
              {addressError && (
                <p className="pf-field-error" role="alert">
                  {addressError}
                </p>
              )}
              <div className="pf-actions">
                <button type="button" onClick={() => act(openLabelPrinting)} disabled={busy} data-testid="open-label-printing">
                  Open label printing
                </button>
                {address !== null && (
                  <button type="submit" className="btn-secondary" disabled={busy || !!addressError}>
                    Save address
                  </button>
                )}
              </div>
            </form>
          ) : (
            <div className="pf-actions">
              <button type="button" onClick={() => act(openLabelPrinting)} disabled={busy} data-testid="open-label-printing">
                Open label printing
              </button>
            </div>
          )}
        </>
      )}
      {view.kind === "off" && (
        <>
          <p className="pf-row-meta">{LABEL_PRINTING_TEXT.off}</p>
          {view.canAsk ? (
            <div className="pf-actions">
              <button type="button" disabled={busy} onClick={() => act(() => requestLabelPrinting(), LABEL_PRINTING_TEXT.asked)} data-testid="request-label-printing">
                Request
              </button>
            </div>
          ) : (
            !isAdmin && <p className="pf-row-meta">{LABEL_PRINTING_TEXT.askAdmin}</p>
          )}
        </>
      )}
      {view.kind === "requested" && (
        <>
          <p className="pf-row-meta">
            Asked{view.since ? ` on ${formatRestaurantTime(view.since)}` : ""}. We'll turn it on and let you know.
          </p>
          {view.canCancel && (
            <div className="pf-actions">
              <button type="button" className="btn-secondary" disabled={busy} onClick={() => act(() => cancelLabelPrintingRequest(), "Request cancelled.")}>
                Cancel request
              </button>
            </div>
          )}
        </>
      )}
      {view.kind === "declined" && (
        <>
          <p className="pf-row-meta">Not turned on yet{view.note ? `: ${view.note}` : "."}</p>
          {view.canAsk ? (
            <div className="pf-actions">
              <button type="button" disabled={busy} onClick={() => act(() => requestLabelPrinting(), LABEL_PRINTING_TEXT.asked)}>
                Ask again
              </button>
            </div>
          ) : (
            !isAdmin && <p className="pf-row-meta">{LABEL_PRINTING_TEXT.askAdmin}</p>
          )}
        </>
      )}
      {message && (
        <p className={message.tone === "bad" ? "pf-field-error" : "pf-row-meta"} role={message.tone === "bad" ? "alert" : "status"}>
          {message.text}
        </p>
      )}
    </section>
  );
}
