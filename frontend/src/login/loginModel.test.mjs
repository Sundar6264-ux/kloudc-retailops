// The single login page's pure helpers (docs/PLATFORM_CONTRACT.md 4.5, 12.3, 14).
//   node --test src/login/loginModel.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  LAST_STORE_KEY,
  LOGIN_MESSAGES,
  errorInfo,
  initialStoreId,
  isPlatformCode,
  isPlatformUser,
  loginErrorMessage,
  movedLoginUrl,
  normalizeMe,
  normalizeStoreCode,
  readLastStore,
  rememberStore,
  storeFromPath,
  validStoreCode,
} from "./loginModel.js";

function memoryStorage(initial = {}) {
  const data = { ...initial };
  return {
    data,
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => {
      data[k] = String(v);
    },
  };
}

const throwing = {
  getItem() {
    throw new Error("SecurityError");
  },
  setItem() {
    throw new Error("QuotaExceeded");
  },
};

function apiError(status, body) {
  const e = new Error("x");
  e.status = status;
  e.body = body;
  return e;
}

test("store codes are normalised like the server does", () => {
  assert.equal(normalizeStoreCode("  xyz-grocery "), "XYZ_GROCERY");
  assert.equal(validStoreCode("xyz-grocery"), "XYZ_GROCERY");
  assert.equal(validStoreCode("ab"), null, "too short");
  assert.equal(validStoreCode("_ABC"), null, "must start with a letter or digit");
  assert.equal(validStoreCode("A".repeat(25)), null, "too long");
  assert.equal(validStoreCode("AB C"), null);
  assert.equal(validStoreCode(null), null);
  assert.ok(isPlatformCode(" platform "));
  assert.ok(!isPlatformCode("DEMO"));
});

test("the last Store ID is remembered, and storage errors never break the page", () => {
  const s = memoryStorage();
  assert.equal(readLastStore(s), "");
  rememberStore(s, "demo");
  assert.equal(s.data[LAST_STORE_KEY], "DEMO");
  assert.equal(readLastStore(s), "DEMO");
  rememberStore(s, "x"); // invalid: not saved
  assert.equal(readLastStore(s), "DEMO");
  assert.equal(readLastStore(memoryStorage({ [LAST_STORE_KEY]: "<script>" })), "");
  assert.equal(readLastStore(throwing), "");
  assert.doesNotThrow(() => rememberStore(throwing, "DEMO"));
  assert.equal(readLastStore(undefined), "");
});

test("the field starts with ?store=, then the remembered one, then the server's own", () => {
  assert.equal(initialStoreId({ queryStore: "abc_1", remembered: "DEMO", brandingCode: "XYZ" }), "ABC_1");
  assert.equal(initialStoreId({ queryStore: "!", remembered: "DEMO", brandingCode: "XYZ" }), "DEMO");
  assert.equal(initialStoreId({ brandingCode: "retailops-test-toast" }), "RETAILOPS_TEST_TOAST");
  assert.equal(initialStoreId({}), "");
  assert.equal(initialStoreId(), "");
});

test("error bodies: new {error,message}, FastAPI {detail} and {detail:{...}}", () => {
  assert.deepEqual(errorInfo({ error: "locked", message: "Wait", retry_after: 30 }).code, "locked");
  assert.equal(errorInfo({ detail: "Not logged in" }).message, "Not logged in");
  const nested = errorInfo({ detail: { error: "store_moved", api_base_url: "https://a.example" } });
  assert.equal(nested.code, "store_moved");
  assert.equal(nested.data.api_base_url, "https://a.example");
  assert.deepEqual(errorInfo(null), { code: null, message: null, data: {} });
  assert.equal(errorInfo({ detail: [{ msg: "x" }] }).message, null, "a validation list is not a sentence");
});

