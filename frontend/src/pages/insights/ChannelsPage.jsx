import { useState } from "react";
import { getInsightsChannels } from "../../api";
import { useAuth } from "../../AuthContext.jsx";
import { TermInfo } from "../../glossary.jsx";
import { Choice, InsightBody, InsightsPage, useInsight } from "../../components/insights/InsightsCommon.jsx";
import { ChartTable, Legend, StackedBars } from "../../components/insights/charts.jsx";
import {
  channelsModel,
  fmtCount,
  fmtMoney,
  fmtMoneyShort,
  fmtShare,
  isNum,
  shortDate,
  weekTitle,
} from "../../components/insights/insightsModel.js";

const WEEKS = [
  { value: 4, label: "4 weeks" },
  { value: 8, label: "8 weeks" },
  { value: 12, label: "12 weeks" },
];

export default function ChannelsPage() {
  const { canSeeSales } = useAuth();
  const [weeks, setWeeks] = useState(8);
  const [view, setView] = useState("value");
  const state = useInsight(() => getInsightsChannels(weeks), [weeks]);
  const data = state.data;

  const hasMoney =
    canSeeSales && (data?.weeks || []).some((w) => (w.by || []).some((b) => isNum(b.net_sales)));
  const metric = hasMoney ? "net_sales" : "orders";
  const model = channelsModel(data, { metric });
  const fmt = hasMoney ? (v) => fmtMoney(v) : (v) => fmtCount(v);
  const unit = hasMoney ? "net sales" : "orders";

  return (
    <InsightsPage
      title="Sales channels"
      alsoInToast
      brief="How each week's sales split between the counter, pick up, online ordering, delivery apps and catering."
      info={{
        brief: "Each column is one week (Monday to Sunday). Switch to Share to see each channel's percentage.",
        body: (
          <>
            <p style={{ marginTop: 0 }}>
              The channel is the order's dining option in Toast: In-store (the counter), Pick up (the kiosk), Online
              ordering, the delivery apps (DoorDash, Uber Eats) and Catering. Orders with no dining option are under
              Other, together with any channel beyond the five biggest.
            </p>
            <p style={{ marginBottom: 0 }}>
              Amounts are net sales, after discounts and before tax and tips. The current week (marked *) only runs
              to yesterday, so its column is shorter until the week ends.
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
              { value: "value", label: hasMoney ? "Dollars" : "Orders" },
              { value: "share", label: "Share" },
            ]}
            value={view}
            onChange={setView}
          />
        </>
      }
    >
      <InsightBody state={state} isEmpty={() => model.empty} empty={`No sales in the last ${weeks} weeks.`}>
        <div className="ins-kpis ins-kpis-compact">
          {model.overall.map((c) => (
            <div className="ins-kpi" key={c.key}>
              <div className="ins-kpi-label">
                <svg width="10" height="10" aria-hidden="true" className="ins-swatch">
                  <rect width="10" height="10" rx="2" fill={c.color} />
                </svg>
                {c.label}
              </div>
              <div className="ins-kpi-value">{fmtShare(c.share)}</div>
              <div className="ins-kpi-sub" title={c.members.length > 1 ? c.members.join(", ") : undefined}>
                {fmt(c.total)} in {weeks} weeks
              </div>
            </div>
          ))}
        </div>
        <section className="card">
          <div className="card-header ins-card-header">
            <h2>
              {view === "share" ? "Share of " : "Weekly "}
              {unit} by channel
              <TermInfo k="insights_channel" label="Channel" />
              {view === "share" && <TermInfo k="insights_channel_share" label="Share" />}
            </h2>
            <Legend items={model.keys} shape="rect" />
          </div>
          <StackedBars
            rows={model.rows.map((r) => ({ ...r, label: r.partial ? `${r.label}*` : r.label, tooltipTitle: weekTitle(r) }))}
            keys={model.keys}
            mode={view}
            format={fmt}
            axisFormat={hasMoney ? fmtMoneyShort : (v) => fmtCount(v)}
            shareFormat={fmtShare}
            ariaLabel={`${view === "share" ? "Share of " : ""}${unit} by channel for each of the last ${weeks} weeks`}
          />
          {model.rows.some((r) => r.partial) && <p className="muted ins-footnote">* This week so far (to yesterday).</p>}
          <ChartTable
            caption={`Weekly ${unit} by channel`}
            columns={[
              { key: "week", label: "Week of" },
              ...model.keys.map((k) => ({ key: k.key, label: k.label, num: true })),
              { key: "total", label: "Total", num: true },
            ]}
            rows={model.rows.map((r) => {
              const row = { key: r.week_start, week: `${shortDate(r.week_start, { year: true })}${r.partial ? " (so far)" : ""}`, total: fmt(r.total) };
              for (const k of model.keys) row[k.key] = `${fmt(r.values[k.key] || 0)} (${fmtShare(r.shares[k.key])})`;
              return row;
            })}
          />
        </section>
      </InsightBody>
    </InsightsPage>
  );
}
