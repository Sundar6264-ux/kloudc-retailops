import { AREAS } from "../featureRoutes.js";

// "● Ordering": where this page lives (identity colour, never a status).
export default function AreaChip({ area, label }) {
  if (!area || !AREAS[area]) return null;
  return (
    <span className="kc-area-chip" data-area={area}>
      <span className="kc-area-dot" aria-hidden="true" />
      {label || AREAS[area].label}
    </span>
  );
}
