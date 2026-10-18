import { Link, useParams } from "react-router-dom";
import ChecklistAdminList from "../components/checklists/ChecklistAdminList.jsx";
import ChecklistEditor from "../components/checklists/ChecklistEditor.jsx";

// Admin-only Checklist builder: /checklists/admin (list) and
// /checklists/admin/:type (editor).
export default function ChecklistAdminPage() {
  const { type } = useParams();
  if (type) return <ChecklistEditor key={type} checklistType={type} />;
  return (
    <>
      <div className="page-header">
        <h1>Checklist builder</h1>
        <p className="muted">
          Build the daily checklists like a Google Form. <Link to="/daily-checklists">Back to Daily Checklists</Link>
        </p>
      </div>
      <ChecklistAdminList />
    </>
  );
}
