import { useCallback, useEffect, useState } from "react";
import { getStoreUsage, measureStoreUsage, setStorePlan } from "../../clients/data.js";
import { dataErrorMessage, STATE_TEXT, STATE_TONE, timeAgo } from "../../settings/dataModel.js";
import { Banner, DailyChart, LimitBars, Section, StatusChip, UsageTiles } from "./dataParts.jsx";

// The Usage & plan tab of a store in the master console
// (docs/WEBSITE_READY_CONTRACT.md B1, B2, I7): usage numbers, the plan with
// its soft limits, and the plan picker (audited). Track A's store detail
// renders <StoreUsagePanel storeCode="ABC" />.
export default function StoreUsagePanel({ storeCode }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [plan, setPlan] = useState("");
  const [busy, setBusy] = useState(null); // "plan" | "measure"
  const [saved, setSaved] = useState(null);

  const load = useCallback(async () => {
    try {
      const d = await getStoreUsage(storeCode);
      setData(d);
      setPlan(d.plan?.plan || "custom");
      setError(null);
    } catch (err) {
      setError(dataErrorMessage(err));
      setData((prev) => prev || { metrics: {}, days: [], plan: null });
    }
  }, [storeCode]);

  useEffect(() => {
    setData(null);
    setSaved(null);
    load();
  }, [load]);

  async function savePlan(e) {
    e.preventDefault();
    setBusy("plan");
    setSaved(null);
    try {
      const d = await setStorePlan(storeCode, plan);
      setData(d);
      setSaved(`Plan saved: ${d.plan.label}.`);
      setError(null);
    } catch (err) {
      setError(dataErrorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function measure() {
    setBusy("measure");
    try {
      setData(await measureStoreUsage(storeCode));
      setError(null);
    } catch (err) {
      setError(dataErrorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  if (data === null) return <p className="dt-muted">Loading…</p>;
  const view = data.plan;
  const changed = view && plan !== view.plan;

  return (
    <div className="dt-stack" data-area="platform" data-testid="store-usage-panel">
      {error && <Banner tone="bad">{error}</Banner>}
      {view && (
        <Section title="Plan" pageId="platform_store_usage" section="plan" testId="plan-section">
          <form className="dt-plan-form" onSubmit={savePlan}>
            <label>
              Store's plan
              <select value={plan} onChange={(e) => setPlan(e.target.value)} disabled={!!busy} data-testid="plan-select">
                {(view.plans || []).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label}
                  </option>
                ))}
              </select>
            </label>
            <button type="submit" disabled={!changed || !!busy}>
              {busy === "plan" ? "Saving…" : "Save plan"}
            </button>
            {view.limit_states?.length > 0 && (
              <StatusChip tone={STATE_TONE[view.state]}>{STATE_TEXT[view.state]}</StatusChip>
            )}
          </form>
          {saved && (
            <p className="dt-link-note" role="status" style={{ margin: "0.5rem 0 0" }}>
              {saved}
            </p>
          )}
          <LimitBars states={view.limit_states} />
        </Section>
      )}
      <Section
        title="Usage"
        pageId="platform_store_usage"
        sub={`Last activity: ${timeAgo(data.metrics?.last_activity_at).toLowerCase()}`}
        actions={
          <button type="button" className="btn-secondary" onClick={measure} disabled={!!busy}>
            {busy === "measure" ? "Measuring…" : "Measure now"}
          </button>
        }
        testId="usage-section"
      >
        <UsageTiles snapshot={data} />
        <DailyChart days={data.days} />
      </Section>
    </div>
  );
}
