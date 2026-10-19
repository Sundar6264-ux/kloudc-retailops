// ⓘ info for a page, a section or a form field (docs/DESIGN_SYSTEM.md 4.3),
// from the page registry (content/help/pages.json) by pageId (+ section).
//
//   <InfoButton pageId="reorder" />                      page-level: the Help panel
//   <InfoButton pageId="reorder" section="urgency" />    a popover beside the button
//   <InfoButton pageId="items_sold" extra={<p>Updated 2:08 AM</p>} />
//   <FieldInfo pageId="signup" section="store_code" />   small ⓘ beside a label
//
// Desktop: page-level ⓘ opens the right-side <HelpPanel /> when one is mounted
// (else a popover); sections and fields open a popover. Phone (< 640 px): a
// bottom sheet for all of them.
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useHelp } from "./HelpProvider.jsx";
import { HelpText } from "./HelpText.jsx";
import { HelpIcon } from "./helpIcons.jsx";
import { areaLabel, glossaryPath, hashHref, helpPath, supportMailto, whoCanSee } from "./helpModel.js";

const PHONE_MAX = 639;

export function isPhoneWidth() {
  return typeof window !== "undefined" && window.matchMedia?.(`(max-width: ${PHONE_MAX}px)`).matches;
}

// The panel body, shared by the popover, the sheet and the Help panel.
// Order: title, summary, points, where the numbers come from, live lines,
// terms on this page, who can see this, full guide, support.
export function InfoContent({ pageId, section, extra, onNavigate, headingLevel = 2, showTitle = true, compact = false }) {
  const help = useHelp();
  const page = help.page(pageId);
  if (!page) {
    return (
      <div className="kc-info">
        <p className="kc-info-summary">No help for this yet.</p>
        <p className="kc-info-support">
          <a className="kc-help-link" href={supportMailto({ email: help.contact().email, page: pageId })}>
            Email support
          </a>
        </p>
      </div>
    );
  }
  const sec = section ? page.sections?.[section] : null;
  const body = sec || page.info || {};
  const links = !page.public;
  const H = `h${headingLevel}`;
  const termKeys = sec ? [] : page.terms || [];
  const who = !sec && whoCanSee(page);
  return (
    <div className={`kc-info${compact ? " kc-info-compact" : ""}`} data-area={page.area || undefined}>
      {showTitle && (
        <div className="kc-info-head">
          {page.area && !sec && (
            <span className="kc-area-chip">
              <span className="kc-area-dot" aria-hidden="true" />
              {areaLabel(page.area)}
            </span>
          )}
          <H className="kc-info-title">{sec ? sec.title : page.title}</H>
          {sec && <p className="kc-info-context">{page.title}</p>}
        </div>
      )}
      {body.summary && (
        <p className="kc-info-summary">
          <HelpText text={body.summary} links={links} onNavigate={onNavigate} />
        </p>
      )}
      {(body.points || []).length > 0 && (
        <ul className="kc-info-points">
          {body.points.map((pt, i) => (
            <li key={i}>
              <HelpText text={pt} links={links} onNavigate={onNavigate} />
            </li>
          ))}
        </ul>
      )}
      {!sec && (page.info?.source || page.info?.freshness) && (
        <div className="kc-info-block">
          <p className="kc-info-label">Where the numbers come from</p>
          {page.info.source && (
            <p className="kc-info-line">
              <HelpText text={page.info.source} links={links} />
            </p>
          )}
          {page.info.freshness && (
            <p className="kc-info-line kc-info-muted">
              <HelpText text={page.info.freshness} links={links} />
            </p>
          )}
        </div>
      )}
      {extra && <div className="kc-info-block kc-info-extra">{extra}</div>}
      {termKeys.length > 0 && (
        <div className="kc-info-block">
          <p className="kc-info-label">Terms on this page</p>
          <div className="kc-info-terms">
            {termKeys.map((k) => {
              const t = help.term(k);
              if (!t) return null;
              return links ? (
                <a key={k} className="kc-term-chip" href={hashHref(glossaryPath(k))} onClick={onNavigate} title={t.definition}>
                  {t.label}
                </a>
              ) : (
                <span key={k} className="kc-term-chip" title={t.definition}>
                  {t.label}
                </span>
              );
            })}
          </div>
        </div>
      )}
      {who && (
        <div className="kc-info-block">
          <p className="kc-info-label">Who can see this</p>
          <p className="kc-info-line">
            <HelpText text={who} links={false} />
          </p>
        </div>
      )}
      <div className="kc-info-foot">
        {links && page.article && (
          <a className="kc-info-guide" href={hashHref(helpPath({ article: page.article, anchor: section || undefined }))} onClick={onNavigate}>
            <HelpIcon name="guides" size={16} />
            Read the full guide
            <HelpIcon name="arrowRight" size={14} />
          </a>
        )}
        <a
          className="kc-info-support"
          href={supportMailto({ email: help.contact().email, page: page.title, storeCode: help.storeCode })}
        >
          <HelpIcon name="contact" size={16} />
          Still stuck? Email support
        </a>
      </div>
    </div>
  );
}

