// The top bar (docs/DESIGN_SYSTEM.md 2.1): 56 px, sticky, on every store
// page. Left: ☰ (phone), the store's name and Store ID. Middle: search over
// pages, help and glossary (⌘K / Ctrl K / "/"). Right: the Purchase Order
// Cart (cart users), Help (?) for this page, and the user menu (password,
// appearance, help, contact, privacy, sign out).
import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { getCart } from "../api";
import { useAuth } from "../AuthContext.jsx";
import { ROUTES_BY_KEY, routeForPath, userMenuLinks } from "../featureRoutes.js";
import Icon from "../ui/icons.jsx";
import Menu, { MenuDivider, MenuItem, MenuLabel, MenuLink } from "../ui/Menu.jsx";
import { THEMES } from "../ui/theme.js";
import useTheme from "../ui/useTheme.js";
import { openPageHelp, useHelpMaybe } from "../ui/helpBridge.jsx";
import { initials, supportMailto, KLOUDC_LINKS } from "../ui/kloudc.js";
import CommandPalette from "./CommandPalette.jsx";
import StoreLogo from "./StoreLogo.jsx";
import { ROUTE_BADGES } from "./Sidebar.jsx";
import useTeamBadges from "./team/useTeamBadges.js";

function CartLink() {
  const navigate = useNavigate();
  const [count, setCount] = useState(0);
  useEffect(() => {
    const refresh = () =>
      getCart()
        .then((res) => setCount(res.total_items || 0))
        .catch(() => {});
    refresh();
    window.addEventListener("cart:updated", refresh);
    return () => window.removeEventListener("cart:updated", refresh);
  }, []);
  return (
    <button
      type="button"
      className="kc-icon-btn topbar-cart"
      data-area="ordering"
      onClick={() => navigate("/purchase-order")}
      aria-label={`Purchase order cart, ${count} item${count === 1 ? "" : "s"}`}
      title="Purchase Order Cart"
    >
      <Icon name="shopping-cart" size={18} />
      {count > 0 && <span className="kc-count-badge topbar-badge">{count > 99 ? "99+" : count}</span>}
    </button>
  );
}

function CopyStoreId({ code }) {
  const [copied, setCopied] = useState(false);
  if (!code) return null;
  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard blocked: the ID is on screen to copy by hand
    }
  }
  return (
    <button type="button" className="topbar-store-id" onClick={copy} title="Copy Store ID" aria-label={`Store ID ${code}. Copy`}>
      <span className="kc-mono">{code}</span>
      <Icon name={copied ? "check" : "copy"} size={13} />
    </button>
  );
}

