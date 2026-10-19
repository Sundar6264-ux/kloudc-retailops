// Help, glossary and ⓘ info for the website (docs/DESIGN_SYSTEM.md 4 and 5.3).
// Content: content/help/*.json (one source for server, website and app),
// imported at build time through "@help". H owns this folder; W places the
// components (PageHeader renders <InfoButton pageId=... />, the shell mounts
// <HelpPanel />, the top bar's ? calls useHelp().togglePanel(pageId)).
export { HelpProvider, useHelp, useHelpWords } from "./HelpProvider.jsx";
export { default as InfoButton, FieldInfo, InfoContent } from "./InfoButton.jsx";
export { default as HelpLink, helpHref } from "./HelpLink.jsx";
export { default as HelpPanel } from "./HelpPanel.jsx";
export { default as HelpCenter } from "./HelpCenter.jsx";
export { default as GlossaryList } from "./GlossaryList.jsx";
export { HelpText, HelpBlocks } from "./HelpText.jsx";
export { HelpSearchResults, useHelpSearch, canOpenPageFor } from "./HelpSearch.jsx";
export { HelpIcon } from "./helpIcons.jsx";
export {
  AREAS,
  HELP_TABS,
  LEGACY_SECTIONS,
  glossaryPath,
  helpPath,
  pageIdForPath,
  supportMailto,
  whoCanSee,
} from "./helpModel.js";
