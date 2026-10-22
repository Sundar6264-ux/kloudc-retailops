import { useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../AuthContext.jsx";
import PasswordInput from "../components/PasswordInput.jsx";
import { useBranding } from "../store/StoreContext.jsx";
import AuthLayout from "../components/AuthLayout.jsx";
import { InfoButton } from "../ui/helpBridge.jsx";
import Icon from "../ui/icons.jsx";
import {
  cleanTotp,
  initialStoreId,
  isPlatformCode,
  loginErrorMessage,
  movedLoginUrl,
  normalizeStoreCode,
  readLastStore,
  readSignedInStore,
  rememberStore,
  validStoreCode,
} from "../login/loginModel.js";
import "../platform/platform.css";
import { takeSignOutReason } from "../login/signOutReason.js";
import "../platform/console.css";

function storage() {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

// The one login page (docs/PLATFORM_CONTRACT.md 4.5, 14): Store ID,
// Username, Password for everyone - store staff, store admins and (Store ID
// PLATFORM) the master admins. The Store ID is remembered on this browser.
//
// Store ID privacy (owner, 2026-10; PLATFORM_CONTRACT "As built - Store ID
// privacy"): the page never looks a Store ID up and never shows a store's
// name or logo while someone types - guessing IDs must not tell which stores exist. The
// store's name appears in the app header after a successful sign-in. The
// only name shown here is this browser's own memory of the last store
// signed in to successfully ("Sign in to <name> again").
export default function LoginPage() {
  const { login } = useAuth();
  // Product branding before login (a single-store server: its own Store ID
  // prefills the field).
  const brand = useBranding();
  const [searchParams] = useSearchParams();
  const [storeId, setStoreId] = useState(() =>
    initialStoreId({
      queryStore: searchParams.get("store"),
      remembered: readLastStore(storage()),
      brandingCode: brand.code,
    }),
  );
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  // Two-step sign-in (masters with TOTP): shown after the server asks.
  const [needTotp, setNeedTotp] = useState(false);
  const [totp, setTotp] = useState("");
  const [error, setError] = useState(null);
  // Signed out because a master paused the store (A12): shown once.
  const [signedOutReason] = useState(() => takeSignOutReason(typeof window !== "undefined" ? window.sessionStorage : null));
  const [loading, setLoading] = useState(false);
  // The last store signed in to successfully on this browser (never from
  // a lookup): offered as one tap.
  const [again] = useState(() => readSignedInStore(storage()));
  const usernameRef = useRef(null);

  const code = validStoreCode(storeId);

  function pickAgain() {
    setStoreId(again.code);
    setNeedTotp(false);
    setError(null);
    usernameRef.current?.focus();
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (!code) {
      setError("Enter your Store ID (letters, digits and _).");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await login(code, username.trim(), password, needTotp ? totp : "");
      rememberStore(storage(), code);
      // Set only when the backend redirected here from a link that needed
      // login first (a WhatsApp invoice link: /public/invoices/...) - a real
      // backend route, not an SPA route, so a full page navigation. Only a
      // path on this site.
      const next = searchParams.get("next");
      if (next && next.startsWith("/") && !next.startsWith("//")) window.location.href = next;
    } catch (err) {
      const info = loginErrorMessage(err);
      if (info.kind === "totp") {
        setNeedTotp(true);
        setTotp("");
        setError(info.message);
        return;
      }
      if (info.kind === "moved") {
        // Contract 12.3: this store has its own server now.
        rememberStore(storage(), code);
        const url = movedLoginUrl(info.apiBaseUrl, code);
        if (url) {
          setError(info.message);
          window.location.assign(url);
          return;
        }
        setError("This store moved to its own server. Ask the store owner for its address.");
      } else {
        setError(info.message);
      }
    } finally {
      setLoading(false);
    }
  }

  // PLATFORM is fixed and known, so its own heading may stay.
  const platform = isPlatformCode(storeId);

  return (
    <AuthLayout testId="login-page" retailOps>
      <form onSubmit={handleSubmit} className="auth-card login-card" noValidate>
        <div className="auth-head">
          <div className="auth-title-row">
            <h1 className="auth-title" data-testid="login-store-name">
              {platform ? "Platform console" : "Sign in to your store"}
            </h1>
            <InfoButton pageId="login" />
          </div>
          <p className="auth-sub">
            {platform ? "KloudC staff only. You'll be asked for your 6-digit code." : "Use the Store ID, username and password your store gave you."}
          </p>
        </div>

        {again && !platform && (
          <button
            type="button"
            className="login-again"
            data-testid="login-again"
            aria-pressed={code === again.code}
            onClick={pickAgain}
          >
            <Icon name="store" size={16} />
            <span>Sign in to {again.name} again</span>
          </button>
        )}

        <label className="login-field">
          Store ID
          <input
            type="text"
            name="store_id"
            data-testid="login-store-id"
            value={storeId}
            onChange={(e) => {
              setStoreId(e.target.value.replace(/[^A-Za-z0-9_\- ]/g, "").slice(0, 32));
              setNeedTotp(false);
            }}
            onBlur={(e) => {
              const n = normalizeStoreCode(e.target.value);
              if (n !== storeId) setStoreId(n);
            }}
            autoComplete="organization"
            autoCapitalize="characters"
            spellCheck={false}
            placeholder="e.g. MAIN_STREET"
            autoFocus={!storeId}
            className="kc-mono-input"
            aria-describedby="login-store-hint"
          />
          <span className="field-hint" id="login-store-hint">
            It's in your welcome email, or ask your store owner.
          </span>
        </label>
        <label className="login-field">
          Username
          <input
            type="text"
            name="username"
            value={username}
            onChange={(e) => {
              setUsername(e.target.value);
              setNeedTotp(false);
            }}
            autoComplete="username"
            ref={usernameRef}
            autoFocus={!!storeId}
          />
        </label>
        <label className="login-field">
          Password
          <PasswordInput value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
        </label>
        {needTotp && (
          <label className="login-field">
            6-digit code
            <input
              type="text"
              name="totp"
              data-testid="login-totp"
              inputMode="numeric"
              autoComplete="one-time-code"
              value={totp}
              onChange={(e) => setTotp(cleanTotp(e.target.value))}
              placeholder="123456"
              autoFocus
              className="kc-mono-input"
            />
          </label>
        )}

        {signedOutReason && !error && (
          <p className="pf-banner warn" role="status" data-testid="signed-out-reason">
            {signedOutReason}
          </p>
        )}

        {error && (
          <p className="error auth-error" role="alert" data-testid="login-error">
            <Icon name="octagon-alert" size={16} />
            <span>{error}</span>
          </p>
        )}

        <button
          type="submit"
          className="auth-submit"
          disabled={loading || !storeId.trim() || !username || !password || (needTotp && totp.length !== 6)}
        >
          {loading ? "Signing in…" : "Sign in"}
        </button>
        <p className="login-links">
          New store? <Link to="/signup">Request access</Link>
        </p>
      </form>
    </AuthLayout>
  );
}
