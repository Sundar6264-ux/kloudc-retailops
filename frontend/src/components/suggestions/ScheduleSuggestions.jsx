import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  acceptAllScheduleSuggestions,
  createVendorSchedule,
  dismissScheduleSuggestion,
  getScheduleSuggestions,
  undoDismissScheduleSuggestion,
} from "../../api";
import { Term } from "../../glossary.jsx";
import useDialog from "../flyers/useDialog.js";
import { WEEKDAY_NAMES, formatIsoDay, latestOnly, parseLeadDays, plural, weekdayList } from "./suggestionsModel.js";
import {
  addAllKeys,
  addAllResultText,
  addAllRows,
  confidenceLabel,
  confidencePill,
  cadenceShort,
  cadenceWarning,
  countsLine,
  defaultUnticked,
  defaultsNote,
  deliveriesSeenText,
  evidenceSummary,
  initialWeekdays,
  orderDayText,
  scheduleSummary,
  visibleMissing,
} from "./scheduleSuggestionsModel.js";
import { currentPosName } from "../../store/gating.js";

const SOURCE_SHORT = {
  invoices: "invoice",
  get toast_receipts() {
    return currentPosName();
  },
  purchase_log: "logged",
  stock_rise: "stock rise",
};

// Suggested vendor schedules on the Vendor Order Calendar (automation batch
// item 7, widened Oct 14 - X2): delivery days read off every source (invoice
// uploads, Toast receiving records, logged deliveries, stock rises), highest
// confidence first, each with Add / Edit / Dismiss, plus "Add all". Then
// every other active vendor under "Vendors without a schedule" with Set
// schedule, so none is missing. Every add goes through the server's create,
// which refuses a vendor that already has a schedule - an admin's schedule is
// never overwritten.
export default function ScheduleSuggestions({ version, onAccepted, readOnly }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null); // {text, undoKey?}
  const [addAllOpen, setAddAllOpen] = useState(false);
  const loads = useRef(latestOnly());

  // Dismiss -> quick Undo fires two reloads; only the newer one may land.
  const load = useCallback(() => {
    const id = loads.current.start();
    return getScheduleSuggestions()
      .then((res) => {
        if (!loads.current.isLatest(id)) return;
        setData(res);
        setError(null);
      })
      .catch((err) => {
        if (loads.current.isLatest(id)) setError(err.message);
      });
  }, []);

  useEffect(() => {
    load();
  }, [load, version]);

  async function create(vendor, weekdays, leadDays) {
    await createVendorSchedule(vendor, weekdays, leadDays);
    setNotice({ text: `Added ${vendor}: ${scheduleSummary(weekdays, leadDays)}.` });
    await onAccepted?.();
    await load();
  }

  async function dismiss(s) {
    const res = await dismissScheduleSuggestion(s.vendor, s.delivery_weekdays);
    setNotice({ text: `Dismissed the suggestion for ${s.vendor}.`, undoKey: res.key });
    await load();
  }

  async function undo(key) {
    try {
      await undoDismissScheduleSuggestion(key);
      setNotice(null);
      await load();
    } catch (err) {
      setError(err.message);
    }
  }

  async function addAll(keys) {
    const result = await acceptAllScheduleSuggestions(keys);
    setAddAllOpen(false);
    setNotice({ text: addAllResultText(result) });
    await onAccepted?.();
    await load();
  }

  if (!data && !error) return <p className="muted sugg-loading">Loading suggested schedules…</p>;
  const suggestions = data?.suggestions || [];
  const checks = data?.schedule_checks || [];
  const missing = data?.vendors_without_schedule || [];
  const weeks = Math.round((data?.lookback_days || 84) / 7);
  const rows = addAllRows(suggestions);

  return (
    <div className="sugg-schedules">
      <div className="sugg-schedules-head">
        <h3 className="sugg-schedules-title">
          <Term k="schedule_suggested">Suggested schedules</Term>
          <span className="pill pill-info sugg-count">{suggestions.length}</span>
        </h3>
        {rows.length > 0 && (
          <button type="button" className="sugg-addall" disabled={readOnly} onClick={() => setAddAllOpen(true)}>
            Add all suggestions ({rows.length})
          </button>
        )}
      </div>
      <p className="muted sugg-schedules-intro">
        Read off the last {weeks} weeks of invoice uploads, {currentPosName()} receiving records and logged deliveries,{" "}
        <Term k="schedule_confidence">most confident first</Term>.
        {data?.counts && <span className="sugg-counts"> {countsLine(data.counts)}.</span>}
      </p>
      {notice && (
        <p className="sugg-notice" role="status">
          {notice.text}{" "}
          {notice.undoKey && (
            <button type="button" className="link-button" onClick={() => undo(notice.undoKey)}>
              Undo
            </button>
          )}
        </p>
      )}
      {error && <p className="error">{error}</p>}
      {data && suggestions.length === 0 && (
        <p className="muted sugg-empty">No vendor has a clear delivery pattern without a schedule right now.</p>
      )}
      <ul className="sugg-schedule-list">
        {suggestions.map((s) => (
          <SuggestionRow key={s.key} s={s} onCreate={create} onDismiss={dismiss} readOnly={readOnly} />
        ))}
      </ul>
      {checks.map((c) => (
        <p key={c.vendor} className="sugg-check muted">
          Check: {c.reason}
        </p>
      ))}
      {data && (
        <MissingVendors rows={missing} weeks={weeks} onCreate={create} onUndo={undo} readOnly={readOnly} />
      )}
      {addAllOpen && (
        <AddAllDialog rows={rows} onClose={() => setAddAllOpen(false)} onConfirm={addAll} />
      )}
    </div>
  );
}

