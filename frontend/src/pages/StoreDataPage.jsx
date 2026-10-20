import { useCallback, useEffect, useMemo, useState } from "react";
import { getOwnUsage, listOwnExports, ownExportLink, requestOwnExport } from "../clients/data.js";
import { getStoreTimeZone } from "../format.js";
import {
  Banner,
  DailyChart,
  ExportsBlock,
  LimitBars,
  Section,
  StatusChip,
  UsageTiles,
  useExports,
} from "../platform/panels/dataParts.jsx";
import { dataErrorMessage, planNotice, STATE_TEXT, STATE_TONE, timeAgo } from "../settings/dataModel.js";
import EmptyState from "../ui/EmptyState.jsx";
import PageHeader from "../ui/PageHeader.jsx";

const OWN_EXPORTS = { key: "own", list: listOwnExports, request: requestOwnExport, link: ownExportLink };

// Setup > Data & usage (/settings/data) for a store's owner/admin: usage,
// the plan with its soft limits, and "Export all data" with its Download
// links - one screen, no drill-down (docs/WEBSITE_READY_CONTRACT.md B1-B4).
export default function StoreDataPage() {
  const [usage, setUsage] = useState(null);
  const [usageError, setUsageError] = useState(null);
  const [starting, setStarting] = useState(false);
  const exportsHook = useExports(OWN_EXPORTS);
  const timeZone = useMemo(() => {
    try {
      return getStoreTimeZone() || undefined;
    } catch {
      return undefined;
    }
  }, []);

  const loadUsage = useCallback(async () => {
    try {
      setUsage(await getOwnUsage());
      setUsageError(null);
    } catch (err) {
      setUsageError(dataErrorMessage(err));
      setUsage({ available: true, metrics: {}, days: [] });
    }
  }, []);

  useEffect(() => {
    loadUsage();
  }, [loadUsage]);

  async function startExport() {
    setStarting(true);
    try {
      await requestOwnExport();
      await exportsHook.load();
    } catch (err) {
      exportsHook.setError(dataErrorMessage(err));
    } finally {
      setStarting(false);
    }
  }

  const available = usage?.available !== false && exportsHook.data?.available !== false;
  const notice = planNotice(usage?.plan);
  const exportBusy = exportsHook.state.running || starting;
  const exportButton = available && (
    <button type="button" onClick={startExport} disabled={exportBusy || exportsHook.data === null} data-testid="export-all">
      {exportBusy ? "Making the file…" : "Export all data"}
    </button>
  );

  return (
    <div className="dt-stack" data-area="setup" data-testid="store-data-page">
      <PageHeader
        pageId="store_data"
        subtitle="What your store uses, your plan, and a copy of all your data."
        actions={exportButton}
      />
      {usage === null ? (
        <p className="dt-muted">Loading…</p>
      ) : !available ? (
        <EmptyState icon="database" title="Measured on the shared service" area="setup">
          Usage, plans and Export all data are part of the KloudC shared service. On this server, ask whoever runs it for a copy of the data.
        </EmptyState>
      ) : (
        <>
          {usageError && <Banner tone="bad">{usageError}</Banner>}
          {notice && <Banner tone={notice.tone}>{notice.text}</Banner>}

          <Section
            title="Usage"
            pageId="store_data"
            section="usage"
            sub={usage.metrics?.last_activity_at ? `Last activity ${timeAgo(usage.metrics.last_activity_at).toLowerCase()}` : null}
            testId="usage-section"
          >
            <UsageTiles snapshot={usage} />
            <DailyChart days={usage.days} />
          </Section>

          {usage.plan && (
            <Section title="Plan and limits" pageId="store_data" section="plan" testId="plan-section">
              <div className="dt-plan-row">
                <span className="dt-plan-name">{usage.plan.label}</span>
                {usage.plan.limit_states?.length > 0 && (
                  <StatusChip tone={STATE_TONE[usage.plan.state]}>{STATE_TEXT[usage.plan.state]}</StatusChip>
                )}
              </div>
              <LimitBars states={usage.plan.limit_states} />
              <p className="dt-muted" style={{ margin: "0.8rem 0 0", fontSize: "0.85rem" }}>
                A limit never stops your work. To change your plan, contact KloudC support.
              </p>
            </Section>
          )}

          <Section
            title="Export all data"
            pageId="store_data"
            section="export"
            sub={`One ZIP file: a spreadsheet per table and every file your store keeps. No passwords or keys. Kept ${exportsHook.data?.keep_days || 7} days; a Download link works ${exportsHook.data?.link_hours || 24} hours.`}
            testId="export-section"
          >
            <ExportsBlock
              api={OWN_EXPORTS}
              exportsHook={exportsHook}
              timeZone={timeZone}
              emptyText="No data exports yet. Press Export all data to make one."
            />
          </Section>
        </>
      )}
    </div>
  );
}
