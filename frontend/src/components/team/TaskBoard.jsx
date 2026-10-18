import { useState } from "react";
import { completeTask, reopenTask, setTaskStarted } from "../../api";
import { BOARD_COLUMNS, boardColumnOf, boardMove, groupBoard } from "./teamAdvancedModel";
import { formatDue, formatWhen, STATUS_LABELS, STATUS_PILL } from "./taskUtils";
import { progressBadge, taskFromConflict } from "./taskEditModel";

// The Tasks page's board view (W5): To do / In progress / Done. Drag a card
// to another column, or use its buttons (keyboard and touch). Moving to Done
// on a task that needs a photo or note opens its details to add it.

// A long column (e.g. All tasks) shows its first cards; "Show all" opens the rest.
const COLUMN_PREVIEW = 15;

const MOVE_BUTTONS = {
  todo: [
    { to: "in_progress", label: "Start" },
    { to: "done", label: "Done" },
  ],
  in_progress: [
    { to: "todo", label: "Back to To do" },
    { to: "done", label: "Done" },
  ],
  done: [{ to: "todo", label: "Reopen" }],
};

function BoardCard({ task, busy, onOpen, onMove, onDragStart }) {
  const col = boardColumnOf(task);
  const moves = (MOVE_BUTTONS[col] || []).filter((m) => boardMove(task, m.to).action);
  return (
    <li
      className={`board-card${task.is_overdue ? " board-card-overdue" : ""}`}
      draggable={!busy}
      onDragStart={(e) => onDragStart(e, task)}
      data-task-id={task.id}
    >
      <button type="button" className="board-card-title" onClick={() => onOpen(task.id)}>
        {task.title}
      </button>
      <div className="board-card-meta muted">
        <span>{task.assignee?.name}</span>
        {task.status === "open" ? (
          <span className={task.is_overdue ? "text-negative" : undefined}>{formatDue(task.due_at)}</span>
        ) : (
          <span>{formatWhen(task.completed_at || task.updated_at)}</span>
        )}
        {task.item_count > 0 && (
          <span>
            {task.items_done}/{task.item_count} steps
          </span>
        )}
        {progressBadge(task) && <span>{progressBadge(task)}</span>}
        {task.priority === "urgent" && <span className="pill pill-failed">Urgent</span>}
        {task.status !== "open" && task.status !== "done" && (
          <span className={`pill ${STATUS_PILL[task.status] || "pill-neutral"}`}>{STATUS_LABELS[task.status] || task.status}</span>
        )}
      </div>
      {task.started_by && task.status === "open" && <div className="board-card-meta muted">Started by {task.started_by.name}</div>}
      {task.status === "done" && task.completed_by && <div className="board-card-meta muted">Done by {task.completed_by.name}</div>}
      {moves.length > 0 && (
        <div className="board-card-actions">
          {moves.map((m) => (
            <button key={m.to} type="button" className="board-move" disabled={busy} onClick={() => onMove(task, m.to)}>
              {m.label}
            </button>
          ))}
        </div>
      )}
    </li>
  );
}

export default function TaskBoard({ openTasks, finishedTasks, onOpen, onOpenComplete, onChanged }) {
  const [busyId, setBusyId] = useState(null);
  const [message, setMessage] = useState(null);
  const [dragOver, setDragOver] = useState(null);
  const [expanded, setExpanded] = useState({});
  const cols = groupBoard(openTasks, finishedTasks);
  const byId = new Map([...(openTasks || []), ...(finishedTasks || [])].map((t) => [t.id, t]));

  async function move(task, to) {
    const { action, reason } = boardMove(task, to);
    if (!action) {
      if (reason) setMessage({ error: true, text: reason });
      return;
    }
    if (action === "complete_form") {
      onOpenComplete(task.id);
      return;
    }
    setBusyId(task.id);
    setMessage(null);
    try {
      let res = null;
      if (action === "start") res = await setTaskStarted(task.id, true);
      else if (action === "unstart") res = await setTaskStarted(task.id, false);
      else if (action === "complete") res = await completeTask(task.id);
      else if (action === "reopen") res = await reopenTask(task.id);
      const labels = { start: "In progress", unstart: "Back in To do", complete: "Done", reopen: "Reopened" };
      setMessage({ error: false, text: `${labels[action]}: ${task.title}` });
      // The answer is the task as saved: the card moves at once.
      onChanged(res && res.id === task.id ? res : null);
    } catch (err) {
      setMessage({ error: true, text: err.message });
      // Someone else moved it first: show where it really is now.
      const current = taskFromConflict(err);
      if (current || err.status === 409) onChanged(current);
    } finally {
      setBusyId(null);
    }
  }

  function onDragStart(e, task) {
    e.dataTransfer.setData("text/plain", String(task.id));
    e.dataTransfer.effectAllowed = "move";
  }

  function onDrop(e, to) {
    e.preventDefault();
    setDragOver(null);
    const task = byId.get(parseInt(e.dataTransfer.getData("text/plain"), 10));
    if (task) move(task, to);
  }

  return (
    <>
      {message && (
        <p className={message.error ? "error" : "success"} role="status">
          {message.text}
        </p>
      )}
      <div className="task-board">
        {BOARD_COLUMNS.map((c) => (
          <section
            key={c.key}
            className={`board-column board-column-${c.key}${dragOver === c.key ? " board-column-over" : ""}`}
            aria-label={c.label}
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(c.key);
            }}
            onDragLeave={() => setDragOver((v) => (v === c.key ? null : v))}
            onDrop={(e) => onDrop(e, c.key)}
          >
            <h3 className="board-column-head">
              {c.label} <span className="muted">({cols[c.key].length})</span>
            </h3>
            {cols[c.key].length === 0 ? (
              <p className="muted board-empty">{c.key === "done" ? "Nothing finished recently." : "Nothing here."}</p>
            ) : (
              <>
                <ul className="board-list">
                  {(expanded[c.key] ? cols[c.key] : cols[c.key].slice(0, COLUMN_PREVIEW)).map((t) => (
                    <BoardCard key={t.id} task={t} busy={busyId === t.id} onOpen={onOpen} onMove={move} onDragStart={onDragStart} />
                  ))}
                </ul>
                {cols[c.key].length > COLUMN_PREVIEW && (
                  <button
                    type="button"
                    className="link-button board-more"
                    onClick={() => setExpanded((v) => ({ ...v, [c.key]: !v[c.key] }))}
                  >
                    {expanded[c.key] ? "Show fewer" : `Show all ${cols[c.key].length}`}
                  </button>
                )}
              </>
            )}
          </section>
        ))}
      </div>
    </>
  );
}
