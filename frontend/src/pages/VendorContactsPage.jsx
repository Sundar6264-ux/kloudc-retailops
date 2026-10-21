import { Link } from "react-router-dom";
import { useAuth } from "../AuthContext.jsx";
import InfoBlock from "../components/InfoBlock.jsx";
import VendorContacts from "../components/poSend/VendorContacts.jsx";
import PageHeader from "../ui/PageHeader.jsx";

// Vendor contacts (automation item 8): where Send PO in the cart emails each
// vendor's purchase order. The demo login is refused by the server (and the
// page says so instead of loading).
export default function VendorContactsPage() {
  const { user } = useAuth();
  return (
    <>
      <PageHeader pageId="vendor_contacts">
        <InfoBlock brief="Who gets each vendor's purchase order when you press Send PO in the cart.">
          Each vendor can have several contacts (a sales rep, the office). When someone presses{" "}
          <strong>Send PO</strong> on a vendor in the <Link to="/purchase-order">Purchase Order Cart</Link>, the
          contacts with an email and <strong>Gets POs</strong> are ticked as recipients; any other saved email can be
          ticked by hand, and nothing else can be typed in. The PO PDF is emailed from the store's email, the order
          is recorded as placed (same as <strong>Order placed</strong>) and those lines leave the cart. WhatsApp
          numbers can be saved, but WhatsApp sending of POs is off for this store. Only an admin can add, change or delete contacts; everyone with the cart can see them.
          Every send is listed under <strong>Recent PO sends</strong>.
        </InfoBlock>
      </PageHeader>
      {user?.demo ? (
        <section className="card">
          <p className="muted">The demo account can't see vendor contacts or send purchase orders.</p>
        </section>
      ) : (
        <VendorContacts isAdmin={!!user?.is_admin} />
      )}
    </>
  );
}
