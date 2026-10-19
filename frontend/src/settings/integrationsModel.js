// Settings -> Integrations (docs/PLATFORM_CONTRACT.md 8.3, 9, 13.5): the
// five connectors a store admin sets up after signing in. Secrets are
// write-only: the server never returns them, the page only shows
// "saved •••• 1234", and a blank secret box means "keep the saved one".
// Pure helpers - tested by integrationsModel.test.mjs.

// field: { key, label, type: text|password|number|checkbox|json|select,
//          secret?, placeholder?, options?, hint? }
export const INTEGRATIONS = [
  {
    kind: "toast",
    label: "Toast",
    blurb: "Your Toast POS: sales, menu, checks and inventory.",
    testable: true,
    fields: [
      { key: "client_id", label: "Client ID", type: "text" },
      { key: "client_secret", label: "Client secret", type: "password", secret: true },
      { key: "restaurant_guid", label: "Restaurant GUID", type: "text" },
      { key: "api_base", label: "API address", type: "text", placeholder: "https://ws-api.toasttab.com" },
      { key: "analytics_client_id", label: "Analytics client ID (optional)", type: "text" },
      { key: "analytics_client_secret", label: "Analytics client secret (optional)", type: "password", secret: true },
    ],
  },
  {
    kind: "clover",
    label: "Clover",
    blurb: "Connect your Clover merchant account.",
    testable: false,
    connect: true,
    fields: [],
  },
  {
    kind: "whatsapp",
    label: "WhatsApp",
    blurb: "Send purchase orders and flyers to WhatsApp groups (Whapi).",
    testable: true,
    fields: [
      { key: "api_token", label: "API token", type: "password", secret: true },
      { key: "base_url", label: "API address", type: "text", placeholder: "https://gate.whapi.cloud" },
      {
        key: "group_chat_ids",
        label: "Group chat IDs (JSON)",
        type: "json",
        placeholder: '{"orders": "1203...@g.us"}',
        hint: "Which WhatsApp group gets what, as JSON: a name and the group's chat ID.",
      },
    ],
  },
  {
    kind: "smtp",
    label: "Email (SMTP)",
    blurb: "The mailbox purchase orders and reports are sent from.",
    testable: true,
    fields: [
      { key: "host", label: "Server", type: "text", placeholder: "smtp.gmail.com" },
      { key: "port", label: "Port", type: "number", placeholder: "465" },
      { key: "ssl", label: "Use SSL", type: "checkbox" },
      { key: "username", label: "Username", type: "text" },
      { key: "password", label: "Password / app password", type: "password", secret: true },
      { key: "from_name", label: "From name", type: "text" },
    ],
  },
  {
    kind: "ai",
    label: "AI",
    blurb: "The AI key for Ask the bot, invoice reading and suggestions.",
    testable: true,
    fields: [{ key: "api_key", label: "API key", type: "password", secret: true }],
  },
];

export const INTEGRATIONS_BY_KIND = Object.fromEntries(INTEGRATIONS.map((i) => [i.kind, i]));

export const OPEN_CARD_KEY = "retailops_integrations_open";

const STATUS = {
  ok: { label: "Connected", tone: "good" },
  failed: { label: "Test failed", tone: "bad" },
  untested: { label: "Saved - not tested", tone: "neutral" },
  none: { label: "Not set up", tone: "off" },
};

// One GET row (or nothing) -> what the card shows.
export function integrationView(spec, row) {
  const r = row && typeof row === "object" ? row : {};
  const configured = !!r.configured;
  const statusKey = configured ? (STATUS[r.status] ? r.status : "untested") : "none";
  const last4 = typeof r.last4 === "string" && r.last4 ? r.last4 : null;
  return {
    kind: spec.kind,
    label: r.label || spec.label,
    configured,
    status: statusKey,
    statusLabel: STATUS[statusKey].label,
    tone: STATUS[statusKey].tone,
    savedText: configured && last4 ? `saved •••• ${last4}` : configured ? "saved" : "",
    public: r.public && typeof r.public === "object" ? r.public : {},
    updatedAt: r.updated_at || null,
    updatedBy: r.updated_by || null,
    lastTestedAt: r.last_tested_at || null,
    lastTestMessage: r.last_test_message || null,
  };
}

const POS_KINDS = { toast: "toast", clover: "clover" };

// The integrations this store can use: only its own POS's connector (a
// Clover store never sees Toast, and the other way round; a CSV-upload store
// has none). An unknown POS (older server) shows both. One already saved
// always shows, so it can still be removed.
export function kindsForStore(posName, rows = []) {
  const pos = String(posName || "").trim().toLowerCase();
  const saved = new Set((Array.isArray(rows) ? rows : []).filter((r) => r && r.configured).map((r) => r.kind));
  return INTEGRATIONS.filter((spec) => {
    if (!(spec.kind in POS_KINDS)) return true;
    if (saved.has(spec.kind)) return true;
    if (!pos) return true;
    return pos === spec.kind;
  }).map((spec) => spec.kind);
}

