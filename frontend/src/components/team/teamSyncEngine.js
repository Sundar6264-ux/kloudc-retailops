// One shared Team change-feed loop for the whole website (Oct 15): the
// sidebar badges, the Tasks page and an open task drawer all listen to the
// same /api/team/updates cursor instead of each polling on its own.
//
// While a page that needs it live is open (the Tasks page, a task drawer:
// subscribe with `live` or `tasks`) it long-polls (`wait`: the server answers
// as soon as something the user can see changes, else after `wait` s with no
// changes). Otherwise - only the sidebar badges listening - it asks every
// `pollMs` (the server's badge_poll_interval_s, 2 minutes) as before, so an
// ordinary page has no request held open. It also falls back to plain polling
// when the server answers a long-poll at once with nothing in it (an older
// server that ignores `wait`). A hidden tab stops asking and asks once as
// soon as it's shown again. Pure JS with injected
// dependencies (no React, no fetch) so node --test can drive it:
// node --test src/components/team/teamSyncEngine.test.mjs

export const LONG_POLL_WAIT_S = 20; // the server caps `wait` at 20
// A long-poll that comes back empty faster than this was not held open.
const NOT_HELD_MS = 1500;
const MAX_BACKOFF_MS = 60000;

// deps: {
//   bootstrap(): Promise<{cursor, badges, flags}>,
//   updates(cursor, {wait, include, signal}): Promise<{cursor, resync_required, changes, badges, tasks?}>,
//   isVisible(): bool, onShown(fn): cleanup,
//   now(): ms, setTimeout, clearTimeout, AbortController (optional)
// }
// subscribe(listener, {live, tasks}) -> unsubscribe. A listener gets
// {changes, tasks, badges, resync}; `resync` = the cursor was lost, reload
// everything.
export function createTeamSync(deps) {
  const now = deps.now || (() => Date.now());
  const setT = deps.setTimeout || setTimeout;
  const clearT = deps.clearTimeout || clearTimeout;
  const Abort = deps.AbortController || (typeof AbortController === "undefined" ? null : AbortController);

  const subs = new Map(); // listener -> {live, tasks}
  let running = false;
  let gen = 0; // bumped on stop, so an old loop exits
  let cursor = null;
  let lastBadges = null;
  let pollMs = 120000;
  let longPoll = true;
  let emptyFast = 0;
  let refreshWanted = false;
  let controller = null;
  let wake = null; // resolves the current sleep early
  let stopShown = null;
  const stats = { requests: 0, bootstraps: 0 };

  function emit(event) {
    for (const [fn] of subs) {
      try {
        fn(event);
      } catch {
        // one listener's bug never stops the feed for the others
      }
    }
  }

  function wantsTasks() {
    for (const [, o] of subs) if (o.tasks) return true;
    return false;
  }

  function wantsLive() {
    for (const [, o] of subs) if (o.live) return true;
    return false;
  }

  // Sleeps `ms` (Infinity = until woken). Woken early by refresh(), the tab
  // being shown again, or stop().
  function sleep(ms) {
    return new Promise((resolve) => {
      let timer = null;
      const done = () => {
        if (timer !== null) clearT(timer);
        if (wake === done) wake = null;
        resolve();
      };
      wake = done;
      if (ms !== Infinity) timer = setT(done, ms);
    });
  }

  function wakeUp() {
    if (wake) wake();
  }

  async function loop(myGen) {
    let failures = 0;
    let resyncPending = false;
    while (myGen === gen && subs.size > 0) {
      if (!deps.isVisible()) {
        await sleep(Infinity);
        continue;
      }
      try {
        if (cursor === null) {
          stats.bootstraps += 1;
          const boot = await deps.bootstrap();
          if (myGen !== gen) return;
          cursor = boot.cursor ?? 0;
          pollMs = Math.max(15, boot.flags?.badge_poll_interval_s || 120) * 1000;
          lastBadges = boot.badges || {};
          emit({ changes: {}, tasks: [], badges: lastBadges, resync: resyncPending });
          resyncPending = false;
          failures = 0;
          // Just fetched: the badge-only poll waits its interval first.
          if (!wantsLive() && !refreshWanted) await sleep(pollMs);
          continue;
        }
        const wait = refreshWanted || !longPoll || !wantsLive() ? 0 : LONG_POLL_WAIT_S;
        refreshWanted = false;
        controller = Abort ? new Abort() : null;
        const started = now();
        stats.requests += 1;
        const res = await deps.updates(cursor, {
          wait,
          include: wantsTasks() ? "tasks" : null,
          signal: controller ? controller.signal : undefined,
        });
        controller = null;
        if (myGen !== gen) return;
        failures = 0;
        if (res.resync_required) {
          cursor = null;
          resyncPending = true;
          continue;
        }
        cursor = res.cursor ?? cursor;
        const changes = res.changes || {};
        const changed = Object.keys(changes).some((k) => (changes[k] || []).length > 0);
        if (res.badges) lastBadges = res.badges;
        emit({ changes, tasks: res.tasks || [], badges: lastBadges || {}, resync: false });
        if (wait > 0 && !changed && now() - started < NOT_HELD_MS) {
          // Twice in a row = the server doesn't hold the request: poll.
          emptyFast += 1;
          if (emptyFast >= 2) longPoll = false;
        } else if (wait > 0) {
          emptyFast = 0;
        }
        if ((!longPoll || !wantsLive()) && !refreshWanted) await sleep(pollMs);
      } catch (err) {
        controller = null;
        if (myGen !== gen) return;
        if (refreshWanted && err && err.name === "AbortError") continue;
        failures += 1;
        // 2 s, 4 s, 8 s ... up to a minute; a refresh or the tab being shown
        // tries again at once.
        await sleep(Math.min(MAX_BACKOFF_MS, 1000 * 2 ** failures));
      }
    }
  }

  function start() {
    if (running) return;
    running = true;
    const myGen = ++gen;
    stopShown = deps.onShown(() => {
      refreshWanted = true;
      wakeUp();
    });
    loop(myGen).finally(() => {
      if (myGen === gen) running = false;
    });
  }

  function stop() {
    gen += 1;
    running = false;
    cursor = null; // the next start (maybe another login) bootstraps again
    lastBadges = null;
    if (controller) controller.abort();
    controller = null;
    if (stopShown) stopShown();
    stopShown = null;
    wakeUp();
  }

  return {
    subscribe(listener, options = {}) {
      const live = !!(options.live || options.tasks);
      const wasLive = wantsLive();
      subs.set(listener, { live, tasks: !!options.tasks });
      if (running && live && !wasLive) {
        // The loop may be sleeping between badge polls: catch up now, then
        // long-poll.
        refreshWanted = true;
        wakeUp();
      }
      start();
      return () => {
        subs.delete(listener);
        if (subs.size === 0) stop();
      };
    },
    // Ask now (e.g. after reading a chat the badge count changed without a
    // feed row). Cuts a waiting long-poll short.
    refresh() {
      if (!running) return;
      refreshWanted = true;
      if (controller) controller.abort();
      wakeUp();
    },
    badges() {
      return lastBadges;
    },
    get mode() {
      return longPoll && wantsLive() ? "long-poll" : "poll";
    },
    stats,
  };
}

