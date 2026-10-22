// RetailOps lockup for the sign-in page only (owner's pick 2026-10-22,
// "Plane-R": a white geometric R whose bowl is the KloudC paper plane, with
// a sky-blue fold, on KloudC blue - the same mark as the app icon and the
// favicon; source brand/retailops-icon-concepts/kloudc-2.svg). The rest of
// the product keeps KloudcLogo. `tone="brand"` draws the mark on its blue
// tile (as the app icon); `tone="light"` draws the R alone in white for the
// blue band; `mark` draws the mark without the "RetailOps" word. Colours
// come from the KloudC tokens.
import { useId } from "react";

// The R in the source's 1024 x 1024 space (its translate(-20 0) applied).
const R_SHAPES = (
  <>
    {/* stem + upper wing of the plane, one white sheet */}
    <path className="ro-logo-r" d="M194 180 L340 180 L830 400 L340 452 L340 844 L194 844 Z" />
    {/* folded lower wing */}
    <polygon className="ro-logo-fold" points="340,452 830,400 340,640" />
    {/* leg */}
    <polygon className="ro-logo-r" points="456,583 602,512 814,844 648,844" />
  </>
);

function Mark({ tone }) {
  const gid = `ro-tile-${useId().replace(/:/g, "")}`;
  if (tone === "light") {
    // The R alone, 48 of the 64 units tall, centred.
    return <g transform="translate(-5 -5) scale(0.0723)">{R_SHAPES}</g>;
  }
  // The app icon: the R on a rounded KloudC-blue tile.
  return (
    <g>
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" className="ro-logo-tile-top" />
          <stop offset="1" className="ro-logo-tile-bottom" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="14" fill={`url(#${gid})`} />
      <g transform="scale(0.0625)">{R_SHAPES}</g>
    </g>
  );
}

export default function RetailOpsLogo({ height = 40, tone = "brand", mark = false, className = "" }) {
  const cls = `ro-logo ro-logo-${tone}${className ? ` ${className}` : ""}`;
  if (mark) {
    return (
      <svg className={cls} viewBox="0 0 64 64" height={height} width={height} role="img" aria-label="RetailOps">
        <Mark tone={tone} />
      </svg>
    );
  }
  return (
    <svg className={cls} viewBox="0 0 290 64" height={height} width={(height * 290) / 64} role="img" aria-label="RetailOps">
      <Mark tone={tone} />
      <text x="76" y="46" className="ro-logo-word" fontWeight="800" fontSize="38">
        Retail<tspan className="ro-logo-ops">Ops</tspan>
      </text>
    </svg>
  );
}
