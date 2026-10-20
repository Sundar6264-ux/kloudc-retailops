import EmptyState from "../../ui/EmptyState.jsx";

// INTERFACE STUB (docs/WEBSITE_READY_CONTRACT.md 3.6, I7). Track C owns this
// file: the System section of the master console's Platform settings -
// storage backend, job queue and workers, failed jobs with Retry, live
// updates mode, database pooling. Track A renders <SystemStatusPanel />.
export default function SystemStatusPanel() {
  return (
    <EmptyState icon="server" title="System" area="platform">
      System status is not available yet.
    </EmptyState>
  );
}
