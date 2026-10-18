import { Link } from "react-router-dom";
import InfoBlock from "../components/InfoBlock.jsx";
import PurchaseOrderCart from "../components/PurchaseOrderCart.jsx";

// One intro line under the title; "? More" is short and points to Help,
// where the full cart guide lives.
export default function PurchaseOrderPage() {
  return (
    <>
      <div className="page-header po-cart-header">
        <h1>Purchase Order Cart</h1>
        <InfoBlock brief="A shared cart for every vendor - add items, check quantities, then email (Send PO) or download each vendor's PO.">
          Items stay here until the order is placed, so an order can be built over several days. When it has gone to
          the vendor, click <strong>Order placed</strong> (Send PO does that for you); the history is on{" "}
          <Link to="/placed-orders">Placed Orders</Link> and who gets each PO is on{" "}
          <Link to="/vendor-contacts">Vendor contacts</Link>. The full guide is in{" "}
          <Link to="/help" state={{ section: "supplier-projection" }}>Help</Link> (New order, "Purchase Order Cart").
        </InfoBlock>
      </div>
      <PurchaseOrderCart />
    </>
  );
}
