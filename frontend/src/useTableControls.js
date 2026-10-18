import { useMemo, useState } from "react";
import { DEFAULT_CODE_KEYS, rowCodeMatch } from "./productMatch.js";

// Shared client-side search + click-to-sort behavior for a plain array of
// row objects, so every table tab gets the same interaction instead of each
// component reinventing it. Search is a simple case-insensitive substring
// match across whichever field names are passed in `searchKeys`.
//
// `codeKeys` (product tables, Oct 14 W2): also match a row by its product
// codes - Toast item id, any vendor SKU, barcode - with the shared rule in
// productMatch.js. `true` = the usual fields (DEFAULT_CODE_KEYS); or pass
// { itemId, supplier, barcode } lists of field names / getter functions.
export function useTableControls(
  rows,
  { searchKeys = [], codeKeys = null, defaultSortKey = null, defaultSortDir = "asc" } = {}
) {
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState(defaultSortKey);
  const [sortDir, setSortDir] = useState(defaultSortDir);

  function toggleSort(key) {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  }

  const result = useMemo(() => {
    let out = rows || [];

    if (search.trim() && (searchKeys.length > 0 || codeKeys)) {
      const needle = search.trim().toLowerCase();
      const keys = codeKeys === true ? DEFAULT_CODE_KEYS : codeKeys;
      out = out.filter(
        (row) =>
          searchKeys.some((key) => String(row?.[key] ?? "").toLowerCase().includes(needle)) ||
          (keys ? rowCodeMatch(row, search, keys) !== null : false)
      );
    }

    if (sortKey) {
      out = [...out].sort((a, b) => {
        const av = a?.[sortKey];
        const bv = b?.[sortKey];
        if (av == null && bv == null) return 0;
        if (av == null) return 1;
        if (bv == null) return -1;
        if (typeof av === "number" && typeof bv === "number") {
          return sortDir === "asc" ? av - bv : bv - av;
        }
        const cmp = String(av).localeCompare(String(bv), undefined, { numeric: true });
        return sortDir === "asc" ? cmp : -cmp;
      });
    }

    return out;
  }, [rows, search, searchKeys, codeKeys, sortKey, sortDir]);

  return { search, setSearch, sortKey, sortDir, toggleSort, rows: result };
}
