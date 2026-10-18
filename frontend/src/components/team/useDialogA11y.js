import { useEffect, useRef } from "react";

// Keyboard basics for the Team drawers, dialogs and menus: Escape closes
// it, and focus goes back to whatever opened it (the button the user
// pressed) when it closes. Pass `active=false` to switch it off (e.g. a
// menu that's currently closed).
export default function useDialogA11y(onClose, active = true) {
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    if (!active) return undefined;
    const opener = document.activeElement;
    function onKey(e) {
      if (e.key === "Escape") {
        e.stopPropagation();
        closeRef.current?.();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      if (opener && typeof opener.focus === "function" && document.contains(opener)) {
        opener.focus();
      }
    };
  }, [active]);
}
