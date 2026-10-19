// The Glossary (docs/DESIGN_SYSTEM.md 4.2): every status, label and idea the
// product uses - the status terms from /api/glossary and the concept terms
// from content/help/glossary.json - in one A-Z list with search, area tabs
// (remembered), letter jumps, "Where you see it" links and related terms.
// /glossary?k=<key> opens with that term highlighted.
import { useEffect, useMemo, useState } from "react";
import { useLocation } from "react-router-dom";
import { useAuth } from "../../AuthContext.jsx";
import { useHelp } from "./HelpProvider.jsx";
import { canOpenPageFor } from "./HelpSearch.jsx";
import { HelpIcon } from "./helpIcons.jsx";
import { glossaryPath, hashHref, plainText, sortTerms, termLetter } from "./helpModel.js";

const TAB_KEY = "kc.tab.glossary";

// Area tabs: the product's areas, with the basics and home/data grouped.
export const GLOSSARY_TABS = [
  { key: "all", label: "All", icon: "glossary", area: "help" },
  { key: "basics", label: "Basics", icon: "basics", area: "help", areas: ["help", null, undefined] },
  { key: "home", label: "Home & data", icon: "home", area: "home", areas: ["home"] },
  { key: "ordering", label: "Ordering", icon: "ordering", area: "ordering", areas: ["ordering"] },
  { key: "stock", label: "Stock", icon: "stock", area: "stock", areas: ["stock"] },
  { key: "sales", label: "Sales", icon: "sales", area: "sales", areas: ["sales"] },
  { key: "team", label: "Team", icon: "team", area: "team", areas: ["team"] },
  { key: "setup", label: "Setup", icon: "setup", area: "setup", areas: ["setup"] },
  { key: "platform", label: "Platform", icon: "platform", area: "platform", areas: ["platform"] },
];

export function termInTab(term, tabKey) {
  if (tabKey === "all") return true;
  const tab = GLOSSARY_TABS.find((t) => t.key === tabKey);
  return !!tab && tab.areas.includes(term.area);
}

export function termMatches(term, q) {
  if (!q) return true;
  const hay = [term.label, term.definition, term.rule || "", term.where || "", ...(term.aliases || [])]
    .map((s) => plainText(String(s)).toLowerCase())
    .join(" \u0001 ");
  return q
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((w) => hay.includes(w));
}

function readTab() {
  try {
    const v = window.localStorage.getItem(TAB_KEY);
    return GLOSSARY_TABS.some((t) => t.key === v) ? v : "all";
  } catch {
    return "all";
  }
}

function useAuthOrNull() {
  try {
    return useAuth();
  } catch {
    return null;
  }
}

const TONE_CLASS = {
  success: "kc-pill-success",
  warning: "kc-pill-warning",
  danger: "kc-pill-danger",
  info: "kc-pill-info",
  neutral: "kc-pill-neutral",
  violet: "kc-pill-violet",
};

