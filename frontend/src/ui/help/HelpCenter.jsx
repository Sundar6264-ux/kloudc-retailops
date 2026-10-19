// The Help Center (docs/DESIGN_SYSTEM.md 4.1): one screen with a search box,
// audience chips and tile tabs - Start here, Guides, FAQ, Troubleshooting,
// Glossary, Contact - remembered on this browser. A guide opens in place
// (/help?a=<id>, with ?s=<heading> to jump inside it).
//
//   /help                      last tab (Start here the first time)
//   /help?tab=faq&s=<faq id>   a tab, opened at one entry
//   /help?a=reorder&s=lookback a guide, at a heading
//   /help?q=lead time          a search
//   /glossary?k=<key>          the Glossary tab (HelpPage tab="glossary")
import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../../AuthContext.jsx";
import { ReconciliationWorkedExample } from "../../components/Reconciliation.jsx";
import { useHelp } from "./HelpProvider.jsx";
import { HelpBlocks, HelpText } from "./HelpText.jsx";
import { HelpSearchResults, audienceFor, canOpenPageFor } from "./HelpSearch.jsx";
import GlossaryList from "./GlossaryList.jsx";
import InfoButton from "./InfoButton.jsx";
import { HelpIcon } from "./helpIcons.jsx";
import {
  AUDIENCES,
  GUIDE_GROUPS,
  HELP_TABS,
  LEGACY_SECTIONS,
  areaLabel,
  articleGroup,
  articleOutline,
  glossaryPath,
  hashHref,
  helpPath,
  supportMailto,
} from "./helpModel.js";

const TAB_KEY = "kc.tab.help";
const AUDIENCE_KEY = "kc.help.audience";

function readStored(key, fallback) {
  try {
    return window.localStorage.getItem(key) || fallback;
  } catch {
    return fallback;
  }
}

function writeStored(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // private window or blocked storage: not remembered
  }
}

function useAuthOrNull() {
  try {
    return useAuth();
  } catch {
    return null;
  }
}

// Which audiences a chip shows ("owner" covers owners and admins).
export function audienceMatches(chip, list) {
  if (chip === "all") return true;
  const aud = list || [];
  if (chip === "owner") return aud.includes("owner") || aud.includes("admin");
  return aud.includes(chip);
}

