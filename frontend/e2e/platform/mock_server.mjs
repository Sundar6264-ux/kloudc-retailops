// A stand-in for the shared KloudC RetailOps service's platform routes
// (docs/PLATFORM_CONTRACT.md 4.5, 12.3, 13), for the website's platform
// checks (platform.mjs) until the real backend tracks are merged. Serves the
// built website and answers in the contract's shapes, in memory, with fake
// stores and generated passwords only. No outside calls.
//
//   MOCK_DIST=<built site> MOCK_PORT=8871 node mock_server.mjs
//
// Fake stores: DEMO_GROCERY (active, Toast), PAUSED_MART (suspended),
// MOVED_MART (dedicated, answers store_moved). Master: Store ID PLATFORM.
// Passwords come from the environment (run.sh makes random ones).
import { createServer } from "node:http";
import { randomBytes, createHash } from "node:crypto";
import { readFileSync, existsSync, statSync } from "node:fs";
import { extname, join, normalize } from "node:path";

const DIST = process.env.MOCK_DIST;
const PORT = Number(process.env.MOCK_PORT || 8871);
const STORE_PW = process.env.MOCK_STORE_PASSWORD;
const MASTER_PW = process.env.MOCK_MASTER_PASSWORD;
if (!DIST || !STORE_PW || !MASTER_PW) {
  console.error("MOCK_DIST, MOCK_STORE_PASSWORD and MOCK_MASTER_PASSWORD are required");
  process.exit(2);
}
const ORIGIN = `http://127.0.0.1:${PORT}`;
const FEATURES = [
  "overview",
  "invoice_submission",
  "daily_checklists",
  "help",
  "team_tasks",
  "team_chat",
  "purchase_order_cart",
  "reorder_candidates",
];

const now = () => new Date().toISOString();
const sha = (s) => createHash("sha256").update(s).digest("hex");

// --- state ---------------------------------------------------------------
const stores = new Map();
function addStore(code, name, status, extra = {}) {
  stores.set(code, {
    id: stores.size + 1,
    code,
    name,
    status,
    hosting: "shared",
    api_base_url: null,
    pos_provider: "toast",
    timezone: "America/Chicago",
    created_at: now(),
    approved_at: now(),
    schema_revision: "0062",
    status_reason: null,
    maintenance_message: null,
    users: new Map([
      ["owner", { id: 1, username: "owner", password: STORE_PW, is_admin: true }],
      ["staff", { id: 2, username: "staff", password: STORE_PW, is_admin: false }],
    ]),
    credentials: new Map(),
    settings: { store_name: name, brand_color: "#1D4ED8", ai_enabled: true },
    ...extra,
  });
}
addStore("DEMO_GROCERY", "Demo Grocery", "active");
addStore("PAUSED_MART", "Paused Mart", "suspended", { status_reason: "Paused by the platform.", maintenance_message: "Paused for maintenance." });
addStore("MOVED_MART", "Moved Mart", "active", { hosting: "dedicated", api_base_url: "https://moved.retailops.invalid" });
addStore("CLOVER_MART", "Clover Mart", "active", { pos_provider: "clover" });

const POS_NAMES = { toast: "Toast", clover: "Clover", csv: "CSV upload" };

const masters = new Map([["owner", { username: "owner", password: MASTER_PW, display_name: "Platform Owner", role: "master" }]]);
const sessions = new Map(); // sha(token) -> {scope, code?, username}
const signups = [];
const hosting = [];
const audit = [];
const failures = []; // {key, at}
const RESERVED = new Set(["PLATFORM", "MASTER", "ADMIN", "ROOT", "SYSTEM", "API", "APP", "WWW", "STATIC", "DIRECTORY", "SUPPORT", "HELP", "STOREOPS", "RETAILOPS", "KLOUDC", "TEST", "NULL", "NONE"]);
const DEDICATED_HOSTING_ENABLED = false;

function log(action, extra = {}) {
  audit.unshift({ id: audit.length + 1, at: now(), action, actor_type: "system", ...extra });
}

// --- helpers -------------------------------------------------------------
function send(res, status, body, headers = {}) {
  const data = body === undefined ? "" : JSON.stringify(body);
  res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store", ...headers });
  res.end(data);
}

async function readBody(req) {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const text = Buffer.concat(chunks).toString("utf8");
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return {};
  }
}

