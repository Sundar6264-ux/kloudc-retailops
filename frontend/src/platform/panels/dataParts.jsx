// Building blocks shared by Setup > Data & usage and the console's Usage &
// plan / Data tabs (docs/WEBSITE_READY_CONTRACT.md B1-B4).
import { useCallback, useEffect, useRef, useState } from "react";
import { absoluteLink } from "../../clients/data.js";
import {
  NOT_MEASURED,
  dailyBars,
  dataErrorMessage,
  exportRow,
  exportsState,
  formatDateTime,
  limitBars,
  usageTiles,
} from "../../settings/dataModel.js";
import Icon from "../../ui/icons.jsx";
import { InfoButton } from "../../ui/helpBridge.jsx";
import "./data.css";

export function Section({ title, pageId, section, sub, actions, children, testId }) {
  return (
    <section className="dt-section" aria-label={title} data-testid={testId}>
      <div className="dt-section-head">
        <div>
          <h2 className="dt-section-title">
            {title}
            {pageId && <InfoButton pageId={pageId} section={section} />}
          </h2>
          {sub && <p className="dt-section-sub">{sub}</p>}
        </div>
        {actions && <div className="dt-actions">{actions}</div>}
      </div>
      {children}
    </section>
  );
}

export function Banner({ tone = "info", children, icon }) {
  const name = icon || (tone === "bad" ? "octagon-alert" : tone === "warn" ? "triangle-alert" : tone === "good" ? "circle-check" : "info");
  return (
    <div className={`dt-banner tone-${tone}`} role={tone === "bad" ? "alert" : "status"}>
      <Icon name={name} size={16} />
      <span>{children}</span>
    </div>
  );
}

export function UsageTiles({ snapshot }) {
  const tiles = usageTiles(snapshot);
  return (
    <div className="dt-tiles" data-testid="usage-tiles">
      {tiles.map((t) => (
        <div className="dt-tile" key={t.key}>
          <span className="dt-tile-icon" aria-hidden="true">
            <Icon name={t.icon} size={18} />
          </span>
          <span className="dt-tile-body">
            <span className="dt-tile-label">{t.label}</span>
            <span className={`dt-tile-value${t.value === NOT_MEASURED ? " is-pending" : ""}`}>{t.value}</span>
            <span className="dt-tile-hint">{t.hint}</span>
          </span>
        </div>
      ))}
    </div>
  );
}

export function DailyChart({ days, metric = "orders_synced", label = "Sales synced per day" }) {
  const bars = dailyBars(days, metric);
  if (!bars.length) return null;
  const total = bars.reduce((s, b) => s + b.value, 0);
  // A flat row of empty bars says nothing: one quiet line instead.
  if (!total) {
    return (
      <p className="dt-muted dt-chart-none" data-testid="daily-chart-none">
        {label}: none in the last {bars.length} days.
      </p>
    );
  }
  const summary = `${label}: ${total.toLocaleString("en-US")} over ${bars.length} days, from ${bars[0].day} to ${bars[bars.length - 1].day}.`;
  return (
    <div className="dt-chart">
      <div className="dt-chart-label">
        <span>{label}</span>
        <span>Last {bars.length} days</span>
      </div>
      <div className="dt-bars" role="img" aria-label={summary}>
        {bars.map((b) => (
          <span
            key={b.day}
            className={`dt-bar${b.value ? "" : " is-empty"}`}
            style={{ height: `${b.value ? b.height : 3}%` }}
            title={`${b.day}: ${b.value.toLocaleString("en-US")}`}
          />
        ))}
      </div>
    </div>
  );
}

