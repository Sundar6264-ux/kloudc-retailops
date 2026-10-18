import { useState } from "react";
import { getInsightsCheckTrends } from "../../api";
import { useAuth } from "../../AuthContext.jsx";
import { TermInfo } from "../../glossary.jsx";
import { Choice, InsightBody, InsightsPage, useInsight } from "../../components/insights/InsightsCommon.jsx";
import { ChartTable, Legend, LineChart } from "../../components/insights/charts.jsx";
import {
  delta,
  fmtCount,
  fmtMoney,
  isNum,
  pctChange,
  SERIES_COLORS,
  SERIES_DASHES,
  shortDate,
} from "../../components/insights/insightsModel.js";

const WEEKS = [
  { value: 4, label: "4 weeks" },
  { value: 8, label: "8 weeks" },
  { value: 12, label: "12 weeks" },
];

// Weekly rows may name their week `week_start` (or `date`).
function rowDate(r) {
  return r.week_start || r.date;
}

function perDay(w) {
  const days = isNum(w.days) && w.days > 0 ? w.days : null;
  return {
    ...w,
    orders: days && isNum(w.orders) ? w.orders / days : w.orders,
    guests: days && isNum(w.guests) ? w.guests / days : w.guests,
    orders_total: w.orders,
    guests_total: w.guests,
  };
}

function Trend({ first, last, fmt, label }) {
  const p = pctChange(last, first);
  const d = delta(p);
  if (d.dir === "none") return null;
  return (
    <div className="ins-kpi-sub">
      <span className={`ins-delta ins-delta-${d.dir}`}>{d.text}</span> since {label} ({fmt(first)})
    </div>
  );
}

