// Inline SVG icons for KloudC RetailOps (docs/DESIGN_SYSTEM.md 2): shapes
// copied from Lucide (https://lucide.dev), no npm package.
//
// Lucide licence (ISC):
// Copyright (c) for portions of Lucide are held by Cole Bemis 2013-2022 as
// part of Feather (MIT). All other copyright (c) for Lucide are held by
// Lucide Contributors 2022.
// Permission to use, copy, modify, and/or distribute this software for any
// purpose with or without fee is hereby granted, provided that the above
// copyright notice and this permission notice appear in all copies.
// THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
// WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
// MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY
// SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
// WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
// ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR
// IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

const p = (d) => ["path", { d }];
const c = (cx, cy, r) => ["circle", { cx, cy, r }];
const r = (x, y, width, height, rx = 2) => ["rect", { x, y, width, height, rx }];

export const ICONS = {
  "layout-dashboard": [r(3, 3, 7, 9, 1), r(14, 3, 7, 5, 1), r(14, 12, 7, 9, 1), r(3, 16, 7, 5, 1)],
  "clipboard-check": [r(8, 2, 8, 4, 1), p("M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"), p("m9 14 2 2 4-4")],
  "shopping-cart": [c(8, 21, 1), c(19, 21, 1), p("M2.05 2.05h2l2.66 12.42a2 2 0 0 0 2 1.58h9.78a2 2 0 0 0 1.95-1.57l1.65-7.43H5.12")],
  package: [
    p("M11 21.73a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73z"),
    p("M12 22V12"),
    p("m3.3 7 7.703 4.734a2 2 0 0 0 1.994 0L20.7 7"),
    p("m7.5 4.27 9 5.15"),
  ],
  "chart-line": [p("M3 3v16a2 2 0 0 0 2 2h16"), p("m19 9-5 5-4-4-3 3")],
  "chart-column": [p("M3 3v16a2 2 0 0 0 2 2h16"), p("M18 17V9"), p("M13 17V5"), p("M8 17v-3")],
  users: [p("M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"), c(9, 7, 4), p("M22 21v-2a4 4 0 0 0-3-3.87"), p("M16 3.13a4 4 0 0 1 0 7.75")],
  user: [p("M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"), c(12, 7, 4)],
  settings: [
    p(
      "M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z",
    ),
    c(12, 12, 3),
  ],
  "building-2": [
    p("M6 22V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v18Z"),
    p("M6 12H4a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2"),
    p("M18 9h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-2"),
    p("M10 6h4"),
    p("M10 10h4"),
    p("M10 14h4"),
    p("M10 18h4"),
  ],
  "life-buoy": [c(12, 12, 10), p("m4.93 4.93 4.24 4.24"), p("m14.83 9.17 4.24-4.24"), p("m14.83 14.83 4.24 4.24"), p("m9.17 14.83-4.24 4.24"), c(12, 12, 4)],
  search: [c(11, 11, 8), p("m21 21-4.3-4.3")],
  info: [c(12, 12, 10), p("M12 16v-4"), p("M12 8h.01")],
  "circle-help": [c(12, 12, 10), p("M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"), p("M12 17h.01")],
  menu: [p("M4 12h16"), p("M4 6h16"), p("M4 18h16")],
  x: [p("M18 6 6 18"), p("m6 6 12 12")],
  "chevron-right": [p("m9 18 6-6-6-6")],
  "chevron-down": [p("m6 9 6 6 6-6")],
  "chevron-left": [p("m15 18-6-6 6-6")],
  "chevrons-left": [p("m11 17-5-5 5-5"), p("m18 17-5-5 5-5")],
  "chevrons-right": [p("m6 17 5-5-5-5"), p("m13 17 5-5-5-5")],
  "log-out": [p("M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"), p("m16 17 5-5-5-5"), p("M21 12H9")],
  lock: [r(3, 11, 18, 11), p("M7 11V7a5 5 0 0 1 10 0v4")],
  sun: [
    c(12, 12, 4),
    p("M12 2v2"),
    p("M12 20v2"),
    p("m4.93 4.93 1.41 1.41"),
    p("m17.66 17.66 1.41 1.41"),
    p("M2 12h2"),
    p("M20 12h2"),
    p("m6.34 17.66-1.41 1.41"),
    p("m19.07 4.93-1.41 1.41"),
  ],
  moon: [p("M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z")],
  monitor: [r(2, 3, 20, 14), p("M8 21h8"), p("M12 17v4")],
  "book-open": [p("M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"), p("M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z")],
  book: [p("M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20")],
  mail: [r(2, 4, 20, 16), p("m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7")],
  shield: [
    p("M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"),
  ],
  "external-link": [p("M15 3h6v6"), p("M10 14 21 3"), p("M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6")],
  copy: [r(8, 8, 14, 14), p("M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2")],
  check: [p("M20 6 9 17l-5-5")],
  "circle-check": [c(12, 12, 10), p("m9 12 2 2 4-4")],
  "triangle-alert": [p("m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"), p("M12 9v4"), p("M12 17h.01")],
  "octagon-alert": [
    p("M12 16h.01"),
    p("M12 8v4"),
    p(
      "M15.312 2a2 2 0 0 1 1.414.586l4.688 4.688A2 2 0 0 1 22 8.688v6.624a2 2 0 0 1-.586 1.414l-4.688 4.688a2 2 0 0 1-1.414.586H8.688a2 2 0 0 1-1.414-.586l-4.688-4.688A2 2 0 0 1 2 15.312V8.688a2 2 0 0 1 .586-1.414l4.688-4.688A2 2 0 0 1 8.688 2z",
    ),
  ],
  circle: [c(12, 12, 10)],
  eye: [p("M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0"), c(12, 12, 3)],
  tag: [
    p("M12.586 2.586A2 2 0 0 0 11.172 2H4a2 2 0 0 0-2 2v7.172a2 2 0 0 0 .586 1.414l8.704 8.704a2.426 2.426 0 0 0 3.42 0l6.58-6.58a2.426 2.426 0 0 0 0-3.42z"),
    c(7.5, 7.5, 0.5),
  ],
  "scan-barcode": [
    p("M3 7V5a2 2 0 0 1 2-2h2"),
    p("M17 3h2a2 2 0 0 1 2 2v2"),
    p("M21 17v2a2 2 0 0 1-2 2h-2"),
    p("M7 21H5a2 2 0 0 1-2-2v-2"),
    p("M8 7v10"),
    p("M12 7v10"),
    p("M17 7v10"),
  ],
  calendar: [p("M8 2v4"), p("M16 2v4"), r(3, 4, 18, 18), p("M3 10h18")],
  "file-text": [p("M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"), p("M14 2v4a2 2 0 0 0 2 2h4"), p("M10 9H8"), p("M16 13H8"), p("M16 17H8")],
  truck: [
    p("M14 18V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v11a1 1 0 0 0 1 1h2"),
    p("M15 18H9"),
    p("M19 18h2a1 1 0 0 0 1-1v-3.65a1 1 0 0 0-.22-.624l-3.48-4.35A1 1 0 0 0 17.52 8H14"),
    c(17, 18, 2),
    c(7, 18, 2),
  ],
  wrench: [
    p("M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"),
  ],
  scale: [
    p("m16 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"),
    p("m2 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"),
    p("M7 21h10"),
    p("M12 3v18"),
    p("M3 7h2c2 0 5-1 7-2 2 1 5 2 7 2h2"),
  ],
  receipt: [
    p("M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1Z"),
    p("M16 8h-6a2 2 0 1 0 0 4h4a2 2 0 1 1 0 4H8"),
    p("M12 17.5v-11"),
  ],
  "list-checks": [p("m3 17 2 2 4-4"), p("m3 7 2 2 4-4"), p("M13 6h8"), p("M13 12h8"), p("M13 18h8")],
  "message-square": [p("M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z")],
  megaphone: [p("m3 11 18-5v12L3 14v-3z"), p("M11.6 16.8a3 3 0 1 1-5.8-1.6")],
  image: [r(3, 3, 18, 18), c(9, 9, 2), p("m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21")],
  plug: [p("M12 22v-5"), p("M9 8V2"), p("M15 8V2"), p("M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z")],
  layers: [
    p("M12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"),
    p("m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"),
    p("m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"),
  ],
  contact: [p("M16 2v2"), p("M7 22v-2a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v2"), p("M8 2v2"), c(12, 11, 3), r(3, 4, 18, 18)],
  flag: [p("M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"), p("M4 22v-7")],
  repeat: [p("m17 2 4 4-4 4"), p("M3 11v-1a4 4 0 0 1 4-4h14"), p("m7 22-4-4 4-4"), p("M21 13v1a4 4 0 0 1-4 4H3")],
  database: [["ellipse", { cx: 12, cy: 5, rx: 9, ry: 3 }], p("M3 5V19A9 3 0 0 0 21 19V5"), p("M3 12A9 3 0 0 0 21 12")],
  clock: [c(12, 12, 10), p("M12 6v6l4 2")],
  "trending-up": [p("M22 7 13.5 15.5 8.5 10.5 2 17"), p("M16 7h6v6")],
  upload: [p("M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"), p("m17 8-5-5-5 5"), p("M12 3v12")],
  plus: [p("M5 12h14"), p("M12 5v14")],
  "more-horizontal": [c(12, 12, 1), c(19, 12, 1), c(5, 12, 1)],
  "arrow-right": [p("M5 12h14"), p("m12 5 7 7-7 7")],
  history: [p("M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"), p("M3 3v5h5"), p("M12 7v5l4 2")],
  inbox: [
    p("M22 12h-6l-2 3h-4l-2-3H2"),
    p("M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"),
  ],
  server: [r(2, 2, 20, 8), r(2, 14, 20, 8), p("M6 6h.01"), p("M6 18h.01")],
  "scroll-text": [
    p("M15 12h-5"),
    p("M15 8h-5"),
    p("M19 17V5a2 2 0 0 0-2-2H4"),
    p("M8 21h12a2 2 0 0 0 2-2v-1a1 1 0 0 0-1-1H11a1 1 0 0 0-1 1v1a2 2 0 1 1-4 0V5a2 2 0 1 0-4 0v2a1 1 0 0 0 1 1h3"),
  ],
  store: [
    p("m2 7 4.41-4.41A2 2 0 0 1 7.83 2h8.34a2 2 0 0 1 1.42.59L22 7"),
    p("M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"),
    p("M15 22v-4a2 2 0 0 0-2-2h-2a2 2 0 0 0-2 2v4"),
    p("M2 7h20"),
    p("M22 7v3a2 2 0 0 1-2 2a2.7 2.7 0 0 1-1.59-.63.7.7 0 0 0-.82 0A2.7 2.7 0 0 1 16 12a2.7 2.7 0 0 1-1.59-.63.7.7 0 0 0-.82 0A2.7 2.7 0 0 1 12 12a2.7 2.7 0 0 1-1.59-.63.7.7 0 0 0-.82 0A2.7 2.7 0 0 1 8 12a2.7 2.7 0 0 1-1.59-.63.7.7 0 0 0-.82 0A2.7 2.7 0 0 1 4 12a2 2 0 0 1-2-2V7"),
  ],
  smartphone: [r(5, 2, 14, 20), p("M12 18h.01")],
  filter: [p("M22 3H2l8 9.46V19l4 2v-8.54L22 3z")],
  lightbulb: [
    p("M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5"),
    p("M9 18h6"),
    p("M10 22h4"),
  ],
  bell: [p("M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"), p("M10.3 21a1.94 1.94 0 0 0 3.4 0")],
  "layout-grid": [r(3, 3, 7, 7, 1), r(14, 3, 7, 7, 1), r(14, 14, 7, 7, 1), r(3, 14, 7, 7, 1)],
  "key-round": [
    p("M2.586 17.414A2 2 0 0 0 2 18.828V21a1 1 0 0 0 1 1h3a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h1a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h.172a2 2 0 0 0 1.414-.586l.814-.814a6.5 6.5 0 1 0-4-4z"),
    c(16.5, 7.5, 0.5),
  ],
  "trash-2": [p("M3 6h18"), p("M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"), p("M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"), p("M10 11v6"), p("M14 11v6")],
  pin: [
    p("M12 17v5"),
    p("M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"),
  ],
  "flame": [p("M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z")],
};

// <Icon name="search" size={18} />: decorative by default (aria-hidden);
// pass `label` when the icon alone carries meaning.
export default function Icon({ name, size = 18, strokeWidth = 2, label, className = "", style }) {
  const shapes = ICONS[name] || ICONS.circle;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`kc-icon${className ? ` ${className}` : ""}`}
      style={style}
      aria-hidden={label ? undefined : "true"}
      role={label ? "img" : undefined}
      aria-label={label}
      focusable="false"
    >
      {shapes.map(([tag, attrs], i) => {
        const Tag = tag;
        return <Tag key={i} {...attrs} />;
      })}
    </svg>
  );
}
