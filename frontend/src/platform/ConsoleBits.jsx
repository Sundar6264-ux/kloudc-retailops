// Small shared pieces of the master console (status chip, feedback banner),
// the same look as the console's own (DESIGN_SYSTEM 1.4: colour + icon + word).
import Icon from "../ui/icons.jsx";

const CHIP_ICON = { good: "circle-check", warn: "triangle-alert", bad: "octagon-alert", info: "info", neutral: "circle", off: "circle" };

export function Chip({ chip }) {
  if (!chip) return null;
  return (
    <span className={`pf-chip tone-${chip.tone}`}>
      <Icon name={CHIP_ICON[chip.tone] || "circle"} size={12} />
      {chip.label}
    </span>
  );
}

export function Feedback({ message }) {
  if (!message) return null;
  return (
    <div className={`pf-banner ${message.tone || ""}`} role={message.tone === "bad" ? "alert" : "status"}>
      {message.text}
    </div>
  );
}

export function when(iso) {
  if (!iso) return "never";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
