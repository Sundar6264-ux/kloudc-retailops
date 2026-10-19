// Help, glossary and page-info logic with no React and no Vite in it, so the
// node tests can use it on the JSON straight from ../content/help.
//
// One content source (docs/DESIGN_SYSTEM.md 5.2): content/help/*.json is read
// by the server (/api/help), this website (build-time import, ui/help/content.js)
// and the app (a bundled copy). Text is plain with two inline marks only:
// **bold** and links [text](page:<id>) / (article:<id>) / (term:<key>) /
// (https://...) / (mailto:...). Placeholders: {pos}, {store}, {store_city},
// {support_email}.

export const AREAS = [
  { key: "home", label: "Home", icon: "home" },
  { key: "ordering", label: "Ordering", icon: "ordering" },
  { key: "stock", label: "Stock", icon: "stock" },
  { key: "sales", label: "Sales", icon: "sales" },
  { key: "team", label: "Team", icon: "team" },
  { key: "setup", label: "Setup", icon: "setup" },
  { key: "platform", label: "Platform", icon: "platform" },
  { key: "help", label: "Help", icon: "help" },
];

// Guide groups in the Help Center (an article's `group`, else its area).
export const GUIDE_GROUPS = [
  { key: "basics", label: "Basics", icon: "basics", area: "help" },
  { key: "home", label: "Home", icon: "home", area: "home" },
  { key: "ordering", label: "Ordering", icon: "ordering", area: "ordering" },
  { key: "stock", label: "Stock", icon: "stock", area: "stock" },
  { key: "sales", label: "Sales", icon: "sales", area: "sales" },
  { key: "team", label: "Team", icon: "team", area: "team" },
  { key: "setup", label: "Setup", icon: "setup", area: "setup" },
  { key: "account", label: "Your account", icon: "account", area: "help" },
  { key: "master", label: "KloudC master", icon: "platform", area: "platform" },
];

export const HELP_TABS = [
  { key: "start", label: "Start here", icon: "start", hint: "Step by step" },
  { key: "guides", label: "Guides", icon: "guides", hint: "Every feature" },
  { key: "faq", label: "FAQ", icon: "faq", hint: "Quick answers" },
  { key: "troubleshooting", label: "Troubleshooting", icon: "fix", hint: "When something's wrong" },
  { key: "glossary", label: "Glossary", icon: "glossary", hint: "What words mean" },
  { key: "contact", label: "Contact", icon: "contact", hint: "Email support" },
];

export const AUDIENCES = [
  { key: "owner", label: "Owner or admin" },
  { key: "staff", label: "Staff" },
  { key: "master", label: "KloudC master" },
];

export const ALLOWED_PLACEHOLDERS = ["{pos}", "{store}", "{store_city}", "{support_email}"];

export const LIMITS = { summary: 160, point: 120, points: 5 };

// ------------------------------------------------------------------ words

// The words a store fills in: its POS, its name, its city, the support email.
export function storeWords({ posName, storeName, storeCity, supportEmail } = {}) {
  return {
    "{pos}": posName || "the POS",
    "{store}": storeName || "your store",
    "{store_city}": storeCity || "your store's",
    "{support_email}": supportEmail || "support@kloudc.com",
  };
}

export function fillWords(text, words) {
  if (typeof text !== "string" || !words) return text;
  let out = text;
  for (const [k, v] of Object.entries(words)) out = out.split(k).join(v);
  return out;
}

// A deep copy of any JSON value with every string's placeholders filled.
export function fillDeep(value, words) {
  if (typeof value === "string") return fillWords(value, words);
  if (Array.isArray(value)) return value.map((v) => fillDeep(v, words));
  if (value && typeof value === "object") {
    const out = {};
    for (const [k, v] of Object.entries(value)) out[k] = fillDeep(v, words);
    return out;
  }
  return value;
}

// ------------------------------------------------------------------ inline marks

const INLINE_RE = /\*\*([^*]+)\*\*|\[([^\]]+)\]\(([^)\s]+)\)/g;