// GET /api/settings/integrations -> a view per kind this store can use, in
// page order (a kind the server leaves out shows as "Not set up").
export function integrationViews(rows, posName = null) {
  const list = Array.isArray(rows) ? rows : [];
  const byKind = Object.fromEntries(list.filter((r) => r && r.kind).map((r) => [r.kind, r]));
  const kinds = new Set(kindsForStore(posName, list));
  return INTEGRATIONS.filter((spec) => kinds.has(spec.kind)).map((spec) => integrationView(spec, byKind[spec.kind]));
}

// The form's starting values: the saved public fields; secret boxes empty.
export function formFromView(spec, view) {
  const form = {};
  for (const f of spec.fields) {
    if (f.secret) form[f.key] = "";
    else if (f.type === "checkbox") form[f.key] = !!view.public[f.key];
    else if (f.type === "json") {
      const v = view.public[f.key];
      form[f.key] = v == null || v === "" ? "" : typeof v === "string" ? v : JSON.stringify(v, null, 2);
    } else form[f.key] = view.public[f.key] == null ? "" : String(view.public[f.key]);
  }
  return form;
}

// Form -> the PUT / test body {"public", "secrets", "clear"} (contract
// 13.5). Blank secret = keep the saved one (left out); `clear` lists the
// secrets the admin chose to remove. Throws Error(field: message) for a
// field that can't be sent (bad JSON, bad port).
export function buildPayload(spec, form, { clear = [] } = {}) {
  const pub = {};
  const secrets = {};
  for (const f of spec.fields) {
    const raw = form?.[f.key];
    if (f.secret) {
      const v = typeof raw === "string" ? raw.trim() : "";
      if (v) secrets[f.key] = v;
      continue;
    }
    if (f.type === "checkbox") {
      pub[f.key] = !!raw;
    } else if (f.type === "number") {
      const s = String(raw ?? "").trim();
      if (s === "") continue;
      const n = Number(s);
      if (!Number.isInteger(n) || n < 1 || n > 65535) throw fieldError(f, "Enter a whole number from 1 to 65535.");
      pub[f.key] = n;
    } else if (f.type === "json") {
      const s = String(raw ?? "").trim();
      if (s === "") continue;
      let parsed;
      try {
        parsed = JSON.parse(s);
      } catch {
        throw fieldError(f, "This isn't valid JSON.");
      }
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw fieldError(f, 'Use a JSON object, like {"orders": "123@g.us"}.');
      }
      pub[f.key] = parsed;
    } else {
      const s = String(raw ?? "").trim();
      if (s !== "") pub[f.key] = s;
    }
  }
  const secretKeys = new Set(spec.fields.filter((f) => f.secret).map((f) => f.key));
  const clearList = [...new Set(clear)].filter((k) => secretKeys.has(k) && !(k in secrets));
  return { public: pub, secrets, clear: clearList };
}

function fieldError(field, message) {
  const e = new Error(`${field.label}: ${message}`);
  e.field = field.key;
  return e;
}

// Nothing to save yet: no public value differs and no secret was typed.
export function hasChanges(spec, view, form, clear = []) {
  if (clear.length) return true;
  const start = formFromView(spec, view);
  return spec.fields.some((f) => (f.secret ? !!String(form?.[f.key] ?? "").trim() : form?.[f.key] !== start[f.key]));
}

// Any text that would show a secret value: a safety check the page runs on
// every server reply before showing it (the server never sends one).
export function containsSecret(value, secrets) {
  const text = typeof value === "string" ? value : JSON.stringify(value ?? "");
  return Object.values(secrets || {}).some((s) => typeof s === "string" && s.length >= 4 && text.includes(s));
}

// Server errors on this page -> plain English.
export function integrationErrorMessage(err) {
  const status = err?.status;
  const body = err?.body && typeof err.body === "object" ? err.body : {};
  const detail = body.detail && typeof body.detail === "object" ? body.detail : {};
  const code = body.error || detail.error;
  const message = body.message || detail.message || (typeof body.detail === "string" ? body.detail : null);
  if (!status) return "Can't reach the server. Check your connection and try again.";
  if (code === "platform_mode_required") {
    return "On this server, connections are set in the server's settings file by whoever runs it.";
  }
  if (code === "not_available") return message || "Clover connection arrives with M3.";
  if (status === 429) return "Too many tests in a row. Wait a minute and try again.";
  if (status === 403) return message || "Only a store admin can change integrations.";
  return message || "Something went wrong. Please try again.";
}
