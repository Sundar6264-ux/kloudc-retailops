import { useState } from "react";
import { getInsightsCompare } from "../../api";
import { useAuth } from "../../AuthContext.jsx";
import { storeDateOffsetStr } from "../../format.js";
import { TermInfo } from "../../glossary.jsx";
import { InsightBody, InsightsPage, useInsight } from "../../components/insights/InsightsCommon.jsx";
import {
  delta,
  fmtCount,
  fmtMoney,
  isNum,
  hourLabel,
  longDate,
  pctChange,
  shortDate,
} from "../../components/insights/insightsModel.js";
import { currentPosName } from "../../store/gating.js";

// The day's figures; money ones are left out for "Hide sales numbers" users
// (the server sends them as null / leaves them out too).
const METRICS = [
  { key: "net_sales", label: "Net sales", money: true, term: "insights_net_sales", fmt: (v) => fmtMoney(v) },
  { key: "orders", label: "Orders", term: "insights_orders", fmt: (v) => fmtCount(v) },
  { key: "avg_check", label: "Average check", money: true, term: "insights_avg_check", fmt: (v) => fmtMoney(v, { cents: true }), note: "Net sales per order" },
  { key: "guests", label: "Guests", term: "insights_guests", fmt: (v) => fmtCount(v), get note() {
    return `${currentPosName()}'s guest count`;
  } },
];

const COMPARISONS = [
  { key: "vs_last_week", label: "Same day last week", term: "insights_vs_last_week" },
  { key: "vs_4wk_avg", label: "4-week average", term: "insights_vs_4wk_avg" },
  { key: "vs_last_year", label: "Same day last year", term: "insights_vs_last_year" },
];

// A comparison's value for one metric, and the % change to today. The
// contract's comparisons carry {date, net_sales, pct}; other metrics are
// used when the server sends them (orders, avg_check, guests, *_pct).
function compareCell(data, comp, metric) {
  const c = data?.[comp];
  if (!c) return null;
  const base = c[metric];
  let pct = metric === "net_sales" && isNum(c.pct) ? c.pct : c[`${metric}_pct`];
  if (!isNum(pct)) pct = pctChange(data[metric], base);
  if (!isNum(base) && !isNum(pct)) return null;
  return { base, pct, date: c.date };
}

function Delta({ pct, against }) {
  const d = delta(pct);
  if (d.dir === "none") return <span className="muted">-</span>;
  return (
    <span className={`ins-delta ins-delta-${d.dir}`}>
      <span aria-hidden="true">{d.text}</span>
      <span className="ins-sr-only">{`${d.words}${against ? ` against ${against}` : ""}`}</span>
    </span>
  );
}

