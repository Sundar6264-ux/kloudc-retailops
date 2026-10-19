import OpenOrders from "../components/OpenOrders.jsx";
import PageHeader from "../ui/PageHeader.jsx";

export default function OpenOrdersPage() {
  return (
    <>
      <PageHeader pageId="open_checks" />
      <OpenOrders />
    </>
  );
}
