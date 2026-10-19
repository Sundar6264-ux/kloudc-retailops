// The right-side Help panel (400 px drawer; a full-screen sheet on phones),
// opened by the top bar's ? and by a page header's ⓘ through useHelp().openPanel.
// W mounts it once in the app shell: <HelpPanel />. It shows the page's info,
// a search over all of help, and links to the Help Center and the Glossary.
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useHelp } from "./HelpProvider.jsx";
import { InfoContent } from "./InfoButton.jsx";
import { HelpIcon } from "./helpIcons.jsx";
import { HelpSearchResults } from "./HelpSearch.jsx";
import { glossaryPath, hashHref, helpPath } from "./helpModel.js";

export default function HelpPanel() {
  const help = useHelp();
  const { panel, closePanel, registerPanel } = help;
  const ref = useRef(null);
  const [q, setQ] = useState("");

  useEffect(() => registerPanel(), [registerPanel]);

  useEffect(() => {
    if (!panel.open) return undefined;
    setQ("");
    const prev = document.activeElement;
    requestAnimationFrame(() => ref.current?.focus());
    function onKey(e) {
      if (e.key === "Escape") closePanel();
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      if (prev && typeof prev.focus === "function" && document.contains(prev)) prev.focus();
    };
  }, [panel.open, panel.pageId, closePanel]);

  if (!panel.open) return null;
  const page = panel.pageId ? help.page(panel.pageId) : null;
  return createPortal(
    <div className="kc-panel-layer">
      <div className="kc-panel-scrim" onClick={closePanel} aria-hidden="true" />
      <aside ref={ref} className="kc-panel" role="dialog" aria-modal="false" aria-label="Help" tabIndex={-1} data-area={page?.area || "help"}>
        <header className="kc-panel-head">
          <span className="kc-panel-badge" aria-hidden="true">
            <HelpIcon name="help" size={16} />
          </span>
          <p className="kc-panel-title">Help</p>
          <button type="button" className="kc-icon-btn" aria-label="Close help" onClick={closePanel}>
            <HelpIcon name="x" size={18} />
          </button>
        </header>
        <div className="kc-panel-search">
          <HelpIcon name="search" size={16} />
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search help, guides and terms"
            aria-label="Search help"
          />
        </div>
        <div className="kc-panel-body">
          {q.trim() ? (
            <HelpSearchResults query={q} onNavigate={closePanel} compact />
          ) : page ? (
            <InfoContent pageId={panel.pageId} section={panel.section} extra={panel.extra} onNavigate={closePanel} />
          ) : (
            <p className="kc-info-summary">Search above, or open the Help Center for guides, answers and fixes.</p>
          )}
        </div>
        <footer className="kc-panel-foot">
          <a className="kc-btn kc-btn-secondary" href={hashHref(helpPath())} onClick={closePanel}>
            <HelpIcon name="guides" size={16} />
            Help Center
          </a>
          <a className="kc-btn kc-btn-ghost" href={hashHref(glossaryPath())} onClick={closePanel}>
            <HelpIcon name="glossary" size={16} />
            Glossary
          </a>
        </footer>
      </aside>
    </div>,
    document.body,
  );
}
