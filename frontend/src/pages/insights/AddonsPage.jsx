import { useState } from "react";
import { getInsightsAddons } from "../../api";
import { useAuth } from "../../AuthContext.jsx";
import { TermInfo } from "../../glossary.jsx";
import SortableTh from "../../components/SortableTh.jsx";
import { useTableControls } from "../../useTableControls.js";
import { Choice, InsightBody, InsightsPage, useInsight } from "../../components/insights/InsightsCommon.jsx";
import { fmtCount, fmtMoney, fmtShare, isNum } from "../../components/insights/insightsModel.js";
import { currentPosName } from "../../store/gating.js";

const DAYS = [
  { value: 7, label: "7 days" },
  { value: 28, label: "28 days" },
  { value: 56, label: "56 days" },
];

const SEARCH_KEYS = ["name", "group"];

export default function AddonsPage() {
  const { canSeeSales } = useAuth();
  const [days, setDays] = useState(28);
  const state = useInsight(() => getInsightsAddons(days), [days]);
  const mods = Array.isArray(state.data?.modifiers) ? state.data.modifiers : [];
  const t = useTableControls(mods, { searchKeys: SEARCH_KEYS, defaultSortKey: "qty", defaultSortDir: "desc" });
  const showMoney = canSeeSales && mods.some((m) => isNum(m.net_sales));
  const showAttach = mods.some((m) => isNum(m.attach_rate));
  const paid = mods.filter((m) => isNum(m.net_sales) && m.net_sales > 0);
  const paidTotal = paid.reduce((a, m) => a + m.net_sales, 0);

  return (
    <InsightsPage
      title="Add-ons"
      brief="Which modifiers and add-ons customers choose, how often, and what the paid ones bring in."
      info={{
        brief: "Attach rate is how often an add-on was chosen per unit of the item it belongs to.",
        body: (
          <p style={{ margin: 0 }}>
            From {currentPosName()}'s item report grouped by modifier: every choice made on an order (spice level, extra cheese, a
            side). Free choices still count as units - they show what people want. Net sales are only the extra
            charged for paid add-ons; that money is already inside the item's own sales elsewhere in the dashboard, so
            don't add the two together. Attach rate is only shown when {currentPosName()} tells us which item the add-on was on.
          </p>
        ),
      }}
      controls={<Choice label="Period" options={DAYS} value={days} onChange={setDays} />}
    >
      <InsightBody state={state} isEmpty={() => mods.length === 0} empty={`No add-ons were chosen in the last ${days} days.`}>
        <div className="ins-kpis">
          <div className="ins-kpi">
            <div className="ins-kpi-label">Add-ons chosen</div>
            <div className="ins-kpi-value">{fmtCount(mods.reduce((a, m) => a + (m.qty || 0), 0))}</div>
            <div className="ins-kpi-sub">{fmtCount(mods.length)} different add-ons</div>
          </div>
          {showMoney && (
            <div className="ins-kpi">
              <div className="ins-kpi-label">Paid add-on sales</div>
              <div className="ins-kpi-value">{fmtMoney(paidTotal)}</div>
              <div className="ins-kpi-sub">from {fmtCount(paid.length)} paid add-ons</div>
            </div>
          )}
        </div>
        <section className="card">
          <div className="card-header ins-card-header">
            <h2>Add-ons, last {days} days</h2>
            {mods.length > 8 && (
              <input
                type="search"
                className="table-search ins-search"
                placeholder="Search add-ons"
                aria-label="Search add-ons"
                value={t.search}
                onChange={(e) => t.setSearch(e.target.value)}
              />
            )}
          </div>
          <div className="table-wrap">
            <table className="ins-addons-table">
              <thead>
                <tr>
                  <SortableTh sortKey="name" currentSortKey={t.sortKey} sortDir={t.sortDir} onSort={t.toggleSort}>
                    Add-on
                  </SortableTh>
                  <SortableTh sortKey="group" currentSortKey={t.sortKey} sortDir={t.sortDir} onSort={t.toggleSort}>
                    Group
                  </SortableTh>
                  <SortableTh sortKey="qty" currentSortKey={t.sortKey} sortDir={t.sortDir} onSort={t.toggleSort} align="right">
                    Times chosen
                  </SortableTh>
                  {showMoney && (
                    <SortableTh sortKey="net_sales" currentSortKey={t.sortKey} sortDir={t.sortDir} onSort={t.toggleSort} align="right">
                      Net sales
                    </SortableTh>
                  )}
                  {showAttach && (
                    <SortableTh sortKey="attach_rate" currentSortKey={t.sortKey} sortDir={t.sortDir} onSort={t.toggleSort} align="right">
                      Attach rate
                      <TermInfo k="insights_attach_rate" label="Attach rate" />
                    </SortableTh>
                  )}
                </tr>
              </thead>
              <tbody>
                {t.rows.map((m, i) => (
                  <tr key={`${m.group}-${m.name}-${i}`}>
                    <td className="cell-name">
                      {m.name}
                      {m.group && <span className="ins-phone-sub">{m.group}</span>}
                    </td>
                    <td className="cell-wrap">{m.group || "-"}</td>
                    <td className="num">{fmtCount(m.qty, 1)}</td>
                    {showMoney && <td className="num">{isNum(m.net_sales) && m.net_sales ? fmtMoney(m.net_sales, { cents: true }) : "Free"}</td>}
                    {showAttach && <td className="num">{isNum(m.attach_rate) ? fmtShare(m.attach_rate) : "-"}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {t.rows.length === 0 && <p className="muted">No add-ons match “{t.search}”.</p>}
        </section>
      </InsightBody>
    </InsightsPage>
  );
}
