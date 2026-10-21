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
  fieldGroups,
  formProblems,
  groupOfField,
  testResultLines,
  TOAST_MAIN_KEYS_MISSING,
  TOAST_SAME_KEYS,
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

// --- Toast's two key pairs ---------------------------------------------------

const toastForm = (over = {}) => ({
  client_id: "main-id",
  client_secret: "main-secret-1111",
  restaurant_guid: "guid-1",
  api_base: "",
  analytics_client_id: "",
  analytics_client_secret: "",
  ...over,
});
const NEW = integrationView(TOAST, null);
const SAVED = integrationView(TOAST, { configured: true, status: "ok", last4: "1111", public: { client_id: "main-id" } });

test("Toast form: two labelled groups, API address under Advanced", () => {
  const groups = fieldGroups(TOAST);
  assert.deepEqual(groups.map((g) => g.group.title), ["Main Toast keys (required)", "Toast Analytics keys (recommended)"]);
  assert.deepEqual(groups[0].fields.map((f) => f.label), ["Client ID", "Client secret", "Restaurant ID (GUID)"]);
  assert.deepEqual(groups[0].advanced.map((f) => f.key), ["api_base"]);
  assert.deepEqual(groups[1].fields.map((f) => f.label), ["Analytics client ID", "Analytics client secret"]);
  assert.match(groups[0].group.hint, /Integrations › Toast API access/);
  assert.match(groups[0].group.hint, /sales, menu and stock/);
  assert.match(groups[1].group.hint, /Analytics scope/);
  assert.match(groups[1].group.hint, /double-check each day's sales totals/);
  for (const f of TOAST.fields) assert.ok(!/optional/i.test(f.label), f.label);
  // Other kinds: one unnamed group with every field.
  assert.deepEqual(fieldGroups(SMTP).map((g) => [g.group, g.fields.length]), [[null, SMTP.fields.length]]);
});

test("Toast: only the Analytics keys filled -> the plain main-keys sentence", () => {
  const form = toastForm({ client_id: "", client_secret: "", restaurant_guid: "", analytics_client_id: "an-id",
                            analytics_client_secret: "an-secret-2222" });
  const p = formProblems(TOAST, NEW, form);
  assert.equal(p.error, TOAST_MAIN_KEYS_MISSING);
  assert.equal(p.error,
    "Fill in the main Toast keys first (Client ID, Client secret and Restaurant ID). The Analytics keys are an extra.");
  assert.deepEqual(p.fields, ["client_id", "client_secret", "restaurant_guid"]);
  // One main field empty: the same sentence, naming that field.
  assert.deepEqual(formProblems(TOAST, NEW, toastForm({ restaurant_guid: "  " })).fields, ["restaurant_guid"]);
});

test("the stopping sentence sits in the group of the fields it is about", () => {
  assert.equal(groupOfField(TOAST, "client_id"), "main");
  assert.equal(groupOfField(TOAST, "api_base"), "main");
  assert.equal(groupOfField(TOAST, "analytics_client_secret"), "analytics");
  assert.equal(groupOfField(TOAST, "nope"), null);
  assert.equal(groupOfField(INTEGRATIONS_BY_KIND.whatsapp, "api_token"), null);
});

test("Toast: a blank Client secret keeps the saved one, unless it is being removed", () => {
  assert.equal(formProblems(TOAST, SAVED, toastForm({ client_secret: "" })).error, null);
  assert.equal(formProblems(TOAST, NEW, toastForm({ client_secret: "" })).error, TOAST_MAIN_KEYS_MISSING);
  assert.equal(formProblems(TOAST, SAVED, toastForm({ client_secret: "" }), ["client_secret"]).error, TOAST_MAIN_KEYS_MISSING);
});

test("Toast: main keys only is fine; a new Analytics pair needs both halves", () => {
  assert.deepEqual(formProblems(TOAST, NEW, toastForm()), { error: null, fields: [], warning: null });
  const half = formProblems(TOAST, NEW, toastForm({ analytics_client_id: "an-id" }));
  assert.match(half.error, /Add the Analytics client secret too/);
  assert.deepEqual(half.fields, ["analytics_client_secret"]);
  // Saved before: a blank Analytics secret may be the saved one (the server warns if not).
  assert.equal(formProblems(TOAST, SAVED, toastForm({ analytics_client_id: "an-id" })).error, null);
  assert.match(formProblems(TOAST, SAVED, toastForm({ analytics_client_secret: "an-secret-2222" })).error,
    /Add the Analytics client ID too/);
});

test("Toast: the same keys in both groups -> a plain warning, not a block", () => {
  const same = formProblems(TOAST, NEW, toastForm({ analytics_client_id: " MAIN-ID ", analytics_client_secret: "main-secret-1111" }));
  assert.equal(same.error, null);
  assert.equal(same.warning,
    "These look like the same keys as the main ones. Toast gives Analytics its own Client ID and secret.");
  assert.equal(same.warning, TOAST_SAME_KEYS);
  assert.equal(formProblems(TOAST, NEW, toastForm({ analytics_client_id: "other", analytics_client_secret: "main-secret-1111" })).warning,
    TOAST_SAME_KEYS, "same secret, different ID");
  assert.equal(formProblems(TOAST, NEW, toastForm({ analytics_client_id: "an-id", analytics_client_secret: "an-secret" })).warning, null);
  assert.ok(!JSON.stringify(same).includes("main-secret-1111"), "never echoes a secret");
  assert.deepEqual(formProblems(SMTP, NEW, {}), { error: null, fields: [], warning: null });
});

test("Test connection lines: one per Toast key pair, else the message", () => {
  const lines = testResultLines({
    ok: true,
    message: "Main keys: working. Analytics keys: not added.",
    parts: [
      { name: "main", label: "Main keys", ok: true, state: "working", text: "working. Connected to Alpha Grill." },
      { name: "analytics", label: "Analytics keys", ok: false, state: "not_added", text: "not added." },
    ],
  });
  assert.deepEqual(lines.map((l) => [l.text, l.ok]), [
    ["Main keys: working. Connected to Alpha Grill.", true],
    ["Analytics keys: not added.", false],
  ]);
  assert.deepEqual(testResultLines({ ok: false, message: "WhatsApp refused" }), [{ text: "WhatsApp refused", ok: false, state: "failed" }]);
  assert.equal(testResultLines({ ok: true })[0].text, "Connection works.");
});

test("a 400 from the server shows its plain sentence", () => {
  const e = Object.assign(new Error("x"), { status: 400, body: { error: "invalid", message: TOAST_MAIN_KEYS_MISSING, field: "client_id" } });
  assert.equal(integrationErrorMessage(e), TOAST_MAIN_KEYS_MISSING);
});
