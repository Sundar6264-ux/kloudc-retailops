import EmptyState from "../../ui/EmptyState.jsx";

// INTERFACE STUB (docs/WEBSITE_READY_CONTRACT.md 3.6, I7). Track B owns this
// file: the Data tab of a store in the master console - exports with their
// download links, and off-boarding (archive, grace period, purge). Track
// A's store detail renders <StoreDataPanel storeCode="ABC" status="active"
// onChanged={reload} /> and nothing else.
export default function StoreDataPanel({ storeCode }) {
  return (
    <EmptyState icon="package" title="Data" area="platform">
      Data export for {storeCode} is not available yet.
    </EmptyState>
  );
}
