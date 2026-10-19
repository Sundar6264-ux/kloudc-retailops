// Help context: the bundled content (ui/help/content.js), the right-side Help
// panel's state, and useHelp() - every lookup with this store's words filled
// in. Mounted once in main.jsx above everything; the store words come from
// useStore(), which works with or without the store providers.
import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import { useStore } from "../../store/StoreContext.jsx";
import { useGlossary } from "../../glossary.jsx";
import { HELP } from "./content.js";
import {
  fillDeep,
  mergeTerms,
  pageIdForPath,
  searchHelp,
  storeWords,
} from "./helpModel.js";

const HelpContext = createContext(null);

export function HelpProvider({ children, content = HELP }) {
  const [panel, setPanel] = useState({ open: false, pageId: null, section: null, extra: null });
  const panelsMounted = useRef(0);

  const openPanel = useCallback((pageId, opts = {}) => {
    setPanel({ open: true, pageId: pageId || null, section: opts.section || null, extra: opts.extra || null });
  }, []);
  const closePanel = useCallback(() => setPanel((p) => ({ ...p, open: false })), []);
  const togglePanel = useCallback(
    (pageId) => setPanel((p) => (p.open ? { ...p, open: false } : { open: true, pageId: pageId || null, section: null, extra: null })),
    [],
  );
  // <HelpPanel /> registers itself, so an ⓘ knows whether a panel exists
  // to open (else it shows its own popover / sheet).
  const registerPanel = useCallback(() => {
    panelsMounted.current += 1;
    return () => {
      panelsMounted.current -= 1;
    };
  }, []);
  const hasPanel = useCallback(() => panelsMounted.current > 0, []);

  const value = useMemo(
    () => ({ content, panel, openPanel, closePanel, togglePanel, registerPanel, hasPanel }),
    [content, panel, openPanel, closePanel, togglePanel, registerPanel, hasPanel],
  );
  return <HelpContext.Provider value={value}>{children}</HelpContext.Provider>;
}

// The words this store's text is filled with ("{pos}" -> "Toast").
export function useHelpWords() {
  const store = useStore();
  const ctx = useContext(HelpContext);
  const email = ctx?.content?.contact?.email;
  return useMemo(
    () =>
      storeWords({
        posName: store?.posName,
        storeName: store?.name && store.name !== store.productName ? store.name : null,
        storeCity: store?.timezone ? String(store.timezone).split("/").pop().replace(/_/g, " ") : null,
        supportEmail: email,
      }),
    [store?.posName, store?.name, store?.productName, store?.timezone, email],
  );
}

// Everything help, filled with this store's words:
//   page(id), section(id, sec), article(id), term(key), terms(), search(q, opts),
//   faq(), troubleshooting(), gettingStarted(), contact(), pageIdForPath(path),
//   openPanel(pageId, {section, extra}), closePanel(), togglePanel(pageId), panel.
export function useHelp() {
  const ctx = useContext(HelpContext);
  const words = useHelpWords();
  const glossary = useGlossary();
  const storeCode = useStore()?.code || null;
  const content = ctx?.content || HELP;

  return useMemo(() => {
    const cache = new Map();
    const filled = (key, value) => {
      if (value == null) return null;
      if (!cache.has(key)) cache.set(key, fillDeep(value, words));
      return cache.get(key);
    };
    let termList = null;
    const terms = () => {
      if (!termList) termList = mergeTerms(glossary?.list, content.conceptList, words);
      return termList;
    };
    const termByKey = (key) => {
      if (!key) return null;
      const api = glossary?.terms?.[key];
      if (api) return { ...filled(`c:${key}`, content.concepts.get(key)), ...api };
      return filled(`c:${key}`, content.concepts.get(key));
    };
    return {
      content,
      words,
      storeCode,
      glossaryLoaded: !!glossary?.loaded,
      apiCategories: glossary?.categories || [],
      page: (id) => filled(`p:${id}`, content.pages.get(id)),
      section: (id, sec) => filled(`p:${id}`, content.pages.get(id))?.sections?.[sec] || null,
      article: (id) => filled(`a:${id}`, content.articles.get(id)),
      articles: () => content.articleList.map((a) => filled(`a:${a.id}`, a)),
      term: termByKey,
      terms,
      faq: () => filled("faq", content.faq),
      troubleshooting: () => filled("trouble", content.troubleshooting),
      gettingStarted: () => filled("start", content.gettingStarted),
      contact: () => filled("contact", content.contact),
      pages: () => content.pageList.map((p) => filled(`p:${p.id}`, p)),
      pageIdForPath: (path) => pageIdForPath(content, path),
      search: (q, opts = {}) => {
        const results = searchHelp(content, q, { terms: terms(), ...opts });
        return results.map((r) => ({ ...r, title: fillDeep(r.title, words), where: fillDeep(r.where, words) }));
      },
      panel: ctx?.panel || { open: false },
      openPanel: ctx?.openPanel || (() => {}),
      closePanel: ctx?.closePanel || (() => {}),
      togglePanel: ctx?.togglePanel || (() => {}),
      registerPanel: ctx?.registerPanel || (() => () => {}),
      hasPanel: ctx?.hasPanel || (() => false),
    };
  }, [ctx, content, words, glossary, storeCode]);
}
