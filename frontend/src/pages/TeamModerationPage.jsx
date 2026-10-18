import { useCallback, useEffect, useState } from "react";
import { dismissReport, listAllBlocks, listReports, resolveReport } from "../api";
import TeamMediaImage from "../components/team/TeamMediaImage.jsx";
import { DocumentChip, VoiceNote } from "../components/team/ChatExtras.jsx";
import { formatWhen } from "../components/team/taskUtils";

// Team hub moderation (docs/TEAM_HUB_DESIGN.md §4.5, admin only): chat
// messages people reported, with the original text even if it was deleted
// since. Resolve (optionally deleting the message) or dismiss, with a note.
// Below, which DM blocks exist - never why, there's no reason on a block.

const TABS = [
  { key: "open", label: "Open" },
  { key: "resolved", label: "Resolved" },
  { key: "dismissed", label: "Dismissed" },
];

const STATUS_PILL = { open: "pill-warn", resolved: "pill-success", dismissed: "pill-neutral" };

function ReportCard({ report: r, onDone }) {
  const [note, setNote] = useState("");
  const [deleteMessage, setDeleteMessage] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const msg = r.message;

  async function act(fn, doneText) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      onDone(doneText);
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  return (
    <article className="moderation-report">
      <div className="moderation-report-head">
        <span className={`pill ${STATUS_PILL[r.status] || "pill-neutral"}`}>{r.status}</span>
        <span className="muted">
          Reported by <strong>{r.reporter?.name}</strong> · {formatWhen(r.created_at)}
        </span>
      </div>
      <p className="moderation-reason">
        <span className="muted">Reason:</span> {r.reason || <em className="muted">none given</em>}
      </p>

      {msg ? (
        <blockquote className={`moderation-message${msg.deleted ? " moderation-message-deleted" : ""}`}>
          <div className="moderation-message-meta muted">
            <strong>{msg.sender?.name}</strong> in {msg.channel_name} · {formatWhen(msg.created_at)}
            {msg.deleted && (
              <>
                <span className="pill pill-neutral">Deleted</span>
                {/* Outside the pill: pills capitalise, the reason is the manager's own words. */}
                {msg.delete_reason && <span>“{msg.delete_reason}”</span>}
              </>
            )}
          </div>
          {msg.body && !msg.body_is_label ? (
            <p className="moderation-message-body">{msg.body}</p>
          ) : (
            !msg.media && !msg.attachment && <p className="muted">(no text)</p>
          )}
          {msg.media && <TeamMediaImage media={msg.media} alt="Reported photo" className="announcement-image" />}
          {/* W5: a reported voice note or PDF - managers can play / open it. */}
          {msg.attachment?.kind === "audio" && <VoiceNote media={msg.attachment} />}
          {msg.attachment?.kind === "document" && <DocumentChip media={msg.attachment} />}
        </blockquote>
      ) : (
        <p className="muted">The message no longer exists.</p>
      )}

      {r.status === "open" ? (
        <div className="task-inline-form">
          <label>
            Note (optional, kept with the report)
            <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} />
          </label>
          {msg && !msg.deleted && (
            <label className="task-item-row">
              <input type="checkbox" checked={deleteMessage} onChange={(e) => setDeleteMessage(e.target.checked)} />
              Delete the message (everyone sees "Removed by a manager")
            </label>
          )}
          {error && <p className="error">{error}</p>}
          <div className="task-inline-actions">
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                act(
                  () => resolveReport(r.id, { note: note.trim(), deleteMessage: deleteMessage && msg && !msg.deleted }),
                  "Report resolved.",
                )
              }
            >
              Resolve
            </button>
            <button
              type="button"
              className="link-button"
              disabled={busy}
              onClick={() => act(() => dismissReport(r.id, note.trim()), "Report dismissed.")}
            >
              Dismiss
            </button>
          </div>
        </div>
      ) : (
        <p className="muted moderation-outcome">
          {r.status === "resolved" ? "Resolved" : "Dismissed"} by {r.resolved_by?.name || "a manager"}
          {r.resolved_at && ` · ${formatWhen(r.resolved_at)}`}
          {r.resolution_note && ` - "${r.resolution_note}"`}
        </p>
      )}
    </article>
  );
}

function BlocksCard() {
  const [blocks, setBlocks] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    listAllBlocks()
      .then((res) => setBlocks(res.blocks || []))
      .catch((err) =>
        // The blocks list lives under the chat API, so with chat switched
        // off on the server it can't load - say so plainly instead of
        // showing the raw "chat is turned off" error.
        setError(
          err.status === 403 || err.status === 503
            ? "The blocks list isn't available while chat is switched off on the server."
            : err.message,
        ),
      );
  }, []);

  return (
    <section className="card">
      <h2>Direct-message blocks</h2>
      <p className="muted">
        Who has blocked whom in direct messages. A block stops both people messaging each other one-to-one; team chats, tasks and
        announcements aren't affected. People give no reason, so none is shown.
      </p>
      {error && <p className="muted">{error}</p>}
      {!blocks && !error && <p className="muted">Loading…</p>}
      {blocks && blocks.length === 0 && <p className="muted">No blocks.</p>}
      {blocks && blocks.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Blocked by</th>
                <th>Blocked</th>
                <th>Since</th>
              </tr>
            </thead>
            <tbody>
              {blocks.map((b) => (
                <tr key={`${b.blocker_user_id}-${b.blocked_user_id}`}>
                  <td>{b.blocker_name}</td>
                  <td>{b.blocked_name}</td>
                  <td>{formatWhen(b.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export default function TeamModerationPage() {
  const [status, setStatus] = useState("open");
  const [reports, setReports] = useState(null);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);

  const load = useCallback(() => {
    setError(null);
    listReports({ status })
      .then((res) => {
        setReports(res.reports || []);
        setHasMore(!!res.has_more);
      })
      .catch((err) => setError(err.message));
  }, [status]);

  useEffect(() => {
    setReports(null);
    load();
  }, [load]);

  async function loadMore() {
    if (!reports?.length) return;
    try {
      const res = await listReports({ status, beforeId: Math.min(...reports.map((r) => r.id)) });
      setReports((prev) => [...prev, ...(res.reports || [])]);
      setHasMore(!!res.has_more);
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <>
      <div className="page-header">
        <h1>Reported messages</h1>
        <p className="muted">Chat messages people reported. Deal with open reports promptly - app store rules expect it.</p>
      </div>

      <section className="card">
        <div className="issue-toolbar">
          <div className="segmented" role="tablist">
            {TABS.map((t) => (
              <button
                key={t.key}
                type="button"
                role="tab"
                aria-selected={status === t.key}
                className={status === t.key ? "active" : ""}
                onClick={() => {
                  setNotice(null);
                  setStatus(t.key);
                }}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>
        {notice && <p className="success">{notice}</p>}
        {error && <p className="error">{error}</p>}
        {!reports && !error && <p className="muted">Loading…</p>}
        {reports && reports.length === 0 && (
          <p className="muted">{status === "open" ? "No open reports." : `No ${status} reports.`}</p>
        )}
        {reports &&
          reports.map((r) => (
            <ReportCard
              key={r.id}
              report={r}
              onDone={(text) => {
                setNotice(text);
                load();
              }}
            />
          ))}
        {hasMore && (
          <button type="button" className="link-button" onClick={loadMore}>
            Older reports
          </button>
        )}
      </section>

      <BlocksCard />
    </>
  );
}
