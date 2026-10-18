import { useEffect, useMemo, useRef, useState } from "react";
import { getPublicChecklist, submitPublicChecklist, uploadPublicChecklistPhoto } from "../api";
import ChecklistForm from "../components/checklists/ChecklistForm.jsx";
import { ChecklistUploadContext } from "../components/checklists/uploadContext.js";

// The logged-out checklist page: /c/{slug}-{code}, e.g. /c/kitchen-4821
// (docs/PUBLIC_CHECKLIST_LINK_CONTRACT.md). main.jsx renders ONLY this for /c/
// paths - no app shell, no login, no navigation, nothing else from the site.
// It talks to /api/public/checklists/{slug}-{code} and nothing else. Every
// name is shown as plain text (React escapes it).

const NAME_MIN = 2;
const NAME_MAX = 60;

// "/c/kitchen-4821" -> "kitchen-4821": the API needs the word AND the code
// (the 4 digits alone don't open anything). Links made before the short codes
// end in a 22-character token instead ("/c/kitchen-service-AbC123...").
export function tokenFromPath(pathname) {
  let last = (pathname || "").replace(/\/+$/, "").split("/").pop() || "";
  // A short link typed as /c/Kitchen-4821 means kitchen-4821 (old long tokens stay case-sensitive).
  if (/^[A-Za-z0-9-]+-[0-9]{4}$/.test(last)) last = last.toLowerCase();
  return /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?-(?:[0-9]{4}|[A-Za-z0-9]{22})$/.test(last) && last.length <= 70 ? last : null;
}

// Product name + logo on top of every public checklist page (owner's ask:
// part-time staff open these links without the app, so show whose page it is).
function BrandHeader() {
  return (
    <header className="clp-brand">
      <img src="/logo-header.png" alt="" width="36" height="36" />
      <span>StoreOps</span>
    </header>
  );
}

function NotActive() {
  return (
    <main className="clp-page">
        <BrandHeader />
      <div className="card clp-message">
        <h1>This link is not active</h1>
        <p className="muted">Ask your manager for the current checklist link.</p>
      </div>
    </main>
  );
}

function NameField({ mode, names, value, onChange, error }) {
  if (mode === "list") {
    return (
      <fieldset className={`clp-name${error ? " clf-has-error" : ""}`}>
        <legend>
          Your name <span className="clf-required">*</span>
        </legend>
        <div className="clp-name-options" role="radiogroup" aria-label="Your name">
          {names.map((n) => (
            <label key={n} className={`clp-name-option${value === n ? " clp-name-on" : ""}`}>
              <input type="radio" name="clp-name" value={n} checked={value === n} onChange={() => onChange(n)} />
              <span>{n}</span>
            </label>
          ))}
        </div>
        {error && <p className="error">{error}</p>}
      </fieldset>
    );
  }
  return (
    <label className={`clp-name${error ? " clf-has-error" : ""}`}>
      <span className="clp-name-label">
        Your name <span className="clf-required">*</span>
      </span>
      <input
        type="text"
        value={value}
        maxLength={NAME_MAX}
        autoComplete="name"
        autoCapitalize="words"
        placeholder="First and last name"
        onChange={(e) => onChange(e.target.value)}
      />
      {error && <p className="error">{error}</p>}
    </label>
  );
}

