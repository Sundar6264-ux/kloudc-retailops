// The store this page is signed in to (docs/PLATFORM_CONTRACT.md 4.3 step
// 4). On the shared service a browser holds ONE session cookie for every
// store: signing in to store B in another tab replaces it, and without a
// check this tab - still showing store A - would read and write store B.
// Every store request therefore names the store this page believes it is
// in (X-Store); the server answers 409 store_mismatch when the cookie is now
// another store's, and the page reloads to show the real one.
// A single-store server ignores the header.

let current = null;

// Requests that must work whatever store the cookie is for (signing in or
// out, asking who is signed in), or that are not store requests at all.
const NO_STORE_HEADER = [
  "/api/auth/login",
  "/api/auth/mobile-login",
  "/api/auth/support-login",
  "/api/auth/logout",
  "/api/auth/me",
  "/api/signup",
  "/api/public",
  "/api/platform",
  "/api/app/branding",
];

export function setSessionStore(code) {
  current = typeof code === "string" && code.trim() ? code.trim().toUpperCase() : null;
}

export function sessionStore() {
  return current;
}

function exempt(path) {
  const bare = String(path || "").split("?", 1)[0];
  return NO_STORE_HEADER.some((p) => bare === p || bare.startsWith(p + "/"));
}

export function storeHeaders(path) {
  if (!current || exempt(path)) return {};
  return { "X-Store": current };
}

// For the XMLHttpRequest uploads.
export function setStoreHeadersOn(xhr, path) {
  for (const [k, v] of Object.entries(storeHeaders(path))) xhr.setRequestHeader(k, v);
}

export function isStoreMismatch(status, body) {
  if (status !== 409 || !body || typeof body !== "object") return false;
  const code = body.error || (body.detail && typeof body.detail === "object" ? body.detail.error : null);
  return code === "store_mismatch";
}
