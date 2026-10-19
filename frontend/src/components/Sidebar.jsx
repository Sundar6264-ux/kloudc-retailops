// The store sidebar (docs/DESIGN_SYSTEM.md 2.1): deep KloudC navy, the
// store's logo and short name on top (a 3 px stripe in the store's
// secondary colour under it), Home and Daily Checklists, then one group per
// area - Ordering, Stock, Sales, Team, Setup - each with its area-coloured
// icon. Groups remember whether they are open (kc.nav.open); the group
// holding the current page always opens. Desktop: fixed, collapsible to an
// icon rail (remembered; a group's links fly out beside the rail). Below
// 1024 px: a drawer under the top bar, opened by its ☰ (TopBar.jsx).
import { useEffect, useMemo, useRef, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { useAuth } from "../AuthContext.jsx";
import { AREAS, buildSidebar, footerLinks, groupsToOpen, routeLabel } from "../featureRoutes.js";
import Icon from "../ui/icons.jsx";
import { readRemembered, writeRemembered } from "../ui/useRemembered.js";
import useTeamBadges from "./team/useTeamBadges.js";
import StoreLogo from "./StoreLogo.jsx";

const COLLAPSE_STORAGE_KEY = "kc.nav.collapsed";
const OPEN_STORAGE_KEY = "kc.nav.open";

// Team hub counts shown next to a sidebar link (route key -> badge key).
export const ROUTE_BADGES = { team_chat: "unread", team_announcements: "announcements_unacked" };

function CountBadge({ count, label }) {
  if (!count) return null;
  return (
    <span className="sidebar-badge" aria-label={label ? `${count} ${label}` : undefined}>
      {count > 99 ? "99+" : count}
    </span>
  );
}

function readOpenGroups() {
  try {
    const v = JSON.parse(readRemembered(OPEN_STORAGE_KEY, "null"));
    return Array.isArray(v) ? new Set(v) : null;
  } catch {
    return null;
  }
}

export function useSidebarEntries() {
  const { user, hasFeature, canSeeSales, store } = useAuth();
  // hasFeature is a new function each render but only reads `user` and `store`.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => buildSidebar({ user, hasFeature, canSeeSales, store }), [user, canSeeSales, store]);
}

export default function Sidebar({ open = false, onClose = () => {}, collapsed = false, onToggleCollapsed = () => {} }) {
  const { user, hasFeature, store } = useAuth();
  const badges = useTeamBadges(!!user);
  const location = useLocation();
  const entries = useSidebarEntries();
  // First visit: the everyday groups open, Setup folded (admins use it
  // less often); after that, as the user left them.
  const [expandedGroups, setExpandedGroups] = useState(
    () => readOpenGroups() || new Set(entries.filter((e) => e.type === "group" && e.key !== "setup").map((e) => e.key)),
  );

  useEffect(() => {
    const here = groupsToOpen(entries, location.pathname);
    if (here.length) {
      setExpandedGroups((prev) => (here.every((k) => prev.has(k)) ? prev : new Set([...prev, ...here])));
    }
  }, [location.pathname, entries]);

  // Keep the current page's link in view in a long menu.
  const navRef = useRef(null);
  useEffect(() => {
    const t = setTimeout(() => {
      const active = navRef.current?.querySelector(".sidebar-link.active");
      if (active && typeof active.scrollIntoView === "function") active.scrollIntoView({ block: "nearest" });
    }, 0);
    return () => clearTimeout(t);
  }, [location.pathname, expandedGroups]);

  function toggleGroup(key) {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      writeRemembered(OPEN_STORAGE_KEY, JSON.stringify([...next]));
      return next;
    });
  }

  const badgeFor = (route) => badges[ROUTE_BADGES[route.key || route.feature]] || 0;
  const pos = store.posName;

  const renderLink = (route, { top = false } = {}) => (
    <NavLink
      key={route.path}
      to={route.path}
      end={route.end}
      onClick={onClose}
      className={({ isActive }) =>
        `sidebar-link${top ? " sidebar-link-top" : ""}${isActive || (route.hubOf && location.pathname.startsWith("/insights/")) ? " active" : ""}`
      }
      data-area={route.area || undefined}
      title={collapsed ? routeLabel(route, pos) : undefined}
    >
      <span className="sidebar-link-icon" aria-hidden="true">
        <Icon name={route.icon || AREAS[route.area]?.icon || "circle"} size={top ? 18 : 16} />
      </span>
      <span className="sidebar-link-label">{routeLabel(route, pos)}</span>
      <CountBadge count={badgeFor(route)} label="new" />
    </NavLink>
  );

  return (
    <>
      {open && <div className="sidebar-backdrop" onClick={onClose} aria-hidden="true" />}
      <nav
        className={`sidebar${open ? " sidebar-open" : ""}${collapsed ? " sidebar-collapsed" : ""}`}
        aria-label="Main menu"
        id="kc-sidebar"
      >
        <div className="sidebar-brand">
          {/* Logo + name go to Home (App sends anyone without it to their first page). */}
          <NavLink to="/" end className="sidebar-brand-link" onClick={onClose} aria-label={`${store.name} - go to Home`}>
            <StoreLogo store={store} />
            <span className="sidebar-brand-name" data-testid="store-name">
              {store.shortName}
            </span>
          </NavLink>
        </div>

        <div className="sidebar-nav" ref={navRef}>
          {entries.map((entry) => {
            if (entry.type === "link") return renderLink(entry.route, { top: true });
            const isOpen = expandedGroups.has(entry.key);
            const groupCount = entry.items.reduce((n, r) => n + badgeFor(r), 0);
            const area = AREAS[entry.area] || {};
            const listId = `sidebar-group-${entry.key}`;
            return (
              <div className={`sidebar-group${isOpen ? " is-open" : ""}`} key={entry.key} data-area={entry.area}>
                <button
                  type="button"
                  className="sidebar-group-toggle"
                  onClick={() => toggleGroup(entry.key)}
                  aria-expanded={isOpen}
                  aria-controls={listId}
                  title={collapsed ? entry.label : undefined}
                >
                  <span className="sidebar-group-icon" aria-hidden="true">
                    <Icon name={area.icon || "circle"} size={16} />
                  </span>
                  <span className="sidebar-group-label">{entry.label}</span>
                  {!isOpen && <CountBadge count={groupCount} label="new" />}
                  <Icon name="chevron-right" size={14} className="sidebar-chevron" />
                </button>
                {(isOpen || collapsed) && (
                  <div className="sidebar-subnav" id={listId}>
                    {collapsed && <p className="sidebar-flyout-title">{entry.label}</p>}
                    {entry.sections.map((sec, i) => (
                      <div className="sidebar-section" key={sec.label || i}>
                        {sec.label && <p className="sidebar-section-label">{sec.label}</p>}
                        {sec.items.map((r) => renderLink(r))}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        <div className="sidebar-footer">
          {footerLinks({ hasFeature, store }).map((l) =>
            l.external ? (
              <a
                key={l.key}
                className="sidebar-link sidebar-external-link"
                href={l.href}
                target="_blank"
                rel="noopener noreferrer"
                onClick={onClose}
                title={collapsed ? l.label : undefined}
              >
                <span className="sidebar-link-icon" aria-hidden="true">
                  <Icon name={l.icon} size={16} />
                </span>
                <span className="sidebar-link-label">{l.label}</span>
                <Icon name="external-link" size={13} className="sidebar-ext" />
              </a>
            ) : (
              <NavLink
                key={l.key}
                to={l.path}
                onClick={onClose}
                title={collapsed ? l.label : undefined}
                data-area="help"
                className={({ isActive }) =>
                  `sidebar-link${isActive || (l.activePaths || []).includes(location.pathname) ? " active" : ""}`
                }
              >
                <span className="sidebar-link-icon" aria-hidden="true">
                  <Icon name={l.icon} size={16} />
                </span>
                <span className="sidebar-link-label">{l.label}</span>
              </NavLink>
            ),
          )}
          <button
            type="button"
            className="sidebar-collapse-btn"
            onClick={onToggleCollapsed}
            aria-label={collapsed ? "Expand menu" : "Collapse menu"}
            aria-pressed={collapsed}
          >
            <Icon name={collapsed ? "chevrons-right" : "chevrons-left"} size={16} />
            <span className="sidebar-link-label">Collapse</span>
          </button>
        </div>
      </nav>
    </>
  );
}

export function readCollapsed() {
  return readRemembered(COLLAPSE_STORAGE_KEY, "0") === "1";
}

export function writeCollapsed(v) {
  writeRemembered(COLLAPSE_STORAGE_KEY, v ? "1" : "0");
}
