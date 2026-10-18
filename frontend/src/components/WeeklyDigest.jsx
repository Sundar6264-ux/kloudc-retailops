import { useState } from "react";
import { generateWeeklyDigest } from "../api";
import InfoBlock from "./InfoBlock.jsx";
import { useAuth } from "../AuthContext.jsx";

const SECTIONS = [
  { key: "sales", label: "Sales" },
  { key: "reorder", label: "Reorder needs" },
  { key: "money_at_risk", label: "Money at risk" },
  { key: "vendor_savings", label: "Vendor savings" },
  { key: "reconciliation", label: "Reconciliation" },
  { key: "housekeeping", label: "Data housekeeping" },
];

function fmtMoney(n) {
  return n == null ? "-" : `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export default function WeeklyDigest({ compact = false }) {
  // "Hide sales numbers" users: the server leaves sales out of the digest
  // (no data.sales, narrative.sales null); the copy stops promising it too.
  const { canSeeSales } = useAuth();
  const [digest, setDigest] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  async function handleGenerate() {
    setLoading(true);
    setError(null);
    try {
      const res = await generateWeeklyDigest();
      setDigest(res);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  const sales = digest?.data?.sales;
  const salesDelta =
    sales && sales.prior_week.revenue > 0
      ? Math.round(((sales.this_week.revenue - sales.prior_week.revenue) / sales.prior_week.revenue) * 1000) / 10
      : null;

  // Overview: one slim row until a digest is actually generated, so the page
  // fits on one screen; the full layout below takes over once there's content.
  if (compact && !digest) {
    return (
      <section className="card digest-compact">
        <div className="digest-compact-text">
          <h2>Weekly Digest</h2>
          <span className="muted">
            AI summary of the last 7 days - {canSeeSales ? "sales, " : ""}reorders, dead stock, reconciliation.
          </span>
          {error && <span className="error">{error}</span>}
        </div>
        <button onClick={handleGenerate} disabled={loading}>
          {loading ? "Generating…" : "Generate"}
        </button>
      </section>
    );
  }

  return (
    <section className="card">
      <div className="card-header">
        <h2>Weekly Digest</h2>
        <button onClick={handleGenerate} disabled={loading}>
          {loading ? "Generating…" : digest ? "Regenerate" : "Generate this week's digest"}
        </button>
      </div>
      <InfoBlock
        brief={`An auto-generated summary pulling from every report already in this dashboard - ${
          canSeeSales ? "sales, " : ""
        }reorder needs, dead stock, vendor savings, reconciliation, and barcode data quality.`}
      >
        Every number is computed the normal way by the same report functions used elsewhere in the
        app - nothing here is estimated. Claude (Anthropic's AI) only turns those real numbers into
        a short written summary per section; it never invents a figure. Generated on demand (not on
        a schedule), so it only costs an API call when you actually want one.
      </InfoBlock>

      {error && <p className="error">{error}</p>}

      {!digest && !loading && !error && (
        <p className="muted">Click "Generate this week's digest" to see a summary of the trailing 7 days.</p>
      )}

      {digest && (
        <div className="digest">
          <p className="muted" style={{ fontSize: "0.8rem" }}>
            {digest.week_start} to {digest.week_end}
          </p>
          <p className="digest-headline">{digest.narrative.headline}</p>

          {sales && canSeeSales && (
            <div className="digest-stat-row">
              <span>
                This week: <strong>{fmtMoney(sales.this_week.revenue)}</strong> ({sales.this_week.checks} checks)
              </span>
              <span>
                Last week: <strong>{fmtMoney(sales.prior_week.revenue)}</strong> ({sales.prior_week.checks} checks)
              </span>
              {salesDelta != null && (
                <span className={salesDelta < 0 ? "text-negative" : "text-positive"}>
                  {salesDelta > 0 ? "+" : ""}
                  {salesDelta}%
                </span>
              )}
            </div>
          )}

          {SECTIONS.filter((s) => digest.narrative[s.key] != null && (canSeeSales || s.key !== "sales")).map((s) => (
            <div className="digest-section" key={s.key}>
              <h4>{s.label}</h4>
              <p>{digest.narrative[s.key]}</p>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
