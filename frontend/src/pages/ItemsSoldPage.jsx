import ItemsSold from "../components/ItemsSold.jsx";
import SalesDownload from "../components/SalesDownload.jsx";
import SalesAccuracy from "../components/SalesAccuracy.jsx";
import { useAuth } from "../AuthContext.jsx";

export default function ItemsSoldPage() {
  const { user, hasFeature } = useAuth();
  return (
    <>
      <div className="page-header">
        <h1>Items Sold</h1>
      </div>
      <ItemsSold />
      {/* The raw line-item export (was on Inventory Reports): sales things on
          the sales page. Its API is still gated on inventory_reports, so it
          shows for whoever had it there. */}
      {hasFeature("inventory_reports") && <SalesDownload />}
      {user?.is_admin && <SalesAccuracy />}
    </>
  );
}
