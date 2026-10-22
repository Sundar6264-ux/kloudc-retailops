// Pure helpers for the store's branding (GET /api/app/branding,
// docs/M2_CONTRACT.md section 2): normalising the response, merging it with
// /api/auth/me, and turning the brand colours into CSS variables. No React,
// no DOM - tested by branding.test.mjs.

// Product defaults (settings PRODUCT_NAME / COMPANY_NAME / BRAND_COLOR /
// BRAND_SECONDARY_COLOR). Used until branding answers, and by an older
// server without the endpoint. Never a store's own values.
export const PRODUCT_DEFAULTS = {
  productName: "KloudC RetailOps",
  company: "KloudC LLC",
  primaryColor: "#004BAD",
  secondaryColor: "#2EA3F2",
  logoPath: "/branding/logo.png",
  iconPath: "/branding/icon.png",
};

const HEX = /^#[0-9A-Fa-f]{6}$/;

function str(v) {
  return typeof v === "string" && v.trim() ? v.trim() : null;
}

// A link or image address from the store's settings: http(s) or a path on
// this site only. A store admin sets these and the website is one origin for
// every store, so a javascript: / data: address must never reach an href.
export function safeUrl(v) {
  const s = str(v);
  if (!s) return null;
  if (/^https?:\/\/[^\s/]/i.test(s)) return s;
  if (s.startsWith("/") && !s.startsWith("//") && !s.startsWith("/\\")) return s;
  return null;
}

function strList(v) {
  return Array.isArray(v) ? v.filter((x) => typeof x === "string") : null;
}

// The branding body -> the website's store object. `raw` may be null (no
// endpoint / network error): every field falls back to a product default,
// and capabilities / storeFeatures stay null ("not told", gating.js).
export function normalizeBranding(raw) {
  const b = raw && typeof raw === "object" ? raw : {};
  const product = b.product && typeof b.product === "object" ? b.product : {};
  const legal = b.legal && typeof b.legal === "object" ? b.legal : {};
  const productName = str(product.name) || PRODUCT_DEFAULTS.productName;
  const name = str(b.name) || productName;
  return {
    loaded: !!raw,
    code: str(b.store_code),
    name,
    shortName: str(b.short_name) || name,
    productName,
    company: str(product.company) || str(legal.company) || PRODUCT_DEFAULTS.company,
    logoUrl: safeUrl(b.logo_url) || PRODUCT_DEFAULTS.logoPath,
    iconUrl: safeUrl(b.icon_url) || PRODUCT_DEFAULTS.iconPath,
    primaryColor: HEX.test(b.primary_color || "") ? b.primary_color : PRODUCT_DEFAULTS.primaryColor,
    secondaryColor: HEX.test(b.secondary_color || "") ? b.secondary_color : PRODUCT_DEFAULTS.secondaryColor,
    timezone: str(b.timezone),
    currency: str(b.currency) || "USD",
    locale: str(b.locale) || "en-US",
    posName: str(b.pos_name),
    capabilities: strList(b.capabilities),
    storeFeatures: strList(b.store_features),
    labelsUrl: safeUrl(b.labels_url),
    legal: {
      privacyUrl: safeUrl(legal.privacy_url),
      termsUrl: safeUrl(legal.terms_url),
      supportUrl: safeUrl(legal.support_url),
    },
    demo: b.demo === true,
    // false = the product's neutral look a shared server gives before
    // sign-in (Store ID privacy, 2026-10); a single-store server's body has
    // no such key and is always the store's own.
    storeSpecific: b.store_specific !== false,
  };
}

// The store as the signed-in user sees it: /api/auth/me's pos_name,
// capabilities and store_features (M1 / M2 additive keys) win over the
// branding ones when present; before login it's branding alone.
export function storeView(branding, me) {
  const b = branding || normalizeBranding(null);
  return {
    ...b,
    code: (me && typeof me.store_code === "string" && me.store_code) || b.code,
    // A CSV-only store has no POS to name: "your POS", never "CSV upload" (A7).
    posName: str(me?.pos_provider) === "csv" ? "your POS" : str(me?.pos_name) || b.posName,
    posProvider: str(me?.pos_provider) || null,
    timezone: str(me?.store_timezone) || b.timezone,
    capabilities: strList(me?.capabilities) || b.capabilities,
    storeFeatures: strList(me?.store_features) || b.storeFeatures,
  };
}

// <title>: the store's name (the product's when no store is configured).
export function documentTitle(store) {
  if (!store || !store.name) return PRODUCT_DEFAULTS.productName;
  return store.name === store.productName ? store.productName : `${store.name} - ${store.productName}`;
}

// ---- colours ----

export function hexToRgb(hex) {
  const m = HEX.exec(hex || "") ? hex.slice(1) : null;
  if (!m) return null;
  return [0, 2, 4].map((i) => parseInt(m.slice(i, i + 2), 16));
}

function rgbToHex([r, g, b]) {
  return `#${[r, g, b].map((v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0")).join("")}`.toUpperCase();
}

