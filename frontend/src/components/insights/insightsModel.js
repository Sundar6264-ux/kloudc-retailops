// Pure helpers for the Insights pages (docs/INSIGHTS_CONTRACT.md). No React,
// no DOM - tested with `node --test src/components/insights/insightsModel.test.mjs`.

export const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
export const DAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

// Categorical slots 1-5 of the dataviz reference palette, in its validated
// order (blue, orange, aqua, yellow, magenta - the order is what keeps
// neighbours apart for colour-blind readers), then a neutral grey for
// "Other". Fixed order, never cycled.
export const SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"];
export const OTHER_COLOR = "#9ca3af";
// Dash patterns so lines stay apart in print and for colour-blind readers.
export const SERIES_DASHES = ["", "6 4", "2 3", "10 3 2 3", "1 4"];

// Sequential blue ramp (light -> dark) for the heatmap.
export const HEAT_RAMP = ["#eef5fd", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"];

const nf0 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });

export function isNum(v) {
  return typeof v === "number" && Number.isFinite(v);
}

// $1,234 (whole dollars) or $1,234.56 with cents. null/undefined -> "-".
export function fmtMoney(v, { cents = false } = {}) {
  if (!isNum(v)) return "-";
  const sign = v < 0 ? "-" : "";
  const abs = Math.abs(v);
  const body = cents
    ? abs.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : nf0.format(Math.round(abs));
  return `${sign}$${body}`;
}

// Short money for chart axes: $950, $1.2k, $12k.
export function fmtMoneyShort(v) {
  if (!isNum(v)) return "-";
  const sign = v < 0 ? "-" : "";
  const abs = Math.abs(v);
  if (abs >= 10000) return `${sign}$${nf0.format(abs / 1000)}k`;
  if (abs >= 1000) return `${sign}$${nf1.format(abs / 1000)}k`;
  return `${sign}$${nf0.format(abs)}`;
}

export function fmtCount(v, digits = 0) {
  if (!isNum(v)) return "-";
  return v.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: digits });
}

// A fraction 0..1 as a percentage: 0.234 -> "23%" (one decimal under 10%).
export function fmtShare(f) {
  if (!isNum(f)) return "-";
  const p = f * 100;
  if (p > 0 && p < 0.1) return "<0.1%";
  if (p !== 0 && Math.abs(p) < 10) return `${nf1.format(p)}%`;
  return `${nf0.format(p)}%`;
}

// A change already in percent (8.24 -> "8.2%"), without a sign.
export function fmtPctAbs(p) {
  if (!isNum(p)) return "-";
  const a = Math.abs(p);
  return `${a < 10 ? nf1.format(a) : nf0.format(a)}%`;
}

// Direction of a percent change. "flat" under half a percent, so 0.3% isn't
// shown as a meaningful rise.
export function delta(p, { flatBelow = 0.5 } = {}) {
  if (!isNum(p)) return { dir: "none", arrow: "", text: "-", words: "no comparison" };
  if (Math.abs(p) < flatBelow) return { dir: "flat", arrow: "=", text: "= 0%", words: "about the same" };
  const up = p > 0;
  const pct = fmtPctAbs(p);
  return {
    dir: up ? "up" : "down",
    arrow: up ? "▲" : "▼",
    text: `${up ? "▲" : "▼"} ${pct}`,
    words: `${up ? "up" : "down"} ${pct}`,
  };
}

// Percent change from `prev` to `cur` (null when there's no base).
export function pctChange(cur, prev) {
  if (!isNum(cur) || !isNum(prev) || prev === 0) return null;
  return ((cur - prev) / Math.abs(prev)) * 100;
}

export function hourLabel(h, { long = false } = {}) {
  if (!isNum(h)) return "";
  const hr = ((h % 24) + 24) % 24;
  const suffix = hr < 12 ? (long ? " AM" : "a") : long ? " PM" : "p";
  const n = hr % 12 === 0 ? 12 : hr % 12;
  return `${n}${suffix}`;
}

