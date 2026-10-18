import { useState } from "react";
import { createTask } from "../../api";
import { dueInputToApi } from "./taskUtils";
import { emptyTeamWarning, isEmptyTeam, teamOptionLabel } from "./taskEditModel";

// New task. Who it can go to follows the backend's rule: admins and people
// with "Assign tasks" (can_assign) can pick another person, several people
// (one task each, linked as one assignment) or a team; everyone else only
// creates tasks for themselves.
// `initialTitle` / `initialDescription` / `source` pre-fill it from something
// else, e.g. a chat message ("Create task" -> source {type: "chat_message", id}).
export default function TaskCreateForm({
  canAssign,
  teams,
  people,
  onCreated,
  onCancel,
  initialTitle = "",
  initialDescription = "",
  source = null,
}) {
  const [title, setTitle] = useState(initialTitle);
  const [description, setDescription] = useState(initialDescription);
  const [assignKind, setAssignKind] = useState("me"); // me | people | team
  const [personIds, setPersonIds] = useState([]);
  const [teamId, setTeamId] = useState("");
  const [due, setDue] = useState("");
  const [requires, setRequires] = useState("none");
  const [priority, setPriority] = useState("normal");
  const [items, setItems] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const activeTeams = (teams || []).filter((t) => !t.archived);

  function togglePerson(id) {
    setPersonIds(personIds.includes(id) ? personIds.filter((x) => x !== id) : [...personIds, id]);
  }

  const pickedTeamWarning = assignKind === "team" ? emptyTeamWarning(activeTeams.find((t) => String(t.id) === String(teamId))) : null;
  const assigneeReady =
    assignKind === "me" ||
    (assignKind === "people" && personIds.length > 0) ||
    (assignKind === "team" && teamId && !pickedTeamWarning);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const body = {
      title: title.trim(),
      description: description.trim() || null,
      due_at: dueInputToApi(due),
      requires,
      priority,
      items: items
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean),
    };
    if (source) {
      body.source_type = source.type;
      body.source_id = String(source.id);
    }
    if (assignKind === "people") body.assignee_user_ids = personIds;
    if (assignKind === "team") body.assignee_team_id = Number(teamId);
    try {
      const res = await createTask(body);
      const n = res.tasks?.length || 1;
      onCreated(n > 1 ? `Sent "${body.title}" to ${n} people.` : `Created "${body.title}".`);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="task-create-form">
      <label className="task-field-wide">
        Title
        <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Clean walk-in cooler" autoFocus />
      </label>
      <label className="task-field-wide">
        Details (optional)
        <textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
      </label>

      <fieldset className="task-field-wide task-assign">
        <legend>Assign to</legend>
        <div className="segmented" role="radiogroup" aria-label="Assign to">
          <button
            type="button"
            role="radio"
            aria-checked={assignKind === "me"}
            className={assignKind === "me" ? "active" : ""}
            onClick={() => setAssignKind("me")}
          >
            Me
          </button>
          {canAssign && (
            <>
              <button
                type="button"
                role="radio"
                aria-checked={assignKind === "people"}
                className={assignKind === "people" ? "active" : ""}
                onClick={() => setAssignKind("people")}
              >
                People
              </button>
              <button
                type="button"
                role="radio"
                aria-checked={assignKind === "team"}
                className={assignKind === "team" ? "active" : ""}
                onClick={() => setAssignKind("team")}
              >
                Team
              </button>
            </>
          )}
        </div>
        {!canAssign && <p className="muted">You can create tasks for yourself. Ask an admin for "Assign tasks" to give them to others.</p>}
        {assignKind === "people" && (
          <div className="task-people">
            {people.length === 0 && <span className="muted">No one else to pick - assign to a team instead.</span>}
            {people.map((p) => (
              <label key={p.id}>
                <input type="checkbox" checked={personIds.includes(p.id)} onChange={() => togglePerson(p.id)} />
                {p.name}
              </label>
            ))}
            {personIds.length > 1 && <p className="muted">Each person gets their own copy, tracked together.</p>}
          </div>
        )}
        {assignKind === "team" && (
          <div>
            <select value={teamId} onChange={(e) => setTeamId(e.target.value)} aria-label="Team">
              <option value="">Pick a team…</option>
              {activeTeams.map((t) => (
                // Oct 15: nobody in it = nobody gets the task (the server refuses it).
                <option key={t.id} value={t.id} disabled={isEmptyTeam(t)}>
                  {teamOptionLabel(t)}
                </option>
              ))}
            </select>
            {pickedTeamWarning ? (
              <p className="error">{pickedTeamWarning}</p>
            ) : (
              <p className="muted">One task - the first person on the team to finish it closes it.</p>
            )}
          </div>
        )}
      </fieldset>

      <label>
        Due (store time)
        <input type="datetime-local" value={due} onChange={(e) => setDue(e.target.value)} />
      </label>
      <label>
        Needs to finish
        <select value={requires} onChange={(e) => setRequires(e.target.value)}>
          <option value="none">Nothing</option>
          <option value="photo">A photo</option>
          <option value="note">A note</option>
        </select>
      </label>
      <label>
        Priority
        <select value={priority} onChange={(e) => setPriority(e.target.value)}>
          <option value="normal">Normal</option>
          <option value="urgent">Urgent (notifies even in quiet hours)</option>
        </select>
      </label>
      <label className="task-field-wide">
        Sub-items (optional, one per line)
        <textarea rows={3} value={items} onChange={(e) => setItems(e.target.value)} placeholder={"Empty shelves\nWipe down\nRestock"} />
      </label>

      <div className="task-inline-actions task-field-wide">
        <button type="submit" disabled={busy || !title.trim() || !assigneeReady}>
          {busy ? "Creating…" : "Create task"}
        </button>
        <button type="button" className="link-button" onClick={onCancel}>
          Cancel
        </button>
        {error && <span className="error">{error}</span>}
      </div>
    </form>
  );
}
