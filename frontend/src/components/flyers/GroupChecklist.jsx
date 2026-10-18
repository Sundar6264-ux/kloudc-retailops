import { Term } from "../../glossary.jsx";
import { shortDay, statusInfo } from "./flyerModel.js";

// The marketing WhatsApp groups a flyer can go to (only groups an admin has
// marked "Use for flyers", so a flyer can never reach a staff group by
// accident). `latestByGroup` (target_key -> newest send) labels groups this
// flyer already went to; a group with a send still going can't be picked.
export default function GroupChecklist({
  idPrefix,
  groups,
  selected,
  onChange,
  latestByGroup,
  disabled,
  problem,
  isAdmin,
  onManageGroups,
  legend = "Send to WhatsApp groups",
}) {
  const problemId = `${idPrefix}-groups-problem`;
  const pickable = groups.filter((g) => !isBusy(latestByGroup?.[g.key]));

  if (groups.length === 0) {
    return (
      <div className="fly-groups-empty" role="note">
        <p className="fly-groups-empty-title">
          No <Term k="marketing_group">marketing groups</Term> yet
        </p>
        {isAdmin ? (
          <p>
            Turn on <strong>Use for flyers</strong> for the customer groups in{" "}
            <button type="button" className="link-button fly-inline-link" onClick={onManageGroups}>
              Manage WhatsApp groups
            </button>
            . Staff groups stay off, so a flyer never goes to them by accident.
          </p>
        ) : (
          <p>
            Ask an admin to turn on <strong>Use for flyers</strong> for the customer WhatsApp groups (Checklist builder →
            WhatsApp groups). You can still save flyers to the library.
          </p>
        )}
      </div>
    );
  }

  function toggle(key, on) {
    const next = new Set(selected);
    if (on) next.add(key);
    else next.delete(key);
    onChange(groups.map((g) => g.key).filter((k) => next.has(k)));
  }

  return (
    <fieldset className="fly-groups" aria-describedby={problem ? problemId : undefined} disabled={disabled}>
      <legend className="fly-groups-legend">
        <Term k="marketing_group" label="marketing group">
          {legend}
        </Term>
      </legend>
      <div className="fly-groups-tools">
        <span className="fly-groups-count" aria-live="polite">
          {selected.length} of {groups.length} picked
        </span>
        <button
          type="button"
          className="link-button"
          onClick={() => onChange(pickable.map((g) => g.key))}
          disabled={disabled || pickable.length === 0 || selected.length === pickable.length}
        >
          Select all
        </button>
        <button type="button" className="link-button" onClick={() => onChange([])} disabled={disabled || selected.length === 0}>
          None
        </button>
        {isAdmin && onManageGroups && (
          <button type="button" className="link-button fly-groups-manage" onClick={onManageGroups}>
            Manage groups
          </button>
        )}
      </div>
      <ul className="fly-group-list">
        {groups.map((g) => {
          const last = latestByGroup?.[g.key];
          const busy = isBusy(last);
          const id = `${idPrefix}-group-${g.key}`;
          return (
            <li key={g.key}>
              <label className={`fly-group${selected.includes(g.key) ? " fly-group-on" : ""}`} htmlFor={id}>
                <input
                  id={id}
                  type="checkbox"
                  checked={selected.includes(g.key)}
                  disabled={busy}
                  onChange={(e) => toggle(g.key, e.target.checked)}
                />
                <span className="fly-group-text">
                  <span className="fly-group-name">{g.label}</span>
                  {last && <span className="fly-group-note">{lastSendNote(last)}</span>}
                </span>
              </label>
            </li>
          );
        })}
      </ul>
      {problem && (
        <p className="error fly-field-error" id={problemId} role="alert">
          {problem}
        </p>
      )}
    </fieldset>
  );
}

function isBusy(send) {
  return send?.status === "queued" || send?.status === "sending";
}

function lastSendNote(send) {
  if (send.status === "sent") return `Already sent${send.sent_at ? ` ${shortDay(send.sent_at)}` : ""}`;
  if (send.status === "failed") return "Last try failed";
  return statusInfo(send.status).label;
}
