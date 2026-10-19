// Fallback Help panel (right-side drawer) for the top bar's ? when the help
// components (track H's <HelpPanel />) aren't in this build: this page's
// name and area, where to read more and how to reach support.
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../AuthContext.jsx";
import AreaChip from "../ui/AreaChip.jsx";
import Icon from "../ui/icons.jsx";
import { usePageMeta } from "../ui/helpBridge.jsx";
import { KLOUDC_LINKS, supportMailto } from "../ui/kloudc.js";

export default function HelpDrawer() {
  const [pageId, setPageId] = useState(null);
  const [open, setOpen] = useState(false);
  const { store, hasFeature } = useAuth();
  const meta = usePageMeta(pageId);
  const closeBtn = useRef(null);

  useEffect(() => {
    const onHelp = (e) => {
      setPageId(e.detail?.pageId || null);
      setOpen((v) => !v || e.detail?.pageId !== pageId);
    };
    window.addEventListener("kc:help", onHelp);
    return () => window.removeEventListener("kc:help", onHelp);
  }, [pageId]);

  useEffect(() => {
    if (!open) return undefined;
    closeBtn.current?.focus();
    const onKey = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  if (!open) return null;
  const close = () => setOpen(false);
  return (
    <>
      <div className="kc-drawer-backdrop" onClick={close} aria-hidden="true" />
      <aside className="kc-drawer" role="dialog" aria-modal="true" aria-label="Help">
        <div className="kc-drawer-head">
          <span className="kc-drawer-title">
            <Icon name="life-buoy" size={18} /> Help
          </span>
          <button type="button" className="kc-icon-btn" onClick={close} aria-label="Close help" ref={closeBtn}>
            <Icon name="x" size={18} />
          </button>
        </div>
        <div className="kc-drawer-body">
          {meta.title && (
            <section className="kc-drawer-page" data-area={meta.area || undefined}>
              <AreaChip area={meta.area} />
              <h2>{meta.title}</h2>
              {meta.summary && <p className="muted">{meta.summary}</p>}
            </section>
          )}
          <nav className="kc-drawer-links" aria-label="Help links">
            {hasFeature("help") && (
              <Link to="/help" onClick={close} className="kc-hub-tile" data-area="help">
                <span className="kc-hub-icon">
                  <Icon name="book-open" size={18} />
                </span>
                <span>
                  <strong>Help Center</strong>
                  <span className="muted">Guides for every page, step by step</span>
                </span>
              </Link>
            )}
            <Link to="/glossary" onClick={close} className="kc-hub-tile" data-area="help">
              <span className="kc-hub-icon">
                <Icon name="book" size={18} />
              </span>
              <span>
                <strong>Glossary</strong>
                <span className="muted">What every status and word means</span>
              </span>
            </Link>
            <a href={supportMailto({ storeCode: store.code, page: meta.title })} className="kc-hub-tile" data-area="help">
              <span className="kc-hub-icon">
                <Icon name="mail" size={18} />
              </span>
              <span>
                <strong>Email support</strong>
                <span className="muted">Opens your mail app. Never send a password.</span>
              </span>
            </a>
            <a href={KLOUDC_LINKS.support} target="_blank" rel="noopener noreferrer" className="kc-hub-tile" data-area="help">
              <span className="kc-hub-icon">
                <Icon name="external-link" size={18} />
              </span>
              <span>
                <strong>KloudC support site</strong>
                <span className="muted">kloudc.com/kloudc-support</span>
              </span>
            </a>
          </nav>
          <p className="kc-drawer-tip muted">
            <Icon name="lightbulb" size={14} /> Tip: press <kbd>⌘K</kbd> or <kbd>/</kbd> to search every page.
          </p>
        </div>
      </aside>
    </>
  );
}