export default function GlossaryList({ embedded = false }) {
  const help = useHelp();
  const auth = useAuthOrNull();
  const location = useLocation();
  const focusKey = new URLSearchParams(location.search).get("k");
  const [tab, setTab] = useState(() => (focusKey ? "all" : readTab()));
  const [query, setQuery] = useState("");
  const canOpenPage = useMemo(() => canOpenPageFor(auth), [auth]);

  const all = useMemo(() => sortTerms(help.terms()), [help]);
  const categories = useMemo(() => {
    const out = new Map();
    for (const c of help.apiCategories) out.set(c.key, c.label);
    for (const c of help.content.glossaryCategories) if (!out.has(c.key)) out.set(c.key, c.label);
    return out;
  }, [help]);

  const counts = useMemo(() => {
    const c = {};
    for (const t of GLOSSARY_TABS) c[t.key] = all.filter((x) => termInTab(x, t.key)).length;
    return c;
  }, [all]);

  const shown = all.filter((t) => termInTab(t, tab) && termMatches(t, query.trim()));
  const letters = [];
  for (const t of shown) {
    const l = termLetter(t);
    if (!letters.includes(l)) letters.push(l);
  }

  useEffect(() => {
    if (!focusKey) return;
    setTab("all");
    setQuery("");
    const t = setTimeout(() => {
      const el = document.getElementById(`term-${focusKey}`);
      if (el) {
        el.scrollIntoView({ block: "center", behavior: "smooth" });
        el.focus({ preventScroll: true });
      }
    }, 60);
    return () => clearTimeout(t);
  }, [focusKey, help.glossaryLoaded]);

  function chooseTab(key) {
    setTab(key);
    try {
      window.localStorage.setItem(TAB_KEY, key);
    } catch {
      // not remembered
    }
  }

  function jumpTo(letter) {
    const el = document.getElementById(`kc-letter-${letter}`);
    if (el) el.scrollIntoView({ block: "start", behavior: "smooth" });
  }

  return (
    <div className={`kc-glossary${embedded ? " kc-glossary-embedded" : ""}`}>
      <div className="kc-glossary-tools">
        <div className="kc-help-search kc-help-search-sm" role="search">
          <HelpIcon name="search" size={16} />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search terms, e.g. critical, lead time, container"
            aria-label="Search the glossary"
            data-testid="glossary-search"
          />
          {query && (
            <button type="button" className="kc-icon-btn" aria-label="Clear search" onClick={() => setQuery("")}>
              <HelpIcon name="x" size={16} />
            </button>
          )}
        </div>
        <div className="kc-area-tabs" role="tablist" aria-label="Glossary areas">
          {GLOSSARY_TABS.filter((t) => counts[t.key] > 0).map((t) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={tab === t.key}
              className={`kc-area-tab${tab === t.key ? " kc-area-tab-on" : ""}`}
              data-area={t.area}
              onClick={() => chooseTab(t.key)}
            >
              <span className="kc-area-tab-icon" aria-hidden="true">
                <HelpIcon name={t.icon} size={14} />
              </span>
              {t.label}
              <span className="kc-count">{counts[t.key]}</span>
            </button>
          ))}
        </div>
        {letters.length > 1 && (
          <nav className="kc-az" aria-label="Jump to letter">
            {letters.map((l) => (
              <button key={l} type="button" onClick={() => jumpTo(l)} aria-label={`Terms starting with ${l}`}>
                {l}
              </button>
            ))}
          </nav>
        )}
      </div>

      {!help.glossaryLoaded && all.length === 0 && <p className="kc-muted">Loading…</p>}
      {shown.length === 0 && (
        <div className="kc-help-empty" role="status">
          <span className="kc-help-empty-icon" aria-hidden="true">
            <HelpIcon name="glossary" size={22} />
          </span>
          <p className="kc-help-empty-title">No term matches "{query}"</p>
          <p className="kc-help-empty-text">Try another word, or search the whole Help Center.</p>
        </div>
      )}

      <div className="kc-terms">
        {letters.map((letter) => (
          <section key={letter} className="kc-letter" aria-labelledby={`kc-letter-${letter}`}>
            <h2 id={`kc-letter-${letter}`} className="kc-letter-head">
              {letter}
            </h2>
            <dl className="kc-term-list">
              {shown
                .filter((t) => termLetter(t) === letter)
                .map((t) => (
                  <TermEntry
                    key={t.key}
                    term={t}
                    focused={t.key === focusKey}
                    category={categories.get(t.category)}
                    canOpenPage={canOpenPage}
                  />
                ))}
            </dl>
          </section>
        ))}
      </div>
    </div>
  );
}

function TermEntry({ term, focused, category, canOpenPage }) {
  const help = useHelp();
  const pages = (term.pages || []).map((id) => help.page(id)).filter(Boolean);
  const related = (term.related || []).map((k) => help.term(k)).filter(Boolean);
  return (
    <div
      id={`term-${term.key}`}
      className={`kc-term${focused ? " kc-term-focus" : ""}`}
      data-area={term.area || "help"}
      tabIndex={focused ? -1 : undefined}
    >
      <dt className="kc-term-head">
        {term.tone ? <span className={`kc-pill ${TONE_CLASS[term.tone] || ""}`}>{term.label}</span> : <span className="kc-term-label">{term.label}</span>}
        {category && <span className="kc-term-cat">{category}</span>}
      </dt>
      <dd className="kc-term-body">
        <p className="kc-term-def">{term.definition}</p>
        {term.rule && (
          <p className="kc-term-rule">
            <span className="kc-term-rule-label">How it's worked out</span>
            {term.rule}
          </p>
        )}
        {(pages.length > 0 || term.where) && (
          <p className="kc-term-where">
            <span className="kc-term-meta-label">Where you see it</span>
            {pages.length > 0
              ? pages.map((p) =>
                  canOpenPage(p) ? (
                    <a key={p.id} className="kc-term-chip-link" href={hashHref(p.web.route)}>
                      {p.title}
                    </a>
                  ) : (
                    <span key={p.id} className="kc-term-chip-plain">
                      {p.app?.location ? `${p.title} (app)` : p.title}
                    </span>
                  ),
                )
              : term.where}
          </p>
        )}
        {related.length > 0 && (
          <p className="kc-term-related">
            <span className="kc-term-meta-label">Related</span>
            {related.map((r) => (
              <a key={r.key} className="kc-help-link" href={hashHref(glossaryPath(r.key))}>
                {r.label}
              </a>
            ))}
          </p>
        )}
      </dd>
    </div>
  );
}
