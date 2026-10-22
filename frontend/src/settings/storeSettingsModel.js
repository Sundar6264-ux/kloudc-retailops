// Setup > Store settings (website ready A5): the store admin's editable,
// non-secret settings (GET/PATCH /api/settings/store, the server's
// STORE_ADMIN_SETTING_KEYS). Pure helpers, tested by storeSettingsModel.test.mjs.

export const STORE_SETTING_GROUPS = [
  {
    key: "store",
    title: "Your store",
    fields: [
      { key: "store_name", label: "Store name", kind: "text", max: 120, help: "On POs, emails and the app." },
      { key: "store_short_name", label: "Short name", kind: "text", max: 40, help: "Where space is tight, like the app's title bar." },
      { key: "store_address", label: "Address", kind: "textarea", max: 300, help: "Printed on purchase orders." },
      { key: "store_phone", label: "Phone", kind: "tel", max: 40 },
      { key: "store_email", label: "Email", kind: "email", max: 200, help: "Where vendors reply to POs." },
    ],
  },
  {
    key: "emails",
    title: "Emails",
    fields: [{ key: "email_from_name", label: "Sender name", kind: "text", max: 120, help: "The name vendors see on PO emails." }],
  },
  {
    key: "look",
    title: "Logo and colours",
    fields: [
      { key: "logo_url", label: "Logo link", kind: "url", help: "An https:// link to your logo image." },
      { key: "icon_url", label: "Icon link", kind: "url", help: "A square image for the browser tab and the app." },
      { key: "brand_color", label: "Main colour", kind: "color" },
      { key: "brand_secondary_color", label: "Second colour", kind: "color" },
    ],
  },
  {
    key: "other",
    title: "Other",
    fields: [
      { key: "ai_enabled", label: "AI features", kind: "bool", help: "Ask Inventory Bot and the weekly digest's sentences." },
    ],
  },
];

export const STORE_SETTING_FIELDS = STORE_SETTING_GROUPS.flatMap((g) => g.fields);

const HEX = /^#[0-9a-fA-F]{6}$/;
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const HTTPS = /^https:\/\/[^\s/]+\.[^\s]+$/;

export function fieldError(field, value) {
  if (field.kind === "bool") return null;
  const v = String(value ?? "").trim();
  if (field.max && v.length > field.max) return `At most ${field.max} characters.`;
  if (!v) return field.key === "store_name" ? "Your store needs a name." : null;
  if (field.kind === "email" && !EMAIL.test(v)) return "Enter an email address like orders@example.com.";
  if (field.kind === "url" && !HTTPS.test(v)) return "Enter a full https:// link.";
  if (field.kind === "color" && !HEX.test(v)) return "Pick a colour, or type it as # and six digits/letters.";
  return null;
}

// Only what changed; empty text is sent as null (cleared).
export function storeSettingsChanges(saved, draft) {
  const out = {};
  for (const f of STORE_SETTING_FIELDS) {
    if (!(f.key in (draft || {}))) continue;
    let v = draft[f.key];
    if (f.kind === "bool") v = !!v;
    else v = String(v ?? "").trim() || null;
    const was = f.kind === "bool" ? !!saved?.[f.key] : saved?.[f.key] ?? null;
    if (v !== was) out[f.key] = v;
  }
  return out;
}

export function draftErrors(draft) {
  const out = {};
  for (const f of STORE_SETTING_FIELDS) {
    if (!(f.key in (draft || {}))) continue;
    const e = fieldError(f, draft[f.key]);
    if (e) out[f.key] = e;
  }
  return out;
}

// What the page shows when the server says it has no shared-platform
// settings (a store on its own single-store server: 409 platform_mode_required).
export function unavailableReason(err) {
  const code = err?.body?.error || err?.body?.detail?.error;
  if (err?.status === 409 && code === "platform_mode_required") return "single";
  if (err?.status === 403) return "forbidden";
  return null;
}

// Own server: shown only when the platform offers it; a pending request is shown instead of the form.
export function hostingView(h) {
  if (!h || !h.enabled) return { show: false };
  const open = (h.requests || []).find((r) => ["requested", "approved", "migrating"].includes(r.status));
  return { show: true, dedicated: h.hosting === "dedicated", pending: open || null };
}

// Label printing (docs/LABEL_PRINTING_FEATURE.md 3.1): the card's state from
// GET /api/label-printing. "on" | "requested" | "declined" | "off".
export const LABEL_PRINTING_TEXT = {
  on: "Label printing is on.",
  off: "Print shelf labels from your phone: scan a barcode, check the price, print.",
  askAdmin: "Ask your store admin.",
  asked: "Asked. We'll turn it on and let you know.",
};

export function labelPrintingView(status) {
  if (!status) return { kind: "loading" };
  const req = status.request || null;
  if (status.state === "on") return { kind: "on", url: status.url || null };
  if (req?.status === "requested") return { kind: "requested", since: req.created_at || null, canCancel: !!status.can_request };
  if (req?.status === "declined") return { kind: "declined", note: req.review_note || "", canAsk: !!status.can_request };
  return { kind: "off", canAsk: !!status.can_request, noAddress: status.state === "on_no_address" };
}

// The label site address: https://<name> with nothing after it (the server
// also checks the host is under the platform domain).
const ORIGIN = /^https:\/\/[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$/i;

export function labelsAddressError(value) {
  const v = String(value ?? "").trim();
  if (!v) return "Enter the label site address.";
  if (!ORIGIN.test(v)) return "Use https://<name> with nothing after it.";
  return null;
}
