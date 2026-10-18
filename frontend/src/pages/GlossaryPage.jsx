import { useEffect, useMemo, useState } from "react";
import { useLocation } from "react-router-dom";
import { TONE_PILLS, useGlossary } from "../glossary.jsx";

// Every status / tag / label with what it means (backend/app/glossary.py).
// The Glossary tab of Help (HelpPage renders it at /glossary), reached from
// the sidebar and from "All definitions" in any (i) popover, which links to
// /glossary?k=<key> to highlight that term.
export default function GlossaryPage() {
  const { list, categories, loaded, error } = useGlossary();
  const location = useLocation();
  const focusKey = new URLSearchParams(location.search).get("k");
  const [search, setSearch] = useState("");

  const groups = useMemo(() => {
    const q = search.trim().toLowerCase();
    const matches = (t) =>
      !q ||
      [t.label, t.definition, t.rule || "", ...(t.aliases || [])].some((s) => s.toLowerCase().includes(q));
    return (categories || [])
      .map((c) => ({ ...c, terms: (list || []).filter((t) => t.category === c.key && matches(t)) }))
      .filter((c) => c.terms.length > 0);
  }, [list, categories, search]);

  useEffect(() => {
    if (!focusKey || !loaded) return;
    const el = document.getElementById(`term-${focusKey}`);
    if (el) el.scrollIntoView({ block: "center" });
  }, [focusKey, loaded]);

  return (
    <>
      <p className="muted glossary-intro">
        What every status, tag and label on the website and the app means, and how it's worked out. Click the
        small <span className="term-info-sample">i</span> next to a label on any page to see its definition there.
      </p>
      <input
        type="search"
        className="glossary-search"
        placeholder="Search, e.g. critical, review, intermittent…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      {!loaded && <p className="muted">Loading…</p>}
      {error && <p className="error">Couldn't load the glossary: {error}</p>}
      {loaded && !error && groups.length === 0 && <p className="muted">Nothing matches "{search}".</p>}
      {groups.map((c) => (
        <section className="card glossary-group" key={c.key}>
          <h2>{c.label}</h2>
          {c.where && <p className="muted glossary-where">Shown on: {c.where}</p>}
          <dl className="glossary-list">
            {c.terms.map((t) => (
              <div
                key={t.key}
                id={`term-${t.key}`}
                className={`glossary-term${t.key === focusKey ? " glossary-term-focus" : ""}`}
              >
                <dt>
                  {t.tone ? <span className={`pill ${TONE_PILLS[t.tone] || ""}`}>{t.label}</span> : t.label}
                </dt>
                <dd>
                  {t.definition}
                  {t.rule && (
                    <span className="glossary-rule">
                      <span className="glossary-rule-label">How it's worked out: </span>
                      {t.rule}
                    </span>
                  )}
                </dd>
              </div>
            ))}
          </dl>
        </section>
      ))}
    </>
  );
}
