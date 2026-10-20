// Settings -> Integrations view model (docs/PLATFORM_CONTRACT.md 8.3, 13.5).
//   node --test src/settings/integrationsModel.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  INTEGRATIONS,
  INTEGRATIONS_BY_KIND,
  buildPayload,
  containsSecret,
  formFromView,
  hasChanges,
  integrationErrorMessage,
  integrationView,
  integrationViews,
  kindsForStore,
  cloverReturnMessage,
  cloverView,
  validateCloverManual,
} from "./integrationsModel.js";

const TOAST = INTEGRATIONS_BY_KIND.toast;
const SMTP = INTEGRATIONS_BY_KIND.smtp;
const WA = INTEGRATIONS_BY_KIND.whatsapp;

test("the five kinds, with the contract's public and secret fields", () => {
  assert.deepEqual(
    INTEGRATIONS.map((i) => i.kind),
    ["toast", "clover", "whatsapp", "smtp", "ai"],
  );
  const secretsOf = (k) => INTEGRATIONS_BY_KIND[k].fields.filter((f) => f.secret).map((f) => f.key);
  assert.deepEqual(secretsOf("toast"), ["client_secret", "analytics_client_secret"]);
  assert.deepEqual(secretsOf("whatsapp"), ["api_token"]);
  assert.deepEqual(secretsOf("smtp"), ["password"]);
  assert.deepEqual(secretsOf("ai"), ["api_key"]);
  assert.ok(INTEGRATIONS_BY_KIND.clover.connect, "Clover is a Connect button");
  for (const spec of INTEGRATIONS) {
    for (const f of spec.fields) assert.equal(f.secret ? f.type : "x", f.secret ? "password" : "x", `${spec.kind}.${f.key}`);
  }
});

test("a saved card shows its status and only the last 4", () => {
  const v = integrationView(TOAST, {
    kind: "toast",
    configured: true,
    status: "ok",
    last4: "9f2a",
    public: { client_id: "abc" },
    last_tested_at: "2026-10-18T10:00:00Z",
    last_test_message: "Connected to the restaurant.",
  });
  assert.equal(v.statusLabel, "Connected");
  assert.equal(v.tone, "good");
  assert.equal(v.savedText, "saved •••• 9f2a");
  assert.equal(v.lastTestMessage, "Connected to the restaurant.");
  assert.equal(integrationView(TOAST, { configured: true, status: "failed" }).statusLabel, "Test failed");
  assert.equal(integrationView(TOAST, { configured: true, status: "weird" }).status, "untested");
  const none = integrationView(TOAST, null);
  assert.equal(none.statusLabel, "Not set up");
  assert.equal(none.savedText, "");
});

test("the list: every kind in page order, missing ones not set up", () => {
  const views = integrationViews([{ kind: "ai", configured: true, status: "untested", last4: "WXYZ" }, { kind: "bogus" }]);
  assert.deepEqual(
    views.map((v) => [v.kind, v.status]),
    [
      ["toast", "none"],
      ["clover", "none"],
      ["whatsapp", "none"],
      ["smtp", "none"],
      ["ai", "untested"],
    ],
  );
  assert.equal(integrationViews(null).length, 5);
});

test("the form starts with the public values; secret boxes always empty", () => {
  const view = integrationView(SMTP, {
    configured: true,
    public: { host: "smtp.example.com", port: 465, ssl: true, username: "po@example.com" },
  });
  assert.deepEqual(formFromView(SMTP, view), {
    host: "smtp.example.com",
    port: "465",
    ssl: true,
    username: "po@example.com",
    password: "",
    from_name: "",
  });
  const wa = formFromView(WA, integrationView(WA, { configured: true, public: { group_chat_ids: { orders: "1@g.us" } } }));
  assert.equal(JSON.parse(wa.group_chat_ids).orders, "1@g.us");
});

test("payload: blank secret = keep, typed secret sent, clear only for untyped secrets", () => {
  const p = buildPayload(TOAST, {
    client_id: " cid ",
    client_secret: "",
    restaurant_guid: "g-1",
    api_base: "",
    analytics_client_id: "",
    analytics_client_secret: "  new-secret ",
  }, { clear: ["client_secret", "analytics_client_secret", "client_id", "bogus"] });
  assert.deepEqual(p, {
    public: { client_id: "cid", restaurant_guid: "g-1" },
    secrets: { analytics_client_secret: "new-secret" },
    clear: ["client_secret"],
  });
});

test("payload: numbers, checkboxes and JSON are checked", () => {
  const ok = buildPayload(SMTP, { host: "h", port: "587", ssl: false, username: "", password: "pw", from_name: "" });
  assert.deepEqual(ok.public, { host: "h", port: 587, ssl: false });
  assert.throws(() => buildPayload(SMTP, { port: "99999" }), /Port: Enter a whole number/);
  assert.throws(() => buildPayload(SMTP, { port: "4.5" }), /whole number/);
  assert.throws(() => buildPayload(WA, { group_chat_ids: "{bad" }), (e) => e.field === "group_chat_ids" && /valid JSON/.test(e.message));
  assert.throws(() => buildPayload(WA, { group_chat_ids: "[1]" }), /JSON object/);
  assert.deepEqual(buildPayload(WA, { group_chat_ids: '{"a":"1@g.us"}', api_token: "" }).public, {
    group_chat_ids: { a: "1@g.us" },
  });
});

