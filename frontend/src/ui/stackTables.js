// Wide tables become stacked cards under 768 px (website ready A6; spec 2.7).
// One shared pattern: a table with class `kc-table--stack` gets each body
// cell's column name as `data-label` (from its header row, colspans
// respected); styles/kc.css lays every row out as a card with "label: value"
// lines on phones, so nothing scrolls sideways. A cell's own data-label wins;
// data-label="" hides the label (e.g. an actions cell). Installed once in
// main.jsx; a MutationObserver labels tables as pages render them.

export const STACK_CLASS = "kc-table--stack";

// Header texts expanded by colspan: [["Item",1],["Qty",2]] -> ["Item","Qty","Qty"].
export function columnLabels(headers) {
  const out = [];
  for (const [text, span] of headers) {
    for (let i = 0; i < Math.max(1, span || 1); i += 1) out.push(String(text || "").replace(/\s+/g, " ").trim());
  }
  return out;
}

// The label of each cell of a row, given the cells' colspans.
export function cellLabels(labels, spans) {
  const out = [];
  let col = 0;
  for (const span of spans) {
    out.push(labels[col] ?? "");
    col += Math.max(1, span || 1);
  }
  return out;
}

export function labelTable(table) {
  const headRow = table.tHead?.rows?.[table.tHead.rows.length - 1];
  if (!headRow) return;
  const labels = columnLabels([...headRow.cells].map((c) => [c.getAttribute("aria-label") || c.textContent, c.colSpan]));
  for (const body of table.tBodies) {
    for (const row of body.rows) {
      const cells = [...row.cells];
      const names = cellLabels(labels, cells.map((c) => c.colSpan));
      cells.forEach((cell, i) => {
        if (cell.dataset.stackAuto === "1" || !cell.hasAttribute("data-label")) {
          cell.setAttribute("data-label", names[i]);
          cell.dataset.stackAuto = "1";
        }
      });
    }
  }
}

export function installTableStacking(root = typeof document !== "undefined" ? document : null) {
  if (!root || typeof MutationObserver === "undefined") return () => {};
  let queued = false;
  const run = () => {
    queued = false;
    root.querySelectorAll(`table.${STACK_CLASS}`).forEach(labelTable);
  };
  const schedule = () => {
    if (queued) return;
    queued = true;
    (typeof requestAnimationFrame === "function" ? requestAnimationFrame : setTimeout)(run);
  };
  const obs = new MutationObserver(schedule);
  obs.observe(root.body || root, { childList: true, subtree: true, characterData: true });
  schedule();
  return () => obs.disconnect();
}
