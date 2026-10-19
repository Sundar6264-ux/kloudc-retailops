import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "../AuthContext.jsx";
import {
  getChatSettings,
  getTeamBootstrap,
  getTeamUpdates,
  listAssignablePeople,
  listChatChannels,
  listChatPeople,
  listMyBlocks,
  openDirectMessage,
  setHidePreviews,
  unblockUser,
} from "../api";
import ChatConversation from "../components/team/ChatConversation.jsx";
import { ChatSearch } from "../components/team/ChatExtras.jsx";
import { messagePreview } from "../components/team/teamAdvancedModel";
import TaskCreateForm from "../components/team/TaskCreateForm.jsx";
import { storeDay, todayStoreDay } from "../components/team/taskUtils";
import useDialogA11y from "../components/team/useDialogA11y";
import { isPageVisible, onPageShown } from "../components/team/usePageActive";
import { lazyStoreFormat } from "../format.js";
import Icon from "../ui/icons.jsx";
import PageHeader from "../ui/PageHeader.jsx";

// Team hub Chat on the website (docs/TEAM_HUB_DESIGN.md §4.4): team
// channels (one per team, automatic) and direct messages. Two panes on a
// desktop, one pane with a Back button on a phone. The list refreshes from
// the change feed; the open conversation polls for new messages itself.

const timeFmt = lazyStoreFormat("en-US", { hour: "numeric", minute: "2-digit" });
const dateFmt = lazyStoreFormat("en-US", { month: "short", day: "numeric" });

function lastWhen(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return storeDay(d) === todayStoreDay() ? timeFmt.format(d) : dateFmt.format(d);
}

function preview(channel, meId) {
  const m = channel.last_message;
  if (!m) return channel.kind === "team" ? "No messages yet" : "Say hello";
  if (m.deleted) return "Message deleted";
  const who = m.sender.id === meId ? "You: " : channel.kind === "team" ? `${m.sender.name}: ` : "";
  return `${who}${messagePreview(m)}`;
}

function ChannelRow({ channel, active, meId, onOpen }) {
  return (
    <button
      type="button"
      className={`chat-channel ${active ? "chat-channel-active" : ""} ${channel.unread ? "chat-channel-unread" : ""}`}
      onClick={() => onOpen(channel.id)}
    >
      <span className="chat-channel-top">
        {channel.kind === "team" ? (
          <span className="task-team-dot" style={{ background: channel.color || "var(--text-disabled)" }} />
        ) : (
          <span className="chat-avatar">{(channel.name || "?").slice(0, 1).toUpperCase()}</span>
        )}
        <span className="chat-channel-name">{channel.kind === "team" ? `# ${channel.name}` : channel.name}</span>
        {channel.muted && (
          <span className="chat-muted" title="Muted" aria-label="Muted">
            🔕
          </span>
        )}
        <span className="chat-channel-when">{lastWhen(channel.last_message?.created_at)}</span>
      </span>
      <span className="chat-channel-bottom">
        <span className="chat-channel-preview">{preview(channel, meId)}</span>
        {channel.unread > 0 && <span className="chat-unread">{channel.unread > 99 ? "99+" : channel.unread}</span>}
      </span>
    </button>
  );
}

