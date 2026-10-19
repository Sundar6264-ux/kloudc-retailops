import { useEffect, useRef, useState } from "react";
import {
  getTeamBootstrap,
  listAssignablePeople,
  listTaskTemplates,
  createTaskTemplate,
  updateTaskTemplate,
  deactivateTaskTemplate,
  previewTaskTemplate,
  createTaskFromTemplate,
} from "../api";
import { dayAssigneesFromApi, dayAssigneesToApi, stepsFromText } from "../components/team/teamAdvancedModel";
import { storeZoneCity } from "../format.js";
import { emptyTeamWarning, isEmptyTeam, teamOptionLabel } from "../components/team/taskEditModel";
import {
  buildRrule,
  parseRrule,
  describeRepeat,
  describeAhead,
  formatWhen,
  dueInputToApi,
  DEFAULT_REPEAT,
  WEEKDAYS,
  REQUIRES_LABELS,
} from "../components/team/taskUtils";
import PageHeader from "../ui/PageHeader.jsx";

// Team hub recurring tasks (design §4.2, §5): a schedule describes when the
// task is DUE (store time) and how long before that it appears. Admin-only
// to create/edit, like the backend. The live preview is the backend's own
// next-5 calculation, so DST or rule mistakes show up before saving.

const AHEAD_CHOICES = [
  { minutes: 0, label: "At the due time" },
  { minutes: 60, label: "1 hour before" },
  { minutes: 120, label: "2 hours before" },
  { minutes: 240, label: "4 hours before" },
  { minutes: 420, label: "7 hours before (default)" },
  { minutes: 720, label: "12 hours before" },
  { minutes: 1440, label: "The day before" },
];

const MONTH_DAYS = Array.from({ length: 31 }, (_, i) => i + 1);