export default function TopBar({ navOpen, onToggleNav }) {
  const { user, logout, hasFeature, store } = useAuth();
  const location = useLocation();
  const help = useHelpMaybe();
  const [theme, setTheme] = useTheme();
  const [searchOpen, setSearchOpen] = useState(false);
  const badges = useTeamBadges(!!user);

  // ⌘K / Ctrl K anywhere, "/" when not typing: open search.
  useEffect(() => {
    function onKey(e) {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target?.tagName) || e.target?.isContentEditable;
      if ((e.key === "k" || e.key === "K") && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setSearchOpen(true);
      } else if (e.key === "/" && !typing) {
        e.preventDefault();
        setSearchOpen(true);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // On a phone the counts are inside the closed menu, so ☰ shows a dot.
  const menuCount = Object.entries(ROUTE_BADGES).reduce((n, [key, badgeKey]) => {
    const route = ROUTES_BY_KEY[key];
    return route && (route.everyone || hasFeature(route.feature)) ? n + (badges[badgeKey] || 0) : n;
  }, 0);

  const here = routeForPath(location.pathname);
  const pageId =
    (help?.pageIdForPath && help.pageIdForPath(location.pathname)) ||
    here?.pageId ||
    (location.pathname.startsWith("/help") ? "help" : location.pathname.startsWith("/glossary") ? "glossary" : null);
  const role = user?.is_admin ? "Admin" : "Staff";
  const privacyUrl = store.legal?.privacyUrl || KLOUDC_LINKS.privacy;
  const termsUrl = store.legal?.termsUrl || null;
  const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || "");

  return (
    <header className="topbar" role="banner">
      <button
        type="button"
        className="kc-icon-btn sidebar-toggle"
        aria-label={navOpen ? "Close menu" : menuCount ? `Open menu (${menuCount} unread)` : "Open menu"}
        aria-expanded={navOpen}
        aria-controls="kc-sidebar"
        onClick={onToggleNav}
      >
        <Icon name={navOpen ? "x" : "menu"} size={20} />
        {!navOpen && menuCount > 0 && <span className="sidebar-toggle-dot" aria-hidden="true" />}
      </button>

      <div className="topbar-store">
        <span className="topbar-store-logo">
          <StoreLogo store={store} size={28} />
        </span>
        <span className="topbar-store-text">
          <span className="topbar-store-name">
            <span className="topbar-name-long">{store.name}</span>
            <span className="topbar-name-short">{store.shortName}</span>
          </span>
          <CopyStoreId code={store.code} />
        </span>
      </div>

      <button type="button" className="topbar-search" onClick={() => setSearchOpen(true)} aria-label="Search pages, help and terms" aria-keyshortcuts="Meta+K Control+K /">
        <Icon name="search" size={16} />
        <span className="topbar-search-text">Search pages, help and terms</span>
        <kbd className="topbar-kbd">{isMac ? "⌘K" : "Ctrl K"}</kbd>
      </button>

      <div className="topbar-actions">
        {hasFeature("purchase_order_cart") && <CartLink />}
        <button
          type="button"
          className="kc-icon-btn topbar-help"
          aria-label="Help for this page"
          title="Help for this page"
          onClick={() => openPageHelp(help, pageId)}
        >
          <Icon name="circle-help" size={18} />
        </button>
        <Menu
          label="Account menu"
          buttonClass="topbar-avatar-btn"
          testId="user-menu"
          button={
            <span className="topbar-avatar" aria-hidden="true">
              {initials(user?.username)}
            </span>
          }
        >
          {(close) => (
            <>
              <div className="kc-menu-head">
                <span className="topbar-avatar topbar-avatar-lg" aria-hidden="true">
                  {initials(user?.username)}
                </span>
                <span>
                  <strong className="kc-menu-name">{user?.username}</strong>
                  <span className="kc-menu-sub">
                    {role} · {store.shortName}
                  </span>
                </span>
              </div>
              <MenuDivider />
              <MenuItem
                icon="circle-help"
                testId="page-help"
                onSelect={() => {
                  close();
                  openPageHelp(help, pageId);
                }}
              >
                Help for this page
              </MenuItem>
              {userMenuLinks({ hasFeature }).map((l) => (
                <MenuLink key={l.key} icon={l.icon} to={l.path} onSelect={close}>
                  {l.label}
                </MenuLink>
              ))}
              <MenuLink icon="mail" href={supportMailto({ storeCode: store.code, page: here?.label })} onSelect={close}>
                Contact support
              </MenuLink>
              <MenuDivider />
              <MenuLabel>Appearance</MenuLabel>
              <div className="kc-menu-segment" role="group" aria-label="Appearance">
                {THEMES.map((t) => (
                  <MenuItem
                    key={t.value}
                    icon={t.value === "system" ? "monitor" : t.value === "light" ? "sun" : "moon"}
                    checked={theme === t.value}
                    onSelect={() => setTheme(t.value)}
                    testId={`theme-${t.value}`}
                  >
                    {t.label}
                  </MenuItem>
                ))}
              </div>
              <MenuDivider />
              <MenuLink icon="shield" href={privacyUrl} external onSelect={close}>
                Privacy policy
              </MenuLink>
              {termsUrl && (
                <MenuLink icon="file-text" href={termsUrl} external onSelect={close}>
                  Terms
                </MenuLink>
              )}
              <MenuItem
                icon="log-out"
                danger
                testId="sign-out"
                onSelect={() => {
                  close();
                  logout();
                }}
              >
                Sign out
              </MenuItem>
            </>
          )}
        </Menu>
      </div>
      {searchOpen && <CommandPalette onClose={() => setSearchOpen(false)} />}
    </header>
  );
}
