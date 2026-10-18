import { useState } from "react";
import { getInsightsLossWatch } from "../../api";
import { useAuth } from "../../AuthContext.jsx";
import { Term, TermInfo } from "../../glossary.jsx";
import SortableTh from "../../components/SortableTh.jsx";
import { useTableControls } from "../../useTableControls.js";
import { Choice, InsightBody, InsightsPage, useInsight } from "../../components/insights/InsightsCommon.jsx";
import { AckButton, ackId, isAcked, useAcks } from "../../components/insights/FlagAck.jsx";
import { fmtCount, fmtMoney, hasRows, isNum, shortDate } from "../../components/insights/insightsModel.js";
import { currentPosName } from "../../store/gating.js";

const DAYS = [
  { value: 7, label: "7 days" },
  { value: 14, label: "14 days" },
  { value: 30, label: "30 days" },
];

const money = (v) => fmtMoney(v, { cents: true });

// Server reason codes (services/insights.LOSS_REASONS) -> label + term.
const LOSS_REASONS = {
  full_comp: { label: "Full comp", term: "insights_loss_full_comp" },
  large_discount: { label: "Large discount", term: "insights_loss_large_discount" },
  large_refund: { label: "Large refund", term: "insights_loss_large_refund" },
  large_void: { label: "Large void", term: "insights_loss_large_void" },
  above_employee_median: { label: "Unusual for this employee", term: "insights_loss_above_median" },
};

