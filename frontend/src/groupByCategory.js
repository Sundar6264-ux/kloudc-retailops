// Shared "group a list by category, biggest first, bury the long tail"
// behavior for any dashboard list/report that has a `category` field -
// reused by Fix Issues and Inventory Reports rather than each building its
// own grouping logic. Deliberately pure/framework-free so it's easy to unit
// reason about and reuse outside React later if needed.
//
// Categories are ranked by how many *currently visible* (already
// searched/sorted) rows they have - the store's own priority signal, not a
// hardcoded category list, so it stays correct as inventory changes. A
// category with `minorThreshold` items or fewer is folded into one
// "Other categories" bucket instead of getting its own section, so a long
// tail of one-off categories doesn't bury the categories that actually
// matter - but nothing is ever dropped, just deprioritized and collapsible.
//
// Each group is capped independently (`perGroupLimit`, overridable per
// category via `groupLimits`) rather than one limit shared across the whole
// list - a single global cap would mean a page-one list is entirely eaten by
// whichever one category happens to be biggest (real data: "Dead inventory"
// has 2,631 items and its top category alone has 426 - a flat 200-row cap
// would show only that one category and never reveal the other 32 exist).
// Independent per-category caps mean every major category is visible on the
// very first render, which is the actual point of grouping by priority.
export function groupByCategory(
  rows,
  { categoryKey = "category", minorThreshold = 2, minRowsToGroup = 10, perGroupLimit = 20, groupLimits = {} } = {}
) {
  if (!rows || rows.length < minRowsToGroup) {
    return { grouped: false, groups: null, shown: rows || [] };
  }

  const byCategory = new Map();
  for (const row of rows) {
    const cat = row[categoryKey] || "Uncategorized";
    if (!byCategory.has(cat)) byCategory.set(cat, []);
    byCategory.get(cat).push(row);
  }

  const entries = [...byCategory.entries()];
  const major = entries
    .filter(([, items]) => items.length > minorThreshold)
    .sort((a, b) => b[1].length - a[1].length)
    .map(([category, items]) => ({ category, items, isOther: false }));
  const minorEntries = entries.filter(([, items]) => items.length <= minorThreshold);
  const minorItems = minorEntries.flatMap(([, items]) => items);

  const orderedGroups = [...major];
  if (minorItems.length > 0) {
    orderedGroups.push({
      category: "Other categories",
      items: minorItems,
      isOther: true,
      minorCategoryCount: minorEntries.length,
    });
  }

  const groups = orderedGroups.map((g) => {
    const cap = groupLimits[g.category] ?? perGroupLimit;
    return { ...g, items: g.items.slice(0, cap), totalInGroup: g.items.length };
  });

  return { grouped: true, groups, shown: groups.flatMap((g) => g.items) };
}
