import SupplierProjection from "../components/SupplierProjection.jsx";
import PageHeader from "../ui/PageHeader.jsx";

export default function SupplierProjectionPage() {
  return (
    <>
      {/* "New order" everywhere (menu, search, the app's Home tile); searching the old name "Supplier Projection" still finds it (glossary). */}
      <PageHeader pageId="new_order" />
      <SupplierProjection />
    </>
  );
}
