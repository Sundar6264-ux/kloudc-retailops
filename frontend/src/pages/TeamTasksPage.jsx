import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "../AuthContext.jsx";
import { bulkTasks, getTaskSummary, getTeamBootstrap, listTasks, listAssignablePeople } from "../api";
import TaskCreateForm from "../components/team/TaskCreateForm.jsx";
import TaskDetailDrawer from "../components/team/TaskDetailDrawer.jsx";
import TaskBoard from "../components/team/TaskBoard.jsx";
import TaskStats from "../components/team/TaskStats.jsx";
import { formatDue, formatWhen, sectionTasks, STATUS_LABELS, STATUS_PILL } from "../components/team/taskUtils";
import useTeamSync from "../components/team/useTeamSync";
import { changedTaskIds, patchTaskLists } from "../components/team/teamSyncEngine";
import {
  bulkResultText,
  hasFilters,
  personChipText,
  progressBadge,
  pruneSelection,
  SOURCE_FILTERS,
  summaryPeople,
  toggleId,
} from "../components/team/taskEditModel";
import { TermInfo } from "../glossary.jsx";
import "../components/team/teamTasks.css";
import { storeZoneCity } from "../format.js";

// Team hub Tasks (docs/TEAM_HUB_DESIGN.md §4.2): what's assigned to me or my
// teams, grouped Overdue / Can't do / Today / Upcoming, plus what's been
// finished recently. Admins and assigners also create tasks here; clicking a
// row opens its details (sub-items, proof, comments, history, actions).
// Oct 15: the lists follow the shared Team change feed (they reload only when
// a task changed, within a second, instead of every 30 s blind); filters by
// person / kind / title; admins tick several open tasks and close them at
// once; assigners get a "My team today" strip.

const VIEWS = [
  { key: "mine", label: "Mine" },
  { key: "team", label: "My teams" },
  { key: "created", label: "Created by me" },
  { key: "all", label: "All", adminOnly: true },
];

// W5: list, board (To do / In progress / Done) or - admins - completion stats.
const LAYOUTS = [
  { key: "list", label: "List" },
  { key: "board", label: "Board" },
  { key: "stats", label: "Stats", adminOnly: true },
];
const LAYOUT_KEY = "retailops_tasks_layout";

function savedLayout() {
  try {
    return localStorage.getItem(LAYOUT_KEY) || "list";
  } catch {
    return "list";
  }
}

const DONE_LIMIT = 20;
const CANT_DO_LIMIT = 50;
// A burst of changes (a bulk close, someone ticking steps) reloads once.
const RELOAD_DEBOUNCE_MS = 250;
const NO_FILTERS = { person: "", source: "", q: "" };

function TaskRow({ task, onOpen, selectable, selected, onToggle }) {
  const cls = task.is_overdue ? "task-row task-row-overdue" : "task-row";
  const badge = progressBadge(task);
  return (
    <tr className={`${cls}${selected ? " task-row-selected" : ""}`} onClick={() => onOpen(task.id)}>
      {selectable && (
        <td className="task-select-cell" onClick={(e) => e.stopPropagation()}>
          {task.status === "open" && (
            <input type="checkbox" checked={selected} onChange={() => onToggle(task.id)} aria-label={`Select ${task.title}`} />
          )}
        </td>
      )}
      <td>
        {/* The row's own click opens it; the button is for keyboard users. */}
        <button type="button" className="task-title-button">
          {task.title}
        </button>
        <div className="task-row-sub muted">
          {task.priority === "urgent" && <span className="pill pill-failed">Urgent</span>}
          {task.requires === "photo" && <span title="Needs a photo">📷 photo</span>}
          {task.requires === "note" && <span title="Needs a note">📝 note</span>}
          {task.item_count > 0 && (
            <span>
              {task.items_done}/{task.item_count} items
            </span>
          )}
          {badge && <span>{badge}</span>}
          {task.template_id && <span>repeats</span>}
          {task.is_auto && <span>automatic</span>}
        </div>
      </td>
      <td>
        {task.assignee?.type === "team" ? (
          <span>
            <span className="task-team-dot" style={{ background: task.assignee.color || "#6b7280" }} aria-hidden="true" />
            {task.assignee.name}
          </span>
        ) : (
          task.assignee?.name
        )}
      </td>
      <td className={task.is_overdue ? "text-negative" : undefined}>
        {task.status === "open" ? formatDue(task.due_at) : formatWhen(task.completed_at || task.updated_at)}
      </td>
      <td>
        {task.status === "open" && task.started_at ? (
          <span className="pill pill-info" title={task.started_by ? `Started by ${task.started_by.name}` : undefined}>
            In progress
          </span>
        ) : (
          <span className={`pill ${STATUS_PILL[task.status] || "pill-neutral"}`}>{STATUS_LABELS[task.status] || task.status}</span>
        )}
        {task.status === "done" && task.completed_by && <span className="muted"> {task.completed_by.name}</span>}
        {task.status === "cant_do" && task.cant_do_reason && (
          <div className="task-row-sub muted" title={task.cant_do_reason}>
            {task.cant_do_reason.length > 60 ? `${task.cant_do_reason.slice(0, 59)}…` : task.cant_do_reason}
          </div>
        )}
      </td>
    </tr>
  );
}

