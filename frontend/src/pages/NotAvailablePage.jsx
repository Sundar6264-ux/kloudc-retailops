import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../AuthContext.jsx";
import { ROUTES_BY_KEY, firstAccessiblePath, routeLabel } from "../featureRoutes.js";
import { useStore } from "../store/StoreContext.jsx";

// Where a link, bookmark or push to a page THIS STORE can't have lands
// (docs/M2_CONTRACT.md section 6): e.g. Insights on a store whose POS has no
// analytics reports. Not a permissions problem - nobody at this store gets
// the page - so it says so plainly and offers the way back.
export default function NotAvailablePage() {
  const [params] = useSearchParams();
  const { hasFeature } = useAuth();
  const store = useStore();
  const route = ROUTES_BY_KEY[params.get("page") || ""];
  const what = route ? routeLabel(route, store.posName) : "This page";
  const home = firstAccessiblePath(hasFeature, store);
  return (
    <div className="page-header not-available" data-testid="not-available">
      <h1>Not available for this store</h1>
      <p className="muted">
        {what} isn't part of {store.name}'s setup
        {store.posName ? ` (it needs something ${store.posName} doesn't provide, or a store setting that is off)` : ""}.
        Nothing is wrong with your account.
      </p>
      {home !== "/no-access" && (
        <p>
          <Link to={home}>Go to your dashboard</Link>
        </p>
      )}
    </div>
  );
}