// "Open **Users** or [the guide](article:setup-users)" ->
// [{type:"text"}, {type:"bold"}, {type:"link", kind, target}].
export function parseInline(text) {
  const out = [];
  if (typeof text !== "string" || !text) return out;
  let last = 0;
  for (const m of text.matchAll(INLINE_RE)) {
    if (m.index > last) out.push({ type: "text", text: text.slice(last, m.index) });
    if (m[1] !== undefined) out.push({ type: "bold", text: m[1] });
    else out.push({ type: "link", text: m[2], ...parseTarget(m[3]) });
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push({ type: "text", text: text.slice(last) });
  return out;
}

export function parseTarget(raw) {
  const t = String(raw || "");
  for (const kind of ["page", "article", "term"]) {
    if (t.startsWith(`${kind}:`)) return { kind, target: t.slice(kind.length + 1) };
  }
  if (/^https:\/\//.test(t)) return { kind: "url", target: t };
  if (/^mailto:/.test(t)) return { kind: "mailto", target: t };
  return { kind: "invalid", target: t };
}

// Inline marks removed: for search, titles and plain previews.
export function plainText(text) {
  return parseInline(text)
    .map((p) => p.text)
    .join("");
}

// ------------------------------------------------------------------ links (HashRouter)

// Every help address the website uses, in one place. The Help Center lives
// at /help (tabs and articles as query parameters, so the one /help route
// serves them all), the glossary at /glossary?k=<key>.
export function helpPath({ tab, article, anchor, q } = {}) {
  const params = new URLSearchParams();
  if (article) params.set("a", article);
  else if (tab) params.set("tab", tab);
  if (anchor) params.set("s", anchor);
  if (q) params.set("q", q);
  const qs = params.toString();
  return qs ? `/help?${qs}` : "/help";
}

// The old Help page's section ids (<Link to="/help" state={{ section }}>)
// -> the guide that replaced each one, and a heading in it.
export const LEGACY_SECTIONS = {
  overview: { article: "home-today" },
  menu: { article: "basics-navigation" },
  invoices: { article: "invoices" },
  "open-orders": { article: "open-checks" },
  "items-sold": { article: "items-sold" },
  insights: { article: "insights" },
  reorder: { article: "reorder" },
  "supplier-projection": { article: "new-order" },
  "placed-orders": { article: "placed-orders" },
  "vendor-calendar": { article: "vendor-calendar" },
  "fix-issues": { article: "fix-issues" },
  "inventory-reports": { article: "inventory-reports" },
  reconciliation: { article: "reconciliation", anchor: "worked-example" },
  downloads: { article: "basics-downloads" },
  categories: { article: "setup-po-sections" },
  "daily-checklists": { article: "daily-checklists" },
  team: { article: "team-tasks" },
  flyers: { article: "flyers" },
  ask: { article: "ask-bot" },
  notifications: { article: "account-alerts" },
  users: { article: "setup-users" },
  integrations: { article: "setup-integrations" },
  "inventory-api": { article: "setup-inventory-from-pos" },
  "live-data": { article: "basics-data-freshness" },
  limitations: { article: "basics-limits" },
};

export function glossaryPath(key) {
  return key ? `/glossary?k=${encodeURIComponent(key)}` : "/glossary";
}

// "#/help?a=reorder" - a plain href that works with or without a router.
export function hashHref(path) {
  return `#${path}`;
}

// Where a link target goes: a router path (store pages, help) or an
// external URL.
export function targetHref(kind, target, { pages } = {}) {
  switch (kind) {
    case "page": {
      const route = pages?.get?.(target)?.web?.route;
      return route ? { path: route } : { path: helpPath({ q: target }) };
    }
    case "article":
      return { path: helpPath({ article: target }) };
    case "term":
      return { path: glossaryPath(target) };
    case "url":
    case "mailto":
      return { external: target };
    default:
      return null;
  }
}

// ------------------------------------------------------------------ content

// The raw JSON files -> lookups. `raw` = { manifest, pages, articles (array),
// gettingStarted, faq, troubleshooting, glossary, glossaryCategories, contact }.
export function buildHelp(raw) {
  const pages = new Map((raw.pages || []).map((p) => [p.id, p]));
  const articles = new Map((raw.articles || []).map((a) => [a.id, a]));
  const concepts = new Map((raw.glossary || []).map((t) => [t.key, t]));
  // Route -> page id. A signed-in page wins over a logged-out one at the
  // same address ("/" is Home once signed in, Sign in before).
  const routes = new Map();
  const isPublic = new Map((raw.pages || []).map((p) => [p.id, !!p.public]));
  for (const p of raw.pages || []) {
    const route = p.web?.route;
    if (!route) continue;
    const have = routes.get(route);
    if (!have || (isPublic.get(have) && !p.public)) routes.set(route, p.id);
  }
  return {
    manifest: raw.manifest || {},
    pages,
    pageList: raw.pages || [],
    articles,
    articleList: raw.articles || [],
    gettingStarted: raw.gettingStarted || {},
    faq: raw.faq || [],
    troubleshooting: raw.troubleshooting || [],
    concepts,
    conceptList: raw.glossary || [],
    glossaryCategories: raw.glossaryCategories || [],
    contact: raw.contact || {},
    routes,
  };
}

// The page id for a route ("/insights/daily" -> "insights_daily"). Exact
// match first, then the longest route the path starts with.
export function pageIdForPath(help, pathname) {
  if (!help || !pathname) return null;
  if (help.routes.has(pathname)) return help.routes.get(pathname);
  let best = null;
  for (const [route, id] of help.routes) {
    if (route !== "/" && pathname.startsWith(`${route}/`) && (!best || route.length > best.length)) best = route;
  }
  return best ? help.routes.get(best) : null;
}

export function areaLabel(key) {
  return AREAS.find((a) => a.key === key)?.label || "";
}

// "Admins only" / "Needs Sales insights access" ... for the ⓘ panel.
const FEATURE_LABELS = {
  overview: "Home",
  open_orders: "Open Checks",
  items_sold: "Items Sold",
  reorder_candidates: "Reorder Candidates",
  supplier_projection: "Supplier Projection",
  vendor_calendar: "Vendor Calendar",
  purchase_order_cart: "Purchase Order Cart",
  delivery_review: "Delivery Review",
  inventory_reports: "Inventory Reports",
  reconciliation: "Reconciliation",
  category_edits: "Edit PO sections",
  invoice_submission: "Invoice Submission",
  daily_checklists: "Daily Checklists",
  ask_bot: "Ask Inventory Bot",
  team_tasks: "Team Tasks",
  team_chat: "Team Chat",
  marketing_flyers: "Marketing flyers",
  sales_insights: "Sales insights",
  help: "Help",
};

export function whoCanSee(page) {
  if (!page) return null;
  if (page.public) return "Anyone, no sign-in needed.";
  if ((page.audience || []).length === 1 && page.audience[0] === "master") return "KloudC master logins only.";
  const g = page.gating || {};
  const parts = [];
  if (g.admin) parts.push("Admins only");
  if (g.feature) parts.push(`${g.admin ? "with" : "Needs"} the **${FEATURE_LABELS[g.feature] || g.feature}** access`);
  if (g.salesOnly) parts.push("hidden from users with Hide sales numbers");
  if (parts.length === 0) return "Everyone at the store.";
  const text = parts.join(", ");
  return `${text.charAt(0).toUpperCase()}${text.slice(1)}.`;
}

// ------------------------------------------------------------------ who may open a page

// Can this signed-in user open this page on the website? The same rules as
// featureRoutes.js routeVisible, from the registry's gating. `auth` is the
// AuthContext value ({ user, hasFeature, canSeeSales }) or null.
export function canOpenPageFor(auth) {
  const user = auth?.user || null;
  const platform = !!user && user.scope === "platform";
  return (page) => {
    if (!page?.web?.route) return false;
    if (page.id === "no_access" || page.id === "not_available") return false;
    const masterOnly = (page.audience || []).length === 1 && page.audience[0] === "master";
    if (platform) return masterOnly || page.id === "help" || page.id === "glossary";
    if (masterOnly) return false;
    if (page.public) return !user;
    if (!user) return false;
    const g = page.gating || {};
    if (g.admin && !user.is_admin) return false;
    if (g.feature && auth?.hasFeature && !auth.hasFeature(g.feature)) return false;
    if (g.salesOnly && auth && auth.canSeeSales === false) return false;
    return true;
  };
}

// The signed-in user's audience ("owner" for owners and admins).
export function audienceFor(auth) {
  const user = auth?.user;
  if (!user) return null;
  if (user.scope === "platform") return "master";
  return user.is_admin ? "owner" : "staff";
}

// ------------------------------------------------------------------ articles

export function slug(text) {
  return plainText(text)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

// The "On this page" list: every "h" block, with its anchor id.
export function articleOutline(article) {
  return (article?.blocks || []).filter((b) => b.type === "h").map((b) => ({ id: b.id || slug(b.text), text: b.text }));
}

export function articleGroup(article) {
  return article?.group || article?.area || "basics";
}

// ------------------------------------------------------------------ search

function norm(s) {
  return plainText(String(s || ""))
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "");
}

function blockText(blocks) {
  return (blocks || [])
    .map((b) => [b.text, ...(b.items || [])].filter(Boolean).join(" "))
    .join(" ");
}

function scoreFields(words, fields) {
  // fields: [[text, weight]]. Every word must match somewhere.
  let total = 0;
  for (const w of words) {
    let best = 0;
    for (const [text, weight] of fields) {
      if (!text) continue;
      const i = text.indexOf(w);
      if (i < 0) continue;
      const atWord = i === 0 || /[^a-z0-9]/.test(text[i - 1]);
      best = Math.max(best, weight * (atWord ? 2 : 1) + (text.startsWith(w) ? weight : 0));
    }
    if (best === 0) return 0;
    total += best;
  }
  return total;
}

// One search over pages, guides, FAQ, troubleshooting and glossary terms.
// `terms`: the merged glossary list (status terms from /api/glossary plus
// concept terms). `canOpenPage(page)` hides pages this user can't open;
// `audience` ranks answers written for this user first.
export function searchHelp(help, query, { terms = [], canOpenPage = () => true, audience = null, limit = 40 } = {}) {
  const q = norm(query).trim();
  if (!q || !help) return [];
  const words = q.split(/\s+/).filter(Boolean);
  const out = [];
  const forMe = (aud) => (audience && (aud || []).includes(audience) ? 1.15 : 1);
  for (const p of help.pageList) {
    if (!canOpenPage(p)) continue;
    const s = scoreFields(words, [
      [norm(p.title), 10],
      [norm(p.subtitle), 6],
      [norm(p.info?.summary), 3],
      [norm((p.info?.points || []).join(" ")), 1],
    ]);
    if (s > 0) out.push({ kind: "page", id: p.id, title: p.title, where: p.app?.location || areaLabel(p.area), area: p.area, score: s * 1.2 });
  }
  for (const a of help.articleList) {
    const s = scoreFields(words, [
      [norm(a.title), 9],
      [norm(a.summary), 3],
      [norm(blockText(a.blocks)), 1],
    ]);
    if (s > 0) out.push({ kind: "article", id: a.id, title: a.title, where: "Guide", area: a.area, score: s * forMe(a.audience) });
  }
  for (const f of help.faq) {
    const s = scoreFields(words, [
      [norm(f.q), 8],
      [norm(blockText(f.a)), 1],
    ]);
    if (s > 0) out.push({ kind: "faq", id: f.id, title: f.q, where: "FAQ", area: f.area, score: s * forMe(f.audience) });
  }
  for (const t of help.troubleshooting) {
    const s = scoreFields(words, [
      [norm(t.symptom), 8],
      [norm((t.causes || []).join(" ")), 2],
      [norm(blockText(t.fixes)), 1],
    ]);
    if (s > 0) out.push({ kind: "trouble", id: t.id, title: t.symptom, where: "Troubleshooting", area: t.area, score: s * forMe(t.audience) });
  }
  for (const t of terms) {
    const s = scoreFields(words, [
      [norm(t.label), 9],
      [norm((t.aliases || []).join(" ")), 6],
      [norm(t.definition), 1],
    ]);
    if (s > 0) out.push({ kind: "term", id: t.key, title: t.label, where: "Glossary", area: t.area, score: s * 0.9 });
  }
  out.sort((a, b) => b.score - a.score || a.title.localeCompare(b.title));
  return out.slice(0, limit);
}

// ------------------------------------------------------------------ glossary

// Status terms (from /api/glossary) and concept terms (bundled) as one list,
// by key; the server's copy wins (it already merges the concepts).
export function mergeTerms(apiTerms, concepts, words) {
  const byKey = new Map();
  for (const t of concepts || []) byKey.set(t.key, fillDeep(t, words));
  for (const t of apiTerms || []) byKey.set(t.key, { ...byKey.get(t.key), ...t });
  return [...byKey.values()];
}

export function sortTerms(list) {
  return [...list].sort((a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: "base" }));
}

export function termLetter(term) {
  const c = plainText(term.label || "").trim().charAt(0).toUpperCase();
  return /[A-Z]/.test(c) ? c : "#";
}

// ------------------------------------------------------------------ support email

// mailto: with a subject and a body template. Never the password; the user
// sends it from their own mail app, nothing is sent automatically.
export function supportMailto({ email, storeCode, page, appVersion, topic } = {}) {
  const to = email || "support@kloudc.com";
  const subject = `RetailOps support${storeCode ? ` - ${storeCode}` : ""}${topic ? ` - ${topic}` : ""}`;
  const lines = [
    "Hello KloudC support,",
    "",
    `Store ID: ${storeCode || ""}`,
    `Page: ${page || ""}`,
    ...(appVersion ? [`App version: ${appVersion}`] : []),
    "",
    "What I did:",
    "",
    "What I expected:",
    "",
    "What happened instead:",
    "",
    "(Attach a screenshot if you can. Never send your password.)",
  ];
  return `mailto:${to}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(lines.join("\n"))}`;
}

// ------------------------------------------------------------------ validation (tests, dev)

const DASHES = /[–—]/;
const PLACEHOLDER_RE = /\{[a-z_]+\}/g;

function eachString(value, path, fn) {
  if (typeof value === "string") fn(value, path);
  else if (Array.isArray(value)) value.forEach((v, i) => eachString(v, `${path}[${i}]`, fn));
  else if (value && typeof value === "object") {
    for (const [k, v] of Object.entries(value)) eachString(v, `${path}.${k}`, fn);
  }
}

// Every problem with the content, as readable strings ([] = fine).
// `glossaryKeys`: every key the merged glossary will have (status + concept).
export function validateHelp(help, { glossaryKeys } = {}) {
  const errors = [];
  const termKeys = new Set(glossaryKeys || [...help.concepts.keys()]);
  const check = (cond, msg) => {
    if (!cond) errors.push(msg);
  };
  const checkLinks = (text, where) => {
    for (const part of parseInline(text)) {
      if (part.type !== "link") continue;
      if (part.kind === "page") check(help.pages.has(part.target), `${where}: unknown page link ${part.target}`);
      else if (part.kind === "article") check(help.articles.has(part.target), `${where}: unknown article link ${part.target}`);
      else if (part.kind === "term") check(termKeys.has(part.target), `${where}: unknown term link ${part.target}`);
      else check(part.kind !== "invalid", `${where}: bad link target ${part.target}`);
    }
  };
  const all = {
    pages: help.pageList,
    articles: help.articleList,
    gettingStarted: help.gettingStarted,
    faq: help.faq,
    troubleshooting: help.troubleshooting,
    glossary: help.conceptList,
    contact: help.contact,
  };
  eachString(all, "help", (s, path) => {
    check(!DASHES.test(s), `${path}: en/em dash`);
    for (const ph of s.match(PLACEHOLDER_RE) || []) check(ALLOWED_PLACEHOLDERS.includes(ph), `${path}: unknown placeholder ${ph}`);
    checkLinks(s, path);
  });
  const areas = new Set(AREAS.map((a) => a.key));
  const seenIds = new Set();
  for (const p of help.pageList) {
    check(!seenIds.has(p.id), `duplicate page id ${p.id}`);
    seenIds.add(p.id);
    check(p.area === null || areas.has(p.area), `page ${p.id}: unknown area ${p.area}`);
    check(!!p.title, `page ${p.id}: no title`);
    const info = p.info || {};
    check(!!info.summary && plainText(info.summary).length <= LIMITS.summary, `page ${p.id}: summary missing or over ${LIMITS.summary}`);
    check((info.points || []).length <= LIMITS.points, `page ${p.id}: more than ${LIMITS.points} points`);
    for (const pt of info.points || []) check(plainText(pt).length <= LIMITS.point, `page ${p.id}: point over ${LIMITS.point}: ${pt}`);
    for (const [sid, sec] of Object.entries(p.sections || {})) {
      check(!!sec.title && !!sec.summary, `page ${p.id} section ${sid}: title and summary needed`);
      check(plainText(sec.summary || "").length <= LIMITS.summary, `page ${p.id} section ${sid}: summary over ${LIMITS.summary}`);
      check((sec.points || []).length <= LIMITS.points, `page ${p.id} section ${sid}: too many points`);
      for (const pt of sec.points || []) check(plainText(pt).length <= LIMITS.point, `page ${p.id} section ${sid}: point too long`);
    }
    for (const k of p.terms || []) check(termKeys.has(k), `page ${p.id}: unknown term ${k}`);
    if (p.article) check(help.articles.has(p.article), `page ${p.id}: unknown article ${p.article}`);
  }
  const seenArticles = new Set();
  for (const a of help.articleList) {
    check(!seenArticles.has(a.id), `duplicate article id ${a.id}`);
    seenArticles.add(a.id);
    check(!!a.title && !!a.summary && (a.blocks || []).length > 0, `article ${a.id}: title, summary and blocks needed`);
    check(GUIDE_GROUPS.some((g) => g.key === articleGroup(a)), `article ${a.id}: unknown group ${articleGroup(a)}`);
    for (const id of a.pages || []) check(help.pages.has(id), `article ${a.id}: unknown page ${id}`);
    for (const id of a.related || []) check(help.articles.has(id), `article ${a.id}: unknown related ${id}`);
    for (const b of a.blocks || []) {
      check(["h", "p", "steps", "bullets", "tip", "note", "warning", "example"].includes(b.type), `article ${a.id}: unknown block ${b.type}`);
    }
  }
  for (const [track, t] of Object.entries(help.gettingStarted || {})) {
    for (const [i, step] of (t.steps || []).entries()) {
      if (step.page) check(help.pages.has(step.page), `getting-started ${track} step ${i + 1}: unknown page ${step.page}`);
      if (step.article) check(help.articles.has(step.article), `getting-started ${track} step ${i + 1}: unknown article ${step.article}`);
    }
  }
  const ids = new Set();
  for (const f of help.faq) {
    check(!ids.has(f.id), `duplicate faq id ${f.id}`);
    ids.add(f.id);
    for (const id of f.pages || []) check(help.pages.has(id), `faq ${f.id}: unknown page ${id}`);
  }
  for (const t of help.troubleshooting) {
    check(!ids.has(t.id), `duplicate troubleshooting id ${t.id}`);
    ids.add(t.id);
    for (const id of t.pages || []) check(help.pages.has(id), `troubleshooting ${t.id}: unknown page ${id}`);
  }
  const cats = new Set(help.glossaryCategories.map((c) => c.key));
  for (const t of help.conceptList) {
    check(t.key.startsWith("concept_"), `concept term ${t.key}: key must start with concept_`);
    check(cats.has(t.category), `concept term ${t.key}: unknown category ${t.category}`);
    check(!!t.label && !!t.definition, `concept term ${t.key}: label and definition needed`);
    for (const id of t.pages || []) check(help.pages.has(id), `concept term ${t.key}: unknown page ${id}`);
    for (const k of t.related || []) check(termKeys.has(k), `concept term ${t.key}: unknown related ${k}`);
  }
  return errors;
}
