import { useState } from "react";
import { useAuth } from "../AuthContext.jsx";
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
  const { user } = useAuth();
  const [refreshSignal, setRefreshSignal] = useState(0);
  // The inventory file upload is for a POS that exports one (csv_catalog_import).
  const canUpload = useStoreAllows("inventory_upload");
  const today = storeDateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric" }).format(new Date());

  return (
    <>
      <div className="page-header overview-header">
        <h1>
          {greeting()}
          {user?.username ? `, ${user.username}` : ""}
        </h1>
        <div className="overview-subline">
          <span className="muted">{today}</span>
          <JobStatusWidget refreshSignal={refreshSignal} inline />
        </div>
      </div>

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
