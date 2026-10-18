import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  getTask,
  completeTask,
  cantDoTask,
  reopenTask,
  updateTask,
  commentOnTask,
  setTaskItem,
  cancelTaskBatch,
  fetchTeamMediaUrl,
  setTaskStarted,
  replaceTaskItems,
} from "../../api";
import { formatDue, formatWhen, REQUIRES_LABELS, STATUS_LABELS, STATUS_PILL, EVENT_LABELS, eventDetailText } from "./taskUtils";
import useDialogA11y from "./useDialogA11y";
import { Term } from "../../glossary.jsx";
import useTeamSync from "./useTeamSync";
import { changedTaskIds } from "./teamSyncEngine";
import {
  assigneeValue,
  buildTaskEdit,
  editFormFromTask,
  emptyTeamWarning,
  isEmptyTeam,
  primaryAction,
  taskFromConflict,
  taskFromResponse,
  taskProgress,
  teamOptionLabel,
} from "./taskEditModel";
import "./teamTasks.css";

// The completion photo is auth-checked (GET /api/team/media/{token}), so it's
// fetched with the session and shown from an object URL.
function ProofPhoto({ photo }) {
  const [src, setSrc] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let url = null;
    let cancelled = false;
    fetchTeamMediaUrl(photo.url)
      .then((u) => {
        url = u;
        if (cancelled) URL.revokeObjectURL(u);
        else setSrc(u);
      })
      .catch((err) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [photo.url]);

  if (error) return <p className="error">Couldn't load the photo: {error}</p>;
  if (!src) return <p className="muted">Loading photo…</p>;
  return (
    <a href={src} target="_blank" rel="noopener noreferrer">
      <img src={src} alt="Completion proof" className="task-proof-photo" />
    </a>
  );
}

