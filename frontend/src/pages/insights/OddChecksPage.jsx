import { useState } from "react";
import { getInsightsOddChecks } from "../../api";
import { useAuth } from "../../AuthContext.jsx";
import { AckButton, isAcked, useAcks } from "../../components/insights/FlagAck.jsx";
import { formatRestaurantTime } from "../../format.js";
import { TermInfo } from "../../glossary.jsx";
import SortableTh from "../../components/SortableTh.jsx";
import { useTableControls } from "../../useTableControls.js";
import { Choice, InsightBody, InsightsPage, useInsight } from "../../components/insights/InsightsCommon.jsx";
import { fmtCount, fmtMoney, isNum, minutesText, shortDate, storeTime } from "../../components/insights/insightsModel.js";
import { currentPosName } from "../../store/gating.js";

const DAYS = [
  { value: 7, label: "7 days" },
  { value: 14, label: "14 days" },
  { value: 30, label: "30 days" },
];

const WHY = {
  open_long: { label: "Open too long", term: "insights_odd_open_long" },
  modified_after_paid: { label: "Changed after payment", term: "insights_odd_modified_after_paid" },
  large_refund: { label: "Large refund", term: "insights_odd_large_refund" },
};

function WhyPills({ why }) {
  const list = Array.isArray(why) ? why : why ? [why] : [];
  if (!list.length) return <span className="muted">-</span>;
  return (
    <span className="ins-why">
      {list.map((w) => {
        const known = WHY[w];
        return (
          <span key={w} className="pill pill-warn ins-reason">
            {known ? known.label : String(w)}
          </span>
        );
      })}
    </span>
  );
}

export default function OddChecksPage() {
  const { user } = useAuth();
  const [days, setDays] = useState(7);
  const acks = useAcks();
  const state = useInsight(() => getInsightsOddChecks(days), [days]);
  const items = (Array.isArray(state.data?.items) ? state.data.items : []).map(acks.apply);
  const open = items.filter((r) => !isAcked(r)).length;
  const t = useTableControls(
    items.map((r) => ({ ...r, why_text: (Array.isArray(r.why) ? r.why : [r.why]).filter(Boolean).join(" ") })),
    { searchKeys: ["employee", "check_number", "why_text"], defaultSortKey: "date", defaultSortDir: "desc" },
  );
  const thresholds = state.data?.thresholds || state.data?.settings;

  return (
    <InsightsPage
      title="Odd checks"
      brief="Checks that stayed open for hours, were changed after they were paid, or had a large refund."
      info={{
        brief: "Admins only. These go out in the same 9 AM Loss watch alert the next day; Acknowledge marks one as looked at.",
        body: (
          <>
            <p style={{ marginTop: 0 }}>
              A check is listed when it was open longer than {thresholds?.open_hours ?? 3} hours, when it was changed
              more than an hour after it was paid (an item removed, a discount added), or when it had a refund of
              {isNum(thresholds?.large_refund) ? ` ${fmtMoney(thresholds.large_refund)}` : " a large amount"} or more.
              Online and delivery orders are changed when they're handed over, so those only count with a refund, and
              {currentPosName()}'s own overnight batch updates are left out. Most have an ordinary reason - a tab left open, a
              correction at closing - but a pattern is worth asking about.
            </p>
            <p style={{ marginBottom: 0 }}>Times are in store time. Look a check up in {currentPosName()} by its number and date.</p>
          </>
        ),
      }}
      controls={<Choice label="Period" options={DAYS} value={days} onChange={setDays} />}
    >
      <InsightBody
        state={state}
        adminOnly
        isEmpty={() => items.length === 0}
        empty={`No odd checks in the last ${days} days.`}
      >
        <div className="ins-legend-terms muted">
          {Object.values(WHY).map((w) => (
            <span key={w.term} className="ins-legend-term">
              {w.label}
              <TermInfo k={w.term} label={w.label} />
            </span>
          ))}
        </div>
        <section className="card">
          <div className="card-header ins-card-header">
            <h2>
              {fmtCount(items.length)} {items.length === 1 ? "check" : "checks"} in {days} days
              {open > 0 && open < items.length ? ` (${fmtCount(open)} not looked at)` : ""}
            </h2>
            {items.length > 8 && (
              <input
                type="search"
                className="table-search ins-search"
                placeholder="Search employee or check"
                aria-label="Search employee or check number"
                value={t.search}
                onChange={(e) => t.setSearch(e.target.value)}
              />
            )}
          </div>
          <div className="table-wrap">
            <table className="ins-stack-table">
              <thead>
                <tr>
                  <SortableTh sortKey="date" currentSortKey={t.sortKey} sortDir={t.sortDir} onSort={t.toggleSort}>
                    Date
                  </SortableTh>
                  <th scope="col">Check</th>
                  <SortableTh sortKey="employee" currentSortKey={t.sortKey} sortDir={t.sortDir} onSort={t.toggleSort}>
                    Employee
                  </SortableTh>
                  <SortableTh sortKey="minutes_open" currentSortKey={t.sortKey} sortDir={t.sortDir} onSort={t.toggleSort}>
                    Opened - paid
                  </SortableTh>
                  <SortableTh sortKey="refund" currentSortKey={t.sortKey} sortDir={t.sortDir} onSort={t.toggleSort} align="right">
                    Refund
                  </SortableTh>
                  <SortableTh sortKey="total" currentSortKey={t.sortKey} sortDir={t.sortDir} onSort={t.toggleSort} align="right">
                    Total
                  </SortableTh>
                  <th scope="col">Why</th>
                  <th scope="col">Status</th>
                </tr>
              </thead>
              <tbody>
                {t.rows.map((r, i) => (
                  <tr key={r.id ?? `${r.date}-${r.check_number}-${i}`} className={isAcked(r) ? "ins-row-done" : undefined}>
                    <td data-label="Date">{shortDate(r.date, { weekday: true })}</td>
                    <td data-label="Check">{r.check_number ?? "-"}</td>
                    <td data-label="Employee">{r.employee || "-"}</td>
                    <td
                      data-label="Opened - paid"
                      className="ins-nowrap"
                      title={[formatRestaurantTime(r.opened_at), formatRestaurantTime(r.paid_at)].filter(Boolean).join(" to ") || undefined}
                    >
                      <span>
                        {storeTime(r.opened_at, r.date) || "?"} - {storeTime(r.paid_at, r.date) || (r.status === "OPEN" ? "still open" : "not paid")}
                        <span className="ins-cell-sub">open {minutesText(r.minutes_open)}</span>
                      </span>
                    </td>
                    <td data-label="Refund" className={`num${isNum(r.refund) && r.refund ? "" : " ins-stack-empty"}`}>
                      {isNum(r.refund) && r.refund ? fmtMoney(r.refund, { cents: true }) : "-"}
                    </td>
                    <td data-label="Total" className="num">{fmtMoney(r.total, { cents: true })}</td>
                    <td className="cell-wrap ins-stack-wide">
                      <WhyPills why={r.why} />
                    </td>
                    <td className="ins-stack-wide">
                      <AckButton row={r} demo={!!user?.demo} onDone={acks.done} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {t.rows.length === 0 && <p className="muted">No checks match “{t.search}”.</p>}
        </section>
      </InsightBody>
    </InsightsPage>
  );
}