// A popover anchored to `anchor`, kept on screen; closes on outside click,
// Esc, resize, or when its button scrolls away.
export function InfoPopover({ anchor, onClose, labelledBy, children, width = 340 }) {
  const ref = useRef(null);
  const [pos, setPos] = useState({ top: -9999, left: -9999, arrowLeft: 16, above: false });

  const place = useCallback(() => {
    if (!anchor || !ref.current) return true;
    const r = anchor.getBoundingClientRect();
    if (r.bottom < 0 || r.top > window.innerHeight) return false;
    const box = ref.current.getBoundingClientRect();
    const margin = 12;
    let left = r.left + r.width / 2 - box.width / 2;
    left = Math.max(margin, Math.min(left, window.innerWidth - box.width - margin));
    let top = r.bottom + 10;
    let above = false;
    if (top + box.height > window.innerHeight - margin && r.top - box.height - 10 > margin) {
      top = r.top - box.height - 10;
      above = true;
    }
    const arrowLeft = Math.max(14, Math.min(box.width - 14, r.left + r.width / 2 - left));
    setPos({ top, left, arrowLeft, above });
    return true;
  }, [anchor]);

  useLayoutEffect(() => {
    place();
  }, [place]);

  useEffect(() => {
    function onDown(e) {
      if (ref.current?.contains(e.target) || anchor?.contains(e.target)) return;
      onClose();
    }
    function onKey(e) {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose(true);
      }
    }
    function onScroll(e) {
      if (ref.current?.contains(e.target)) return;
      if (!place()) onClose();
    }
    document.addEventListener("mousedown", onDown, true);
    document.addEventListener("touchstart", onDown, true);
    document.addEventListener("keydown", onKey, true);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onClose);
    return () => {
      document.removeEventListener("mousedown", onDown, true);
      document.removeEventListener("touchstart", onDown, true);
      document.removeEventListener("keydown", onKey, true);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onClose);
    };
  }, [anchor, onClose, place]);

  return createPortal(
    <div
      ref={ref}
      className={`kc-popover${pos.above ? " kc-popover-above" : ""}`}
      role="dialog"
      aria-labelledby={labelledBy}
      style={{ top: pos.top, left: pos.left, width: `min(${width}px, calc(100vw - 24px))`, "--kc-arrow-left": `${pos.arrowLeft}px` }}
    >
      <span className="kc-popover-arrow" aria-hidden="true" />
      {children}
    </div>,
    document.body,
  );
}

