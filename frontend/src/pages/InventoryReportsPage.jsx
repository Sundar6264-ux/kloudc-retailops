import SalesDownload from "../components/SalesDownload.jsx";
import InventoryReports from "../components/InventoryReports.jsx";
import { useAuth } from "../AuthContext.jsx";

// The sales export moved to Items Sold (Oct 15 nav clean-up); it stays here
// only for someone who can't open Items Sold, so nobody loses it. The
// reports' own brief is the page intro.
export default function InventoryReportsPage() {
  const { hasFeature } = useAuth();
  return (
    <>
      <div className="page-header">
        <h1>Inventory Reports</h1>
      </div>
      {!hasFeature("items_sold") && <SalesDownload />}
      <InventoryReports />
    </>
  );
}
