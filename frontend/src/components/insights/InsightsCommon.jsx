// Shared pieces of the Insights pages: the loader hook, the page frame, the
// "From <pos> Analytics, updated ..." line and the loading / empty / stale /
// error states.
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../../AuthContext.jsx";
import { ALSO_IN_POS, routeForPath } from "../../featureRoutes.js";
import { useLocation } from "react-router-dom";
import PageHeader from "../../ui/PageHeader.jsx";
import InsightsHub from "./InsightsHub.jsx";
import { usePosName } from "../../store/StoreContext.jsx";
import { posText } from "../../store/gating.js";
import { formatRestaurantTime } from "../../format.js";
import { TermInfo } from "../../glossary.jsx";
import InfoBlock from "../InfoBlock.jsx";
import { errorKind, staleText } from "./insightsModel.js";

// Loads `load()` whenever `deps` change. Keeps the previous answer on screen
// (dimmed) while a new one loads, so filters don't make the page jump.
export function useInsight(load, deps) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const seq = useRef(0);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(load, deps);
  const reload = useCallback(() => {
    const id = ++seq.current;
    setState((s) => ({ ...s, loading: true, error: null }));
    run()
      .then((data) => {
        if (id === seq.current) setState({ data, error: null, loading: false });
      })
      .catch((error) => {
        if (id === seq.current) setState({ data: null, error, loading: false });
      });
  }, [run]);
  useEffect(() => {
    reload();
  }, [reload]);
  return { ...state, reload };
}

// `alsoInToast`: one of the pages that repeat the POS's own reports - says
// so under the title (they sit in the sidebar's "Also in <pos>" group).
export function InsightsPage({ title, brief, info, controls, children, alsoInToast = false, className = "" }) {
  const { hasFeature } = useAuth();
  const pos = usePosName();
  const pageId = routeForPath(useLocation().pathname)?.pageId || "insights";
  return (
    <div className={`ins-page ${className}`}>
      {/* One Insights hub (DESIGN_SYSTEM 3.1): this page's title, then the
          hub's tile tabs to every other Insights page. */}
      <PageHeader pageId={pageId} title={title} subtitle={brief} areaLabel="Sales › Insights">
        {alsoInToast && (
          <p className="ins-also-toast">
            <span className="ins-also-toast-tag">{posText(ALSO_IN_POS.label, pos)}</span>
            <span>{posText(ALSO_IN_POS.note, pos)}</span>
          </p>
        )}
      </PageHeader>
      <InsightsHub />
      {info && (
        <div className="ins-info">
          <InfoBlock brief={info.brief}>
            {info.body}
            {hasFeature("help") && (
              <p style={{ marginBottom: 0 }}>
                <HelpLink />
              </p>
            )}
          </InfoBlock>
        </div>
      )}
      {controls && <div className="ins-controls">{controls}</div>}
      {children}
    </div>
  );
}

