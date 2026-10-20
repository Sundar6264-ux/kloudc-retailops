// Store detail (website ready A4, spec 3.2): a side drawer from a Stores row
// (a full-screen sheet on phones) with tile tabs Overview · Usage & plan ·
// Settings · Data · Audit, each one click away; the last tab is remembered.
// Usage & plan and Data are track B's panels; Settings and Audit are the
// console's own (passed in, so this file does not copy them).
import { useEffect, useRef, useState } from "react";
import { getStoreHealth } from "../clients/console.js";
import { formatRestaurantTime } from "../format.js";
import Icon from "../ui/icons.jsx";
import TileTabs from "../ui/TileTabs.jsx";
import useRemembered from "../ui/useRemembered.js";
import { InfoButton } from "../ui/helpBridge.jsx";
import { Chip, Feedback } from "./ConsoleBits.jsx";
import {
  DETAIL_TAB_KEY,
  STORE_DETAIL_TABS,
  detailTab,
  healthSummary,
  integrationChip,
  jobChip,
  jobLabel,
} from "./consoleModel.js";
import StoreDataPanel from "./panels/StoreDataPanel.jsx";
import StoreUsagePanel from "./panels/StoreUsagePanel.jsx";
import { platformErrorMessage, storeStatusChip } from "./platformModel.js";
import "./console.css";

export default function StoreDetail({ store, onClose, onChanged, renderSettings, renderAudit }) {
  const [remembered, setRemembered] = useRemembered(DETAIL_TAB_KEY, "overview");
  const tab = detailTab(remembered);
  const closeRef = useRef(null);
  const lastFocus = useRef(null);

  useEffect(() => {
    lastFocus.current = document.activeElement;
    closeRef.current?.focus();
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
      lastFocus.current?.focus?.();
    };
  }, [onClose]);

  return (
    <>
      <div className="kc-drawer-backdrop" onClick={onClose} aria-hidden="true" />
      <div className="kc-drawer pf-detail" role="dialog" aria-modal="true" aria-labelledby="pf-detail-title" data-testid="store-detail">
        <div className="kc-drawer-head pf-detail-head">
          <span className="kc-drawer-title" id="pf-detail-title">
            <Icon name="store" size={18} />
            <span className="pf-detail-name">{store.name}</span>
            <span className="pf-code">{store.code}</span>
          </span>
          <span className="pf-detail-head-actions">
            <InfoButton pageId="platform_stores" />
            <button type="button" ref={closeRef} className="kc-icon-btn" aria-label="Close store detail" onClick={onClose}>
              <Icon name="x" size={18} />
            </button>
          </span>
        </div>
        <TileTabs items={STORE_DETAIL_TABS} value={tab} onChange={setRemembered} label="Store detail" area="platform" compact className="pf-detail-tabs" />
        <div className="kc-drawer-body pf-detail-body" role="tabpanel" aria-label={STORE_DETAIL_TABS.find((t) => t.key === tab)?.label}>
          {tab === "overview" && <Overview code={store.code} />}
          {tab === "usage" && <StoreUsagePanel storeCode={store.code} />}
          {tab === "settings" && renderSettings?.(store)}
          {tab === "data" && <StoreDataPanel storeCode={store.code} status={store.status} onChanged={onChanged} />}
          {tab === "audit" && renderAudit?.(store)}
        </div>
      </div>
    </>
  );
}

const fmt = (iso) => (iso ? formatRestaurantTime(iso) : "never");

function Overview({ code }) {
  const [h, setH] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    let live = true;
    setH(null);
    getStoreHealth(code)
      .then((r) => live && setH(r))
      .catch((err) => live && setError(platformErrorMessage(err)));
    return () => {
      live = false;
    };
  }, [code]);

  if (error) return <Feedback message={{ tone: "bad", text: error }} />;
  if (!h) return <p className="muted">Loading…</p>;
  const summary = healthSummary(h);
  const db = h.database || {};
  return (
    <div className="pf-health" data-testid="store-health">
      <Feedback message={summary && { tone: summary.tone === "good" ? "ok" : summary.tone, text: summary.text }} />
      <dl className="pf-facts">
        <div>
          <dt>Status</dt>
          <dd>
            <Chip chip={storeStatusChip(h.status)} />
            {h.status_reason && <span className="pf-row-meta"> {h.status_reason}</span>}
          </dd>
        </div>
        {h.provisioning_step && h.status !== "active" && (
          <div>
            <dt>Set-up step</dt>
            <dd>{h.provisioning_step}</dd>
          </div>
        )}
        <div>
          <dt>POS</dt>
          <dd>{(h.pos_provider || "").toUpperCase() || "None"}</dd>
        </div>
        <div>
          <dt>Database version</dt>
          <dd>{db.reachable ? <span className="pf-code">{db.revision || "unknown"}</span> : <Chip chip={{ tone: "bad", label: "Can't reach it" }} />}</dd>
        </div>
        <div>
          <dt>Last activity</dt>
          <dd>{fmt(h.last_activity_at)}</dd>
        </div>
        <div>
          <dt>Time zone</dt>
          <dd>{h.timezone || "—"}</dd>
        </div>
      </dl>

      <h3 className="pf-section-title">Background jobs</h3>
      {(db.jobs || []).length === 0 ? (
        <p className="muted">No job has run yet.</p>
      ) : (
        <ul className="pf-mini-list">
          {db.jobs.map((j) => (
            <li key={j.job}>
              <span className="pf-mini-title">{jobLabel(j.job)}</span>
              <Chip chip={jobChip(j.status)} />
              <span className="pf-row-meta">{fmt(j.finished_at || j.started_at)}</span>
              {j.error && <span className="pf-row-meta pf-mini-error">{j.error}</span>}
            </li>
          ))}
        </ul>
      )}

      <h3 className="pf-section-title">Connections</h3>
      {(h.integrations || []).length === 0 ? (
        <p className="muted">Nothing connected yet.</p>
      ) : (
        <ul className="pf-mini-list">
          {h.integrations.map((i) => (
            <li key={i.kind}>
              <span className="pf-mini-title">{i.label || i.kind}</span>
              <Chip chip={integrationChip(i)} />
              {i.last_tested_at && <span className="pf-row-meta">tested {fmt(i.last_tested_at)}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
