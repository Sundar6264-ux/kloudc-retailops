import { useEffect, useMemo, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { useAuth } from "../AuthContext.jsx";
import { ROUTES_BY_KEY, buildSidebar, footerLinks, groupsToOpen } from "../featureRoutes.js";
import useTeamBadges from "./team/useTeamBadges.js";

const COLLAPSE_STORAGE_KEY = "spicetown_sidebar_collapsed";

// Team hub counts shown next to a sidebar link (route key -> badge key).
const ROUTE_BADGES = { team_chat: "unread", team_announcements: "announcements_unacked" };

function CountBadge({ count }) {
  if (!count) return null;
  return <span className="sidebar-badge">{count > 99 ? "99+" : count}</span>;
}

function ChevronIcon({ open }) {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 12 12"
      fill="none"
      style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform 0.12s", flexShrink: 0 }}
    >
      <path d="M4 2 L8 6 L4 10" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export default function Sidebar() {
  const { user, logout, hasFeature, canSeeSales } = useAuth();
  const badges = useTeamBadges(!!user);
  const location = useLocation();
  const [open, setOpen] = useState(false);
  // Desktop-only collapse, independent of the mobile drawer above - remembered
  // across visits since it's a standing layout preference, not per-session.
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(COLLAPSE_STORAGE_KEY) === "1");
  const [expandedGroups, setExpandedGroups] = useState(() => new Set());
  // What this user gets, in order (featureRoutes.js buildSidebar): empty
  // groups left out, a group with one link shown as that link.
  const entries = useMemo(
    () => buildSidebar({ user, hasFeature, canSeeSales }),
    // hasFeature is a new function each render but only reads `user`.
    [user, canSeeSales],
  );

  // Auto-open whichever group contains the current page, so navigating
  // straight to a sub-item (a bookmark, a link from another page) never
  // leaves it hidden inside a collapsed group with no visual cue. A page in
  // a nested group (Insights > Also in Toast) opens both levels.
  useEffect(() => {
    const open = groupsToOpen(entries, location.pathname);
    if (open.length) {
      setExpandedGroups((prev) => (open.every((k) => prev.has(k)) ? prev : new Set([...prev, ...open])));
    }
  }, [location.pathname, entries]);

  function closeMobileNav() {
    setOpen(false);
  }

  function toggleCollapsed() {
    setCollapsed((prev) => {
      const next = !prev;
      localStorage.setItem(COLLAPSE_STORAGE_KEY, next ? "1" : "0");
      return next;
    });
  }

  function toggleGroup(key) {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  // On a phone the counts are inside the closed menu, so the ☰ button
  // shows a dot when anything the user can open has unread items.
  const menuCount = Object.entries(ROUTE_BADGES).reduce((n, [key, badgeKey]) => {
    const route = ROUTES_BY_KEY[key];
    return route && (route.everyone || hasFeature(route.feature)) ? n + (badges[badgeKey] || 0) : n;
  }, 0);

  function navLinkClass({ isActive }) {
    return isActive ? "active" : undefined;
  }

  const badgeFor = (route) => badges[ROUTE_BADGES[route.key || route.feature]] || 0;
  const renderLink = (route) => (
    <NavLink key={route.path} to={route.path} end={route.end} onClick={closeMobileNav} className={navLinkClass}>
      {route.label}
      <CountBadge count={badgeFor(route)} />
    </NavLink>
  );

  return (
    <>
      <button
        className="sidebar-toggle"
        aria-label={open ? "Close menu" : menuCount ? `Open menu (${menuCount} unread)` : "Open menu"}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? "✕" : "☰"}
        {!open && menuCount > 0 && <span className="sidebar-toggle-dot" aria-hidden="true" />}
      </button>
      {open && <div className="sidebar-backdrop" onClick={closeMobileNav} />}
      {collapsed && (
        <button className="sidebar-expand-btn" aria-label="Expand menu" onClick={toggleCollapsed}>
          »
        </button>
      )}
      <nav className={`sidebar${open ? " sidebar-open" : ""}${collapsed ? " sidebar-collapsed" : ""}`}>
        <div className="sidebar-brand">
          {/* Logo + name go to the dashboard (Overview; App sends anyone
              without it to their first page). */}
          <NavLink to="/" end className="sidebar-brand-link" onClick={closeMobileNav} aria-label="StoreOps - go to the dashboard">
            <img src="/logo-header.png" alt="" />
            <span>StoreOps</span>
          </NavLink>
          <button className="sidebar-collapse-btn" aria-label="Collapse menu" onClick={toggleCollapsed}>
            «
          </button>
        </div>
        <div className="sidebar-nav">
          {entries.map((entry) => {
            if (entry.type === "link") return renderLink(entry.route);

            const { items, subgroups } = entry;
            const isOpen = expandedGroups.has(entry.key);
            const groupCount = [...items, ...subgroups.flatMap((sub) => sub.items)].reduce((n, r) => n + badgeFor(r), 0);
            return (
              <div className="sidebar-group" key={entry.key}>
                <button
                  type="button"
                  className="sidebar-group-toggle"
                  onClick={() => toggleGroup(entry.key)}
                  aria-expanded={isOpen}
                >
                  <ChevronIcon open={isOpen} />
                  {entry.label}
                  {!isOpen && <CountBadge count={groupCount} />}
                </button>
                {isOpen && (
                  <div className="sidebar-subnav">
                    {items.map(renderLink)}
                    {/* A quieter, collapsed group inside this one (Sales &
                        Insights > Also in Toast: pages that repeat Toast's
                        own reports). */}
                    {subgroups.map((sub) => {
                      const subOpen = expandedGroups.has(sub.key);
                      const noteId = `sidebar-note-${sub.key}`;
                      return (
                        <div className="sidebar-subgroup" key={sub.key}>
                          <button
                            type="button"
                            className="sidebar-group-toggle sidebar-subgroup-toggle"
                            onClick={() => toggleGroup(sub.key)}
                            aria-expanded={subOpen}
                            aria-describedby={sub.note ? noteId : undefined}
                            title={sub.note}
                          >
                            <ChevronIcon open={subOpen} />
                            {sub.label}
                          </button>
                          {sub.note && (
                            <p id={noteId} className="sidebar-subgroup-note" hidden={!subOpen}>
                              {sub.note}
                            </p>
                          )}
                          {subOpen && <div className="sidebar-subnav sidebar-subgroup-nav">{sub.items.map(renderLink)}</div>}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}

          {/* For everyone, below the groups: the label printer (an external
              tool with its own login, not part of the feature grants), Help
              (or just its Glossary tab), Notifications, Change password. */}
          {footerLinks({ hasFeature }).map((l, i) =>
            l.external ? (
              <a
                key={l.key}
                className={i === 0 ? "sidebar-external-link" : undefined}
                href={l.href}
                target="_blank"
                rel="noopener noreferrer"
                onClick={closeMobileNav}
              >
                {l.label}
              </a>
            ) : (
              <NavLink
                key={l.key}
                to={l.path}
                onClick={closeMobileNav}
                className={({ isActive }) =>
                  isActive || (l.activePaths || []).includes(location.pathname) ? "active" : undefined
                }
              >
                {l.label}
              </NavLink>
            ),
          )}
        </div>
        <div className="sidebar-footer">
          {user && (
            <div style={{ marginBottom: "0.5rem" }}>
              Signed in as <strong style={{ color: "var(--sidebar-text-active)" }}>{user.username}</strong>
              {user.is_admin && " (admin)"}
            </div>
          )}
          <button className="sidebar-logout" onClick={logout}>
            Log out
          </button>
        </div>
      </nav>
    </>
  );
}
