import { createContext, useContext, useState, useEffect, useCallback, useMemo } from "react";
import { getMe, login as apiLogin, logout as apiLogout, restoreToken } from "./api";
import { UserStoreContext, useBranding } from "./store/StoreContext.jsx";
import { storeView } from "./store/branding.js";
import { featureAvailable, setCurrentPosName } from "./store/gating.js";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const branding = useBranding();
  // The store as this user sees it (branding + /me's pos_name, capabilities
  // and store_features) - useStore() everywhere below this provider.
  const store = useMemo(() => {
    const view = storeView(branding, user);
    // Text built outside React (store/gating.js currentPosName) follows /me too.
    setCurrentPosName(view.posName);
    return view;
  }, [branding, user]);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      // No-op on web. On the mobile app, loads a previously-stored bearer
      // token into memory before /me is called - otherwise a native app
      // relaunch would look logged-out for a moment (no cookie exists there
      // to fall back on) even though a valid stored session exists.
      await restoreToken();
      const me = await getMe();
      setUser(me);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    const onUnauthorized = () => setUser(null);
    window.addEventListener("auth:unauthorized", onUnauthorized);
    return () => window.removeEventListener("auth:unauthorized", onUnauthorized);
  }, []);

  async function login(username, password) {
    const me = await apiLogin(username, password);
    setUser(me);
    return me;
  }

  async function logout() {
    await apiLogout();
    setUser(null);
  }

  // Admin always has full access; a non-admin only sees what's in their own
  // allowed_features (set by an admin on the Users page). Same rule the
  // backend enforces per-endpoint (app/features.py) - this is purely for
  // hiding nav/routes the API would 403 on anyway, not a security boundary
  // of its own. On top, for everyone including admins: a feature this
  // store's POS or settings can't support is off (store/gating.js
  // FEATURE_REQUIREMENTS; the server answers 409 for those).
  function hasFeature(key) {
    if (!user) return false;
    if (!featureAvailable(key, store)) return false;
    if (user.is_admin) return true;
    // Items Sold is nothing but sales numbers - off for a "Hide sales
    // numbers" user even if granted (the server's /me already drops it).
    if (key === "items_sold" && user.hide_sales) return false;
    return (user.allowed_features || []).includes(key);
  }

  // False when an admin ticked "Hide sales numbers" for this user (never for
  // an admin). Hides sales tiles, totals and digest sales - the server strips
  // or 403s the same data (backend/app/sales_access.py).
  const canSeeSales = !!user && (user.is_admin || !user.hide_sales);

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, refresh, hasFeature, canSeeSales, store }}>
      <UserStoreContext.Provider value={store}>{children}</UserStoreContext.Provider>
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
