import EmptyState from "../ui/EmptyState.jsx";

// PAGE STUB (docs/WEBSITE_READY_CONTRACT.md 3.6). Track B owns this file:
// Setup > Data & usage for a store's owner/admin - usage, plan and soft
// limits, "Export all data" with its download links. Track A routes it at
// /settings/data (lazy) and adds the nav entry.
export default function StoreDataPage() {
  return (
    <EmptyState icon="database" title="Data & usage" area="setup">
      Data export and usage are not available yet.
    </EmptyState>
  );
}
