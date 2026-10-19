import { Fragment, useEffect, useState } from "react";
import {
  getInventoryApiStatus,
  getInventoryApiCompare,
  getInventoryApiNightlyCompare,
  getInventoryApiSwitch,
  runInventoryApiMenuRecheck,
  setInventoryApiSwitch,
} from "../api";
import { ReadinessCard, SwitchCard, SwitchHistory } from "../components/inventoryApi/InventoryApiSwitch.jsx";
import { formatRestaurantTime } from "../format.js";
import { TermInfo } from "../glossary.jsx";
import { currentPosName } from "../store/gating.js";
import { useStoreAllows } from "../store/StoreContext.jsx";
import PageHeader from "../ui/PageHeader.jsx";

// Admin page for the inventory-from-Toast work (backend
// services/inventory_api.py): the "Use Toast for inventory" switch and its
// readiness streak (components/inventoryApi/), the settings in force, the
// nightly side-by-side comparison of an API-built snapshot with that day's
// CSV upload, a "Compare now" against the latest CSV, and the hourly barcode
// re-check.

const fmt = (v) => {
  if (v == null || v === "") return "-";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(2);
  return String(v);
};
const pct = (v) => (v == null ? "-" : `${v}%`);
const when = (iso) => (iso ? formatRestaurantTime(iso) : "-");

function SettingsCard({ status, onRecheck, rechecking, recheckMsg }) {
  const canRecheck = useStoreAllows("menu_recheck");
  const s = status.settings;
  const due = status.upload_due;
  return (
    <section className="card">
      <div className="card-header">
        <h3 style={{ margin: 0 }}>Settings and status</h3>
      </div>
      <table className="invapi-kv">
        <tbody>
          <tr>
            <td className="invapi-key">
              API snapshots + live stock <TermInfo k="inventory_api_snapshot" label={`Nightly ${currentPosName()} snapshot`} />
            </td>
            <td>
              <span className={`pill ${s.inventory_api_snapshots ? "pill-success" : "pill-neutral"}`}>
                {s.inventory_api_snapshots ? "On" : "Off"}
              </span>{" "}
              <span className="muted">
                Set with the switch above.{" "}
                {s.inventory_api_snapshots
                  ? `Each night at ${s.snapshot_time} the snapshot is built from ${currentPosName()} (never over a CSV for that day).`
                  : `Off: each night at ${s.snapshot_time} the API set is only built and compared with that day's CSV - nothing is stored.`}
              </span>
            </td>
          </tr>
          <tr>
            <td className="invapi-key">Latest snapshot</td>
            <td>
              {fmt(status.latest_snapshot_date)} ({status.latest_snapshot_source === "api" ? `from ${currentPosName()}` : "CSV upload"})
            </td>
          </tr>
          <tr>
            <td className="invapi-key">Latest CSV upload</td>
            <td>
              {fmt(status.latest_csv_date)}
              {status.csv_age_days != null && <span className="muted"> - {status.csv_age_days} days old</span>}{" "}
              {due && (
                <span className="term">
                  <span className="pill pill-warn">Upload due</span>
                  <TermInfo k="inventory_upload_due" label="Inventory upload due" />
                </span>
              )}
              <div className="muted">
                Costs, vendor codes and last received dates only come from the CSV. The "Inventory upload due" alert
                goes out once a day at 9 AM when it is {s.inventory_csv_max_age_days} or more days old (0 = never).
              </div>
            </td>
          </tr>
          <tr>
            <td className="invapi-key">
              Barcode re-check <TermInfo k="barcode_menu_recheck" label="Barcode re-check" />
            </td>
            <td>
              {s.menu_barcode_recheck_minutes > 0 ? `Every ${s.menu_barcode_recheck_minutes} min` : "Off"}
              {!s.scheduler_enabled && <span className="muted"> (scheduler off on this server)</span>}
              <div className="muted">
                {status.menu_check.fetched_at ? (
                  <>
                    {currentPosName()}'s menu last changed {when(status.menu_check.menu_last_updated)}; pulled{" "}
                    {when(status.menu_check.fetched_at)} ({status.menu_check.items} items).
                  </>
                ) : (
                  <>{currentPosName()}'s menu hasn't been read yet.</>
                )}{" "}
                Missing and invalid barcodes in Fix Issues use it when it is newer than the latest upload.
              </div>
              {canRecheck && (
                <button className="btn-secondary invapi-btn" disabled={rechecking} onClick={onRecheck}>
                  {rechecking ? "Checking…" : "Check barcodes now"}
                </button>
              )}
              {recheckMsg && <span className="muted"> {recheckMsg}</span>}
            </td>
          </tr>
          <tr>
            <td className="invapi-key">
              Live stock <TermInfo k="live_stock" label="Live stock" />
            </td>
            <td>
              {status.live_stock?.live ? `Live, as of ${when(status.live_stock.as_of)}` : "Snapshot counts"}
              <div className="muted">With the switch on, these read {currentPosName()}'s current count (cached 5 minutes):</div>
              <ul className="invapi-list">
                {status.live_stock_screens.map((s2) => (
                  <li key={s2}>{s2}</li>
                ))}
              </ul>
            </td>
          </tr>
          <tr>
            <td className="invapi-key">Last nightly run</td>
            <td>
              {status.last_snapshot_run ? (
                <>
                  <span className={`pill ${status.last_snapshot_run.status === "success" ? "pill-success" : "pill-failed"}`}>
                    {status.last_snapshot_run.status}
                  </span>{" "}
                  {when(status.last_snapshot_run.started_at)}
                  {status.last_snapshot_run.detail?.stored && (
                    <span className="muted"> - {storedText(status.last_snapshot_run.detail.stored)}</span>
                  )}
                  {status.last_snapshot_run.detail?.error && (
                    <span className="error"> {status.last_snapshot_run.detail.error}</span>
                  )}
                </>
              ) : (
                "Not run yet"
              )}
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  );
}

