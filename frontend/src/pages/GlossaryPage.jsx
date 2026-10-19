// The Glossary: every status, label and idea, from /api/glossary plus the
// concept terms in content/help (ui/help/GlossaryList.jsx). /glossary renders
// it as the Help Center's Glossary tab (HelpPage tab="glossary").
import { GlossaryList } from "../ui/help/index.js";

export default function GlossaryPage() {
  return <GlossaryList />;
}