function Section({ title, tasks, onOpen, tone, selectable, selected, onToggle }) {
  if (tasks.length === 0) return null;
  return (
    <>
      <tr className="task-section-row">
        <th colSpan={selectable ? 5 : 4} className={tone ? `task-section-${tone}` : undefined}>
          {title} ({tasks.length})
        </th>
      </tr>
      {tasks.map((t) => (
        <TaskRow
          key={t.id}
          task={t}
          onOpen={onOpen}
          selectable={selectable}
          selected={selectable && selected.has(t.id)}
          onToggle={onToggle}
        />
      ))}
    </>
  );
}

// "My team today" (admins and assigners): totals plus one chip per person
// with something open or finished today. An admin's chip shows that person's
// tasks (All view, person filter).
function TeamTodayStrip({ summary, onPerson, activePerson }) {
  if (!summary) return null;
  const people = summaryPeople(summary);
  const t = summary.totals || {};
  return (
    <div className="task-today" aria-label="My team today">
      <span className="task-today-totals">
        <strong>My team today:</strong> {t.open ?? 0} open
        {t.overdue ? <span className="text-negative"> · {t.overdue} overdue</span> : null} · {t.done_today ?? 0} done
      </span>
      {people.map((p) =>
        onPerson ? (
          <button
            key={p.user_id}
            type="button"
            className={`task-today-chip${String(p.user_id) === activePerson ? " task-today-chip-active" : ""}${p.overdue ? " task-today-chip-overdue" : ""}`}
            onClick={() => onPerson(String(p.user_id))}
            title={`Show ${p.name}'s tasks`}
          >
            <strong>{p.name}</strong> {personChipText(p)}
          </button>
        ) : (
          <span key={p.user_id} className={`task-today-chip${p.overdue ? " task-today-chip-overdue" : ""}`}>
            <strong>{p.name}</strong> {personChipText(p)}
          </span>
        ),
      )}
    </div>
  );
}

