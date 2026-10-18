// One page component for the menu-performance endpoint, shown as two pages
// (owner, Oct 14): `view="trends"` = Menu trends (Rising / Falling / Not
// sold, main Insights section), `view="sellers"` = Best & worst sellers (Top
// / Bottom, "Also in Toast"). The open list is in the URL as `?tab=`.
import { useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { getInsightsMenuPerformance } from "../../api";
import { useAuth } from "../../AuthContext.jsx";
import { storeTodayStr } from "../../format.js";
import { TermInfo } from "../../glossary.jsx";
import { Choice, InsightBody, InsightsPage, useInsight } from "../../components/insights/InsightsCommon.jsx";
import {
  delta,
  filterRows,
  fmtCount,
  fmtMoney,
  hasRows,
  isNum,
  lastSoldText,
  MENU_GROUPS,
  MENU_TABS,
  MENU_VIEWS,
  menuTabFor,
  menuViewTabs,
  shortDate,
} from "../../components/insights/insightsModel.js";
import { currentPosName } from "../../store/gating.js";

const DAYS = [
  { value: 7, label: "7 days" },
  { value: 28, label: "28 days" },
  { value: 56, label: "56 days" },
];

const TAB_TEXT = {
  top: "Best sellers by net sales.",
  bottom: "Lowest net sales among items that did sell.",
  rising: "Biggest rise in units against the period before (items that sold at least 3 then).",
  falling: "Biggest drop in units against the period before (items that sold at least 3 then).",
  not_sold: "Kitchen items on the menu with no sales in this period, longest unsold first.",
};

const TAB_TERMS = {
  top: "insights_menu_top",
  bottom: "insights_menu_bottom",
  rising: "insights_menu_rising",
  falling: "insights_menu_falling",
  not_sold: "insights_menu_not_sold",
};

function Change({ pct, prev }) {
  if (!isNum(pct)) return <span className="muted">{prev === 0 ? "New" : "-"}</span>;
  const d = delta(pct);
  return <span className={`ins-delta ins-delta-${d.dir}`}>{d.text}</span>;
}

const VIEW_TEXT = {
  trends: {
    brief: "What's picking up, what's slowing down and what hasn't sold at all - for the kitchen, the grocery or both.",
    infoBrief: "Rising and Falling compare this period with the same number of days just before it.",
  },
  sellers: {
    brief: "The items with the most and the least net sales - for the kitchen, the grocery or both.",
    infoBrief: "Top and Bottom rank the items that sold by net sales in the period.",
  },
};

export default function MenuPerformancePage({ view = "trends" }) {
  const { canSeeSales } = useAuth();
  const [days, setDays] = useState(28);
  const [group, setGroup] = useState("all");
  const [params, setParams] = useSearchParams();
  const tabs = menuViewTabs(view);
  const tab = menuTabFor(view, params.get("tab"));
  const [search, setSearch] = useState("");

  function setTab(key) {
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("tab", key);
        return next;
      },
      { replace: true },
    );
  }
  const tabRefs = useRef([]);
  const state = useInsight(() => getInsightsMenuPerformance(days, group), [days, group]);
  const data = state.data;
  const today = storeTodayStr();

  const list = Array.isArray(data?.[tab]) ? data[tab] : [];
  const rows = filterRows(list, search, ["name", "menu_group"]);
  const showMoney = canSeeSales && list.some((r) => isNum(r.net_sales));
  const notSold = tab === "not_sold";

  function onTabKey(e, i) {
    let next = null;
    if (e.key === "ArrowRight") next = (i + 1) % tabs.length;
    if (e.key === "ArrowLeft") next = (i - 1 + tabs.length) % tabs.length;
    if (e.key === "Home") next = 0;
    if (e.key === "End") next = tabs.length - 1;
    if (next == null) return;
    e.preventDefault();
    setTab(tabs[next].key);
    tabRefs.current[next]?.focus();
  }

  return (
    <InsightsPage
      title={MENU_VIEWS[view].title}
      brief={VIEW_TEXT[view].brief}
      alsoInToast={view === "sellers"}
      info={{
        brief: VIEW_TEXT[view].infoBrief,
        body: (
          <>
            <p style={{ marginTop: 0 }}>
              Units and net sales come from {currentPosName()}'s item report for each business day (add-ons are counted inside
              their item, discounts are taken off). <strong>Kitchen</strong> is the prepared-food menu
              {data?.kitchen_rule ? ` (${data.kitchen_rule})` : ""}; <strong>Grocery</strong> is the store's other menus.
            </p>
            {view === "trends" ? (
              <p style={{ marginBottom: 0 }}>
                <strong>Not sold</strong> lists kitchen items that are still on the menu in {currentPosName()} but didn't
                sell once in the period - candidates to promote, fix or take off the menu. The best and worst sellers
                are on <Link to={`${MENU_VIEWS.sellers.path}?tab=top`}>Best &amp; worst sellers</Link>.
              </p>
            ) : (
              <p style={{ marginBottom: 0 }}>
                <strong>Bottom</strong> only counts items that sold at least once; for items that didn't sell at all,
                or that are rising or falling, see <Link to={`${MENU_VIEWS.trends.path}?tab=rising`}>Menu trends</Link>.
              </p>
            )}
          </>
        ),
      }}
      controls={
        <>
          <Choice label="Period" options={DAYS} value={days} onChange={setDays} />
          <Choice label="Items" options={MENU_GROUPS.map((g) => ({ value: g.key, label: g.label }))} value={group} onChange={setGroup} />
        </>
      }
    >
      <InsightBody
        state={state}
        isEmpty={(d) => !hasRows(d, tabs.map((t) => t.key))}
        empty={`No item sales in the last ${days} days.`}
      >
        <section className="card">
          <div className="ins-tabs" role="tablist" aria-label="Lists">
            {tabs.map((t, i) => {
              const n = Array.isArray(data?.[t.key]) ? data[t.key].length : 0;
              return (
                <button
                  key={t.key}
                  ref={(el) => (tabRefs.current[i] = el)}
                  type="button"
                  role="tab"
                  id={`ins-tab-${t.key}`}
                  aria-selected={tab === t.key}
                  aria-controls="ins-menu-panel"
                  tabIndex={tab === t.key ? 0 : -1}
                  className={`ins-tab${tab === t.key ? " is-active" : ""}`}
                  onClick={() => setTab(t.key)}
                  onKeyDown={(e) => onTabKey(e, i)}
                >
                  {t.label}
                  <span className="ins-tab-count">{n}</span>
                </button>
              );
            })}
          </div>
          <div id="ins-menu-panel" role="tabpanel" aria-labelledby={`ins-tab-${tab}`}>
            <div className="ins-panel-head">
              <p className="muted ins-panel-text">
                {TAB_TEXT[tab]}
                {TAB_TERMS[tab] && <TermInfo k={TAB_TERMS[tab]} label={MENU_TABS.find((t) => t.key === tab)?.label} />}
                {notSold && group === "grocery" && " (Only kitchen items are checked.)"}
                {group !== "all" && (
                  <>
                    {" "}
                    {group === "kitchen" ? "Kitchen" : "Grocery"} items only
                    {group === "kitchen" ? (
                      <TermInfo k="insights_menu_kitchen" label="Kitchen" />
                    ) : (
                      <TermInfo k="insights_menu_grocery" label="Grocery" />
                    )}
                  </>
                )}
              </p>
              {data?.menu_map === false && group !== "all" && (
                <p className="ins-stale ins-inline-stale" role="status">
                  <span className="ins-stale-icon" aria-hidden="true">!</span>
                  <span>{currentPosName()}'s menu hasn't been read yet, so kitchen and grocery items can't be told apart. Check back in a few minutes.</span>
                </p>
              )}
              {list.length > 8 && (
                <input
                  type="search"
                  className="table-search ins-search"
                  placeholder="Search items"
                  aria-label="Search items"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              )}
            </div>
            {data?.period?.from && (
              <p className="muted ins-footnote ins-period">
                {shortDate(data.period.from)} to {shortDate(data.period.to)}
                {data.prev_period?.from ? `, compared with ${shortDate(data.prev_period.from)} to ${shortDate(data.prev_period.to)}` : ""}.
              </p>
            )}
            {list.length === 0 ? (
              <p className="muted">Nothing in this list for the chosen period and items.</p>
            ) : rows.length === 0 ? (
              <p className="muted">No items match “{search}”.</p>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th scope="col" className="num ins-rank ins-hide-phone">#</th>
                      <th scope="col">Item</th>
                      <th scope="col" className="ins-hide-phone">Menu group</th>
                      {!notSold && <th scope="col" className="num">Units</th>}
                      {!notSold && showMoney && <th scope="col" className="num">Net sales</th>}
                      {!notSold && <th scope="col" className="num ins-hide-phone">Units before</th>}
                      {!notSold && <th scope="col" className="num">Change</th>}
                      <th scope="col">Last sold</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r, i) => (
                      <tr key={r.item_guid || `${r.name}-${i}`}>
                        <td className="num ins-rank ins-hide-phone">{i + 1}</td>
                        <td className="cell-name">
                          {r.name}
                          {r.menu_group && <span className="ins-phone-sub">{r.menu_group}</span>}
                        </td>
                        <td className="cell-wrap ins-hide-phone">{r.menu_group || "-"}</td>
                        {!notSold && <td className="num">{fmtCount(r.qty, 1)}</td>}
                        {!notSold && showMoney && <td className="num">{fmtMoney(r.net_sales)}</td>}
                        {!notSold && <td className="num ins-hide-phone">{fmtCount(r.prev_qty, 1)}</td>}
                        {!notSold && (
                          <td className="num">
                            <Change pct={r.change_pct} prev={r.prev_qty} />
                          </td>
                        )}
                        <td className="ins-nowrap">{lastSoldText(r.last_sold, today)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </section>
      </InsightBody>
    </InsightsPage>
  );
}
