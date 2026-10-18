import { useEffect, useState } from "react";
import { fetchFlyerObjectUrl } from "../../api";

// A flyer picture from the API (thumb_url / file_url). Those paths need the
// session, so the picture is fetched with it and shown from an object URL -
// the same approach as checklist photos (ChecklistImage.jsx).
//
// Thumbnails are cached for as long as the Flyers page is open (paging,
// filters and live status refreshes never fetch one twice); the page calls
// clearFlyerImageCache when it closes. A full-size picture (cache={false}) is
// fetched for one view and freed when that view goes away.

const cache = new Map(); // path -> {promise, url}

function cachedObjectUrl(path) {
  let entry = cache.get(path);
  if (!entry) {
    entry = { url: null, promise: null };
    entry.promise = fetchFlyerObjectUrl(path).then(
      (url) => {
        entry.url = url;
        return url;
      },
      (err) => {
        cache.delete(path); // a later view tries again
        throw err;
      },
    );
    cache.set(path, entry);
  }
  return entry;
}

export function clearFlyerImageCache() {
  for (const entry of cache.values()) {
    if (entry.url) URL.revokeObjectURL(entry.url);
  }
  cache.clear();
}

export default function FlyerImage({ path, alt = "", className = "", cache: useCache = true, onSrc, kind }) {
  const [state, setState] = useState(() => ({
    src: useCache && path ? cache.get(path)?.url || null : null,
    failed: false,
  }));

  useEffect(() => {
    if (!path) {
      setState({ src: null, failed: true });
      return undefined;
    }
    let cancelled = false;
    let own = null;
    if (useCache) {
      const entry = cachedObjectUrl(path);
      if (entry.url) {
        setState({ src: entry.url, failed: false });
        onSrc?.(entry.url);
        return undefined;
      }
      setState({ src: null, failed: false });
      entry.promise
        .then((url) => {
          if (!cancelled) {
            setState({ src: url, failed: false });
            onSrc?.(url);
          }
        })
        .catch(() => !cancelled && setState({ src: null, failed: true }));
    } else {
      setState({ src: null, failed: false });
      fetchFlyerObjectUrl(path)
        .then((url) => {
          if (cancelled) {
            URL.revokeObjectURL(url);
            return;
          }
          own = url;
          setState({ src: url, failed: false });
          onSrc?.(url);
        })
        .catch(() => !cancelled && setState({ src: null, failed: true }));
    }
    return () => {
      cancelled = true;
      if (own) {
        onSrc?.(null);
        URL.revokeObjectURL(own);
      }
    };
    // onSrc is a notification only; a new function each render must not refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, useCache]);

  if (state.failed) {
    return (
      <span className={`fly-img fly-img-missing ${className}`} role={alt ? "img" : undefined} aria-label={alt || undefined}>
        <span aria-hidden="true" className="fly-img-missing-icon">
          {kind === "pdf" ? "PDF" : "?"}
        </span>
        <span className="fly-img-missing-text">{kind === "pdf" ? "PDF" : "No preview"}</span>
      </span>
    );
  }
  if (!state.src) {
    return alt ? (
      <span className={`fly-img fly-img-loading ${className}`} role="img" aria-label={`${alt} (loading)`} />
    ) : (
      <span className={`fly-img fly-img-loading ${className}`} aria-hidden="true" />
    );
  }
  return (
    <img
      className={`fly-img ${className}`}
      src={state.src}
      alt={alt}
      draggable={false}
      onError={() => setState({ src: null, failed: true })}
    />
  );
}
