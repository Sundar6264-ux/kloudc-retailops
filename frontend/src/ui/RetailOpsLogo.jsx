// RetailOps lockup for the sign-in page only (owner's pick 2026-10-22, option
// 2 "Bag check": a shopping bag with a tick; brand/retailops-logo-options).
// The rest of the product keeps KloudcLogo. `tone="light"` draws the white
// version for the blue band; `mark` draws the bag alone. Colours come from
// the KloudC tokens, so dark mode gets the lighter blue by itself.
const BAG = (
  <g>
    <path className="ro-logo-handle" d="M24 22 v-4 a8 8 0 0 1 16 0 v4" fill="none" strokeWidth="4.5" strokeLinecap="round" />
    <path className="ro-logo-bag" d="M12 22 h40 l-3.2 31 a5 5 0 0 1 -5 4.5 H20.2 a5 5 0 0 1 -5 -4.5 Z" />
    <path
      className="ro-logo-tick"
      d="M22.5 39 l7 7 l13 -13"
      fill="none"
      strokeWidth="5.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </g>
);

export default function RetailOpsLogo({ height = 40, tone = "brand", mark = false, className = "" }) {
  const cls = `ro-logo ro-logo-${tone}${className ? ` ${className}` : ""}`;
  if (mark) {
    return (
      <svg className={cls} viewBox="0 0 64 64" height={height} width={height} role="img" aria-label="RetailOps">
        {BAG}
      </svg>
    );
  }
  return (
    <svg className={cls} viewBox="0 0 290 64" height={height} width={(height * 290) / 64} role="img" aria-label="RetailOps">
      {BAG}
      <text x="72" y="46" className="ro-logo-word" fontWeight="800" fontSize="38">
        Retail<tspan className="ro-logo-ops">Ops</tspan>
      </text>
    </svg>
  );
}
