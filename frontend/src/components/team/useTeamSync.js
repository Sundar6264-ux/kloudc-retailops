import { useEffect, useRef } from "react";
import { getTeamBootstrap, getTeamUpdates } from "../../api";
import { createTeamSync } from "./teamSyncEngine";
import { isPageVisible, onPageShown } from "./usePageActive";

// The website's one Team change feed (see teamSyncEngine.js). Everything that
// needs to know "something changed" subscribes here: the sidebar badges
// (useTeamBadges), the Tasks page (reloads only when a task changed) and the
// open task drawer (reloads when its task changed).

let shared = null;

export function teamSync() {
  if (!shared) {
    shared = createTeamSync({
      bootstrap: getTeamBootstrap,
      updates: (cursor, opts) => getTeamUpdates(cursor, opts),
      isVisible: isPageVisible,
      onShown: onPageShown,
    });
  }
  return shared;
}

// `onEvent({changes, tasks, badges, resync})` runs for every feed answer
// while `enabled`. `live: true` = this screen wants changes within a second
// (long-poll while it's open; the sidebar alone polls every 2 minutes).
// `tasks: true` (implies live) asks the server to include each changed
// task's row (the Tasks page patches its lists with them).
export default function useTeamSync(onEvent, { enabled = true, live = false, tasks = false } = {}) {
  const ref = useRef(onEvent);
  ref.current = onEvent;
  useEffect(() => {
    if (!enabled) return undefined;
    return teamSync().subscribe((event) => ref.current && ref.current(event), { live, tasks });
  }, [enabled, live, tasks]);
}
