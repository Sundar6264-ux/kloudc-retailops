// Search (⌘K): one box over (1) the pages this user can open, (2) help
// guides, FAQ and troubleshooting and (3) glossary terms - grouped, arrow
// keys + Enter, each result says where it lives. A centred dialog on a
// desktop, full screen on a phone.
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../AuthContext.jsx";
import { AREAS, searchablePages } from "../featureRoutes.js";
import Icon from "../ui/icons.jsx";
import { useHelpMaybe } from "../ui/helpBridge.jsx";
import { rankPages, helpResultPath } from "../ui/searchModel.js";

const KIND_LABEL = { article: "Guides", faq: "FAQ", trouble: "Troubleshooting", term: "Glossary" };
const KIND_ICON = { article: "book-open", faq: "circle-help", trouble: "wrench", term: "book" };

export default function CommandPalette({ onClose }) {
  const auth = useAuth();
  const help = useHelpMaybe();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const input = useRef(null);
  const listRef = useRef(null);
  const opener = useRef(typeof document !== "undefined" ? document.activeElement : null);

  const pages = useMemo(
    () => searchablePages({ user: auth.user, hasFeature: auth.hasFeature, canSeeSales: auth.canSeeSales, store: auth.store }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [auth.user, auth.canSeeSales, auth.store],
  );

  const results = useMemo(() => {
    const out = rankPages(pages, q).map((p) => ({
      kind: "page",
      key: `page:${p.route.path}`,
      title: p.label,
      where: p.where,
      to: p.route.path,
      icon: p.route.icon || AREAS[p.route.area]?.icon || "circle",
      area: p.route.area,
    }));
    if (q.trim() && help?.search) {
      let found = [];
      try {
        found = help.search(q, { limit: 24 }) || [];
      } catch {
        found = [];
      }
      for (const r of found) {
        if (r.kind === "page") continue;
        const to = helpResultPath(r);
        if (!to) continue;
        out.push({ kind: r.kind, key: `${r.kind}:${r.id}`, title: r.title, where: r.where || KIND_LABEL[r.kind], to, icon: KIND_ICON[r.kind] || "info", area: r.area });
      }
    }
    return out.slice(0, 40);
  }, [pages, q, help]);

  useEffect(() => {
    input.current?.focus();
    const prev = opener.current;
    return () => prev?.focus?.();
  }, []);
  useEffect(() => setActive(0), [q]);
  useEffect(() => {
    listRef.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  function go(r) {
    if (!r) return;
    onClose();
    navigate(r.to);
  }

  function onKeyDown(e) {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(results.length - 1, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(0, i - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      go(results[active]);
    } else if (e.key === "Tab") {
      // Focus stays in the dialog (the box is its only control).
      e.preventDefault();
    }
  }

  // Group headings in result order.
  let lastGroup = null;
  return (
    <div className="kc-overlay kc-palette-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="kc-palette" role="dialog" aria-modal="true" aria-label="Search" onKeyDown={onKeyDown}>
        <div className="kc-palette-input">
          <Icon name="search" size={18} />
          <input
            ref={input}
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={help?.search ? "Search pages, guides, FAQ and terms" : "Search pages"}
            aria-label="Search"
            role="combobox"
            aria-expanded="true"
            aria-controls="kc-palette-list"
            aria-activedescendant={results[active] ? `kc-pal-${active}` : undefined}
          />
          <button type="button" className="kc-icon-btn" onClick={onClose} aria-label="Close search">
            <Icon name="x" size={18} />
          </button>
        </div>
        <div className="kc-palette-list" id="kc-palette-list" role="listbox" ref={listRef} aria-label="Results">
          {results.length === 0 && <p className="kc-palette-empty">Nothing matches "{q}". Try another word, or open Help.</p>}
          {results.map((r, i) => {
            const group = r.kind === "page" ? "Pages" : KIND_LABEL[r.kind] || "Help";
            const head = group !== lastGroup ? group : null;
            lastGroup = group;
            return (
              <div key={r.key}>
                {head && <p className="kc-palette-group">{head}</p>}
                <button
                  type="button"
                  id={`kc-pal-${i}`}
                  data-index={i}
                  role="option"
                  aria-selected={i === active}
                  className={`kc-palette-item${i === active ? " is-active" : ""}`}
                  onMouseMove={() => setActive(i)}
                  onClick={() => go(r)}
                  tabIndex={-1}
                >
                  <span className="kc-palette-icon" data-area={r.area || undefined} aria-hidden="true">
                    <Icon name={r.icon} size={16} />
                  </span>
                  <span className="kc-palette-title">{r.title}</span>
                  {r.where && <span className="kc-palette-where">{r.where}</span>}
                  <Icon name="arrow-right" size={14} className="kc-palette-go" />
                </button>
              </div>
            );
          })}
        </div>
        <div className="kc-palette-foot" aria-hidden="true">
          <span>
            <kbd>↑</kbd> <kbd>↓</kbd> to move
          </span>
          <span>
            <kbd>Enter</kbd> to open
          </span>
          <span>
            <kbd>Esc</kbd> to close
          </span>
        </div>
      </div>
    </div>
  );
}
