import { useEffect, useState } from "react";
import {
  listUsers,
  createUser,
  setUserPassword,
  listFeatures,
  setUserFeatures,
  deleteUser,
  setUserCanAssign,
  setUserHideSales,
} from "../api";
import { useAuth } from "../AuthContext.jsx";
import PasswordInput from "../components/PasswordInput.jsx";
import { Term } from "../glossary.jsx";

function SetPasswordRow({ user, onDone }) {
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function handleSubmit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await setUserPassword(user.id, password);
      setOpen(false);
      setPassword("");
      onDone(`Password set for ${user.username}.`);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button className="link-button" onClick={() => setOpen(true)}>
        Set password
      </button>
    );
  }

  return (
    <form onSubmit={handleSubmit} style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
      <PasswordInput
        placeholder="New password (min 8 chars)"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        style={{ maxWidth: "220px" }}
        autoFocus
      />
      <button type="submit" disabled={busy || password.length < 8}>
        {busy ? "Saving…" : "Save"}
      </button>
      <button type="button" className="link-button" onClick={() => setOpen(false)}>
        Cancel
      </button>
      {error && <span className="error">{error}</span>}
    </form>
  );
}

function FeatureCheckboxes({ features, selected, onChange }) {
  function toggle(key) {
    onChange(selected.includes(key) ? selected.filter((k) => k !== key) : [...selected, key]);
  }

  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: "0.6rem 1.1rem" }}>
      {features.map((f) => (
        <label key={f.key} style={{ display: "flex", alignItems: "center", gap: "0.35rem", fontSize: "0.85rem" }}>
          <input type="checkbox" checked={selected.includes(f.key)} onChange={() => toggle(f.key)} />
          {f.label}
        </label>
      ))}
    </div>
  );
}

