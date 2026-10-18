import { useState } from "react";
import { getInsightsPrepForecast } from "../../api";
import { storeDateOffsetStr } from "../../format.js";
import { TermInfo } from "../../glossary.jsx";
import { InsightBody, InsightsPage, useInsight } from "../../components/insights/InsightsCommon.jsx";
import { MiniBars } from "../../components/insights/charts.jsx";
import {
  fmtCount,
  forecastBasis,
  hourLabel,
  isNum,
  longDate,
  peakHours,
  prepForecastText,
  rangeText,
} from "../../components/insights/insightsModel.js";

// The hourly split summed over every item (the store's shape for the day).
function sumByHour(items) {
  const m = new Map();
  for (const it of items) for (const b of it.by_hour || []) m.set(b.hour, (m.get(b.hour) || 0) + (b.qty || 0));
  return [...m.entries()].sort((a, b) => a[0] - b[0]).map(([hour, qty]) => ({ hour, qty }));
}

export default function PrepForecastPage() {
  const tomorrow = storeDateOffsetStr(1);
  const [date, setDate] = useState(tomorrow);
  const [copied, setCopied] = useState(null);
  const state = useInsight(() => getInsightsPrepForecast(date), [date]);
  const data = state.data;
  const items = Array.isArray(data?.items) ? data.items : [];
  // One shared hour axis for every row's little chart.
  const hours = [...new Set(items.flatMap((it) => (it.by_hour || []).filter((b) => b.qty > 0).map((b) => b.hour)))].sort((a, b) => a - b);
  const span = hours.length ? Array.from({ length: hours[hours.length - 1] - hours[0] + 1 }, (_, i) => hours[0] + i) : [];
  // As built the hourly split is the store's usual hourly pattern for that
  // weekday (the same shape for every item), so it's shown once, not per row.
  const storeShape = data?.by_hour_source === "store_hourly_share";
  const anyHourly = !storeShape && items.some((it) => Array.isArray(it.by_hour) && it.by_hour.length);
  const storeHours = storeShape ? sumByHour(items) : [];
  const storePeaks = peakHours(storeHours, 2);

  async function copy() {
    try {
      await navigator.clipboard.writeText(prepForecastText(data));
      setCopied("Copied. Paste it into a message or note.");
    } catch {
      setCopied("Couldn't copy - select the list and copy it instead.");
    }
    setTimeout(() => setCopied(null), 4000);
  }

  return (
    <InsightsPage
      className="ins-printable"
      title="Prep forecast"
      brief="How many of each Street Kitchen item to expect on a day, to plan prep."
      info={{
        brief: "Based on the same weekday in recent weeks. Use the range, not just the middle number, when deciding how much to make.",
        body: (
          <>
            <p style={{ marginTop: 0 }}>
              For each Street Kitchen item we take its sales on the same weekday over the last 8 weeks, leave out the
              highest and lowest day (so one catering order or one slow holiday doesn't skew it) and average the rest.
              That's nudged by the last 4 weeks' trend against the 4 before (at most 20% either way). The range is the
              middle half of those days: most weeks sold between the low and the high number.
            </p>
            <p style={{ marginBottom: 0 }}>
              Busiest hours are the store's usual hourly pattern on that weekday (Toast doesn't report items by hour).
              It doesn't know about holidays, weather or catering orders you've taken.
            </p>
          </>
        ),
      }}
      controls={
        <>
          <label className="ins-field">
            <span>Day</span>
            <input
              type="date"
              value={date}
              min="2026-05-26"
              max={storeDateOffsetStr(14)}
              onChange={(e) => e.target.value && setDate(e.target.value)}
            />
          </label>
          {items.length > 0 && (
            <div className="ins-actions ins-no-print">
              <button type="button" className="btn-secondary" onClick={() => window.print()}>
                Print
              </button>
              <button type="button" className="btn-secondary" onClick={copy}>
                Copy as text
              </button>
              {copied && (
                <span className="muted ins-copied" role="status">
                  {copied}
                </span>
              )}
            </div>
          )}
        </>
      }
    >
      <InsightBody state={state} isEmpty={() => items.length === 0} empty={`No kitchen sales are expected on ${longDate(date)} from recent weeks.`}>
        <section className="card ins-prep">
          <div className="ins-prep-head">
            <h2>{longDate(data?.date || date)}</h2>
            <p className="muted">{forecastBasis(data).replace(/\.?$/, ".")}</p>
            {storeShape && storeHours.length > 0 && (
              <p className="ins-prep-shape">
                <MiniBars data={storeHours} hours={span} barWidth={7} height={26} />
                <span>
                  Busiest around {storePeaks.map((h) => hourLabel(h, { long: true })).join(" and ")}: the store's usual
                  pattern on {data?.weekday ? `${data.weekday}s` : "that day"}.{" "}
                  <TermInfo k="insights_prep_by_hour" label="By hour" />
                </span>
              </p>
            )}
          </div>
          <div className="table-wrap">
            <table className="ins-prep-table">
              <thead>
                <tr>
                  <th scope="col">Item</th>
                  <th scope="col" className="num">
                    Expect
                    <TermInfo k="insights_prep_expected" label="Expect" />
                  </th>
                  <th scope="col" className="num">
                    Range
                    <TermInfo k="insights_prep_range" label="Range" />
                  </th>
                  {anyHourly && <th scope="col">Busiest hours</th>}
                  <th scope="col" className="ins-print-only ins-check-col">
                    Done
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((it, i) => {
                  const peaks = peakHours(it.by_hour, 2);
                  return (
                    <tr key={it.item_guid || `${it.name}-${i}`}>
                      <td className="cell-name">{it.name}</td>
                      <td className="num ins-prep-qty">{isNum(it.expected_qty) ? fmtCount(Math.round(it.expected_qty)) : "-"}</td>
                      <td className="num">{rangeText(it) || "-"}</td>
                      {anyHourly && (
                        <td className="ins-prep-hours">
                          <MiniBars data={it.by_hour} hours={span} />
                          <span>{peaks.length ? peaks.map((h) => hourLabel(h, { long: true })).join(", ") : "-"}</span>
                        </td>
                      )}
                      <td className="ins-print-only ins-check-col">
                        <span className="ins-checkbox" />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="muted ins-footnote">
            {fmtCount(items.length)} kitchen {items.length === 1 ? "item" : "items"}. A forecast, not an order - check
            catering orders and the weather too.
          </p>
        </section>
      </InsightBody>
    </InsightsPage>
  );
}