export default function TeamTasksPage() {
  const { user } = useAuth();
  const [boot, setBoot] = useState(null);
  const [people, setPeople] = useState([]);
  const [view, setView] = useState("mine");
  const [lists, setLists] = useState({ open: null, done: null, cantDo: null });
  const [truncated, setTruncated] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [creating, setCreating] = useState(false);
  const [detailId, setDetailId] = useState(null);
  const [detailMode, setDetailMode] = useState(null);
  const [layout, setLayoutState] = useState(savedLayout);
  const [overdueOnly, setOverdueOnly] = useState(false);
  const [filters, setFilters] = useState(NO_FILTERS);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState(() => new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [summary, setSummary] = useState(null);

  function setLayout(key) {
    setLayoutState(key);
    try {
      localStorage.setItem(LAYOUT_KEY, key);
    } catch {
      // fine - just not remembered
    }
  }

  function openDetail(id, mode = null) {
    setDetailMode(mode);
    setDetailId(id);
  }
  const shownLayout = layout === "stats" && !user?.is_admin ? "list" : layout;

  const isDemo = !!user?.demo;
  const canAssign = !!(user?.is_admin || boot?.can_assign);
  // Real staff's work: never for the App Review demo login (the server
  // refuses both for it anyway).
  const canBulk = !!user?.is_admin && !isDemo;
  const showToday = canAssign && !isDemo;
  const { open, done, cantDo } = lists;

  // Title search waits for a pause in typing.
  useEffect(() => {
    const id = setTimeout(() => setFilters((f) => (f.q === search ? f : { ...f, q: search })), 300);
    return () => clearTimeout(id);
  }, [search]);

  // Only the newest request's answer is shown: switching Mine → My teams
  // quickly (or a reload landing late) must never show the old view's tasks.
  const seq = useRef(0);
  const load = useCallback(() => {
    const mine = ++seq.current;
    setError(null);
    const none = Promise.resolve({ items: [] });
    const narrow = {
      assigneeUserId: filters.person || undefined,
      source: filters.source || undefined,
      q: filters.q || undefined,
    };
    Promise.all([
      listTasks({ view, status: "open", overdue: overdueOnly, ...narrow }),
      overdueOnly ? none : listTasks({ view, status: "done", limit: DONE_LIMIT, ...narrow }),
      // "Can't do" tasks stay visible (with Reopen in the drawer) instead
      // of vanishing from every view.
      overdueOnly ? none : listTasks({ view, status: "cant_do", limit: CANT_DO_LIMIT, ...narrow }),
    ])
      .then(([o, d, c]) => {
        if (mine !== seq.current) return;
        setLists({ open: o.items || [], done: d.items || [], cantDo: c.items || [] });
        setTruncated(!!o.truncated);
      })
      .catch((err) => mine === seq.current && setError(err.message));
  }, [view, overdueOnly, filters]);

  const loadSummary = useCallback(() => {
    if (!showToday) return;
    getTaskSummary()
      .then(setSummary)
      .catch(() => setSummary(null)); // a nice-to-have: no strip rather than an error
  }, [showToday]);

  // Several changes close together (the feed, the write's own answer) = one
  // reload of the lists (and the strip).
  const loadRef = useRef(load);
  loadRef.current = load;
  const summaryRef = useRef(loadSummary);
  summaryRef.current = loadSummary;
  const reloadTimer = useRef(null);
  const requestReload = useCallback(() => {
    clearTimeout(reloadTimer.current);
    reloadTimer.current = setTimeout(() => {
      loadRef.current();
      summaryRef.current();
    }, RELOAD_DEBOUNCE_MS);
  }, []);
  useEffect(() => () => clearTimeout(reloadTimer.current), []);

  useEffect(() => {
    getTeamBootstrap()
      .then(setBoot)
      .catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    if (canAssign) listAssignablePeople(!!user?.is_admin).then(setPeople);
  }, [canAssign, user?.is_admin]);

  useEffect(() => {
    loadSummary();
  }, [loadSummary]);

  // A new view starts from "Loading…"; a filter change keeps the old rows
  // until the new ones arrive (no flicker while typing).
  useEffect(() => {
    setLists({ open: null, done: null, cantDo: null });
    setTruncated(false);
    setSelected(new Set());
  }, [view, overdueOnly]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    setSelected((s) => pruneSelection(s, open));
  }, [open]);

  // The shared Team feed: a task anyone changed moves here within a second;
  // a hidden tab catches up as soon as it's shown. Rows already on screen are
  // patched at once from the feed's copy, then the lists reload for anything
  // that joined or left this view.
  useTeamSync(
    (event) => {
      if (event.changes?.team?.length) getTeamBootstrap().then(setBoot).catch(() => {});
      if (event.resync) {
        requestReload();
        return;
      }
      if (changedTaskIds(event).size === 0) return;
      if (event.tasks?.length) setLists((l) => patchTaskLists(l, event.tasks));
      requestReload();
    },
    { tasks: true },
  );

  // After this page's own write (drawer, board): show its answer now.
  function onChanged(fresh) {
    if (fresh && fresh.id != null) setLists((l) => patchTaskLists(l, [fresh]));
    requestReload();
  }

  function showPerson(personId) {
    if (user?.is_admin) setView("all");
    setFilters((f) => ({ ...f, person: f.person === personId ? "" : personId }));
  }

  function clearFilters() {
    setSearch("");
    setFilters(NO_FILTERS);
  }

  async function runBulk(action) {
    const ids = [...selected];
    if (ids.length === 0) return;
    if (action === "cancel" && !window.confirm(`Cancel ${ids.length} task${ids.length === 1 ? "" : "s"}?`)) return;
    setBulkBusy(true);
    setError(null);
    setNotice(null);
    try {
      const res = await bulkTasks(ids, action);
      const titles = new Map((open || []).map((t) => [t.id, t.title]));
      setNotice(bulkResultText(res, action, titles));
      if (res.tasks?.length) setLists((l) => patchTaskLists(l, res.tasks));
      setSelected(new Set());
      requestReload();
    } catch (err) {
      setError(err.message);
    } finally {
      setBulkBusy(false);
    }
  }

  const sections = open ? sectionTasks(open) : null;
  const tasksOff = boot && boot.flags && boot.flags.tasks_enabled === false;
  const selectable = canBulk && shownLayout === "list" && !!open && open.length > 0;
  const allSelected = selectable && open.every((t) => selected.has(t.id));
  const toggle = (id) => setSelected((s) => toggleId(s, id));
  const filtered = hasFilters({ ...filters, q: search });

  return (
    <>
      <div className="page-header">
        <h1>Tasks</h1>
        <p className="muted">
          Work assigned to you or your teams. Times are store time ({storeZoneCity()}). Team tasks are done once anyone on the team finishes
          them.
        </p>
      </div>

      {tasksOff && <p className="error">Tasks are switched off on the server right now.</p>}

      <section className="card">
        <div className="issue-toolbar">
          <div className="segmented" role="tablist" style={shownLayout === "stats" ? { display: "none" } : undefined}>
            {VIEWS.filter((v) => !v.adminOnly || user?.is_admin).map((v) => (
              <button
                key={v.key}
                type="button"
                role="tab"
                aria-selected={view === v.key}
                className={view === v.key ? "active" : ""}
                onClick={() => setView(v.key)}
              >
                {v.label}
              </button>
            ))}
          </div>
          {shownLayout !== "stats" && (
            <label className="task-overdue-toggle">
              <input type="checkbox" checked={overdueOnly} onChange={(e) => setOverdueOnly(e.target.checked)} />
              Overdue only
            </label>
          )}
          <span className="issue-toolbar-spacer" />
          <div className="segmented" role="tablist" aria-label="Layout">
            {LAYOUTS.filter((l) => !l.adminOnly || user?.is_admin).map((l) => (
              <button
                key={l.key}
                type="button"
                role="tab"
                aria-selected={shownLayout === l.key}
                className={shownLayout === l.key ? "active" : ""}
                onClick={() => setLayout(l.key)}
              >
                {l.label}
              </button>
            ))}
          </div>
          {!creating && (
            <button type="button" onClick={() => setCreating(true)}>
              New task
            </button>
          )}
        </div>

        {shownLayout !== "stats" && (
          <div className="task-filters">
            <input
              type="search"
              className="task-search"
              placeholder="Search task titles"
              aria-label="Search task titles"
              value={search}
              maxLength={200}
              onChange={(e) => setSearch(e.target.value)}
            />
            <select
              value={filters.source}
              onChange={(e) => setFilters({ ...filters, source: e.target.value })}
              aria-label="Kind of task"
            >
              {SOURCE_FILTERS.map((s) => (
                <option key={s.key} value={s.key}>
                  {s.label}
                </option>
              ))}
            </select>
            {canAssign && people.length > 1 && (
              <select value={filters.person} onChange={(e) => setFilters({ ...filters, person: e.target.value })} aria-label="Person">
                <option value="">Anyone</option>
                {people.map((p) => (
                  <option key={p.id} value={String(p.id)}>
                    {p.name}
                  </option>
                ))}
              </select>
            )}
            {filtered && (
              <button type="button" className="link-button" onClick={clearFilters}>
                Clear filters
              </button>
            )}
          </div>
        )}

        {showToday && shownLayout !== "stats" && (
          <TeamTodayStrip summary={summary} onPerson={user?.is_admin ? showPerson : null} activePerson={filters.person} />
        )}

        {creating && (
          <TaskCreateForm
            canAssign={canAssign}
            teams={boot?.teams || []}
            people={people}
            onCancel={() => setCreating(false)}
            onCreated={(msg) => {
              setCreating(false);
              setNotice(msg);
              requestReload();
            }}
          />
        )}

        {notice && <p className="success">{notice}</p>}
        {shownLayout === "stats" && <TaskStats />}
        {shownLayout !== "stats" && error && <p className="error">{error}</p>}
        {shownLayout !== "stats" && !open && !error && <p className="muted">Loading…</p>}

        {selectable && selected.size > 0 && (
          <div className="task-bulk-bar" role="region" aria-label="Selected tasks">
            <strong>{selected.size} selected</strong>
            <button type="button" disabled={bulkBusy} onClick={() => runBulk("done")}>
              {bulkBusy ? "Saving…" : "Mark done"}
            </button>
            <button type="button" className="btn-secondary" disabled={bulkBusy} onClick={() => runBulk("cancel")}>
              Cancel tasks
            </button>
            <button type="button" className="link-button" disabled={bulkBusy} onClick={() => setSelected(new Set())}>
              Clear
            </button>
            <span className="muted">Tasks that need a photo are skipped - open them to add it.</span>
          </div>
        )}

        {shownLayout === "board" && open && (
          <TaskBoard
            openTasks={open}
            finishedTasks={[...(done || []), ...(cantDo || [])]}
            onOpen={(id) => openDetail(id)}
            onOpenComplete={(id) => openDetail(id, "complete")}
            onChanged={onChanged}
          />
        )}

        {shownLayout === "list" && open && overdueOnly && open.length === 0 && <p className="muted">Nothing overdue.</p>}

        {shownLayout === "list" && open && (
          <>
            {open.length === 0 && (done || []).length === 0 && (cantDo || []).length === 0 && (
              <p className="muted">{filtered ? "No tasks match these filters." : "Nothing here yet."}</p>
            )}
            {open.length === 0 && ((done || []).length > 0 || (cantDo || []).length > 0) && (
              <p className="muted">All caught up - no open tasks.</p>
            )}
            {truncated && (
              <p className="muted">
                Showing the first {open.length} open tasks, oldest due first. Finish or cancel old ones to see the rest.
              </p>
            )}
            {(open.length > 0 || (done || []).length > 0 || (cantDo || []).length > 0) && (
              <div className="table-wrap">
                <table className="task-table">
                  <thead>
                    <tr>
                      {selectable && (
                        <th className="task-select-cell">
                          <input
                            type="checkbox"
                            checked={allSelected}
                            onChange={() => setSelected(allSelected ? new Set() : new Set(open.map((t) => t.id)))}
                            aria-label="Select every open task shown"
                          />
                        </th>
                      )}
                      <th>Task</th>
                      <th>Assigned to</th>
                      <th>Due / finished</th>
                      <th>
                        Status <TermInfo k="task_status" label="the task status" />
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    <Section title="Overdue" tasks={sections.overdue} onOpen={openDetail} tone="overdue" selectable={selectable} selected={selected} onToggle={toggle} />
                    <Section title="Can't do" tasks={cantDo || []} onOpen={openDetail} tone="overdue" selectable={selectable} selected={selected} onToggle={toggle} />
                    <Section title="Today" tasks={sections.today} onOpen={openDetail} selectable={selectable} selected={selected} onToggle={toggle} />
                    <Section title="Upcoming" tasks={sections.upcoming} onOpen={openDetail} selectable={selectable} selected={selected} onToggle={toggle} />
                    <Section title="Recently finished" tasks={done || []} onOpen={openDetail} tone="done" selectable={selectable} selected={selected} onToggle={toggle} />
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </section>

      {detailId && (
        <TaskDetailDrawer
          taskId={detailId}
          initialMode={detailMode}
          onClose={() => setDetailId(null)}
          onChanged={onChanged}
          canAssign={canAssign}
          teams={boot?.teams || []}
          people={people}
        />
      )}
    </>
  );
}
