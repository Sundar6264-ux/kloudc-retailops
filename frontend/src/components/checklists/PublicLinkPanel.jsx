import { useEffect, useState } from "react";
import {
  deleteChecklistPublicLink,
  getChecklistPublicLink,
  rotateChecklistPublicLink,
  saveChecklistPublicLink,
} from "../../api";
import { formatRestaurantTime } from "../../format.js";
import { Term } from "../../glossary.jsx";

// Checklist builder > Public link (admins): a logged-out link for this one
// checklist, for staff without an app account (docs/PUBLIC_CHECKLIST_LINK_CONTRACT.md).

const SLUG_RE = /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/;

function errorText(e) {
  const problems = e?.body?.problems;
  return Array.isArray(problems) && problems.length ? problems.join(" ") : e.message;
}

function formFrom(link) {
  return {
    enabled: !!link.enabled,
    slug: link.slug || "",
    name_mode: link.name_mode || "list",
    allowed_names: link.allowed_names || [],
    max_per_hour: link.max_per_hour || 6,
  };
}

export default function PublicLinkPanel({ checklistType, active }) {
  const [link, setLink] = useState(null);
  const [form, setForm] = useState(null);
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [msg, setMsg] = useState(null);
  const [copied, setCopied] = useState(false);

  function apply(res) {
    setLink(res);
    setForm(formFrom(res));
  }

  useEffect(() => {
    getChecklistPublicLink(checklistType)
      .then(apply)
      .catch((e) => setErr(e.message));
  }, [checklistType]);

  if (!link || !form) {
    return <div className="clb-settings card">{err ? <p className="error">{err}</p> : <p className="muted">Loading…</p>}</div>;
  }

  const set = (patch) => {
    setMsg(null);
    setErr(null);
    setForm((f) => ({ ...f, ...patch }));
  };
  const dirty = JSON.stringify(form) !== JSON.stringify(formFrom(link)) || !link.exists;
  const slugOk = form.slug.length >= 2 && form.slug.length <= 20 && SLUG_RE.test(form.slug) && !form.slug.includes("--");
  // "https://store.example.com/c/kitchen-4821" -> "store.example.com/c/kitchen-4821" (what people type).
  const shortUrl = link.url ? link.url.replace(/^https?:\/\//, "") : "";

  function addName() {
    const n = newName.trim().replace(/\s+/g, " ");
    if (!n) return;
    if (!form.allowed_names.some((x) => x.toLowerCase() === n.toLowerCase())) {
      set({ allowed_names: [...form.allowed_names, n] });
    }
    setNewName("");
  }

  async function run(fn, done) {
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      apply(await fn());
      setMsg(done);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const save = () =>
    run(
      () => saveChecklistPublicLink(checklistType, { ...form, max_per_hour: Number(form.max_per_hour) }),
      form.enabled ? "Saved. The link is on." : "Saved. The link is off."
    );

  function rotate() {
    if (
      !window.confirm(
        "Make a new link? The 4 digits at the end change, and the current link stops working straight away - anyone using it will need the new one."
      )
    )
      return;
    run(() => rotateChecklistPublicLink(checklistType), "New link made - new 4 digits. The old link no longer works.");
  }

  function remove() {
    if (!window.confirm("Remove the public link? It stops working straight away. Past submissions stay.")) return;
    run(() => deleteChecklistPublicLink(checklistType), "Public link removed.");
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(link.url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      window.prompt("Copy the link:", link.url);
    }
  }

  return (
    <div className="clb-settings card">
      <h2>
        <Term k="checklist_public_link">Public link</Term>
      </h2>
      <p className="muted">
        A link people can open without an app account to fill in <strong>only this checklist</strong>. They pick
        or type their name; the submission is saved and sent to WhatsApp exactly like one from the app, marked
        "via public link". Only admins can change this.
      </p>
      {!active && <p className="error">This checklist is inactive, so its link shows "not active" until you turn it back on.</p>}

      {link.exists && (
        <>
          <div className="clb-public-url">
            <code className="clb-public-url-big" data-testid="public-link-url" title={link.url}>
              {shortUrl}
            </code>
            <button type="button" className="btn-secondary" onClick={copy}>
              {copied ? "Copied" : "Copy link"}
            </button>
          </div>
          {link.short ? (
            <p className="muted">
              Easy to remember: the word <strong>{link.slug}</strong> and the code <strong>{link.code}</strong>.
            </p>
          ) : (
            <p className="muted">
              This is an older long link. It keeps working; press "Make a new link" to get a short one (a word and 4
              digits).
            </p>
          )}
          <p className="muted">
            {link.enabled ? "On" : "Off - the link shows \"This link is not active\""}
            {" · "}Last used: {link.last_used_at ? formatRestaurantTime(link.last_used_at) : "never"}
            {" · "}This hour: {link.submissions_last_hour} of {link.max_per_hour}
            {link.rotated_at && ` · New link made ${formatRestaurantTime(link.rotated_at)} by ${link.rotated_by}`}
          </p>
        </>
      )}

      <div className="clb-setting">
        <div>
          <strong>Link on</strong>
          <p className="muted">Off = the link shows "This link is not active". Nothing is deleted.</p>
        </div>
        <input type="checkbox" role="switch" aria-label="Link on" checked={form.enabled} onChange={(e) => set({ enabled: e.target.checked })} />
      </div>
      <label className="clb-setting">
        <div>
          <strong>Link word</strong>
          <p className="muted">
            The word before the 4 digits, e.g. "kitchen": lowercase letters, numbers and hyphens (2-20). Changing it
            changes the link.
          </p>
          {!slugOk && <p className="error">Use 2-20 lowercase letters, numbers or single hyphens.</p>}
        </div>
        <input
          type="text"
          aria-label="Link word"
          value={form.slug}
          maxLength={20}
          onChange={(e) => set({ slug: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-") })}
        />
      </label>
      <div className="clb-setting">
        <div>
          <strong>Who is filling it in</strong>
          <p className="muted">A list of names to pick from, or let people type their name.</p>
        </div>
        <div role="radiogroup" aria-label="Name entry">
          <label style={{ marginRight: "0.75rem" }}>
            <input type="radio" name="clb-name-mode" checked={form.name_mode === "list"} onChange={() => set({ name_mode: "list" })} /> Pick from a list
          </label>
          <label>
            <input type="radio" name="clb-name-mode" checked={form.name_mode === "free_text"} onChange={() => set({ name_mode: "free_text" })} /> Type a name
          </label>
        </div>
      </div>
      {form.name_mode === "list" && (
        <div className="clb-setting" style={{ display: "block" }}>
          <strong>Names</strong>
          <div className="clb-public-names">
            {form.allowed_names.length === 0 && <span className="muted">No names yet.</span>}
            {form.allowed_names.map((n) => (
              <span key={n} className="clb-public-name">
                {n}
                <button
                  type="button"
                  aria-label={`Remove ${n}`}
                  onClick={() => set({ allowed_names: form.allowed_names.filter((x) => x !== n) })}
                >
                  ×
                </button>
              </span>
            ))}
          </div>
          <div className="clb-public-url">
            <input
              type="text"
              aria-label="Add a name"
              placeholder="Add a name"
              value={newName}
              maxLength={60}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  addName();
                }
              }}
            />
            <button type="button" className="btn-secondary" onClick={addName} disabled={!newName.trim()}>
              Add
            </button>
          </div>
        </div>
      )}
      <label className="clb-setting">
        <div>
          <strong>Limit per hour</strong>
          <p className="muted">At most this many submissions through the link in any hour (1-60).</p>
        </div>
        <input
          type="number"
          aria-label="Limit per hour"
          min={1}
          max={60}
          value={form.max_per_hour}
          onChange={(e) => set({ max_per_hour: e.target.value })}
          style={{ width: "6rem" }}
        />
      </label>

      {err && <p className="error">{err}</p>}
      {msg && <p className="success">{msg}</p>}
      <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", marginTop: "0.75rem" }}>
        <button type="button" onClick={save} disabled={busy || !dirty || !slugOk}>
          {busy ? "Saving…" : link.exists ? "Save link" : "Create link"}
        </button>
        {link.exists && (
          <>
            <button type="button" className="btn-secondary" onClick={rotate} disabled={busy}>
              Make a new link
            </button>
            <button type="button" className="link-button link-danger" onClick={remove} disabled={busy}>
              Remove link
            </button>
          </>
        )}
      </div>
    </div>
  );
}
