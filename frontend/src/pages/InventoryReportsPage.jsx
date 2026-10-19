import SalesDownload from "../components/SalesDownload.jsx";
import InventoryReports from "../components/InventoryReports.jsx";
import { useAuth } from "../AuthContext.jsx";
import PageHeader from "../ui/PageHeader.jsx";

// The sales export moved to Items Sold (Oct 15 nav clean-up); it stays here
// only for someone who can't open Items Sold, so nobody loses it. The
// reports' own brief is the page intro.
export default function InventoryReportsPage() {
  const { hasFeature } = useAuth();
  return (
    <>
      <PageHeader pageId="inventory_reports" />
      {!hasFeature("items_sold") && <SalesDownload />}
      <InventoryReports />
    </>
  );
}
