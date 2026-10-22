// node --test src/components/team/teamSyncEngine.test.mjs (no deps).
import assert from "node:assert/strict";
import test from "node:test";

import { changedTaskIds, createTeamSync, LONG_POLL_WAIT_S, patchTaskLists } from "./teamSyncEngine.js";

const tick = () => new Promise((r) => setImmediate(r));
async function settle(n = 10) {
  for (let i = 0; i < n; i += 1) await tick();
}
// The engine's sleeps (badge-poll interval, error back-off) use these manual
// timers: they only run when a test calls later(), never on their own. Real
// (shortened) timers made the "nothing yet" checks race the wall clock on a
// busy machine.
let pendingTimers = new Set();
// Lets every sleep that is waiting now run out.
async function later() {
  await settle(); // whatever was just answered reaches its sleep first
  const due = [...pendingTimers];
  pendingTimers.clear();
  for (const t of due) t.fn();
  await settle();
}

// A scripted server: each updates() call waits for the test to answer it.
function fakeServer({ visible = true } = {}) {
  const calls = [];
  const state = { visible, shown: null, clock: 0, bootstraps: 0 };
  const timers = new Set();
  pendingTimers = timers; // tests run one at a time; later() serves this one
  const deps = {
    bootstrap: async () => {
      state.bootstraps += 1;
      return { cursor: 10, badges: { tasks_due: 1 }, flags: { badge_poll_interval_s: 30 } };
    },
    updates: (cursor, opts) =>
      new Promise((resolve, reject) => {
        const call = { cursor, opts, resolve, reject };
        calls.push(call);
        if (opts.signal) {
          opts.signal.addEventListener("abort", () => {
            const e = new Error("aborted");
            e.name = "AbortError";
            reject(e);
          });
        }
      }),
    isVisible: () => state.visible,
    onShown: (fn) => {
      state.shown = fn;
      return () => {
        state.shown = null;
      };
    },
    now: () => state.clock,
    setTimeout: (fn) => {
      const t = { fn };
      timers.add(t);
      return t;
    },
    clearTimeout: (t) => timers.delete(t),
  };
  return { deps, calls, state };
}

test("bootstraps once, then long-polls with the cursor and tells every listener", async () => {
  const { deps, calls } = fakeServer();
  const sync = createTeamSync(deps);
  const a = [];
  const b = [];
  const offA = sync.subscribe((e) => a.push(e), { live: true });
  const offB = sync.subscribe((e) => b.push(e), { tasks: true });
  await settle();
  assert.equal(a[0].badges.tasks_due, 1);
  assert.equal(calls.length, 1, "one shared request, not one per listener");
  assert.equal(calls[0].cursor, 10);
  assert.equal(calls[0].opts.wait, LONG_POLL_WAIT_S);
  assert.equal(calls[0].opts.include, "tasks");
  calls[0].resolve({ cursor: 12, changes: { task: [{ id: 5, action: "created" }] }, badges: { tasks_due: 2 }, tasks: [{ id: 5 }] });
  await settle();
  assert.equal(b.at(-1).badges.tasks_due, 2);
  assert.deepEqual([...changedTaskIds(b.at(-1))], [5]);
  assert.deepEqual(b.at(-1).tasks, [{ id: 5 }]);
  assert.equal(calls[1].cursor, 12);
  offA();
  offB();
  await settle();
});

test("refresh cuts a waiting long-poll short and asks at once (wait 0)", async () => {
  const { deps, calls } = fakeServer();
  const sync = createTeamSync(deps);
  const off = sync.subscribe(() => {}, { live: true });
  await settle();
  assert.equal(calls[0].opts.wait, LONG_POLL_WAIT_S);
  sync.refresh();
  await settle();
  assert.equal(calls.length, 2);
  assert.equal(calls[1].opts.wait, 0);
  calls[1].resolve({ cursor: 10, changes: {}, badges: {} });
  await settle();
  assert.equal(calls[2].opts.wait, LONG_POLL_WAIT_S, "back to long-polling");
  off();
});

test("a server that ignores wait (answers empty at once, twice) is polled instead", async () => {
  const { deps, calls } = fakeServer();
  const sync = createTeamSync(deps);
  const off = sync.subscribe(() => {}, { live: true });
  await settle();
  calls[0].resolve({ cursor: 10, changes: {}, badges: {} });
  await settle();
  assert.equal(sync.mode, "long-poll");
  calls[1].resolve({ cursor: 10, changes: {}, badges: {} });
  await later();
  assert.equal(sync.mode, "poll");
  assert.equal(calls.at(-1).opts.wait, 0);
  off();
});

test("an empty answer after a real wait keeps long-polling", async () => {
  const { deps, calls, state } = fakeServer();
  const sync = createTeamSync(deps);
  const off = sync.subscribe(() => {}, { live: true });
  for (let i = 0; i < 3; i += 1) {
    await settle();
    state.clock += 20000;
    calls.at(-1).resolve({ cursor: 10, changes: {}, badges: {} });
  }
  await settle();
  assert.equal(sync.mode, "long-poll");
  off();
});

