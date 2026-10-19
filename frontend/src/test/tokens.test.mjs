// Design tokens (docs/DESIGN_SYSTEM.md 1, 5.4): every documented text /
// background pair reaches WCAG AA (4.5:1 text, 3:1 UI parts) in BOTH
// themes, the dark block for "System" matches the explicit Dark block, a
// store brand of any colour (the worst cases: yellow #FFD400, near-black
// #111111) stays readable, and no new raw colour sneaks into the CSS or
// components outside the token file.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { brandCssVars, contrastRatio, hexToRgb } from "../store/branding.js";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..");
const TOKENS = readFileSync(join(SRC, "styles/tokens.css"), "utf8");

function block(css, opener) {
  const at = css.indexOf(opener);
  assert.ok(at >= 0, `block ${opener}`);
  const start = css.indexOf("{", at) + 1;
  let depth = 1;
  let i = start;
  while (depth) {
    if (css[i] === "{") depth++;
    if (css[i] === "}") depth--;
    i++;
  }
  return css.slice(start, i - 1);
}

function vars(body) {
  const out = {};
  for (const m of body.matchAll(/(--[a-z0-9-]+):\s*([^;]+);/g)) out[m[1]] = m[2].trim();
  return out;
}

const LIGHT = vars(block(TOKENS, ":root {"));
const DARK_SYSTEM = vars(block(block(TOKENS, "@media (prefers-color-scheme: dark)"), ':root:not([data-theme="light"])'));
const DARK_EXPLICIT = vars(block(TOKENS, ':root[data-theme="dark"]'));
const DARK = { ...LIGHT, ...DARK_EXPLICIT };

function resolve(theme, name, depth = 0) {
  let v = theme[name];
  assert.ok(v !== undefined, `token ${name}`);
  for (let i = 0; i < 10 && /var\(/.test(v); i++) {
    v = v.replace(/var\((--[a-z0-9-]+)(?:,\s*([^)]+))?\)/, (_, n, fb) => (theme[n] !== undefined ? resolve(theme, n, depth + 1) : fb));
  }
  return v;
}

// An opaque hex for a token; rgba() is laid over `base` (its surface).
function color(theme, name, base = "--surface") {
  const v = resolve(theme, name);
  if (/^#[0-9a-f]{6}$/i.test(v)) return v;
  const m = /^rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)$/.exec(v);
  assert.ok(m, `${name} = ${v}`);
  const under = hexToRgb(color(theme, base));
  const a = Number(m[4]);
  const mixed = [1, 2, 3].map((k, i) => Math.round(Number(m[k]) * a + under[i] * (1 - a)));
  return `#${mixed.map((x) => x.toString(16).padStart(2, "0")).join("")}`;
}

function pairs(theme) {
  const p = [];
  const text = (fg, bgs, min = 4.5) => bgs.forEach((bg) => p.push([fg, bg, min]));
  text("--text", ["--bg", "--surface", "--surface-2", "--surface-3"]);
  text("--text-2", ["--bg", "--surface", "--surface-2", "--surface-3"]);
  text("--text-muted", ["--bg", "--surface", "--surface-2", "--surface-3"]);
  text("--kc-link", ["--bg", "--surface", "--surface-2"]);
  for (const a of ["home", "ordering", "stock", "sales", "team", "setup", "platform", "help"]) {
    text(`--area-${a}`, ["--surface", `--area-${a}-soft`]);
  }
  for (const s of ["success", "warning", "danger", "info", "violet", "neutral"]) {
    text(`--${s}-text`, ["--surface", `--${s}-soft`]);
  }
  text("--nav-text", ["--nav-bg", "--nav-bg-2"]);
  text("--nav-muted", ["--nav-bg", "--nav-bg-2"]);
  for (const a of ["home", "ordering", "stock", "sales", "team", "setup"]) text(`--area-${a}-nav`, ["--nav-bg"], 4.5);
  text("--on-fill", ["--success-fill", "--danger-fill", "--warning-fill", "--info-fill", "--neutral-fill", "--kc-blue-fill"]);
  text("--toast-text", ["--toast-bg"]);
  text("--inverse-text", ["--inverse-bg"]);
  // UI parts: 3:1.
  text("--input-border", ["--surface", "--bg"], 3);
  text("--focus", ["--surface", "--bg"], 3);
  return p;
}

for (const [label, theme] of [
  ["light", LIGHT],
  ["dark", DARK],
]) {
  test(`contrast: every documented pair passes AA (${label})`, () => {
    const bad = [];
    for (const [fg, bg, min] of pairs(theme)) {
      const base = bg.startsWith("--nav") ? "--nav-bg" : "--surface";
      const back = color(theme, bg, base);
      const front = color(theme, fg, bg);
      const r = contrastRatio(front, back);
      if (r < min) bad.push(`${fg} on ${bg}: ${r.toFixed(2)} < ${min}`);
    }
    assert.deepEqual(bad, []);
  });
}

test("dark: the System block and the explicit Dark block are the same", () => {
  assert.deepEqual(DARK_SYSTEM, DARK_EXPLICIT);
});

test("store brand colours stay readable in both themes (worst cases included)", () => {
  const dark = color(DARK, "--surface");
  for (const hex of ["#004BAD", "#FFD400", "#111111", "#0F766E", "#DC2626", "#7C3AED", "#F97316", "#FFFFFF", "#22C55E"]) {
    const v = brandCssVars(hex, "#2EA3F2");
    assert.ok(contrastRatio(v["--accent"], "#FFFFFF") >= 4.5, `${hex}: white on light fill`);
    assert.ok(contrastRatio(v["--accent-dark"], "#FFFFFF") >= 4.5, `${hex}: white on dark fill`);
    assert.ok(contrastRatio(v["--brand-text-light"], "#FFFFFF") >= 4.5, `${hex}: brand text on white`);
    assert.ok(contrastRatio(v["--brand-text-dark"], dark) >= 4.5, `${hex}: brand text on the dark surface`);
  }
  // The product's own (KloudC blue) uses the documented dark values.
  const product = brandCssVars("#004BAD", "#2EA3F2");
  assert.equal(product["--accent-dark"], "#1F6FD1");
  assert.equal(product["--brand-text-dark"], "#6CB6F7");
});

// ---- no raw colours outside the token file ----

function files(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) files(p, out);
    else if (/\.(css|jsx|js)$/.test(name) && !/\.test\.|test\/fixtures/.test(p)) out.push(p);
  }
  return out;
}

// Data palettes (chart series, the heat ramp, team colours people pick) and
// the brand maths are colours as DATA, not styling.
const ALLOWED = new Set([
  "styles/tokens.css",
  "store/branding.js",
  "components/insights/insightsModel.js",
  "pages/TeamsPage.jsx",
]);

test("no raw hex colour outside styles/tokens.css (and the data palettes)", () => {
  const hits = [];
  for (const p of files(SRC)) {
    const rel = relative(SRC, p);
    if (ALLOWED.has(rel) || rel.startsWith("ui/help/")) continue;
    const text = readFileSync(p, "utf8");
    text.split("\n").forEach((line, i) => {
      // Comments may name colours.
      const code = line.replace(/\/\*.*?\*\/|\/\/.*$/g, "");
      if (/(^|[^&\w])#[0-9a-fA-F]{6}\b|(^|[\s:(,])#[0-9a-fA-F]{3}\b(?![-\w])/.test(code)) hits.push(`${rel}:${i + 1}: ${line.trim()}`);
    });
  }
  assert.deepEqual(hits, []);
});
