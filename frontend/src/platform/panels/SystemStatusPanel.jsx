// The System section of the master console's Platform settings
// (docs/WEBSITE_READY_CONTRACT.md track C, names in section 4): Storage ·
// Background jobs · Workers · Live updates, then the jobs that need a
// master (Retry) and the workers. Track A renders <SystemStatusPanel />.
// Read-only apart from Retry: storage, the queue and live updates are
// switched on by server settings, never here.
import { useCallback, useEffect, useState } from "react";
import EmptyState from "../../ui/EmptyState.jsx";
import Icon from "../../ui/icons.jsx";
import useRemembered from "../../ui/useRemembered.js";
import { InfoButton } from "../../ui/helpBridge.jsx";
import { getSystemStatus, listSystemJobs, retrySystemJob } from "../../clients/system.js";
import { platformErrorMessage } from "../platformModel.js";
import {
  JOB_FILTERS,
  JOB_FILTER_KEY,
  canRetry,
  databaseLine,
  errorLine,
  jobFilter,
  jobKindLabel,
  jobMeta,
  jobStatusChip,
  retryMessage,
  systemTiles,
  workerChip,
  workerMeta,
} from "./systemModel.js";
import "./systemStatus.css";

function Chip({ tone, children }) {
  return <span className={`pf-chip tone-${tone || "neutral"}`}>{children}</span>;
}