test("Save is only offered when something changed", () => {
  const view = integrationView(TOAST, { configured: true, public: { client_id: "cid" } });
  const form = formFromView(TOAST, view);
  assert.equal(hasChanges(TOAST, view, form), false);
  assert.equal(hasChanges(TOAST, view, { ...form, client_secret: "x" }), true);
  assert.equal(hasChanges(TOAST, view, { ...form, client_id: "other" }), true);
  assert.equal(hasChanges(TOAST, view, form, ["client_secret"]), true);
});

test("a reply that would show a typed secret is caught", () => {
  assert.equal(containsSecret({ last4: "cret" }, { client_secret: "my-secret" }), false);
  assert.equal(containsSecret({ echo: "my-secret" }, { client_secret: "my-secret" }), true);
  assert.equal(containsSecret("abc", { short: "ab" }), false, "too short to judge");
  assert.equal(containsSecret(null, {}), false);
});

test("error messages", () => {
  const err = (status, body) => Object.assign(new Error("x"), { status, body });
  assert.match(integrationErrorMessage(err(409, { error: "platform_mode_required" })), /server's settings file/);
  assert.match(integrationErrorMessage(err(409, { detail: { error: "platform_mode_required" } })), /settings file/);
  assert.equal(
    integrationErrorMessage(err(501, { error: "not_available", message: "Not on this server" })),
    "Not on this server",
  );
  assert.match(integrationErrorMessage(err(429, {})), /Wait a minute/);
  assert.match(integrationErrorMessage(err(403, { detail: "The demo account can look around" })), /demo/);
  assert.match(integrationErrorMessage(new TypeError("fetch")), /Can't reach/);
});

test("only the store's own POS connector (a Clover store never sees Toast)", () => {
  assert.deepEqual(kindsForStore("Toast"), ["toast", "whatsapp", "smtp", "ai"]);
  assert.deepEqual(kindsForStore("Clover"), ["clover", "whatsapp", "smtp", "ai"]);
  assert.deepEqual(kindsForStore("CSV upload"), ["whatsapp", "smtp", "ai"]);
  assert.deepEqual(kindsForStore(null), ["toast", "clover", "whatsapp", "smtp", "ai"], "older server: both");
  assert.deepEqual(
    kindsForStore("Clover", [{ kind: "toast", configured: true }]),
    ["toast", "clover", "whatsapp", "smtp", "ai"],
    "a saved one stays visible so it can be removed",
  );
  assert.deepEqual(
    integrationViews([], "Clover").map((v) => v.kind),
    ["clover", "whatsapp", "smtp", "ai"],
  );
});

test("Clover card view: not connected, token, Connect with Clover", () => {
  const off = cloverView({ oauth_enabled: false, connected: false, editable: true });
  assert.equal(off.connected, false);
  assert.equal(off.statusLabel, "Not set up");
  assert.equal(off.oauthEnabled, false);
  assert.deepEqual(off.lines, []);
  const token = cloverView({ connected: true, mode: "token", merchant_id: "TESTMERCH0001", region: "eu", last4: "5555",
    status: "ok", merchant_name: "Sample Corner Market" });
  assert.equal(token.statusLabel, "Connected");
  assert.equal(token.savedText, "saved •••• 5555");
  assert.deepEqual(token.lines, [["Merchant", "Sample Corner Market"], ["Merchant ID", "TESTMERCH0001"],
    ["Region", "Europe"], ["Connected with", "API token"]]);
  const oauth = cloverView({ connected: true, mode: "oauth", status: "untested", merchant_id: "M1", region: "sandbox" });
  assert.equal(oauth.statusLabel, "Saved - not tested");
  assert.deepEqual(oauth.lines.at(-1), ["Connected with", "Connect with Clover"]);
  assert.equal(cloverView(null).editable, true);
  assert.equal(cloverView({ editable: false }).editable, false);
});

test("back from Clover: banner text", () => {
  assert.equal(cloverReturnMessage("?clover=connected").tone, "ok");
  assert.match(cloverReturnMessage("?clover=error&reason=expired").text, /10 minutes/);
  assert.match(cloverReturnMessage("?clover=error&reason=denied").text, /cancelled/);
  assert.match(cloverReturnMessage("?clover=error&reason=zzz").text, /refused/);
  assert.equal(cloverReturnMessage(""), null);
  assert.equal(cloverReturnMessage("?other=1"), null);
});

test("Clover manual form validation", () => {
  const fresh = cloverView({});
  assert.deepEqual(validateCloverManual({ merchant_id: " testmerch0001 ", api_token: " tok ", region: "eu" }, fresh),
    { errors: {}, body: { merchant_id: "TESTMERCH0001", api_token: "tok", region: "eu" } });
  const bad = validateCloverManual({ merchant_id: "x!", api_token: "", region: "mars" }, fresh);
  assert.deepEqual(Object.keys(bad.errors).sort(), ["api_token", "merchant_id"]);
  assert.equal(bad.body.region, "us");
  const saved = cloverView({ connected: true, mode: "token", merchant_id: "TESTMERCH0001", region: "us" });
  assert.deepEqual(validateCloverManual({ merchant_id: "TESTMERCH0001", api_token: "", region: "us" }, saved).errors, {},
    "blank token keeps the saved one for the same merchant and region");
  assert.ok(validateCloverManual({ merchant_id: "TESTMERCH0001", api_token: "", region: "eu" }, saved).errors.api_token);
});

test("Clover 409 when the Clover app isn't set up", () => {
  const e = Object.assign(new Error("x"), { status: 409, body: { error: "clover_app_not_configured", message: "Use a token" } });
  assert.equal(integrationErrorMessage(e), "Use a token");
});