export function hourRangeLabel(h) {
  return `${hourLabel(h, { long: true })} - ${hourLabel(h + 1, { long: true })}`;
}

// "2026-10-06" -> "Oct 6" (no timezone shift: parsed as a plain date).
export function shortDate(iso, { weekday = false, year = false } = {}) {
  if (!iso || typeof iso !== "string") return "";
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  if (!y || !m || !d) return iso;
  const date = new Date(Date.UTC(y, m - 1, d));
  const opts = { month: "short", day: "numeric", timeZone: "UTC" };
  if (weekday) opts.weekday = "short";
  if (year) opts.year = "numeric";
  return date.toLocaleDateString("en-US", opts);
}

export function longDate(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  const date = new Date(Date.UTC(y, m - 1, d));
  return date.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric", timeZone: "UTC" });
}

// Monday = 0 ... Sunday = 6 for a "YYYY-MM-DD".
export function weekdayIndex(iso) {
  if (!iso) return null;
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  const js = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
  return (js + 6) % 7;
}

export function addDays(iso, n) {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  const date = new Date(Date.UTC(y, m - 1, d + n));
  return date.toISOString().slice(0, 10);
}

export function plural(n, one, many = `${one}s`) {
  return `${fmtCount(n)} ${n === 1 ? one : many}`;
}

// ---------- heatmap ----------

// Index cells by "dow:hour" and work out which hours to show: the first to
// the last hour with any activity in the chosen metric, so a 24-column grid
// doesn't spend most of its width on hours the store is closed.
export function heatmapModel(data, metric) {
  const cells = Array.isArray(data?.cells) ? data.cells : [];
  const byKey = new Map();
  let max = 0;
  let minHour = null;
  let maxHour = null;
  for (const c of cells) {
    if (!isNum(c?.dow) || !isNum(c?.hour)) continue;
    byKey.set(`${c.dow}:${c.hour}`, c);
    const v = c[metric];
    if (isNum(v) && v > 0) {
      max = Math.max(max, v);
      minHour = minHour == null ? c.hour : Math.min(minHour, c.hour);
      maxHour = maxHour == null ? c.hour : Math.max(maxHour, c.hour);
    }
  }
  const dayCount = Array.isArray(data?.days) && data.days.length ? data.days.length : 7;
  const hours = [];
  if (minHour != null) for (let h = minHour; h <= maxHour; h += 1) hours.push(h);
  return { byKey, max, hours, dayCount, empty: minHour == null };
}

// Ramp step for a value (0 = nothing at all; 1..n-1 scaled to the max).
export function heatStep(value, max, steps = HEAT_RAMP.length) {
  if (!isNum(value) || value <= 0 || !isNum(max) || max <= 0) return 0;
  const f = Math.min(1, value / max);
  return Math.max(1, Math.min(steps - 1, Math.ceil(f * (steps - 1))));
}

// Text on a heat cell: dark ink on the light steps, white on the dark ones.
export function heatInk(step) {
  return step >= 4 ? "#ffffff" : "#111827";
}

export function dayName(data, dow, { short = false } = {}) {
  const fromData = Array.isArray(data?.days) ? data.days[dow] : null;
  if (typeof fromData === "string" && fromData) {
    if (!short) return DAY_NAMES.find((d) => d.startsWith(fromData.slice(0, 3))) || fromData;
    return fromData.slice(0, 3);
  }
  return (short ? DAY_SHORT : DAY_NAMES)[dow] ?? String(dow);
}

// ---------- charts ----------

// Round axis ticks covering [min, max] (always including 0 for bars).
export function niceTicks(min, max, count = 4) {
  if (!isNum(min) || !isNum(max)) return [0, 1];
  if (min === max) {
    if (max === 0) return [0, 1];
    min = Math.min(0, min);
  }
  const span = max - min;
  const raw = span / Math.max(1, count);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / mag;
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
  const start = Math.floor(min / step) * step;
  const end = Math.ceil(max / step) * step;
  const ticks = [];
  for (let v = start; v <= end + step / 2; v += step) ticks.push(Math.round(v * 1e6) / 1e6);
  return ticks;
}