export function LimitBars({ states }) {
  const bars = limitBars(states);
  if (!bars.length) return <p className="dt-muted" style={{ margin: "0.6rem 0 0" }}>This plan has no limits.</p>;
  return (
    <div className="dt-limits" data-testid="limit-bars">
      {bars.map((b) => (
        <div key={b.key}>
          <div className="dt-limit-top">
            <span className="dt-limit-label">{b.label}</span>
            <span className="dt-limit-text">{b.text}</span>
          </div>
          <div
            className={`dt-meter tone-${b.tone}`}
            role="meter"
            aria-label={`${b.label}: ${b.text}`}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={b.pct}
          >
            <span style={{ width: `${b.pct}%` }} />
          </div>
          <div className={`dt-limit-state tone-${b.tone}`}>{b.stateText}</div>
        </div>
      ))}
    </div>
  );
}

export function StatusChip({ tone, children, busy }) {
  return (
    <span className={`dt-chip tone-${tone}`}>
      {busy && <span className="dt-spinner" aria-hidden="true" />}
      {children}
    </span>
  );
}

// "Export all data" + the list of data exports with Download / Try again.
// `api`: { list(), request(), link(id) } (store or master endpoints).
export function useExports(api) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const timer = useRef(null);

  const load = useCallback(async () => {
    try {
      const d = await api.list();
      setData(d);
      setError(null);
      return d;
    } catch (err) {
      setError(dataErrorMessage(err));
      setData((prev) => prev || { exports: [] });
      return null;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api.key]);

  useEffect(() => {
    load();
  }, [load]);

  const state = exportsState(data?.exports);
  useEffect(() => {
    if (!state.pollMs) return undefined;
    timer.current = setTimeout(load, state.pollMs);
    return () => clearTimeout(timer.current);
  }, [state.pollMs, data, load]);

  return { data, error, setError, load, state };
}

export function ExportsBlock({ api, exportsHook, timeZone, emptyText }) {
  const { data, error, setError, load, state } = exportsHook;
  const [busy, setBusy] = useState(null);
  const [linkNote, setLinkNote] = useState(null);
  const rows = (data?.exports || []).map((e) => exportRow(e, timeZone));

  async function download(id) {
    setBusy(`dl-${id}`);
    setLinkNote(null);
    try {
      const link = await api.link(id);
      setLinkNote({ id, text: `Download link works until ${formatDateTime(link.expires_at, timeZone)}.` });
      window.location.assign(absoluteLink(link.url));
      await load();
    } catch (err) {
      setError(dataErrorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function retry() {
    setBusy("retry");
    try {
      await api.request();
      await load();
    } catch (err) {
      setError(dataErrorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="dt-stack" style={{ gap: "0.6rem" }}>
      {error && <Banner tone="bad">{error}</Banner>}
      {data === null ? (
        <p className="dt-muted">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="dt-muted" style={{ margin: 0 }}>{emptyText}</p>
      ) : (
        <ul className="dt-list" data-testid="exports-list">
          {rows.map((r, i) => (
            <li className="dt-row" key={r.id}>
              <div className="dt-row-main">
                <div className="dt-row-top">
                  <span className="dt-row-title">{r.title}</span>
                  <StatusChip tone={r.tone} busy={r.busy}>{r.statusLabel}</StatusChip>
                </div>
                <span className="dt-row-meta">
                  {r.when}
                  {r.meta ? ` · ${r.meta}` : ""}
                </span>
                {r.error && <span className="dt-row-error">{r.error}</span>}
                {linkNote?.id === r.id && <span className="dt-link-note" role="status">{linkNote.text}</span>}
              </div>
              <div className="dt-actions">
                {r.canDownload && (
                  <button type="button" onClick={() => download(r.id)} disabled={!!busy} aria-label={`Download ${r.title.toLowerCase()} of ${r.when}`}>
                    {busy === `dl-${r.id}` ? "Getting link…" : "Download"}
                  </button>
                )}
                {r.error && i === 0 && state.canRequest && (
                  <button type="button" className="btn-secondary" onClick={retry} disabled={!!busy}>
                    {busy === "retry" ? "Starting…" : "Try again"}
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
