import ReorderCandidates from "../components/ReorderCandidates.jsx";
import PageHeader from "../ui/PageHeader.jsx";

export default function ReorderPage() {
  return (
    <>
      <PageHeader pageId="reorder" />
      <ReorderCandidates />
    </>
  );
}
