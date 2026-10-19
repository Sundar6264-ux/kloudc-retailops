// Pure helpers for the top bar's search (CommandPalette.jsx); tested by
// ui/searchModel.test.mjs.

function norm(s) {
  return String(s || "")
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "");
}

// Pages matching `q` (every word somewhere in the label or where it lives),
// best first: label starts with the query, then a word of the label starts
// with it, then anywhere. An empty query lists every page in menu order.
export function rankPages(pages, q) {
  const query = norm(q).trim();
  if (!query) return pages;
  const words = query.split(/\s+/).filter(Boolean);
  const scored = [];
  pages.forEach((p, i) => {
    const label = norm(p.label);
    const hay = `${label} ${norm(p.where)}`;
    if (!words.every((w) => hay.includes(w))) return;
    let score = 1;
    if (label.startsWith(query)) score = 4;
    else if (label.split(/[\s&/-]+/).some((w) => w.startsWith(words[0]))) score = 3;
    else if (label.includes(query)) score = 2;
    scored.push({ p, score, i });
  });
  scored.sort((a, b) => b.score - a.score || a.i - b.i);
  return scored.map((s) => s.p);
}

// Where a help search result opens (track H's addresses: /help?a=<article>,
// /help?tab=faq&s=<id>, /glossary?k=<term>). null for kinds it can't open.
export function helpResultPath(r) {
  if (!r || !r.id) return null;
  const id = encodeURIComponent(r.id);
  if (r.href && String(r.href).startsWith("/")) return r.href;
  switch (r.kind) {
    case "article":
      return `/help?a=${id}`;
    case "faq":
      return `/help?tab=faq&s=${id}`;
    case "trouble":
      return `/help?tab=troubleshooting&s=${id}`;
    case "term":
      return `/glossary?k=${id}`;
    default:
      return null;
  }
}