// "New message": pick a person to DM.
function NewDmPicker({ onPick, onCancel }) {
  const [people, setPeople] = useState(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState(null);

  useEffect(() => {
    listChatPeople()
      .then(setPeople)
      .catch((err) => setError(err.message));
  }, []);

  const shown = (people || []).filter((p) => p.name.toLowerCase().includes(query.trim().toLowerCase()));
  return (
    <div className="chat-new-dm">
      <div className="chat-new-dm-head">
        <strong>New message</strong>
        <button type="button" className="link-button" onClick={onCancel}>
          Cancel
        </button>
      </div>
      <input type="search" placeholder="Search people" value={query} onChange={(e) => setQuery(e.target.value)} autoFocus />
      {error && <p className="error">{error}</p>}
      {!people && !error && <p className="muted">Loading…</p>}
      {people && shown.length === 0 && <p className="muted">Nobody to message.</p>}
      <div className="chat-new-dm-list">
        {shown.map((p) => (
          <button type="button" key={p.id} className="chat-channel" onClick={() => onPick(p)}>
            <span className="chat-channel-top">
              <span className="chat-avatar">{p.name.slice(0, 1).toUpperCase()}</span>
              <span className="chat-channel-name">{p.name}</span>
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

// Hide message text in notifications, and the people I've blocked.
function ChatSettings({ blocks, onBlocksChanged, onClose }) {
  const [hide, setHide] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    getChatSettings()
      .then((s) => setHide(!!s.hide_previews))
      .catch((err) => setError(err.message));
  }, []);

  // Ticks straight away; put back if saving fails.
  async function toggle() {
    const next = !hide;
    setHide(next);
    try {
      const s = await setHidePreviews(next);
      setHide(!!s.hide_previews);
    } catch (err) {
      setHide(!next);
      setError(err.message);
    }
  }

  async function unblock(b) {
    try {
      await unblockUser(b.user_id);
      onBlocksChanged();
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <div className="chat-settings">
      <div className="chat-new-dm-head">
        <strong>Chat settings</strong>
        <button type="button" className="link-button" onClick={onClose}>
          Done
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      <label className="chat-settings-toggle">
        <input type="checkbox" checked={!!hide} disabled={hide === null} onChange={toggle} />
        Hide message text in notifications
      </label>
      <p className="muted chat-settings-note">Notifications will say “New message from …” instead of the message.</p>
      <h4>Blocked people</h4>
      {blocks.length === 0 ? (
        <p className="muted">You haven't blocked anyone.</p>
      ) : (
        <ul className="chat-blocks">
          {blocks.map((b) => (
            <li key={b.user_id}>
              <span>{b.name}</span>
              <button type="button" className="link-button" onClick={() => unblock(b)}>
                Unblock
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function TeamChatPage() {
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  const openId = Number(params.get("c")) || null;
  const [boot, setBoot] = useState(null);
  const [channels, setChannels] = useState(null);
  const [blocks, setBlocks] = useState([]);
  const [panel, setPanel] = useState(null); // null | "new" | "settings" | "search"
  const [jump, setJump] = useState(null); // W5: {channelId, messageId} from a search hit
  const [error, setError] = useState(null);
  const [taskFrom, setTaskFrom] = useState(null); // {message, channel}
  const [taskNotice, setTaskNotice] = useState(null);
  const [assignees, setAssignees] = useState([]);
  // Messages deleted/moderated since the conversation loaded (from the
  // change feed) - the open conversation re-reads those it has on screen.
  const [messageEdits, setMessageEdits] = useState({ tick: 0, ids: [] });
  const cursor = useRef(null);
  useDialogA11y(() => setTaskFrom(null), !!taskFrom);

  const loadChannels = useCallback(() => {
    return listChatChannels()
      .then(setChannels)
      .catch((err) => setError(err.message));
  }, []);

  const loadBlocks = useCallback(() => {
    return listMyBlocks()
      .then(setBlocks)
      .catch(() => {});
  }, []);

  useEffect(() => {
    getTeamBootstrap()
      .then((b) => {
        setBoot(b);
        cursor.current = b.cursor ?? 0;
      })
      .catch((err) => setError(err.message));
    loadChannels();
    loadBlocks();
  }, [loadChannels, loadBlocks]);

  // The list follows the change feed (new messages, channels, blocks) - at
  // most every 10 s while chat is open, so a new DM shows up quickly. A
  // hidden tab skips ticks and catches up as soon as it's shown.
  useEffect(() => {
    if (!boot) return undefined;
    let busy = false;
    async function tick() {
      if (busy || !isPageVisible()) return;
      busy = true;
      try {
        const res = await getTeamUpdates(cursor.current ?? 0);
        cursor.current = res.cursor ?? cursor.current;
        const changed = res.changes || {};
        if (res.resync_required || changed.message || changed.channel || changed.team) loadChannels();
        if (res.resync_required || changed.dm_block) loadBlocks();
        const edited = (changed.message || []).filter((c) => c.action !== "created").map((c) => c.id);
        if (edited.length) setMessageEdits((prev) => ({ tick: prev.tick + 1, ids: edited }));
      } catch {
        // Next tick.
      } finally {
        busy = false;
      }
    }
    const ms = Math.min(10, Math.max(5, boot.flags?.poll_interval_s || 30)) * 1000;
    const timer = setInterval(tick, ms);
    const stopShown = onPageShown(tick);
    return () => {
      clearInterval(timer);
      stopShown();
    };
  }, [boot, loadChannels, loadBlocks]);

  const canAssign = !!(user?.is_admin || boot?.can_assign);
  useEffect(() => {
    if (taskFrom && canAssign) listAssignablePeople(!!user?.is_admin).then(setAssignees);
  }, [taskFrom, canAssign, user?.is_admin]);

  function open(id) {
    setPanel(null);
    setParams(id ? { c: String(id) } : {});
  }

  async function startDm(person) {
    try {
      const channel = await openDirectMessage(person.id);
      await loadChannels();
      open(channel.id);
    } catch (err) {
      setError(err.message);
    }
  }

  const off = boot?.flags && boot.flags.chat_enabled === false;
  const teamChannels = (channels || []).filter((c) => c.kind === "team");
  const dms = (channels || []).filter((c) => c.kind === "dm");

  return (
    <>
      <PageHeader pageId="team_chat">
        <p className="muted chat-page-intro">Team channels and direct messages. Hover a message (or tap ⋯) to copy, report or turn it into a task.</p>
      </PageHeader>

      {off && <p className="error">Chat is switched off on the server right now.</p>}
      {error && <p className="error">{error}</p>}
      {taskNotice && <p className="success">{taskNotice}</p>}

      <div className={`chat-layout ${openId ? "chat-layout-open" : ""}`}>
        <aside className="chat-sidebar">
          <div className="chat-sidebar-head">
            <button type="button" onClick={() => setPanel(panel === "new" ? null : "new")}>
              New message
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setPanel(panel === "search" ? null : "search")}
              aria-label="Search messages"
              title="Search messages"
              aria-expanded={panel === "search"}
            >
              <Icon name="search" size={16} />
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setPanel(panel === "settings" ? null : "settings")}
              aria-label="Chat settings"
              title="Chat settings"
            >
              <Icon name="settings" size={16} />
            </button>
          </div>
          {panel === "new" && <NewDmPicker onPick={startDm} onCancel={() => setPanel(null)} />}
          {panel === "search" && (
            <ChatSearch
              onClose={() => setPanel(null)}
              onOpen={(m) => {
                setJump({ channelId: m.channel_id, messageId: m.id });
                setParams({ c: String(m.channel_id) });
              }}
            />
          )}
          {panel === "settings" && (
            <ChatSettings blocks={blocks} onBlocksChanged={() => (loadBlocks(), loadChannels())} onClose={() => setPanel(null)} />
          )}
          {!channels && !error && <p className="muted chat-empty">Loading…</p>}
          {channels && channels.length === 0 && (
            <p className="muted chat-empty">No conversations yet. Ask an admin to add you to a team, or start a direct message.</p>
          )}
          {teamChannels.length > 0 && <div className="chat-list-label">Teams</div>}
          {teamChannels.map((c) => (
            <ChannelRow key={c.id} channel={c} active={c.id === openId} meId={user.id} onOpen={open} />
          ))}
          {dms.length > 0 && <div className="chat-list-label">Direct messages</div>}
          {dms.map((c) => (
            <ChannelRow key={c.id} channel={c} active={c.id === openId} meId={user.id} onOpen={open} />
          ))}
        </aside>

        <section className="chat-main">
          {openId ? (
            <ChatConversation
              key={openId}
              channelId={openId}
              jumpToMessageId={jump && jump.channelId === openId ? jump.messageId : null}
              me={user}
              isAdmin={!!user.is_admin}
              pollSeconds={boot?.flags?.chat_poll_interval_s || 5}
              messageEdits={messageEdits}
              myBlocks={blocks}
              onBack={() => open(null)}
              onChannelChanged={loadChannels}
              onBlocksChanged={loadBlocks}
              onCreateTask={(message, channel) => {
                setTaskNotice(null);
                setTaskFrom({ message, channel });
              }}
            />
          ) : (
            <div className="chat-placeholder muted">Pick a conversation, or start a new message.</div>
          )}
        </section>
      </div>

      {taskFrom && (
        <div className="issue-drawer-backdrop" onMouseDown={(e) => e.target === e.currentTarget && setTaskFrom(null)}>
          <div className="issue-drawer task-drawer" role="dialog" aria-modal="true" aria-label="Create a task from this message">
            <div className="issue-drawer-head">
              <h3>Create task</h3>
              <button type="button" className="link-button" onClick={() => setTaskFrom(null)}>
                Close
              </button>
            </div>
            <p className="muted">
              From {taskFrom.message.sender.name}'s message in{" "}
              {taskFrom.channel?.kind === "team" ? `#${taskFrom.channel.name}` : "a direct message"}.
            </p>
            {user.is_admin || user.allowed_features?.includes("team_tasks") ? (
              <TaskCreateForm
                canAssign={canAssign}
                teams={boot?.teams || []}
                people={assignees}
                initialTitle={(taskFrom.message.body || "Follow up on photo").slice(0, 120)}
                initialDescription={
                  (taskFrom.message.body || "").length > 120 ? taskFrom.message.body : ""
                }
                source={{ type: "chat_message", id: taskFrom.message.id }}
                onCancel={() => setTaskFrom(null)}
                onCreated={(msg) => {
                  setTaskFrom(null);
                  setTaskNotice(`${msg} It's in Team › Tasks.`);
                }}
              />
            ) : (
              <p className="error">You don't have access to Tasks - ask an admin.</p>
            )}
          </div>
        </div>
      )}
    </>
  );
}
