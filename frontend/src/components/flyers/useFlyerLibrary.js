import { useCallback, useEffect, useRef, useState } from "react";
import { getFlyer, listFlyers } from "../../api";

const PAGE_SIZE = 30;
const LIST_POLL_MS = 4000;
const MAX_POLLED = 6;

// The library list: category filter, search (debounced), paging with
// next_before_id, and live send counts for flyers still going out (each such
// flyer is re-read every few seconds until nothing is pending). `skipPollId`
// is the flyer open in the detail drawer, which polls it itself.
export default function useFlyerLibrary({ skipPollId } = {}) {
  const [category, setCategory] = useState("");
  const [query, setQuery] = useState("");
  const [q, setQ] = useState("");
  const [state, setState] = useState({ items: [], counts: null, next: null, loading: true, error: null });
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState(null);
  const [reloadTick, setReloadTick] = useState(0);
  const [pollTick, setPollTick] = useState(0);
  const requestRef = useRef(0);

  useEffect(() => {
    const timer = setTimeout(() => setQ(query.trim()), 300);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    const req = ++requestRef.current;
    setState((s) => ({ ...s, loading: true, error: null }));
    setMoreError(null);
    listFlyers({ category, q, limit: PAGE_SIZE })
      .then((res) => {
        if (req !== requestRef.current) return;
        setState({
          items: res.items || [],
          counts: res.counts || null,
          next: res.next_before_id ?? null,
          loading: false,
          error: null,
        });
      })
      .catch((err) => {
        if (req !== requestRef.current) return;
        setState((s) => ({ ...s, loading: false, error: err.message || "Couldn't load the library." }));
      });
  }, [category, q, reloadTick]);

  const reload = useCallback(() => setReloadTick((t) => t + 1), []);

  const loadMore = useCallback(() => {
    if (state.next == null || loadingMore) return;
    const req = requestRef.current;
    setLoadingMore(true);
    setMoreError(null);
    listFlyers({ category, q, limit: PAGE_SIZE, beforeId: state.next })
      .then((res) => {
        if (req !== requestRef.current) return; // the filter changed meanwhile
        setState((s) => {
          const seen = new Set(s.items.map((f) => f.id));
          return {
            ...s,
            items: [...s.items, ...(res.items || []).filter((f) => !seen.has(f.id))],
            next: res.next_before_id ?? null,
          };
        });
      })
      .catch((err) => req === requestRef.current && setMoreError(err.message || "Couldn't load more."))
      .finally(() => setLoadingMore(false));
  }, [category, q, state.next, loadingMore]);

  // Replace one flyer's card data (send counts, title...) in place.
  const patchItem = useCallback((flyer) => {
    if (!flyer?.id) return;
    const { sends: _sends, ...card } = flyer;
    setState((s) => ({ ...s, items: s.items.map((f) => (f.id === flyer.id ? { ...f, ...card } : f)) }));
  }, []);

  // Live counts on the cards while sends are still going out.
  useEffect(() => {
    const ids = state.items
      .filter((f) => (f.send_counts?.pending || 0) > 0 && f.id !== skipPollId)
      .slice(0, MAX_POLLED)
      .map((f) => f.id);
    if (ids.length === 0) return undefined;
    let cancelled = false;
    const timer = setTimeout(async () => {
      if (!document.hidden) {
        const results = await Promise.allSettled(ids.map((id) => getFlyer(id)));
        if (cancelled) return;
        for (const r of results) if (r.status === "fulfilled") patchItem(r.value);
      }
      if (!cancelled) setPollTick((t) => t + 1);
    }, LIST_POLL_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [state.items, skipPollId, pollTick, patchItem]);

  return {
    category,
    setCategory,
    query,
    setQuery,
    searching: q,
    items: state.items,
    counts: state.counts,
    hasMore: state.next != null,
    loading: state.loading,
    error: state.error,
    loadingMore,
    moreError,
    loadMore,
    reload,
    patchItem,
  };
}