export default function SystemStatusPanel() {
  const [status, setStatus] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [loading, setLoading] = useState(true);
  const [filterKey, setFilterKey] = useRemembered(JOB_FILTER_KEY, "attention");
  const [jobs, setJobs] = useState({ items: [], next: null, loading: true, error: "" });
  const [busy, setBusy] = useState(null);
  const [notice, setNotice] = useState(null);
  const [now, setNow] = useState(() => Date.now());
  const filter = jobFilter(filterKey);

  const loadStatus = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      setStatus(await getSystemStatus());
    } catch (err) {
      setLoadError(platformErrorMessage(err));
    } finally {
      setLoading(false);
      setNow(Date.now());
    }
  }, []);

  const loadJobs = useCallback(
    async (before = null) => {
      setJobs((j) => ({ ...j, loading: true, error: "" }));
      try {
        const reply = await listSystemJobs({ statuses: filter.statuses, before, limit: 25 });
        setJobs((j) => ({
          items: before ? [...j.items, ...reply.jobs] : reply.jobs,
          next: reply.next_before,
          loading: false,
          error: "",
        }));
      } catch (err) {
        setJobs((j) => ({ ...j, loading: false, error: platformErrorMessage(err) }));
      }
    },
    [filter.key], // eslint-disable-line react-hooks/exhaustive-deps
  );

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  useEffect(() => {
    loadJobs();
  }, [loadJobs]);

  function refresh() {
    setNotice(null);
    loadStatus();
    loadJobs();
  }

  async function retry(job) {
    setBusy(job.id);
    setNotice(null);
    try {
      await retrySystemJob(job.id);
      setNotice({ ok: true, text: retryMessage(job) });
      await Promise.all([loadStatus(), loadJobs()]);
    } catch (err) {
      setNotice({ ok: false, text: platformErrorMessage(err) });
    } finally {
      setBusy(null);
    }
  }

  const tiles = status ? systemTiles(status) : [];
  const workers = status?.jobs?.workers || [];
  const queueMode = status?.jobs?.mode === "queue";

  return (
    <section className="ss-panel" data-area="platform" aria-labelledby="ss-title" data-testid="system-panel">
      <div className="ss-head">
        <h2 id="ss-title" className="ss-title">
          <Icon name="server" size={18} />
          <span>System</span>
        </h2>
        <InfoButton pageId="platform_system" />
        <button
          type="button"
          className="kc-icon-btn ss-refresh"
          onClick={refresh}
          disabled={loading}
          aria-label="Refresh system status"
          title="Refresh"
        >
          <Icon name="repeat" size={16} />
        </button>
      </div>

      {notice && (
        <p className={`pf-banner ${notice.ok ? "ok" : "bad"}`} role="status">
          {notice.text}
        </p>
      )}

      {loadError ? (
        <div className="pf-banner bad" role="alert">
          {loadError}{" "}
          <button type="button" className="btn-secondary ss-inline-btn" onClick={loadStatus}>
            Try again
          </button>
        </div>
      ) : (
        <ul className="ss-tiles" aria-busy={loading && !status}>
          {(status ? tiles : SKELETON).map((t) => (
            <li key={t.key} className={`ss-tile tone-${t.tone || "neutral"}`} data-testid={`system-tile-${t.key}`}>
              <div className="ss-tile-top">
                <span className="ss-tile-icon" aria-hidden="true">
                  <Icon name={t.icon} size={16} />
                </span>
                <span className="ss-tile-label">{t.label}</span>
                {status && <InfoButton pageId="platform_system" section={t.section} />}
              </div>
              <p className="ss-tile-value">{status ? t.value : " "}</p>
              <p className="ss-tile-detail">{status ? t.detail : "Loading…"}</p>
            </li>
          ))}
        </ul>
      )}
      {status && <p className="ss-db">{databaseLine(status.database)}</p>}

      <div className="ss-section-head">
        <h3 className="ss-h3">Background jobs</h3>
        <div className="ss-filters" role="group" aria-label="Show jobs">
          {JOB_FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              className="ss-filter"
              aria-pressed={f.key === filter.key}
              onClick={() => setFilterKey(f.key)}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {jobs.error && (
        <p className="pf-banner bad" role="alert">
          {jobs.error}
        </p>
      )}
      {!jobs.error && !jobs.loading && jobs.items.length === 0 && (
        <EmptyState icon="circle-check" title={filter.key === "attention" ? "Nothing needs you" : "No jobs here"} area="platform">
          {queueMode || status?.jobs?.counts
            ? filter.key === "attention"
              ? "No background job has failed."
              : "No job matches this filter."
            : "Jobs run inside the web server, so there is no queue to show."}
        </EmptyState>
      )}
      {jobs.items.length > 0 && (
        <div className="pf-list ss-list" role="list" aria-label="Background jobs">
          {jobs.items.map((job) => {
            const chip = jobStatusChip(job.status);
            return (
              <div key={job.id} className="pf-row" role="listitem" data-testid={`job-${job.id}`}>
                <div className="pf-row-top">
                  <span className="pf-row-title">{jobKindLabel(job.kind)}</span>
                  <Chip tone={chip.tone}>{chip.label}</Chip>
                  <span className="pf-code">#{job.id}</span>
                </div>
                <p className="pf-row-meta">{jobMeta(job, now)}</p>
                {job.error && <p className="ss-error">{errorLine(job.error)}</p>}
                {canRetry(job) && (
                  <div className="pf-actions">
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={() => retry(job)}
                      disabled={busy === job.id}
                      aria-label={`Retry ${jobKindLabel(job.kind)} #${job.id}`}
                    >
                      {busy === job.id ? "Retrying…" : "Retry"}
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
      {jobs.next && (
        <button type="button" className="btn-secondary ss-more" onClick={() => loadJobs(jobs.next)} disabled={jobs.loading}>
          {jobs.loading ? "Loading…" : "Show older jobs"}
        </button>
      )}

      <div className="ss-section-head">
        <h3 className="ss-h3">Workers</h3>
      </div>
      {workers.length === 0 ? (
        <EmptyState icon="server" title={queueMode ? "No worker is running" : "No workers needed"} area="platform">
          {queueMode
            ? "Jobs wait in the queue until a worker starts. See the multi-process deploy guide."
            : "With one server process, jobs run inside the web server."}
        </EmptyState>
      ) : (
        <div className="pf-list" role="list" aria-label="Workers">
          {workers.map((w) => {
            const chip = workerChip(w.state);
            return (
              <div key={w.id} className="pf-row" role="listitem" data-testid="worker-row">
                <div className="pf-row-top">
                  <span className="pf-row-title">{w.hostname}</span>
                  <Chip tone={chip.tone}>{chip.label}</Chip>
                  <span className="pf-code">pid {w.pid}</span>
                </div>
                <p className="pf-row-meta">{workerMeta(w, now)}</p>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

const SKELETON = [
  { key: "storage", label: "Storage", icon: "database" },
  { key: "jobs", label: "Background jobs", icon: "list-checks" },
  { key: "workers", label: "Workers", icon: "server" },
  { key: "live", label: "Live updates", icon: "repeat" },
];
