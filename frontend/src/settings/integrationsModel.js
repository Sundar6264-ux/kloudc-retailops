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
    // Toast gives a store two key pairs; the form shows them as two groups.
    groups: [
      {
        key: "main",
        title: "Main Toast keys (required)",
        hint:
          "In Toast Web, go to Integrations › Toast API access. These keys bring in your sales, menu and stock.",
      },
      {
        key: "analytics",
        title: "Toast Analytics keys (recommended)",
        hint:
          "A separate key pair in Toast Web (Integrations › Toast API access) with the Analytics scope. They double-check each day's sales totals after closing.",
      },
    ],
    fields: [
      { key: "client_id", label: "Client ID", type: "text", group: "main" },
      { key: "client_secret", label: "Client secret", type: "password", secret: true, group: "main" },
      { key: "restaurant_guid", label: "Restaurant ID (GUID)", type: "text", group: "main" },
      {
        key: "api_base",
        label: "API address",
        type: "text",
        placeholder: "https://ws-api.toasttab.com",
        group: "main",
        advanced: true,
        hint: "Leave as is unless Toast gave you a different address.",
      },
      { key: "analytics_client_id", label: "Analytics client ID", type: "text", group: "analytics" },
      {
        key: "analytics_client_secret",
        label: "Analytics client secret",
        type: "password",
        secret: true,
        group: "analytics",
      },
    ],
  },
  {
    kind: "clover",
    label: "Clover",
    blurb: "Your Clover POS: sales, items, stock and employees.",
    testable: true,
    // Its own card (CloverCard): Connect with Clover, or a merchant API token.
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

// --- Toast's two key pairs ----------------------------------------------------

export const TOAST_MAIN_KEYS_MISSING =
  "Fill in the main Toast keys first (Client ID, Client secret and Restaurant ID). The Analytics keys are an extra.";
export const TOAST_SAME_KEYS =
  "These look like the same keys as the main ones. Toast gives Analytics its own Client ID and secret.";

// A spec's fields in its groups: [{group, fields, advanced}] (one unnamed
// group when the spec has none). `advanced` fields go under "Advanced".
export function fieldGroups(spec) {
  if (!Array.isArray(spec.groups) || !spec.groups.length) {
    return [{ group: null, fields: spec.fields.filter((f) => !f.advanced), advanced: spec.fields.filter((f) => f.advanced) }];
  }
  return spec.groups.map((g) => {
    const mine = spec.fields.filter((f) => f.group === g.key);
    return { group: g, fields: mine.filter((f) => !f.advanced), advanced: mine.filter((f) => f.advanced) };
  });
}

// The group key a field belongs to (null for a spec without groups), so a
// message about that field can sit inside the right group.
export function groupOfField(spec, key) {
  if (!Array.isArray(spec?.groups) || !spec.groups.length) return null;
  return spec.fields.find((f) => f.key === key)?.group || null;
}

const trimmed = (v) => (typeof v === "string" ? v.trim() : v == null ? "" : String(v).trim());

// Checks before Save / Test connection, in plain words:
// {error, fields, warning}. `error` stops the save (the main Toast keys
// missing, or a new Analytics pair with only one half); `warning` is shown
// but doesn't stop it (Analytics keys that repeat the main ones). Secrets
// are compared only with each other, never shown. A blank secret box means
// "keep the saved one", so a saved Client secret counts as filled in.
export function formProblems(spec, view, form, clear = []) {
  const none = { error: null, fields: [], warning: null };
  if (!spec || spec.kind !== "toast") return none;
  const f = form || {};
  const configured = !!view?.configured;
  const missing = [];
  if (!trimmed(f.client_id)) missing.push("client_id");
  if (!trimmed(f.client_secret) && (!configured || clear.includes("client_secret"))) missing.push("client_secret");
  if (!trimmed(f.restaurant_guid)) missing.push("restaurant_guid");

  const aId = trimmed(f.analytics_client_id);
  const aSecret = trimmed(f.analytics_client_secret);
  const sameId = !!aId && aId.toLowerCase() === trimmed(f.client_id).toLowerCase();
  const sameSecret = !!aSecret && aSecret === trimmed(f.client_secret);
  const warning = sameId || sameSecret ? TOAST_SAME_KEYS : null;

  if (missing.length) return { error: TOAST_MAIN_KEYS_MISSING, fields: missing, warning };
  // Nothing saved yet: an Analytics pair needs both halves.
  if (!configured && aId && !aSecret) {
    return {
      error: "Add the Analytics client secret too. The Analytics keys only work as a pair.",
      fields: ["analytics_client_secret"],
      warning,
    };
  }
  if (aSecret && !aId) {
    return {
      error: "Add the Analytics client ID too. The Analytics keys only work as a pair.",
      fields: ["analytics_client_id"],
      warning,
    };
  }
  return { error: null, fields: [], warning };
}

// A Test connection reply -> the lines to show: Toast answers one line per
// key pair ("Main keys: working", "Analytics keys: not added"); others one.
export function testResultLines(reply) {
  const r = reply && typeof reply === "object" ? reply : {};
  const parts = Array.isArray(r.parts) ? r.parts.filter((p) => p && typeof p === "object" && p.label && p.text) : [];
  if (parts.length) {
    return parts.map((p) => ({ text: `${p.label}: ${p.text}`, ok: !!p.ok, state: String(p.state || "") }));
  }
  const ok = r.ok !== false && r.status !== "failed";
  const text = r.message || r.last_test_message || (ok ? "Connection works." : "Connection failed.");
  return [{ text, ok, state: ok ? "working" : "failed" }];
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
  if (code === "not_available") return message || "This isn't available on this server.";
  if (code === "clover_app_not_configured") {
    return message || "Connect with Clover isn't set up on this service yet. Use an API token instead.";
  }
  if (status === 429) return "Too many tests in a row. Wait a minute and try again.";
  if (status === 403) return message || "Only a store admin can change integrations.";
  return message || "Something went wrong. Please try again.";
}

// --- Clover (website-ready track D3) -------------------------------------------

export const CLOVER_REGIONS = [
  { value: "us", label: "US and Canada" },
  { value: "eu", label: "Europe" },
  { value: "la", label: "Latin America" },
  { value: "sandbox", label: "Sandbox (testing)" },
];

const CLOVER_REGION_LABEL = Object.fromEntries(CLOVER_REGIONS.map((r) => [r.value, r.label]));

// GET /api/settings/integrations/clover/status -> what the Clover card shows.
export function cloverView(status) {
  const s = status && typeof status === "object" ? status : {};
  const connected = !!s.connected;
  const statusKey = connected ? (STATUS[s.status] ? s.status : "untested") : "none";
  const mode = s.mode === "oauth" ? "oauth" : s.mode === "token" ? "token" : null;
  const lines = [];
  if (connected && s.merchant_name) lines.push(["Merchant", s.merchant_name]);
  if (connected && s.merchant_id) lines.push(["Merchant ID", s.merchant_id]);
  if (connected && s.region) lines.push(["Region", CLOVER_REGION_LABEL[s.region] || s.region]);
  if (connected && mode) lines.push(["Connected with", mode === "oauth" ? "Connect with Clover" : "API token"]);
  return {
    connected,
    mode,
    editable: s.editable !== false,
    oauthEnabled: !!s.oauth_enabled,
    environment: s.environment || null,
    status: statusKey,
    statusLabel: STATUS[statusKey].label,
    tone: STATUS[statusKey].tone,
    savedText: connected && s.last4 ? `saved •••• ${s.last4}` : connected ? "saved" : "",
    lines,
    merchantId: s.merchant_id || "",
    region: s.region || "us",
    lastTestedAt: s.last_tested_at || null,
    lastTestMessage: s.last_test_message || null,
  };
}

const RETURN_REASONS = {
  expired: "The Clover sign-in took longer than 10 minutes. Choose Connect with Clover again.",
  invalid: "That Clover sign-in link isn't valid any more. Choose Connect with Clover again.",
  denied: "Clover didn't connect: the request was cancelled in Clover.",
  refused: "Clover refused the connection. Try again, or use an API token.",
  not_configured: "Connect with Clover isn't set up on this service yet. Use an API token instead.",
};

// Back from Clover: ?clover=connected | ?clover=error&reason=... -> a banner.
export function cloverReturnMessage(search) {
  let params;
  try {
    params = new URLSearchParams(search || "");
  } catch {
    return null;
  }
  const result = params.get("clover");
  if (result === "connected") return { tone: "ok", text: "Clover is connected. Your sales and items start syncing now." };
  if (result === "error") {
    return { tone: "bad", text: RETURN_REASONS[params.get("reason")] || RETURN_REASONS.refused };
  }
  return null;
}

// The manual form -> {errors, body}. Merchant IDs are letters and digits
// (Clover shows them as 13 capitals); a blank token keeps the saved one only
// for the same merchant and region.
export function validateCloverManual(form, view) {
  const merchant = String(form?.merchant_id || "").trim().toUpperCase();
  const token = String(form?.api_token || "").trim();
  const region = CLOVER_REGION_LABEL[form?.region] ? form.region : "us";
  const errors = {};
  if (!/^[A-Z0-9]{6,32}$/.test(merchant)) errors.merchant_id = "Enter the Merchant ID from your Clover dashboard (letters and digits).";
  const samePlace = !!view?.connected && view.mode === "token" && view.merchantId === merchant && view.region === region;
  if (!token && !samePlace) errors.api_token = "Enter the API token.";
  return { errors, body: { merchant_id: merchant, api_token: token, region } };
}
