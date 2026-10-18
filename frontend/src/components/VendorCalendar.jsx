import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  createVendorSchedule,
  deleteVendorSchedule,
  getSuppliers,
  getVendorCalendar,
  skipVendorOrder,
  unskipVendorOrder,
  getVendorSchedules,
  postOrderAction,
  undoOrderPlacedOutside,
  undoOrderSkip,
  updateVendorSchedule,
} from "../api";
import InfoBlock from "./InfoBlock.jsx";
import { Term } from "../glossary.jsx";
import { useAuth } from "../AuthContext.jsx";
import ScheduleSuggestions from "./suggestions/ScheduleSuggestions.jsx";
import { UndoBar } from "./CriticalDismissals.jsx";
import {
  NOTE_MAX,
  chipLabel,
  dayText,
  handledOf,
  handledText,
  menuActions,
  noteProblem,
  orderDueCount,
  orderDueTitle,
  undoRequest,
} from "./orderActions/orderActionsModel.js";

const WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function toDateStr(d) {
  return d.toISOString().slice(0, 10);
}

// Monday-first grid covering every full week that touches this month, so
// the calendar never shows a partial week at either edge.
function buildMonthGrid(year, month) {
  const first = new Date(year, month, 1);
  const last = new Date(year, month + 1, 0);
  const firstWeekday = (first.getDay() + 6) % 7; // Mon=0 .. Sun=6
  const lastWeekday = (last.getDay() + 6) % 7;
  const start = new Date(first);
  start.setDate(start.getDate() - firstWeekday);
  const end = new Date(last);
  end.setDate(end.getDate() + (6 - lastWeekday));

  const days = [];
  const cursor = new Date(start);
  while (cursor <= end) {
    days.push(new Date(cursor));
    cursor.setDate(cursor.getDate() + 1);
  }
  return days;
}

function eventsByDate(events) {
  const map = {};
  events.forEach((e) => {
    (map[e.date] = map[e.date] || []).push(e);
  });
  return map;
}

