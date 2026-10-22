// Where a `fixed` menu (ui/Menu.jsx) goes against the window: below the
// button when the list fits there, else above it when there's more room
// above (a Download menu near the bottom of a phone screen opened downward
// and PDF / CSV fell off-screen). When neither side fits, the list takes the
// bigger side and scrolls. Pure, so node tests can check it:
// node --test src/ui/menuPlacement.test.mjs
export const MENU_GAP = 6;
export const MENU_EDGE = 8;
// Used before the list has been measured (about three items with hints).
export const MENU_GUESS_HEIGHT = 180;

export function fixedMenuPlace(rect, view, align = "end", listHeight = MENU_GUESS_HEIGHT) {
  const need = (Number(listHeight) || MENU_GUESS_HEIGHT) + MENU_GAP + MENU_EDGE;
  const below = view.height - rect.bottom;
  const above = rect.top;
  const style = { position: "fixed" };
  const up = below < need && above > below;
  if (up) {
    style.top = "auto";
    style.bottom = view.height - rect.top + MENU_GAP;
    if (above < need) {
      style.maxHeight = Math.max(80, above - MENU_GAP - MENU_EDGE);
      style.overflowY = "auto";
    }
  } else {
    style.top = rect.bottom + MENU_GAP;
    if (below < need) {
      style.maxHeight = Math.max(80, below - MENU_GAP - MENU_EDGE);
      style.overflowY = "auto";
    }
  }
  if (align === "start") style.left = Math.max(MENU_EDGE, rect.left);
  else style.right = Math.max(MENU_EDGE, view.width - rect.right);
  return style;
}