// Choice pills ("4 weeks | 8 weeks | 12 weeks"). A radiogroup: arrow keys
// move the choice, one tab stop.
export function Choice({ label, options, value, onChange, disabled }) {
  const refs = useRef([]);
  const labelId = useId();
  const idx = Math.max(0, options.findIndex((o) => o.value === value));
  function onKeyDown(e) {
    let next = null;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (idx + 1) % options.length;
    if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = (idx - 1 + options.length) % options.length;
    if (next == null) return;
    e.preventDefault();
    onChange(options[next].value);
    refs.current[next]?.focus();
  }
  return (
    <div className="ins-choice">
      <span className="ins-choice-label" id={labelId}>
        {label}
      </span>
      <div className="range-pills ins-pills" role="radiogroup" aria-labelledby={labelId}>
        {options.map((o, i) => (
          <button
            key={o.value}
            ref={(el) => (refs.current[i] = el)}
            type="button"
            role="radio"
            aria-checked={o.value === value}
            tabIndex={o.value === value ? 0 : -1}
            className={`range-pill${o.value === value ? " active" : ""}`}
            disabled={disabled}
            onClick={() => onChange(o.value)}
            onKeyDown={onKeyDown}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}

// "From Toast Analytics, updated Oct 13, 9:42 AM EDT." plus the stale note.
// `updated_at` is when the newest number on the page was read from the POS;
// `generated_at` (when this answer was put together) is the fallback.
export function SourceLine({ data, extra }) {
  const pos = usePosName();
  if (!data) return null;
  const when = formatRestaurantTime(data.updated_at || data.generated_at);
  const note = staleText(data);
  return (
    <div className="ins-source">
      <p className="muted ins-source-line">
        From {pos} Analytics{when ? `, updated ${when}` : ""}.{extra ? ` ${extra}` : ""}{" "}
        <TermInfo k="insights_source" label={`${pos} Analytics`} />
      </p>
      {note && (
        <p className="ins-stale" role="status">
          <span className="ins-stale-icon" aria-hidden="true">!</span>
          <span>
            {note} <TermInfo k="insights_stale" label="Not up to date" />
          </span>
        </p>
      )}
    </div>
  );
}

function errorText(err, { adminOnly, pos }) {
  const kind = errorKind(err);
  if (kind === "forbidden") {
    if (adminOnly) return "This page is for admins only.";
    return err?.message && !/forbidden/i.test(err.message)
      ? err.message
      : "Your account can't see this report. Ask an admin if you need it.";
  }
  if (kind === "busy") return `${pos} is limiting how often we can ask right now. Try again in a minute.`;
  if (kind === "bad_request") return String(err?.message || "That choice isn't available.").replace(/\.*$/, ".");
  if (kind === "network") return "Couldn't reach the server. Check the connection and try again.";
  return `Couldn't load this report: ${err?.message || "unknown error"}.`;
}

// Loading / error / empty / stale frame around a report's content.
export function InsightBody({ state, isEmpty, empty, adminOnly = false, children }) {
  const { data, error, loading, reload } = state;
  const pos = usePosName();
  if (error) {
    const forbidden = errorKind(error) === "forbidden";
    return (
      <section className="card ins-state ins-state-error" role="alert">
        <p className="ins-state-title">
          {forbidden ? "Not available" : errorKind(error) === "bad_request" ? "Can't show that" : "Something went wrong"}
        </p>
        <p className="muted">{errorText(error, { adminOnly, pos })}</p>
        {!forbidden && errorKind(error) !== "bad_request" && (
          <button type="button" className="btn-secondary" onClick={reload}>
            Try again
          </button>
        )}
      </section>
    );
  }
  if (!data) {
    return (
      <section className="card ins-state" aria-busy="true">
        <div className="ins-skeleton" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
        <p className="muted" role="status">
          Loading from {pos} Analytics… The first load of a period can take up to a minute.
        </p>
      </section>
    );
  }
  const nothing = isEmpty(data);
  return (
    <div className={`ins-body${loading ? " is-loading" : ""}`} aria-busy={loading || undefined}>
      {!(nothing && data.stale) && <SourceLine data={data} />}
      {nothing ? (
        <section className="card ins-state">
          <p className="ins-state-title">{data.stale ? "No numbers saved yet" : "Nothing to show"}</p>
          <p className="muted">{data.stale ? staleText(data) : empty}</p>
          {data.stale && (
            <button type="button" className="btn-secondary" onClick={state.reload}>
              Check again
            </button>
          )}
        </section>
      ) : (
        children
      )}
    </div>
  );
}

// A KPI tile (value first, label under it).
export function Kpi({ label, value, sub, term, tone, children }) {
  return (
    <div className={`ins-kpi${tone ? ` ins-kpi-${tone}` : ""}`}>
      <div className="ins-kpi-label">
        {label}
        {term && <TermInfo k={term} label={label} />}
      </div>
      <div className="ins-kpi-value">{value}</div>
      {sub && <div className="ins-kpi-sub">{sub}</div>}
      {children}
    </div>
  );
}

export function HelpLink() {
  return (
    <Link to="/help" state={{ section: "insights" }} className="ins-help-link">
      More in Help: Insights
    </Link>
  );
}