export default function CheckTrendsPage() {
  const { canSeeSales } = useAuth();
  const [weeks, setWeeks] = useState(12);
  const [grain, setGrain] = useState("weekly");
  const state = useInsight(() => getInsightsCheckTrends(weeks), [weeks]);
  const data = state.data;

  // The server's weekly rows carry totals and `days`; the charts show a day's
  // average so a short (current or closed-day) week isn't made to look slow.
  const weekly = (Array.isArray(data?.weekly) ? data.weekly : []).map((w) => perDay(w));
  const daily = Array.isArray(data?.days) ? data.days : [];
  const useWeekly = grain === "weekly" && weekly.length > 0;
  const rows = useWeekly ? weekly : daily;
  const labels = rows.map((r) => `${shortDate(rowDate(r))}${useWeekly && r.partial ? "*" : ""}`);
  const hasMoney = canSeeSales && rows.some((r) => isNum(r.avg_check));

  const full = weekly.filter((w) => !w.partial);
  const first = full[0];
  const last = full[full.length - 1];
  const firstLabel = first ? `the week of ${shortDate(rowDate(first))}` : "";
  const unitWord = useWeekly ? "per day, weekly average" : "per day";

  return (
    <InsightsPage
      title="Check size & guests"
      alsoInToast
      brief="Average check, orders and guests over recent weeks: are people spending more per visit, or are there more visits?"
      info={{
        brief: "Weekly smooths out the weekday ups and downs; Daily shows every business day.",
        body: (
          <>
            <p style={{ marginTop: 0 }}>
              Average check is net sales divided by the number of checks (after discounts, before tax and
              tips). Guests is Toast's guest count: the number entered on a dine-in check, or 1 per order
              when nobody entered one, so for a grocery it mostly follows orders.
            </p>
            <p style={{ marginBottom: 0 }}>
              Weekly orders and guests are a day's average for each Monday-to-Sunday week, so a week with a
              closed day, or the current week so far (marked *), isn't made to look slower than it was. The
              average check is the whole week's net sales divided by its orders.
            </p>
          </>
        ),
      }}
      controls={
        <>
          <Choice label="Period" options={WEEKS} value={weeks} onChange={setWeeks} />
          <Choice
            label="Show"
            options={[
              { value: "weekly", label: "Weekly" },
              { value: "daily", label: "Daily" },
            ]}
            value={grain}
            onChange={setGrain}
          />
        </>
      }
    >
      <InsightBody state={state} isEmpty={() => rows.length === 0} empty={`No sales in the last ${weeks} weeks.`}>
        {last && (
          <div className="ins-kpis">
            {hasMoney && isNum(last.avg_check) && (
              <div className="ins-kpi">
                <div className="ins-kpi-label">
                  Average check, last full week
                  <TermInfo k="insights_avg_check" label="Average check" />
                </div>
                <div className="ins-kpi-value">{fmtMoney(last.avg_check, { cents: true })}</div>
                {first !== last && <Trend first={first.avg_check} last={last.avg_check} fmt={(v) => fmtMoney(v, { cents: true })} label={firstLabel} />}
              </div>
            )}
            {isNum(last.orders) && (
              <div className="ins-kpi">
                <div className="ins-kpi-label">
                  Orders a day, last full week
                  <TermInfo k="insights_orders" label="Orders" />
                </div>
                <div className="ins-kpi-value">{fmtCount(last.orders, 1)}</div>
                {first !== last && <Trend first={first.orders} last={last.orders} fmt={(v) => fmtCount(v, 1)} label={firstLabel} />}
              </div>
            )}
            {isNum(last.guests) && (
              <div className="ins-kpi">
                <div className="ins-kpi-label">
                  Guests a day, last full week
                  <TermInfo k="insights_guests" label="Guests" />
                </div>
                <div className="ins-kpi-value">{fmtCount(last.guests, 1)}</div>
                {first !== last && <Trend first={first.guests} last={last.guests} fmt={(v) => fmtCount(v, 1)} label={firstLabel} />}
              </div>
            )}
          </div>
        )}

        {hasMoney && (
          <section className="card">
            <h2>
              Average check
              <TermInfo k="insights_avg_check" label="Average check" />
            </h2>
            <LineChart
              labels={labels}
              series={[{ key: "avg_check", label: "Average check", color: SERIES_COLORS[0], values: rows.map((r) => r.avg_check) }]}
              format={(v) => fmtMoney(v, { cents: true })}
              axisFormat={(v) => fmtMoney(v, { cents: !Number.isInteger(v) })}
              zeroBase={false}
              ariaLabel={`Average check ${unitWord}, last ${weeks} weeks`}
            />
          </section>
        )}

        <section className="card">
          <div className="card-header ins-card-header">
            <h2>Orders and guests ({unitWord})</h2>
            <Legend
              items={[
                { key: "orders", label: "Orders", color: SERIES_COLORS[0], dash: SERIES_DASHES[0] },
                { key: "guests", label: "Guests", color: SERIES_COLORS[1], dash: SERIES_DASHES[1] },
              ]}
            />
          </div>
          <LineChart
            labels={labels}
            series={[
              { key: "orders", label: "Orders", color: SERIES_COLORS[0], dash: SERIES_DASHES[0], values: rows.map((r) => r.orders) },
              { key: "guests", label: "Guests", color: SERIES_COLORS[1], dash: SERIES_DASHES[1], values: rows.map((r) => r.guests) },
            ]}
            format={(v) => fmtCount(v, 1)}
            axisFormat={(v) => fmtCount(v)}
            ariaLabel={`Orders and guests ${unitWord}, last ${weeks} weeks`}
          />
          {useWeekly && weekly.some((w) => w.partial) && <p className="muted ins-footnote">* This week so far (to yesterday).</p>}
          <ChartTable
            caption={`Check size and guests, ${useWeekly ? "weekly" : "daily"}`}
            columns={[
              { key: "when", label: useWeekly ? "Week of" : "Day" },
              ...(hasMoney ? [{ key: "avg_check", label: "Avg check", num: true }] : []),
              { key: "orders", label: useWeekly ? "Orders a day" : "Orders", num: true },
              { key: "guests", label: useWeekly ? "Guests a day" : "Guests", num: true },
              ...(hasMoney ? [{ key: "net_sales", label: useWeekly ? "Net sales (week)" : "Net sales", num: true }] : []),
            ]}
            rows={rows.map((r) => ({
              key: rowDate(r),
              when: `${shortDate(rowDate(r), { weekday: !useWeekly, year: true })}${useWeekly && r.partial ? " (so far)" : ""}`,
              avg_check: fmtMoney(r.avg_check, { cents: true }),
              orders: fmtCount(r.orders, 1),
              guests: fmtCount(r.guests, 1),
              net_sales: fmtMoney(r.net_sales),
            }))}
          />
        </section>
      </InsightBody>
    </InsightsPage>
  );
}