export default function HelpCenter({ tab: forcedTab }) {
  const help = useHelp();
  const auth = useAuthOrNull();
  const location = useLocation();
  const navigate = useNavigate();
  const params = new URLSearchParams(location.search);
  // Old links pass the old Help page's section in the router state.
  const legacy = LEGACY_SECTIONS[location.state?.section] || null;
  const articleId = params.get("a") || legacy?.article || null;
  const anchor = params.get("s") || legacy?.anchor || null;
  const urlTab = forcedTab || params.get("tab");
  const isPlatform = auth?.user?.scope === "platform";
  const canHelp = isPlatform || !auth?.hasFeature || auth.hasFeature("help");

  const [tab, setTab] = useState(() => {
    const t = urlTab || readStored(TAB_KEY, "start");
    return HELP_TABS.some((x) => x.key === t) ? t : "start";
  });
  useEffect(() => {
    if (urlTab && HELP_TABS.some((x) => x.key === urlTab)) setTab(urlTab);
  }, [urlTab]);

  const myAudience = audienceFor(auth) || "owner";
  const [audience, setAudience] = useState(() => (isPlatform ? "master" : readStored(AUDIENCE_KEY, myAudience)));
  const [query, setQuery] = useState(params.get("q") || "");
  const searchRef = useRef(null);
  const canOpenPage = useMemo(() => canOpenPageFor(auth), [auth]);

  function chooseTab(key) {
    setTab(key);
    writeStored(TAB_KEY, key);
    setQuery("");
    navigate(key === "glossary" ? glossaryPath() : helpPath({ tab: key }));
  }

  function chooseAudience(key) {
    setAudience(key);
    writeStored(AUDIENCE_KEY, key);
  }

  // Without the Help page (a login not given it), /glossary is the whole page.
  if (!canHelp) {
    return (
      <div className="kc-help" data-area="help">
        <header className="kc-help-hero kc-help-hero-slim">
          <h1 className="kc-help-title">Glossary</h1>
          <p className="kc-help-lead">What every word, status and label in RetailOps means.</p>
        </header>
        <GlossaryList />
      </div>
    );
  }

  if (articleId) {
    return <HelpArticle id={articleId} anchor={anchor} canOpenPage={canOpenPage} />;
  }

  const contact = help.contact();
  const searching = query.trim().length > 0;
  return (
    <div className="kc-help" data-area="help">
      <header className="kc-help-hero">
        <p className="kc-eyebrow">
          <HelpIcon name="help" size={14} /> KloudC RetailOps
        </p>
        <div className="kc-help-title-row">
          <h1 className="kc-help-title">Help Center</h1>
          <InfoButton pageId="help" />
        </div>
        <p className="kc-help-lead">Guides, quick answers and fixes for every part of RetailOps.</p>
        <div className="kc-help-search" role="search">
          <HelpIcon name="search" size={18} />
          <input
            ref={searchRef}
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search pages, guides, answers and terms"
            aria-label="Search help"
            data-testid="help-search"
          />
          {query && (
            <button type="button" className="kc-icon-btn" aria-label="Clear search" onClick={() => setQuery("")}>
              <HelpIcon name="x" size={16} />
            </button>
          )}
        </div>
        <div className="kc-chip-row" role="group" aria-label="Show help for">
          <span className="kc-chip-label">For</span>
          {[...AUDIENCES, { key: "all", label: "Everyone" }].map((a) => (
            <button
              key={a.key}
              type="button"
              className={`kc-chip${audience === a.key ? " kc-chip-on" : ""}`}
              aria-pressed={audience === a.key}
              onClick={() => chooseAudience(a.key)}
            >
              {a.label}
            </button>
          ))}
        </div>
      </header>

      {searching ? (
        <section className="kc-help-section" aria-label="Search results">
          <HelpSearchResults query={query} />
        </section>
      ) : (
        <>
          <div className="kc-tiletabs" role="tablist" aria-label="Help sections">
            {HELP_TABS.map((t, i) => (
              <button
                key={t.key}
                type="button"
                role="tab"
                id={`help-tab-${t.key}`}
                aria-selected={tab === t.key}
                aria-controls={`help-panel-${t.key}`}
                tabIndex={tab === t.key ? 0 : -1}
                className={`kc-tiletab${tab === t.key ? " kc-tiletab-on" : ""}`}
                data-testid={`help-tab-${t.key}`}
                onClick={() => chooseTab(t.key)}
                onKeyDown={(e) => {
                  const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
                  if (!step) return;
                  e.preventDefault();
                  const nextKey = HELP_TABS[(i + step + HELP_TABS.length) % HELP_TABS.length].key;
                  chooseTab(nextKey);
                  requestAnimationFrame(() => document.getElementById(`help-tab-${nextKey}`)?.focus());
                }}
              >
                <span className="kc-tiletab-icon" aria-hidden="true">
                  <HelpIcon name={t.icon} size={18} />
                </span>
                <span className="kc-tiletab-text">
                  <span className="kc-tiletab-label">{t.label}</span>
                  <span className="kc-tiletab-hint">{t.hint}</span>
                </span>
              </button>
            ))}
          </div>
          <section className="kc-help-section" role="tabpanel" id={`help-panel-${tab}`} aria-labelledby={`help-tab-${tab}`}>
            {tab === "start" && <StartHere audience={audience} canOpenPage={canOpenPage} />}
            {tab === "guides" && <Guides audience={audience} />}
            {tab === "faq" && <Faq audience={audience} focusId={anchor} />}
            {tab === "troubleshooting" && <Troubleshooting audience={audience} focusId={anchor} />}
            {tab === "glossary" && <GlossaryList embedded />}
            {tab === "contact" && <Contact />}
          </section>
        </>
      )}
      <p className="kc-help-foot">
        {contact.product} by {contact.company} · {contact.location} ·{" "}
        <a className="kc-help-link" href={contact.privacy_url} target="_blank" rel="noopener noreferrer">
          Privacy
        </a>
        {contact.terms_url && (
          <>
            {" · "}
            <a className="kc-help-link" href={contact.terms_url} target="_blank" rel="noopener noreferrer">
              Terms
            </a>
          </>
        )}
      </p>
    </div>
  );
}

