// The Insights hub's tile tabs (docs/DESIGN_SYSTEM.md 3.1): the eleven
// Insights pages on one screen as tiles - the main section, then the pages
// that repeat the POS's own reports ("Also in {pos}"). Each tile is a link
// (every page keeps its address); the open page is remembered, so the menu's
// one Insights link comes back to it (App.jsx InsightsHome).
import { useEffect } from "react";
import { useLocation } from "react-router-dom";
import { useAuth } from "../../AuthContext.jsx";
import { ALSO_IN_POS, insightsTiles, routeLabel } from "../../featureRoutes.js";
import { posText } from "../../store/gating.js";
import TileTabs from "../../ui/TileTabs.jsx";
import { writeRemembered } from "../../ui/useRemembered.js";

export const INSIGHTS_TAB_KEY = "kc.tab.insights";

export default function InsightsHub() {
  const { user, hasFeature, canSeeSales, store } = useAuth();
  const { pathname } = useLocation();
  const { main, alsoIn } = insightsTiles({ user, hasFeature, canSeeSales, store });
  const pos = store.posName;

  useEffect(() => {
    if ([...main, ...alsoIn].some((r) => r.path === pathname)) writeRemembered(INSIGHTS_TAB_KEY, pathname);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname]);

  const toItems = (routes) => routes.map((r) => ({ key: r.path, to: r.path, label: routeLabel(r, pos), icon: r.icon }));
  return (
    <nav className="ins-hub" aria-label="Insights pages" data-area="sales">
      {main.length > 0 && <TileTabs items={toItems(main)} value={pathname} label="Insights" area="sales" compact />}
      {alsoIn.length > 0 && (
        <div className="ins-hub-also">
          <p className="ins-hub-also-label" title={posText(ALSO_IN_POS.note, pos)}>
            {posText(ALSO_IN_POS.label, pos)}
          </p>
          <TileTabs items={toItems(alsoIn)} value={pathname} label={posText(ALSO_IN_POS.label, pos)} area="sales" compact />
        </div>
      )}
    </nav>
  );
}
