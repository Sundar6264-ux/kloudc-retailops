import { useRef, useState } from "react";
import { createAnnouncement, uploadTeamMedia } from "../../api";

// Admin: post an announcement to one or more teams. The recipients are the
// members of those teams right now - people who join later don't get it
// (design §4.3). An optional image is uploaded first and sent by token.
//
// Retry-safe: the post carries a client_uuid made once per compose, so
// pressing Post again after a timeout returns the announcement the server
// already made instead of posting (and pushing) it twice; and the image is
// uploaded once, its token reused by every retry.
export default function AnnouncementCompose({ teams, onCancel, onPosted }) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [teamIds, setTeamIds] = useState([]);
  const [requireAck, setRequireAck] = useState(true);
  const [pinned, setPinned] = useState(false);
  const [image, setImage] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const clientUuid = useRef(
    window.crypto?.randomUUID ? window.crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`,
  );
  const uploaded = useRef(null); // {file, token}

  function toggleTeam(id) {
    setTeamIds((prev) => (prev.includes(id) ? prev.filter((t) => t !== id) : [...prev, id]));
  }

  async function submit(e) {
    e.preventDefault();
    if (!title.trim()) return setError("Add a title.");
    if (teamIds.length === 0) return setError("Pick at least one team.");
    setBusy(true);
    setError(null);
    try {
      let mediaToken = null;
      if (image) {
        if (uploaded.current?.file !== image) {
          uploaded.current = { file: image, token: (await uploadTeamMedia(image)).token };
        }
        mediaToken = uploaded.current.token;
      }
      const posted = await createAnnouncement({
        client_uuid: clientUuid.current,
        title: title.trim(),
        body: body.trim(),
        team_ids: teamIds,
        require_ack: requireAck,
        pinned,
        media_token: mediaToken,
      });
      const count = posted.stats?.recipients;
      onPosted(count != null ? `Posted to ${count} ${count === 1 ? "person" : "people"}.` : "Posted.");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="task-create-form" onSubmit={submit}>
      <label className="task-field-wide">
        Title
        <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} placeholder="e.g. New closing time on Sundays" />
      </label>
      <label className="task-field-wide">
        Message
        <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={4} />
      </label>
      <fieldset className="task-field-wide">
        <legend>Send to</legend>
        <div className="task-people">
          {teams.map((t) => (
            <label key={t.id}>
              <input type="checkbox" checked={teamIds.includes(t.id)} onChange={() => toggleTeam(t.id)} />
              <span className="task-team-dot" style={{ background: t.color || "#6b7280" }} aria-hidden="true" />
              {t.name}
            </label>
          ))}
        </div>
      </fieldset>
      <fieldset>
        <legend>Options</legend>
        <label className="task-item-row">
          <input type="checkbox" checked={requireAck} onChange={(e) => setRequireAck(e.target.checked)} />
          Ask everyone to confirm they've read it
        </label>
        <label className="task-item-row">
          <input type="checkbox" checked={pinned} onChange={(e) => setPinned(e.target.checked)} />
          Pin to the top
        </label>
      </fieldset>
      <label>
        Image (optional)
        <input type="file" accept="image/*" onChange={(e) => setImage(e.target.files?.[0] || null)} />
      </label>
      {error && <p className="error task-field-wide">{error}</p>}
      <div className="task-inline-actions task-field-wide">
        <button type="submit" disabled={busy}>
          {busy ? "Posting…" : "Post announcement"}
        </button>
        <button type="button" className="link-button" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </form>
  );
}