// Mark done with a note / photo. `run` is the drawer's (busy state, errors,
// a 409 showing the task as it is now).
function CompleteForm({ task, run, busy, onCancel }) {
  const [note, setNote] = useState("");
  const [photo, setPhoto] = useState(null);
  const needsPhoto = task.requires === "photo";
  const needsNote = task.requires === "note";

  function submit(e) {
    e.preventDefault();
    run(() => completeTask(task.id, { note: note.trim() || undefined, photo: photo || undefined }), "Marked done.");
  }

  const ready = (!needsPhoto || photo) && (!needsNote || note.trim());
  return (
    <form onSubmit={submit} className="task-inline-form">
      <label>
        Photo {needsPhoto ? "(required)" : "(optional)"}
        <input type="file" accept="image/*" onChange={(e) => setPhoto(e.target.files?.[0] || null)} />
      </label>
      <label>
        Note {needsNote ? "(required)" : "(optional)"}
        <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
      <div className="task-inline-actions">
        <button type="submit" disabled={busy || !ready}>
          {busy ? "Saving…" : "Mark done"}
        </button>
        <button type="button" className="link-button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function CantDoForm({ task, run, busy, onCancel }) {
  const [reason, setReason] = useState("");

  function submit(e) {
    e.preventDefault();
    run(() => cantDoTask(task.id, reason.trim()), "Marked can't do - the person who assigned it is notified.");
  }

  return (
    <form onSubmit={submit} className="task-inline-form">
      <label>
        Why can't it be done?
        <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} autoFocus />
      </label>
      <div className="task-inline-actions">
        <button type="submit" disabled={busy || !reason.trim()}>
          {busy ? "Saving…" : "Send"}
        </button>
        <button type="button" className="link-button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

// "Assigned to" picker: people and teams. A team with nobody in it is shown
// as "(no members)" and can't be picked (the server refuses it) unless it's
// already the assignee.
function AssigneeSelect({ value, onChange, current, teams, people, currentLabel }) {
  const shownPeople = [...(people || [])];
  if (current.startsWith("user:") && !shownPeople.some((p) => `user:${p.id}` === current)) {
    shownPeople.unshift({ id: Number(current.slice(5)), name: currentLabel || "Current person" });
  }
  const liveTeams = (teams || []).filter((t) => !t.archived || `team:${t.id}` === current);
  const picked = value.startsWith("team:") ? liveTeams.find((t) => `team:${t.id}` === value) : null;
  const warning = picked && `team:${picked.id}` !== current ? emptyTeamWarning(picked) : null;
  return (
    <label>
      Assigned to
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <optgroup label="People">
          {shownPeople.map((p) => (
            <option key={`u${p.id}`} value={`user:${p.id}`}>
              {p.name}
            </option>
          ))}
        </optgroup>
        {liveTeams.length > 0 && (
          <optgroup label="Teams">
            {liveTeams.map((t) => (
              <option key={`t${t.id}`} value={`team:${t.id}`} disabled={isEmptyTeam(t) && `team:${t.id}` !== current}>
                {teamOptionLabel(t)}
              </option>
            ))}
          </optgroup>
        )}
      </select>
      {warning && <span className="error">{warning}</span>}
    </label>
  );
}

// Oct 15: Edit (title, details, due, priority, what's needed, assignee,
// steps) or - `only="assignee"` - just Reassign. Sends only what changed,
// with the task's updated_at so an edit never overwrites someone else's
// change made meanwhile (409 -> the drawer shows the task as it is now).
function TaskEditForm({ task, only, canAssign, teams, people, run, busy, onCancel }) {
  const [base] = useState(task);
  const [form, setForm] = useState(() => editFormFromTask(task));
  const [problem, setProblem] = useState(null);
  const reassignOnly = only === "assignee";
  const canEditItems = !reassignOnly && !!base.can?.edit_items;
  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value });

  function setStep(n, text) {
    setForm({ ...form, items: form.items.map((it, i) => (i === n ? { ...it, text } : it)) });
  }

  function submit(e) {
    e.preventDefault();
    const { patch, items, error } = buildTaskEdit(base, form, { canAssign, canEditItems });
    if (error) {
      setProblem(error);
      return;
    }
    if (!patch && !items) {
      onCancel();
      return;
    }
    setProblem(null);
    const msg = reassignOnly ? "Reassigned." : "Saved.";
    run(async () => {
      let res = null;
      if (patch) res = await updateTask(base.id, patch);
      if (items) res = await replaceTaskItems(base.id, items, (res && res.updated_at) || base.updated_at);
      return res;
    }, msg);
  }

  const assignee = canAssign ? (
    <AssigneeSelect
      value={form.assignee}
      onChange={(v) => setForm({ ...form, assignee: v })}
      current={assigneeValue(base)}
      currentLabel={base.assignee?.name}
      teams={teams}
      people={people}
    />
  ) : null;

  return (
    <form onSubmit={submit} className="task-inline-form task-edit-form">
      {reassignOnly ? (
        assignee
      ) : (
        <>
          <label>
            Title
            <input type="text" value={form.title} onChange={set("title")} autoFocus />
          </label>
          <label>
            Details
            <textarea rows={2} value={form.description} onChange={set("description")} />
          </label>
          <label>
            Due (store time)
            <input type="datetime-local" value={form.due} onChange={set("due")} />
          </label>
          <div className="task-edit-row">
            <label>
              Priority
              <select value={form.priority} onChange={set("priority")}>
                <option value="normal">Normal</option>
                <option value="urgent">Urgent</option>
              </select>
            </label>
            <label>
              Needs to finish
              <select value={form.requires} onChange={set("requires")}>
                <option value="none">Nothing</option>
                <option value="photo">A photo</option>
                <option value="note">A note</option>
              </select>
            </label>
          </div>
          {assignee}
          {canEditItems && (
            <fieldset className="task-steps-edit">
              <legend>Steps</legend>
              {form.items.map((it, n) => (
                <div key={it.id ?? `new${n}`} className="task-step-edit-row">
                  <input
                    type="text"
                    value={it.text}
                    onChange={(e) => setStep(n, e.target.value)}
                    aria-label={`Step ${n + 1}`}
                    placeholder="Step"
                  />
                  <button
                    type="button"
                    className="link-button"
                    aria-label={`Remove step ${n + 1}`}
                    onClick={() => setForm({ ...form, items: form.items.filter((_, i) => i !== n) })}
                  >
                    Remove
                  </button>
                </div>
              ))}
              {form.items.length < 30 && (
                <button
                  type="button"
                  className="link-button"
                  onClick={() => setForm({ ...form, items: [...form.items, { text: "" }] })}
                >
                  + Add step
                </button>
              )}
            </fieldset>
          )}
        </>
      )}
      <div className="task-inline-actions">
        <button type="submit" disabled={busy}>
          {busy ? "Saving…" : "Save"}
        </button>
        <button type="button" className="link-button" onClick={onCancel}>
          Cancel
        </button>
        {problem && <span className="error">{problem}</span>}
      </div>
    </form>
  );
}

