import {
  EMPTY_CLOSED_FILTERS,
  closedCategoryOptions,
  closedFilterCount,
  closedPersonOptions,
} from "./closedFiltersModel.js";

// Fix Issues › Closed: Employee (who closed it) and Category, in the same
// style as the open list's filters (FixIssueFilters). `rows` are the Closed
// tab's rows before these two filters, so each option shows how many it
// would leave.
export default function ClosedFilters({ rows, filters, onChange, shownCount }) {
  const people = closedPersonOptions(rows, filters);
  const categories = closedCategoryOptions(rows, filters);
  const set = (patch) => onChange({ ...filters, ...patch });
  const active = closedFilterCount(filters);

  return (
    <div className="fi-filters" role="group" aria-label="Filter the closed list">
      <select
        className="fi-filter-select"
        aria-label="Employee"
        value={filters.closedBy}
        onChange={(e) => set({ closedBy: e.target.value })}
      >
        <option value="">Everyone</option>
        {filters.closedBy && !people.some((p) => p.name.toLowerCase() === filters.closedBy.toLowerCase()) && (
          <option value={filters.closedBy}>Closed by {filters.closedBy} (0)</option>
        )}
        {people.map((p) => (
          <option key={p.name} value={p.name}>
            Closed by {p.name} ({p.count})
          </option>
        ))}
      </select>
      <select
        className="fi-filter-select"
        aria-label="Category"
        value={filters.category}
        onChange={(e) => set({ category: e.target.value })}
      >
        <option value="">All categories</option>
        {filters.category && !categories.some((c) => c.name.toLowerCase() === filters.category.toLowerCase()) && (
          <option value={filters.category}>{filters.category} (0)</option>
        )}
        {categories.map((c) => (
          <option key={c.name} value={c.name}>
            {c.name} ({c.count})
          </option>
        ))}
      </select>
      {active > 0 && shownCount != null && (
        <span className="muted fi-filter-count">
          {shownCount} of {rows.length} shown
        </span>
      )}
      {active > 0 && (
        <button type="button" className="link-button fi-filter-clear" onClick={() => onChange(EMPTY_CLOSED_FILTERS)}>
          Clear filters ({active})
        </button>
      )}
    </div>
  );
}
