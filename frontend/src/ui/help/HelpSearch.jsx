// One search over the pages this user can open, guides, FAQ answers,
// troubleshooting fixes and glossary terms (useHelp().search). Used by the
// Help Center, the Help panel and (through searchHelpFor) the top bar's ⌘K.
import { useAuth } from "../../AuthContext.jsx";
import { isPlatformUser } from "../../login/loginModel.js";
import { useHelp } from "./HelpProvider.jsx";
import { HelpIcon } from "./helpIcons.jsx";
import { glossaryPath, hashHref, helpPath } from "./helpModel.js";

const KINDS = [
  { kind: "page", label: "Pages", icon: "arrowRight" },
  { kind: "article", label: "Guides", icon: "guides" },
  { kind: "faq", label: "Answers", icon: "faq" },
  { kind: "trouble", label: "Fixes", icon: "fix" },
  { kind: "term", label: "Glossary", icon: "glossary" },
];

function useAuthOrNull() {
  try {
    return useAuth();
  } catch {
    return null;
  }
}

// Can this signed-in user open this page on the website? (The same rules as
// featureRoutes.js routeVisible, from the registry's gating.)
export function canOpenPageFor(auth) {
  const user = auth?.user || null;
  const platform = user ? isPlatformUser(user) : false;
  return (page) => {
    if (!page?.web?.route) return false;
    if (["no_access", "not_available"].includes(page.id)) return false;
    const masterOnly = (page.audience || []).length === 1 && page.audience[0] === "master";
    if (platform) return masterOnly;
    if (masterOnly) return false;
    if (page.public) return !user;
    if (!user) return false;
    const g = page.gating || {};
    if (g.admin && !user.is_admin) return false;
    if (g.feature && auth?.hasFeature && !auth.hasFeature(g.feature)) return false;
    if (g.salesOnly && auth && !auth.canSeeSales) return false;
    return true;
  };
}

// The signed-in user's audience for ranking ("owner" for owners and admins).
export function audienceFor(auth) {
  const user = auth?.user;
  if (!user) return null;
  if (isPlatformUser(user)) return "master";
  return user.is_admin ? "owner" : "staff";
}

export function resultHref(r, help) {
  switch (r.kind) {
    case "page":
      return hashHref(help.page(r.id)?.web?.route || "/");
    case "article":
      return hashHref(helpPath({ article: r.id }));
    case "faq":
      return hashHref(helpPath({ tab: "faq", anchor: r.id }));
    case "trouble":
      return hashHref(helpPath({ tab: "troubleshooting", anchor: r.id }));
    case "term":
      return hashHref(glossaryPath(r.id));
    default:
      return hashHref(helpPath());
  }
}

// For the top bar's search: grouped results for a query, each with an href.
export function useHelpSearch() {
  const help = useHelp();
  const auth = useAuthOrNull();
  const canOpenPage = canOpenPageFor(auth);
  const audience = audienceFor(auth);
  return (q, opts = {}) =>
    help.search(q, { canOpenPage, audience, ...opts }).map((r) => ({ ...r, href: resultHref(r, help) }));
}

export function HelpSearchResults({ query, onNavigate, compact = false, limit = 30 }) {
  const search = useHelpSearch();
  const results = search(query, { limit });
  if (!query.trim()) return null;
  if (results.length === 0) {
    return (
      <div className="kc-help-empty" role="status">
        <span className="kc-help-empty-icon" aria-hidden="true">
          <HelpIcon name="search" size={22} />
        </span>
        <p className="kc-help-empty-title">Nothing matches "{query}"</p>
        <p className="kc-help-empty-text">Try a shorter word, or email support and we'll point you the right way.</p>
      </div>
    );
  }
  return (
    <div className={`kc-search-results${compact ? " kc-search-compact" : ""}`} role="region" aria-label="Search results" aria-live="polite">
      {KINDS.map(({ kind, label, icon }) => {
        const group = results.filter((r) => r.kind === kind);
        if (group.length === 0) return null;
        return (
          <section key={kind} className="kc-search-group">
            <h3 className="kc-search-group-title">
              {label} <span className="kc-count">{group.length}</span>
            </h3>
            <ul>
              {group.map((r) => (
                <li key={`${r.kind}:${r.id}`}>
                  <a className="kc-search-item" href={r.href} onClick={onNavigate} data-area={r.area || undefined}>
                    <span className="kc-search-icon" aria-hidden="true">
                      <HelpIcon name={icon} size={15} />
                    </span>
                    <span className="kc-search-text">
                      <span className="kc-search-title">{r.title}</span>
                      {r.where && <span className="kc-search-where">{r.where}</span>}
                    </span>
                    <HelpIcon name="chevronRight" size={16} className="kc-search-chevron" />
                  </a>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
