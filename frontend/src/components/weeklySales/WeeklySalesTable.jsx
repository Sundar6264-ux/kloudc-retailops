// Week-by-week sales with each week's average on-hand count beside it
// ("Jun 1 - Jun 7 · sold 10 · avg on hand 24 (6 of 7 days)"). Shared by
// Supplier Projection, Reorder Candidates and Fix Issues' item details.
import { useEffect, useState } from "react";
import { getItemWeeklySales } from "../../api";
import { useAuth } from "../../AuthContext.jsx";
import { TermInfo } from "../../glossary.jsx";
import {
  formatSold,
  hasOnHandFields,
  hasRevenue,
  noSales,
  onHandCell,
  weekLabel,
  weekSummary,
} from "./weeklySalesModel.js";

function fmtMoney(v) {
  if (v === null || v === undefined || v === "") return "-";
  const n = Number(v);
  return Number.isFinite(n) ? `$${n.toFixed(2)}` : "-";
}

export function WeeklySalesTable({ weeks, emptyText, includesContainer = false, caption }) {
  const { canSeeSales } = useAuth();
  if (!Array.isArray(weeks) || weeks.length === 0) {
    return <span className="muted">{emptyText || "No sales recorded for this item in the selected window."}</span>;
  }
  const showRevenue = canSeeSales && hasRevenue(weeks);
  const showOnHand = hasOnHandFields(weeks);
  return (
    <div className="ws-wrap">
      {noSales(weeks) && <p className="muted ws-note">No sales in this window - stock counts only.</p>}
      <table className="sp-weekly-table ws-table">
        {caption && <caption className="ws-caption">{caption}</caption>}
        <thead>
          <tr>
            <th scope="col">Week</th>
            <th scope="col" className="num">
              Sold
            </th>
            {showOnHand && (
              <th scope="col" className="num">
                <span className="ws-th-term">
                  Avg on hand
                  <TermInfo k="avg_on_hand" label="Avg on hand" />
                </span>
              </th>
            )}
            {showRevenue && (
              <th scope="col" className="num">
                Revenue
              </th>
            )}
          </tr>
        </thead>
        <tbody>
          {weeks.map((w) => {
            const oh = onHandCell(w);
            return (
              <tr key={w.week_start} title={weekSummary(w)}>
                <td className="ws-week">{weekLabel(w)}</td>
                <td className="num">{formatSold(w.quantity)}</td>
                {showOnHand && (
                  <td className={`num ws-onhand ${oh.negative ? "flag" : ""}`}>
                    <span className={oh.known ? undefined : "muted"}>{oh.value}</span>
                    {oh.coverage && <span className="muted ws-coverage"> {oh.coverage}</span>}
                  </td>
                )}
                {showRevenue && <td className="num">{fmtMoney(w.revenue)}</td>}
              </tr>
            );
          })}
        </tbody>
      </table>
      {showOnHand && includesContainer && (
        <p className="muted ws-note">On hand includes the Container count.</p>
      )}
    </div>
  );
}

// Loads one item's breakdown itself (Reorder Candidates, Fix Issues details).
// `hideWhenForbidden`: someone without any page that grants the endpoint
// gets nothing rather than a red "no access" line.
export function ItemWeeklySales({ itemId, start, end, emptyText, hideWhenForbidden = false }) {
  const [state, setState] = useState({ loading: true });
  useEffect(() => {
    let live = true;
    setState({ loading: true });
    getItemWeeklySales(itemId, start, end, { includeEmptyWeeks: true })
      .then((res) => live && setState({ weeks: res.weeks || [], container: !!res.on_hand_includes_container }))
      .catch((err) => live && setState({ error: err.message || "Could not load weekly sales.", status: err.status }));
    return () => {
      live = false;
    };
  }, [itemId, start, end]);
  if (state.loading) return <span className="muted">Loading weekly sales…</span>;
  if (state.error) {
    if (hideWhenForbidden && state.status === 403) return null;
    return <span className="error">{state.error}</span>;
  }
  return <WeeklySalesTable weeks={state.weeks} emptyText={emptyText} includesContainer={state.container} />;
}
