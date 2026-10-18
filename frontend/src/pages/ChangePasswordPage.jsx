import { useState } from "react";
import { changePassword } from "../api";
import PasswordInput from "../components/PasswordInput.jsx";

export default function ChangePasswordPage() {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setSuccess(false);

    if (newPassword !== confirmPassword) {
      setError("New password and confirmation don't match.");
      return;
    }

    setBusy(true);
    try {
      await changePassword(currentPassword, newPassword);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setSuccess(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="page-header">
        <h1>Change password</h1>
        <p className="muted">Requires your current password. There's no "forgot password" option - ask an admin to reset it if you lose it.</p>
      </div>
      <section className="card" style={{ maxWidth: "360px" }}>
      <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "0.9rem" }}>
        <label style={{ display: "flex", flexDirection: "column", gap: "0.3rem", fontSize: "0.85rem" }}>
          Current password
          <PasswordInput
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            autoComplete="current-password"
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: "0.3rem", fontSize: "0.85rem" }}>
          New password
          <PasswordInput
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            autoComplete="new-password"
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: "0.3rem", fontSize: "0.85rem" }}>
          Confirm new password
          <PasswordInput
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            autoComplete="new-password"
          />
        </label>

        {error && <p className="error" style={{ margin: 0 }}>{error}</p>}
        {success && <p className="success" style={{ margin: 0 }}>Password updated.</p>}

        <button type="submit" disabled={busy || !currentPassword || newPassword.length < 8}>
          {busy ? "Saving…" : "Change password"}
        </button>
      </form>
      </section>
    </>
  );
}
