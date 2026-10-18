// A <th> that's clickable to sort by `sortKey`, with a ▲/▼ indicator when
// it's the active sort column. Pass the current sortKey/sortDir/toggleSort
// from useTableControls straight through.
export default function SortableTh({ children, sortKey, currentSortKey, sortDir, onSort, align }) {
  const active = sortKey === currentSortKey;
  return (
    <th
      className="sortable-th"
      style={align === "right" ? { textAlign: "right" } : undefined}
      onClick={() => onSort(sortKey)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onSort(sortKey);
      }}
    >
      {children}
      <span className={`sort-caret${active ? " sort-caret-active" : ""}`}>
        {active ? (sortDir === "asc" ? " ▲" : " ▼") : " ⇅"}
      </span>
    </th>
  );
}
