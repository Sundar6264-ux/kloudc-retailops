import { useState } from "react";
import { getInsightsReturningCustomers } from "../../api";
import { TermInfo } from "../../glossary.jsx";
import { Choice, InsightBody, InsightsPage, useInsight } from "../../components/insights/InsightsCommon.jsx";
import { ChartTable, LineChart } from "../../components/insights/charts.jsx";
import { fmtCount, fmtShare, isNum, longDate, SERIES_COLORS, shortDate } from "../../components/insights/insightsModel.js";
import { currentPosName } from "../../store/gating.js";

const WEEKS = [
  { value: 4, label: "4 weeks" },
  { value: 8, label: "8 weeks" },
  { value: 12, label: "12 weeks" },
];

export default function ReturningCustomersPage() {
  const [weeks, setWeeks] = useState(8);
  const state = useInsight(() => getInsightsReturningCustomers(weeks), [weeks]);
  const data = state.data;
  const rows = Array.isArray(data?.weeks) ? data.weeks : [];
  const overall = data?.overall || {};

  return (
    <InsightsPage
      title="Returning customers"
      brief="How many card-paying customers come back, week by week."
      info={{
        brief: "Counts only: no names or card numbers are stored or shown.",
        body: (
          <>
            <p style={{ marginTop: 0 }}>
              {currentPosName()} gives each card a fingerprint. We scramble it again with a secret key, keep only that scrambled code
              (for at most {data?.retention_days ?? 400} days) and count how many different cards paid each week, and how many of those
              came back: paid on two or more days that week, or had paid before it. Card numbers, names and emails are never stored.
            </p>
            <p style={{ marginBottom: 0 }}>
              Cash customers can't be counted, and one person paying with two cards counts as two. So this is a trend to
              watch, not an exact headcount. The rate climbs in the first weeks simply because there is more history to
              recognise people from.
            </p>
          </>
        ),
      }}
      controls={<Choice label="Period" options={WEEKS} value={weeks} onChange={setWeeks} />}
    >
      <InsightBody state={state} isEmpty={() => rows.length === 0} empty={`No card payments in the last ${weeks} weeks.`}>
        <div className="ins-kpis">
          <div className="ins-kpi">
            <div className="ins-kpi-label">
              Returning rate
              <TermInfo k="insights_returning_rate" label="Returning rate" />
            </div>
            <div className="ins-kpi-value">{fmtShare(overall.returning_rate)}</div>
            <div className="ins-kpi-sub">over {weeks} weeks</div>
          </div>
          <div className="ins-kpi">
            <div className="ins-kpi-label">
              Card customers
              <TermInfo k="insights_card_customers" label="Card customers" />
            </div>
            <div className="ins-kpi-value">{fmtCount(overall.card_customers)}</div>
            <div className="ins-kpi-sub">different cards</div>
          </div>
          <div className="ins-kpi">
            <div className="ins-kpi-label">
              Returning
              <TermInfo k="insights_returning_customer" label="Returning" />
            </div>
            <div className="ins-kpi-value">{fmtCount(overall.returning)}</div>
            <div className="ins-kpi-sub">paid on 2+ days, or had paid before</div>
          </div>
          {isNum(overall.avg_visits) && (
            <div className="ins-kpi">
              <div className="ins-kpi-label">
                Visits per customer
                <TermInfo k="insights_avg_visits" label="Visits per customer" />
              </div>
              <div className="ins-kpi-value">{fmtCount(overall.avg_visits, 1)}</div>
              <div className="ins-kpi-sub">average</div>
            </div>
          )}
        </div>
        <section className="card">
          <h2>Returning rate by week</h2>
          <LineChart
            labels={rows.map((r) => `${shortDate(r.week_start)}${r.partial ? "*" : ""}`)}
            series={[{ key: "rate", label: "Returning rate", color: SERIES_COLORS[0], values: rows.map((r) => r.returning_rate) }]}
            format={fmtShare}
            axisFormat={(v) => `${Math.round(v * 100)}%`}
            ariaLabel={`Share of card customers who had paid before, each week for the last ${weeks} weeks`}
          />
          <p className="muted ins-footnote">
            {rows.some((r) => r.partial) ? "* This week so far (to yesterday). " : ""}
            {data?.history_from
              ? `Card history starts ${longDate(data.history_from)}, so the first weeks after that look lower than they are.`
              : ""}
          </p>
          <ChartTable
            caption="Returning customers by week"
            columns={[
              { key: "week", label: "Week of" },
              { key: "card_customers", label: "Card customers", num: true },
              { key: "returning", label: "Returning", num: true },
              { key: "rate", label: "Rate", num: true },
              { key: "avg_visits", label: "Visits each", num: true },
            ]}
            rows={rows.map((r) => ({
              key: r.week_start,
              week: `${shortDate(r.week_start, { year: true })}${r.partial ? " (so far)" : ""}`,
              card_customers: fmtCount(r.card_customers),
              returning: fmtCount(r.returning),
              rate: fmtShare(r.returning_rate),
              avg_visits: fmtCount(r.avg_visits, 1),
            }))}
          />
        </section>
      </InsightBody>
    </InsightsPage>
  );
}
