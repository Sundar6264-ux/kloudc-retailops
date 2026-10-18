import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  blockUser,
  deleteChatMessage,
  getChatChannel,
  listChannelMembers,
  listChatMessages,
  markChannelRead,
  reportChatMessage,
  sendChatMessage,
  unblockUser,
  updateMyChannel,
  uploadTeamMedia,
  uploadChatAttachment,
  listChannelPins,
} from "../../api";
import { ChatSearch, MessageAttachment, PinsBar, SeenByDialog, togglePin, useVoiceRecorder } from "./ChatExtras.jsx";
import { checkDocument, displayBody, formatDuration } from "./teamAdvancedModel";
import { STORE_TZ, storeDay, todayStoreDay } from "./taskUtils";
import useDialogA11y from "./useDialogA11y";
import usePageActive, { isPageVisible, onPageShown } from "./usePageActive";
import { refreshTeamBadges } from "./useTeamBadges";

// One chat conversation (team channel or DM): messages oldest-first grouped
// by day, "Load earlier", a 5 s poll for new ones while it's open, and a
// composer that shows a message straight away and retries a failed send
// with the SAME client_uuid (the server returns the original, so a retry
// can never post twice). docs/TEAM_HUB_DESIGN.md §4.4.

const PAGE = 50;
// Same limit as the server (chat_service.MAX_BODY).
const MAX_BODY = 4000;
// How many pages of already-loaded messages to re-read when some of them
// were deleted or moderated.
const MAX_WINDOW_PAGES = 10;

const timeFmt = new Intl.DateTimeFormat("en-US", { timeZone: STORE_TZ, hour: "numeric", minute: "2-digit" });
const dayFmt = new Intl.DateTimeFormat("en-US", { timeZone: STORE_TZ, weekday: "long", month: "short", day: "numeric" });

