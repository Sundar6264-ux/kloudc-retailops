// A master added by another master signs in with a temporary password and
// must choose their own before anything else (website ready A2). The server
// refuses every other console call until then (password_change_required).
import { useState } from "react";
import { changeMasterPassword } from "../clients/console.js";
import PasswordInput from "../components/PasswordInput.jsx";
import Icon from "../ui/icons.jsx";
import { Feedback } from "./ConsoleBits.jsx";
import { platformErrorMessage } from "./platformModel.js";

export const MASTER_PASSWORD_MIN = 12;

export function passwordProblem(current, next, again) {
  if (!current) return "Enter the temporary password.";
  if ((next || "").length < MASTER_PASSWORD_MIN) return `Your new password needs at least ${MASTER_PASSWORD_MIN} characters.`;
  if (next === current) return "Choose a password different from the temporary one.";
  if (next !== again) return "The two new passwords don't match.";
  return null;
}

export default function PasswordChangePanel({ onChanged }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    const problem = passwordProblem(current, next, again);
    if (problem) return setError(problem);
    setBusy(true);
    setError(null);
    try {
      await changeMasterPassword(current, next);
      onChanged();
    } catch (err) {
      setError(platformErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="pf-row pf-password" aria-labelledby="pf-password-title" data-testid="must-change-password">
      <div className="pf-row-top">
        <Icon name="lock" size={18} />
        <h2 id="pf-password-title" className="pf-row-title">
          Choose your own password
        </h2>
      </div>
      <p className="pf-row-meta">You signed in with a temporary password. Choose your own to open the console.</p>
      <form className="pf-inline-form" onSubmit={submit} noValidate>
        <label>
          Temporary password
          <PasswordInput value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" autoFocus />
        </label>
        <label>
          New password
          <PasswordInput value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" />
        </label>
        <label>
          New password again
          <PasswordInput value={again} onChange={(e) => setAgain(e.target.value)} autoComplete="new-password" />
        </label>
        <div className="pf-actions">
          <button type="submit" disabled={busy}>
            {busy ? "Saving…" : "Save password"}
          </button>
        </div>
      </form>
      {error && <Feedback message={{ tone: "bad", text: error }} />}
    </section>
  );
}