function VendorScheduleForm({ availableSuppliers, onAdd }) {
  const [vendor, setVendor] = useState("");
  const [weekdays, setWeekdays] = useState(() => new Set());
  const [leadDays, setLeadDays] = useState(2);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  function toggleWeekday(idx) {
    setWeekdays((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (!vendor || weekdays.size === 0) return;
    setSaving(true);
    setError(null);
    try {
      await onAdd(vendor, [...weekdays].sort((a, b) => a - b), Number(leadDays));
      setVendor("");
      setWeekdays(new Set());
      setLeadDays(2);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="recon-form" style={{ marginTop: "0.75rem" }}>
      <label className="recon-form-field recon-form-field-wide">
        Vendor
        <select value={vendor} onChange={(e) => setVendor(e.target.value)} required>
          <option value="">Select a vendor…</option>
          {availableSuppliers.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>
      <div className="recon-form-field recon-form-field-wide">
        Delivery day(s)
        <div className="vendor-cal-weekday-picker">
          {WEEKDAY_LABELS.map((label, idx) => (
            <label key={label} className="vendor-cal-weekday-option">
              <input type="checkbox" checked={weekdays.has(idx)} onChange={() => toggleWeekday(idx)} />
              {label}
            </label>
          ))}
        </div>
      </div>
      <label className="recon-form-field">
        Order lead time (days before delivery)
        <input type="number" min="0" max="30" value={leadDays} onChange={(e) => setLeadDays(e.target.value)} />
      </label>
      <div className="recon-form-submit">
        <button type="submit" disabled={!vendor || weekdays.size === 0 || saving}>
          {saving ? "Saving…" : "Add vendor schedule"}
        </button>
      </div>
      {error && <p className="error">{error}</p>}
    </form>
  );
}

function ScheduleList({ schedules, togglingId, deletingId, onToggleActive, onDelete }) {
  if (schedules.length === 0) {
    return <p className="muted">No vendor schedules yet - add one below to start building the calendar.</p>;
  }
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Vendor</th>
            <th>Delivery days</th>
            <th>
              <Term k="lead_time">Order lead time</Term>
            </th>
            <th>
              <Term k="schedule_paused" label="Active / Paused">Active</Term>
            </th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {schedules.map((s) => (
            <tr key={s.id}>
              <td>{s.vendor}</td>
              <td>{s.delivery_weekdays.map((d) => WEEKDAY_LABELS[d]).join(", ") || "-"}</td>
              <td>{s.lead_days} day(s) before</td>
              <td>
                <input
                  type="checkbox"
                  checked={s.active}
                  disabled={togglingId === s.id}
                  onChange={() => onToggleActive(s)}
                />
              </td>
              <td>
                <button
                  type="button"
                  className="link-button link-danger"
                  disabled={deletingId === s.id}
                  onClick={() => onDelete(s.id)}
                >
                  {deletingId === s.id ? "Deleting…" : "Delete"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Clicking an order-due chip opens a small menu: review the order in
// Supplier Projection, or deal with it for this cycle (W1): Order already
// placed / Skip this week / Other (with a note), and Undo. Skip is open to
// everyone with the calendar (as before); the rest is admin-only.
function OtherNoteForm({ busy, onSave, onCancel }) {
  const [note, setNote] = useState("");
  const problem = noteProblem(note, true);
  return (
    <form
      className="vendor-cal-other-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (!problem) onSave(note.trim());
      }}
    >
      <label>
        Why? (shown to everyone)
        <textarea
          rows={2}
          maxLength={NOTE_MAX}
          value={note}
          autoFocus
          placeholder="e.g. Vendor closed this week"
          onChange={(e) => setNote(e.target.value)}
        />
      </label>
      <div className="vendor-cal-other-actions">
        <button type="button" className="link-button" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button type="submit" className="vendor-cal-other-save" disabled={busy || !!problem}>
          {busy ? "Saving…" : "Hide this order"}
        </button>
      </div>
    </form>
  );
}

const UNDO_LABELS = {
  skip: "Undo skip",
  other: "Undo Other",
  placed_outside: "Undo order placed",
};

function EventChip({ event, menuOpen, onToggleMenu, onReview, onAction, actions, busy }) {
  const [otherOpen, setOtherOpen] = useState(false);
  useEffect(() => {
    if (!menuOpen) setOtherOpen(false);
  }, [menuOpen]);
  if (event.type === "order_due") {
    const handled = handledOf(event);
    const label = chipLabel(event);
    const state = handled
      ? handled.kind === "skip" || handled.kind === "other"
        ? " vendor-cal-chip-skipped"
        : " vendor-cal-chip-ordered"
      : "";
    return (
      <div className="vendor-cal-chip-wrap">
        <button
          type="button"
          className={`vendor-cal-chip vendor-cal-chip-order${event.urgent ? " vendor-cal-chip-urgent" : ""}${state}`}
          onClick={onToggleMenu}
          aria-expanded={menuOpen}
          title={
            handled
              ? `${handledText(handled)} - the order for the ${event.for_delivery_date} delivery.`
              : orderDueTitle(event)
          }
        >
          {label}: {event.vendor}
          {orderDueCount(event)}
        </button>
        {menuOpen && (
          <div className="vendor-cal-menu" role="menu">
            <div className="vendor-cal-menu-title">
              {event.vendor} · delivery {dayText(event.for_delivery_date)}
            </div>
            {handled && <div className="vendor-cal-menu-handled">{handledText(handled)}</div>}
            <button type="button" className="vendor-cal-menu-item" onClick={onReview}>
              Review order →
            </button>
            {otherOpen ? (
              <OtherNoteForm
                busy={busy}
                onCancel={() => setOtherOpen(false)}
                onSave={(note) => onAction("other", note)}
              />
            ) : (
              actions.map((a) =>
                a === "placed" ? (
                  <button key={a} type="button" className="vendor-cal-menu-item" disabled={busy} onClick={() => onAction("placed")}>
                    {busy ? "Saving…" : "Order already placed"}
                  </button>
                ) : a === "skip" ? (
                  <button
                    key={a}
                    type="button"
                    className="vendor-cal-menu-item vendor-cal-menu-danger"
                    disabled={busy}
                    onClick={() => onAction("skip")}
                  >
                    {busy ? "Saving…" : "Skip this week"}
                  </button>
                ) : a === "other" ? (
                  <button key={a} type="button" className="vendor-cal-menu-item" disabled={busy} onClick={() => setOtherOpen(true)}>
                    Other…
                  </button>
                ) : (
                  <button key={a} type="button" className="vendor-cal-menu-item" disabled={busy} onClick={() => onAction("undo")}>
                    {busy ? "Undoing…" : UNDO_LABELS[handled?.kind] || "Undo"}
                  </button>
                )
              )
            )}
          </div>
        )}
      </div>
    );
  }
  const statusLabel = {
    upcoming: "expected",
    delivered: "confirmed",
    missed: "missed",
    skipped: event.skip_reason === "other" ? "hidden" : "skipped",
  }[event.status];
  return (
    <span
      className={`vendor-cal-chip vendor-cal-chip-delivery vendor-cal-chip-${event.status}`}
      title={event.skip_note ? `Other: ${event.skip_note}` : undefined}
    >
      Delivery: {event.vendor} ({statusLabel})
    </span>
  );
}

export default function VendorCalendar() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [suppliers, setSuppliers] = useState([]);
  const [schedules, setSchedules] = useState([]);
  const [loadingSchedules, setLoadingSchedules] = useState(true);
  const [togglingId, setTogglingId] = useState(null);
  const [deletingId, setDeletingId] = useState(null);
  const [error, setError] = useState(null);

  const today = useMemo(() => new Date(), []);
  const [year, setYear] = useState(today.getFullYear());
  const [month, setMonth] = useState(today.getMonth());

  const [events, setEvents] = useState([]);
  const [loadingCalendar, setLoadingCalendar] = useState(true);
  const [openMenuKey, setOpenMenuKey] = useState(null);
  const [skipBusy, setSkipBusy] = useState(false);
  const [undo, setUndo] = useState(null); // {message, request}
  const isAdmin = !!user?.is_admin;
  const demo = !!user?.demo;
  const [calendarVersion, setCalendarVersion] = useState(0);

  useEffect(() => {
    getSuppliers()
      .then((res) => setSuppliers(res.suppliers))
      .catch(() => setSuppliers([]));
  }, []);

  function loadSchedules() {
    setLoadingSchedules(true);
    return getVendorSchedules()
      .then((res) => setSchedules(res.items))
      .catch((err) => setError(err.message))
      .finally(() => setLoadingSchedules(false));
  }

  useEffect(() => {
    loadSchedules();
  }, []);

  const monthGrid = useMemo(() => buildMonthGrid(year, month), [year, month]);

  useEffect(() => {
    if (monthGrid.length === 0) return;
    setLoadingCalendar(true);
    const start = toDateStr(monthGrid[0]);
    const end = toDateStr(monthGrid[monthGrid.length - 1]);
    getVendorCalendar(start, end)
      .then((res) => setEvents(res.events))
      .catch((err) => setError(err.message))
      .finally(() => setLoadingCalendar(false));
    // monthGrid is recomputed (new array) whenever year/month changes, and
    // its first/last day are all this effect actually depends on.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [year, month, calendarVersion]);

  // Close an open order menu on any click outside it.
  useEffect(() => {
    if (!openMenuKey) return undefined;
    function handleDocClick(e) {
      if (!e.target.closest(".vendor-cal-chip-wrap")) setOpenMenuKey(null);
    }
    document.addEventListener("mousedown", handleDocClick);
    return () => document.removeEventListener("mousedown", handleDocClick);
  }, [openMenuKey]);

  // Runs an Undo request from the model (undoRequest) - admins use the
  // order-actions routes; anyone else undoes a skip the old way.
  const runUndo = useCallback(
    async (req) => {
      if (req.kind === "placed") return undoOrderPlacedOutside(req.orderId);
      if (isAdmin) return undoOrderSkip(req.vendor, req.deliveryDate);
      return unskipVendorOrder(req.vendor, req.deliveryDate);
    },
    [isAdmin]
  );

  async function handleAction(event, action, note) {
    setSkipBusy(true);
    setError(null);
    const when = dayText(event.for_delivery_date);
    try {
      if (action === "undo") {
        const req = undoRequest(event);
        if (req) await runUndo(req);
        setUndo({ message: `${event.vendor}: the order for ${when} is due again.`, request: null });
      } else if (action === "skip" && !isAdmin) {
        await skipVendorOrder(event.vendor, event.for_delivery_date);
        setUndo({
          message: `${event.vendor}: order for ${when} skipped.`,
          request: { kind: "skip", vendor: event.vendor, deliveryDate: event.for_delivery_date },
        });
      } else {
        const result = await postOrderAction({
          vendor: event.vendor,
          action,
          delivery_date: event.for_delivery_date,
          ...(note ? { note } : {}),
        });
        setUndo({
          message: result.message,
          request: undoRequest({ ...event, vendor: result.vendor }, result.handled),
        });
      }
      setOpenMenuKey(null);
      setCalendarVersion((v) => v + 1);
    } catch (err) {
      setError(err.message);
    } finally {
      setSkipBusy(false);
    }
  }

  const closeUndo = useCallback(() => setUndo(null), []);

  const eventMap = useMemo(() => eventsByDate(events), [events]);
  const availableSuppliers = useMemo(() => {
    const existing = new Set(schedules.map((s) => s.vendor));
    return suppliers.filter((s) => !existing.has(s));
  }, [suppliers, schedules]);

  function goToMonth(delta) {
    const d = new Date(year, month + delta, 1);
    setYear(d.getFullYear());
    setMonth(d.getMonth());
  }

  async function handleAddSchedule(vendor, weekdays, leadDays) {
    await createVendorSchedule(vendor, weekdays, leadDays);
    await loadSchedules();
  }

  async function handleToggleActive(schedule) {
    setTogglingId(schedule.id);
    try {
      await updateVendorSchedule(schedule.id, { active: !schedule.active });
      await loadSchedules();
    } catch (err) {
      setError(err.message);
    } finally {
      setTogglingId(null);
    }
  }

  async function handleDelete(id) {
    setDeletingId(id);
    try {
      await deleteVendorSchedule(id);
      await loadSchedules();
    } catch (err) {
      setError(err.message);
    } finally {
      setDeletingId(null);
    }
  }

  function handleOrderDueClick(event) {
    navigate("/supplier-projection", {
      state: { presetSupplier: event.vendor, presetHorizonDays: event.horizon_days },
    });
  }

  const monthLabel = new Date(year, month, 1).toLocaleDateString(undefined, { month: "long", year: "numeric" });
  const todayStr = toDateStr(today);

  return (
    <>
      <section className="card">
        <h2>Vendor delivery schedule</h2>
        <InfoBlock brief="One-time setup: tell it which day(s) each vendor delivers and how far ahead the order needs to go in. Everything else on the calendar below is figured out automatically.">
          This is the only manual step. Once a vendor's delivery day(s) and order lead time are set,
          the calendar regenerates itself indefinitely with no further upkeep: what to order and how
          much (based on real sales history and current on-hand, covering exactly the days until that
          vendor's next delivery - not a generic fixed window), whether a delivery was actually
          confirmed (matched against Invoice Submission and logged purchases), and which expected
          deliveries were missed.
        </InfoBlock>
        {error && <p className="error">{error}</p>}
        {!loadingSchedules && (
          <ScheduleList
            schedules={schedules}
            togglingId={togglingId}
            deletingId={deletingId}
            onToggleActive={handleToggleActive}
            onDelete={handleDelete}
          />
        )}
        <ScheduleSuggestions
          version={schedules.length}
          readOnly={!!user?.demo}
          onAccepted={async () => {
            await loadSchedules();
            setCalendarVersion((v) => v + 1);
          }}
        />
        <VendorScheduleForm availableSuppliers={availableSuppliers} onAdd={handleAddSchedule} />
      </section>

      <section className="card">
        <div className="card-header">
          <h2>{monthLabel}</h2>
          <div>
            <button type="button" className="link-button" onClick={() => goToMonth(-1)}>
              ← Previous
            </button>{" "}
            <button type="button" className="link-button" onClick={() => goToMonth(1)}>
              Next →
            </button>
          </div>
        </div>
        <div className="vendor-cal-legend muted">
          <Term k="cal_order_due">Order due</Term>
          <Term k="cal_items_needed">(n) = items needed (5+), (n low) = fewer, not an order yet</Term>
          <Term k="urgency_ordered">ordered</Term>
          <Term k="cal_upcoming">Delivery expected</Term>
          <Term k="cal_delivered">confirmed</Term>
          <Term k="cal_missed">missed</Term>
          <Term k="cal_skipped">skipped</Term>
          <Term k="order_placed_outside">already placed</Term>
          <Term k="order_due_other">hidden (Other)</Term>
        </div>
        {loadingCalendar && <p className="muted">Loading…</p>}
        {/* Phones: the month grid scrolls sideways inside this box, not the whole page. */}
        <div className="vendor-cal-grid-scroll">
        <div className="vendor-cal-grid">
          {WEEKDAY_LABELS.map((label) => (
            <div key={label} className="vendor-cal-weekday-header">
              {label}
            </div>
          ))}
          {monthGrid.map((day) => {
            const dateStr = toDateStr(day);
            const inMonth = day.getMonth() === month;
            const dayEvents = eventMap[dateStr] || [];
            return (
              <div
                key={dateStr}
                className={`vendor-cal-day${inMonth ? "" : " vendor-cal-day-outside"}${
                  dateStr === todayStr ? " vendor-cal-day-today" : ""
                }`}
              >
                <div className="vendor-cal-day-num">{day.getDate()}</div>
                {dayEvents.map((event, idx) => {
                  const menuKey = `${event.vendor}|${event.for_delivery_date}`;
                  return (
                    <EventChip
                      key={idx}
                      event={event}
                      menuOpen={event.type === "order_due" && openMenuKey === menuKey}
                      onToggleMenu={() => setOpenMenuKey((k) => (k === menuKey ? null : menuKey))}
                      onReview={() => handleOrderDueClick(event)}
                      onAction={(action, note) => handleAction(event, action, note)}
                      actions={menuActions(event, { isAdmin, demo })}
                      busy={skipBusy}
                    />
                  );
                })}
              </div>
            );
          })}
        </div>
        </div>
        {undo && (
          <UndoBar
            key={undo.message}
            message={undo.message}
            warning={null}
            onUndo={
              undo.request
                ? async () => {
                    try {
                      await runUndo(undo.request);
                      setUndo(null);
                      setCalendarVersion((v) => v + 1);
                    } catch (err) {
                      setUndo(null);
                      setError(err.message);
                    }
                  }
                : undefined
            }
            onClose={closeUndo}
          />
        )}
      </section>
    </>
  );
}
