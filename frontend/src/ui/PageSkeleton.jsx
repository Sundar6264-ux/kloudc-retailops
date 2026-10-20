// What a page shows while its code loads (route-level code splitting,
// website ready A10): the frozen header's height and a few soft rows, so the
// page does not jump when it arrives. Announced politely to screen readers;
// still under prefers-reduced-motion.
export default function PageSkeleton({ bare = false }) {
  return (
    <div className={`kc-skeleton${bare ? " kc-skeleton--bare" : ""}`} role="status" aria-live="polite" aria-busy="true">
      <span className="sr-only">Loading…</span>
      <div className="kc-skeleton-header" aria-hidden="true">
        <span className="kc-skeleton-bar kc-skeleton-bar--title" />
        <span className="kc-skeleton-bar kc-skeleton-bar--search" />
      </div>
      <div className="kc-skeleton-body" aria-hidden="true">
        {[0, 1, 2, 3, 4].map((i) => (
          <span key={i} className="kc-skeleton-bar kc-skeleton-bar--row" />
        ))}
      </div>
    </div>
  );
}