test("resync_required bootstraps again and tells listeners to reload", async () => {
  const { deps, calls, state } = fakeServer();
  const sync = createTeamSync(deps);
  const events = [];
  const off = sync.subscribe((e) => events.push(e), { live: true });
  await settle();
  calls[0].resolve({ cursor: 99, resync_required: true, changes: {}, badges: {} });
  await settle();
  assert.equal(state.bootstraps, 2);
  assert.equal(events.at(-1).resync, true);
  off();
});

test("a hidden tab stops asking; showing it asks at once", async () => {
  const { deps, calls, state } = fakeServer({ visible: false });
  const sync = createTeamSync(deps);
  const off = sync.subscribe(() => {});
  await settle();
  assert.equal(calls.length, 0);
  assert.equal(state.bootstraps, 0);
  state.visible = true;
  state.shown();
  await settle();
  assert.equal(state.bootstraps, 1);
  assert.equal(calls.length, 1);
  off();
});

test("errors back off and recover; the last listener leaving stops the loop", async () => {
  const { deps, calls, state } = fakeServer();
  const sync = createTeamSync(deps);
  const off = sync.subscribe(() => {}, { live: true });
  await settle();
  calls[0].reject(new Error("502"));
  await later();
  assert.equal(calls.length, 2, "tried again after the back-off");
  off();
  await settle();
  const n = calls.length;
  await later();
  assert.equal(calls.length, n, "nothing more after unsubscribe");
  // Subscribing again (e.g. another login) starts from a fresh bootstrap.
  const off2 = sync.subscribe(() => {});
  await settle();
  assert.equal(state.bootstraps, 2);
  off2();
});

test("badges alone poll (no request held open); a live screen switches to long-poll and back", async () => {
  const { deps, calls } = fakeServer();
  const sync = createTeamSync(deps);
  const offBadges = sync.subscribe(() => {});
  await settle();
  assert.equal(calls.length, 0, "the sidebar alone: bootstrap, then wait for the next poll");
  await later();
  assert.equal(calls[0].opts.wait, 0, "a badge poll answers at once");
  assert.equal(calls[0].opts.include, null);
  assert.equal(sync.mode, "poll");
  calls[0].resolve({ cursor: 10, changes: {}, badges: {} });
  await settle();
  assert.equal(calls.length, 1, "then waits for the next poll");
  // The Tasks page opens: catch up at once, then long-poll with tasks.
  const offTasks = sync.subscribe(() => {}, { tasks: true });
  await settle();
  assert.equal(calls.length, 2);
  assert.equal(calls[1].opts.wait, 0);
  calls[1].resolve({ cursor: 10, changes: {}, badges: {} });
  await settle();
  assert.equal(calls[2].opts.wait, LONG_POLL_WAIT_S);
  assert.equal(calls[2].opts.include, "tasks");
  assert.equal(sync.mode, "long-poll");
  offTasks();
  calls[2].resolve({ cursor: 10, changes: {}, badges: {} });
  await later();
  assert.equal(sync.mode, "poll");
  assert.equal(calls.at(-1).opts.wait, 0, "back to plain polls once the page closes");
  offBadges();
});

test("one listener throwing never stops the others", async () => {
  const { deps, calls } = fakeServer();
  const sync = createTeamSync(deps);
  const got = [];
  const off1 = sync.subscribe(() => {
    throw new Error("bug");
  });
  const off2 = sync.subscribe((e) => got.push(e), { live: true });
  await settle();
  assert.equal(got.length, 1);
  calls[0].resolve({ cursor: 11, changes: { message: [{ id: 1, action: "created" }] }, badges: {} });
  await settle();
  assert.equal(got.length, 2);
  off1();
  off2();
});

test("patchTaskLists moves patched tasks between open / done / can't do", () => {
  const lists = {
    open: [
      { id: 1, status: "open", title: "A" },
      { id: 2, status: "open", title: "B" },
    ],
    done: [{ id: 3, status: "done", title: "C" }],
    cantDo: [],
  };
  const out = patchTaskLists(lists, [
    { id: 1, status: "done", title: "A" },
    { id: 2, status: "open", title: "B2" },
    { id: 3, status: "open", title: "C" },
    { id: 4, status: "open", title: "new - waits for the reload" },
  ]);
  assert.deepEqual(
    out.open.map((t) => [t.id, t.title]),
    [
      [2, "B2"],
      [3, "C"],
    ],
  );
  assert.deepEqual(
    out.done.map((t) => t.id),
    [1],
  );
  const cancelled = patchTaskLists(lists, [{ id: 2, status: "cancelled" }]);
  assert.deepEqual(
    cancelled.open.map((t) => t.id),
    [1],
  );
  const cant = patchTaskLists(lists, [{ id: 1, status: "cant_do" }]);
  assert.deepEqual(
    cant.cantDo.map((t) => t.id),
    [1],
  );
  assert.equal(patchTaskLists(lists, []), lists);
  const loading = { open: null, done: null, cantDo: null };
  assert.equal(patchTaskLists(loading, [{ id: 1, status: "open" }]), loading);
});