function newUuid() {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function dayLabel(iso) {
  const day = storeDay(new Date(iso));
  const today = todayStoreDay();
  if (day === today) return "Today";
  const y = new Date();
  y.setDate(y.getDate() - 1);
  if (day === storeDay(y)) return "Yesterday";
  return dayFmt.format(new Date(iso));
}

// Server messages merged by id; a pending bubble is replaced by the server
// copy with the same client_uuid.
function merge(list, incoming) {
  const byId = new Map(list.filter((m) => !m.pending).map((m) => [m.id, m]));
  for (const m of incoming) byId.set(m.id, m);
  const uuids = new Set([...byId.values()].map((m) => m.client_uuid));
  const pending = list.filter((m) => m.pending && !uuids.has(m.client_uuid));
  return [...[...byId.values()].sort((a, b) => a.id - b.id), ...pending];
}

function escapeRegExp(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// "@name" highlighter. Names can contain spaces ("@Sai Durga"), so it
// matches the channel's actual member names - longest first, any case,
// followed by a non-word character or the end - the same rule the server
// uses to decide who was mentioned. Without members it falls back to a
// single word.
function mentionPattern(names) {
  const sorted = [...new Set(names.filter(Boolean))].sort((a, b) => b.length - a.length);
  if (!sorted.length) return /(@[\w.-]*\w)/g;
  return new RegExp(`(@(?:${sorted.map(escapeRegExp).join("|")}))(?=\\W|$)`, "gi");
}

function MessageText({ text, pattern }) {
  if (!text) return null;
  const parts = text.split(pattern);
  return (
    <span className="chat-text">
      {parts.map((p, i) =>
        i % 2 === 1 ? (
          <span key={i} className="chat-mention">
            {p}
          </span>
        ) : (
          p
        ),
      )}
    </span>
  );
}

// A small modal asking for a reason (report / manager delete).
function ReasonDialog({ title, label, confirmLabel, required, onSubmit, onClose }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  useDialogA11y(() => !busy && onClose());

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onSubmit(reason.trim());
      onClose();
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  return (
    <div
      className="issue-drawer-backdrop chat-dialog-backdrop"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <form className="chat-dialog" onSubmit={submit} role="dialog" aria-modal="true" aria-label={title}>
        <h3>{title}</h3>
        <label>
          {label}
          <textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} autoFocus />
        </label>
        {error && <p className="error">{error}</p>}
        <div className="chat-dialog-actions">
          <button type="button" className="btn-secondary" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="submit" disabled={busy || (required && !reason.trim())}>
            {confirmLabel}
          </button>
        </div>
      </form>
    </div>
  );
}

function MessageMenu({ message, mine, isAdmin, canPin, onCopy, onCreateTask, onReport, onDelete, onPin, onSeenBy }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useDialogA11y(() => setOpen(false), open);

  useEffect(() => {
    if (!open) return undefined;
    function close(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  if (message.deleted || message.pending) return null;
  const pick = (fn) => () => {
    setOpen(false);
    fn();
  };
  return (
    <div className="chat-menu" ref={ref}>
      <button
        type="button"
        className="chat-menu-button"
        aria-label="Message actions"
        title="Message actions"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        ⋯
      </button>
      {open && (
        <div className={`chat-menu-list ${mine ? "chat-menu-list-mine" : ""}`} role="menu">
          {message.body && !message.body_is_label && (
            <button type="button" role="menuitem" className="chat-menu-item" onClick={pick(onCopy)}>
              Copy text
            </button>
          )}
          {canPin && (
            <button type="button" role="menuitem" className="chat-menu-item" onClick={pick(onPin)}>
              {message.pinned ? "Unpin" : "Pin"}
            </button>
          )}
          {mine && (
            <button type="button" role="menuitem" className="chat-menu-item" onClick={pick(onSeenBy)}>
              Seen by…
            </button>
          )}
          <button type="button" role="menuitem" className="chat-menu-item" onClick={pick(onCreateTask)}>
            Create task
          </button>
          {!mine && (
            <button type="button" role="menuitem" className="chat-menu-item" onClick={pick(onReport)}>
              Report
            </button>
          )}
          {(mine || isAdmin) && (
            <button type="button" role="menuitem" className="chat-menu-item chat-menu-danger" onClick={pick(onDelete)}>
              Delete
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export default function ChatConversation({
  channelId,
  jumpToMessageId = null,
  me,
  isAdmin,
  pollSeconds,
  messageEdits,
  myBlocks,
  onBack,
  onChannelChanged,
  onBlocksChanged,
  onCreateTask,
}) {
  const [channel, setChannel] = useState(null);
  const [messages, setMessages] = useState(null);
  const [hasMore, setHasMore] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [members, setMembers] = useState([]);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [draft, setDraft] = useState("");
  const [photo, setPhoto] = useState(null);
  const [mention, setMention] = useState(null); // {query, start}
  const [dialog, setDialog] = useState(null); // {kind, message}
  // W5: a PDF waiting to be sent, pinned messages, in-chat search.
  const [doc, setDoc] = useState(null);
  const [pinsInfo, setPinsInfo] = useState({ pins: [], can_pin: false });
  const [searching, setSearching] = useState(false);
  const [flashId, setFlashId] = useState(null);
  const docRef = useRef(null);
  const voice = useVoiceRecorder();
  const listRef = useRef(null);
  const inputRef = useRef(null);
  const fileRef = useRef(null);
  const stickToBottom = useRef(true);
  const lastMarked = useRef(0);
  const messagesRef = useRef([]);
  messagesRef.current = messages || [];
  const photoRef = useRef(null);
  photoRef.current = photo;
  const active = usePageActive();

  // Photo previews (the composer's, and pending bubbles') are blob URLs
  // holding the whole file - free them when this conversation goes away.
  useEffect(
    () => () => {
      if (photoRef.current?.url) URL.revokeObjectURL(photoRef.current.url);
      for (const m of messagesRef.current) if (m.pending && m.previewUrl) URL.revokeObjectURL(m.previewUrl);
    },
    [],
  );

  const newestServerId = useCallback(() => {
    const ids = messagesRef.current.filter((m) => !m.pending).map((m) => m.id);
    return ids.length ? Math.max(...ids) : 0;
  }, []);

  const refreshChannel = useCallback(() => {
    return getChatChannel(channelId)
      .then(setChannel)
      .catch((err) => setError(err.message));
  }, [channelId]);

  // First load: channel, newest page, members.
  useEffect(() => {
    let cancelled = false;
    setChannel(null);
    setMessages(null);
    setError(null);
    setNotice(null);
    setDraft("");
    setPhoto(null);
    lastMarked.current = 0;
    stickToBottom.current = true;
    Promise.all([getChatChannel(channelId), listChatMessages(channelId, { limit: PAGE })])
      .then(([c, page]) => {
        if (cancelled) return;
        setChannel(c);
        setMessages(page.messages || []);
        setHasMore(!!page.has_more);
      })
      .catch((err) => !cancelled && setError(err.message));
    listChannelMembers(channelId)
      .then((m) => !cancelled && setMembers(m))
      .catch(() => {});
    listChannelPins(channelId)
      .then((d) => !cancelled && setPinsInfo(d))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [channelId]);

  const loadPins = useCallback(() => {
    listChannelPins(channelId)
      .then(setPinsInfo)
      .catch(() => {});
  }, [channelId]);

  // New messages while it's open - only while the tab is visible (a
  // hidden tab skips ticks and catches up the moment it's shown again).
  const loaded = messages !== null;
  useEffect(() => {
    if (!loaded) return undefined;
    let busy = false;
    async function tick() {
      if (busy || !isPageVisible()) return;
      busy = true;
      try {
        const after = newestServerId();
        const page = await listChatMessages(channelId, after ? { afterId: after, limit: PAGE } : { limit: PAGE });
        if (page.messages?.length) setMessages((prev) => merge(prev || [], page.messages));
        refreshChannel();
      } catch {
        // Try again next tick.
      } finally {
        busy = false;
      }
    }
    const ms = Math.max(2, pollSeconds || 5) * 1000;
    const timer = setInterval(tick, ms);
    const stopShown = onPageShown(tick);
    return () => {
      clearInterval(timer);
      stopShown();
    };
  }, [channelId, loaded, pollSeconds, newestServerId, refreshChannel]);

  // Messages already on screen that were deleted or removed by a manager
  // (the change feed reports them): re-read the loaded window so they
  // update here too, not only after a reload.
  const editTick = messageEdits?.tick;
  useEffect(() => {
    if (!editTick || !loaded) return undefined;
    loadPins();
    const loadedIds = new Set(messagesRef.current.filter((m) => !m.pending).map((m) => m.id));
    if (!(messageEdits.ids || []).some((id) => loadedIds.has(id))) return undefined;
    let cancelled = false;
    (async () => {
      try {
        let after = Math.min(...loadedIds) - 1;
        const newest = Math.max(...loadedIds);
        const fresh = [];
        for (let i = 0; i < MAX_WINDOW_PAGES; i += 1) {
          const page = await listChatMessages(channelId, { afterId: after, limit: 100 });
          const rows = page.messages || [];
          fresh.push(...rows);
          if (!page.has_more || !rows.length || rows[rows.length - 1].id >= newest) break;
          after = rows[rows.length - 1].id;
        }
        if (!cancelled && fresh.length) setMessages((prev) => merge(prev || [], fresh));
      } catch {
        // The next change or a reload picks it up.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [editTick, loaded, channelId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Read marker: the newest message on screen - only while someone can
  // actually see it (tab visible and focused); otherwise it waits until
  // they come back.
  useEffect(() => {
    if (!active) return;
    const newest = newestServerId();
    if (!newest || newest <= lastMarked.current) return;
    lastMarked.current = newest;
    markChannelRead(channelId, newest)
      .then(() => {
        refreshTeamBadges();
        onChannelChanged?.();
      })
      .catch(() => {
        lastMarked.current = 0;
      });
  }, [messages, active, channelId, newestServerId, onChannelChanged]);

  // Keep the view at the bottom unless the reader scrolled up.
  useEffect(() => {
    const el = listRef.current;
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight;
  }, [messages]);

  function onScroll() {
    const el = listRef.current;
    if (!el) return;
    stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  }

  async function loadOlder() {
    const oldest = messagesRef.current.filter((m) => !m.pending).map((m) => m.id);
    if (!oldest.length) return;
    setLoadingOlder(true);
    const el = listRef.current;
    const before = el ? el.scrollHeight - el.scrollTop : 0;
    try {
      const page = await listChatMessages(channelId, { beforeId: Math.min(...oldest), limit: PAGE });
      stickToBottom.current = false;
      setMessages((prev) => merge(prev || [], page.messages || []));
      setHasMore(!!page.has_more);
      requestAnimationFrame(() => {
        if (el) el.scrollTop = el.scrollHeight - before;
      });
    } catch (err) {
      setError(err.message);
    } finally {
      setLoadingOlder(false);
    }
  }

  // --- Sending ---------------------------------------------------------------

  async function deliver(pending) {
    setMessages((prev) =>
      (prev || []).map((m) => (m.client_uuid === pending.client_uuid ? { ...m, failed: false, error: null } : m)),
    );
    try {
      let token = pending.mediaToken;
      if (pending.file && !token) {
        const media =
          pending.kind && pending.kind !== "image"
            ? await uploadChatAttachment(pending.file, { filename: pending.filename, durationMs: pending.durationMs })
            : await uploadTeamMedia(pending.file);
        token = media.token;
        // Keep the token so a retry doesn't upload the photo again.
        setMessages((prev) =>
          (prev || []).map((m) => (m.client_uuid === pending.client_uuid ? { ...m, mediaToken: token } : m)),
        );
      }
      const saved = await sendChatMessage(channelId, {
        body: pending.body,
        clientUuid: pending.client_uuid,
        mediaToken: token,
      });
      stickToBottom.current = true;
      setMessages((prev) => merge(prev || [], [saved]));
      // The server copy shows the uploaded photo now - free the local one.
      if (pending.previewUrl) URL.revokeObjectURL(pending.previewUrl);
      onChannelChanged?.();
    } catch (err) {
      // Network trouble and server errors are worth another try; a 4xx
      // (photo type not allowed, too long, blocked...) won't change on retry.
      const retryable = !err.status || err.status >= 500 || err.status === 408 || err.status === 429;
      setMessages((prev) =>
        (prev || []).map((m) =>
          m.client_uuid === pending.client_uuid ? { ...m, failed: true, error: err.message, retryable } : m,
        ),
      );
      if (err.status === 403) refreshChannel();
    }
  }

  function discard(pending) {
    if (pending.previewUrl) URL.revokeObjectURL(pending.previewUrl);
    setMessages((prev) => (prev || []).filter((m) => m.client_uuid !== pending.client_uuid));
  }

  function send() {
    const body = draft.trim();
    if (!body && !photo && !doc) return;
    if (body.length > MAX_BODY) return;
    const pending = {
      pending: true,
      client_uuid: newUuid(),
      body,
      file: doc?.file || photo?.file || null,
      kind: doc ? "document" : photo ? "image" : null,
      filename: doc?.file?.name,
      previewUrl: doc ? null : photo?.url || null,
      mediaToken: null,
      sender: { id: me.id, name: me.username },
      created_at: new Date().toISOString(),
    };
    setDraft("");
    setPhoto(null);
    setDoc(null);
    setMention(null);
    stickToBottom.current = true;
    setMessages((prev) => [...(prev || []), pending]);
    deliver(pending);
    inputRef.current?.focus();
  }

  function onKeyDown(e) {
    if (mention && mentionOptions.length && (e.key === "Enter" || e.key === "Tab")) {
      e.preventDefault();
      pickMention(mentionOptions[0]);
      return;
    }
    if (e.key === "Escape" && mention) {
      setMention(null);
      return;
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  function onDraftChange(e) {
    const value = e.target.value;
    setDraft(value);
    const caret = e.target.selectionStart ?? value.length;
    // Names can have spaces ("@Sai Du"), so keep the picker open while the
    // text after "@" still starts some member's name.
    const match = /(^|\s)@([^@\n]{0,40})$/.exec(value.slice(0, caret));
    const query = match ? match[2].toLowerCase() : null;
    const open =
      query !== null &&
      (!/\s/.test(query) || members.some((m) => m.name.toLowerCase().startsWith(query)));
    setMention(open ? { query, start: caret - match[2].length - 1 } : null);
  }

  const mentionOptions = useMemo(() => {
    if (!mention) return [];
    return members
      .filter((m) => m.id !== me.id && m.name.toLowerCase().startsWith(mention.query))
      .slice(0, 6);
  }, [mention, members, me.id]);

  function pickMention(person) {
    if (!mention) return;
    const caret = inputRef.current?.selectionStart ?? draft.length;
    const next = `${draft.slice(0, mention.start)}@${person.name} ${draft.slice(caret)}`;
    setDraft(next);
    setMention(null);
    requestAnimationFrame(() => {
      const pos = mention.start + person.name.length + 2;
      inputRef.current?.focus();
      inputRef.current?.setSelectionRange(pos, pos);
    });
  }

  // W5: a voice note is sent as soon as recording stops (like WhatsApp),
  // with whatever text is typed.
  async function stopVoice() {
    const rec = await voice.stop();
    if (!rec) return;
    const pending = {
      pending: true,
      client_uuid: newUuid(),
      body: "",
      file: rec.blob,
      kind: "audio",
      filename: "voice-note.m4a",
      durationMs: rec.durationMs,
      mediaToken: null,
      sender: { id: me.id, name: me.username },
      created_at: new Date().toISOString(),
    };
    stickToBottom.current = true;
    setMessages((prev) => [...(prev || []), pending]);
    deliver(pending);
  }

  function pickDocument(e) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    const problem = checkDocument(file);
    if (problem) {
      setError(problem);
      return;
    }
    if (photo?.url) URL.revokeObjectURL(photo.url);
    setPhoto(null);
    setDoc({ file });
  }

  async function pin(message) {
    try {
      const updated = await togglePin(message);
      setMessages((prev) => merge(prev || [], [updated]));
      setNotice(updated.pinned ? "Pinned for everyone in this chat." : "Unpinned.");
      loadPins();
    } catch (err) {
      setError(err.message);
    }
  }

  // Scroll to a message already on screen (pins, search) and flash it.
  function jumpTo(messageId) {
    const el = listRef.current?.querySelector(`[data-message-id="${messageId}"]`);
    if (!el) {
      setNotice("That message is further back - use \"Load earlier messages\" at the top.");
      return;
    }
    stickToBottom.current = false;
    el.scrollIntoView({ block: "center", behavior: "smooth" });
    setFlashId(messageId);
    setTimeout(() => setFlashId((v) => (v === messageId ? null : v)), 2000);
  }

  // A search hit opened from the chat list: once the messages are in, go to
  // it (older ones may need "Load earlier").
  const jumped = useRef(null);
  useEffect(() => {
    if (!jumpToMessageId || messages === null || jumped.current === jumpToMessageId) return;
    jumped.current = jumpToMessageId;
    requestAnimationFrame(() => jumpTo(jumpToMessageId));
  }, [jumpToMessageId, messages]); // eslint-disable-line react-hooks/exhaustive-deps

  function pickPhoto(e) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    if (photo?.url) URL.revokeObjectURL(photo.url);
    setDoc(null);
    setPhoto({ file, url: URL.createObjectURL(file) });
  }

  // --- Message actions -------------------------------------------------------

  async function copy(message) {
    try {
      await navigator.clipboard.writeText(message.body || "");
      setNotice("Copied.");
    } catch {
      setNotice("Couldn't copy - select the text instead.");
    }
  }

  async function removeOwn(message) {
    try {
      const updated = await deleteChatMessage(message.id);
      setMessages((prev) => merge(prev || [], [updated]));
      onChannelChanged?.();
    } catch (err) {
      setError(err.message);
    }
  }

  function askDelete(message) {
    if (message.sender.id === me.id) {
      if (window.confirm("Delete this message for everyone?")) removeOwn(message);
      return;
    }
    setDialog({ kind: "moderate", message });
  }

  // --- Header actions --------------------------------------------------------

  const isDm = channel?.kind === "dm";
  // `blocked` means "I blocked them" on current servers (they also send
  // can_send); older servers set it both ways, so my own blocks list is the
  // tiebreaker there.
  const newServer = channel && typeof channel.can_send === "boolean";
  const iBlocked =
    isDm && (myBlocks.some((b) => b.user_id === channel.other_user_id) || (newServer && !!channel.blocked));
  const canSend = channel ? (newServer ? channel.can_send : !channel.blocked) : true;
  const mentionRe = useMemo(() => mentionPattern(members.map((m) => m.name)), [members]);

  async function toggleMute() {
    try {
      setChannel(await updateMyChannel(channelId, { muted: !channel.muted }));
      onChannelChanged?.();
    } catch (err) {
      setError(err.message);
    }
  }

  async function hideDm() {
    if (!window.confirm(`Hide your conversation with ${channel.name}? It comes back if either of you sends a message.`))
      return;
    try {
      await updateMyChannel(channelId, { hidden: true });
      onChannelChanged?.();
      onBack();
    } catch (err) {
      setError(err.message);
    }
  }

  async function toggleBlock() {
    try {
      if (iBlocked) {
        await unblockUser(channel.other_user_id);
        setNotice(`Unblocked ${channel.name}.`);
      } else {
        if (
          !window.confirm(
            `Block ${channel.name}? Neither of you will be able to send direct messages to the other. Team channels aren't affected.`,
          )
        )
          return;
        await blockUser(channel.other_user_id);
        setNotice(`Blocked ${channel.name}.`);
      }
      await Promise.all([refreshChannel(), onBlocksChanged?.()]);
      onChannelChanged?.();
    } catch (err) {
      setError(err.message);
    }
  }

  // --- Render ----------------------------------------------------------------

  const serverMessages = (messages || []).filter((m) => !m.pending);
  const lastServer = serverMessages[serverMessages.length - 1];
  const showSeen = lastServer && lastServer.sender.id === me.id && channel?.seen_by > 0;

  let lastDay = null;
  let lastSender = null;

  return (
    <div className="chat-conversation">
      <div className="chat-conv-head">
        <button type="button" className="link-button chat-back" onClick={onBack} aria-label="Back to conversations">
          ‹ Back
        </button>
        <div className="chat-conv-title">
          {channel?.kind === "team" && (
            <span className="task-team-dot" style={{ background: channel.color || "#9ca3af" }} />
          )}
          <strong>{channel ? (channel.kind === "team" ? `# ${channel.name}` : channel.name) : "…"}</strong>
          {channel?.muted && <span className="pill">Muted</span>}
        </div>
        {channel && (
          <div className="chat-conv-actions">
            <button type="button" className="link-button" onClick={() => setSearching(!searching)} aria-expanded={searching}>
              Search
            </button>
            <button type="button" className="link-button" onClick={toggleMute}>
              {channel.muted ? "Unmute" : "Mute"}
            </button>
            {isDm && (
              <>
                <button type="button" className="link-button" onClick={hideDm}>
                  Hide
                </button>
                <button type="button" className="link-button link-danger" onClick={toggleBlock}>
                  {iBlocked ? "Unblock" : "Block"}
                </button>
              </>
            )}
          </div>
        )}
      </div>

      {error && (
        <p className="error chat-banner">
          {error}{" "}
          <button type="button" className="link-button" onClick={() => setError(null)}>
            Dismiss
          </button>
        </p>
      )}
      {notice && (
        <p className="success chat-banner" onClick={() => setNotice(null)}>
          {notice}
        </p>
      )}

      {searching && (
        <ChatSearch
          channelId={channelId}
          onClose={() => setSearching(false)}
          onOpen={(m) => {
            setSearching(false);
            jumpTo(m.id);
          }}
        />
      )}
      <PinsBar pins={pinsInfo.pins} onJump={jumpTo} />

      <div className="chat-messages" ref={listRef} onScroll={onScroll}>
        {messages === null && !error && <p className="muted chat-empty">Loading…</p>}
        {hasMore && (
          <div className="chat-load-older">
            <button type="button" className="link-button" onClick={loadOlder} disabled={loadingOlder}>
              {loadingOlder ? "Loading…" : "Load earlier messages"}
            </button>
          </div>
        )}
        {messages && messages.length === 0 && (
          <p className="muted chat-empty">No messages yet. Say hello.</p>
        )}
        {(messages || []).map((m) => {
          const day = storeDay(new Date(m.created_at));
          const newDay = day !== lastDay;
          lastDay = day;
          const mine = m.sender.id === me.id;
          const showName = !mine && (newDay || lastSender !== m.sender.id) && channel?.kind === "team";
          lastSender = m.sender.id;
          return (
            <div key={m.pending ? m.client_uuid : m.id}>
              {newDay && <div className="chat-day">{dayLabel(m.created_at)}</div>}
              <div className={`chat-row ${mine ? "chat-row-mine" : ""}`} data-message-id={m.id || ""}>
                <div
                  className={`chat-bubble ${mine ? "chat-bubble-mine" : ""} ${m.deleted ? "chat-bubble-deleted" : ""} ${
                    flashId === m.id ? "chat-bubble-flash" : ""
                  }`}
                >
                  {m.pinned && !m.deleted && (
                    <div className="chat-pinned-tag" title={m.pinned_by ? `Pinned by ${m.pinned_by.name}` : "Pinned"}>
                      📌 Pinned
                    </div>
                  )}
                  {showName && <div className="chat-sender">{m.sender.name}</div>}
                  {m.deleted ? (
                    <em className="muted">{m.deleted_by_moderator ? "Removed by a manager" : "Message deleted"}</em>
                  ) : (
                    <>
                      {!m.pending && <MessageAttachment message={m} />}
                      {m.pending && m.previewUrl && <img src={m.previewUrl} alt="Photo" className="chat-photo" />}
                      {m.pending && m.kind === "audio" && (
                        <div className="chat-voice muted">Voice message ({formatDuration(m.durationMs)})</div>
                      )}
                      {m.pending && m.kind === "document" && <div className="chat-doc muted">Document: {m.filename}</div>}
                      <MessageText text={m.pending ? m.body : displayBody(m)} pattern={mentionRe} />
                    </>
                  )}
                  <div className="chat-meta">
                    {m.pending ? (
                      m.failed ? (
                        <span className="chat-failed" role="alert">
                          <span className="chat-failed-text">Not sent{m.error ? `: ${m.error}` : ""}</span>
                          {m.retryable !== false && (
                            <button type="button" className="chat-retry" onClick={() => deliver(m)}>
                              Retry
                            </button>
                          )}
                          <button type="button" className="chat-retry" onClick={() => discard(m)}>
                            Discard
                          </button>
                        </span>
                      ) : (
                        "Sending…"
                      )
                    ) : (
                      timeFmt.format(new Date(m.created_at))
                    )}
                  </div>
                </div>
                <MessageMenu
                  message={m}
                  mine={mine}
                  isAdmin={isAdmin}
                  canPin={!!pinsInfo.can_pin}
                  onPin={() => pin(m)}
                  onSeenBy={() => setDialog({ kind: "seen", message: m })}
                  onCopy={() => copy(m)}
                  onCreateTask={() => onCreateTask(m, channel)}
                  onReport={() => setDialog({ kind: "report", message: m })}
                  onDelete={() => askDelete(m)}
                />
              </div>
            </div>
          );
        })}
        {showSeen && (
          <div className="chat-seen">{isDm ? "Seen" : `Seen by ${channel.seen_by}`}</div>
        )}
      </div>

      {channel && !canSend ? (
        <div className="chat-blocked">
          {iBlocked ? (
            <>
              You blocked {channel.name}.{" "}
              <button type="button" className="link-button" onClick={toggleBlock}>
                Unblock
              </button>
            </>
          ) : (
            "You can't send messages in this conversation."
          )}
        </div>
      ) : (
        <div className="chat-composer">
          {mention && mentionOptions.length > 0 && (
            <div className="chat-mentions" role="listbox" aria-label="Mention someone">
              {mentionOptions.map((p) => (
                <button type="button" key={p.id} role="option" className="chat-mention-option" onMouseDown={(e) => e.preventDefault()} onClick={() => pickMention(p)}>
                  @{p.name}
                </button>
              ))}
            </div>
          )}
          {photo && (
            <div className="chat-photo-preview">
              <img src={photo.url} alt="Photo to send" />
              <button
                type="button"
                className="link-button"
                onClick={() => {
                  URL.revokeObjectURL(photo.url);
                  setPhoto(null);
                }}
              >
                Remove
              </button>
            </div>
          )}
          {doc && (
            <div className="chat-photo-preview chat-doc-preview">
              <span className="chat-doc-icon" aria-hidden="true">
                PDF
              </span>
              <span className="chat-doc-name">{doc.file.name}</span>
              <button type="button" className="link-button" onClick={() => setDoc(null)}>
                Remove
              </button>
            </div>
          )}
          {voice.error && <p className="error chat-hint">{voice.error}</p>}
          {voice.recording && (
            <div className="chat-recording" role="status">
              <span className="chat-rec-dot" aria-hidden="true" /> Recording {formatDuration(voice.elapsedMs)} (up to 5:00)
              <button type="button" className="link-button" onClick={voice.cancel}>
                Cancel
              </button>
              <button type="button" onClick={stopVoice}>
                Send voice note
              </button>
            </div>
          )}
          <div className="chat-composer-row">
            <button
              type="button"
              className="btn-secondary chat-attach"
              onClick={() => fileRef.current?.click()}
              aria-label="Attach a photo"
              title="Attach a photo"
            >
              📷
            </button>
            <input ref={fileRef} type="file" accept="image/*" hidden onChange={pickPhoto} />
            <button
              type="button"
              className="btn-secondary chat-attach"
              onClick={() => docRef.current?.click()}
              aria-label="Attach a PDF"
              title="Attach a PDF (up to 10 MB)"
            >
              📎
            </button>
            <input ref={docRef} type="file" accept="application/pdf,.pdf" hidden onChange={pickDocument} />
            {voice.supported && !voice.recording && (
              <button
                type="button"
                className="btn-secondary chat-attach"
                onClick={voice.start}
                aria-label="Record a voice note"
                title="Record a voice note"
              >
                🎤
              </button>
            )}
            <textarea
              ref={inputRef}
              rows={1}
              value={draft}
              onChange={onDraftChange}
              onKeyDown={onKeyDown}
              placeholder="Write a message"
              aria-label="Message"
              maxLength={MAX_BODY}
              disabled={!channel}
            />
            <button type="button" onClick={send} disabled={!channel || (!draft.trim() && !photo && !doc)}>
              Send
            </button>
          </div>
          <p className="chat-hint muted">
            {draft.length > MAX_BODY - 200
              ? `${MAX_BODY - draft.length} characters left`
              : `Enter to send · Shift+Enter for a new line · @ to mention${
                  voice.supported ? "" : " · voice notes: record them in the app"
                }`}
          </p>
        </div>
      )}

      {dialog?.kind === "seen" && <SeenByDialog messageId={dialog.message.id} onClose={() => setDialog(null)} />}
      {dialog?.kind === "report" && (
        <ReasonDialog
          title="Report this message"
          label="What's wrong with it? A manager will review it."
          confirmLabel="Report"
          required
          onClose={() => setDialog(null)}
          onSubmit={async (reason) => {
            await reportChatMessage(dialog.message.id, reason);
            setNotice("Thanks - a manager will review it.");
          }}
        />
      )}
      {dialog?.kind === "moderate" && (
        <ReasonDialog
          title={`Remove ${dialog.message.sender.name}'s message`}
          label="Reason (kept for the moderation record)"
          confirmLabel="Remove message"
          required
          onClose={() => setDialog(null)}
          onSubmit={async (reason) => {
            const updated = await deleteChatMessage(dialog.message.id, reason);
            setMessages((prev) => merge(prev || [], [updated]));
            onChannelChanged?.();
          }}
        />
      )}
    </div>
  );
}
