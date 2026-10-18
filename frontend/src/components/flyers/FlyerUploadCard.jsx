import { useEffect, useRef, useState } from "react";
import { sendFlyer, uploadFlyerWithProgress } from "../../api";
import { Term } from "../../glossary.jsx";
import {
  CAPTION_MAX,
  TITLE_MAX,
  acceptAttribute,
  countSends,
  fileKind,
  fileProblem,
  formatBytes,
  formProblems,
  groupProblem,
  isHeic,
  plural,
  titleFromFileName,
} from "./flyerModel.js";
import GroupChecklist from "./GroupChecklist.jsx";
import SendFlyerModal from "./SendFlyerModal.jsx";

// A picked file's preview, before it's uploaded. Pictures show as they are
// (a HEIC only in browsers that can draw it - it's turned into a JPEG when
// saved); a PDF shows in the browser's viewer where it has one.
export function LocalPreview({ file, url, small = false }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [url]);
  const kind = fileKind(file);

  if (kind === "pdf") {
    const badge = (
      <span className="fly-pdf-badge">
        <span className="fly-pdf-badge-icon" aria-hidden="true">
          PDF
        </span>
        {!small && url && (
          <a href={url} target="_blank" rel="noopener noreferrer">
            Open the PDF
          </a>
        )}
      </span>
    );
    if (small || !url) return <div className="fly-local-preview fly-local-pdf">{badge}</div>;
    return (
      <div className="fly-local-preview fly-local-pdf">
        <object data={url} type="application/pdf" className="fly-pdf-embed" aria-label={`Preview of ${file.name}`}>
          {badge}
        </object>
      </div>
    );
  }
  if (failed || !url) {
    return (
      <div className="fly-local-preview fly-local-none">
        <span className="fly-local-none-text">
          {isHeic(file)
            ? "This browser can't show a HEIC photo. It's turned into a JPEG when saved."
            : "No preview for this file."}
        </span>
      </div>
    );
  }
  return (
    <div className="fly-local-preview">
      <img className="fly-local-img" src={url} alt={`Preview of ${file.name}`} onError={() => setFailed(true)} />
    </div>
  );
}

