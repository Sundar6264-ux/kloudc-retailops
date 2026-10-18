import { useEffect, useState } from "react";
import { fetchTeamMediaUrl } from "../../api";

// Team media (announcement images, reported chat photos) is auth-checked
// (GET /api/team/media/{token}), so it can't be a plain <img src>: fetch it
// with the session and show an object URL, revoked when it goes away.
export default function TeamMediaImage({ media, alt, className }) {
  const [src, setSrc] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let url = null;
    let cancelled = false;
    setSrc(null);
    setError(null);
    fetchTeamMediaUrl(media.url)
      .then((u) => {
        url = u;
        if (cancelled) URL.revokeObjectURL(u);
        else setSrc(u);
      })
      .catch((err) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [media.url]);

  if (error) return <p className="error">Couldn't load the image: {error}</p>;
  if (!src) return <p className="muted">Loading image…</p>;
  return (
    <a href={src} target="_blank" rel="noopener noreferrer">
      <img src={src} alt={alt} className={className || "team-media-image"} />
    </a>
  );
}
