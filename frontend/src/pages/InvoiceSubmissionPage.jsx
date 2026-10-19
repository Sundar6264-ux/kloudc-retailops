import InvoiceSubmission from "../components/InvoiceSubmission.jsx";
import PageHeader from "../ui/PageHeader.jsx";

export default function InvoiceSubmissionPage() {
  return (
    <>
      <PageHeader pageId="invoices" />
      <InvoiceSubmission />
    </>
  );
}