function UploadIcon() {
  return (
    <svg className="fly-drop-icon" width="36" height="36" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M12 16V4m0 0-4.5 4.5M12 4l4.5 4.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

// The line under the card once the send dialog closes.
function sentNotice(title, result) {
  if (!result) return `"${title}" is saved in the library, not sent.`;
  const c = countSends(result.sends);
  if (c.pending) return `"${title}" is going out to ${c.pending + c.sent + c.failed} ${plural(c.pending + c.sent + c.failed, "group")}. Open it to follow each one.`;
  if (c.failed) return `"${title}": ${c.sent} sent, ${c.failed} failed. Open it to retry.`;
  if (c.sent) return `Sent "${title}" to ${c.sent} ${plural(c.sent, "group")}.`;
  return `"${title}" is going out. Open it to follow each group.`;
}

const FIELD_IDS = {
  file: "fly-choose",
  title: "fly-title",
  category: "fly-category",
  caption: "fly-caption",
};

// Upload card: file (drag and drop or choose), title, type, message and the
// groups, then "Save to library" (store only) or "Send…" (confirm, then send).
export default function FlyerUploadCard({ meta, isAdmin, onManageGroups, onSaved, onOpen }) {
  const [file, setFile] = useState(null);
  const [previewUrl, setPreviewUrl] = useState(null);
  const [title, setTitle] = useState("");
  const [titleAuto, setTitleAuto] = useState(true); // still the file-name guess
  const [category, setCategory] = useState("");
  const [caption, setCaption] = useState("");
  const [groups, setGroups] = useState([]);
  const [problems, setProblems] = useState({});
  const [groupErr, setGroupErr] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [saving, setSaving] = useState(null); // progress text while saving
  const [saveError, setSaveError] = useState(null);
  const [notice, setNotice] = useState(null); // {text, flyerId}
  const [confirming, setConfirming] = useState(false);
  const savedRef = useRef(null); // the flyer, once a send has uploaded it
  const inputRef = useRef(null);
  const cardRef = useRef(null);

  const busy = saving != null || confirming;
  const canSend = meta.whatsapp_connected && meta.groups.length > 0;
  const categoryLabel = (key) => meta.categories.find((c) => c.key === key)?.label || key;

  useEffect(() => {
    if (!file) {
      setPreviewUrl(null);
      return undefined;
    }
    const url = URL.createObjectURL(file);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  // A file dropped next to the box shouldn't make the browser leave the page.
  useEffect(() => {
    const stop = (e) => {
      if (e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files")) e.preventDefault();
    };
    window.addEventListener("dragover", stop);
    window.addEventListener("drop", stop);
    return () => {
      window.removeEventListener("dragover", stop);
      window.removeEventListener("drop", stop);
    };
  }, []);

  // Groups that stopped being marketing groups drop out of the selection.
  useEffect(() => {
    setGroups((prev) => prev.filter((k) => meta.groups.some((g) => g.key === k)));
  }, [meta.groups]);

  function pickFile(f) {
    if (!f || busy) return;
    setNotice(null);
    setSaveError(null);
    const problem = fileProblem(f, meta);
    if (problem) {
      setProblems((p) => ({ ...p, file: problem }));
      if (inputRef.current) inputRef.current.value = "";
      return;
    }
    setFile(f);
    setProblems((p) => ({ ...p, file: undefined }));
    if (titleAuto || !title.trim()) {
      setTitle(titleFromFileName(f.name));
      setTitleAuto(true);
      setProblems((p) => ({ ...p, title: undefined }));
    }
  }

  function chooseFile() {
    inputRef.current?.click();
  }

  function removeFile() {
    setFile(null);
    if (titleAuto) setTitle("");
    if (inputRef.current) inputRef.current.value = "";
    // The empty box's "Choose a file" replaces "Change file": focus it once drawn.
    focusField("file");
  }

  function reset() {
    setFile(null);
    setTitle("");
    setTitleAuto(true);
    setCategory("");
    setCaption("");
    setGroups([]);
    setProblems({});
    setGroupErr(null);
    if (inputRef.current) inputRef.current.value = "";
  }

  function focusField(name) {
    if (name === "groups") {
      cardRef.current?.querySelector(".fly-groups input[type=checkbox]:not(:disabled)")?.focus();
      return;
    }
    // After a re-render, so a just-shown error can be read.
    setTimeout(() => document.getElementById(FIELD_IDS[name])?.focus(), 0);
  }

  function validate(forSend) {
    const p = formProblems({ file, title, category, caption }, meta);
    setProblems(p);
    const gErr = forSend ? groupProblem(groups, meta) : null;
    setGroupErr(gErr);
    const first = ["file", "title", "category", "caption"].find((k) => p[k]) || (gErr ? "groups" : null);
    if (first) focusField(first);
    return !first;
  }

  function fields() {
    return { file, title: title.trim(), category, caption: caption.trim() };
  }

  async function save() {
    setNotice(null);
    setSaveError(null);
    if (!validate(false)) return;
    setSaving("Uploading… 0%");
    try {
      const flyer = await uploadFlyerWithProgress(fields(), (pct) => setSaving(pct < 100 ? `Uploading… ${pct}%` : "Saving…"));
      reset();
      setNotice({ text: `Saved "${flyer.title}" to ${flyer.category_label || categoryLabel(flyer.category)}.`, flyerId: flyer.id });
      onSaved?.(flyer);
    } catch (err) {
      setSaveError(err.message || "Couldn't save the flyer.");
    } finally {
      setSaving(null);
    }
  }

  function startSend() {
    setNotice(null);
    setSaveError(null);
    if (!validate(true)) return;
    savedRef.current = null;
    setConfirming(true);
  }

  // Upload (once), then send. Called by the confirm dialog.
  async function performSend(idempotencyKey, setStage) {
    let flyer = savedRef.current;
    if (!flyer) {
      setStage("Uploading… 0%");
      flyer = await uploadFlyerWithProgress(fields(), (pct) => setStage(pct < 100 ? `Uploading… ${pct}%` : "Saving…"));
      savedRef.current = flyer;
      onSaved?.(flyer);
    }
    setStage("Starting…");
    try {
      const res = await sendFlyer(flyer.id, groups, idempotencyKey);
      return { flyer, batchId: res.batch_id, sends: res.sends || [] };
    } catch (err) {
      const e = new Error(`"${flyer.title}" is saved in the library, but it wasn't sent: ${err.message}`);
      e.status = err.status;
      throw e;
    }
  }

  function closeConfirm(result) {
    setConfirming(false);
    const flyer = savedRef.current;
    savedRef.current = null;
    if (!flyer) return; // nothing was uploaded: the form stays as it was
    reset();
    setNotice({ text: sentNotice(flyer.title, result), flyerId: flyer.id });
    onSaved?.(flyer);
  }

  const kind = fileKind(file);
  const pickedGroups = meta.groups.filter((g) => groups.includes(g.key));
  const titleMax = meta.max_title_chars || TITLE_MAX;
  const captionMax = meta.max_caption_chars || CAPTION_MAX;
  const captionNear = caption.length > captionMax - 100;

  return (
    <section
      className="card fly-upload"
      aria-labelledby="fly-upload-title"
      ref={cardRef}
      onPaste={(e) => {
        const f = e.clipboardData?.files?.[0];
        if (f) {
          e.preventDefault();
          pickFile(f);
        }
      }}
    >
      <h2 id="fly-upload-title" className="fly-section-title">
        New <Term k="flyer">flyer</Term>
      </h2>
      <div className="fly-upload-grid">
        <div className="fly-upload-file">
          <div
            className={`fly-drop${dragging ? " fly-drop-over" : ""}${file ? " fly-drop-has-file" : ""}${problems.file ? " fly-drop-bad" : ""}`}
            onDragEnter={(e) => {
              e.preventDefault();
              if (!busy) setDragging(true);
            }}
            onDragOver={(e) => {
              e.preventDefault();
              if (e.dataTransfer) e.dataTransfer.dropEffect = busy ? "none" : "copy";
              if (!busy && !dragging) setDragging(true);
            }}
            onDragLeave={(e) => {
              if (!e.currentTarget.contains(e.relatedTarget)) setDragging(false);
            }}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              pickFile(e.dataTransfer?.files?.[0]);
            }}
          >
            {file ? (
              <div className="fly-drop-file">
                <LocalPreview file={file} url={previewUrl} />
                <div className="fly-file-row">
                  <div className="fly-file-info">
                    <span className="fly-file-name">{file.name}</span>
                    <span className="fly-file-size">
                      {kind === "pdf" ? "PDF" : "Picture"} · {formatBytes(file.size)}
                    </span>
                  </div>
                  <div className="fly-file-actions">
                    <button type="button" id={FIELD_IDS.file} className="btn-secondary fly-btn-sm" onClick={chooseFile} disabled={busy}>
                      Change file
                    </button>
                    <button type="button" className="link-button" onClick={removeFile} disabled={busy}>
                      Remove
                    </button>
                  </div>
                </div>
              </div>
            ) : (
              <div className="fly-drop-empty">
                <UploadIcon />
                <p className="fly-drop-title">{dragging ? "Drop it here" : "Drag a flyer here"}</p>
                <p className="fly-drop-or">or</p>
                <button type="button" id={FIELD_IDS.file} className="btn-secondary fly-btn-sm" onClick={chooseFile} disabled={busy}>
                  Choose a file
                </button>
                <p className="fly-hint" id="fly-file-hint">
                  A picture (JPEG, PNG, WebP or HEIC) up to {meta.max_image_mb} MB, or a PDF up to {meta.max_pdf_mb} MB.
                </p>
              </div>
            )}
            <input
              ref={inputRef}
              type="file"
              className="fly-file-input"
              accept={acceptAttribute(meta)}
              tabIndex={-1}
              aria-hidden="true"
              onChange={(e) => pickFile(e.target.files?.[0])}
            />
          </div>
          {problems.file && (
            <p className="error fly-field-error" role="alert">
              {problems.file}
            </p>
          )}
        </div>

        <div className="fly-upload-fields">
          <div className="fly-field">
            <label className="fly-label" htmlFor={FIELD_IDS.title}>
              Title
            </label>
            <input
              id={FIELD_IDS.title}
              type="text"
              value={title}
              maxLength={titleMax}
              placeholder="e.g. Diwali flower garlands"
              disabled={busy}
              aria-invalid={problems.title ? "true" : undefined}
              aria-describedby={problems.title ? "fly-title-problem" : undefined}
              onChange={(e) => {
                setTitle(e.target.value);
                setTitleAuto(false);
                if (problems.title) setProblems((p) => ({ ...p, title: undefined }));
              }}
            />
            {problems.title && (
              <p className="error fly-field-error" id="fly-title-problem">
                {problems.title}
              </p>
            )}
          </div>

          <div className="fly-field">
            <label className="fly-label" htmlFor={FIELD_IDS.category}>
              Type of flyer
            </label>
            <select
              id={FIELD_IDS.category}
              value={category}
              disabled={busy}
              aria-invalid={problems.category ? "true" : undefined}
              aria-describedby={problems.category ? "fly-category-problem" : "fly-category-hint"}
              onChange={(e) => {
                setCategory(e.target.value);
                if (problems.category) setProblems((p) => ({ ...p, category: undefined }));
              }}
            >
              <option value="" disabled>
                Choose a type…
              </option>
              {meta.categories.map((c) => (
                <option key={c.key} value={c.key}>
                  {c.label}
                </option>
              ))}
            </select>
            {problems.category ? (
              <p className="error fly-field-error" id="fly-category-problem">
                {problems.category}
              </p>
            ) : (
              <p className="fly-hint" id="fly-category-hint">
                Files it in that folder of Spice Town Flyers on the Mac mini.
              </p>
            )}
          </div>

          <div className="fly-field">
            <div className="fly-label-row">
              <label className="fly-label" htmlFor={FIELD_IDS.caption}>
                Message <span className="fly-optional">(optional)</span>
              </label>
              <span className={`fly-counter${captionNear ? " fly-counter-near" : ""}`} id="fly-caption-count">
                {caption.length}/{captionMax}
              </span>
            </div>
            <textarea
              id={FIELD_IDS.caption}
              rows={4}
              value={caption}
              maxLength={captionMax}
              placeholder="Goes with the flyer in WhatsApp"
              disabled={busy}
              aria-describedby="fly-caption-count fly-caption-hint"
              onChange={(e) => setCaption(e.target.value)}
            />
            <p className="fly-hint" id="fly-caption-hint">
              WhatsApp formatting: <code>*bold*</code> <code>_italic_</code> <code>~strike~</code>
            </p>
          </div>
        </div>
      </div>

      <GroupChecklist
        idPrefix="fly-new"
        groups={meta.groups}
        selected={groups}
        onChange={(keys) => {
          setGroups(keys);
          if (groupErr) setGroupErr(null);
        }}
        disabled={busy}
        problem={groupErr}
        isAdmin={isAdmin}
        onManageGroups={onManageGroups}
      />

      <div className="fly-upload-actions">
        {!meta.whatsapp_connected && (
          <p className="fly-hint fly-actions-note">WhatsApp isn't connected on the server, so flyers can only be saved for now.</p>
        )}
        <button type="button" className="btn-secondary" onClick={save} disabled={busy}>
          {saving || "Save to library"}
        </button>
        <button type="button" onClick={startSend} disabled={busy || !canSend}>
          Send…
        </button>
      </div>

      {saveError && (
        <p className="error" role="alert">
          {saveError}
        </p>
      )}
      {notice && (
        <p className="success fly-notice" role="status">
          {notice.text}{" "}
          <button type="button" className="link-button fly-inline-link" onClick={() => onOpen(notice.flyerId)}>
            Open
          </button>
        </p>
      )}

      {confirming && (
        <SendFlyerModal
          title={title.trim()}
          categoryLabel={categoryLabel(category)}
          caption={caption.trim()}
          kindLabel={kind === "pdf" ? "PDF" : "Picture"}
          preview={<LocalPreview file={file} url={previewUrl} small />}
          groups={pickedGroups}
          performSend={performSend}
          onClose={closeConfirm}
        />
      )}
    </section>
  );
}