// --- Applying a feed event to the Tasks page's lists -----------------------------

// The ids of tasks the event says changed.
export function changedTaskIds(event) {
  return new Set(((event && event.changes && event.changes.task) || []).map((c) => c.id));
}

// Patches the page's {open, done, cantDo} lists in place with fresh task rows
// (from the feed's `tasks`, or a write's response) so the screen moves at
// once; the page still reloads the lists right after for anything that
// joined or left the view. A task only moves between lists it's already in
// the view of - new ones wait for that reload.
export function patchTaskLists(lists, fresh) {
  const byId = new Map((fresh || []).filter((t) => t && t.id != null).map((t) => [t.id, t]));
  if (byId.size === 0 || !lists || lists.open === null) return lists;
  const patch = (list) => (list || []).map((t) => (byId.has(t.id) ? { ...t, ...byId.get(t.id) } : t));
  const open = [];
  const done = [];
  const cantDo = [];
  for (const t of [...patch(lists.open), ...patch(lists.done), ...patch(lists.cantDo)]) {
    if (t.status === "open") open.push(t);
    else if (t.status === "done") done.push(t);
    else if (t.status === "cant_do") cantDo.push(t);
    // cancelled: drops out of every list
  }
  return { open, done: lists.done === null ? null : done, cantDo: lists.cantDo === null ? null : cantDo };
}