function storedText(stored) {
  if (stored.skipped === "csv_exists") return "a CSV for that day was already in, so nothing was stored (CSV wins)";
  if (stored.skipped === "switch_off") return "switch off, nothing stored";
  if (stored.skipped === "implausible") {
    return `${currentPosName()}'s answer looked cut short (${stored.reason || "too few items"}), so nothing was stored and the previous snapshot stays in use`;
  }
  if (stored.skipped) return `nothing stored (${stored.skipped})`;
  return `${stored.written ?? 0} items stored`;
}

// On hand, counted items only: matched, explained by what sold after the file
// (nightly runs), and the rest. Absent on comparisons made before Oct 13.
function qtyLine(comparison) {
  const q = comparison.fields.find((f) => f.field === "inventory_quantity");
  if (!q || q.tracked == null) return null;
  const sales = comparison.sales_window != null;
  return (
    <p className="muted">
      On hand, items counted on either side: {q.tracked_same} of {q.tracked} match
      {sales ? `, ${q.explained_by_sales} more differ by exactly what sold after the file` : ""}, {q.unexplained}{" "}
      {sales ? "unexplained" : "differ"} ({pct(q.matched_or_explained_pct)}).
      {q.unexplained_examples?.length > 0 &&
        ` For example: ${q.unexplained_examples
          .slice(0, 5)
          .map((e) => `${e.name} (${currentPosName()} ${e.api_out_of_stock ? "out of stock" : fmt(e.api)}, CSV ${fmt(e.csv)})`)
          .join(", ")}.`}
    </p>
  );
}