// Every k-th label so x labels don't collide (keeps the last one).
export function labelEvery(n, widthPx, minGapPx = 44) {
  if (n <= 1 || !isNum(widthPx) || widthPx <= 0) return 1;
  const per = widthPx / n;
  return Math.max(1, Math.ceil(minGapPx / per));
}

// Every `every`-th x label, plus the last one - and a regular label less
// than `every` slots before the last is dropped so the two never touch.
export function showXLabel(i, n, every) {
  if (i === n - 1) return true;
  if (i % every !== 0) return false;
  return n - 1 - i >= every;
}

// Stable colour per series key: known keys in the order first seen, so a
// channel keeps its colour on every page and when a filter drops another.
export function colourForIndex(i) {
  return i < SERIES_COLORS.length ? SERIES_COLORS[i] : OTHER_COLOR;
}

// ---------- channels ----------

// Normalise /channels into chart rows. Channel order: the API's `channels`
// list (biggest first) if given, else by total. At most MAX_CHANNELS named
// channels get their own colour; the rest (and Toast's "Other") fold into one
// grey "Other" at the top of the stack - never a generated 6th hue.
export const MAX_CHANNELS = 5;

export function channelsModel(data, { metric = "net_sales", max = MAX_CHANNELS } = {}) {
  const weeks = Array.isArray(data?.weeks) ? data.weeks : [];
  const totals = new Map();
  for (const w of weeks) {
    for (const b of w.by || []) {
      const name = b.channel || "Other";
      totals.set(name, (totals.get(name) || 0) + (isNum(b[metric]) ? b[metric] : 0));
    }
  }
  const given = Array.isArray(data?.channels) && data.channels.length;
  let names = given
    ? data.channels.map((c) => (typeof c === "string" ? c : c?.name ?? c?.channel)).filter(Boolean)
    : [];
  for (const n of totals.keys()) if (!names.includes(n)) names.push(n);
  if (!given) names = names.sort((a, b) => (totals.get(b) || 0) - (totals.get(a) || 0));
  const named = names.filter((n) => n !== "Other").slice(0, max);
  const fold = (name) => (named.includes(name) ? name : "Other");
  const hasOther = names.some((n) => fold(n) === "Other");
  const keys = named.map((name, i) => ({ key: name, label: name, color: colourForIndex(i) }));
  if (hasOther) keys.push({ key: "Other", label: "Other", color: OTHER_COLOR });

  const rows = weeks.map((w) => {
    const values = {};
    let total = 0;
    for (const b of w.by || []) {
      const name = fold(b.channel || "Other");
      const v = isNum(b[metric]) ? b[metric] : 0;
      values[name] = (values[name] || 0) + v;
      total += v;
    }
    const shares = {};
    for (const k of keys) shares[k.key] = total > 0 ? (values[k.key] || 0) / total : 0;
    const partial = !!w.partial;
    const days = isNum(w.days) ? w.days : null;
    return {
      label: shortDate(w.week_start),
      week_start: w.week_start,
      values,
      shares,
      total,
      partial,
      days,
    };
  });
  const folded = new Map();
  for (const [n, v] of totals) folded.set(fold(n), (folded.get(fold(n)) || 0) + v);
  const grand = [...folded.values()].reduce((a, b) => a + b, 0);
  const overall = keys.map((k) => ({
    ...k,
    total: folded.get(k.key) || 0,
    share: grand > 0 ? (folded.get(k.key) || 0) / grand : 0,
    members: k.key === "Other" ? names.filter((n) => fold(n) === "Other") : [k.key],
  }));
  return { keys, rows, overall, empty: rows.length === 0 || grand === 0 };
}

// "Week of Oct 12 (so far, 2 days)" for a week that hasn't ended.
export function weekTitle(row) {
  const base = `Week of ${shortDate(row.week_start, { year: true })}`;
  if (!row.partial) return base;
  return `${base} (so far${isNum(row.days) ? `, ${plural(row.days, "day")}` : ""})`;
}

