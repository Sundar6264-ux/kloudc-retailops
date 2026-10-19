import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../AuthContext.jsx";
import { lookupStore } from "../api";
import PasswordInput from "../components/PasswordInput.jsx";
import StoreLogo from "../components/StoreLogo.jsx";
import { useBranding } from "../store/StoreContext.jsx";
import { safeUrl } from "../store/branding.js";
import {
  cleanTotp,
  initialStoreId,
  isPlatformCode,
  loginErrorMessage,
  movedLoginUrl,
  normalizeStoreCode,
  readLastStore,
  rememberStore,
  validStoreCode,
} from "../login/loginModel.js";
import "../platform/platform.css";

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
export default function LoginPage() {
  const { login } = useAuth();
  // Product branding before login (a single-store server: its store's).
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
  const [loading, setLoading] = useState(false);
  // The store the typed Store ID belongs to (public lookup on blur), for
  // its name and logo above the form. null = not looked up / not found.
  const [found, setFound] = useState(null);
  const lookupSeq = useRef(0);

  const code = validStoreCode(storeId);

  async function lookUp(raw) {
    const c = validStoreCode(raw);
    const mine = ++lookupSeq.current;
    if (!c || isPlatformCode(c) || c === brand.code) {
      setFound(null);
      return;
    }
    try {
      const rec = await lookupStore(c);
      if (mine !== lookupSeq.current) return;
      setFound(rec && rec.code ? rec : null);
    } catch {
      if (mine === lookupSeq.current) setFound(null);
    }
  }

  useEffect(() => {
    // Once, for a prefilled Store ID.
    if (code) lookUp(code);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

  const platform = isPlatformCode(storeId);
  const ownStore = !!code && code === brand.code;
  const header = found
    ? { name: found.name, shortName: found.name, logoUrl: safeUrl(found.branding?.logo_url) }
    : ownStore
      ? brand
      : { name: brand.productName, shortName: brand.productName, logoUrl: brand.code ? null : brand.logoUrl };
  const title = platform ? `${brand.productName} platform` : header.name || brand.productName;

  return (
    <div className="login-page">
      <form onSubmit={handleSubmit} className="card login-card" noValidate>
        <div className="login-head">
          <StoreLogo store={header} size={36} className="login-logo" />
          <strong className="login-title" data-testid="login-store-name">
            {title}
          </strong>
        </div>

        <label className="login-field">
          Store ID
          <input
            type="text"
            name="store_id"
            data-testid="login-store-id"
            value={storeId}
            onChange={(e) => {
              setStoreId(e.target.value.replace(/[^A-Za-z0-9_\- ]/g, "").slice(0, 32));
              setFound(null);
              setNeedTotp(false);
            }}
            onBlur={(e) => {
              const n = normalizeStoreCode(e.target.value);
              if (n !== storeId) setStoreId(n);
              lookUp(n);
            }}
            autoComplete="organization"
            autoCapitalize="characters"
            spellCheck={false}
            placeholder="e.g. MAIN_STREET"
            autoFocus={!storeId}
          />
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
            />
          </label>
        )}

        {error && (
          <p className="error" role="alert" data-testid="login-error" style={{ margin: 0 }}>
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={loading || !storeId.trim() || !username || !password || (needTotp && totp.length !== 6)}
        >
          {loading ? "Signing in…" : "Sign in"}
        </button>
        <p className="login-links">
          New store? <Link to="/signup">Request access</Link>
        </p>
        <p className="muted login-foot">
          {brand.productName} by {brand.company}
        </p>
      </form>
    </div>
  );
}
