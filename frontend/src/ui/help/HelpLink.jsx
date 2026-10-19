// A link to a Help Center guide (or one of its headings), a tab, or a
// glossary term:
//   <HelpLink article="reorder" anchor="lookback">Learn more</HelpLink>
//   <HelpLink tab="faq">Questions</HelpLink>
//   <HelpLink term="lead_time">Lead time</HelpLink>
import { glossaryPath, hashHref, helpPath } from "./helpModel.js";
import { HelpIcon } from "./helpIcons.jsx";

export function helpHref({ article, anchor, tab, term } = {}) {
  return hashHref(term ? glossaryPath(term) : helpPath({ article, anchor, tab }));
}

export default function HelpLink({ article, anchor, tab, term, children, className = "", icon = false, onClick }) {
  return (
    <a className={`kc-help-link ${className}`.trim()} href={helpHref({ article, anchor, tab, term })} onClick={onClick}>
      {icon && <HelpIcon name="guides" size={14} />}
      {children || "Learn more"}
    </a>
  );
}