// ---------- menu / add-ons ----------

export const MENU_TABS = [
  { key: "top", label: "Top" },
  { key: "bottom", label: "Bottom" },
  { key: "rising", label: "Rising" },
  { key: "falling", label: "Falling" },
  { key: "not_sold", label: "Not sold" },
];

// The menu-performance endpoint feeds two pages (owner, Oct 14): Menu trends
// (Rising / Falling / Not sold - lists Toast doesn't have) and, under "Also
// in Toast", Best & worst sellers (Top / Bottom). The open list is `?tab=`.
export const MENU_VIEWS = {
  trends: { path: "/insights/menu-trends", title: "Menu trends", tabs: ["rising", "falling", "not_sold"] },
  sellers: { path: "/insights/best-sellers", title: "Best & worst sellers", tabs: ["top", "bottom"] },
};

// The tabs ({key, label}) one view shows, in order.
export function menuViewTabs(view) {
  const keys = MENU_VIEWS[view]?.tabs || [];
  return MENU_TABS.filter((t) => keys.includes(t.key));
}

// The tab to show for a `?tab=` value: itself when the view has it, else the
// view's first tab.
export function menuTabFor(view, tab) {
  const keys = MENU_VIEWS[view]?.tabs || [];
  return keys.includes(tab) ? tab : keys[0];
}

// Where an old "/insights/menu" link goes, given its query string ("?tab=top"
// or ""). Top / Bottom open Best & worst sellers; anything else (Rising,
// Falling, Not sold, no tab) opens Menu trends, the page kept in the main
// section. Other query parameters are passed on.
export function menuPerformanceRedirect(search = "") {
  const params = new URLSearchParams(search);
  const tab = params.get("tab");
  const view = MENU_VIEWS.sellers.tabs.includes(tab) ? MENU_VIEWS.sellers : MENU_VIEWS.trends;
  if (tab != null && !view.tabs.includes(tab)) params.delete("tab");
  const query = params.toString();
  return `${view.path}${query ? `?${query}` : ""}`;
}

export const MENU_GROUPS = [
  { key: "all", label: "Everything" },
  { key: "kitchen", label: "Street Kitchen" },
  { key: "grocery", label: "Grocery" },
];

