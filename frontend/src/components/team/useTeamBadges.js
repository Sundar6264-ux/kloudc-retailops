import { useState } from "react";
import useTeamSync, { teamSync } from "./useTeamSync";

// Team hub badge counts for the sidebar ({unread, announcements_unacked,
// tasks_due}). Since Oct 15 they ride on the shared Team change feed
// (useTeamSync.js): every 2 minutes on an ordinary page, within a second
// while the Tasks page is open (it long-polls the same feed). Pages that
// change a count without a feed row (reading a chat, confirming an
// announcement) fire `refreshTeamBadges()` so the sidebar asks at once.

export function refreshTeamBadges() {
  teamSync().refresh();
}

export default function useTeamBadges(enabled) {
  const [badges, setBadges] = useState(() => (enabled && teamSync().badges()) || {});
  useTeamSync((event) => setBadges(event.badges || {}), { enabled: !!enabled });
  return enabled ? badges : {};
}
