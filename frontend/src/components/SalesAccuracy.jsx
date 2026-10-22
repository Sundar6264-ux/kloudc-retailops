import { useCallback, useEffect, useRef, useState } from "react";
import { getSalesAccuracy, runSalesAccuracy } from "../api";
import { formatRestaurantTime } from "../format.js";
import InfoBlock from "./InfoBlock.jsx";
import { missingKeysLine } from "./salesAccuracyModel.js";
import { SALES_ACCURACY_TERMS, Term } from "../glossary.jsx";
import { currentPosName } from "../store/gating.js";

// Admin-only card under Items Sold: each recent business date's stored sales
// next to Toast's own item-level total (Toast Analytics), and what the
// re-pull job corrected (backend/app/services/sales_accuracy.py).

const REASONS = {
  item_net: "item sales differed",
  item_qty: "quantities differed",
  orders: "order count differed",
  get analytics_changed() {
    return `${currentPosName()} changed this day`;
  },
  unpaid: "unpaid items to recheck",
  never_synced: "not synced yet",
};

// The server's cap for one run (sales_accuracy.MAX_DAYS).
const MAX_CHECK_DAYS = 60;

const VERDICTS = {
  match: { label: "Matches", cls: "pill-success" },
  fixed: { label: "Fixed", cls: "pill-success" },
  mismatch: { label: "Differs", cls: "pill-warn" },
};

function money(v) {
  if (v == null) return "-";
  const sign = v < 0 ? "-" : "";
  return `${sign}$${Math.abs(v).toFixed(2)}`;
}

function reasonText(row) {
  const parts = (row.reasons || []).map((r) => REASONS[r] || r);
  if (row.waiting) parts.push(`waiting for ${currentPosName()} to update`);
  return parts.join(", ") || "-";
}

export default function SalesAccuracy() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [starting, setStarting] = useState(false);
  const [message, setMessage] = useState(null);
  const pollTimer = useRef(null);

  const load = useCallback(async () => {
    try {
      const result = await getSalesAccuracy(30);
      setData(result);
      setError(null);
      return result;
    } catch (err) {
      setError(err.message);
      return null;
    }
  }, []);

  // While a check runs on the server, look again every 5 s (up to 5 min).
  const poll = useCallback(
    (left = 60) => {
      clearTimeout(pollTimer.current);
      pollTimer.current = setTimeout(async () => {
        const result = await load();
        if (result?.running && left > 1) poll(left - 1);
        else if (result && !result.running) setMessage("Check finished.");
      }, 5000);
    },
    [load]
  );

  useEffect(() => {
    load().then((r) => r?.running && poll());
    return () => clearTimeout(pollTimer.current);
  }, [load, poll]);

  // days: undefined = the server's usual window (SALES_REPULL_DAYS); 60 =
  // the longest it allows, for correcting older history once after deploy.
  async function checkNow(days) {
    setStarting(true);
    setMessage(null);
    try {
      await runSalesAccuracy(days);
      setMessage(
        days
          ? `Checking the last ${days} days with ${currentPosName()} - this takes several minutes.`
          : `Checking with ${currentPosName()} - this takes about a minute.`
      );
      setData((d) => (d ? { ...d, running: true } : d));
      poll();
    } catch (err) {
      setMessage(err.message);
    } finally {
      setStarting(false);
    }
  }

  const lastRun = data?.runs?.[0];

  return (
    <section className="card">
      <div className="card-header">
        <h2 style={{ margin: 0 }}>Sales accuracy</h2>
        {data?.configured && (
          <div className="sales-accuracy-actions">
            <button
              type="button"
              className="link-button"
              onClick={() => checkNow(MAX_CHECK_DAYS)}
              disabled={starting || data.running}
              title="Re-check older days too, e.g. once after the first deploy"
            >
              Check the last {MAX_CHECK_DAYS} days
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => checkNow()}
              disabled={starting || data.running}
            >
              {data.running ? "Checking…" : "Check now"}
            </button>
          </div>
        )}
      </div>
      <InfoBlock brief={`Admins only. Each recent day's stored sales next to ${currentPosName()}'s own item totals; days that differ are copied again from ${currentPosName()} automatically.`}>
        <p style={{ marginTop: 0 }}>
          {currentPosName()} keeps changing days after they close: a check voided or discounted the next
          morning, an item taken off an order, a late payment. A few times a day
          {data?.times?.length ? ` (${data.times.join(", ")})` : ""} the app asks {currentPosName()}'s Analytics
          for the last {data?.window_days ?? 14} days and compares each day's item sales (by
          {currentPosName()}'s business date, add-ons inside their item, discounts taken off) with what we have
          stored. A day that differs, or that {currentPosName()} changed since the last check, is copied again from
          {currentPosName()}'s orders; the rest are left alone.
        </p>
        <p style={{ marginBottom: 0 }}>
          "Ours" is the stored total before and after that re-sync. A day can still differ for a
          few hours after an edit: {currentPosName()} says its Analytics catch up within 2-4 hours (up to 6
          days for an order edited long after its date). Such a day is checked again on the next
          run. This compares {currentPosName()}'s business dates; the Items Sold report above counts by
          payment date, so its daily totals can differ slightly from these.
        </p>
      </InfoBlock>

      {error && <p className="error">{error}</p>}
      {message && <p className="muted">{message}</p>}
      {data && !data.configured && (
        <p className="muted">
          {missingKeysLine(data.warning, currentPosName())}
          {data.last_recheck?.detail ? ` Last re-check: ${data.last_recheck.detail}` : ""}
        </p>
      )}
      {lastRun && (
        <p className="muted" style={{ margin: "0.5rem 0" }}>
          Last check {formatRestaurantTime(lastRun.started_at)}
          {lastRun.status === "failed" ? " (failed)" : ""}: {lastRun.detail}
        </p>
      )}

      {data && data.days.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Business date</th>
                <th className="num">{currentPosName()} item sales</th>
                <th className="num">Ours before</th>
                <th className="num">Ours after</th>
                <th className="num">Difference</th>
                <th>Result</th>
                <th>Why re-checked</th>
              </tr>
            </thead>
            <tbody>
              {data.days.map((row) => {
                const v = VERDICTS[row.verdict] || { label: row.verdict || "-", cls: "pill-neutral" };
                return (
                  <tr key={row.business_date}>
                    <td>{row.business_date}</td>
                    <td className="num">{money(row.toast_item_net)}</td>
                    <td className="num">{money(row.ours_before)}</td>
                    <td className="num">{row.resynced ? money(row.ours_after) : "-"}</td>
                    <td className="num">{money(row.diff_after)}</td>
                    <td>
                      <Term k={SALES_ACCURACY_TERMS[row.verdict]}>
                        <span className={`pill ${v.cls}`}>{v.label}</span>
                      </Term>
                    </td>
                    <td className="cell-wrap">
                      {row.waiting ? (
                        <Term k={SALES_ACCURACY_TERMS.waiting} label={`Waiting for ${currentPosName()}`}>
                          {reasonText(row)}
                        </Term>
                      ) : (
                        reasonText(row)
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {data && data.configured && data.days.length === 0 && (
        <p className="muted">No checks yet. The first one runs at the next scheduled time, or press Check now.</p>
      )}
    </section>
  );
}
