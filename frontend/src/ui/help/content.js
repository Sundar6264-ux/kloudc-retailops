// The help content, imported at build time from ../content/help through the
// "@help" alias (vite.config.js). One source for the server, this website and
// the app: docs/DESIGN_SYSTEM.md 5.2. Nothing here is fetched.
import manifest from "@help/manifest.json";
import pages from "@help/pages.json";
import gettingStarted from "@help/getting-started.json";
import faq from "@help/faq.json";
import troubleshooting from "@help/troubleshooting.json";
import glossary from "@help/glossary.json";
import glossaryCategories from "@help/glossary-categories.json";
import contact from "@help/contact.json";
import { buildHelp } from "./helpModel.js";

const articleModules = import.meta.glob("@help/articles/*.json", { eager: true, import: "default" });
const articles = Object.values(articleModules).sort((a, b) => (a.order ?? 999) - (b.order ?? 999) || a.title.localeCompare(b.title));

export const RAW_HELP = { manifest, pages, articles, gettingStarted, faq, troubleshooting, glossary, glossaryCategories, contact };

export const HELP = buildHelp(RAW_HELP);
