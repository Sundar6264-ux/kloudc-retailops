import { useCallback, useEffect, useRef, useState } from "react";
import { fetchTeamMediaUrl, getMessageSeenBy, pinChatMessage, searchChatMessages } from "../../api";
import TeamMediaImage from "./TeamMediaImage.jsx";
import {
  attachmentOf,
  formatBytes,
  formatDuration,
  highlightParts,
  MAX_VOICE_MS,
  messagePreview,
  pickVoiceMime,
  seenSummary,
} from "./teamAdvancedModel";
import { STORE_TZ } from "./taskUtils";
import useDialogA11y from "./useDialogA11y";

// W5 chat pieces: voice notes and PDFs in a bubble, the voice recorder,
// pinned messages, "Seen by" and message search. Media is auth-checked
// (GET /api/team/media/{token}), so it's fetched with the session into an
// object URL - never a bare src.

const whenFmt = new Intl.DateTimeFormat("en-US", {
  timeZone: STORE_TZ,
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

// Fetched once per mount, revoked on unmount.
function useMediaBlobUrl(url, load) {
  const [src, setSrc] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (!load || !url) return undefined;
    let made = null;
    let cancelled = false;
    setError(null);
    fetchTeamMediaUrl(url)
      .then((u) => {
        made = u;
        if (cancelled) URL.revokeObjectURL(u);
        else setSrc(u);
      })
      .catch((err) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
      if (made) URL.revokeObjectURL(made);
    };
  }, [url, load]);
  return { src, error };
}

export function VoiceNote({ media }) {
  const [load, setLoad] = useState(false);
  const { src, error } = useMediaBlobUrl(media.url, load);
  if (error) return <p className="error chat-voice">Couldn't load the voice message.</p>;
  return (
    <div className="chat-voice">
      {src ? (
        // eslint-disable-next-line jsx-a11y/media-has-caption
        <audio controls autoPlay src={src} preload="auto" aria-label="Voice message" />
      ) : (
        <button type="button" className="chat-voice-play" onClick={() => setLoad(true)} disabled={load}>
          <span aria-hidden="true">▶</span> {load ? "Loading…" : "Play voice message"}
        </button>
      )}
      <span className="chat-voice-len">{formatDuration(media.duration_ms)}</span>
    </div>
  );
}

