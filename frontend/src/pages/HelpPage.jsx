// /help and /glossary: the Help Center (ui/help/HelpCenter.jsx). Its words
// live in content/help (one source for the website, the app and the server);
// the old hand-written help that was here was moved there and deleted.
import { HelpCenter } from "../ui/help/index.js";

export default function HelpPage({ tab }) {
  return <HelpCenter tab={tab === "glossary" ? "glossary" : undefined} />;
}