export default function DailyComparePage() {
  const { canSeeSales } = useAuth();
  const yesterday = storeDateOffsetStr(-1);
  const [date, setDate] = useState(yesterday);
  const state = useInsight(() => getInsightsCompare(date), [date]);
  const data = state.data;

  const metrics = METRICS.filter((m) => (!m.money || canSeeSales) && isNum(data?.[m.key]));
  const comps = COMPARISONS.filter((c) => c.key !== "vs_last_year" || data?.vs_last_year);
  // The table only has columns the server compares (net sales and orders, as built).
  const tableMetrics = metrics.filter((m) => comps.some((c) => compareCell(data, c.key, m.key)));
  const weeksUsed = data?.vs_4wk_avg?.weeks_used;
  const today = storeDateOffsetStr(0);

  return (
    <InsightsPage
      title="Daily compare"
      brief="One day's sales and orders next to the same day last week, the 4-week average and last year."
      info={{
        brief: "Pick a day; yesterday is the default. ▲ means higher than the day it's compared with, ▼ lower.",
        body: (
          <>
            <p style={{ marginTop: 0 }}>
              "Same day last week" is the same weekday 7 days earlier (a Tuesday against a Tuesday), so a
              normally quiet day isn't compared with a busy one. The "4-week average" is the average of
              the same weekday over the 4 weeks before (days the store was closed are left out). Last year
              shows once the store has a year of {currentPosName()} history. Today can be picked too: it's compared with
              the same hours of the earlier days.
            </p>
            <p style={{ marginBottom: 0 }}>
              Numbers are {currentPosName()}'s own Analytics totals for the business day (the day closes at 4 AM), so
              they can differ by a few dollars from Items Sold, which counts by payment time.
            </p>
          </>
        ),
      }}
      controls={
        <label className="ins-field">
          <span>Day</span>
          <input type="date" value={date} max={today} min="2026-05-26" onChange={(e) => e.target.value && setDate(e.target.value)} />
        </label>
      }
    >
      <InsightBody
        state={state}
        isEmpty={(d) => !METRICS.some((m) => isNum(d?.[m.key]) && d[m.key] !== 0)}
        empty={`No sales were recorded on ${longDate(date)}. The store may have been closed.`}
      >
        {data && (
          <>
            <h2 className="ins-day-title">
              {longDate(data.date || date)}
              {data.partial && (
                <span className="ins-day-note">
                  {" "}
                  - so far{isNum(data.through_hour) ? `, up to ${hourLabel(data.through_hour, { long: true })}` : ""}
                </span>
              )}
            </h2>
            {data.partial && (
              <p className="muted ins-footnote ins-partial-note">
                Today isn't over: it's compared with the same hours of the earlier days. {currentPosName()}'s numbers run a few hours
                behind the registers.
              </p>
            )}
            <div className="ins-kpis">
              {metrics.map((m) => {
                const lw = compareCell(data, "vs_last_week", m.key);
                return (
                  <div className="ins-kpi" key={m.key}>
                    <div className="ins-kpi-label">
                      {m.label}
                      <TermInfo k={m.term} label={m.label} />
                    </div>
                    <div className="ins-kpi-value">{m.fmt(data[m.key])}</div>
                    <div className="ins-kpi-sub">
                      {lw ? (
                        <>
                          <Delta pct={lw.pct} against="the same day last week" /> vs last week
                          {isNum(lw.base) ? ` (${m.fmt(lw.base)})` : ""}
                        </>
                      ) : (
                        m.note || "No comparison"
                      )}
                    </div>
                  </div>
                );
              })}
            </div>

            {tableMetrics.length > 0 && (
              <section className="card">
                <h2>How it compares</h2>
                <div className="table-wrap">
                  <table className="ins-compare-table">
                    <thead>
                      <tr>
                        <th scope="col">Compared with</th>
                        {tableMetrics.map((m) => (
                          <th key={m.key} scope="col" className="num">
                            {m.label}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      <tr className="ins-row-strong">
                        <th scope="row" className="ins-row-head">
                          {shortDate(data.date || date, { weekday: true })} (this day)
                        </th>
                        {tableMetrics.map((m) => (
                          <td key={m.key} className="num">
                            {m.fmt(data[m.key])}
                          </td>
                        ))}
                      </tr>
                      {comps.map((c) => {
                        const dateNote = data[c.key]?.date ? ` - ${shortDate(data[c.key].date, { weekday: true, year: c.key === "vs_last_year" })}` : "";
                        return (
                          <tr key={c.key}>
                            <th scope="row" className="ins-row-head">
                              {c.key === "vs_4wk_avg" && isNum(weeksUsed) && weeksUsed > 0 && weeksUsed < 4
                                ? `${weeksUsed}-week average`
                                : c.label}
                              {dateNote}
                              <TermInfo k={c.term} label={c.label} />
                            </th>
                            {tableMetrics.map((m) => {
                              const cell = compareCell(data, c.key, m.key);
                              return (
                                <td key={m.key} className="num">
                                  {cell ? (
                                    <span className="ins-compare-cell">
                                      <span>{isNum(cell.base) ? m.fmt(cell.base) : ""}</span>
                                      <Delta pct={cell.pct} against={c.label.toLowerCase()} />
                                    </span>
                                  ) : (
                                    <span className="muted">-</span>
                                  )}
                                </td>
                              );
                            })}
                          </tr>
                        );
                      })}
                      {!data.vs_last_year && (
                        <tr>
                          <th scope="row" className="ins-row-head">
                            Same day last year
                            <TermInfo k="insights_vs_last_year" label="Same day last year" />
                          </th>
                          <td colSpan={tableMetrics.length} className="muted">
                            Not available yet - the store's {currentPosName()} history doesn't go back that far.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
                <p className="muted ins-footnote">▲ higher, ▼ lower than the day it's compared with; = means within half a percent.</p>
              </section>
            )}
          </>
        )}
      </InsightBody>
    </InsightsPage>
  );
}
