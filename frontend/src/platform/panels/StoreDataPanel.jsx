import { useCallback, useEffect, useId, useMemo, useState } from "react";
import {
  cancelOffboarding,
  getOffboarding,
  listStoreExports,
  purgeStoreData,
  requestStoreExport,
  startOffboarding,
  storeExportLink,
} from "../../clients/data.js";
import {
  confirmMatches,
  dataErrorMessage,
  offboardingActions,
  offboardingState,
  timelineRows,
} from "../../settings/dataModel.js";
import { Banner, ExportsBlock, Section, StatusChip, useExports } from "./dataParts.jsx";

// The Data tab of a store in the master console (docs/WEBSITE_READY_CONTRACT.md
// B4, B5, I7): data exports with their Download links, and off-boarding -
// Archived -> Deleting on <date> -> Deleted, every step audited. Track A's
// store detail renders <StoreDataPanel storeCode status onChanged />.
export default function StoreDataPanel({ storeCode, status, onChanged }) {
  const api = useMemo(
    () => ({
      key: storeCode,
      list: () => listStoreExports(storeCode),
      request: () => requestStoreExport(storeCode),
      link: (id) => storeExportLink(storeCode, id),
    }),
    [storeCode],
  );
  const exportsHook = useExports(api);
  const [off, setOff] = useState(null);
  const [error, setError] = useState(null);
  const [starting, setStarting] = useState(false);

  const loadOff = useCallback(async () => {
    try {
      setOff(await getOffboarding(storeCode));
      setError(null);
    } catch (err) {
      setError(dataErrorMessage(err));
    }
  }, [storeCode]);

  useEffect(() => {
    setOff(null);
    loadOff();
  }, [loadOff, status]);

  // While the final export is being made, refresh the off-boarding block too.
  const finalBusy = ["queued", "running"].includes(off?.final_export?.status);
  useEffect(() => {
    if (!finalBusy) return undefined;
    const t = setTimeout(loadOff, 2000);
    return () => clearTimeout(t);
  }, [finalBusy, off, loadOff]);

  async function startExport() {
    setStarting(true);
    try {
      await api.request();
      await exportsHook.load();
    } catch (err) {
      exportsHook.setError(dataErrorMessage(err));
    } finally {
      setStarting(false);
    }
  }

  async function changed(next) {
    setOff(next);
    await exportsHook.load();
    onChanged?.();
  }

  const deleted = off?.state === "deleted";
  const exportBusy = exportsHook.state.running || starting;

  return (
    <div className="dt-stack" data-area="platform" data-testid="store-data-panel">
      <Section
        title="Data exports"
        pageId="platform_store_data"
        section="exports"
        sub="The same ZIP file the store's owner can make: every table as a spreadsheet and every file, without secrets."
        actions={
          !deleted && (
            <button type="button" onClick={startExport} disabled={exportBusy || exportsHook.data === null} data-testid="export-all">
              {exportBusy ? "Making the file…" : "Export all data"}
            </button>
          )
        }
        testId="exports-section"
      >
        <ExportsBlock api={api} exportsHook={exportsHook} emptyText="No data exports for this store yet." />
      </Section>

      <Section title="Off-boarding" pageId="platform_store_data" section="offboarding" testId="offboarding-section">
        {error && <Banner tone="bad">{error}</Banner>}
        {off === null ? (
          <p className="dt-muted">Loading…</p>
        ) : (
          <Offboarding storeCode={storeCode} view={off} onChanged={changed} />
        )}
      </Section>
    </div>
  );
}