function cookieToken(req) {
  const m = /(?:^|;\s*)session_token=([^;]+)/.exec(req.headers.cookie || "");
  return m ? decodeURIComponent(m[1]) : null;
}

function session(req) {
  const t = cookieToken(req) || (req.headers.authorization || "").replace(/^Bearer /, "") || null;
  return t ? sessions.get(sha(t)) || null : null;
}

function norm(code) {
  return String(code || "").trim().toUpperCase().replace(/-/g, "_");
}

function branding(store) {
  if (!store) {
    return {
      store_code: null,
      name: null,
      product: { name: "KloudC RetailOps", company: "KloudC LLC" },
      legal: { terms_url: "https://kloudc.invalid/terms", privacy_url: "https://kloudc.invalid/privacy" },
    };
  }
  return {
    store_code: store.code,
    name: store.name,
    short_name: store.name.split(" ")[0],
    primary_color: "#1D4ED8",
    secondary_color: "#2EA3F2",
    timezone: store.timezone,
    pos_name: POS_NAMES[store.pos_provider],
    capabilities: ["sales", "catalog", "stock_levels", "open_orders"],
    store_features: ["ai"],
    logo_url: null,
    product: { name: "KloudC RetailOps", company: "KloudC LLC" },
  };
}

function me(store, user) {
  return {
    id: user.id,
    username: user.username,
    is_admin: user.is_admin,
    allowed_features: user.is_admin ? FEATURES : ["overview", "help"],
    hide_sales: false,
    store_code: store.code,
    pos_name: POS_NAMES[store.pos_provider],
    capabilities: ["sales", "catalog", "stock_levels", "open_orders"],
    store_features: ["ai"],
    scope: "store",
    api_base_url: null,
  };
}

function locked(key) {
  const since = Date.now() - 15 * 60 * 1000;
  return failures.filter((f) => f.key === key && f.at > since).length >= 5;
}

const SPECS = {
  toast: { label: "Toast", secret: ["client_secret", "analytics_client_secret"], primary: "client_secret" },
  clover: { label: "Clover", secret: ["access_token", "refresh_token"], primary: "access_token" },
  whatsapp: { label: "WhatsApp", secret: ["api_token"], primary: "api_token" },
  smtp: { label: "Email (SMTP)", secret: ["password"], primary: "password" },
  ai: { label: "AI", secret: ["api_key"], primary: "api_key" },
};

function credView(store, kind) {
  const c = store.credentials.get(kind);
  return {
    kind,
    label: SPECS[kind].label,
    configured: !!c,
    status: c ? c.status : null,
    last4: c ? c.last4 : null,
    public: c ? c.public : {},
    updated_at: c ? c.updated_at : null,
    updated_by: c ? c.updated_by : null,
    last_tested_at: c ? c.last_tested_at : null,
    last_test_message: c ? c.last_test_message : null,
  };
}