// "Last sold" in words relative to a reference date (both "YYYY-MM-DD").
export function lastSoldText(lastSold, today) {
  if (!lastSold) return "Not in this window";
  const a = Date.parse(`${lastSold.slice(0, 10)}T00:00:00Z`);
  const b = Date.parse(`${(today || lastSold).slice(0, 10)}T00:00:00Z`);
  if (!isNum(a) || !isNum(b)) return lastSold;
  const days = Math.round((b - a) / 86400000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  return `${days} days ago`;
}

// ---------- prep forecast ----------

// The basis line, e.g. "Based on the last 8 Saturdays". Uses the server's
// own wording when it sends one.
export function forecastBasis(data) {
  if (typeof data?.basis === "string" && data.basis) return data.basis;
  const n = data?.weeks ?? data?.basis_weeks ?? 8;
  const dow = weekdayIndex(data?.date);
  const day = dow == null ? "weeks" : `${DAY_NAMES[dow]}s`;
  return `Based on the last ${n} ${day}`;
}

export function rangeText(item) {
  if (!item) return "";
  if (isNum(item.low) && isNum(item.high)) {
    if (Math.round(item.low) === Math.round(item.high)) return fmtCount(Math.round(item.low));
    return `${fmtCount(Math.round(item.low))}-${fmtCount(Math.round(item.high))}`;
  }
  return "";
}

// Plain-text prep list (Copy button / sharing).
export function prepForecastText(data) {
  const items = Array.isArray(data?.items) ? data.items : [];
  const lines = [`Prep forecast - ${longDate(data?.date)}`, forecastBasis(data), ""];
  for (const it of items) {
    const range = rangeText(it);
    lines.push(`${it.name}: ${fmtCount(Math.round(it.expected_qty ?? 0))}${range ? ` (range ${range})` : ""}`);
  }
  if (!items.length) lines.push("No kitchen items expected.");
  return lines.join("\n");
}

// Peak hours of one item's hourly split (top `n` by qty).
export function peakHours(byHour, n = 2) {
  if (!Array.isArray(byHour)) return [];
  return [...byHour]
    .filter((b) => isNum(b?.qty) && b.qty > 0)
    .sort((a, b) => b.qty - a.qty || a.hour - b.hour)
    .slice(0, n)
    .map((b) => b.hour)
    .sort((a, b) => a - b);
}

// ---------- odd checks ----------

const TIME_FMT = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit" });
const DAY_FMT = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" });

// A timestamp as store time: "2:06 PM", with the date in front when it
// isn't `onDate` ("YYYY-MM-DD") - e.g. a tab paid the next morning.
export function storeTime(isoTs, onDate, { withDate = false } = {}) {
  if (!isoTs) return "";
  const d = new Date(isoTs);
  if (Number.isNaN(d.getTime())) return "";
  const time = TIME_FMT.format(d);
  const day = DAY_FMT.format(d);
  return withDate || (onDate && day !== onDate) ? `${shortDate(day)}, ${time}` : time;
}

// 45 -> "45 min", 205 -> "3 h 25 min", 3000 -> "2 days".
export function minutesText(m) {
  if (!isNum(m)) return "-";
  const mm = Math.round(m);
  if (mm < 60) return `${mm} min`;
  const h = Math.floor(mm / 60);
  const rest = mm % 60;
  if (h >= 48) return `${fmtCount(Math.round(mm / 1440))} days`;
  return rest ? `${h} h ${rest} min` : `${h} h`;
}

// ---------- states ----------

// Why a page's numbers may be incomplete (null when they aren't). The
// server's `note` says it best - which days, why (Toast's daily limit, the
// 4:30 AM update, Toast couldn't provide them) and when they come - so it
// wins. A server without `note` falls back to `configured`, `filling` and
// `coverage`; "being read" only while a fill really runs.
export function staleText(data) {
  if (!data || !data.stale) return null;
  if (typeof data.note === "string" && data.note.trim()) return data.note.trim();
  if (data.configured === false) {
    return "Toast Analytics isn't set up on this server yet, so only numbers saved earlier can be shown.";
  }
  const cov = data.coverage;
  const partial =
    cov && isNum(cov.dates_expected) && isNum(cov.dates_cached) && cov.dates_cached < cov.dates_expected;
  const saved = partial ? `${fmtCount(cov.dates_cached)} of ${plural(cov.dates_expected, "day")}` : "";
  if (data.filling) return `Some days are still being read from Toast.${partial ? ` ${saved} saved so far.` : ""} Check back in a few minutes.`;
  if (partial) return `${saved} are saved; the rest are added automatically.`;
  return "Toast Analytics didn't answer just now, so this is the last copy we saved. It may be missing the newest numbers.";
}

// What an error means for the person looking at the page.
export function errorKind(err) {
  const status = err?.status;
  if (status === 403) return "forbidden";
  if (status === 404) return "missing";
  if (status === 429) return "busy";
  if (status === 400 || status === 422) return "bad_request";
  if (!status) return "network";
  return "server";
}

// Is there anything to show at all? (A report with every list empty is
// "empty", which looks different from "Toast hasn't answered yet".)
export function hasRows(data, keys) {
  if (!data) return false;
  return keys.some((k) => Array.isArray(data[k]) && data[k].length > 0);
}

// Search a list of objects by a few fields (case-insensitive).
export function filterRows(rows, query, fields) {
  const q = (query || "").trim().toLowerCase();
  if (!q) return rows;
  return rows.filter((r) => fields.some((f) => String(r?.[f] ?? "").toLowerCase().includes(q)));
}