function luminance([r, g, b]) {
  const lin = (v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

export function contrastRatio(hexA, hexB) {
  const a = luminance(hexToRgb(hexA));
  const b = luminance(hexToRgb(hexB));
  const [hi, lo] = a > b ? [a, b] : [b, a];
  return (hi + 0.05) / (lo + 0.05);
}

// Mix `hex` toward `toward` (another hex) by `amount` (0..1).
export function mix(hex, toward, amount) {
  const a = hexToRgb(hex);
  const b = hexToRgb(toward);
  return rgbToHex(a.map((v, i) => v + (b[i] - v) * amount));
}

// The brand colour, darkened just enough for white button text to keep
// 4.5:1 (WCAG AA). A dark brand colour comes back unchanged.
export function readableOnWhiteText(hex) {
  let c = hex;
  for (let i = 0; i < 20 && contrastRatio(c, "#FFFFFF") < 4.5; i++) c = mix(c, "#000000", 0.08);
  return c;
}

function rgbToHsl([r, g, b]) {
  const [rr, gg, bb] = [r / 255, g / 255, b / 255];
  const max = Math.max(rr, gg, bb);
  const min = Math.min(rr, gg, bb);
  const l = (max + min) / 2;
  if (max === min) return [0, 0, l];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h;
  if (max === rr) h = (gg - bb) / d + (gg < bb ? 6 : 0);
  else if (max === gg) h = (bb - rr) / d + 2;
  else h = (rr - gg) / d + 4;
  return [h / 6, s, l];
}

function hslToRgb([h, s, l]) {
  if (s === 0) return [l * 255, l * 255, l * 255];
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  const f = (t) => {
    let x = t;
    if (x < 0) x += 1;
    if (x > 1) x -= 1;
    if (x < 1 / 6) return p + (q - p) * 6 * x;
    if (x < 1 / 2) return q;
    if (x < 2 / 3) return p + (q - p) * (2 / 3 - x) * 6;
    return p;
  };
  return [f(h + 1 / 3) * 255, f(h) * 255, f(h - 1 / 3) * 255];
}

// `hex` with its HSL lightness moved by `delta` (keeps the hue, so a deep
// blue lightens to a clear sky blue rather than a washed-out grey-blue).
export function shiftLightness(hex, delta) {
  const [h, s, l] = rgbToHsl(hexToRgb(hex));
  return rgbToHex(hslToRgb([h, s, Math.max(0, Math.min(1, l + delta))]));
}

// The colour lightened (or, toward "dark", darkened) until it reaches
// `ratio` against `bg`: the brand as text on a theme's surface.
export function readableOn(hex, bg, ratio = 4.5, direction = "light") {
  let c = hex.toUpperCase();
  const step = direction === "light" ? 0.03 : -0.03;
  for (let i = 0; i < 40 && contrastRatio(c, bg) < ratio; i++) c = shiftLightness(c, step);
  return c;
}

// Dark-theme surface (styles/tokens.css --surface, dark value).
export const DARK_SURFACE = "#162130";
export const LIGHT_SURFACE = "#FFFFFF";

// The product's own dark-theme values (docs/DESIGN_SYSTEM.md 1.1).
const PRODUCT_DARK = { fill: "#1F6FD1", text: "#6CB6F7" };

// The fill for primary buttons on the dark theme: the brand lightened just
// enough to stand off the dark page (3:1, a UI part), never so far that its
// white label drops under 4.5:1.
export function darkFill(accent) {
  let c = accent.toUpperCase();
  for (let i = 0; i < 30 && contrastRatio(c, DARK_SURFACE) < 3; i++) {
    const next = shiftLightness(c, 0.02);
    if (contrastRatio(next, "#FFFFFF") < 4.5) break;
    c = next;
  }
  return c;
}

// CSS custom properties for the brand: --accent (light-theme fill: buttons,
// the active nav marker) and --accent-dark (dark-theme fill), its hover, an
// "r, g, b" triple for translucent shadows, the secondary colour, and the
// brand as TEXT for each theme (--brand-text-light / -dark, 4.5:1 on that
// theme's surface). styles/tokens.css picks the pair for the current theme
// (--primary, --brand-text, --brand-soft).
export function brandCssVars(primary, secondary) {
  const p = HEX.test(primary || "") ? primary : PRODUCT_DEFAULTS.primaryColor;
  const s = HEX.test(secondary || "") ? secondary : PRODUCT_DEFAULTS.secondaryColor;
  const accent = readableOnWhiteText(p.toUpperCase());
  const isProduct = p.toUpperCase() === PRODUCT_DEFAULTS.primaryColor;
  // 6:1 on the dark surface (not just 4.5) so brand text still passes on
  // the tinted brand-soft fills it sits on (avatar, chips, active tabs).
  const textDark = isProduct ? PRODUCT_DARK.text : readableOn(p, DARK_SURFACE, 6);
  return {
    "--brand": p.toUpperCase(),
    "--brand-secondary": s.toUpperCase(),
    "--accent": accent,
    "--accent-dark": isProduct ? PRODUCT_DARK.fill : darkFill(accent),
    "--accent-hover": mix(accent, "#000000", 0.12),
    "--accent-rgb": hexToRgb(accent).join(", "),
    "--brand-text-light": readableOn(accent, LIGHT_SURFACE, 4.5, "dark"),
    "--brand-text-dark": textDark,
    "--brand-text-dark-rgb": hexToRgb(textDark).join(", "),
  };
}

// Whose page a public checklist link is: the "store" a working link's form
// carries ({name, short_name, logo_url}), in the shape StoreLogo reads, or
// null (an older server, or nothing usable). The public branding no longer
// names a store before sign-in on the shared service (Store ID privacy).
export function linkStore(form) {
  const s = form && typeof form.store === "object" ? form.store : null;
  const name = s && typeof s.name === "string" ? s.name.trim() : "";
  if (!name) return null;
  const shortName = typeof s.short_name === "string" && s.short_name.trim() ? s.short_name.trim() : name;
  const logoUrl = typeof s.logo_url === "string" && s.logo_url.trim() ? s.logo_url.trim() : null;
  return { name, shortName, logoUrl };
}
