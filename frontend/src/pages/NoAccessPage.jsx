import PageHeader from "../ui/PageHeader.jsx";
import EmptyState from "../ui/EmptyState.jsx";

export default function NoAccessPage() {
  return (
    <>
      <PageHeader pageId="no_access" title="No access yet" />
      <section className="card">
        <EmptyState icon="lock" title="Nothing to open yet">
          Your account hasn't been granted access to any dashboard tab yet. Ask an admin to enable some for you on the Users page.
        </EmptyState>
      </section>
    </>
  );
}
