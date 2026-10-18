import { useState } from "react";
import { getInsightsHeatmap } from "../../api";
import { useAuth } from "../../AuthContext.jsx";
import { TermInfo } from "../../glossary.jsx";
import { Choice, InsightBody, InsightsPage, useInsight } from "../../components/insights/InsightsCommon.jsx";
import { HeatGrid } from "../../components/insights/charts.jsx";
import {
  dayName,
  fmtCount,
  fmtMoney,
  heatmapModel,
  HEAT_RAMP,
  hourLabel,
  hourRangeLabel,
  isNum,
} from "../../components/insights/insightsModel.js";

const WEEKS = [
  { value: 4, label: "4 weeks" },
  { value: 8, label: "8 weeks" },
  { value: 12, label: "12 weeks" },
];

function SlotList({ title, term, items, data, money, metricOf }) {
  if (!Array.isArray(items) || !items.length) return null;
  return (
    <div className="ins-slotlist">
      <h3>
        {title}
        {term}
      </h3>
      <ol>
        {items.slice(0, 5).map((s) => (
          <li key={`${s.dow}-${s.hour}`}>
            <span className="ins-slot-when">
              {dayName(data, s.dow)} {hourRangeLabel(s.hour)}
            </span>
            <span className="ins-slot-value">{metricOf(s, money)}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

export default function HeatmapPage() {
  const { canSeeSales } = useAuth();
  const [weeks, setWeeks] = useState(8);
  const [metricChoice, setMetricChoice] = useState("sales");
  const state = useInsight(() => getInsightsHeatmap(weeks), [weeks]);
  const data = state.data;

  const hasMoney = canSeeSales && (data?.cells || []).some((c) => isNum(c.avg_net_sales));
  const metric = hasMoney && metricChoice === "sales" ? "avg_net_sales" : "avg_orders";
  const money = metric === "avg_net_sales";
  const model = heatmapModel(data, metric);
  const rows = Array.from({ length: model.dayCount }, (_, i) => i);

  const fmt = (v) => (money ? fmtMoney(v) : fmtCount(v, 1));
  function describe(dow, hour, cell) {
    const when = `${dayName(data, dow)} ${hourRangeLabel(hour)}`;
    if (!cell || !isNum(cell[metric]) || cell[metric] <= 0) return `${when}: nothing sold on average`;
    const parts = [`${when}: ${money ? "average net sales" : "average orders"} ${fmt(cell[metric])}`];
    if (money && isNum(cell.avg_orders)) parts.push(`${fmtCount(cell.avg_orders, 1)} orders on average`);
    if (money && isNum(cell.max_net_sales)) parts.push(`best ${fmtMoney(cell.max_net_sales)}`);
    return parts.join(", ");
  }

  // Busiest / quietest come with avg_net_sales; a counts-only answer may
  // send avg_orders instead.
  const metricOf = (s) =>
    hasMoney && isNum(s.avg_net_sales) ? `${fmtMoney(s.avg_net_sales)} avg` : isNum(s.avg_orders) ? `${fmtCount(s.avg_orders, 1)} ${s.avg_orders === 1 ? "order" : "orders"} avg` : "";

  return (
    <InsightsPage
      title="Busy hours"
      alsoInToast
      brief="Which hours of the week are busiest, averaged over recent weeks."
      info={{
        brief: "Darker squares are busier hours. Point at or tab to a square to see its numbers.",
        body: (
          <p style={{ margin: 0 }}>
            Each square is one hour on one weekday: the average across the chosen weeks of that hour's net
            sales (or orders), from Toast's hourly Analytics for each business day. Hours the store was closed
            in every week are left off the grid. Use it to plan staff, prep and deliveries around the real rush.
          </p>
        ),
      }}
      controls={
        <>
          <Choice label="Period" options={WEEKS} value={weeks} onChange={setWeeks} />
          {hasMoney && (
            <Choice
              label="Show"
              options={[
                { value: "sales", label: "Net sales" },
                { value: "orders", label: "Orders" },
              ]}
              value={metricChoice}
              onChange={setMetricChoice}
            />
          )}
        </>
      }
    >
      <InsightBody
        state={state}
        isEmpty={() => model.empty}
        empty={`No sales in the last ${weeks} weeks.`}
      >
        <section className="card">
          <div className="card-header ins-card-header">
            <h2>
              {money ? "Average net sales per hour" : "Average orders per hour"}
              <TermInfo k="insights_hourly_average" label="Hourly average" />
            </h2>
            <div className="ins-heat-scale" aria-hidden="true">
              <span>Less</span>
              {HEAT_RAMP.slice(1).map((c) => (
                <i key={c} style={{ background: c }} />
              ))}
              <span>More</span>
            </div>
          </div>
          <HeatGrid
            rows={rows}
            hours={model.hours}
            cellFor={(dow, h) => model.byKey.get(`${dow}:${h}`)}
            valueOf={(c) => c[metric]}
            max={model.max}
            describe={describe}
            rowLabel={(dow) => dayName(data, dow, { short: true })}
            colLabel={(h) => hourLabel(h)}
            ariaLabel={`${money ? "Average net sales" : "Average orders"} by weekday and hour, last ${weeks} weeks`}
          />
          {!hasMoney && !canSeeSales && <p className="muted ins-footnote">Showing order counts: sales amounts are hidden for your account.</p>}
        </section>
        <section className="card ins-slots">
          <SlotList title="Busiest hours" term={<TermInfo k="insights_busiest" label="Busiest hours" />} items={data?.busiest} data={data} metricOf={metricOf} />
          <SlotList title="Quietest open hours" term={<TermInfo k="insights_quietest" label="Quietest hours" />} items={data?.quietest} data={data} metricOf={metricOf} />
        </section>
      </InsightBody>
    </InsightsPage>
  );
}
