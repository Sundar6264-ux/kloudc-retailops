// Unit tests for the Worker logic: node --test test/ (from directory/worker).
// The byte-for-byte comparison with the Python service is in
// backend/tests/test_directory.py (it drives test/parity.mjs).
import assert from "node:assert/strict";
import { test } from "node:test";
import { SlidingWindowLimiter, createDirectory, normalizeCode } from "../src/directory.js";

const BODY = '{"schema":1,"code":"DEMO","name":"StoreOps Demo Market"}';
const DATA = {
  schema: 1,
  rate: { per_minute: 3, per_hour: 100 },
  cache: {
    ok: "public, max-age=300, stale-while-revalidate=3600",
    not_found: "public, max-age=60",
    no_store: "no-store",
    well_known: "public, max-age=3600",
    open_page: "public, max-age=3600",
  },
  errors: {
    invalid_code: '{"error":"invalid_code","message":"bad"}',
    unknown_store: '{"error":"unknown_store","message":"No store with that code."}',
    directory_unavailable: '{"error":"directory_unavailable","message":"down"}',
    not_found: '{"error":"not_found","message":"Not found."}',
    rate_limited_message: "Too many lookups. Try again in {seconds} seconds.",
  },
  aasa: '{"applinks":{"details":[{"appIDs":[],"components":[]}]}}',
  assetlinks: "[]",
  open_page: { html: "<!doctype html><p>open</p>", csp: "default-src 'none'" },
  stores: { DEMO: { body: BODY, etag: '"abc"' } },
};

function get(dir, path, { ip = "203.0.113.1", headers = {}, method = "GET", env = {} } = {}) {
  const req = new Request(`https://directory.storeops.invalid${path}`, {
    method,
    headers: { "CF-Connecting-IP": ip, ...headers },
  });
  return dir.fetch(req, env, {});
}

test("normalizeCode matches the Python rules", () => {
  assert.equal(normalizeCode("  storeops-test-toast "), "STOREOPS_TEST_TOAST");
  assert.equal(normalizeCode("demo"), "DEMO");
  for (const bad of ["ab", "A".repeat(25), "_ABC", "BAD!CODE", "", null, "DE MO"]) {
    assert.equal(normalizeCode(bad), null, String(bad));
  }
});

test("known, unknown, invalid codes and 304", async () => {
  const dir = createDirectory(DATA, { now: () => 0 });
  let r = await get(dir, "/v1/stores/demo", { ip: "1" });
  assert.equal(r.status, 200);
  assert.equal(await r.text(), BODY);
  assert.equal(r.headers.get("etag"), '"abc"');
  assert.equal(r.headers.get("content-type"), "application/json");
  r = await get(dir, "/v1/stores/NOPE_NOPE", { ip: "2" });
  assert.equal(r.status, 404);
  assert.equal(r.headers.get("cache-control"), "public, max-age=60");
  r = await get(dir, "/v1/stores/%E0%A4%A", { ip: "3" });
  assert.equal(r.status, 400);
  r = await get(dir, "/v1/stores/DEMO", { ip: "4", headers: { "If-None-Match": 'W/"abc"' } });
  assert.equal(r.status, 304);
  assert.equal(await r.text(), "");
  r = await get(dir, "/v1/stores/constructor", { ip: "5" });
  assert.equal(r.status, 404, "object prototype keys are not stores");
});

test("rate limit: per minute, Retry-After, refused hits not counted", async () => {
  let t = 100;
  const dir = createDirectory(DATA, { now: () => t });
  for (const p of ["/v1/stores/DEMO", "/v1/stores/NOPE_NOPE", "/v1/stores/x"]) {
    assert.notEqual((await get(dir, p)).status, 429);
  }
  let r = await get(dir, "/v1/stores/DEMO");
  assert.equal(r.status, 429);
  assert.equal(r.headers.get("retry-after"), "60");
  assert.deepEqual(JSON.parse(await r.text()), {
    error: "rate_limited",
    message: "Too many lookups. Try again in 60 seconds.",
    retry_after: 60,
  });
  assert.equal((await get(dir, "/v1/stores/DEMO", { ip: "other" })).status, 200);
  t += 45;
  assert.equal((await get(dir, "/v1/stores/DEMO")).headers.get("retry-after"), "15");
  t += 15;
  assert.equal((await get(dir, "/v1/stores/DEMO")).status, 200);
});

test("rate limit: per hour window", () => {
  let t = 0;
  const lim = new SlidingWindowLimiter(10, 3, () => t);
  for (let i = 0; i < 3; i++) {
    assert.equal(lim.check("a"), null);
    t += 100;
  }
  assert.equal(lim.check("a"), 3300);
  t += 3300;
  assert.equal(lim.check("a"), null);
});

test("limiter keeps at most maxClients", () => {
  const lim = new SlidingWindowLimiter(1, 10, () => 0, 2);
  lim.check("a");
  lim.check("b");
  lim.check("c");
  assert.equal(lim.hits.size, 2);
  assert.equal(lim.hits.has("a"), false);
});

test("Cloudflare rate-limit binding refusal answers 429", async () => {
  const dir = createDirectory(DATA, { now: () => 0 });
  const keys = [];
  const env = { DIRECTORY_RATE_LIMITER: { limit: async ({ key }) => (keys.push(key), { success: false }) } };
  const r = await get(dir, "/v1/stores/DEMO", { ip: "9.9.9.9", env });
  assert.equal(r.status, 429);
  assert.equal(r.headers.get("retry-after"), "60");
  assert.deepEqual(keys, ["9.9.9.9"]);
});

test("other routes: healthz, well-known, open page, 404, HEAD, 405", async () => {
  const dir = createDirectory(DATA, { now: () => 0 });
  assert.equal(await (await get(dir, "/healthz")).text(), '{"ok":true,"stores":1}');
  const aasa = await get(dir, "/.well-known/apple-app-site-association");
  assert.equal(aasa.headers.get("content-type"), "application/json");
  assert.equal(await aasa.text(), DATA.aasa);
  assert.equal(await (await get(dir, "/.well-known/assetlinks.json")).text(), "[]");
  for (const p of ["/s", "/s/", "/s/ANY_CODE"]) {
    const r = await get(dir, p);
    assert.equal(r.status, 200, p);
    assert.equal(r.headers.get("content-security-policy"), "default-src 'none'");
  }
  assert.equal((await get(dir, "/s/a/b")).status, 404);
  assert.equal((await get(dir, "/v1/stores/")).status, 404);
  const head = await get(dir, "/v1/stores/DEMO", { method: "HEAD", ip: "h" });
  assert.equal(head.status, 200);
  assert.equal(await head.text(), "");
  const post = await get(dir, "/v1/stores/DEMO", { method: "POST" });
  assert.equal(post.status, 405);
});