function EditAccessRow({ user, features, onDone }) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState(user.allowed_features || []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function handleSave() {
    setBusy(true);
    setError(null);
    try {
      await setUserFeatures(user.id, selected);
      setOpen(false);
      onDone(`Access updated for ${user.username}.`);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (user.is_admin) {
    return <span className="muted">Full access (admin)</span>;
  }

  if (!open) {
    return (
      <button className="link-button" onClick={() => setOpen(true)}>
        Edit access ({(user.allowed_features || []).length})
      </button>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem", alignItems: "flex-start" }}>
      <FeatureCheckboxes features={features} selected={selected} onChange={setSelected} />
      <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
        <button type="button" disabled={busy} onClick={handleSave}>
          {busy ? "Saving…" : "Save access"}
        </button>
        <button type="button" className="link-button" onClick={() => setOpen(false)}>
          Cancel
        </button>
        {error && <span className="error">{error}</span>}
      </div>
    </div>
  );
}

// Team hub: lets a non-admin (e.g. a shift lead) assign tasks to other
// people. Admins always can, so there's nothing to toggle for them.
function CanAssignToggle({ user, onDone }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  if (user.is_admin) {
    return <span className="muted">Always (admin)</span>;
  }

  async function handleChange(e) {
    const next = e.target.checked;
    setBusy(true);
    setError(null);
    try {
      await setUserCanAssign(user.id, next);
      onDone(next ? `${user.username} can now assign tasks.` : `${user.username} can no longer assign tasks.`);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <label style={{ display: "flex", alignItems: "center", gap: "0.35rem", fontSize: "0.85rem" }}>
      <input type="checkbox" checked={!!user.can_assign} disabled={busy} onChange={handleChange} />
      {user.can_assign ? "Yes" : "No"}
      {error && <span className="error"> {error}</span>}
    </label>
  );
}

// "Hide sales numbers": keeps today's/yesterday's sales, Items Sold, order
// totals and the digest's sales out of this person's web and app. Admins
// always see sales, so there's nothing to toggle for them.
function HideSalesToggle({ user, onDone }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  if (user.is_admin) {
    return <span className="muted">Always sees sales (admin)</span>;
  }

  async function handleChange(e) {
    const next = e.target.checked;
    setBusy(true);
    setError(null);
    try {
      await setUserHideSales(user.id, next);
      onDone(next ? `Sales numbers are now hidden for ${user.username}.` : `${user.username} can see sales numbers again.`);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <label
      style={{ display: "flex", alignItems: "center", gap: "0.35rem", fontSize: "0.85rem" }}
      title="Hides today's/yesterday's sales, Items Sold, order totals and the digest's sales for this user"
    >
      <input type="checkbox" checked={!!user.hide_sales} disabled={busy} onChange={handleChange} />
      {user.hide_sales ? "Hidden" : "Shown"}
      {error && <span className="error"> {error}</span>}
    </label>
  );
}

// Two-step inline confirm (never window.confirm() - matches this app's
// existing delete-confirm pattern elsewhere, e.g. Invoice Submission's
// admin delete). The backend independently refuses self-delete and
// deleting the last remaining admin regardless of what this UI allows -
// isSelf just avoids offering an action that would only ever 400.
function DeleteUserRow({ user, isSelf, onDone }) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function handleDelete() {
    setBusy(true);
    setError(null);
    try {
      await deleteUser(user.id);
      onDone(`User "${user.username}" deleted.`);
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  if (isSelf) {
    return <span className="muted">-</span>;
  }

  if (!confirming) {
    return (
      <button
        type="button"
        className="link-button"
        style={{ color: "var(--failed-text)" }}
        onClick={() => setConfirming(true)}
      >
        Delete
      </button>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.3rem", alignItems: "flex-start" }}>
      <span style={{ fontSize: "0.8rem" }}>Delete {user.username}?</span>
      <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
        <button type="button" className="link-button" onClick={() => setConfirming(false)} disabled={busy}>
          Cancel
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={handleDelete}
          style={{ background: "var(--failed-text)", borderColor: "var(--failed-text)" }}
        >
          {busy ? "Deleting…" : "Yes, delete"}
        </button>
      </div>
      {error && <span className="error">{error}</span>}
    </div>
  );
}

export default function UsersPage() {
  const { user: currentUser } = useAuth();
  const [users, setUsers] = useState(null);
  const [features, setFeatures] = useState([]);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);

  const [newUsername, setNewUsername] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newIsAdmin, setNewIsAdmin] = useState(false);
  const [newFeatures, setNewFeatures] = useState([]);
  const [newHideSales, setNewHideSales] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState(null);

  function load() {
    listUsers()
      .then(setUsers)
      .catch((err) => setError(err.message));
  }

  useEffect(() => {
    load();
    listFeatures()
      .then((res) => setFeatures(res.features))
      .catch((err) => setError(err.message));
  }, []);

  async function handleCreate(e) {
    e.preventDefault();
    setCreating(true);
    setCreateError(null);
    try {
      await createUser(newUsername, newPassword, newIsAdmin, newFeatures, !newIsAdmin && newHideSales);
      setNewUsername("");
      setNewPassword("");
      setNewIsAdmin(false);
      setNewFeatures([]);
      setNewHideSales(false);
      setNotice(`User "${newUsername}" created.`);
      load();
    } catch (err) {
      setCreateError(err.message);
    } finally {
      setCreating(false);
    }
  }

  return (
    <>
      <div className="page-header">
        <h1>Users</h1>
        <p className="muted">
          Admin-only: create dashboard logins, set/reset passwords, and choose which tabs each
          regular user can access. An admin always has full access to everything - the checklist
          below only applies to non-admin accounts.
        </p>
      </div>
      <section className="card">
        <h2 style={{ marginTop: 0, fontSize: "1.05rem" }}>Add a user</h2>
        <form onSubmit={handleCreate} style={{ display: "flex", flexDirection: "column", gap: "0.85rem" }}>
          <div style={{ display: "flex", gap: "0.75rem", alignItems: "flex-end", flexWrap: "wrap" }}>
            <label style={{ display: "flex", flexDirection: "column", fontSize: "0.82rem", gap: "0.3rem" }}>
              Username
              <input type="text" value={newUsername} onChange={(e) => setNewUsername(e.target.value)} />
            </label>
            <label style={{ display: "flex", flexDirection: "column", fontSize: "0.82rem", gap: "0.3rem" }}>
              Password
              <PasswordInput value={newPassword} onChange={(e) => setNewPassword(e.target.value)} />
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontSize: "0.85rem", paddingBottom: "0.4rem" }}>
              <input type="checkbox" checked={newIsAdmin} onChange={(e) => setNewIsAdmin(e.target.checked)} />
              Admin
            </label>
            <button type="submit" disabled={creating || !newUsername || newPassword.length < 8}>
              {creating ? "Creating…" : "Create user"}
            </button>
          </div>
          {!newIsAdmin && (
            <div>
              <div className="muted" style={{ fontSize: "0.82rem", marginBottom: "0.4rem" }}>
                Tab access for this user (none selected = no access to anything yet):
              </div>
              <FeatureCheckboxes features={features} selected={newFeatures} onChange={setNewFeatures} />
              <label style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontSize: "0.85rem", marginTop: "0.6rem" }}>
                <input type="checkbox" checked={newHideSales} onChange={(e) => setNewHideSales(e.target.checked)} />
                Hide sales numbers (no today's/yesterday's sales, Items Sold, order totals or digest sales)
              </label>
            </div>
          )}
        </form>
        {newPassword && newPassword.length < 8 && (
          <p className="muted" style={{ marginTop: "0.5rem" }}>Password needs at least 8 characters.</p>
        )}
        {createError && <p className="error">{createError}</p>}
      </section>

      <section className="card" style={{ marginTop: "1.1rem" }}>
        <h2 style={{ marginTop: 0, fontSize: "1.05rem" }}>Users</h2>
        {notice && <p className="success">{notice}</p>}
        {error && <p className="error">{error}</p>}
        {users && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Username</th>
                  <th>Admin</th>
                  <th>Access</th>
                  <th>Assign tasks</th>
                  <th>
                    <Term k="hide_sales">Sales numbers</Term>
                  </th>
                  <th>Password</th>
                  <th>Delete</th>
                </tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.id}>
                    <td>{u.username}</td>
                    <td>{u.is_admin ? "Yes" : "No"}</td>
                    <td>
                      <EditAccessRow
                        user={u}
                        features={features}
                        onDone={(msg) => {
                          setNotice(msg);
                          load();
                        }}
                      />
                    </td>
                    <td>
                      <CanAssignToggle
                        user={u}
                        onDone={(msg) => {
                          setNotice(msg);
                          load();
                        }}
                      />
                    </td>
                    <td>
                      <HideSalesToggle
                        user={u}
                        onDone={(msg) => {
                          setNotice(msg);
                          load();
                        }}
                      />
                    </td>
                    <td>
                      <SetPasswordRow user={u} onDone={setNotice} />
                    </td>
                    <td>
                      <DeleteUserRow
                        user={u}
                        isSelf={u.id === currentUser?.id}
                        onDone={(msg) => {
                          setNotice(msg);
                          load();
                        }}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