function ExamplesTable({ field }) {
  return (
    <table className="invapi-examples">
      <thead>
        <tr>
          <th>Item</th>
          <th className="num">{currentPosName()} API</th>
          <th className="num">CSV</th>
        </tr>
      </thead>
      <tbody>
        {field.examples.map((e) => (
          <tr key={e.item_id}>
            <td className="cell-wrap">{e.name}</td>
            <td className="num">{e.api_out_of_stock ? "out of stock" : fmt(e.api)}</td>
            <td className="num">{fmt(e.csv)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ComparisonTable({ comparison, meta }) {
  const [open, setOpen] = useState(null);
  return (
    <>
      <p className="muted">
        CSV of {comparison.csv_date}: {comparison.items_in_both} items in both, {comparison.only_in_api} only in {currentPosName()}'s
        API, {comparison.only_in_csv} only in the CSV. {comparison.out_of_stock_with_negative_csv} items {currentPosName()} shows as out
        of stock are negative in the CSV - they keep the CSV count on Negative inventory. Carried from the CSV (no API
        source): {comparison.carried_from_csv.join(", ")}.
        {meta?.sales_through && ` Sales are counted through ${meta.sales_through}.`}
      </p>
      {qtyLine(comparison)}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Field</th>
              <th className="num">Same</th>
              <th className="num">Differ</th>
              <th className="num">Same %</th>
              <th className="num">Within 5%</th>
              <th>Examples</th>
            </tr>
          </thead>
          <tbody>
            {comparison.fields.map((f) => (
              <Fragment key={f.field}>
                <tr>
                  <td>{f.label}</td>
                  <td className="num">{f.same}</td>
                  <td className="num">{f.differ}</td>
                  <td className="num">{pct(f.same_pct)}</td>
                  <td className="num">{f.within_5pct_pct != null ? pct(f.within_5pct_pct) : "-"}</td>
                  <td>
                    {f.examples?.length > 0 && (
                      <button
                        className="link-button"
                        aria-expanded={open === f.field}
                        onClick={() => setOpen(open === f.field ? null : f.field)}
                      >
                        {open === f.field ? "Hide examples" : `Examples (${f.examples.length})`}
                      </button>
                    )}
                  </td>
                </tr>
                {open === f.field && (
                  <tr className="invapi-examples-row">
                    <td colSpan={6}>
                      <ExamplesTable field={f} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
      {comparison.only_in_api_examples?.length > 0 && (
        <p className="muted">
          Only in {currentPosName()}'s API (e.g. new since the CSV):{" "}
          {comparison.only_in_api_examples.map((e) => e.name).join(", ")}
        </p>
      )}
      {comparison.only_in_csv_examples?.length > 0 && (
        <p className="muted">
          Only in the CSV: {comparison.only_in_csv_examples.map((e) => e.name).join(", ")}
        </p>
      )}
    </>
  );
}

export default function InventoryApiPage() {
  const [status, setStatus] = useState(null);
  const [nightly, setNightly] = useState(undefined);
  const [live, setLive] = useState(null);
  const [error, setError] = useState(null);
  const [comparing, setComparing] = useState(false);
  const [compareError, setCompareError] = useState(null);
  const [rechecking, setRechecking] = useState(false);
  const [recheckMsg, setRecheckMsg] = useState(null);
  const [sw, setSw] = useState(null);

  function loadStatus() {
    return getInventoryApiStatus()
      .then(setStatus)
      .catch((err) => setError(err.message));
  }

  function loadSwitch() {
    return getInventoryApiSwitch()
      .then(setSw)
      .catch((err) => setError(err.message));
  }

  async function handleSwitch(on, note) {
    const res = await setInventoryApiSwitch(on, note);
    setSw(res);
    await loadStatus();
  }

  useEffect(() => {
    loadStatus();
    loadSwitch();
    getInventoryApiNightlyCompare()
      .then((d) => setNightly(d.nightly))
      .catch(() => setNightly(null));
  }, []);

  async function handleCompare(refresh) {
    setComparing(true);
    setCompareError(null);
    try {
      setLive(await getInventoryApiCompare(refresh));
    } catch (err) {
      setCompareError(err.message);
    } finally {
      setComparing(false);
    }
  }

  async function handleRecheck() {
    setRechecking(true);
    setRecheckMsg(null);
    try {
      const res = await runInventoryApiMenuRecheck();
      setRecheckMsg(res.detail?.changed ? `Menu changed - ${res.detail.items} items re-read.` : `No change in ${currentPosName()}'s menu.`);
      await loadStatus();
    } catch (err) {
      setRecheckMsg(err.message);
    } finally {
      setRechecking(false);
    }
  }

  return (
    <>
      <PageHeader pageId="inventory_from_pos" title={<>Inventory from {currentPosName()}</>}>
        <p className="muted">
          The inventory snapshot built from {currentPosName()}'s API, side by side with the CSV upload, and the switch that puts it
          to use.
        </p>
      </PageHeader>
      {error && <p className="error">{error}</p>}
      <SwitchCard sw={sw} status={status} readiness={status?.readiness} when={when} onChange={handleSwitch} />
      {status?.readiness && <ReadinessCard readiness={status.readiness} when={when} />}
      {status && (
        <SettingsCard status={status} onRecheck={handleRecheck} rechecking={rechecking} recheckMsg={recheckMsg} />
      )}

      <section className="card">
        <div className="card-header">
          <h3 style={{ margin: 0 }}>Nightly comparison</h3>
        </div>
        {nightly === undefined && <p className="muted">Loading…</p>}
        {nightly === null && (
          <p className="muted">
            No nightly comparison yet. It is made at the nightly run when that day's CSV was already uploaded (it
            usually is - the file comes in around 1-3 AM).
          </p>
        )}
        {nightly && (
          <>
            <p className="muted">
              Run {when(nightly.run_at)}. The nightly run is just after {currentPosName()}'s 4 AM close, so on-hand counts should
              match that night's CSV unless something sold after the file was exported.
            </p>
            <ComparisonTable comparison={nightly.comparison} meta={nightly.meta} />
          </>
        )}
      </section>

      <section className="card">
        <div className="card-header">
          <h3 style={{ margin: 0 }}>Compare now</h3>
          <div>
            <button disabled={comparing} onClick={() => handleCompare(Boolean(live))}>
              {comparing ? "Comparing…" : live ? "Compare again" : "Compare now"}
            </button>
          </div>
        </div>
        <p className="muted">
          Builds the snapshot from {currentPosName()} right now (two {currentPosName()} calls, nothing is stored) and compares it with the latest
          CSV. On-hand counts move with every sale since the CSV was exported. {currentPosName()}'s own 7/30/90-day figures in the
          CSV run about two days behind; ours count through yesterday.
        </p>
        {compareError && <p className="error">{compareError}</p>}
        {live && (
          <>
            <p className="muted">Built {when(live.built_at)}.</p>
            <ComparisonTable comparison={live.comparison} meta={live.meta} />
          </>
        )}
      </section>

      <SwitchHistory history={sw?.history} when={when} />
    </>
  );
}
