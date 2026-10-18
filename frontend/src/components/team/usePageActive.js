import { useEffect, useState } from "react";

// Whether someone can actually be looking at this page. Team pollers skip
// ticks while the tab is hidden (and poll once as soon as it's shown again),
// and chat only marks messages read while the tab is visible AND focused -
// a chat left open in a background tab or on an unattended counter PC must
// never tell the sender "Seen".

export function isPageVisible() {
  return typeof document === "undefined" || document.visibilityState !== "hidden";
}

export function isPageActive() {
  if (!isPageVisible()) return false;
  return typeof document.hasFocus !== "function" || document.hasFocus();
}

// Calls `fn` whenever the tab becomes visible again. Returns the cleanup.
export function onPageShown(fn) {
  function handler() {
    if (isPageVisible()) fn();
  }
  document.addEventListener("visibilitychange", handler);
  return () => document.removeEventListener("visibilitychange", handler);
}

// Re-renders on visibility and window focus changes; `true` while the page
// is visible and focused.
export default function usePageActive() {
  const [active, setActive] = useState(isPageActive);

  useEffect(() => {
    // Focus can move within the page (focus/blur fire on window only when
    // it leaves or returns), so re-read the real state on every event.
    const update = () => setActive(isPageActive());
    document.addEventListener("visibilitychange", update);
    window.addEventListener("focus", update);
    window.addEventListener("blur", update);
    return () => {
      document.removeEventListener("visibilitychange", update);
      window.removeEventListener("focus", update);
      window.removeEventListener("blur", update);
    };
  }, []);

  return active;
}
