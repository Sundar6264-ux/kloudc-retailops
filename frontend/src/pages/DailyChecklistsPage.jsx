import DailyChecklists from "../components/DailyChecklists.jsx";
import PageHeader from "../ui/PageHeader.jsx";

export default function DailyChecklistsPage() {
  return (
    <>
      <PageHeader pageId="daily_checklists" />
      <DailyChecklists />
    </>
  );
}
