import { useState } from "react";
import { useAuth } from "../AuthContext.jsx";
import JobStatusWidget from "../components/JobStatusWidget.jsx";
import InventoryUpload from "../components/InventoryUpload.jsx";
import WeeklyDigest from "../components/WeeklyDigest.jsx";
import { ActivityTiles, StatTiles, TodaySchedule } from "../components/OverviewWidgets.jsx";

function greeting() {
  const hour = new Date().getHours();
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}

export default function Overview() {
  const { user } = useAuth();
  const [refreshSignal, setRefreshSignal] = useState(0);
  const today = new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });

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
          <InventoryUpload onUploaded={() => setRefreshSignal((n) => n + 1)} />
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