function Offboarding({ storeCode, view, onChanged }) {
  const state = offboardingState(view);
  const actions = offboardingActions(view);
  const timeline = timelineRows(view);
  const [form, setForm] = useState(null); // "start" | "cancel" | "purge"
  const [confirm, setConfirm] = useState("");
  const [reason, setReason] = useState("");
  const [code, setCode] = useState("");
  const [restore, setRestore] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);
  const ids = { confirm: useId(), reason: useId(), code: useId() };

  function open(which) {
    setForm((f) => (f === which ? null : which));
    setConfirm("");
    setReason("");
    setCode("");
    setMessage(null);
  }

  async function run(fn, done) {
    setBusy(true);
    setMessage(null);
    try {
      const next = await fn();
      setForm(null);
      setMessage({ tone: "good", text: done });
      await onChanged(next);
    } catch (err) {
      setMessage({ tone: "bad", text: dataErrorMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  const final = view.final_export;
  return (
    <div>
      <div className="dt-state">
        <StatusChip tone={state.tone}>{state.label}</StatusChip>
        {final && (
          <StatusChip tone={final.status === "ready" ? "good" : final.status === "failed" ? "bad" : "info"} busy={["queued", "running"].includes(final.status)}>
            Final data export: {final.status === "ready" ? "ready" : final.status === "failed" ? "failed" : "being made"}
          </StatusChip>
        )}
      </div>
      <p className="dt-state-text">{state.text}</p>
      {message && <Banner tone={message.tone}>{message.text}</Banner>}

      {(actions.start || actions.cancel || actions.purge) && (
        <div className="dt-actions" style={{ marginTop: "0.6rem" }}>
          {actions.start && (
            <button type="button" className="btn-secondary" aria-expanded={form === "start"} onClick={() => open("start")}>
              {view.status === "archived" ? "Schedule deletion" : "Archive and delete later"}
            </button>
          )}
          {actions.cancel && (
            <button type="button" className="btn-secondary" aria-expanded={form === "cancel"} onClick={() => open("cancel")}>
              Cancel deletion
            </button>
          )}
          {actions.purge && (
            <button type="button" className="dt-danger" aria-expanded={form === "purge"} onClick={() => open("purge")}>
              Delete store data now
            </button>
          )}
        </div>
      )}
      {actions.purgeBlockedReason && <p className="dt-link-note">{actions.purgeBlockedReason}</p>}

      {form === "start" && (
        <form
          className="dt-form"
          onSubmit={(e) => {
            e.preventDefault();
            run(() => startOffboarding(storeCode, { confirm, reason: reason || null }), "Deletion scheduled. The final data export is being made.");
          }}
        >
          <p className="dt-form-note">
            {view.status === "archived" ? "" : "The store is archived now: nobody can sign in. "}A final data export is made, and the store's data is deleted after {view.grace_days} days unless you cancel.
          </p>
          <label htmlFor={ids.confirm}>
            Type the Store ID ({storeCode})
            <input id={ids.confirm} value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="off" spellCheck={false} />
          </label>
          <label htmlFor={ids.reason}>
            Reason (for the audit log)
            <input id={ids.reason} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} />
          </label>
          <div className="dt-actions">
            <button type="submit" disabled={busy || !confirmMatches(confirm, storeCode)}>
              {busy ? "Working…" : view.status === "archived" ? "Schedule deletion" : "Archive and schedule"}
            </button>
          </div>
        </form>
      )}

      {form === "cancel" && (
        <form
          className="dt-form"
          onSubmit={(e) => {
            e.preventDefault();
            run(() => cancelOffboarding(storeCode, { restore }), restore ? "Deletion cancelled. The store is suspended; resume it on the Stores tab." : "Deletion cancelled. The store stays archived.");
          }}
        >
          <label className="dt-check dt-form-wide">
            <input type="checkbox" checked={restore} onChange={(e) => setRestore(e.target.checked)} />
            Bring the store back (suspended, so you can resume it)
          </label>
          <div className="dt-actions">
            <button type="submit" disabled={busy}>{busy ? "Working…" : "Cancel deletion"}</button>
          </div>
        </form>
      )}

      {form === "purge" && (
        <form
          className="dt-form"
          onSubmit={(e) => {
            e.preventDefault();
            run(() => purgeStoreData(storeCode, { confirm, totp_code: code || null }), "Store data deleted.");
          }}
        >
          <p className="dt-form-note" role="note">
            This drops the store's database and deletes its files and saved keys for good. Download the final data export first.
          </p>
          <label htmlFor={ids.confirm}>
            Type the Store ID ({storeCode})
            <input id={ids.confirm} value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="off" spellCheck={false} />
          </label>
          <label htmlFor={ids.code}>
            Two-step code
            <input id={ids.code} placeholder="If you use two-step sign-in" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} inputMode="numeric" autoComplete="one-time-code" />
          </label>
          <div className="dt-actions">
            <button type="submit" className="dt-danger" disabled={busy || !confirmMatches(confirm, storeCode)}>
              {busy ? "Deleting…" : "Delete store data"}
            </button>
          </div>
        </form>
      )}

      {timeline.length > 0 && (
        <ol className="dt-timeline" aria-label="Off-boarding timeline">
          {timeline.map((t) => (
            <li key={t.key}>
              <div>{t.label}{t.who ? ` · ${t.who}` : ""}</div>
              <div className="dt-timeline-when">{t.when}</div>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
