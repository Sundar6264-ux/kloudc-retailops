// KloudC RetailOps lockup as inline SVG (brand/kloudc-retailops/logo.svg):
// the paper plane + "KloudC" + "RetailOps". `tone="light"` draws the white
// version for the blue gradient bands; `mark` draws the plane alone.
const PLANE = (
  <g strokeLinecap="round" strokeLinejoin="round">
    <g stroke="var(--kc-logo-lines)" strokeWidth="22" fill="none">
      <path d="M150 640 L170 628 M215 602 L245 584 M290 556 L430 470" />
      <path d="M110 760 L130 748 M180 720 L215 699 M265 669 L470 548" />
      <path d="M300 740 L320 728 M370 700 L405 679 M455 649 L560 586" />
    </g>
    <path d="M290 372 L936 238 L462 478 Z" fill="var(--kc-logo-sky)" stroke="var(--kc-logo-edge)" strokeWidth="10" />
    <path d="M462 478 L936 238 L510 660 Z" fill="var(--kc-logo-blue)" stroke="var(--kc-logo-edge)" strokeWidth="10" />
    <path d="M560 540 L936 238 L752 622 Z" fill="var(--kc-logo-sky)" stroke="var(--kc-logo-edge)" strokeWidth="10" />
  </g>
);

export default function KloudcLogo({ height = 40, tone = "brand", mark = false, className = "" }) {
  const cls = `kc-logo kc-logo-${tone}${className ? ` ${className}` : ""}`;
  if (mark) {
    return (
      <svg className={cls} viewBox="80 200 900 600" height={height} width={(height * 900) / 600} role="img" aria-label="KloudC">
        {PLANE}
      </svg>
    );
  }
  return (
    <svg className={cls} viewBox="0 60 1200 320" height={height} width={(height * 1200) / 320} role="img" aria-label="KloudC RetailOps">
      <g transform="scale(0.41)">{PLANE}</g>
      <text x="420" y="250" className="kc-logo-word" fontWeight="800" fontSize="190">
        KloudC
      </text>
      <text x="426" y="350" className="kc-logo-sub" fontWeight="700" fontSize="84" letterSpacing="2">
        RetailOps
      </text>
    </svg>
  );
}