test("login errors: 401 / 403 suspended and not ready / 409 moved / 429 / network", () => {
  assert.deepEqual(loginErrorMessage(apiError(401, { detail: "Incorrect store ID, username or password" })), {
    kind: "incorrect",
    message: "Incorrect store ID, username or password",
  });
  assert.equal(loginErrorMessage(apiError(401, null)).message, LOGIN_MESSAGES.incorrect);

  const suspended = loginErrorMessage(apiError(403, { error: "store_suspended", message: "Paused for billing." }));
  assert.deepEqual(suspended, { kind: "suspended", message: "Paused for billing." });
  assert.equal(loginErrorMessage(apiError(403, { detail: { error: "store_suspended" } })).message, LOGIN_MESSAGES.suspended);
  assert.equal(loginErrorMessage(apiError(403, { error: "store_not_ready" })).kind, "not_ready");

  const moved = loginErrorMessage(apiError(409, { error: "store_moved", api_base_url: "https://xyz.retailops.example" }));
  assert.equal(moved.kind, "moved");
  assert.equal(moved.apiBaseUrl, "https://xyz.retailops.example");
  assert.equal(loginErrorMessage(apiError(409, { error: "store_mismatch" })).kind, "mismatch");

  const locked = loginErrorMessage(apiError(429, { error: "locked", retry_after: 840 }));
  assert.equal(locked.kind, "locked");
  assert.equal(locked.retryAfter, 840);
  assert.equal(locked.message, "Too many sign-in tries. Wait 14 minutes and try again.");
  assert.match(loginErrorMessage(apiError(429, { detail: { error: "locked", retry_after: 45 } })).message, /45 seconds/);
  assert.match(loginErrorMessage(apiError(429, {})).message, /a few minutes/);

  assert.equal(loginErrorMessage(new TypeError("Failed to fetch")).kind, "unreachable");
  assert.equal(loginErrorMessage(apiError(500, null)).message, LOGIN_MESSAGES.generic);
});

test("a moved store's login link is only ever an https origin", () => {
  assert.equal(movedLoginUrl("https://xyz.retailops.example", "xyz"), "https://xyz.retailops.example/#/login?store=XYZ");
  assert.equal(movedLoginUrl("https://xyz.retailops.example/", "XYZ"), "https://xyz.retailops.example/#/login?store=XYZ");
  assert.equal(movedLoginUrl("http://127.0.0.1:8001", "XYZ"), "http://127.0.0.1:8001/#/login?store=XYZ");
  assert.equal(movedLoginUrl("http://xyz.retailops.example", "XYZ"), null, "plain http");
  assert.equal(movedLoginUrl("javascript:alert(1)", "XYZ"), null);
  assert.equal(movedLoginUrl("https://u:p@xyz.example", "XYZ"), null);
  assert.equal(movedLoginUrl("https://xyz.example/evil", "XYZ"), null);
  assert.equal(movedLoginUrl("https://xyz.example?x=1", "XYZ"), null);
  assert.equal(movedLoginUrl("not a url", "XYZ"), null);
  assert.equal(movedLoginUrl("https://xyz.example", "!"), null);
  assert.equal(movedLoginUrl(null, "XYZ"), null);
});

test("/me: a platform session is flattened; a store session keeps every key", () => {
  const platform = normalizeMe({ scope: "platform", user: { username: "owner", display_name: "Owner", role: "master" } });
  assert.deepEqual(platform, {
    scope: "platform",
    username: "owner",
    display_name: "Owner",
    role: "master",
    totp_enrolled: false,
    is_admin: false,
    allowed_features: [],
  });
  assert.ok(isPlatformUser(platform));
  assert.equal(normalizeMe({ scope: "platform", user: { username: "o", totp_enrolled: true } }).totp_enrolled, true);
  const store = normalizeMe({ id: 1, username: "maria", is_admin: true, store_code: "XYZ" });
  assert.equal(store.scope, "store", "an M2 server leaves scope out");
  assert.equal(store.store_code, "XYZ");
  assert.ok(!isPlatformUser(store));
  assert.equal(normalizeMe(null), null);
  assert.ok(!isPlatformUser(null));
});

test("a shared server's checklist link carries the Store ID; a single store's doesn't", () => {
  assert.equal(storeFromPath("/c/xyz-grocery/kitchen-4821"), "XYZ_GROCERY");
  assert.equal(storeFromPath("/c/XYZ/kitchen-4821/"), "XYZ");
  assert.equal(storeFromPath("/c/kitchen-4821"), null);
  assert.equal(storeFromPath("/c/!!/kitchen-4821"), null);
  assert.equal(storeFromPath("/x/XYZ/kitchen-4821"), null);
  assert.equal(storeFromPath(""), null);
});
