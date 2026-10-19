import Icon from "./icons.jsx";

// Empty state (docs/DESIGN_SYSTEM.md 2.8): icon in a soft circle, a title,
// one sentence, an optional action.
export default function EmptyState({ icon = "inbox", title, children, action, area }) {
  return (
    <div className="kc-empty" data-area={area || undefined}>
      <span className="kc-empty-icon" aria-hidden="true">
        <Icon name={icon} size={24} />
      </span>
      {title && <p className="kc-empty-title">{title}</p>}
      {children && <p className="kc-empty-text">{children}</p>}
      {action && <div className="kc-empty-action">{action}</div>}
    </div>
  );
}
