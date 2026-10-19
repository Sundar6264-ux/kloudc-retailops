// KloudC company facts used by the product's own pages (login footer, user
// menu, master console): docs/DESIGN_SYSTEM.md, brand/kloudc.txt. No Terms
// link until a store's/branding terms_url (TERMS_URL) is set.
export const KLOUDC = {
  product: "KloudC RetailOps",
  company: "KloudC LLC",
  location: "Connecticut, USA",
  tagline: "Make Your POS Inventory Work Like Your Real Store.",
  email: "support@kloudc.com",
};

export const KLOUDC_LINKS = {
  home: "https://kloudc.com/",
  product: "https://kloudc.com/retailops/",
  privacy: "https://kloudc.com/kloudc-privacy-policy/",
  support: "https://kloudc.com/kloudc-support/",
  contact: "https://kloudc.com/contact/",
};

// The three one-line value points on the logged-out pages.
export const VALUE_POINTS = [
  { icon: "wrench", text: "Find inventory problems before they cost margin" },
  { icon: "shopping-cart", text: "Order the right amount from every vendor" },
  { icon: "users", text: "Keep the team on the same page" },
];

// "AB" from a username ("avery.b" -> "AB", "dana" -> "DA").
export function initials(name) {
  const s = String(name || "").trim();
  if (!s) return "?";
  const parts = s.split(/[\s._-]+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return s.slice(0, 2).toUpperCase();
}

// mailto: for support - opens the user's mail app with a template; nothing
// is ever sent automatically, and it never asks for a password.
export function supportMailto({ storeCode, page, email = KLOUDC.email } = {}) {
  const subject = `RetailOps support - ${storeCode || "no Store ID"}`;
  const body = [
    `Store ID: ${storeCode || ""}`,
    `Page: ${page || ""}`,
    "",
    "What I did:",
    "",
    "What I expected:",
    "",
    "What happened instead:",
    "",
    "(Add a screenshot if you can. Never send your password.)",
  ].join("\n");
  return `mailto:${email}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}

export const COPYRIGHT = `© ${new Date().getFullYear() < 2026 ? 2026 : new Date().getFullYear()} ${KLOUDC.company} · ${KLOUDC.location}`;