// A bottom sheet (phones): scrim, drag handle, close button, focus kept inside.
export function InfoSheet({ onClose, label, children }) {
  const ref = useRef(null);
  useEffect(() => {
    const prev = document.activeElement;
    ref.current?.focus();
    function onKey(e) {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose(true);
      }
      if (e.key === "Tab" && ref.current) {
        const items = ref.current.querySelectorAll('a[href], button:not([disabled]), input, [tabindex="0"]');
        if (items.length === 0) return;
        const first = items[0];
        const last = items[items.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }
    document.addEventListener("keydown", onKey, true);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.body.style.overflow = overflow;
      if (prev && typeof prev.focus === "function") prev.focus();
    };
  }, [onClose]);
  return createPortal(
    <div className="kc-sheet-layer">
      <div className="kc-sheet-scrim" onClick={() => onClose()} aria-hidden="true" />
      <div ref={ref} className="kc-sheet" role="dialog" aria-modal="true" aria-label={label} tabIndex={-1}>
        <span className="kc-sheet-handle" aria-hidden="true" />
        <button type="button" className="kc-icon-btn kc-sheet-close" aria-label="Close" onClick={() => onClose(true)}>
          <HelpIcon name="x" size={18} />
        </button>
        <div className="kc-sheet-body">{children}</div>
      </div>
    </div>,
    document.body,
  );
}

export default function InfoButton({
  pageId,
  section,
  extra,
  variant = "icon",
  label,
  className = "",
  size = "md",
}) {
  const help = useHelp();
  const page = help.page(pageId);
  const btnRef = useRef(null);
  const [mode, setMode] = useState(null); // null | "popover" | "sheet"
  const popId = useId();
  const title = section ? page?.sections?.[section]?.title || page?.title : page?.title;
  const aria = label || `About ${title || "this page"}`;

  const close = useCallback((focusBack) => {
    setMode(null);
    if (focusBack) btnRef.current?.focus();
  }, []);

  function onClick(e) {
    e.preventDefault();
    e.stopPropagation();
    if (mode) {
      close();
      return;
    }
    if (isPhoneWidth()) {
      setMode("sheet");
    } else if (!section && help.hasPanel()) {
      if (help.panel.open && help.panel.pageId === pageId) help.closePanel();
      else help.openPanel(pageId, { extra });
    } else {
      setMode("popover");
    }
  }

  const expanded = !!mode || (!section && help.panel.open && help.panel.pageId === pageId);
  return (
    <>
      <button
        type="button"
        ref={btnRef}
        className={`kc-info-btn kc-info-btn-${variant} kc-info-btn-${size} ${className}`.trim()}
        aria-label={aria}
        aria-expanded={expanded}
        aria-haspopup="dialog"
        aria-controls={mode ? popId : undefined}
        onClick={onClick}
        onMouseDown={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.stopPropagation()}
        data-help-page={pageId}
        data-help-section={section || undefined}
      >
        <HelpIcon name="info" size={variant === "inline" ? 15 : 16} />
        {variant === "inline" && <span>{label || "About this page"}</span>}
      </button>
      {mode === "popover" && (
        <InfoPopover anchor={btnRef.current} onClose={close} labelledBy={`${popId}-t`}>
          <div id={popId}>
            <span id={`${popId}-t`} className="kc-visually-hidden">
              {aria}
            </span>
            <InfoContent pageId={pageId} section={section} extra={extra} headingLevel={3} onNavigate={() => close()} compact />
          </div>
        </InfoPopover>
      )}
      {mode === "sheet" && (
        <InfoSheet onClose={close} label={aria}>
          <div id={popId}>
            <InfoContent pageId={pageId} section={section} extra={extra} headingLevel={2} onNavigate={() => close()} />
          </div>
        </InfoSheet>
      )}
    </>
  );
}

// A small ⓘ beside a form label: always a popover (sheet on phones).
export function FieldInfo({ pageId, section, label }) {
  return <InfoButton pageId={pageId} section={section} label={label} size="sm" className="kc-field-info" />;
}