// ------------------------------------------------------------------ Start here

function trackFor(audience) {
  if (audience === "staff") return "staff";
  if (audience === "master") return "master";
  return "owner";
}

function readDone(key) {
  try {
    return new Set(JSON.parse(window.localStorage.getItem(key) || "[]"));
  } catch {
    return new Set();
  }
}

function useDone(track) {
  const key = `kc.help.start.${track}`;
  const [done, setDone] = useState(() => readDone(key));
  useEffect(() => setDone(readDone(key)), [key]);
  function toggle(i) {
    setDone((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      try {
        window.localStorage.setItem(key, JSON.stringify([...next]));
      } catch {
        // not remembered
      }
      return next;
    });
  }
  return [done, toggle];
}

function StartHere({ audience, canOpenPage }) {
  const help = useHelp();
  const tracks = help.gettingStarted();
  const key = trackFor(audience);
  const track = tracks[key];
  const [done, toggle] = useDone(key);
  if (!track) return null;
  const total = track.steps.length;
  const count = track.steps.filter((_, i) => done.has(i)).length;
  const pct = Math.round((count / total) * 100);
  return (
    <div className="kc-start">
      <div className="kc-card kc-start-card">
        <div className="kc-start-head">
          <div>
            <p className="kc-info-label">Getting started</p>
            <h2 className="kc-h2">{track.title}</h2>
            <p className="kc-muted">{track.intro}</p>
          </div>
          <div className="kc-progress">
            <span className="kc-progress-text">
              {count} of {total} done
            </span>
            <span
              className="kc-progress-bar"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={total}
              aria-valuenow={count}
              aria-label="Steps done"
            >
              <span style={{ width: `${pct}%` }} />
            </span>
          </div>
        </div>
        <ol className="kc-steps">
          {track.steps.map((s, i) => {
            const page = s.page ? help.page(s.page) : null;
            const open = page && canOpenPage(page);
            const isDone = done.has(i);
            return (
              <li key={i} className={`kc-step${isDone ? " kc-step-done" : ""}`}>
                <button
                  type="button"
                  className="kc-step-check"
                  aria-pressed={isDone}
                  aria-label={isDone ? `Step ${i + 1} done. Mark as not done` : `Mark step ${i + 1} done`}
                  onClick={() => toggle(i)}
                >
                  {isDone ? <HelpIcon name="check" size={16} strokeWidth={3} /> : <span>{i + 1}</span>}
                </button>
                <div className="kc-step-body">
                  <p className="kc-step-title">{s.title}</p>
                  <p className="kc-step-text">
                    <HelpText text={s.text} />
                  </p>
                  {(open || s.article) && (
                    <div className="kc-step-actions">
                      {open && (
                        <a className="kc-btn kc-btn-secondary kc-btn-sm" href={hashHref(page.web.route)}>
                          Open {page.title}
                          <HelpIcon name="arrowRight" size={14} />
                        </a>
                      )}
                      {s.article && (
                        <a className="kc-btn kc-btn-ghost kc-btn-sm" href={hashHref(helpPath({ article: s.article }))}>
                          <HelpIcon name="guides" size={14} />
                          Read the guide
                        </a>
                      )}
                    </div>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      </div>
      <p className="kc-muted kc-small">Your ticks are kept on this browser only. Pick Staff or KloudC master above for the other tracks.</p>
    </div>
  );
}

// ------------------------------------------------------------------ Guides

function Guides({ audience }) {
  const help = useHelp();
  const articles = help.articles().filter((a) => audienceMatches(audience, a.audience));
  return (
    <div className="kc-guides">
      {GUIDE_GROUPS.map((g) => {
        const list = articles.filter((a) => articleGroup(a) === g.key);
        if (list.length === 0) return null;
        return (
          <section key={g.key} className="kc-guide-group" data-area={g.area}>
            <h2 className="kc-group-title">
              <span className="kc-group-icon" aria-hidden="true">
                <HelpIcon name={g.icon} size={16} />
              </span>
              {g.label}
              <span className="kc-count">{list.length}</span>
            </h2>
            <div className="kc-hub-grid">
              {list.map((a) => (
                <a key={a.id} className="kc-hub-tile" href={hashHref(helpPath({ article: a.id }))}>
                  <span className="kc-hub-text">
                    <span className="kc-hub-title">{a.title}</span>
                    <span className="kc-hub-sub">{a.summary}</span>
                  </span>
                  <HelpIcon name="chevronRight" size={16} className="kc-hub-chevron" />
                </a>
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}

// ------------------------------------------------------------------ FAQ and troubleshooting

function useScrollTo(focusId) {
  useEffect(() => {
    if (!focusId) return;
    requestAnimationFrame(() => {
      const el = document.getElementById(`kc-qa-${focusId}`);
      if (!el) return;
      if (el.tagName === "DETAILS") el.open = true;
      el.scrollIntoView({ block: "start", behavior: "smooth" });
    });
  }, [focusId]);
}

const AREA_ORDER = ["help", "home", "ordering", "stock", "sales", "team", "setup", "platform"];

function groupByArea(list) {
  const groups = new Map();
  for (const x of list) {
    const area = x.area || "help";
    if (!groups.has(area)) groups.set(area, []);
    groups.get(area).push(x);
  }
  return AREA_ORDER.filter((k) => groups.has(k)).map((k) => ({ area: k, items: groups.get(k) }));
}

function areaTitle(area) {
  return area === "help" ? "Basics" : areaLabel(area);
}

function Faq({ audience, focusId }) {
  const help = useHelp();
  useScrollTo(focusId);
  const list = help.faq().filter((f) => audienceMatches(audience, f.audience) || f.id === focusId);
  return (
    <div className="kc-qa">
      {groupByArea(list).map((g) => (
        <section key={g.area} className="kc-qa-group" data-area={g.area}>
          <h2 className="kc-group-title">
            <span className="kc-area-dot" aria-hidden="true" />
            {areaTitle(g.area)}
          </h2>
          <div className="kc-qa-list">
            {g.items.map((f) => (
              <details key={f.id} id={`kc-qa-${f.id}`} className="kc-qa-item" open={f.id === focusId}>
                <summary>
                  <span className="kc-qa-q">{f.q}</span>
                  <HelpIcon name="chevronDown" size={18} className="kc-qa-chevron" />
                </summary>
                <div className="kc-qa-body">
                  <HelpBlocks blocks={f.a} />
                </div>
              </details>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function Troubleshooting({ audience, focusId }) {
  const help = useHelp();
  useScrollTo(focusId);
  const list = help.troubleshooting().filter((t) => audienceMatches(audience, t.audience) || t.id === focusId);
  return (
    <div className="kc-qa">
      {groupByArea(list).map((g) => (
        <section key={g.area} className="kc-qa-group" data-area={g.area}>
          <h2 className="kc-group-title">
            <span className="kc-area-dot" aria-hidden="true" />
            {areaTitle(g.area)}
          </h2>
          <div className="kc-qa-list">
            {g.items.map((t) => (
              <details key={t.id} id={`kc-qa-${t.id}`} className="kc-qa-item kc-trouble" open={t.id === focusId}>
                <summary>
                  <span className="kc-trouble-icon" aria-hidden="true">
                    <HelpIcon name="fix" size={15} />
                  </span>
                  <span className="kc-qa-q">{t.symptom}</span>
                  <HelpIcon name="chevronDown" size={18} className="kc-qa-chevron" />
                </summary>
                <div className="kc-qa-body kc-trouble-body">
                  <div className="kc-trouble-causes">
                    <p className="kc-info-label">Likely causes</p>
                    <ul className="kc-help-bullets">
                      {t.causes.map((c, i) => (
                        <li key={i}>
                          <HelpText text={c} />
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div className="kc-trouble-fixes">
                    <p className="kc-info-label">Try this</p>
                    <HelpBlocks blocks={t.fixes} />
                  </div>
                </div>
              </details>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ Contact

function Contact() {
  const help = useHelp();
  const c = help.contact();
  const [copied, setCopied] = useState(false);
  const mailto = supportMailto({ email: c.email, storeCode: help.storeCode, page: "Help Center" });
  async function copy() {
    try {
      await navigator.clipboard.writeText(c.email);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }
  return (
    <div className="kc-contact">
      <div className="kc-card kc-contact-main">
        <span className="kc-contact-badge" aria-hidden="true">
          <HelpIcon name="contact" size={22} />
        </span>
        <h2 className="kc-h2">Email KloudC support</h2>
        <p className="kc-muted">
          <HelpText text={c.intro} />
        </p>
        <div className="kc-contact-actions">
          <a className="kc-btn kc-btn-primary" href={mailto} data-testid="contact-email">
            <HelpIcon name="contact" size={16} />
            Email {c.email}
          </a>
          <button type="button" className="kc-btn kc-btn-secondary" onClick={copy}>
            <HelpIcon name={copied ? "check" : "copy"} size={16} />
            {copied ? "Copied" : "Copy address"}
          </button>
        </div>
        <p className="kc-small kc-muted">Your email app opens with a short template. Nothing is sent until you press Send.</p>
      </div>
      <div className="kc-card">
        <h3 className="kc-h3">A good support email</h3>
        <ul className="kc-help-bullets">
          {(c.tips || []).map((t, i) => (
            <li key={i}>
              <HelpText text={t} />
            </li>
          ))}
        </ul>
      </div>
      <div className="kc-card kc-contact-links">
        <h3 className="kc-h3">More from KloudC</h3>
        <a className="kc-link-row" href={c.support_url} target="_blank" rel="noopener noreferrer">
          <HelpIcon name="help" size={16} /> <span>Support page</span> <HelpIcon name="external" size={14} />
        </a>
        <a className="kc-link-row" href={c.contact_url} target="_blank" rel="noopener noreferrer">
          <HelpIcon name="globe" size={16} /> <span>Contact KloudC</span> <HelpIcon name="external" size={14} />
        </a>
        <a className="kc-link-row" href={c.privacy_url} target="_blank" rel="noopener noreferrer">
          <HelpIcon name="privacy" size={16} /> <span>Privacy policy</span> <HelpIcon name="external" size={14} />
        </a>
        {c.terms_url && (
          <a className="kc-link-row" href={c.terms_url} target="_blank" rel="noopener noreferrer">
            <HelpIcon name="guides" size={16} /> <span>Terms</span> <HelpIcon name="external" size={14} />
          </a>
        )}
        <p className="kc-small kc-muted">
          {c.product} by {c.company}, {c.location}.
        </p>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ one guide

function ReconciliationExample() {
  const auth = useAuthOrNull();
  if (auth?.hasFeature && !auth.hasFeature("reconciliation")) {
    return <p className="kc-muted kc-small">The live example needs Reconciliation access.</p>;
  }
  return (
    <div className="kc-example">
      <ReconciliationWorkedExample />
    </div>
  );
}

const EXAMPLES = { reconciliation_worked: ReconciliationExample };

function HelpArticle({ id, anchor, canOpenPage }) {
  const help = useHelp();
  const article = help.article(id);
  const outline = article ? articleOutline(article) : [];

  useEffect(() => {
    if (!article) return;
    requestAnimationFrame(() => {
      const el = anchor ? document.getElementById(`kc-h-${anchor}`) : null;
      if (el) el.scrollIntoView({ block: "start", behavior: "smooth" });
      else window.scrollTo({ top: 0 });
    });
  }, [article, anchor]);

  if (!article) {
    return (
      <div className="kc-help" data-area="help">
        <nav className="kc-crumbs" aria-label="Breadcrumb">
          <a href={hashHref(helpPath({ tab: "guides" }))}>
            <HelpIcon name="arrowLeft" size={14} /> Help Center
          </a>
        </nav>
        <div className="kc-card kc-help-empty">
          <p className="kc-help-empty-title">That guide isn't here</p>
          <p className="kc-help-empty-text">It may have moved. Search the Help Center, or browse the guides.</p>
          <a className="kc-btn kc-btn-secondary" href={hashHref(helpPath({ tab: "guides" }))}>
            Browse guides
          </a>
        </div>
      </div>
    );
  }
  const group = GUIDE_GROUPS.find((g) => g.key === articleGroup(article));
  const pages = (article.pages || []).map((pid) => help.page(pid)).filter((p) => p && canOpenPage(p));
  const related = (article.related || []).map((rid) => help.article(rid)).filter(Boolean);
  const mail = supportMailto({ email: help.contact().email, storeCode: help.storeCode, page: article.title, topic: "Guide feedback" });
  const jump = (hid) => {
    const el = document.getElementById(`kc-h-${hid}`);
    if (el) el.scrollIntoView({ block: "start", behavior: "smooth" });
  };

  return (
    <div className="kc-help kc-article-page" data-area={article.area || group?.area || "help"}>
      <nav className="kc-crumbs" aria-label="Breadcrumb">
        <a href={hashHref(helpPath({ tab: "guides" }))}>
          <HelpIcon name="arrowLeft" size={14} /> Help Center
        </a>
        <span aria-hidden="true">/</span>
        <span>{group?.label || "Guides"}</span>
      </nav>
      <div className="kc-article-layout">
        <article className="kc-article" aria-labelledby="kc-article-title">
          {article.area && (
            <span className="kc-area-chip">
              <span className="kc-area-dot" aria-hidden="true" />
              {areaLabel(article.area)}
            </span>
          )}
          <h1 id="kc-article-title" className="kc-article-title">
            {article.title}
          </h1>
          <p className="kc-article-lead">
            <HelpText text={article.summary} />
          </p>
          {pages.length > 0 && (
            <div className="kc-article-open">
              {pages.map((p) => (
                <a key={p.id} className="kc-btn kc-btn-secondary kc-btn-sm" href={hashHref(p.web.route)}>
                  Open {p.title}
                  <HelpIcon name="arrowRight" size={14} />
                </a>
              ))}
            </div>
          )}
          <div className="kc-article-body">
            <HelpBlocks blocks={article.blocks} examples={EXAMPLES} />
          </div>
          <div className="kc-article-feedback">
            <p>Was this helpful? Something missing?</p>
            <a className="kc-btn kc-btn-ghost kc-btn-sm" href={mail}>
              <HelpIcon name="contact" size={14} />
              Email us
            </a>
          </div>
        </article>
        <aside className="kc-article-rail" aria-label="In this guide">
          {outline.length > 1 && (
            <nav className="kc-rail-card" aria-label="On this page">
              <p className="kc-info-label">On this page</p>
              <ul>
                {outline.map((o) => (
                  <li key={o.id}>
                    <button type="button" className="kc-rail-link" onClick={() => jump(o.id)}>
                      <HelpText text={o.text} links={false} />
                    </button>
                  </li>
                ))}
              </ul>
            </nav>
          )}
          {related.length > 0 && (
            <nav className="kc-rail-card" aria-label="Related guides">
              <p className="kc-info-label">Related</p>
              <ul>
                {related.map((r) => (
                  <li key={r.id}>
                    <a className="kc-rail-link" href={hashHref(helpPath({ article: r.id }))}>
                      {r.title}
                    </a>
                  </li>
                ))}
              </ul>
            </nav>
          )}
        </aside>
      </div>
    </div>
  );
}
