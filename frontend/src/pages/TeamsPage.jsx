import { useEffect, useState } from "react";
import { listTeams, createTeam, updateTeam, setTeamMembers, listUsers } from "../api";

// Team hub (docs/TEAM_HUB_DESIGN.md): the groups tasks, announcements and
// chat are sent to. Admin-only, like Users. "Everyone" always contains every
// user, so its members can't be edited; other teams start empty and an admin
// picks who's in them here. Archiving hides a team from new assignments but
// keeps its history.

// Lowercase hex. The server stores colors upper-cased, so compare with
// sameColor(), never ===. The first four are the seeded teams' colors.
const TEAM_COLORS = [
  { value: "#5b8def", label: "Sky" },
  { value: "#3ed598", label: "Mint" },
  { value: "#f5a524", label: "Amber" },
  { value: "#a78bfa", label: "Lavender" },
  { value: "#2f6fe0", label: "Blue" },
  { value: "#1f8a4c", label: "Green" },
  { value: "#d98a2b", label: "Orange" },
  { value: "#7c5cd1", label: "Purple" },
  { value: "#0e8a7d", label: "Teal" },
  { value: "#d92d3f", label: "Red" },
  { value: "#6b7280", label: "Grey" },
];

const normColor = (c) => (c || "").toLowerCase();

function ColorPicker({ value, onChange }) {
  const current = normColor(value);
  return (
    <select value={current} onChange={(e) => onChange(e.target.value)} aria-label="Team color">
      {TEAM_COLORS.map((c) => (
        <option key={c.value} value={c.value}>
          {c.label}
        </option>
      ))}
      {!TEAM_COLORS.some((c) => c.value === current) && <option value={current}>Custom ({current})</option>}
    </select>
  );
}

function Swatch({ color }) {
  return (
    <span
      aria-hidden="true"
      style={{
        display: "inline-block",
        width: "0.8rem",
        height: "0.8rem",
        borderRadius: "50%",
        background: color || "#6b7280",
        marginRight: "0.45rem",
        verticalAlign: "middle",
      }}
    />
  );
}

function MembersEditor({ team, users, onDone }) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState(team.member_ids || []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  if (team.is_everyone) {
    return <span className="muted">Everyone ({users.length}) - always every user</span>;
  }

  function toggle(id) {
    setSelected(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);
  }

  async function handleSave() {
    setBusy(true);
    setError(null);
    try {
      await setTeamMembers(team.id, selected);
      setOpen(false);
      onDone(`Members updated for ${team.name}.`);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button
        className="link-button"
        onClick={() => {
          setSelected(team.member_ids || []);
          setOpen(true);
        }}
      >
        Edit members ({(team.member_ids || []).length})
      </button>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem", alignItems: "flex-start" }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.6rem 1.1rem" }}>
        {users.map((u) => (
          <label key={u.id} style={{ display: "flex", alignItems: "center", gap: "0.35rem", fontSize: "0.85rem" }}>
            <input type="checkbox" checked={selected.includes(u.id)} onChange={() => toggle(u.id)} />
            {u.username}
          </label>
        ))}
      </div>
      <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
        <button type="button" disabled={busy} onClick={handleSave}>
          {busy ? "Saving…" : "Save members"}
        </button>
        <button type="button" className="link-button" onClick={() => setOpen(false)}>
          Cancel
        </button>
        {error && <span className="error">{error}</span>}
      </div>
    </div>
  );
}

function NameEditor({ team, onDone }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(team.name);
  const [color, setColor] = useState(normColor(team.color) || TEAM_COLORS[0].value);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function handleSave(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await updateTeam(team.id, { name: name.trim(), color });
      setOpen(false);
      onDone(`Saved ${name.trim()}.`);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <span>
        <Swatch color={team.color} />
        <strong>{team.name}</strong>
        {team.archived && <span className="muted"> (archived)</span>}{" "}
        <button
          className="link-button"
          onClick={() => {
            setName(team.name);
            setColor(normColor(team.color) || TEAM_COLORS[0].value);
            setOpen(true);
          }}
        >
          Rename
        </button>
      </span>
    );
  }

  return (
    <form onSubmit={handleSave} style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
      <input type="text" value={name} onChange={(e) => setName(e.target.value)} style={{ maxWidth: "180px" }} autoFocus />
      <ColorPicker value={color} onChange={setColor} />
      <button type="submit" disabled={busy || !name.trim()}>
        {busy ? "Saving…" : "Save"}
      </button>
      <button type="button" className="link-button" onClick={() => setOpen(false)}>
        Cancel
      </button>
      {error && <span className="error">{error}</span>}
    </form>
  );
}

