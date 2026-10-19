// Appearance: System (default), Light or Dark (docs/DESIGN_SYSTEM.md 1.8).
// Remembered in localStorage "kc.theme"; public/theme-init.js applies it
// before first paint, applyTheme() after a change. Pure helpers + a tiny
// subscribe so the user menu and anything else stay in step.
export const THEME_KEY = "kc.theme";
export const THEMES = [
  { value: "system", label: "System" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

export function normalizeTheme(v) {
  return v === "light" || v === "dark" ? v : "system";
}

export function readTheme(storage) {
  try {
    return normalizeTheme(storage?.getItem(THEME_KEY));
  } catch {
    return "system";
  }
}

export function writeTheme(storage, theme) {
  const t = normalizeTheme(theme);
  try {
    if (t === "system") storage?.removeItem(THEME_KEY);
    else storage?.setItem(THEME_KEY, t);
  } catch {
    // storage off: the choice lasts for this page only
  }
  return t;
}

// The data-theme attribute for a choice: none for "system".
export function themeAttribute(theme) {
  const t = normalizeTheme(theme);
  return t === "system" ? null : t;
}

// What the page actually shows: the choice, or the OS setting for "system".
export function effectiveTheme(theme, prefersDark) {
  const t = normalizeTheme(theme);
  if (t === "system") return prefersDark ? "dark" : "light";
  return t;
}

const listeners = new Set();

export function applyTheme(theme, doc = typeof document !== "undefined" ? document : null) {
  const attr = themeAttribute(theme);
  if (doc) {
    if (attr) doc.documentElement.setAttribute("data-theme", attr);
    else doc.documentElement.removeAttribute("data-theme");
  }
  for (const fn of listeners) fn(normalizeTheme(theme));
}

export function onThemeChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
