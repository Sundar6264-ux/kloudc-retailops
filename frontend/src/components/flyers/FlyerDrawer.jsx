import { useEffect, useRef, useState } from "react";
import { deleteFlyer, flyerFileHref, getFlyer, retryFlyerSend, sendFlyer, updateFlyer } from "../../api";
import FlyerImage from "./FlyerImage.jsx";
import {
  CAPTION_MAX,
  POLL_MS,
  TITLE_MAX,
  failedSends,
  formatBytes,
  groupByBatch,
  groupProblem,
  hasPending,
  latestSendByGroup,
  shortDayTime,
} from "./flyerModel.js";
import GroupChecklist from "./GroupChecklist.jsx";
import SendFlyerModal from "./SendFlyerModal.jsx";
import { SendRows, sendProgressText } from "./SendStatus.jsx";
import useDialog from "./useDialog.js";
import WhatsAppText from "./WhatsAppText.jsx";

function EditForm({ flyer, categories, titleMax, captionMax, onCancel, onSaved }) {
  const [title, setTitle] = useState(flyer.title || "");
  const [category, setCategory] = useState(flyer.category || "");
  const [caption, setCaption] = useState(flyer.caption || "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const moved = category !== flyer.category;
  const movedTo = categories.find((c) => c.key === category)?.label;

  async function save(e) {
    e.preventDefault();
    const t = title.trim();
    if (!t) {
      setError("Give the flyer a title.");
      document.getElementById("fly-edit-title")?.focus();
      return;
    }
    const changes = {};
    if (t !== flyer.title) changes.title = t;
    if (category !== flyer.category) changes.category = category;
    if (caption.trim() !== (flyer.caption || "")) changes.caption = caption.trim();
    if (Object.keys(changes).length === 0) {
      onCancel();
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const updated = await updateFlyer(flyer.id, changes);
      onSaved(updated);
    } catch (err) {
      setError(err.message || "Couldn't save the changes.");
      setSaving(false);
    }
  }

  return (
    <form
      className="fly-edit"
      onSubmit={save}
      aria-label="Edit flyer"
      onKeyDown={(e) => {
        if (e.key !== "Escape" || saving) return;
        e.stopPropagation(); // Escape here cancels the edit, not the drawer
        onCancel();
      }}
    >
      <div className="fly-field">
        <label className="fly-label" htmlFor="fly-edit-title">
          Title
        </label>
        <input
          id="fly-edit-title"
          type="text"
          value={title}
          maxLength={titleMax}
          autoFocus
          onChange={(e) => setTitle(e.target.value)}
        />
      </div>
      <div className="fly-field">
        <label className="fly-label" htmlFor="fly-edit-category">
          Type of flyer
        </label>
        <select id="fly-edit-category" value={category} onChange={(e) => setCategory(e.target.value)}>
          {categories.map((c) => (
            <option key={c.key} value={c.key}>
              {c.label}
            </option>
          ))}
        </select>
        {moved && movedTo && <p className="fly-hint">The file moves to the {movedTo} folder on the server.</p>}
      </div>
      <div className="fly-field">
        <div className="fly-label-row">
          <label className="fly-label" htmlFor="fly-edit-caption">
            Message <span className="fly-optional">(optional)</span>
          </label>
          <span className={`fly-counter${caption.length > captionMax - 100 ? " fly-counter-near" : ""}`} id="fly-edit-count">
            {caption.length}/{captionMax}
          </span>
        </div>
        <textarea
          id="fly-edit-caption"
          rows={4}
          value={caption}
          maxLength={captionMax}
          aria-describedby="fly-edit-count fly-edit-hint"
          onChange={(e) => setCaption(e.target.value)}
        />
        <p className="fly-hint" id="fly-edit-hint">
          WhatsApp formatting: <code>*bold*</code> <code>_italic_</code> <code>~strike~</code>. Changes apply to future
          sends only.
        </p>
      </div>
      {error && (
        <p className="error fly-field-error" role="alert">
          {error}
        </p>
      )}
      <div className="fly-row-actions">
        <button type="button" className="btn-secondary" onClick={onCancel} disabled={saving}>
          Cancel
        </button>
        <button type="submit" disabled={saving}>
          {saving ? "Saving…" : "Save changes"}
        </button>
      </div>
    </form>
  );
}

// Detail drawer for one flyer: big preview, open / download the file, edit,
// delete, send to more groups, retry failed sends and the full send history.
// Polls GET /api/flyers/{id} every 3 s while any send is waiting or going.
export default function FlyerDrawer({ flyerId, meta, isAdmin, onClose, onUpdated, onDeleted, onManageGroups }) {
  const [flyer, setFlyer] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [reloadTick, setReloadTick] = useState(0);
  const [pollTick, setPollTick] = useState(0);
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [actionError, setActionError] = useState(null);
  const [groups, setGroups] = useState([]);
  const [groupErr, setGroupErr] = useState(null);
  const [sending, setSending] = useState(false);
  const [retrying, setRetrying] = useState(() => new Set());
  const [fullLoaded, setFullLoaded] = useState(false);
  const mounted = useRef(true);
  const deleteBtnRef = useRef(null);
  const cancelDeleteRef = useRef(null);
  const editBtnRef = useRef(null);
  const dialog = useDialog(onClose, { initialFocus: "[data-autofocus]" });

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoadError(null);
    getFlyer(flyerId)
      .then((f) => !cancelled && setFlyer(f))
      .catch((err) => {
        if (cancelled) return;
        setLoadError(err.status === 404 ? "This flyer isn't in the library any more." : err.message || "Couldn't load the flyer.");
      });
    return () => {
      cancelled = true;
    };
  }, [flyerId, reloadTick]);

  const sends = flyer?.sends || [];
  const pending = hasPending(sends);

  // Live status while anything is waiting or sending (the send dialog polls
  // on its own while it's open).
  useEffect(() => {
    if (!flyer || sending || !pending) return undefined;
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const f = await getFlyer(flyer.id);
        if (cancelled) return;
        setFlyer(f);
        onUpdated?.(f);
      } catch (err) {
        // Deleted meanwhile: stop. Anything else: try again on the next tick.
        if (!cancelled && err.status === 404) {
          setFlyer(null);
          setLoadError("This flyer isn't in the library any more.");
        }
      } finally {
        if (!cancelled) setPollTick((t) => t + 1);
      }
    }, POLL_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // onUpdated is a notification; a new function each render must not restart the timer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flyer, sending, pending, pollTick]);

  useEffect(() => {
    if (confirmDelete) cancelDeleteRef.current?.focus();
  }, [confirmDelete]);

  async function refresh() {
    try {
      const f = await getFlyer(flyerId);
      if (!mounted.current) return;
      setFlyer(f);
      onUpdated?.(f);
    } catch (err) {
      if (mounted.current) setActionError(err.message || "Couldn't refresh the flyer.");
    }
  }

  async function retry(list) {
    setActionError(null);
    for (const s of list) {
      setRetrying((prev) => new Set(prev).add(s.id));
      try {
        await retryFlyerSend(flyer.id, s.id);
      } catch (err) {
        if (mounted.current) setActionError(`${s.target_label}: ${err.message}`);
      } finally {
        if (mounted.current) {
          setRetrying((prev) => {
            const next = new Set(prev);
            next.delete(s.id);
            return next;
          });
        }
      }
    }
    await refresh();
  }

  async function doDelete() {
    setDeleting(true);
    setActionError(null);
    try {
      await deleteFlyer(flyer.id);
      onDeleted?.(flyer);
    } catch (err) {
      if (!mounted.current) return;
      setActionError(err.message || "Couldn't delete the flyer.");
      setDeleting(false);
    }
  }

  function keepIt() {
    setConfirmDelete(false);
    setTimeout(() => deleteBtnRef.current?.focus(), 0);
  }

  function endEdit() {
    setEditing(false);
    setTimeout(() => editBtnRef.current?.focus(), 0);
  }

  function startSend() {
    const problem = groupProblem(groups, meta);
    setGroupErr(problem);
    if (problem) return;
    setSending(true);
  }

  async function performSend(idempotencyKey, setStage) {
    setStage("Starting…");
    const res = await sendFlyer(flyer.id, groups, idempotencyKey);
    return { flyer, batchId: res.batch_id, sends: res.sends || [] };
  }

  function closeSend(result) {
    setSending(false);
    if (result) setGroups([]);
    refresh();
  }

  const latest = latestSendByGroup(sends);
  const batches = groupByBatch(sends);
  const failed = failedSends(sends);
  const canSend = meta.whatsapp_connected && meta.groups.length > 0;
  const title = flyer?.title || (loadError ? "Flyer" : "Loading…");

  return (
    <div className="fly-drawer-backdrop" onMouseDown={(e) => e.target === e.currentTarget && !sending && onClose()}>
      <div className="fly-drawer" role="dialog" aria-modal="true" aria-labelledby="fly-drawer-title" {...dialog}>
        <div className="fly-drawer-head">
          <h3 id="fly-drawer-title">{title}</h3>
          <button type="button" className="fly-icon-btn" data-autofocus aria-label="Close details" onClick={onClose}>
            ×
          </button>
        </div>

        {loadError && (
          <div className="fly-drawer-section">
            <p className="error" role="alert">
              {loadError}
            </p>
            <button type="button" className="btn-secondary fly-btn-sm" onClick={() => setReloadTick((t) => t + 1)}>
              Try again
            </button>
          </div>
        )}
        {!flyer && !loadError && <p className="fly-hint">Loading…</p>}

        {flyer && (
          <>
            <p className="fly-drawer-meta">
              {flyer.category_label}
              {" · "}
              {flyer.kind === "pdf" ? "PDF" : "Picture"}
              {flyer.size_bytes ? ` · ${formatBytes(flyer.size_bytes)}` : ""}
              {flyer.width && flyer.height ? ` · ${flyer.width} × ${flyer.height}` : ""}
            </p>

            <div className="fly-drawer-preview">
              {!fullLoaded && <FlyerImage path={flyer.thumb_url} kind={flyer.kind} alt={`${flyer.title} (preview)`} />}
              {flyer.kind !== "pdf" && flyer.file_url && (
                <FlyerImage
                  path={flyer.file_url}
                  cache={false}
                  kind={flyer.kind}
                  alt={flyer.title}
                  className={fullLoaded ? "" : "fly-img-pending"}
                  onSrc={(src) => setFullLoaded(Boolean(src))}
                />
              )}
            </div>

            <div className="fly-drawer-tools">
              {flyer.file_url && (
                <>
                  <a className="fly-tool-link" href={flyerFileHref(flyer.file_url)} target="_blank" rel="noopener noreferrer">
                    Open file
                  </a>
                  <a className="fly-tool-link" href={flyerFileHref(flyer.file_url)} download={flyer.file_name || undefined}>
                    Download
                  </a>
                </>
              )}
              <button
                type="button"
                ref={editBtnRef}
                className="link-button"
                onClick={() => {
                  setConfirmDelete(false);
                  setEditing(true);
                }}
                disabled={editing || deleting}
              >
                Edit
              </button>
              <button
                type="button"
                ref={deleteBtnRef}
                className="link-button link-danger"
                onClick={() => {
                  setEditing(false);
                  setConfirmDelete(true);
                }}
                disabled={confirmDelete || deleting}
              >
                Delete
              </button>
            </div>

            {confirmDelete && (
              <div
                className="fly-confirm-delete"
                role="group"
                aria-labelledby="fly-delete-q"
                onKeyDown={(e) => {
                  if (e.key !== "Escape" || deleting) return;
                  e.stopPropagation(); // Escape here means "Keep it", not "close the drawer"
                  keepIt();
                }}
              >
                <p id="fly-delete-q">
                  <strong>Delete "{flyer.title}"?</strong> It leaves the library. The file isn't erased: it moves to the
                  _Deleted folder in the store's flyers folder on the server.
                </p>
                <div className="fly-row-actions">
                  <button
                    type="button"
                    ref={cancelDeleteRef}
                    className="btn-secondary"
                    onClick={keepIt}
                    disabled={deleting}
                  >
                    Keep it
                  </button>
                  <button type="button" className="fly-btn-danger" onClick={doDelete} disabled={deleting}>
                    {deleting ? "Deleting…" : "Delete flyer"}
                  </button>
                </div>
              </div>
            )}

            {actionError && (
              <p className="error" role="alert">
                {actionError}
              </p>
            )}

            {editing ? (
              <EditForm
                flyer={flyer}
                categories={meta.categories}
                titleMax={meta.max_title_chars || TITLE_MAX}
                captionMax={meta.max_caption_chars || CAPTION_MAX}
                onCancel={endEdit}
                onSaved={(updated) => {
                  setFlyer((prev) => ({ ...prev, ...updated, sends: updated.sends || prev.sends }));
                  endEdit();
                  onUpdated?.(updated, { reload: true });
                }}
              />
            ) : (
              <>
                <dl className="fly-facts">
                  <div>
                    <dt>Message</dt>
                    <dd>{flyer.caption ? <WhatsAppText text={flyer.caption} className="fly-caption" /> : <span className="fly-muted">None</span>}</dd>
                  </div>
                  {flyer.file_name && (
                    <div>
                      <dt>File</dt>
                      <dd className="fly-break">{flyer.file_name}</dd>
                    </div>
                  )}
                  <div>
                    <dt>Added</dt>
                    <dd>
                      {shortDayTime(flyer.created_at)}
                      {flyer.created_by_name ? ` by ${flyer.created_by_name}` : ""}
                    </dd>
                  </div>
                  <div>
                    <dt>Last sent</dt>
                    <dd>{flyer.last_sent_at ? shortDayTime(flyer.last_sent_at) : "Not sent yet"}</dd>
                  </div>
                </dl>
              </>
            )}

            {meta.whatsapp_enabled !== false && (
            <section className="fly-drawer-section" aria-labelledby="fly-more-title">
              <h4 id="fly-more-title">Send to more groups</h4>
              {!meta.whatsapp_connected && (
                <p className="fly-hint">WhatsApp isn't connected on the server, so nothing can be sent right now.</p>
              )}
              <GroupChecklist
                idPrefix="fly-more"
                groups={meta.groups}
                selected={groups}
                onChange={(keys) => {
                  setGroups(keys);
                  if (groupErr) setGroupErr(null);
                }}
                latestByGroup={latest}
                disabled={sending}
                problem={groupErr}
                isAdmin={isAdmin}
                onManageGroups={onManageGroups}
                legend="Groups"
              />
              {meta.groups.length > 0 && (
                <div className="fly-row-actions fly-row-start">
                  <button type="button" onClick={startSend} disabled={!canSend || sending}>
                    Send…
                  </button>
                </div>
              )}
            </section>
            )}

            <section className="fly-drawer-section" aria-labelledby="fly-history-title">
              <div className="fly-history-head">
                <h4 id="fly-history-title">Send history</h4>
                {failed.length > 1 && !pending && (
                  <button type="button" className="link-button" onClick={() => retry(failed)} disabled={retrying.size > 0}>
                    Retry all failed ({failed.length})
                  </button>
                )}
              </div>
              {batches.length === 0 ? (
                <p className="fly-hint">Not sent to any group yet.</p>
              ) : (
                batches.map((b) => (
                  <div key={b.key} className="fly-batch">
                    <p className="fly-batch-head">
                      <span>
                        {shortDayTime(b.requestedAt)}
                        {b.requestedBy ? ` · ${b.requestedBy}` : ""}
                      </span>
                      <span className="fly-batch-progress">{sendProgressText(b.sends)}</span>
                    </p>
                    <SendRows sends={b.sends} onRetry={(s) => retry([s])} retrying={retrying} busy={false} />
                  </div>
                ))
              )}
              {pending && <p className="fly-hint" aria-live="polite">Updating every few seconds while it sends.</p>}
            </section>
          </>
        )}

        {sending && flyer && (
          <SendFlyerModal
            title={flyer.title}
            categoryLabel={flyer.category_label}
            caption={flyer.caption}
            kindLabel={flyer.kind === "pdf" ? "PDF" : "Picture"}
            preview={<FlyerImage path={flyer.thumb_url} kind={flyer.kind} alt="" />}
            groups={meta.groups.filter((g) => groups.includes(g.key))}
            performSend={performSend}
            onClose={closeSend}
          />
        )}
      </div>
    </div>
  );
}
