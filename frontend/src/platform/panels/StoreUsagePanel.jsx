import EmptyState from "../../ui/EmptyState.jsx";

// INTERFACE STUB (docs/WEBSITE_READY_CONTRACT.md 3.6, I7). Track B owns this
// file: the Usage & plan tab of a store in the master console - usage
// metrics, the plan with its soft limits, and the plan picker. Track A's
// store detail renders <StoreUsagePanel storeCode="ABC" /> and nothing else.
export default function StoreUsagePanel({ storeCode }) {
  return (
    <EmptyState icon="chart-line" title="Usage" area="platform">
      Usage for {storeCode} is not measured yet.
    </EmptyState>
  );
}
