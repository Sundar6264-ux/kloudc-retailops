import EmptyState from "../ui/EmptyState.jsx";

// PAGE STUB (docs/WEBSITE_READY_CONTRACT.md 3.6). Track D owns this file:
// Setup > Item vendors - who supplies each item, edited by hand or from a
// CSV, for stores whose POS has no supplier field (Clover). Track A routes
// it at /setup/item-vendors (lazy) and adds the nav entry.
export default function ItemVendorsPage() {
  return (
    <EmptyState icon="truck" title="Item vendors" area="setup">
      The item vendors editor is not available yet.
    </EmptyState>
  );
}
