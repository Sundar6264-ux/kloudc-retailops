// The contrast + overflow check of audit.mjs, shared with website_ready_a.mjs.
// Runs inside the page (page.evaluate(auditPage)): WCAG AA ratios for every
// visible text, and controls poking out of the viewport sideways.
export function auditPage() {
  const parse = (c) => {
    const m = c.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  };
  const lum = ({ r, g, b }) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const blend = (top, bot) => ({ r: top.r * top.a + bot.r * (1 - top.a), g: top.g * top.a + bot.g * (1 - top.a), b: top.b * top.a + bot.b * (1 - top.a), a: 1 });
  const bgOf = (el) => {
    const stack = [];
    for (let e = el; e; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.backgroundImage && cs.backgroundImage !== "none") return null; // gradient/image: skip
      const c = parse(cs.backgroundColor);
      if (c && c.a > 0) { stack.push(c); if (c.a >= 1) break; }
    }
    let base = { r: 255, g: 255, b: 255, a: 1 };
    const bodyBg = parse(getComputedStyle(document.body).backgroundColor);
    if (bodyBg && bodyBg.a >= 1) base = bodyBg;
    for (let i = stack.length - 1; i >= 0; i--) base = stack[i].a >= 1 ? stack[i] : blend(stack[i], base);
    return base;
  };
  const out = [];
  const seen = new Set();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const t = n.textContent.trim();
    if (!t) continue;
    const el = n.parentElement;
    if (!el || seen.has(el)) continue;
    seen.add(el);
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1 || r.bottom < 0 || r.top > innerHeight * 3) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === "hidden" || +cs.opacity === 0) continue;
    if (el.closest("[disabled], [aria-disabled=true], .disabled, option")) continue;
    if (el.closest("button:disabled, input:disabled")) continue;
    const fg = parse(cs.color); const bg = bgOf(el);
    if (!fg || !bg) continue;
    let eff = fg.a < 1 ? blend(fg, bg) : fg;
    let op = 1; for (let e = el; e; e = e.parentElement) op *= +getComputedStyle(e).opacity;
    if (op < 0.6) continue; // intentionally faded (disabled look)
    const L1 = lum(eff), L2 = lum(bg);
    const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    const size = parseFloat(cs.fontSize); const bold = +cs.fontWeight >= 700;
    const need = size >= 24 || (size >= 18.66 && bold) ? 3 : 4.5;
    if (ratio < need - 0.05) {
      const cls = (el.className && typeof el.className === "string" ? "." + el.className.trim().split(/\s+/).join(".") : "");
      out.push(`${ratio.toFixed(2)} ${el.tagName.toLowerCase()}${cls} "${t.slice(0, 40)}" fg=${cs.color} `);
    }
  }
  // elements poking out of the viewport sideways
  const over = [];
  for (const el of document.querySelectorAll("button, a, input, select, .card, table")) {
    const r = el.getBoundingClientRect();
    if (r.width && r.right > innerWidth + 1 && !el.closest("[style*=overflow], .table-wrap, .table-scroll")) {
      let clipped = false;
      for (let e = el.parentElement; e; e = e.parentElement) { const o = getComputedStyle(e).overflowX; if (o === "auto" || o === "scroll" || o === "hidden") { clipped = true; break; } }
      if (!clipped) over.push(`${el.tagName.toLowerCase()}.${(el.className || "").toString().split(" ")[0]} "${(el.textContent || "").trim().slice(0, 30)}" right=${Math.round(r.right)}`);
    }
  }
  return { low: out, over };
}
