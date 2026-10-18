import { useEffect, useState } from "react";
import { fetchTeamMediaUrl } from "../../api";

// An image in a checklist: a display-only "image" item or an uploaded photo
// answer. Team media (/api/team/media/{token}) is auth-checked, so it is
// fetched with the session and shown as an object URL; any other URL is a
// plain <img>. A bare media id with no url (an old answer the server didn't
// describe) shows as a placeholder.
export function mediaUrlFor(media, mediaId) {
  if (media?.url) return media.url;
  if (mediaId && !/^\d+$/.test(String(mediaId))) return `/api/team/media/${mediaId}`;
  return null;
}

export default function ChecklistImage({ url, media, mediaId, alt, className }) {
  const target = url || mediaUrlFor(media, mediaId);
  // Team media needs the session; a public checklist link's form pictures
  // (/api/public/...) and local previews (blob:) are plain images.
  const isTeamMedia = Boolean(target && target.startsWith("/api/") && !target.startsWith("/api/public/"));
  const [src, setSrc] = useState(isTeamMedia ? null : target);
  const [error, setError] = useState(null);

  useEffect(() => {
    setError(null);
    if (!target) return undefined;
    if (!isTeamMedia) {
      setSrc(target);
      return undefined;
    }
    let objectUrl = null;
    let cancelled = false;
    setSrc(null);
    fetchTeamMediaUrl(target)
      .then((u) => {
        objectUrl = u;
        if (cancelled) URL.revokeObjectURL(u);
        else setSrc(u);
      })
      .catch((err) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [target, isTeamMedia]);

  const cls = className || "clf-thumb-img";
  if (!target) return <span className={`${cls} clf-img-placeholder`}>Photo {mediaId ? `#${mediaId}` : ""}</span>;
  if (error) return <span className={`${cls} clf-img-placeholder`}>Couldn't load the image</span>;
  if (!src) return <span className={`${cls} clf-img-placeholder`}>Loading…</span>;
  return (
    <a href={src} target="_blank" rel="noopener noreferrer">
      <img src={src} alt={alt} className={cls} onError={() => setError("failed")} />
    </a>
  );
}
