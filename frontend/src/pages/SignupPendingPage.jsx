import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { getSignupStatus } from "../api";
import { PENDING_POLL_MS, SIGNUP_STORAGE_KEY, pendingView, shouldPoll } from "../signup/signupModel.js";
import "../platform/platform.css";
import AuthLayout from "../components/AuthLayout.jsx";
import { InfoButton } from "../ui/helpBridge.jsx";

// Logged out there is no store yet: the viewer's own clock.
function viewerZone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

function savedRequest() {
  try {
    const v = JSON.parse(localStorage.getItem(SIGNUP_STORAGE_KEY) || "null");
    return v && v.id && v.t ? v : null;
  } catch {
    return null;
  }
}

// After "Create your store" (docs/PLATFORM_CONTRACT.md 14): waits for the
// master's decision. Checks every 30 s while the tab is visible, for up to
// an hour; the link keeps working later (and this browser remembers it).
export default function SignupPendingPage() {
  const [params] = useSearchParams();
  const saved = savedRequest();
  const id = params.get("id") || saved?.id;
  const token = params.get("t") || saved?.t;
  const [view, setView] = useState(id && token ? pendingView({ status: "pending" }) : pendingView(null));
  const [checkedAt, setCheckedAt] = useState(null);
  const [error, setError] = useState(null);
  const started = useRef(Date.now());

  const check = useCallback(async () => {
    if (!id || !token) return;
    try {
      const s = await getSignupStatus(id, token);
      setView(pendingView(s));
      setError(null);
      setCheckedAt(new Date());
    } catch (err) {
      if (err.status === 404) setView(pendingView(null));
      else setError("Couldn't check just now - trying again shortly.");
    }
  }, [id, token]);

  useEffect(() => {
    check();
  }, [check]);

  useEffect(() => {
    const t = setInterval(() => {
      const visible = typeof document === "undefined" || document.visibilityState !== "hidden";
      if (shouldPoll({ done: view.done, visible, startedAt: started.current, now: Date.now() })) check();
    }, PENDING_POLL_MS);
    return () => clearInterval(t);
  }, [check, view.done]);

  return (
    <AuthLayout>
      <div className="auth-card signup-card" data-testid="signup-pending" data-state={view.state}>
        <div className="auth-head">
          <div className="auth-title-row">
            <h1 className="auth-title">Your store request</h1>
            <InfoButton pageId="signup_pending" />
          </div>
        </div>
        <p className="pending-state">{view.title}</p>
        {view.text && <p className="muted">{view.text}</p>}
        {error && <p className="error">{error}</p>}
        {checkedAt && !view.done && (
          <p className="muted" style={{ fontSize: "0.78rem" }}>
            Last checked {checkedAt.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", timeZone: viewerZone() })}
          </p>
        )}
        <div className="signup-actions">
          {view.state === "approved" ? (
            <Link
              className="button-link"
              to={view.storeCode ? `/login?store=${encodeURIComponent(view.storeCode)}` : "/login"}
              data-testid="pending-sign-in"
            >
              Sign in{view.storeCode ? ` with Store ID ${view.storeCode}` : ""}
            </Link>
          ) : (
            <>
              {!view.done && (
                <button type="button" className="btn-secondary" style={{ width: "auto" }} onClick={check}>
                  Check now
                </button>
              )}
              <Link to="/login">Back to sign in</Link>
            </>
          )}
        </div>
      </div>
    </AuthLayout>
  );
}
