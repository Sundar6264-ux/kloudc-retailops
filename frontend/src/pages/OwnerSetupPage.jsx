import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { redeemOwnerSetup } from "../api";
import PasswordInput from "../components/PasswordInput.jsx";
import { ownerSetupParams, ownerSetupProblem } from "../signup/signupModel.js";
import "../platform/platform.css";

// A store owner's one-time set-up link (docs/PLATFORM_CONTRACT.md "As built -
// C": POST /api/signup/owner-setup). A master makes the link for a store
// created without a password (or a lost one); it sets the owner's password
// once, then the owner signs in on the one login page.
export default function OwnerSetupPage() {
  const [params] = useSearchParams();
  const link = ownerSetupParams(params.get("store"), params.get("token"));
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState(null);

  async function handleSubmit(e) {
    e.preventDefault();
    const problem = ownerSetupProblem(password, confirm);
    if (problem) {
      setError(problem);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const out = await redeemOwnerSetup(link.store, link.token, password);
      setDone({ store: out?.store_code || link.store, username: out?.username || "" });
    } catch (err) {
      if (err.status === 404) setError("This link is not valid any more. Ask for a new one.");
      else if (err.status === 429) setError("Too many tries. Wait a little and try again.");
      else setError(err.message || "Couldn't save the password. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="signup-page">
      <div className="card signup-card" data-testid="owner-setup" data-state={done ? "done" : link ? "form" : "invalid"}>
        <div className="page-header" style={{ paddingRight: 0, marginBottom: "0.6rem" }}>
          <h1>Set your password</h1>
        </div>
        {!link ? (
          <>
            <p className="error">This set-up link is incomplete. Open the full link you were given.</p>
            <div className="signup-actions">
              <Link to="/login">Back to sign in</Link>
            </div>
          </>
        ) : done ? (
          <>
            <p className="pending-state">Your password is set.</p>
            <p className="muted">
              Sign in with Store ID <strong>{done.store}</strong>
              {done.username ? (
                <>
                  {" "}
                  and username <strong>{done.username}</strong>
                </>
              ) : null}
              .
            </p>
            <div className="signup-actions">
              <Link className="button-link" to={`/login?store=${encodeURIComponent(done.store)}`} data-testid="setup-sign-in">
                Sign in
              </Link>
            </div>
          </>
        ) : (
          <form onSubmit={handleSubmit} noValidate>
            <p className="muted">
              Store ID <strong>{link.store}</strong>. Choose the password you'll sign in with (at least 10 characters).
            </p>
            <label className="login-field">
              New password
              <PasswordInput value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
            </label>
            <label className="login-field">
              Confirm password
              <PasswordInput value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" />
            </label>
            {error && (
              <p className="error" role="alert" data-testid="setup-error">
                {error}
              </p>
            )}
            <div className="signup-actions">
              <button type="submit" disabled={saving || !password || !confirm} style={{ width: "auto" }}>
                {saving ? "Saving…" : "Set password"}
              </button>
              <Link to="/login">Back to sign in</Link>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
