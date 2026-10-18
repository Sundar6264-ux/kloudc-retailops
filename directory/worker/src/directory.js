// StoreOps store directory as a Cloudflare Worker (M2 contract section 4).
//
// All bodies (store records, errors, AASA, assetlinks, the open-in-app page)
// come precomputed from `data.json`, which
// `python -m app.directory.build_static --worker-data` writes from the stores
// file. The Python service (backend/app/directory/main.py) is the reference;
// backend/tests/test_directory.py runs both and compares the responses.
//
// No dependencies; only the Workers runtime APIs (Request, Response, URL),
// which Node 18+ also has, so the same file runs under `node --test`.

const CODE_RE = /^[A-Z0-9][A-Z0-9_]{2,23}$/;
const MINUTE = 60;
const HOUR = 3600;
const AASA_PATH = "/.well-known/apple-app-site-association";
const ASSETLINKS_PATH = "/.well-known/assetlinks.json";

// ASCII whitespace only, like stores.CODE_TRIM (JS trim() and Python's
// strip() disagree outside ASCII).
const CODE_TRIM_RE = /^[ \t\n\r\v\f]+|[ \t\n\r\v\f]+$/g;

export function normalizeCode(raw) {
  if (typeof raw !== "string") return null;
  const code = raw.replace(CODE_TRIM_RE, "").toUpperCase().replace(/-/g, "_");
  return CODE_RE.test(code) ? code : null;
}

// Per-client sliding windows, same rules as app/directory/ratelimit.py:
// every lookup counts, a refused one does not. Per isolate only - Cloudflare
// may run several isolates, so the Workers rate-limit binding (when bound)
// and a Cloudflare rate-limit rule are the real guard (directory/README.md).
export class SlidingWindowLimiter {
  constructor(perMinute, perHour, now, maxClients = 50000) {
    this.perMinute = perMinute;
    this.perHour = perHour;
    this.now = now;
    this.maxClients = maxClients;
    this.hits = new Map();
  }

  static wait(hits, limit, window, now) {
    if (limit <= 0) return 0;
    const recent = hits.filter((t) => t > now - window);
    if (recent.length < limit) return 0;
    return recent[recent.length - limit] + window - now;
  }

  // null = allowed (and counted); otherwise whole seconds to wait.
  check(key) {
    if (this.perMinute <= 0 && this.perHour <= 0) return null;
    const now = this.now();
    let hits = this.hits.get(key);
    if (hits === undefined) {
      hits = [];
      while (this.hits.size >= this.maxClients) {
        this.hits.delete(this.hits.keys().next().value);
      }
    } else {
      this.hits.delete(key); // re-insert below: most recently used last
    }
    this.hits.set(key, hits);
    while (hits.length && hits[0] <= now - HOUR) hits.shift();
    const wait = Math.max(
      SlidingWindowLimiter.wait(hits, this.perMinute, MINUTE, now),
      SlidingWindowLimiter.wait(hits, this.perHour, HOUR, now),
    );
    if (wait > 0) return Math.max(1, Math.ceil(wait));
    hits.push(now);
    return null;
  }
}

function ifNoneMatch(request, etag) {
  const header = request.headers.get("If-None-Match");
  if (!header) return false;
  return header.split(",").some((tag) => {
    let t = tag.trim();
    if (t === "*") return true;
    if (t.startsWith("W/")) t = t.slice(2);
    return t === etag;
  });
}

// On Cloudflare, CF-Connecting-IP is set by the edge and cannot be forged by
// the client (unlike X-Forwarded-For), so the Worker always keys on it.
function clientIp(request) {
  return (request.headers.get("CF-Connecting-IP") || "").trim() || "unknown";
}