export function DocumentChip({ media }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  async function open() {
    // Opened first (still inside the click), filled once the file is in -
    // a window opened after an await is blocked as a pop-up.
    const win = window.open("", "_blank");
    setBusy(true);
    setError(null);
    try {
      const url = await fetchTeamMediaUrl(media.url);
      if (win) win.location.href = url;
      else window.location.href = url;
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (err) {
      if (win) win.close();
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="chat-doc">
      <span className="chat-doc-icon" aria-hidden="true">
        PDF
      </span>
      <span className="chat-doc-text">
        <span className="chat-doc-name">{media.name || "document.pdf"}</span>
        <span className="chat-doc-meta">
          {[media.page_count ? `${media.page_count} page${media.page_count === 1 ? "" : "s"}` : null, formatBytes(media.bytes)]
            .filter(Boolean)
            .join(" · ")}
        </span>
      </span>
      <button type="button" className="chat-doc-open" onClick={open} disabled={busy}>
        {busy ? "Opening…" : "Open"}
      </button>
      {error && <span className="error">{error}</span>}
    </div>
  );
}

// Whatever is attached to a message: photo, voice note or PDF.
export function MessageAttachment({ message }) {
  const a = attachmentOf(message);
  if (!a) return null;
  if (a.kind === "audio") return <VoiceNote media={a} />;
  if (a.kind === "document") return <DocumentChip media={a} />;
  return <TeamMediaImage media={a} alt="Photo" className="chat-photo" />;
}

// --- Voice recorder -------------------------------------------------------------

// Records AAC in MP4 (what phones can play) where the browser can; returns
// {supported, recording, elapsedMs, start, stop, cancel, error}. stop()
// resolves to {blob, durationMs} or null.
export function useVoiceRecorder() {
  const mime = typeof window !== "undefined" && window.MediaRecorder ? pickVoiceMime((t) => window.MediaRecorder.isTypeSupported(t)) : null;
  const [recording, setRecording] = useState(false);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [error, setError] = useState(null);
  const rec = useRef(null);

  const cleanup = useCallback(() => {
    const r = rec.current;
    if (!r) return;
    clearInterval(r.timer);
    r.stream.getTracks().forEach((t) => t.stop());
    rec.current = null;
    setRecording(false);
  }, []);

  useEffect(() => cleanup, [cleanup]);

  const start = useCallback(async () => {
    if (!mime || rec.current) return;
    setError(null);
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setError("Microphone access is blocked. Allow it for this site in the browser's settings.");
      return;
    }
    const recorder = new MediaRecorder(stream, { mimeType: mime });
    const chunks = [];
    recorder.ondataavailable = (e) => e.data && e.data.size && chunks.push(e.data);
    const startedAt = Date.now();
    const r = { recorder, stream, chunks, startedAt, timer: null, done: null };
    r.timer = setInterval(() => {
      const ms = Date.now() - startedAt;
      setElapsedMs(ms);
      if (ms >= MAX_VOICE_MS && r.recorder.state === "recording") r.recorder.stop();
    }, 250);
    r.done = new Promise((resolve) => {
      recorder.onstop = () => resolve();
    });
    rec.current = r;
    setElapsedMs(0);
    setRecording(true);
    recorder.start();
  }, [mime]);

  const stop = useCallback(async () => {
    const r = rec.current;
    if (!r) return null;
    if (r.recorder.state === "recording") r.recorder.stop();
    await r.done;
    const durationMs = Math.min(Date.now() - r.startedAt, MAX_VOICE_MS);
    const blob = new Blob(r.chunks, { type: "audio/mp4" });
    cleanup();
    return blob.size ? { blob, durationMs } : null;
  }, [cleanup]);

  const cancel = useCallback(() => {
    const r = rec.current;
    if (r && r.recorder.state === "recording") {
      r.recorder.onstop = null;
      r.recorder.stop();
    }
    cleanup();
  }, [cleanup]);

  return { supported: !!mime, recording, elapsedMs, start, stop, cancel, error };
}

// --- Pinned messages ----------------------------------------------------------------

export function PinsBar({ pins, onJump }) {
  const [open, setOpen] = useState(false);
  if (!pins || !pins.length) return null;
  const top = pins[0];
  return (
    <div className="chat-pins">
      <button type="button" className="chat-pins-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span aria-hidden="true">📌</span>
        <span className="chat-pins-text">
          <strong>{pins.length === 1 ? "Pinned" : `${pins.length} pinned`}</strong> {messagePreview(top)}
        </span>
        <span className="chat-pins-toggle">{open ? "Hide" : "Show all"}</span>
      </button>
      {open && (
        <ul className="chat-pins-list">
          {pins.map((m) => (
            <li key={m.id}>
              <button
                type="button"
                className="chat-pin-item"
                onClick={() => {
                  setOpen(false);
                  onJump(m.id);
                }}
              >
                <span className="chat-pin-who">{m.sender.name}:</span> {messagePreview(m)}
                <span className="muted chat-pin-by">
                  {" "}
                  · pinned by {m.pinned_by?.name}
                  {m.pinned_at ? `, ${whenFmt.format(new Date(m.pinned_at))}` : ""}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export async function togglePin(message) {
  return pinChatMessage(message.id, !message.pinned);
}

// --- Seen by -------------------------------------------------------------------------

export function SeenByDialog({ messageId, onClose }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  useDialogA11y(onClose);
  useEffect(() => {
    getMessageSeenBy(messageId)
      .then(setData)
      .catch((err) => setError(err.message));
  }, [messageId]);
  return (
    <div className="issue-drawer-backdrop chat-dialog-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="chat-dialog" role="dialog" aria-modal="true" aria-label="Seen by">
        <h3>{data ? seenSummary(data) || "Nobody else is in this chat" : "Seen by"}</h3>
        {error && <p className="error">{error}</p>}
        {!data && !error && <p className="muted">Loading…</p>}
        {data && (
          <div className="chat-seen-lists">
            <div>
              <h4>Seen</h4>
              {data.seen.length ? (
                <ul>
                  {data.seen.map((p) => (
                    <li key={p.id}>{p.name}</li>
                  ))}
                </ul>
              ) : (
                <p className="muted">Nobody yet.</p>
              )}
            </div>
            <div>
              <h4>Not yet</h4>
              {data.not_seen.length ? (
                <ul>
                  {data.not_seen.map((p) => (
                    <li key={p.id}>{p.name}</li>
                  ))}
                </ul>
              ) : (
                <p className="muted">Everyone has seen it.</p>
              )}
            </div>
          </div>
        )}
        <div className="chat-dialog-actions">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

// --- Search ---------------------------------------------------------------------------

function Highlight({ text, q }) {
  return highlightParts(text, q).map((p, i) => (p.match ? <mark key={i}>{p.text}</mark> : <span key={i}>{p.text}</span>));
}

// Search messages in every chat I can see (or one, with channelId).
export function ChatSearch({ channelId, onOpen, onClose, autoFocus = true }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState(null);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const seq = useRef(0);

  useEffect(() => {
    const query = q.trim();
    if (query.length < 2) {
      setResults(null);
      setError(null);
      return undefined;
    }
    const mine = ++seq.current;
    const t = setTimeout(() => {
      setBusy(true);
      searchChatMessages(query, { channelId })
        .then((r) => {
          if (mine !== seq.current) return;
          setResults(r.results || []);
          setHasMore(!!r.has_more);
          setError(null);
        })
        .catch((err) => mine === seq.current && setError(err.message))
        .finally(() => mine === seq.current && setBusy(false));
    }, 300);
    return () => clearTimeout(t);
  }, [q, channelId]);

  async function more() {
    const last = results[results.length - 1];
    setBusy(true);
    try {
      const r = await searchChatMessages(q.trim(), { channelId, beforeId: last.id });
      setResults([...results, ...(r.results || [])]);
      setHasMore(!!r.has_more);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="chat-search">
      <div className="chat-search-row">
        <input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={channelId ? "Search this chat" : "Search messages"}
          aria-label={channelId ? "Search this chat" : "Search messages"}
          autoFocus={autoFocus}
          onKeyDown={(e) => e.key === "Escape" && onClose?.()}
        />
        {onClose && (
          <button type="button" className="link-button" onClick={onClose}>
            Close
          </button>
        )}
      </div>
      {q.trim().length > 0 && q.trim().length < 2 && <p className="muted chat-search-note">Type at least 2 characters.</p>}
      {error && <p className="error chat-search-note">{error}</p>}
      {busy && !results && <p className="muted chat-search-note">Searching…</p>}
      {results && results.length === 0 && <p className="muted chat-search-note">No messages found.</p>}
      {results && results.length > 0 && (
        <ul className="chat-search-results">
          {results.map((m) => (
            <li key={m.id}>
              <button type="button" className="chat-search-hit" onClick={() => onOpen(m)}>
                <span className="chat-search-where">
                  {m.channel ? (m.channel.kind === "team" ? `# ${m.channel.name}` : m.channel.name) : ""} · {m.sender.name} ·{" "}
                  {whenFmt.format(new Date(m.created_at))}
                </span>
                <span className="chat-search-text">
                  <Highlight text={messagePreview(m)} q={q.trim()} />
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {hasMore && (
        <button type="button" className="link-button" onClick={more} disabled={busy}>
          {busy ? "Loading…" : "More results"}
        </button>
      )}
    </div>
  );
}
