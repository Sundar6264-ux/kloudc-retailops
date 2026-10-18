import { useEffect, useRef } from "react";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), ' +
  'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

// Keyboard handling for a modal dialog or drawer: focus moves into it when it
// opens (to `initialFocus`, a selector, else the first control) and back to
// whatever had it when it closes; Tab stays inside; Escape calls onClose.
// Spread the result on the dialog element: <div {...dialog} role="dialog">.
// Escape stops here, so a dialog opened from another one closes on its own.
export default function useDialog(onClose, { initialFocus } = {}) {
  const ref = useRef(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    const previous = document.activeElement;
    const node = ref.current;
    if (node) {
      const target = (initialFocus && node.querySelector(initialFocus)) || node.querySelector(FOCUSABLE) || node;
      target.focus({ preventScroll: true });
    }
    return () => {
      if (previous && typeof previous.focus === "function" && document.contains(previous)) {
        previous.focus({ preventScroll: true });
      }
    };
    // Runs once per open; initialFocus is read on open only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function onKeyDown(e) {
    if (e.key === "Escape") {
      e.stopPropagation();
      onCloseRef.current?.();
      return;
    }
    if (e.key !== "Tab") return;
    e.stopPropagation(); // a dialog opened from this one handles its own Tab
    const node = ref.current;
    if (!node) return;
    const items = [...node.querySelectorAll(FOCUSABLE)].filter((el) => el.getClientRects().length > 0);
    if (items.length === 0) {
      e.preventDefault();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    if (e.shiftKey && (document.activeElement === first || document.activeElement === node)) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }

  return { ref, onKeyDown, tabIndex: -1 };
}
