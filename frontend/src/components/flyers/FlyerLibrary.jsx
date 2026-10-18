import FlyerImage from "./FlyerImage.jsx";
import { sendSummary, shortDay } from "./flyerModel.js";

function FlyerCard({ flyer, onOpen }) {
  const summary = sendSummary(flyer.send_counts);
  return (
    <button type="button" className="fly-card" onClick={() => onOpen(flyer.id)} aria-label={`${flyer.title}, ${flyer.category_label || ""}. ${summary.text}. Open details`}>
      <span className="fly-card-thumb">
        <FlyerImage path={flyer.thumb_url} kind={flyer.kind} />
        {flyer.kind === "pdf" && (
          <span className="fly-card-kind" aria-hidden="true">
            PDF
          </span>
        )}
      </span>
      <span className="fly-card-body" aria-hidden="true">
        <span className="fly-card-name">{flyer.title}</span>
        <span className="fly-card-meta">
          {flyer.category_label}
          {flyer.created_at && <span className="fly-nowrap"> · {shortDay(flyer.created_at)}</span>}
        </span>
        <span className={`pill ${summary.pill} fly-card-pill`}>{summary.text}</span>
      </span>
    </button>
  );
}

// Library: type chips with counts, search, and a thumbnail grid. A card opens
// the detail drawer.
export default function FlyerLibrary({ lib, categories, onOpen }) {
  const chips = [{ key: "", label: "All" }, ...categories];
  const filtered = Boolean(lib.category || lib.searching);
  const chipCount = (key) => {
    if (!lib.counts) return null;
    const n = key ? lib.counts[key] : lib.counts.all;
    return typeof n === "number" ? n : key ? 0 : null;
  };

  return (
    <section className="card fly-library" aria-labelledby="fly-library-title">
      <div className="fly-library-head">
        <h2 id="fly-library-title" className="fly-section-title">
          Library
        </h2>
        <input
          type="search"
          className="fly-search"
          aria-label="Search flyers"
          placeholder="Search flyers"
          value={lib.query}
          onChange={(e) => lib.setQuery(e.target.value)}
        />
      </div>
      <div className="fly-chips" role="group" aria-label="Filter by type">
        {chips.map((c) => {
          const n = chipCount(c.key);
          const on = lib.category === c.key;
          return (
            <button
              type="button"
              key={c.key || "all"}
              className={`fly-chip${on ? " fly-chip-on" : ""}`}
              aria-pressed={on}
              onClick={() => lib.setCategory(c.key)}
            >
              {c.label}
              {n != null && <span className="fly-chip-count">{n}</span>}
            </button>
          );
        })}
      </div>

      {lib.error && (
        <p className="error" role="alert">
          Couldn't load the library: {lib.error}{" "}
          <button type="button" className="link-button fly-inline-link" onClick={lib.reload}>
            Try again
          </button>
        </p>
      )}

      {!lib.error && lib.items.length === 0 && lib.loading && (
        <ul className="fly-grid" aria-hidden="true">
          {[0, 1, 2, 3].map((i) => (
            <li key={i}>
              <span className="fly-card fly-card-skeleton">
                <span className="fly-card-thumb">
                  <span className="fly-img fly-img-loading" />
                </span>
                <span className="fly-card-body">
                  <span className="fly-skel-line" />
                  <span className="fly-skel-line fly-skel-short" />
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}

      {!lib.error && lib.items.length === 0 && !lib.loading && (
        <div className="fly-empty">
          {filtered ? (
            <>
              <p>No flyers match{lib.searching ? ` "${lib.searching}"` : ""}.</p>
              <button
                type="button"
                className="btn-secondary fly-btn-sm"
                onClick={() => {
                  lib.setQuery("");
                  lib.setCategory("");
                }}
              >
                Show all flyers
              </button>
            </>
          ) : (
            <p>
              No flyers yet. Upload one above - it's kept here and in the Spice Town Flyers folder on the Mac mini, filed
              by type.
            </p>
          )}
        </div>
      )}

      {lib.items.length > 0 && (
        <ul className={`fly-grid${lib.loading ? " fly-grid-refreshing" : ""}`} aria-busy={lib.loading || undefined}>
          {lib.items.map((f) => (
            <li key={f.id}>
              <FlyerCard flyer={f} onOpen={onOpen} />
            </li>
          ))}
        </ul>
      )}

      {lib.moreError && (
        <p className="error" role="alert">
          {lib.moreError}
        </p>
      )}
      {lib.hasMore && lib.items.length > 0 && (
        <div className="fly-more">
          <button type="button" className="btn-secondary" onClick={lib.loadMore} disabled={lib.loadingMore}>
            {lib.loadingMore ? "Loading…" : "Show more"}
          </button>
        </div>
      )}
    </section>
  );
}
