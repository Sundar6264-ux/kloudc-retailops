import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "../AuthContext.jsx";
import {
  ackAnnouncement,
  getAnnouncementRecipients,
  getTeamBootstrap,
  listAnnouncements,
  markAnnouncementSeen,
  remindAnnouncement,
  updateAnnouncement,
} from "../api";
import AnnouncementCompose from "../components/team/AnnouncementCompose.jsx";
import TeamMediaImage from "../components/team/TeamMediaImage.jsx";
import { refreshTeamBadges } from "../components/team/useTeamBadges";
import { formatWhen } from "../components/team/taskUtils";
import useDialogA11y from "../components/team/useDialogA11y";
import { isPageVisible, onPageShown } from "../components/team/usePageActive";

// Team hub Announcements (docs/TEAM_HUB_DESIGN.md §4.3): one-way posts from
// a manager to one or more teams. Everyone sees what was sent to them
// (pinned first) and confirms the ones that ask for it; admins also post,
// pin, edit, delete, see "7 of 9 acknowledged" and remind whoever hasn't.

const PAGE = 20;

// Pinned first, then newest - the backend pages newest-first by id.
function ordered(list) {
  return [...list].sort((a, b) => (a.pinned === b.pinned ? b.id - a.id : a.pinned ? -1 : 1));
}

function statsLine(a) {
  const s = a.stats;
  if (!s) return null;
  return a.require_ack ? `${s.acked} of ${s.recipients} acknowledged` : `${s.seen} of ${s.recipients} seen`;
}

