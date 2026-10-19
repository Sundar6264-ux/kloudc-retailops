// The store this page is signed in to rides on every store request as
// X-Store (docs/PLATFORM_CONTRACT.md 4.3 step 4): on the shared service one
// browser has ONE session cookie, so signing in to another store in another
// tab must make this tab's requests fail (409 store_mismatch) instead of
// reading or writing the other store under this tab's store.
//   node --test src/sessionStore.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { isStoreMismatch, sessionStore, setSessionStore, storeHeaders } from "./sessionStore.js";

test("store requests carry X-Store once a store session is known", () => {
  setSessionStore(null);
  assert.deepEqual(storeHeaders("/api/inventory"), {});
  setSessionStore(" alpha ");
  assert.equal(sessionStore(), "ALPHA");
  assert.deepEqual(storeHeaders("/api/inventory"), { "X-Store": "ALPHA" });
  assert.deepEqual(storeHeaders("/api/team/tasks?x=1"), { "X-Store": "ALPHA" });
  assert.deepEqual(storeHeaders("/api/settings/integrations/toast"), { "X-Store": "ALPHA" });
});

test("sign-in, sign-out, /me, public and platform routes never carry it", () => {
  setSessionStore("ALPHA");
  for (const path of ["/api/auth/login", "/api/auth/mobile-login", "/api/auth/support-login", "/api/auth/logout",
    "/api/auth/me", "/api/signup", "/api/signup/status?x=1", "/api/public/stores/ALPHA", "/api/platform/stores",
    "/api/app/branding?store=ALPHA"]) {
    assert.deepEqual(storeHeaders(path), {}, path);
  }
  setSessionStore(undefined);
  assert.equal(sessionStore(), null);
});

test("409 store_mismatch is recognised (top level or inside detail)", () => {
  assert.equal(isStoreMismatch(409, { error: "store_mismatch" }), true);
  assert.equal(isStoreMismatch(409, { detail: { error: "store_mismatch" } }), true);
  assert.equal(isStoreMismatch(409, { error: "conflict" }), false);
  assert.equal(isStoreMismatch(401, { error: "store_mismatch" }), false);
  assert.equal(isStoreMismatch(409, null), false);
});

test("api.js sends it on every store request and upload; AuthContext keeps it in step", () => {
  const api = readFileSync(new URL("./api.js", import.meta.url), "utf8");
  const request = api.slice(api.indexOf("async function request("), api.indexOf("async function postJSON("));
  assert.match(request, /storeHeaders\(path\)/);
  assert.match(request, /isStoreMismatch\(/);
  const xhrs = api.split("new XMLHttpRequest()").length - 1;
  assert.ok(xhrs >= 4);
  assert.equal(api.split("setStoreHeadersOn(xhr").length - 1, xhrs);
  const auth = readFileSync(new URL("./AuthContext.jsx", import.meta.url), "utf8");
  assert.match(auth, /setSessionStore\(/);
  assert.match(auth, /auth:store-changed/);
});