function ArchiveToggle({ team, onDone }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  if (team.is_everyone) {
    return <span className="muted">-</span>;
  }

  async function handleToggle() {
    setBusy(true);
    setError(null);
    try {
      await updateTeam(team.id, { archived: !team.archived });
      onDone(team.archived ? `${team.name} restored.` : `${team.name} archived.`);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <span>
      <button type="button" className="link-button" disabled={busy} onClick={handleToggle}>
        {team.archived ? "Restore" : "Archive"}
      </button>
      {error && <span className="error"> {error}</span>}
    </span>
  );
}

export default function TeamsPage() {
  const [teams, setTeams] = useState(null);
  const [users, setUsers] = useState([]);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);

  const [newName, setNewName] = useState("");
  const [newColor, setNewColor] = useState(TEAM_COLORS[0].value);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState(null);

  function load() {
    listTeams()
      .then(setTeams)
      .catch((err) => setError(err.message));
  }

  useEffect(() => {
    load();
    listUsers()
      .then(setUsers)
      .catch((err) => setError(err.message));
  }, []);

  async function handleCreate(e) {
    e.preventDefault();
    setCreating(true);
    setCreateError(null);
    try {
      await createTeam(newName.trim(), newColor);
      setNotice(`Team "${newName.trim()}" created. Add its members below.`);
      setNewName("");
      load();
    } catch (err) {
      setCreateError(err.message);
    } finally {
      setCreating(false);
    }
  }

  function done(msg) {
    setNotice(msg);
    load();
  }

  const active = (teams || []).filter((t) => !t.archived);
  const archived = (teams || []).filter((t) => t.archived);

  function renderRows(list) {
    return list.map((t) => (
      <tr key={t.id}>
        <td>
          <NameEditor team={t} onDone={done} />
        </td>
        <td>
          {t.is_everyone ? users.length : (t.member_ids || []).length}
          {/* Oct 15: a task or schedule for an empty team reaches nobody, so it's refused. */}
          {!t.is_everyone && !t.archived && (t.member_ids || []).length === 0 && (
            <div className="muted text-negative" style={{ fontSize: "0.8rem" }}>
              No members yet - tasks can't be assigned to it
            </div>
          )}
        </td>
        <td>
          <MembersEditor team={t} users={users} onDone={done} />
        </td>
        <td>
          <ArchiveToggle team={t} onDone={done} />
        </td>
      </tr>
    ));
  }

  return (
    <>
      <div className="page-header">
        <h1>Teams</h1>
        <p className="muted">
          Admin-only: the groups that tasks, announcements and chat are sent to. "Everyone" always
          includes every user. Add people to the other teams here; a person can be in several teams.
        </p>
      </div>

      <section className="card">
        <h2 style={{ marginTop: 0, fontSize: "1.05rem" }}>Add a team</h2>
        <form onSubmit={handleCreate} style={{ display: "flex", gap: "0.75rem", alignItems: "flex-end", flexWrap: "wrap" }}>
          <label style={{ display: "flex", flexDirection: "column", fontSize: "0.82rem", gap: "0.3rem" }}>
            Name
            <input type="text" value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="e.g. Office" />
          </label>
          <label style={{ display: "flex", flexDirection: "column", fontSize: "0.82rem", gap: "0.3rem" }}>
            Color
            <ColorPicker value={newColor} onChange={setNewColor} />
          </label>
          <button type="submit" disabled={creating || !newName.trim()}>
            {creating ? "Creating…" : "Create team"}
          </button>
        </form>
        {createError && <p className="error">{createError}</p>}
      </section>

      <section className="card" style={{ marginTop: "1.1rem" }}>
        <h2 style={{ marginTop: 0, fontSize: "1.05rem" }}>Teams</h2>
        {notice && <p className="success">{notice}</p>}
        {error && <p className="error">{error}</p>}
        {!teams && !error && <p className="muted">Loading…</p>}
        {teams && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Team</th>
                  <th>Members</th>
                  <th>Edit members</th>
                  <th>Archive</th>
                </tr>
              </thead>
              <tbody>{renderRows(active)}</tbody>
            </table>
          </div>
        )}
        {archived.length > 0 && (
          <>
            <h3 style={{ fontSize: "0.95rem", marginTop: "1.2rem" }}>Archived</h3>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Team</th>
                    <th>Members</th>
                    <th>Edit members</th>
                    <th>Restore</th>
                  </tr>
                </thead>
                <tbody>{renderRows(archived)}</tbody>
              </table>
            </div>
          </>
        )}
      </section>
    </>
  );
}
