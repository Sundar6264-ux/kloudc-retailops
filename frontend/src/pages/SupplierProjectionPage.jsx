import SupplierProjection from "../components/SupplierProjection.jsx";

export default function SupplierProjectionPage() {
  return (
    <>
      <div className="page-header">
        {/* "New order" everywhere (menu, the app's Home tile); its old name kept beside it. */}
        <h1>
          New order <span className="muted" style={{ fontSize: "0.9rem", fontWeight: 500 }}>Supplier Projection</span>
        </h1>
      </div>
      <SupplierProjection />
    </>
  );
}