// --- API -----------------------------------------------------------------
async function api(req, res, url) {
  const p = url.pathname;
  const method = req.method;
  const s = session(req);

  if (p === "/api/health") return send(res, 200, { status: "ok" });
  if (p === "/api/app/branding") return send(res, 200, branding(stores.get(norm(url.searchParams.get("store"))) || (s?.code ? stores.get(s.code) : null)));
  if (p === "/api/app/config") return send(res, 200, { min_supported_build: { ios: 0, android: 0 } });

  // Store lookup (13.4)
  let m = /^\/api\/public\/stores\/([^/]+)$/.exec(p);
  if (m) {
    const st = stores.get(norm(decodeURIComponent(m[1])));
    if (!st || !["active", "suspended"].includes(st.status)) return send(res, 404, { error: "unknown_store", message: "No store with that code." });
    return send(res, 200, {
      schema: 1,
      code: st.code,
      name: st.name,
      api_base_url: st.api_base_url || ORIGIN,
      status: st.status === "suspended" ? "maintenance" : "active",
      message: st.status === "suspended" ? st.maintenance_message : null,
      branding: { short_name: st.name.split(" ")[0], primary_color: "#1D4ED8" },
      min_build: { ios: 0, android: 0 },
      updated_at: now(),
    });
  }

  // Login (4.5)
  if (p === "/api/auth/login" && method === "POST") {
    const b = await readBody(req);
    const code = norm(b.store_code);
    const user = String(b.username || "").toLowerCase();
    const key = `${code}:${user}`;
    if (locked(key)) {
      log("login.locked", { store_code: code, target: user });
      return send(res, 429, { error: "locked", message: "Too many tries.", retry_after: 840 });
    }
    const fail = () => {
      failures.push({ key, at: Date.now() });
      log("login.failed", { store_code: code, target: user });
      return send(res, 401, { detail: "That Store ID, username or password isn't right. Check all three and try again." });
    };
    if (code === "PLATFORM") {
      const u = masters.get(user);
      if (!u || u.password !== b.password) return fail();
      const token = `PLATFORM.${randomBytes(24).toString("base64url")}`;
      sessions.set(sha(token), { scope: "platform", username: u.username });
      log("platform_login.ok", { actor_type: "platform_user", actor_name: u.username });
      return send(res, 200, { scope: "platform", user: { username: u.username, display_name: u.display_name, role: u.role } }, {
        "Set-Cookie": `session_token=${encodeURIComponent(token)}; Path=/; HttpOnly; SameSite=Lax`,
      });
    }
    const st = stores.get(code);
    const u = st?.users.get(user);
    if (!st || !u || u.password !== b.password || st.status === "archived") return fail();
    if (st.hosting === "dedicated") return send(res, 409, { error: "store_moved", api_base_url: st.api_base_url });
    if (st.status === "suspended") return send(res, 403, { error: "store_suspended", message: "This store is paused. Please contact the store owner." });
    if (st.status !== "active") return send(res, 403, { error: "store_not_ready", message: "This store isn't ready yet." });
    const token = `${code}.${randomBytes(24).toString("base64url")}`;
    sessions.set(sha(token), { scope: "store", code, username: u.username });
    log("login.ok", { store_code: code, actor_type: "store_user", actor_name: u.username });
    return send(res, 200, me(st, u), { "Set-Cookie": `session_token=${encodeURIComponent(token)}; Path=/; HttpOnly; SameSite=Lax` });
  }
  if (p === "/api/auth/logout" && method === "POST") {
    const t = cookieToken(req);
    if (t) sessions.delete(sha(t));
    return send(res, 200, { ok: true }, { "Set-Cookie": "session_token=; Path=/; Max-Age=0" });
  }
  if (p === "/api/auth/me") {
    if (!s) return send(res, 401, { detail: "Not logged in" });
    if (s.scope === "platform") {
      const u = masters.get(s.username);
      return send(res, 200, { scope: "platform", user: { username: u.username, display_name: u.display_name, role: u.role } });
    }
    const st = stores.get(s.code);
    return send(res, 200, me(st, st.users.get(s.username)));
  }

  // Sign-up (13.3)
  if (p === "/api/signup/code-available") {
    const code = norm(url.searchParams.get("code"));
    const taken = stores.has(code) || signups.some((r) => r.status === "pending" && r.requested_code === code);
    const reserved = RESERVED.has(code);
    // As the real server: `reason` is a code, `message` its text.
    return send(res, 200, { code, available: !taken && !reserved, ...(taken ? { reason: "taken", message: "That code is already used by another store." } : reserved ? { reason: "reserved", message: "That code is reserved." } : {}) });
  }
  if (p === "/api/signup" && method === "POST") {
    const b = await readBody(req);
    if (b.website) return send(res, 202, { request_id: 0, status_token: "x", status: "pending" }); // honeypot: looks fine, stored nowhere
    const code = norm(b.requested_code);
    if (!b.accept_terms || !b.store_name || String(b.owner_password || "").length < 10 || stores.has(code) || RESERVED.has(code)) {
      return send(res, 400, { error: "invalid", message: "Please check the form." });
    }
    const token = randomBytes(18).toString("base64url");
    const r = {
      id: signups.length + 1,
      status: "pending",
      status_token_hash: sha(token),
      requested_code: code,
      store_name: b.store_name,
      contact_name: b.contact_name,
      contact_email: b.contact_email,
      contact_phone: b.contact_phone || null,
      address: b.address || null,
      timezone: b.timezone,
      pos_provider: b.pos_provider,
      owner_username: b.owner_username,
      owner_password: b.owner_password, // the mock only; the real service keeps a bcrypt hash
      notes: b.notes || null,
      created_at: now(),
    };
    signups.push(r);
    log("signup.created", { actor_type: "signup", target: code });
    return send(res, 202, { request_id: r.id, status_token: token, status: "pending" });
  }
  if (p === "/api/signup/status") {
    const r = signups.find((x) => String(x.id) === url.searchParams.get("request_id"));
    if (!r || r.status_token_hash !== sha(url.searchParams.get("token") || "")) return send(res, 404, { error: "not_found" });
    return send(res, 200, { status: r.status, ...(r.status === "approved" ? { store_code: r.store_code } : {}), ...(r.review_note ? { message: r.review_note } : {}) });
  }

  // Master console (13.1)
  if (p.startsWith("/api/platform/")) {
    if (!s) return send(res, 401, { detail: "Not logged in" });
    if (s.scope !== "platform") return send(res, 403, { error: "forbidden", message: "Masters only." });
    const actor = { actor_type: "platform_user", actor_name: s.username };
    const sub = p.slice("/api/platform".length);
    if (sub === "/signups") {
      const status = url.searchParams.get("status");
      return send(res, 200, signups.filter((r) => !status || r.status === status).map(({ owner_password, status_token_hash, ...rest }) => rest));
    }
    if ((m = /^\/signups\/(\d+)\/(approve|reject)$/.exec(sub)) && method === "POST") {
      const r = signups.find((x) => x.id === Number(m[1]));
      const b = await readBody(req);
      if (!r || r.status !== "pending") return send(res, 409, { error: "not_pending", message: "That request isn't waiting any more." });
      if (m[2] === "reject") {
        r.status = "rejected";
        r.review_note = b.note || null;
        log("signup.rejected", { ...actor, target: r.requested_code });
        return send(res, 200, { status: "rejected" });
      }
      const code = norm(b.code || r.requested_code);
      addStore(code, r.store_name, "active", { pos_provider: r.pos_provider, timezone: b.timezone || r.timezone });
      stores.get(code).users = new Map([[r.owner_username.toLowerCase(), { id: 1, username: r.owner_username, password: r.owner_password, is_admin: true }]]);
      r.status = "approved";
      r.store_code = code;
      log("signup.approved", { ...actor, store_code: code });
      log("store.provisioned", { actor_type: "system", store_code: code, details: JSON.stringify({ pack_id: b.pack_id || "default" }) });
      return send(res, 202, { store_code: code, status: "provisioning" });
    }
    if (sub === "/stores") {
      const status = url.searchParams.get("status");
      return send(res, 200, [...stores.values()].filter((x) => !status || x.status === status).map(({ users, credentials, settings, ...rest }) => rest));
    }
    if ((m = /^\/stores\/([A-Z0-9_]+)\/(suspend|resume|retry-provisioning|archive)$/.exec(sub)) && method === "POST") {
      const st = stores.get(m[1]);
      if (!st) return send(res, 404, { error: "unknown_store" });
      const b = await readBody(req);
      if (m[2] === "suspend") (st.status = "suspended"), (st.status_reason = b.reason || null);
      if (m[2] === "resume") (st.status = "active"), (st.status_reason = null);
      if (m[2] === "retry-provisioning") st.status = "active";
      if (m[2] === "archive") {
        if (b.confirm !== st.code) return send(res, 400, { error: "confirm", message: "Type the Store ID to confirm." });
        st.status = "archived";
      }
      log(`store.${m[2].replace("-provisioning", "_provisioning")}`, { ...actor, store_code: st.code });
      return send(res, 200, { ok: true });
    }
    if ((m = /^\/stores\/([A-Z0-9_]+)\/settings$/.exec(sub))) {
      const st = stores.get(m[1]);
      if (method === "PUT") {
        Object.assign(st.settings, await readBody(req));
        log("store.settings_changed", { ...actor, store_code: st.code });
      }
      return send(res, 200, { settings: st.settings });
    }
    if ((m = /^\/stores\/([A-Z0-9_]+)\/hosting-requests$/.exec(sub)) && method === "POST") {
      if (!DEDICATED_HOSTING_ENABLED) return send(res, 409, { error: "dedicated_hosting_disabled", message: "Dedicated hosting is off." });
    }
    // As the real server: the list answers {enabled, requests}; the actions 409 while off.
    if (sub === "/hosting-requests") return send(res, 200, { enabled: DEDICATED_HOSTING_ENABLED, requests: DEDICATED_HOSTING_ENABLED ? hosting : [] });
    if (/^\/hosting-requests\/\d+\//.test(sub)) {
      if (!DEDICATED_HOSTING_ENABLED) return send(res, 409, { error: "dedicated_hosting_disabled", message: "Dedicated hosting is off." });
      return send(res, 200, hosting);
    }
    if (sub === "/audit") {
      const store = url.searchParams.get("store");
      const action = url.searchParams.get("action");
      return send(res, 200, audit.filter((a) => (!store || a.store_code === store) && (!action || a.action === action)).slice(0, 200));
    }
    if (sub === "/packs") {
      return send(res, 200, [
        { pack_id: "grocery_us", label: "US grocery", private: false },
        { pack_id: "restaurant_us", label: "US restaurant", private: false },
      ]);
    }
    return send(res, 404, { error: "not_found" });
  }

  // Integrations (13.5)
  if (p.startsWith("/api/settings/integrations")) {
    if (!s) return send(res, 401, { detail: "Not logged in" });
    if (s.scope !== "store") return send(res, 403, { detail: "Store admins only" });
    const st = stores.get(s.code);
    if (!st.users.get(s.username)?.is_admin) return send(res, 403, { detail: "Admins only" });
    const sub = p.slice("/api/settings/integrations".length);
    if (sub === "" && method === "GET") return send(res, 200, Object.keys(SPECS).map((k) => credView(st, k)));
    if (sub === "/clover/connect") return send(res, 501, { error: "not_available", message: "Clover connection arrives with M3" });
    m = /^\/([a-z]+)(\/test)?$/.exec(sub);
    if (!m || !SPECS[m[1]]) return send(res, 404, { error: "unknown_kind" });
    const kind = m[1];
    const spec = SPECS[kind];
    if (m[2]) {
      const b = await readBody(req);
      const c = st.credentials.get(kind);
      const secret = (b.secrets && b.secrets[spec.primary]) || c?.secrets?.[spec.primary] || "";
      const ok = !!secret && !secret.startsWith("bad");
      const message = ok ? "Connected." : "The service refused these details.";
      if (c && !b.secrets) Object.assign(c, { status: ok ? "ok" : "failed", last_tested_at: now(), last_test_message: message });
      log("credential.tested", { store_code: st.code, target: kind });
      return send(res, 200, { ok, message });
    }
    if (method === "PUT") {
      const b = await readBody(req);
      const old = st.credentials.get(kind) || { secrets: {}, public: {}, created_at: now() };
      const secrets = { ...old.secrets };
      for (const [k, v] of Object.entries(b.secrets || {})) if (spec.secret.includes(k) && v) secrets[k] = v;
      for (const k of b.clear || []) delete secrets[k];
      const primary = secrets[spec.primary] || "";
      st.credentials.set(kind, {
        ...old,
        public: b.public || {},
        secrets,
        last4: primary ? primary.slice(-4) : null,
        status: "untested",
        last_tested_at: null,
        last_test_message: null,
        updated_at: now(),
        updated_by: `store:${st.code}:${s.username}`,
      });
      log("credential.saved", { store_code: st.code, target: kind });
      return send(res, 200, credView(st, kind));
    }
    if (method === "DELETE") {
      st.credentials.delete(kind);
      log("credential.deleted", { store_code: st.code, target: kind });
      return send(res, 200, { ok: true });
    }
  }

  if (!s) return send(res, 401, { detail: "Not logged in" });
  // Store pages' own data: not part of this mock.
  return send(res, 404, { detail: "Not in the platform mock" });
}

// --- static --------------------------------------------------------------
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".png": "image/png", ".svg": "image/svg+xml", ".json": "application/json", ".ico": "image/x-icon", ".webmanifest": "application/manifest+json" };

function serveStatic(res, pathname) {
  let file = normalize(join(DIST, decodeURIComponent(pathname)));
  if (!file.startsWith(DIST)) return send(res, 404, { detail: "Not found" });
  if (!existsSync(file) || statSync(file).isDirectory() || pathname.startsWith("/c/")) file = join(DIST, "index.html");
  res.writeHead(200, { "Content-Type": TYPES[extname(file)] || "application/octet-stream" });
  res.end(readFileSync(file));
}

createServer(async (req, res) => {
  try {
    const url = new URL(req.url, ORIGIN);
    if (url.pathname.startsWith("/api/")) return await api(req, res, url);
    if (url.pathname.startsWith("/branding/")) return send(res, 404, { detail: "no logo" });
    return serveStatic(res, url.pathname);
  } catch (e) {
    console.error(e);
    send(res, 500, { detail: "mock error" });
  }
}).listen(PORT, "127.0.0.1", () => console.log(JSON.stringify({ mock: "platform", port: PORT })));
