// KloudC RetailOps: apply the remembered Appearance (user menu) before the
// first paint, so a dark-theme user never sees a white flash. "system" (or
// nothing saved) leaves it to prefers-color-scheme (styles/tokens.css).
// A file, not an inline script: the pages' CSP allows script-src 'self' only.
(function () {
  try {
    var t = window.localStorage.getItem("kc.theme");
    if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
  } catch (e) {
    /* storage off: follow the system */
  }
})();