function BatchSummary({ batch, onChanged }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  if (!batch || batch.total <= 1) return null;
  const openCount = batch.assignees.filter((a) => a.status === "open").length;

  async function cancelOpen() {
    if (!window.confirm(`Cancel the ${openCount} still-open task(s) in this assignment?`)) return;
    setBusy(true);
    setError(null);
    try {
      await cancelTaskBatch(batch.id);
      onChanged("Cancelled the open tasks in this assignment.");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="task-detail-section">
      <h4>
        Sent to {batch.assignees.map((a) => a.name).join(", ")} · {batch.done} of {batch.total} done
      </h4>
      <ul className="task-batch-list">
        {batch.assignees.map((a) => (
          <li key={a.task_id}>
            {a.name} <span className={`pill ${STATUS_PILL[a.status] || "pill-neutral"}`}>{STATUS_LABELS[a.status] || a.status}</span>
            {a.completed_at && <span className="muted"> {formatWhen(a.completed_at)}</span>}
          </li>
        ))}
      </ul>
      {openCount > 0 && (
        <button type="button" className="btn-secondary" disabled={busy} onClick={cancelOpen}>
          Cancel the {openCount} open
        </button>
      )}
      {error && <p className="error">{error}</p>}
    </div>
  );
}

// The task drawer. Since Oct 15 it stays live (the shared Team feed reloads it
// when someone else changes this task), a write shows its answer at once
// (no extra fetch), a 409 shows the task as it is now, and the main button
// goes where the real work is (a lead's Fix Issues list, the checklist ...).
// `canAssign` / `teams` / `people` feed Reassign and Edit's assignee picker.
export default function TaskDetailDrawer({
  taskId,
  onClose,
  onChanged,
  initialMode = null,
  canAssign = false,
  teams = [],
  people = [],
}) {
  const [task, setTask] = useState(null);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [mode, setMode] = useState(initialMode); // "complete" | "cantdo" | "edit" | "reassign" | null
  useDialogA11y(onClose);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  // Only the newest answer is shown (a feed reload racing a write's answer).
  const seq = useRef(0);
  const busyRef = useRef(false);
  const staleRef = useRef(false);

  function load() {
    const mine = ++seq.current;
    getTask(taskId)
      .then((t) => mine === seq.current && setTask(t))
      .catch((err) => mine === seq.current && setError(err.message));
  }

  useEffect(() => {
    setTask(null);
    setMode(initialMode);
    setNotice(null);
    setError(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

  // Someone else changed this task: show it. Mid-write, wait for the write.
  useTeamSync((event) => {
    if (!event.resync && !changedTaskIds(event).has(taskId)) return;
    if (busyRef.current) staleRef.current = true;
    else load();
  }, { live: true });

  function show(fresh) {
    seq.current += 1; // a reload already on its way is older than this
    setTask(fresh);
  }

  function changed(msg, res) {
    setMode(null);
    setNotice(msg);
    const fresh = taskFromResponse(res, taskId);
    if (fresh) show(fresh);
    else load();
    onChanged?.(fresh);
  }

  // Returns whether it worked, so callers can keep what the user typed
  // when it didn't.
  async function run(fn, msg) {
    setBusy(true);
    busyRef.current = true;
    staleRef.current = false;
    setError(null);
    setNotice(null);
    try {
      const res = await fn();
      changed(msg, res);
      return true;
    } catch (err) {
      setError(err.message);
      const current = taskFromConflict(err);
      if (current) {
        // Someone else got there first: show the task as it is now.
        setMode(null);
        if (Array.isArray(current.events)) show(current);
        else load();
        onChanged?.(current);
      } else if (err.status === 409 || err.status === 404) {
        setMode(null);
        load();
        onChanged?.(null);
      }
      return false;
    } finally {
      setBusy(false);
      busyRef.current = false;
      if (staleRef.current) {
        staleRef.current = false;
        load();
      }
    }
  }

  async function sendComment(e) {
    e.preventDefault();
    const body = comment.trim();
    if (!body) return;
    if (await run(() => commentOnTask(taskId, body), "Comment added.")) setComment("");
  }

  const action = primaryAction(task);
  const progress = taskProgress(task);

  return (
    <div className="issue-drawer-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="issue-drawer task-drawer" role="dialog" aria-modal="true" aria-label="Task details">
        <div className="issue-drawer-head">
          <h3>{task ? task.title : "Task"}</h3>
          <button type="button" className="link-button" onClick={onClose} aria-label="Close details">
            ✕
          </button>
        </div>
        {!task && !error && <p className="muted">Loading…</p>}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {notice && <p className="success">{notice}</p>}
        {task && (
          <>
            <p className="task-detail-meta">
              <Term k="task_status" label="the task status">
                <span className={`pill ${STATUS_PILL[task.status] || "pill-neutral"}`}>{STATUS_LABELS[task.status] || task.status}</span>
              </Term>
              {(task.priority === "urgent" || task.is_overdue) && (
                <Term k="task_urgent_overdue" label="Urgent / Overdue">
                  {task.priority === "urgent" && <span className="pill pill-failed">Urgent</span>}
                  {task.is_overdue && <span className="pill pill-warn">Overdue</span>}
                </Term>
              )}
              <span>
                {task.assignee?.type === "team" ? "Team: " : ""}
                <strong>{task.assignee?.name}</strong>
              </span>
              <span className="muted">Due {formatDue(task.due_at)}</span>
              {task.requires !== "none" && <span className="muted">Needs: {REQUIRES_LABELS[task.requires]}</span>}
              {task.template_id && <span className="muted">Repeating</span>}
              {task.is_auto && <span className="muted">Automatic</span>}
              {task.status === "open" && task.started_at && (
                <span className="pill pill-info">
                  In progress{task.started_by ? ` - ${task.started_by.name}` : ""}, since {formatWhen(task.started_at)}
                </span>
              )}
            </p>
            {progress && (
              <div className="task-progress" title={`${progress.done} of ${progress.total} done`}>
                <div className="task-progress-bar" aria-hidden="true">
                  <span style={{ width: `${progress.pct}%` }} />
                </div>
                <span>
                  <strong>{progress.left}</strong> left of {progress.total}
                  {progress.done > 0 && <span className="muted"> · {progress.done} done</span>}
                </span>
              </div>
            )}
            {task.description && <p className="task-description">{task.description}</p>}

            {mode !== "edit" && task.items?.length > 0 && (
              <div className="task-detail-section">
                <h4>
                  Sub-items ({task.items_done}/{task.item_count})
                </h4>
                {task.items.map((item) => (
                  <label key={item.id} className="task-item-row">
                    <input
                      type="checkbox"
                      checked={item.done}
                      disabled={busy || task.status !== "open" || !task.can.complete}
                      onChange={() => run(() => setTaskItem(task.id, item.id, !item.done), null)}
                    />
                    <span className={item.done ? "task-item-done" : undefined}>{item.text}</span>
                    {item.done && item.done_by && <span className="muted"> - {item.done_by}</span>}
                  </label>
                ))}
              </div>
            )}

            {(task.completed_by || task.cant_do_reason || (task.status === "cancelled" && task.completion_note)) && (
              <div className="task-detail-section">
                {task.status === "done" && task.completed_by && (
                  <p>
                    Done by <strong>{task.completed_by.name}</strong>, {formatWhen(task.completed_at)}
                  </p>
                )}
                {task.completion_note && <p className="task-description">{task.completion_note}</p>}
                {task.completion_photo && <ProofPhoto photo={task.completion_photo} />}
                {task.status === "cant_do" && task.cant_do_reason && (
                  <p className="task-cant-do-reason">
                    <span className="pill pill-warn">Can't do</span> <em>{task.cant_do_reason}</em>
                  </p>
                )}
              </div>
            )}

            <BatchSummary batch={task.batch} onChanged={changed} />

            {mode === "complete" && <CompleteForm task={task} run={run} busy={busy} onCancel={() => setMode(null)} />}
            {mode === "cantdo" && <CantDoForm task={task} run={run} busy={busy} onCancel={() => setMode(null)} />}
            {(mode === "edit" || mode === "reassign") && (
              <TaskEditForm
                key={`${task.id}-${mode}`}
                task={task}
                only={mode === "reassign" ? "assignee" : null}
                canAssign={canAssign}
                teams={teams}
                people={people}
                run={run}
                busy={busy}
                onCancel={() => setMode(null)}
              />
            )}

            {!mode && (
              <div className="issue-drawer-actions task-drawer-actions">
                {action && (
                  <Link className="button-link" to={action.path}>
                    {action.label}
                  </Link>
                )}
                {task.can.start && (
                  <button type="button" className="btn-secondary" disabled={busy} onClick={() => run(() => setTaskStarted(task.id, true), "In progress.")}>
                    Start
                  </button>
                )}
                {task.can.unstart && (
                  <button type="button" className="btn-secondary" disabled={busy} onClick={() => run(() => setTaskStarted(task.id, false), "Moved back to To do.")}>
                    Back to To do
                  </button>
                )}
                {task.can.complete && (
                  <>
                    <button type="button" className="btn-secondary" disabled={busy} onClick={() => setMode("cantdo")}>
                      Can't do
                    </button>
                    {task.requires === "none" ? (
                      <button
                        type="button"
                        className={action ? "btn-secondary" : undefined}
                        disabled={busy}
                        onClick={() => run(() => completeTask(task.id), "Marked done.")}
                      >
                        Mark done
                      </button>
                    ) : (
                      <button type="button" className={action ? "btn-secondary" : undefined} disabled={busy} onClick={() => setMode("complete")}>
                        Mark done…
                      </button>
                    )}
                  </>
                )}
                {task.can.edit && (
                  <button type="button" className="btn-secondary" disabled={busy} onClick={() => setMode("edit")}>
                    Edit
                  </button>
                )}
                {task.can.edit && canAssign && (
                  <button type="button" className="btn-secondary" disabled={busy} onClick={() => setMode("reassign")}>
                    Reassign
                  </button>
                )}
                {task.can.edit && (
                  <button
                    type="button"
                    className="btn-secondary"
                    disabled={busy}
                    onClick={() => window.confirm("Cancel this task?") && run(() => updateTask(task.id, { cancelled: true }), "Task cancelled.")}
                  >
                    Cancel task
                  </button>
                )}
                {task.can.reopen && (
                  <button type="button" className="btn-secondary" disabled={busy} onClick={() => run(() => reopenTask(task.id), "Reopened.")}>
                    Reopen
                  </button>
                )}
              </div>
            )}

            <div className="task-detail-section">
              <h4>History</h4>
              <ul className="task-history">
                {(task.events || []).map((ev) => (
                  <li key={ev.id}>
                    <strong>{ev.user_name || "System"}</strong> {EVENT_LABELS[ev.kind] || ev.kind}
                    {eventDetailText(ev)}
                    <span className="muted"> · {formatWhen(ev.created_at)}</span>
                  </li>
                ))}
              </ul>
              {task.can.comment && (
                <form onSubmit={sendComment} className="task-comment-form">
                  <input type="text" placeholder="Add a comment…" value={comment} onChange={(e) => setComment(e.target.value)} />
                  <button type="submit" disabled={busy || !comment.trim()}>
                    Comment
                  </button>
                </form>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
