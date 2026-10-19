import SupplierProjection from "../components/SupplierProjection.jsx";
import PageHeader from "../ui/PageHeader.jsx";

export default function SupplierProjectionPage() {
  return (
    <>
      {/* "New order" everywhere (menu, the app's Home tile); its old name kept beside it. */}
      <PageHeader pageId="new_order" titleSuffix={<span className="kc-title-aside">Supplier Projection</span>} />
      <SupplierProjection />
    </>
  );
}