function nameProblem(mode, names, value) {
  const name = (value || "").trim().replace(/\s+/g, " ");
  if (mode === "list") return names.includes(name) ? null : "Pick your name.";
  if (name.length < NAME_MIN || name.length > NAME_MAX) return `Enter your name (${NAME_MIN}-${NAME_MAX} characters).`;
  if (!/^[\p{L}\p{M} .'-]+$/u.test(name)) return "Use only letters, spaces, dots, apostrophes and hyphens.";
  return null;
}

export default function PublicChecklistPage() {
  const token = useMemo(() => tokenFromPath(window.location.pathname), []);
  const [state, setState] = useState(token ? "loading" : "inactive");
  const [form, setForm] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [name, setName] = useState("");
  const [nameError, setNameError] = useState(null);
  const [formKey, setFormKey] = useState(0);
  // {media id: key} from each photo upload, sent with the submission.
  const mediaKeys = useRef({});
  const topRef = useRef(null);

  function load() {
    setLoadError(null);
    return getPublicChecklist(token)
      .then((data) => {
        setForm(data);
        setState("ready");
        if (data.title) document.title = data.title;
        return data;
      })
      .catch((err) => {
        if (err.status === 404) setState("inactive");
        else {
          setLoadError(err.message);
          setState("error");
        }
        throw err;
      });
  }

  useEffect(() => {
    if (token) load().catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const upload = useMemo(
    () => async (file) => {
      try {
        const res = await uploadPublicChecklistPhoto(token, file);
        mediaKeys.current[String(res.id)] = res.key;
        // Shown from the phone's own copy - uploaded photos can't be viewed through the link.
        return { id: res.id, url: URL.createObjectURL(file), width: res.width, height: res.height };
      } catch (err) {
        if (err.status === 404) setState("inactive");
        throw err;
      }
    },
    [token]
  );

  if (state === "inactive") return <NotActive />;
  if (state === "loading") {
    return (
      <main className="clp-page">
        <BrandHeader />
        <p className="muted">Loading…</p>
      </main>
    );
  }
  if (state === "error") {
    return (
      <main className="clp-page">
        <BrandHeader />
        <div className="card clp-message">
          <h1>Couldn't load the checklist</h1>
          <p className="error">{loadError}</p>
          <button type="button" onClick={() => load().catch(() => {})}>
            Try again
          </button>
        </div>
      </main>
    );
  }

  const schema = form.schema;
  const names = form.allowed_names || [];

  async function onSubmit(answers) {
    const problem = nameProblem(form.name_mode, names, name);
    setNameError(problem);
    if (problem) {
      topRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
      throw new Error("Add your name at the top first.");
    }
    const keys = {};
    for (const [id, key] of Object.entries(mediaKeys.current)) keys[id] = key;
    try {
      return await submitPublicChecklist(token, {
        name: name.trim().replace(/\s+/g, " "),
        answers,
        definition_version: schema.version,
        media_keys: keys,
      });
    } catch (err) {
      if (err.status === 404) {
        setState("inactive");
        throw err;
      }
      if (err.status === 409) {
        // Changed since it was opened: show the new version, answers kept.
        try {
          await load();
        } catch {
          throw err;
        }
        throw new Error(
          "This checklist was changed since you opened it. The new version is shown now - your answers are kept. Answer any new questions and submit again."
        );
      }
      if (err.status === 400 && /name/i.test(err.message)) setNameError(err.message);
      throw err;
    }
  }

  return (
    <ChecklistUploadContext.Provider value={upload}>
      <main className="clp-page">
        <BrandHeader />
        <section className="card" ref={topRef}>
          <ChecklistForm
            key={formKey}
            schema={schema}
            header={
              <>
                <h1 className="clp-title">{schema.title}</h1>
                {schema.description && <p className="muted">{schema.description}</p>}
                <NameField
                  mode={form.name_mode}
                  names={names}
                  value={name}
                  onChange={(v) => {
                    setName(v);
                    setNameError(null);
                  }}
                  error={nameError}
                />
              </>
            }
            onSubmit={onSubmit}
            renderResult={(result) => (
              <div className="clp-done" role="status">
                <h1>Submitted - thank you</h1>
                <p>
                  Thanks, <strong>{name.trim().replace(/\s+/g, " ")}</strong>. Your checklist was sent.
                </p>
                {result.all_clear ? (
                  <p className="success">No issues reported.</p>
                ) : (
                  <>
                    <p className="clp-issues-note">You reported these issues:</p>
                    <pre className="checklist-issue-summary">{result.issue_summary}</pre>
                  </>
                )}
                <p className="muted">You can close this page now.</p>
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => {
                    mediaKeys.current = {};
                    setFormKey((k) => k + 1);
                  }}
                >
                  Fill in another
                </button>
              </div>
            )}
          />
        </section>
      </main>
    </ChecklistUploadContext.Provider>
  );
}
