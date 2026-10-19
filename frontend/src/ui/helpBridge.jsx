// The website's link to the help components (track H, frontend/src/ui/help/,
// docs/DESIGN_SYSTEM.md 5.3). Read through a glob so this layout code builds
// with or without them: when ui/help/index.js exists its InfoButton,
// HelpPanel, HelpCenter and useHelp are used; otherwise a small fallback ⓘ
// (title, area and a link to Help) stands in. Page titles and areas come
// from the help registry when it has the page, else from featureRoutes.js
// PAGE_META - so a title can't differ between header, menu and search.
import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { AREAS, PAGE_META } from "../featureRoutes.js";
import Icon from "./icons.jsx";

const mods = import.meta.glob("./help/index.js", { eager: true });
const H = mods["./help/index.js"] || {};

export const helpAvailable = !!H.useHelp;
export const HelpPanel = H.HelpPanel || null;
export const HelpCenter = H.HelpCenter || null;
export const HelpProvider = H.HelpProvider || null;

const useHelpImpl = H.useHelp || (() => null);

// useHelp() from track H, or null without it. (The choice is made once per
// build, so hook order never changes.)
export function useHelpMaybe() {
  return useHelpImpl();
}

// { title, area, icon, summary } for a page id: help registry first, then
// the route table.
export function usePageMeta(pageId) {
  const help = useHelpMaybe();
  const local = PAGE_META[pageId] || {};
  let reg = null;
  try {
    reg = help?.page ? help.page(pageId) : null;
  } catch {
    reg = null;
  }
  return {
    title: reg?.title || local.title || "",
    area: reg?.area !== undefined && reg?.area !== null ? reg.area : local.area || null,
    icon: local.icon || AREAS[reg?.area]?.icon || null,
    summary: reg?.info?.summary || null,
  };
}

// Open the page's help: H's right-side Help panel when there is one, else
// the fallback drawer (TopBar listens for "kc:help").
export function openPageHelp(help, pageId) {
  if (help?.openPanel && help?.hasPanel?.()) {
    help.openPanel(pageId);
    return;
  }
  window.dispatchEvent(new CustomEvent("kc:help", { detail: { pageId } }));
}

// The ⓘ button: H's InfoButton when present, else a fallback popover.
export function InfoButton(props) {
  if (H.InfoButton) {
    const Real = H.InfoButton;
    return <Real {...props} />;
  }
  return <FallbackInfo {...props} />;
}

function FallbackInfo({ pageId, section, extra, label }) {
  const meta = usePageMeta(pageId);
  const [open, setOpen] = useState(false);
  const id = useId();
  const wrap = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e) => {
      if (wrap.current && !wrap.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  const title = meta.title || "this page";
  return (
    <span className="kc-info-wrap" ref={wrap}>
      <button
        type="button"
        className="kc-icon-btn kc-info-btn"
        aria-label={label || `About ${title}`}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((v) => !v)}
      >
        <Icon name="info" size={16} />
      </button>
      {open && (
        <div className="kc-popover kc-info-pop" id={id} role="dialog" aria-label={`About ${title}`}>
          <p className="kc-pop-title">{section ? `${title}: ${section.replace(/_/g, " ")}` : title}</p>
          {meta.summary && <p className="kc-pop-text">{meta.summary}</p>}
          {extra && <div className="kc-pop-extra">{extra}</div>}
          <Link className="kc-pop-link" to="/help" onClick={() => setOpen(false)}>
            Open Help <Icon name="arrow-right" size={14} />
          </Link>
        </div>
      )}
    </span>
  );
}
