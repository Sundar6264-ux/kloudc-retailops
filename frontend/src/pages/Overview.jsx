import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../AuthContext.jsx";
import AreaChip from "../ui/AreaChip.jsx";
import Icon from "../ui/icons.jsx";
import { InfoButton } from "../ui/helpBridge.jsx";
import JobStatusWidget from "../components/JobStatusWidget.jsx";
import InventoryUpload from "../components/InventoryUpload.jsx";
import WeeklyDigest from "../components/WeeklyDigest.jsx";
import { ActivityTiles, StatTiles, TodaySchedule } from "../components/OverviewWidgets.jsx";
import { storeDateTimeFormat } from "../format.js";
import { useStoreAllows } from "../store/StoreContext.jsx";

// Store time, like every other date on the dashboard.
function greeting() {
  const hour = Number(storeDateTimeFormat("en-US", { hour: "numeric", hourCycle: "h23" }).format(new Date()));
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}

export default function Overview() {
  const { user, hasFeature, store } = useAuth();
  const [refreshSignal, setRefreshSignal] = useState(0);
  // The inventory file upload is for a POS that exports one (csv_catalog_import).
  const canUpload = useStoreAllows("inventory_upload");
  const today = storeDateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric" }).format(new Date());

  return (
    <>
      {/* Greeting band (DESIGN_SYSTEM 2.1/3.1): the store's own colour, softly. */}
      <header className="page-header overview-header kc-greeting" data-area="home">
        <div className="kc-greeting-main">
          <AreaChip area="home" label={store.shortName} />
          <div className="kc-page-title-row">
            <h1>
              {greeting()}
              {user?.username ? `, ${user.username}` : ""}
            </h1>
            <InfoButton pageId="home" />
          </div>
          <div className="overview-subline">
            <span className="muted">{today}</span>
            <JobStatusWidget refreshSignal={refreshSignal} inline />
          </div>
        </div>
        <div className="kc-greeting-actions">
          {hasFeature("invoice_submission") && (
            <Link to="/invoices" className="btn-secondary kc-quick" data-area="stock">
              <Icon name="upload" size={16} /> Upload invoice
            </Link>
          )}
          {hasFeature("supplier_projection") && (
            <Link to="/supplier-projection" className="button-link kc-quick-primary">
              <Icon name="plus" size={16} /> New order
            </Link>
          )}
        </div>
      </header>

      <StatTiles />

      <div className="overview-layout">
        <div className="overview-main">
          {canUpload && <InventoryUpload onUploaded={() => setRefreshSignal((n) => n + 1)} />}
          <WeeklyDigest compact />
          <ActivityTiles />
        </div>
        <div className="overview-side">
          <TodaySchedule />
          {/* No "Quick actions" (Oct 15): they repeated the menu and the tiles above. */}
        </div>
      </div>
    </>
  );
}