export function createDirectory(data, options = {}) {
  const now = options.now || (() => Date.now() / 1000);
  const limiter = new SlidingWindowLimiter(data.rate.per_minute, data.rate.per_hour, now);
  const cache = data.cache;
  const storeCount = Object.keys(data.stores).length;

  function respond(request, status, body, headers) {
    const h = new Headers(headers);
    return new Response(request.method === "HEAD" || status === 304 ? null : body, { status, headers: h });
  }

  function json(request, status, body, cacheControl, extra = {}) {
    return respond(request, status, body, {
      "Content-Type": "application/json",
      "Cache-Control": cacheControl,
      "Access-Control-Allow-Origin": "*",
      "X-Content-Type-Options": "nosniff",
      ...extra,
    });
  }

  function page(request) {
    return respond(request, 200, data.open_page.html, {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": cache.open_page,
      "Content-Security-Policy": data.open_page.csp,
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "no-referrer",
    });
  }

  async function lookup(request, env, rawCode) {
    const ip = clientIp(request);
    // Cloudflare's Workers rate-limit binding (wrangler.toml [[ratelimits]],
    // see wrangler.toml.example) is shared across isolates; optional.
    if (env && env.DIRECTORY_RATE_LIMITER && typeof env.DIRECTORY_RATE_LIMITER.limit === "function") {
      const { success } = await env.DIRECTORY_RATE_LIMITER.limit({ key: ip });
      if (!success) return rateLimited(request, 60);
    }
    const wait = limiter.check(ip);
    if (wait !== null) return rateLimited(request, wait);

    let decoded;
    try {
      decoded = decodeURIComponent(rawCode);
    } catch {
      decoded = null;
    }
    // The service matches routes on the decoded path, so an encoded "/"
    // (%2F) is a path that isn't a lookup there: answer the same.
    if (decoded !== null && decoded.includes("/")) return notFound(request);
    const code = normalizeCode(decoded);
    if (code === null) return json(request, 400, data.errors.invalid_code, cache.no_store);
    const entry = Object.prototype.hasOwnProperty.call(data.stores, code) ? data.stores[code] : null;
    if (entry === null) return json(request, 404, data.errors.unknown_store, cache.not_found);
    if (ifNoneMatch(request, entry.etag)) {
      return respond(request, 304, null, {
        ETag: entry.etag,
        "Cache-Control": cache.ok,
        "Access-Control-Allow-Origin": "*",
      });
    }
    return json(request, 200, entry.body, cache.ok, { ETag: entry.etag });
  }

  function notFound(request) {
    return json(request, 404, data.errors.not_found, cache.no_store);
  }

  function rateLimited(request, seconds) {
    const body = JSON.stringify({
      error: "rate_limited",
      message: data.errors.rate_limited_message.replace("{seconds}", String(seconds)),
      retry_after: seconds,
    });
    return json(request, 429, body, cache.no_store, { "Retry-After": String(seconds) });
  }

  return {
    async fetch(request, env, ctx) {
      if (request.method !== "GET" && request.method !== "HEAD") {
        return new Response(null, { status: 405, headers: { Allow: "GET, HEAD" } });
      }
      const path = new URL(request.url).pathname;
      const m = path.match(/^\/v1\/stores\/([^/]+)$/);
      if (m) return lookup(request, env, m[1]);
      if (path === "/healthz") {
        return json(request, 200, `{"ok":true,"stores":${storeCount}}`, cache.no_store);
      }
      if (path === AASA_PATH) {
        return respond(request, 200, data.aasa, { "Content-Type": "application/json", "Cache-Control": cache.well_known });
      }
      if (path === ASSETLINKS_PATH) {
        return respond(request, 200, data.assetlinks, {
          "Content-Type": "application/json",
          "Cache-Control": cache.well_known,
        });
      }
      if (path === "/s" || path === "/s/") return page(request);
      const s = path.match(/^\/s\/([^/]+)$/);
      if (s) {
        let seg;
        try {
          seg = decodeURIComponent(s[1]);
        } catch {
          seg = "";
        }
        return seg.includes("/") ? notFound(request) : page(request);
      }
      return notFound(request);
    },
  };
}