// Admin: who got it and who has read / confirmed it, with Remind.
function RecipientsDrawer({ announcement, onClose, onChanged }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);
  useDialogA11y(onClose);

  const load = useCallback(() => {
    getAnnouncementRecipients(announcement.id)
      .then(setData)
      .catch((err) => setError(err.message));
  }, [announcement.id]);

  useEffect(load, [load]);

  const pending = data
    ? data.recipients.filter((r) => (announcement.require_ack ? !r.acked_at : !r.seen_at)).length
    : 0;

  async function remind() {
    setBusy(true);
    setError(null);
    try {
      const res = await remindAnnouncement(announcement.id);
      setNotice(
        res.reminded
          ? `Reminded ${res.reminded} ${res.reminded === 1 ? "person" : "people"}.${
              res.pending_push ? " The push goes out after quiet hours." : ""
            }`
          : "Nobody left to remind.",
      );
      load();
      onChanged();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="issue-drawer-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="issue-drawer task-drawer" role="dialog" aria-modal="true" aria-label="Who has read it">
        <div className="issue-drawer-head">
          <h3>{announcement.title}</h3>
          <button type="button" className="link-button" onClick={onClose} aria-label="Close">
            Close
          </button>
        </div>
        {error && <p className="error">{error}</p>}
        {!data && !error && <p className="muted">Loading…</p>}
        {data && (
          <>
            <p className="announcement-summary">
              <strong>{data.summary}</strong>
            </p>
            {notice && <p className="success">{notice}</p>}
            <div className="task-inline-actions">
              <button type="button" onClick={remind} disabled={busy || pending === 0}>
                {busy ? "Reminding…" : pending ? `Remind ${pending} ${pending === 1 ? "person" : "people"}` : "Everyone's done"}
              </button>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Person</th>
                    <th>Seen</th>
                    {announcement.require_ack && <th>Confirmed</th>}
                    <th>Last reminded</th>
                  </tr>
                </thead>
                <tbody>
                  {data.recipients.map((r) => (
                    <tr key={r.user_id}>
                      <td>{r.name}</td>
                      <td>{r.seen_at ? formatWhen(r.seen_at) : <span className="muted">Not yet</span>}</td>
                      {announcement.require_ack && (
                        <td>
                          {r.acked_at ? (
                            <span className="pill pill-success ann-pill">{formatWhen(r.acked_at)}</span>
                          ) : (
                            <span className="pill pill-warn">Waiting</span>
                          )}
                        </td>
                      )}
                      <td>{r.last_reminded_at ? formatWhen(r.last_reminded_at) : "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function EditForm({ announcement, onCancel, onSaved }) {
  const [title, setTitle] = useState(announcement.title);
  const [body, setBody] = useState(announcement.body || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function save(e) {
    e.preventDefault();
    if (!title.trim()) return setError("Add a title.");
    setBusy(true);
    setError(null);
    try {
      onSaved(await updateAnnouncement(announcement.id, { title: title.trim(), body: body.trim() }));
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  return (
    <form className="task-inline-form" onSubmit={save}>
      <label>
        Title
        <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} />
      </label>
      <label>
        Message
        <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={4} />
      </label>
      {error && <p className="error">{error}</p>}
      <div className="task-inline-actions">
        <button type="submit" disabled={busy}>
          {busy ? "Saving…" : "Save"}
        </button>
        <button type="button" className="link-button" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function AnnouncementCard({ announcement: a, isAdmin, onReplace, onRemove, onShowRecipients }) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const needsAck = a.require_ack && a.me && !a.me.acked_at;

  async function run(fn) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const ack = () =>
    run(async () => {
      onReplace(await ackAnnouncement(a.id));
      refreshTeamBadges();
    });
  const togglePin = () => run(async () => onReplace(await updateAnnouncement(a.id, { pinned: !a.pinned })));
  const remove = () => {
    if (!window.confirm(`Delete "${a.title}"? Nobody will see it any more.`)) return;
    run(async () => {
      await updateAnnouncement(a.id, { deleted: true });
      onRemove(a.id);
    });
  };

  return (
    <article className={`card announcement-card${a.pinned ? " announcement-pinned" : ""}${needsAck ? " announcement-needs-ack" : ""}`}>
      <header className="announcement-head">
        <div>
          <h2>
            {a.pinned && <span className="pill pill-info">Pinned</span>} {a.title}
          </h2>
          <p className="muted announcement-meta">
            {a.created_by?.name} · {formatWhen(a.created_at)}
            {a.edited_at && " · edited"}
            {a.teams?.length > 0 && (
              <>
                {" · "}
                {a.teams.map((t, i) => (
                  <span key={t.id}>
                    {i > 0 && ", "}
                    <span className="task-team-dot" style={{ background: t.color || "#6b7280" }} aria-hidden="true" />
                    {t.name}
                  </span>
                ))}
              </>
            )}
          </p>
        </div>
      </header>

      {editing ? (
        <EditForm
          announcement={a}
          onCancel={() => setEditing(false)}
          onSaved={(updated) => {
            setEditing(false);
            onReplace(updated);
          }}
        />
      ) : (
        <>
          {a.body && <p className="announcement-body">{a.body}</p>}
          {a.media && <TeamMediaImage media={a.media} alt="" className="announcement-image" />}
        </>
      )}

      {error && <p className="error">{error}</p>}

      <div className="announcement-actions">
        {a.require_ack && a.me && (
          a.me.acked_at ? (
            <span className="pill pill-success ann-pill">You confirmed · {formatWhen(a.me.acked_at)}</span>
          ) : (
            <button type="button" onClick={ack} disabled={busy}>
              I've read this
            </button>
          )
        )}
        {isAdmin && (
          <>
            {statsLine(a) && (
              <button type="button" className="link-button" onClick={() => onShowRecipients(a)}>
                {statsLine(a)}
              </button>
            )}
            <span className="issue-toolbar-spacer" />
            {!editing && (
              <button type="button" className="link-button" onClick={() => setEditing(true)} disabled={busy}>
                Edit
              </button>
            )}
            <button type="button" className="link-button" onClick={togglePin} disabled={busy}>
              {a.pinned ? "Unpin" : "Pin"}
            </button>
            <button type="button" className="link-button link-danger" onClick={remove} disabled={busy}>
              Delete
            </button>
          </>
        )}
      </div>
    </article>
  );
}

export default function TeamAnnouncementsPage() {
  const { user } = useAuth();
  const isAdmin = !!user?.is_admin;
  const [boot, setBoot] = useState(null);
  const [items, setItems] = useState(null);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [composing, setComposing] = useState(false);
  const [recipientsFor, setRecipientsFor] = useState(null);
  // Ids already marked seen this visit, so a refresh doesn't re-send them.
  const seenSent = useRef(new Set());

  // The refresh merges the newest page into what's loaded (by id) instead
  // of replacing it, so "Older announcements" pages don't disappear on the
  // next poll. Loaded items newer than the page's oldest that are missing
  // from it were deleted.
  const loadedOlder = useRef(false);
  const load = useCallback(() => {
    setError(null);
    listAnnouncements({ limit: PAGE })
      .then((res) => {
        const fresh = res.announcements || [];
        setItems((prev) => {
          // Nothing loaded yet, or nothing at all anymore: take the page as is.
          if (!prev || !fresh.length) return fresh;
          const freshIds = new Set(fresh.map((a) => a.id));
          const floor = Math.min(...fresh.map((a) => a.id));
          const kept = prev.filter((a) => !freshIds.has(a.id) && a.id < floor);
          return [...fresh, ...kept];
        });
        if (!loadedOlder.current) setHasMore(!!res.has_more);
      })
      .catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    getTeamBootstrap()
      .then(setBoot)
      .catch(() => setBoot({}));
    load();
  }, [load]);

  // Keep counts and new posts current while the page is open; a hidden tab
  // skips ticks and refreshes when shown again.
  useEffect(() => {
    const seconds = boot?.flags?.poll_interval_s || 30;
    const id = setInterval(() => {
      if (isPageVisible()) load();
    }, seconds * 1000);
    const stopShown = onPageShown(load);
    return () => {
      clearInterval(id);
      stopShown();
    };
  }, [boot, load]);

  // Showing an announcement on screen counts as seeing it.
  useEffect(() => {
    if (!items) return;
    for (const a of items) {
      if (a.me && !a.me.seen_at && !seenSent.current.has(a.id)) {
        seenSent.current.add(a.id);
        markAnnouncementSeen(a.id)
          .then((updated) => replace(updated))
          .catch(() => seenSent.current.delete(a.id));
      }
    }
  }, [items]);

  function replace(updated) {
    setItems((prev) => (prev ? prev.map((a) => (a.id === updated.id ? updated : a)) : prev));
  }

  function removeLocal(id) {
    setItems((prev) => (prev ? prev.filter((a) => a.id !== id) : prev));
  }

  async function loadMore() {
    if (!items?.length) return;
    setLoadingMore(true);
    try {
      const oldest = Math.min(...items.map((a) => a.id));
      const res = await listAnnouncements({ beforeId: oldest, limit: PAGE });
      loadedOlder.current = true;
      setItems((prev) => [...prev, ...(res.announcements || []).filter((a) => !prev.some((p) => p.id === a.id))]);
      setHasMore(!!res.has_more);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoadingMore(false);
    }
  }

  const off = boot?.flags && boot.flags.announcements_enabled === false;
  const waiting = items ? items.filter((a) => a.require_ack && a.me && !a.me.acked_at).length : 0;

  return (
    <>
      <div className="page-header">
        <h1>News</h1>
        <p className="muted">
          News and instructions for the team. {isAdmin ? "People who join a team later don't get older announcements." : ""}
        </p>
      </div>

      {off && <p className="error">Announcements are switched off on the server right now.</p>}

      <section className="card">
        <div className="issue-toolbar">
          {waiting > 0 ? (
            <span className="pill pill-warn ann-pill">
              {waiting} waiting for you to confirm
            </span>
          ) : (
            <span className="muted">{items && items.length > 0 ? "You're all caught up." : ""}</span>
          )}
          <span className="issue-toolbar-spacer" />
          {isAdmin && !composing && (
            <button type="button" onClick={() => setComposing(true)}>
              New announcement
            </button>
          )}
        </div>
        {composing && (
          <AnnouncementCompose
            teams={boot?.teams || []}
            onCancel={() => setComposing(false)}
            onPosted={(msg) => {
              setComposing(false);
              setNotice(msg);
              load();
            }}
          />
        )}
        {notice && <p className="success">{notice}</p>}
        {error && <p className="error">{error}</p>}
        {!items && !error && <p className="muted">Loading…</p>}
        {items && items.length === 0 && <p className="muted">No announcements yet.</p>}
      </section>

      {items &&
        ordered(items).map((a) => (
          <AnnouncementCard
            key={a.id}
            announcement={a}
            isAdmin={isAdmin}
            onReplace={replace}
            onRemove={removeLocal}
            onShowRecipients={setRecipientsFor}
          />
        ))}

      {hasMore && (
        <div className="announcement-more">
          <button type="button" className="link-button" onClick={loadMore} disabled={loadingMore}>
            {loadingMore ? "Loading…" : "Older announcements"}
          </button>
        </div>
      )}

      {recipientsFor && (
        <RecipientsDrawer announcement={recipientsFor} onClose={() => setRecipientsFor(null)} onChanged={load} />
      )}
    </>
  );
}
