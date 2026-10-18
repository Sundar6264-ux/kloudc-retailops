// The store this website belongs to (one server = one store, plan 3.1):
// name, logo, colours and time zone from GET /api/app/branding, fetched
// once at boot BEFORE login (the login page shows the store's name and
// logo too). Applies them to the page: document title, favicon, CSS colour
// variables and the store time zone used by format.js.
//
// useStore() gives the store as the signed-in user sees it: AuthProvider
// merges /api/auth/me's pos_name / capabilities / store_features in
// (branding.js storeView) and provides it through UserStoreContext.
import { createContext, useContext, useEffect, useState } from "react";
import { getBranding } from "../api";
import { setStoreTimeZone } from "../format.js";
import { brandCssVars, documentTitle, normalizeBranding, storeView } from "./branding.js";
import { elementAvailable, posText, setCurrentPosName } from "./gating.js";

const BrandingContext = createContext(null);
export const UserStoreContext = createContext(null);

// Never wait on branding forever: an unreachable server still gets the
// login page (with product defaults) after this.
const BRANDING_TIMEOUT_MS = 5000;

function setFavicon(href) {
  if (typeof document === "undefined" || !href) return;
  for (const link of document.querySelectorAll('link[rel="icon"], link[rel="apple-touch-icon"]')) {
    link.setAttribute("href", href);
    // index.html's first icon is declared SVG; the store's icon is an image
    // of its own type, and a browser may skip one whose declared type is wrong.
    link.removeAttribute("type");
  }
}

export function applyBranding(store) {
  setStoreTimeZone(store.timezone);
  setCurrentPosName(store.posName);
  if (typeof document === "undefined") return;
  document.title = documentTitle(store);
  const root = document.documentElement;
  for (const [k, v] of Object.entries(brandCssVars(store.primaryColor, store.secondaryColor))) {
    root.style.setProperty(k, v);
  }
  const theme = document.querySelector('meta[name="theme-color"]');
  if (theme) theme.setAttribute("content", store.primaryColor);
  if (store.loaded) setFavicon(store.iconUrl);
}

export function StoreProvider({ children }) {
  const [branding, setBranding] = useState(null);

  useEffect(() => {
    let done = false;
    const finish = (raw) => {
      if (done) return;
      done = true;
      const b = normalizeBranding(raw);
      applyBranding(b);
      setBranding(b);
    };
    const timer = setTimeout(() => finish(null), BRANDING_TIMEOUT_MS);
    getBranding()
      .then(finish)
      .catch(() => finish(null))
      .finally(() => clearTimeout(timer));
    return () => clearTimeout(timer);
  }, []);

  if (!branding) {
    return (
      <div className="store-boot" aria-busy="true">
        <p className="muted">Loading…</p>
      </div>
    );
  }
  return <BrandingContext.Provider value={branding}>{children}</BrandingContext.Provider>;
}

// Branding only (no /me): the login page, the public checklist page.
export function useBranding() {
  return useContext(BrandingContext) || normalizeBranding(null);
}

// The store as this user sees it (branding + /me). Outside AuthProvider
// it is branding alone.
export function useStore() {
  const merged = useContext(UserStoreContext);
  const branding = useBranding();
  return merged || storeView(branding, null);
}

// Text with "{pos}" -> the store's POS name.
export function usePosText() {
  const { posName } = useStore();
  return (text) => posText(text, posName);
}

// The POS name for running text ("Toast", "Clover"); "the POS" if unknown.
export function usePosName() {
  return useStore().posName || "the POS";
}

// Is this page control on for this store? (gating.js ELEMENT_REQUIREMENTS)
export function useStoreAllows(element) {
  return elementAvailable(element, useStore());
}
