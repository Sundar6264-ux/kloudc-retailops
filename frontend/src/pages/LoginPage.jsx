import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "../AuthContext.jsx";
import PasswordInput from "../components/PasswordInput.jsx";

export default function LoginPage() {
  const { login } = useAuth();
  const [searchParams] = useSearchParams();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await login(username, password);
      // Set only when the backend redirected here from a link that needed
      // login first (e.g. a WhatsApp invoice link) - a real backend route
      // (/public/invoices/...), not an SPA route, so this is a full page
      // navigation rather than react-router's navigate(). Bounces the
      // person straight back to the actual link they tapped instead of
      // leaving them to find it themselves inside the dashboard.
      const next = searchParams.get("next");
      if (next) window.location.href = next;
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--bg)",
      }}
    >
      <form
        onSubmit={handleSubmit}
        className="card"
        style={{ width: "320px", display: "flex", flexDirection: "column", gap: "0.9rem" }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", marginBottom: "0.4rem" }}>
          <img src="/logo-header.png" alt="StoreOps" style={{ width: 36, height: 36, borderRadius: 8 }} />
          <strong style={{ fontSize: "1.1rem" }}>StoreOps</strong>
        </div>

        <label style={{ display: "flex", flexDirection: "column", gap: "0.3rem", fontSize: "0.85rem" }}>
          Username
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            autoFocus
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: "0.3rem", fontSize: "0.85rem" }}>
          Password
          <PasswordInput
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />
        </label>

        {error && <p className="error" style={{ margin: 0 }}>{error}</p>}

        <button type="submit" disabled={loading || !username || !password}>
          {loading ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
