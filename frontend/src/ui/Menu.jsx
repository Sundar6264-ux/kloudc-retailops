// A small dropdown menu (user menu, store menu, row ⋯): a button that opens
// a popover list; Esc / outside click closes and focus goes back to the
// button; arrow keys move between items. `children(close)` renders the
// items (MenuItem / MenuLink / MenuLabel / MenuDivider).
// `fixed`: the list is placed against the window (position: fixed) instead of
// the button's box, so a menu inside a scrolling table isn't cut off; it
// opens upward when there's no room below (measured once the list is
// drawn, ui/menuPlacement.js) and closes on scroll.
import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import Icon from "./icons.jsx";
import { fixedMenuPlace } from "./menuPlacement.js";

const viewport = () => ({ width: window.innerWidth, height: window.innerHeight });

export default function Menu({
  button,
  buttonClass = "kc-icon-btn",
  label,
  align = "end",
  children,
  className = "",
  testId,
  fixed = false,
}) {
  const [open, setOpen] = useState(false);
  const [place, setPlace] = useState(null);
  const wrap = useRef(null);
  const btn = useRef(null);
  const listRef = useRef(null);
  const id = useId();

  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e) => {
      if (wrap.current && !wrap.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === "Escape") {
        setOpen(false);
        btn.current?.focus();
      }
    };
    const onScroll = (e) => {
      if (listRef.current && listRef.current.contains(e.target)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    if (fixed) {
      window.addEventListener("scroll", onScroll, true);
      window.addEventListener("resize", onScroll);
    }
    // First item gets focus so the keyboard can walk the list.
    const first = listRef.current?.querySelector('[role="menuitem"], [role="menuitemradio"]');
    first?.focus();
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onScroll);
    };
  }, [open, fixed]);

  // Once the list is drawn, place it again with its real height: a longer
  // list than the first guess flips upward too.
  useLayoutEffect(() => {
    if (!open || !fixed || !btn.current || !listRef.current) return;
    const h = listRef.current.scrollHeight;
    if (!h) return;
    const next = fixedMenuPlace(btn.current.getBoundingClientRect(), viewport(), align, h);
    setPlace((prev) => (JSON.stringify(prev) === JSON.stringify(next) ? prev : next));
  }, [open, fixed, align]);

  function toggle() {
    if (!open && fixed && btn.current) {
      setPlace(fixedMenuPlace(btn.current.getBoundingClientRect(), viewport(), align));
    }
    setOpen((v) => !v);
  }

  function onListKey(e) {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
    const items = [...listRef.current.querySelectorAll('[role="menuitem"], [role="menuitemradio"]')];
    const i = items.indexOf(document.activeElement);
    let n = i;
    if (e.key === "ArrowDown") n = (i + 1) % items.length;
    if (e.key === "ArrowUp") n = (i - 1 + items.length) % items.length;
    if (e.key === "Home") n = 0;
    if (e.key === "End") n = items.length - 1;
    e.preventDefault();
    items[n]?.focus();
  }

  const close = () => setOpen(false);
  return (
    <div className={`kc-menu${className ? ` ${className}` : ""}`} ref={wrap}>
      <button
        type="button"
        ref={btn}
        className={buttonClass}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        aria-label={label}
        onClick={toggle}
        data-testid={testId}
      >
        {button}
      </button>
      {open && (
        <div
          className={`kc-popover kc-menu-list kc-menu-${align}`}
          role="menu"
          id={id}
          aria-label={label}
          ref={listRef}
          onKeyDown={onListKey}
          style={fixed && place ? place : undefined}
        >
          {children(close)}
        </div>
      )}
    </div>
  );
}

export function MenuItem({ icon, children, onSelect, danger = false, checked, hint, testId }) {
  const role = checked === undefined ? "menuitem" : "menuitemradio";
  return (
    <button
      type="button"
      role={role}
      aria-checked={checked === undefined ? undefined : checked}
      tabIndex={-1}
      className={`kc-menu-item${danger ? " is-danger" : ""}`}
      onClick={onSelect}
      data-testid={testId}
    >
      {icon && <Icon name={icon} size={16} />}
      <span className="kc-menu-item-text">{children}</span>
      {hint && <span className="kc-menu-hint">{hint}</span>}
      {checked && <Icon name="check" size={16} className="kc-menu-check" />}
    </button>
  );
}

export function MenuLink({ icon, to, href, children, onSelect, external = false }) {
  const inner = (
    <>
      {icon && <Icon name={icon} size={16} />}
      <span className="kc-menu-item-text">{children}</span>
      {external && <Icon name="external-link" size={14} className="kc-menu-ext" />}
    </>
  );
  if (href) {
    return (
      <a role="menuitem" tabIndex={-1} className="kc-menu-item" href={href} target={external ? "_blank" : undefined} rel={external ? "noopener noreferrer" : undefined} onClick={onSelect}>
        {inner}
      </a>
    );
  }
  return (
    <Link role="menuitem" tabIndex={-1} className="kc-menu-item" to={to} onClick={onSelect}>
      {inner}
    </Link>
  );
}

export function MenuLabel({ children }) {
  return <div className="kc-menu-label">{children}</div>;
}

export function MenuDivider() {
  return <div className="kc-menu-divider" role="separator" />;
}
