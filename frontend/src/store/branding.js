// Pure helpers for the store's branding (GET /api/app/branding,
// docs/M2_CONTRACT.md section 2): normalising the response, merging it with
// /api/auth/me, and turning the brand colours into CSS variables. No React,
// no DOM - tested by branding.test.mjs.

// Product defaults (settings PRODUCT_NAME / COMPANY_NAME / BRAND_COLOR /
// BRAND_SECONDARY_COLOR). Used until branding answers, and by an older
// server without the endpoint. Never a store's own values.
export const PRODUCT_DEFAULTS = {
  productName: "StoreOps",
  company: "KloudC LLC",
  primaryColor: "#0F766E",
  secondaryColor: "#334155",
  logoPath: "/branding/logo.png",
  iconPath: "/branding/icon.png",
};

const HEX = /^#[0-9A-Fa-f]{6}$/;

function str(v) {
  return typeof v === "string" && v.trim() ? v.trim() : null;
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
    logoUrl: str(b.logo_url) || PRODUCT_DEFAULTS.logoPath,
    iconUrl: str(b.icon_url) || PRODUCT_DEFAULTS.iconPath,
    primaryColor: HEX.test(b.primary_color || "") ? b.primary_color : PRODUCT_DEFAULTS.primaryColor,
    secondaryColor: HEX.test(b.secondary_color || "") ? b.secondary_color : PRODUCT_DEFAULTS.secondaryColor,
    timezone: str(b.timezone),
    currency: str(b.currency) || "USD",
    locale: str(b.locale) || "en-US",
    posName: str(b.pos_name),
    capabilities: strList(b.capabilities),
    storeFeatures: strList(b.store_features),
    labelsUrl: str(b.labels_url),
    legal: {
      privacyUrl: str(legal.privacy_url),
      termsUrl: str(legal.terms_url),
      supportUrl: str(legal.support_url),
    },
    demo: b.demo === true,
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
    posName: str(me?.pos_name) || b.posName,
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

// CSS custom properties for the brand: --accent (buttons, active nav
// marker), its hover / soft tints, an "r, g, b" triple for translucent
// shadows, and the secondary colour.
export function brandCssVars(primary, secondary) {
  const p = HEX.test(primary || "") ? primary : PRODUCT_DEFAULTS.primaryColor;
  const s = HEX.test(secondary || "") ? secondary : PRODUCT_DEFAULTS.secondaryColor;
  const accent = readableOnWhiteText(p.toUpperCase());
  return {
    "--brand": p.toUpperCase(),
    "--brand-secondary": s.toUpperCase(),
    "--accent": accent,
    "--accent-hover": mix(accent, "#000000", 0.12),
    "--accent-soft": mix(p, "#FFFFFF", 0.9),
    "--accent-rgb": hexToRgb(accent).join(", "),
  };
}
