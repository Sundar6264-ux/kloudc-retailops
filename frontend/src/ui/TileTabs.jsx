// Tile tabs (docs/DESIGN_SYSTEM.md 2.6): a row of hub tiles acting as tabs -
// one screen, the chosen tile highlighted in the area colour, arrow keys move
// between them. `items`: [{ key, label, sub?, icon?, count?, to? }]. With
// `to`, the tiles are links (the Insights hub: each page keeps its address).
import { useRef } from "react";
import { Link } from "react-router-dom";
import Icon from "./icons.jsx";

export default function TileTabs({ items, value, onChange, label, area, compact = false, className = "" }) {
  const refs = useRef([]);
  function onKeyDown(e, i) {
    const n = items.length;
    let next = null;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (i + 1) % n;
    if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = (i - 1 + n) % n;
    if (e.key === "Home") next = 0;
    if (e.key === "End") next = n - 1;
    if (next === null) return;
    e.preventDefault();
    refs.current[next]?.focus();
    if (!items[next].to) onChange?.(items[next].key);
  }
  return (
    <div
      className={`kc-tile-tabs${compact ? " kc-tile-tabs-compact" : ""}${className ? ` ${className}` : ""}`}
      role="tablist"
      aria-label={label}
      data-area={area || undefined}
    >
      {items.map((it, i) => {
        const selected = it.key === value;
        const body = (
          <>
            {it.icon && (
              <span className="kc-tile-icon" aria-hidden="true">
                <Icon name={it.icon} size={compact ? 16 : 18} />
              </span>
            )}
            <span className="kc-tile-text">
              <span className="kc-tile-label">{it.label}</span>
              {it.sub && !compact && <span className="kc-tile-sub">{it.sub}</span>}
            </span>
            {it.count > 0 && <span className="kc-count-badge">{it.count > 99 ? "99+" : it.count}</span>}
          </>
        );
        const common = {
          key: it.key,
          role: "tab",
          "aria-selected": selected,
          tabIndex: selected ? 0 : -1,
          className: `kc-tile-tab${selected ? " is-selected" : ""}`,
          ref: (el) => (refs.current[i] = el),
          onKeyDown: (e) => onKeyDown(e, i),
          "data-area": it.area || undefined,
        };
        return it.to ? (
          <Link {...common} to={it.to} onClick={() => onChange?.(it.key)}>
            {body}
          </Link>
        ) : (
          <button {...common} type="button" onClick={() => onChange?.(it.key)}>
            {body}
          </button>
        );
      })}
    </div>
  );
}
