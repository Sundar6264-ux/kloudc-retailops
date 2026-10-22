// Home stat tiles: the short line under a tile that couldn't load. A POS
// that isn't connected yet (the server's 503 says "... isn't connected
// yet ...") is not a failure to retry, so it says so; anything else stays
// "Couldn't load".
export function tileErrorText(message) {
  return /isn't connected yet/i.test(String(message || "")) ? "Not connected" : "Couldn't load";
}

// Home "Open checks" tile: value, line under it and tone. Nothing fetched
// from the POS yet (last_refreshed_at sent and null: a store that isn't
// connected, or before the first check) is "not checked yet" - not "all
// closed", which would claim something nobody checked.
export function openChecksTile(data, posName) {
  if (!data) return { value: null, sub: null, tone: "neutral" };
  const count = data.count ?? (data.orders || []).length;
  if (!count && "last_refreshed_at" in data && data.last_refreshed_at == null) {
    return { value: "-", sub: "Not checked yet", tone: "neutral" };
  }
  return {
    value: count.toLocaleString(),
    sub: count ? `not yet closed in ${posName}` : "all closed",
    tone: count ? "warn" : "good",
  };
}
