import PlacedOrders from "../components/PlacedOrders.jsx";
import PageHeader from "../ui/PageHeader.jsx";

export default function PlacedOrdersPage() {
  return (
    <>
      <PageHeader pageId="placed_orders" />
      <PlacedOrders />
    </>
  );
}