function WeekdayPicker({ weekdays, onToggle, idPrefix }) {
  return (
    <div className="vendor-cal-weekday-picker" role="group" aria-label="Delivery days">
      {WEEKDAY_NAMES.map((label, idx) => (
        <label key={label} className="vendor-cal-weekday-option">
          <input
            type="checkbox"
            checked={weekdays.has(idx)}
            onChange={() => onToggle(idx)}
            id={`${idPrefix}-${label}`}
          />
          {label}
        </label>
      ))}
    </div>
  );
}

function useWeekdaySet(initial) {
  const [weekdays, setWeekdays] = useState(() => new Set(initial));
  function toggle(day) {
    setWeekdays((prev) => {
      const next = new Set(prev);
      if (next.has(day)) next.delete(day);
      else next.add(day);
      return next;
    });
  }
  return [weekdays, toggle];
}

function useBusy() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  async function run(action) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(err.message);
    } finally {
      // Usually the row is gone by now, but a quick Undo can keep the same
      // row on screen - it must not stay disabled.
      setBusy(false);
    }
  }
  return { busy, error, run };
}

function DeliveryDates({ s }) {
  const deliveries = s.evidence?.deliveries || [];
  if (!deliveries.length) return null;
  return (
    <details className="sugg-dates">
      <summary>Show the {plural(deliveries.length, "delivery", "deliveries")}</summary>
      <ul>
        {[...deliveries].reverse().map((d) => (
          <li key={d.date} className={s.delivery_weekdays.includes(WEEKDAY_NAMES.indexOf(d.weekday)) ? "" : "muted"}>
            {formatIsoDay(d.date)}
            <span className="sugg-date-sources"> - {(d.sources || []).map((x) => SOURCE_SHORT[x] || x).join(", ")}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}

function SuggestionRow({ s, onCreate, onDismiss, readOnly }) {
  const [editing, setEditing] = useState(false);
  const [weekdays, toggle] = useWeekdaySet(s.delivery_weekdays);
  const [lead, setLead] = useState(String(s.lead_days));
  const { busy, error, run } = useBusy();
  const leadDays = parseLeadDays(lead);
  const chosen = [...weekdays].sort((a, b) => a - b);
  const sources = s.evidence?.sources_text;

  return (
    <li className="sugg-schedule-row">
      <div className="sugg-schedule-text">
        <div className="sugg-schedule-headline">
          <strong className="sugg-vendor-name">{s.vendor}</strong>
          <span className="sugg-days-chip">{weekdayList(s.delivery_weekdays)}</span>
          <span className={`pill ${confidencePill(s.confidence)} sugg-conf`}>
            {confidenceLabel(s.confidence)}
          </span>
        </div>
        <div className="sugg-evidence">
          <strong>{evidenceSummary(s)}</strong>
          {sources ? ` - from ${sources}.` : "."}
        </div>
        <div className="sugg-reason">
          {orderDayText(s)}
          {s.in_inventory === false && ` Not listed as a supplier on any item in ${currentPosName()}.`}
        </div>
        {cadenceWarning(s) && <div className="sugg-cadence">{cadenceWarning(s)}</div>}
        <DeliveryDates s={s} />
      </div>
      {editing && (
        <div className="sugg-schedule-edit">
          <WeekdayPicker weekdays={weekdays} onToggle={toggle} idPrefix={`sugg-${s.key}`} />
          <label className="sugg-lead">
            Order lead time (days before delivery){" "}
            <input type="number" min="0" max="14" value={lead} onChange={(e) => setLead(e.target.value)} />
          </label>
        </div>
      )}
      {error && <p className="error">{error}</p>}
      <div className="sugg-schedule-actions">
        <button
          type="button"
          className="link-button link-danger"
          disabled={busy || readOnly}
          onClick={() => run(() => onDismiss(s))}
        >
          Dismiss
        </button>
        {!editing && (
          <button type="button" className="btn-secondary" disabled={busy || readOnly} onClick={() => setEditing(true)}>
            Edit
          </button>
        )}
        <button
          type="button"
          disabled={busy || readOnly || (editing && (!chosen.length || leadDays == null))}
          onClick={() =>
            run(() =>
              editing ? onCreate(s.vendor, chosen, leadDays) : onCreate(s.vendor, s.delivery_weekdays, s.lead_days),
            )
          }
        >
          {busy ? "Saving…" : editing ? "Save schedule" : "Add schedule"}
        </button>
      </div>
    </li>
  );
}

function MissingVendors({ rows, weeks, onCreate, onUndo, readOnly }) {
  const [expanded, setExpanded] = useState(false);
  const shown = visibleMissing(rows, expanded);
  return (
    <div className="sugg-missing">
      <h3 className="sugg-schedules-title">
        <Term k="schedule_missing">Vendors without a schedule</Term>
        <span className="pill pill-neutral sugg-count">{rows.length}</span>
      </h3>
      {rows.length === 0 ? (
        <p className="muted">Every vendor has a schedule or a suggestion above.</p>
      ) : (
        <p className="muted sugg-schedules-intro">
          Not enough to suggest a day from the last {weeks} weeks. Set one by hand if you know it.
        </p>
      )}
      <ul className="sugg-missing-list">
        {shown.map((row) => (
          <MissingRow key={row.vendor} row={row} onCreate={onCreate} onUndo={onUndo} readOnly={readOnly} />
        ))}
      </ul>
      {rows.length > shown.length && (
        <button type="button" className="link-button sugg-showall" onClick={() => setExpanded(true)}>
          Show all {rows.length} vendors
        </button>
      )}
      {expanded && rows.length > 8 && (
        <button type="button" className="link-button sugg-showall" onClick={() => setExpanded(false)}>
          Show fewer
        </button>
      )}
    </div>
  );
}

function MissingRow({ row, onCreate, onUndo, readOnly }) {
  const [open, setOpen] = useState(false);
  const [weekdays, toggle] = useWeekdaySet(initialWeekdays(row));
  const [lead, setLead] = useState("1");
  const { busy, error, run } = useBusy();
  const leadDays = parseLeadDays(lead);
  const chosen = [...weekdays].sort((a, b) => a - b);
  return (
    <li className="sugg-missing-row">
      <div className="sugg-missing-text">
        <div className="sugg-schedule-headline">
          <strong className="sugg-vendor-name">{row.vendor}</strong>
          <span className="sugg-seen muted">{deliveriesSeenText(row)}</span>
        </div>
        <div className="sugg-reason">
          {row.reason}
          {row.sources_text ? ` From ${row.sources_text}.` : ""}
          {row.in_inventory === false && ` Not listed as a supplier on any item in ${currentPosName()}.`}
        </div>
      </div>
      <div className="sugg-schedule-actions">
        {row.why === "dismissed" && row.dismissed_key && (
          <button type="button" className="link-button" disabled={busy || readOnly} onClick={() => run(() => onUndo(row.dismissed_key))}>
            Show suggestion again
          </button>
        )}
        {!open && (
          <button type="button" className="btn-secondary" disabled={busy || readOnly} onClick={() => setOpen(true)}>
            Set schedule
          </button>
        )}
      </div>
      {open && (
        <div className="sugg-schedule-edit sugg-missing-form">
          <WeekdayPicker weekdays={weekdays} onToggle={toggle} idPrefix={`miss-${row.vendor}`} />
          <label className="sugg-lead">
            Order lead time (days before delivery){" "}
            <input type="number" min="0" max="14" value={lead} onChange={(e) => setLead(e.target.value)} />
          </label>
          <div className="sugg-schedule-actions">
            <button type="button" className="link-button" disabled={busy} onClick={() => setOpen(false)}>
              Cancel
            </button>
            <button
              type="button"
              disabled={busy || readOnly || !chosen.length || leadDays == null}
              onClick={() => run(() => onCreate(row.vendor, chosen, leadDays))}
            >
              {busy ? "Saving…" : "Save schedule"}
            </button>
          </div>
        </div>
      )}
      {error && <p className="error">{error}</p>}
    </li>
  );
}

function AddAllDialog({ rows, onClose, onConfirm }) {
  // High and Medium start ticked; Low (and vendors that deliver every few
  // weeks) start unticked but stay listed.
  const [unticked, setUnticked] = useState(() => defaultUnticked(rows));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const close = () => {
    if (!saving) onClose();
  };
  const dialog = useDialog(close);
  const keys = addAllKeys(rows, unticked);
  const note = defaultsNote(rows);
  const defaults = rows.filter((s) => !unticked.has(s.key) && s.lead_days_source !== "placed_orders").length;

  function flip(key) {
    setUnticked((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  async function confirm() {
    setSaving(true);
    setError(null);
    try {
      await onConfirm(keys);
    } catch (err) {
      setError(err.message);
      setSaving(false);
    }
  }

  return createPortal(
    <div className="fly-modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && close()}>
      <div className="fly-modal sugg-addall-modal" role="dialog" aria-modal="true" aria-labelledby="sugg-addall-title" {...dialog}>
        <div className="fly-modal-head">
          <h3 id="sugg-addall-title">Add {plural(keys.length, "schedule")}?</h3>
          <button type="button" className="fly-icon-btn" aria-label="Close" onClick={close} disabled={saving}>
            ×
          </button>
        </div>
        <div className="fly-modal-body">
          <p style={{ marginTop: 0 }}>
            Each ticked vendor gets the suggested days. A vendor that already has a schedule is skipped - an
            existing schedule is never changed. High and Medium confidence start ticked; untick any you'd rather
            check first.
          </p>
          {note && <p className="sugg-addall-defaults">{note}</p>}
          <ul className="sugg-addall-list">
            {rows.map((s) => (
              <li key={s.key}>
                <label className="sugg-addall-item">
                  <input type="checkbox" checked={!unticked.has(s.key)} onChange={() => flip(s.key)} disabled={saving} />
                  <span className="sugg-addall-name">
                    <strong>{s.vendor}</strong> - {weekdayList(s.delivery_weekdays)}, order{" "}
                    {plural(s.lead_days, "day")} before
                    {s.lead_days_source !== "placed_orders" && <span className="muted"> (default)</span>}
                  </span>
                  <span className={`pill ${confidencePill(s.confidence)} sugg-conf`}>{confidenceLabel(s.confidence)}</span>
                </label>
                {cadenceShort(s) && <div className="sugg-addall-cadence">{cadenceShort(s)}</div>}
              </li>
            ))}
          </ul>
          {defaults > 0 && (
            <p className="muted sugg-addall-note">
              {plural(defaults, "vendor")} marked (default) {defaults === 1 ? "has" : "have"} no placed orders to
              measure the order day from: it starts at 1 day before. To pick another order day, untick it here and
              use Edit on its row.
            </p>
          )}
          {error && <p className="error fly-modal-error">{error}</p>}
        </div>
        <div className="fly-modal-actions">
          <button type="button" className="btn-secondary" onClick={close} disabled={saving}>
            Cancel
          </button>
          <button type="button" onClick={confirm} disabled={saving || keys.length === 0}>
            {saving ? "Adding…" : `Add ${plural(keys.length, "schedule")}`}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
