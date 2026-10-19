// Compact list header (docs/DESIGN_SYSTEM.md 2.5): ONE slim, frozen row -
// search, then icon buttons (filter with a count badge, settings, ⓘ) - and
// an optional row of sideways-scrolling chips. Explanatory text lives behind
// the ⓘ (`infoExtra`), not above the list.
import Icon from "./icons.jsx";
import { InfoButton } from "./helpBridge.jsx";

export default function ListHeader({
  search,
  onSearch,
  placeholder = "Search",
  filterCount = 0,
  onFilter,
  filterOpen,
  onSettings,
  pageId,
  section,
  infoExtra,
  chips,
  actions,
  sticky = true,
}) {
  return (
    <div className={`kc-list-header${sticky ? " is-sticky" : ""}`}>
      <div className="kc-list-header-row">
        {onSearch && (
          <label className="kc-search-field">
            <Icon name="search" size={16} />
            <input type="search" value={search} onChange={(e) => onSearch(e.target.value)} placeholder={placeholder} aria-label={placeholder} />
          </label>
        )}
        {actions}
        {onFilter && (
          <button type="button" className="kc-icon-btn" aria-label={filterCount ? `Filters (${filterCount} on)` : "Filters"} aria-expanded={filterOpen} onClick={onFilter}>
            <Icon name="filter" size={16} />
            {filterCount > 0 && <span className="kc-dot-badge">{filterCount}</span>}
          </button>
        )}
        {onSettings && (
          <button type="button" className="kc-icon-btn" aria-label="List settings" onClick={onSettings}>
            <Icon name="settings" size={16} />
          </button>
        )}
        {pageId && <InfoButton pageId={pageId} section={section} extra={infoExtra} />}
      </div>
      {chips && <div className="kc-list-chips">{chips}</div>}
    </div>
  );
}