function RepeatBuilder({ repeat, onChange }) {
  // Any change here replaces a custom rule with the pattern shown.
  function set(patch) {
    onChange({ ...repeat, custom: false, raw: null, ...patch });
  }
  function toggleDay(code) {
    const days = repeat.days.includes(code) ? repeat.days.filter((d) => d !== code) : [...repeat.days, code];
    set({ days: days.length ? days : [code] });
  }
  return (
    <div className="task-repeat">
      {repeat.custom && (
        <p className="task-notice" role="note">
          This schedule uses options this page can't edit (<code>{repeat.raw}</code>). It's kept exactly as it is unless you
          pick a new pattern below.
        </p>
      )}
      <div className="segmented" role="radiogroup" aria-label="Repeats">
        {[
          ["daily", "Every day"],
          ["weekdays", "Weekdays"],
          ["weekly", "Weekly on…"],
          ["monthly", "Monthly"],
        ].map(([kind, label]) => (
          <button
            key={kind}
            type="button"
            role="radio"
            aria-checked={repeat.kind === kind}
            className={repeat.kind === kind ? "active" : ""}
            onClick={() => set({ kind })}
          >
            {label}
          </button>
        ))}
      </div>
      {repeat.kind === "weekly" && (
        <div className="task-weekdays">
          {WEEKDAYS.map((d) => (
            <label key={d.code}>
              <input type="checkbox" checked={repeat.days.includes(d.code)} onChange={() => toggleDay(d.code)} />
              {d.label}
            </label>
          ))}
        </div>
      )}
      <div className="task-repeat-row">
        {repeat.kind === "monthly" && (
          <label>
            Day of month
            <select value={repeat.monthDay} onChange={(e) => set({ monthDay: parseInt(e.target.value, 10) })}>
              {MONTH_DAYS.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
              <option value={-1}>Last day of the month</option>
            </select>
          </label>
        )}
        <label>
          Due at (store time)
          <input type="time" value={repeat.time} onChange={(e) => set({ time: e.target.value || "00:00" })} />
        </label>
      </div>
      {repeat.kind === "monthly" && !repeat.custom && repeat.monthDay > 28 && (
        <p className="task-notice" role="note">
          Months without a day {repeat.monthDay} (like February) are skipped. Pick "Last day of the month" to cover every
          month.
        </p>
      )}
    </div>
  );
}

function Preview({ rrule, ahead }) {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const seq = useRef(0);

  useEffect(() => {
    const mine = ++seq.current;
    const t = setTimeout(() => {
      previewTaskTemplate(rrule, ahead)
        .then((r) => mine === seq.current && (setRows(r), setError(null)))
        .catch((err) => mine === seq.current && (setRows(null), setError(err.message)));
    }, 250);
    return () => clearTimeout(t);
  }, [rrule, ahead]);

  if (error) return <p className="error">{error}</p>;
  if (!rows) return <p className="muted">Working out the next dates…</p>;
  return (
    <div className="task-preview">
      <strong>Next {rows.length}</strong>
      <ul>
        {rows.map((r) => (
          <li key={r.due_at_utc}>
            Due {formatWhen(r.due_at_utc)}
            <span className="muted"> · appears {formatWhen(r.generate_at_local)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// W5: who does it on particular weekdays ("Sat: Alex, Sun: Kitchen");
// a day left on "Same as above" uses the template's own assignee.
function DayAssignees({ value, onChange, teams, people }) {
  return (
    <div className="tpl-days">
      {WEEKDAYS.map((d) => (
        <label key={d.code} className="tpl-day">
          <span>{d.label}</span>
          <select value={value[d.code] || ""} onChange={(e) => onChange({ ...value, [d.code]: e.target.value })}>
            <option value="">Same as above</option>
            <optgroup label="Teams">
              {teams.map((t) => (
                <option key={`t${t.id}`} value={`team:${t.id}`} disabled={isEmptyTeam(t) && value[d.code] !== `team:${t.id}`}>
                  {teamOptionLabel(t)}
                </option>
              ))}
            </optgroup>
            <optgroup label="People">
              {people.map((p) => (
                <option key={`u${p.id}`} value={`user:${p.id}`}>
                  {p.name}
                </option>
              ))}
            </optgroup>
          </select>
        </label>
      ))}
    </div>
  );
}

function TemplateForm({ initial, teams, people, onSaved, onCancel }) {
  const [repeats, setRepeats] = useState(initial ? initial.repeats !== false : true);
  const [stepsText, setStepsText] = useState((initial?.items || []).join("\n"));
  const [byDay, setByDay] = useState(dayAssigneesFromApi(initial?.day_assignees));
  const [showDays, setShowDays] = useState((initial?.day_assignees || []).length > 0);
  const [title, setTitle] = useState(initial?.title || "");
  const [description, setDescription] = useState(initial?.description || "");
  const [repeat, setRepeat] = useState(initial ? parseRrule(initial.rrule) : DEFAULT_REPEAT);
  const [ahead, setAhead] = useState(initial?.generate_ahead_minutes ?? 420);
  const [assignKind, setAssignKind] = useState(initial?.assignee_user_id ? "person" : "team");
  const [userId, setUserId] = useState(initial?.assignee_user_id ? String(initial.assignee_user_id) : "");
  const [teamId, setTeamId] = useState(initial?.assignee_team_id ? String(initial.assignee_team_id) : "");
  const [requires, setRequires] = useState(initial?.requires || "none");
  const [priority, setPriority] = useState(initial?.priority || "normal");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const rrule = buildRrule(repeat);
  const steps = stepsFromText(stepsText);
  const activeTeams = (teams || []).filter((t) => !t.archived);
  const aheadChoices = AHEAD_CHOICES.some((c) => c.minutes === ahead)
    ? AHEAD_CHOICES
    : [...AHEAD_CHOICES, { minutes: ahead, label: describeAhead(ahead) }];
  const assigneeReady = (assignKind === "person" && userId) || (assignKind === "team" && teamId);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const body = {
      title: title.trim(),
      description: description.trim() || null,
      repeats,
      rrule: repeats ? rrule : "",
      items: steps,
      day_assignees: repeats ? dayAssigneesToApi(Object.entries(byDay).map(([day, who]) => ({ day, who }))) : {},
      generate_ahead_minutes: ahead,
      requires,
      priority,
      assignee_user_id: assignKind === "person" ? Number(userId) : null,
      assignee_team_id: assignKind === "team" ? Number(teamId) : null,
    };
    try {
      if (initial) await updateTaskTemplate(initial.id, body);
      else await createTaskTemplate(body);
      onSaved(
        initial
          ? `Saved "${body.title}" - changes apply to future tasks.`
          : repeats
            ? `Scheduled "${body.title}".`
            : `Saved the template "${body.title}".`,
      );
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
      <label className="task-field-wide">
        Steps (optional, one per line - ticked off on every task made from it)
        <textarea
          rows={Math.min(8, Math.max(3, steps.length + 1))}
          value={stepsText}
          onChange={(e) => setStepsText(e.target.value)}
          placeholder={"e.g.\nLights and music on\nCount the till\nUnlock the doors"}
        />
      </label>
      <fieldset className="task-field-wide">
        <legend>Repeats</legend>
        <div className="segmented" role="radiogroup" aria-label="Repeat or not">
          <button type="button" role="radio" aria-checked={repeats} className={repeats ? "active" : ""} onClick={() => setRepeats(true)}>
            On a schedule
          </button>
          <button type="button" role="radio" aria-checked={!repeats} className={!repeats ? "active" : ""} onClick={() => setRepeats(false)}>
            Only when I create it
          </button>
        </div>
        {repeats ? (
          <>
            <RepeatBuilder repeat={repeat} onChange={setRepeat} />
            <p className="muted">{describeRepeat(rrule)}</p>
          </>
        ) : (
          <p className="muted">Saved as a template: use "Create task now" whenever it's needed.</p>
        )}
      </fieldset>
      <label hidden={!repeats}>
        Appears
        <select value={ahead} onChange={(e) => setAhead(Number(e.target.value))}>
          {aheadChoices.map((c) => (
            <option key={c.minutes} value={c.minutes}>
              {c.label}
            </option>
          ))}
        </select>
      </label>
      <fieldset className="task-assign">
        <legend>Assign to</legend>
        <div className="segmented" role="radiogroup" aria-label="Assign to">
          <button
            type="button"
            role="radio"
            aria-checked={assignKind === "team"}
            className={assignKind === "team" ? "active" : ""}
            onClick={() => setAssignKind("team")}
          >
            Team
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={assignKind === "person"}
            className={assignKind === "person" ? "active" : ""}
            onClick={() => setAssignKind("person")}
          >
            Person
          </button>
        </div>
        {assignKind === "team" ? (
          <>
            <select value={teamId} onChange={(e) => setTeamId(e.target.value)} aria-label="Team">
              <option value="">Pick a team…</option>
              {activeTeams.map((t) => (
                // Oct 15: an empty team can't be given a schedule (the server refuses it).
                <option key={t.id} value={t.id} disabled={isEmptyTeam(t) && String(t.id) !== String(teamId)}>
                  {teamOptionLabel(t)}
                </option>
              ))}
            </select>
            {emptyTeamWarning(activeTeams.find((t) => String(t.id) === String(teamId))) && (
              <p className="error">{emptyTeamWarning(activeTeams.find((t) => String(t.id) === String(teamId)))}</p>
            )}
          </>
        ) : (
          <select value={userId} onChange={(e) => setUserId(e.target.value)} aria-label="Person">
            <option value="">Pick a person…</option>
            {people.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        )}
      </fieldset>
      {repeats && (
        <div className="task-field-wide">
          <button type="button" className="link-button tpl-days-toggle" aria-expanded={showDays} onClick={() => setShowDays((v) => !v)}>
            {showDays ? "Hide the weekday assignees" : "Someone else on some days?"}
          </button>
          {showDays && <DayAssignees value={byDay} onChange={setByDay} teams={activeTeams} people={people} />}
        </div>
      )}
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
          <option value="urgent">Urgent</option>
        </select>
      </label>
      {repeats && (
        <div className="task-field-wide">
          <Preview rrule={rrule} ahead={ahead} />
        </div>
      )}
      <div className="task-inline-actions task-field-wide">
        <button type="submit" disabled={busy || !title.trim() || !assigneeReady}>
          {busy ? "Saving…" : initial ? "Save" : repeats ? "Create schedule" : "Save template"}
        </button>
        <button type="button" className="link-button" onClick={onCancel}>
          Cancel
        </button>
        {error && <span className="error">{error}</span>}
      </div>
    </form>
  );
}

// W5 "Create task now": one task from the template, for its own assignee
// (today's weekday one, if set) or someone picked here.
function CreateNowForm({ template, teams, people, onDone, onCancel }) {
  const [who, setWho] = useState("");
  const [due, setDue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const body = { due_at: dueInputToApi(due) };
    if (who.startsWith("team:")) body.assignee_team_id = Number(who.slice(5));
    else if (who.startsWith("user:")) body.assignee_user_ids = [Number(who.slice(5))];
    try {
      const out = await createTaskFromTemplate(template.id, body);
      const task = out.tasks?.[0];
      onDone(`Created "${template.title}"${task?.assignee?.name ? ` for ${task.assignee.name}` : ""}.`);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="task-inline-form tpl-create-now">
      <label>
        For
        <select value={who} onChange={(e) => setWho(e.target.value)}>
          <option value="">{template.assignee?.name ? `${template.assignee.name} (the template's)` : "The template's assignee"}</option>
          <optgroup label="Teams">
            {teams.map((t) => (
              <option key={`t${t.id}`} value={`team:${t.id}`} disabled={isEmptyTeam(t)}>
                {teamOptionLabel(t)}
              </option>
            ))}
          </optgroup>
          <optgroup label="People">
            {people.map((p) => (
              <option key={`u${p.id}`} value={`user:${p.id}`}>
                {p.name}
              </option>
            ))}
          </optgroup>
        </select>
      </label>
      <label>
        Due (optional, store time)
        <input type="datetime-local" value={due} onChange={(e) => setDue(e.target.value)} />
      </label>
      <div className="task-inline-actions">
        <button type="submit" disabled={busy}>
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

export default function TeamTemplatesPage() {
  const [templates, setTemplates] = useState(null);
  const [teams, setTeams] = useState([]);
  const [people, setPeople] = useState([]);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [editing, setEditing] = useState(null); // null | "new" | template
  const [usingId, setUsingId] = useState(null);

  function load() {
    listTaskTemplates()
      .then(setTemplates)
      .catch((err) => setError(err.message));
  }

  useEffect(() => {
    load();
    getTeamBootstrap()
      .then((b) => setTeams(b.teams || []))
      .catch((err) => setError(err.message));
    listAssignablePeople(true).then(setPeople);
  }, []);

  async function deactivate(t) {
    if (!window.confirm(`Stop "${t.title}"? Tasks already created stay as they are.`)) return;
    try {
      await deactivateTaskTemplate(t.id);
      setNotice(`Stopped "${t.title}".`);
      load();
    } catch (err) {
      setError(err.message);
    }
  }

  async function reactivate(t) {
    try {
      await updateTaskTemplate(t.id, { active: true });
      setNotice(`Restarted "${t.title}".`);
      load();
    } catch (err) {
      setError(err.message);
    }
  }

  const active = (templates || []).filter((t) => t.active);
  const stopped = (templates || []).filter((t) => !t.active);

  const activeTeams = teams.filter((t) => !t.archived);

  function rows(list) {
    return list.map((t) => [
      <tr key={t.id}>
        <td>
          <strong>{t.title}</strong>
          <div className="muted task-row-sub">
            {t.requires !== "none" && <span>Needs {REQUIRES_LABELS[t.requires].toLowerCase()}</span>}
            {t.priority === "urgent" && <span className="pill pill-failed">Urgent</span>}
            {(t.items || []).length > 0 && (
              <span title={t.items.join("\n")}>
                {t.items.length} step{t.items.length === 1 ? "" : "s"}
              </span>
            )}
          </div>
        </td>
        <td>
          {t.assignee?.name}
          {(t.day_assignees || []).length > 0 && (
            <div className="muted task-row-sub">
              {t.day_assignees.map((d) => `${WEEKDAYS.find((w) => w.code === d.day)?.label}: ${d.name}`).join(" · ")}
            </div>
          )}
        </td>
        <td>
          {t.repeats === false ? (
            <span className="muted">Template only - created when needed</span>
          ) : (
            <>
              {/* Oct 15: the server's words, so the app shows the same text. */}
              {t.schedule_text || describeRepeat(t.rrule)}
              <div className="muted">Appears {describeAhead(t.generate_ahead_minutes)}</div>
            </>
          )}
        </td>
        <td>
          {t.active && t.next ? (
            <>
              {formatWhen(t.next.due_at_utc)}
              {t.next.assignee && t.next.assignee.name !== t.assignee?.name && <div className="muted">{t.next.assignee.name}</div>}
            </>
          ) : (
            <span className="muted">-</span>
          )}
        </td>
        <td className="tpl-actions">
          <button type="button" className="link-button" onClick={() => setUsingId(usingId === t.id ? null : t.id)}>
            Create task now
          </button>{" "}
          <button type="button" className="link-button" onClick={() => setEditing(t)}>
            Edit
          </button>{" "}
          {t.active ? (
            <button type="button" className="link-button" onClick={() => deactivate(t)}>
              Stop
            </button>
          ) : (
            <button type="button" className="link-button" onClick={() => reactivate(t)}>
              Restart
            </button>
          )}
        </td>
      </tr>,
      usingId === t.id && (
        <tr key={`${t.id}-use`} className="tpl-use-row">
          <td colSpan={5}>
            <CreateNowForm
              template={t}
              teams={activeTeams}
              people={people}
              onCancel={() => setUsingId(null)}
              onDone={(msg) => {
                setUsingId(null);
                setNotice(msg);
              }}
            />
          </td>
        </tr>
      ),
    ]);
  }

  return (
    <>
      <PageHeader pageId="team_templates" title="Recurring tasks and templates">
        <p className="muted">
          Reusable tasks with steps (e.g. "Open store"). On a schedule they create tasks automatically ("due every Monday 3 PM,
          appears 8 AM"), optionally for someone else on some weekdays; any template can also be used with "Create task now".
          Times are store time ({storeZoneCity()}). Edits apply to future tasks only.
        </p>
      </PageHeader>

      <section className="card">
        <div className="issue-toolbar">
          <span className="issue-toolbar-spacer" />
          {!editing && (
            <button type="button" onClick={() => setEditing("new")}>
              New template
            </button>
          )}
        </div>
        {editing && (
          <TemplateForm
            key={editing === "new" ? "new" : editing.id}
            initial={editing === "new" ? null : editing}
            teams={teams}
            people={people}
            onCancel={() => setEditing(null)}
            onSaved={(msg) => {
              setEditing(null);
              setNotice(msg);
              load();
            }}
          />
        )}
        {notice && <p className="success">{notice}</p>}
        {error && <p className="error">{error}</p>}
        {!templates && !error && <p className="muted">Loading…</p>}
        {templates && templates.length === 0 && <p className="muted">No recurring tasks yet.</p>}
        {active.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Task</th>
                  <th>Assigned to</th>
                  <th>Schedule</th>
                  <th>Next due</th>
                  <th />
                </tr>
              </thead>
              <tbody>{rows(active)}</tbody>
            </table>
          </div>
        )}
        {stopped.length > 0 && (
          <>
            <h3 style={{ fontSize: "0.95rem", marginTop: "1.2rem" }}>Stopped</h3>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Task</th>
                    <th>Assigned to</th>
                    <th>Schedule</th>
                    <th>Next due</th>
                    <th />
                  </tr>
                </thead>
                <tbody>{rows(stopped)}</tbody>
              </table>
            </div>
          </>
        )}
      </section>
    </>
  );
}