function reasonLabel(r) {
  if (!r) return "-";
  return LOSS_REASONS[r]?.label || String(r).replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

function ReasonPills({ row }) {
  const list = Array.isArray(row.reasons) && row.reasons.length ? row.reasons : row.reason_flag ? [row.reason_flag] : [];
  if (!list.length) return <span className="muted">-</span>;
  return (
    <span className="ins-why">
      {list.map((r) => (
        <span key={r} className="pill pill-warn ins-reason">
          {reasonLabel(r)}
        </span>
      ))}
    </span>
  );
}

export default function LossWatchPage() {
  const { user } = useAuth();
  const [days, setDays] = useState(30);
  const acks = useAcks();
  const state = useInsight(() => getInsightsLossWatch(days), [days]);
  const data = state.data;
  const th = data?.thresholds;

  const daily = Array.isArray(data?.daily) ? data.daily : [];
  const employees = Array.isArray(data?.by_employee) ? data.by_employee : [];
  const flagged = (Array.isArray(data?.flagged_checks) ? data.flagged_checks : []).map(acks.apply);
  const openFlags = flagged.filter((r) => !isAcked(r)).length;
  const [showAll, setShowAll] = useState(false);
  const shownFlags = showAll ? flagged : flagged.filter((r) => !isAcked(r) || acks.recent(r));

  const totals = daily.reduce(
    (t, d) => ({
      discount: t.discount + (d.discount || 0),
      void: t.void + (d.void || 0),
      refund: t.refund + (d.refund || 0),
      comp: t.comp + (d.comp_estimate || 0),
    }),
    { discount: 0, void: 0, refund: 0, comp: 0 },
  );

  const emp = useTableControls(employees, { searchKeys: ["employee"], defaultSortKey: "discount", defaultSortDir: "desc" });

  return (
    <InsightsPage
      title="Loss watch"
      brief="Discounts, voids, refunds and comps by day and by employee, with the checks worth a second look."
      info={{
        brief: "Admins only. Flagged checks also go out as one 9 AM alert the next day; Acknowledge marks one as looked at.",
        body: (
          <>
            <p style={{ marginTop: 0 }}>
              A check is flagged when it was comped 100%; when its discount reached {fmtMoney(th?.large_discount ?? 50)},
              its refund {fmtMoney(th?.large_refund ?? 25)} or its voids {fmtMoney(th?.large_void ?? 50)}; or when it was
              more than {th?.median_multiple ?? 3} times that employee's usual (the median of their own discounted,
              refunded or voided checks over 30 days). A flag is a prompt to look, not proof of a problem: a manager's
              comp for a regular or a refund for a spoiled item is normal.
            </p>
            <p style={{ marginBottom: 0 }}>
              Comp is an estimate: {currentPosName()} reports discounts and voids, and a check discounted to $0 is counted as a comp.
              No guest card details are shown here.
            </p>
          </>
        ),
      }}
      controls={<Choice label="Period" options={DAYS} value={days} onChange={setDays} />}
    >
      <InsightBody
        state={state}
        adminOnly
        isEmpty={(d) => !hasRows(d, ["daily", "by_employee", "flagged_checks"])}
        empty={`No discounts, voids or refunds in the last ${days} days.`}
      >
        <div className="ins-kpis">
          <div className={`ins-kpi${openFlags ? " ins-kpi-warn" : ""}`}>
            <div className="ins-kpi-label">Checks to look at</div>
            <div className="ins-kpi-value">{fmtCount(openFlags)}</div>
            <div className="ins-kpi-sub">{fmtCount(flagged.length)} flagged in {days} days</div>
          </div>
          <div className="ins-kpi">
            <div className="ins-kpi-label">
              Discounts
              <TermInfo k="insights_discount" label="Discounts" />
            </div>
            <div className="ins-kpi-value">{fmtMoney(totals.discount)}</div>
          </div>
          <div className="ins-kpi">
            <div className="ins-kpi-label">
              Voids
              <TermInfo k="insights_void" label="Voids" />
            </div>
            <div className="ins-kpi-value">{fmtMoney(totals.void)}</div>
          </div>
          <div className="ins-kpi">
            <div className="ins-kpi-label">
              Refunds
              <TermInfo k="insights_refund" label="Refunds" />
            </div>
            <div className="ins-kpi-value">{fmtMoney(totals.refund)}</div>
          </div>
          <div className="ins-kpi">
            <div className="ins-kpi-label">
              Comps (estimate)
              <TermInfo k="insights_comp_estimate" label="Comps (estimate)" />
            </div>
            <div className="ins-kpi-value">{fmtMoney(totals.comp)}</div>
          </div>
        </div>

        <section className="card">
          <div className="card-header ins-card-header">
            <h2>
              Flagged checks
              <TermInfo k="insights_loss_flag" label="Flagged check" />
            </h2>
            {flagged.length > 0 && (
              <Choice
                label="Show"
                options={[
                  { value: false, label: `To look at (${fmtCount(openFlags)})` },
                  { value: true, label: `All (${fmtCount(flagged.length)})` },
                ]}
                value={showAll}
                onChange={setShowAll}
              />
            )}
          </div>
          {flagged.length > 0 && (
            <div className="ins-legend-terms">
              {Object.values(LOSS_REASONS).map((w) => (
                <span key={w.term} className="ins-legend-term">
                  {w.label}
                  <TermInfo k={w.term} label={w.label} />
                </span>
              ))}
            </div>
          )}
          {flagged.length === 0 ? (
            <p className="muted">Nothing flagged in the last {days} days.</p>
          ) : shownFlags.length === 0 ? (
            <p className="muted">Every flagged check has been looked at. Choose All to see them.</p>
          ) : (
            <div className="table-wrap">
              <table className="ins-stack-table">
                <thead>
                  <tr>
                    <th scope="col">Date</th>
                    <th scope="col">Check</th>
                    <th scope="col">Employee</th>
                    <th scope="col" className="num">Total</th>
                    <th scope="col" className="num">Discount</th>
                    <th scope="col" className="num">Refund</th>
                    <th scope="col" className="num">Void</th>
                    <th scope="col">Why</th>
                    <th scope="col">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {shownFlags.map((r, i) => (
                    <tr key={ackId(r) ?? `${r.date}-${r.check_number}-${i}`} className={isAcked(r) ? "ins-row-done" : undefined}>
                      <td data-label="Date">{shortDate(r.date, { weekday: true })}</td>
                      <td data-label="Check">{r.check_number ?? "-"}</td>
                      <td data-label="Employee" className="cell-wrap">{r.employee || "-"}</td>
                      <td data-label="Total" className="num">{money(r.total)}</td>
                      {["discount", "refund", "void"].map((k) => (
                        <td key={k} data-label={k[0].toUpperCase() + k.slice(1)} className={`num${isNum(r[k]) && r[k] ? "" : " ins-stack-empty"}`}>
                          {isNum(r[k]) && r[k] ? money(r[k]) : "-"}
                        </td>
                      ))}
                      <td className="cell-wrap ins-stack-wide">
                        <ReasonPills row={r} />
                      </td>
                      <td className="ins-stack-wide">
                        <AckButton row={r} demo={!!user?.demo} onDone={acks.done} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="card">
          <div className="card-header ins-card-header">
            <h2>By employee</h2>
            {employees.length > 6 && (
              <input
                type="search"
                className="table-search ins-search"
                placeholder="Search employees"
                aria-label="Search employees"
                value={emp.search}
                onChange={(e) => emp.setSearch(e.target.value)}
              />
            )}
          </div>
          {employees.length === 0 ? (
            <p className="muted">No discounts, voids or refunds by anyone in this period.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <SortableTh sortKey="employee" currentSortKey={emp.sortKey} sortDir={emp.sortDir} onSort={emp.toggleSort}>
                      Employee
                    </SortableTh>
                    <SortableTh sortKey="checks" currentSortKey={emp.sortKey} sortDir={emp.sortDir} onSort={emp.toggleSort} align="right">
                      Checks
                    </SortableTh>
                    <SortableTh sortKey="discount" currentSortKey={emp.sortKey} sortDir={emp.sortDir} onSort={emp.toggleSort} align="right">
                      Discount
                    </SortableTh>
                    <SortableTh sortKey="void" currentSortKey={emp.sortKey} sortDir={emp.sortDir} onSort={emp.toggleSort} align="right">
                      Void
                    </SortableTh>
                    <SortableTh sortKey="refund" currentSortKey={emp.sortKey} sortDir={emp.sortDir} onSort={emp.toggleSort} align="right">
                      Refund
                    </SortableTh>
                    <th scope="col">Pattern</th>
                  </tr>
                </thead>
                <tbody>
                  {emp.rows.map((r) => (
                    <tr key={r.employee}>
                      <td className="cell-wrap">{r.employee || "Unknown"}</td>
                      <td className="num">{fmtCount(r.checks)}</td>
                      <td className="num">{money(r.discount)}</td>
                      <td className="num">{money(r.void)}</td>
                      <td className="num">{money(r.refund)}</td>
                      <td>
                        {r.unusual ? (
                          <Term k="insights_unusual">
                            <span className="pill pill-warn ins-reason">{r.flagged ? `${r.flagged} flagged` : "Unusual"}</span>
                          </Term>
                        ) : (
                          <span className="muted">Usual</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="card">
          <h2>By day</h2>
          {daily.length === 0 ? (
            <p className="muted">Nothing in this period.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Date</th>
                    <th scope="col" className="num">Discount</th>
                    <th scope="col" className="num">Void</th>
                    <th scope="col" className="num">Refund</th>
                    <th scope="col" className="num">Comp (est.)</th>
                    <th scope="col" className="num">Checks discounted</th>
                    <th scope="col" className="num">Checks with voids</th>
                  </tr>
                </thead>
                <tbody>
                  {[...daily].sort((a, b) => (a.date < b.date ? 1 : -1)).map((d) => (
                    <tr key={d.date}>
                      <td>{shortDate(d.date, { weekday: true })}</td>
                      <td className="num">{money(d.discount)}</td>
                      <td className="num">{money(d.void)}</td>
                      <td className="num">{money(d.refund)}</td>
                      <td className="num">{money(d.comp_estimate)}</td>
                      <td className="num">{fmtCount(d.discount_checks)}</td>
                      <td className="num">{fmtCount(d.void_checks)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </InsightBody>
    </InsightsPage>
  );
}
