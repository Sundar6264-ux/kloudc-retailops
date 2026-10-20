// Console > Masters (website ready A2): everyone who can sign in here. Add a
// master (temporary password, shown once), remove one (never yourself, never
// the last), reset another master's two-step sign-in, move your own to a
// new phone (a code from the current one first). Every change is audited.
import { useCallback, useEffect, useState } from "react";
import { enableTotp } from "../api.js";
import { addMaster, listMasters, moveTwoStep, removeMaster, resetMasterTwoStep } from "../clients/console.js";
import { cleanTotp } from "../login/loginModel.js";
import Icon from "../ui/icons.jsx";
import { Chip, Feedback, when } from "./ConsoleBits.jsx";
import { masterActions, masterStatus, masterUsernameError } from "./consoleModel.js";
import { platformErrorMessage } from "./platformModel.js";

export default function MastersTab({ search = "" }) {
  const [rows, setRows] = useState(null);
  const [message, setMessage] = useState(null);
  const [adding, setAdding] = useState(false);
  const load = useCallback(async () => {
    try {
      const r = await listMasters();
      setRows(r.masters || []);
    } catch (err) {
      setRows([]);
      setMessage({ tone: "bad", text: platformErrorMessage(err) });
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  // A master with two-step sign-in confirms these changes with a fresh code.
  const needCode = !!(rows || []).find((m) => m.is_me)?.two_step;
  const needle = search.trim().toLowerCase();
  const shown = (rows || []).filter(
    (m) => !needle || m.username.includes(needle) || (m.display_name || "").toLowerCase().includes(needle),
  );

  return (
    <div className="pf-masters" data-testid="masters-tab">
      <Feedback message={message} />
      <div className="pf-toolbar">
        <button type="button" aria-expanded={adding} onClick={() => setAdding((v) => !v)} data-testid="add-master">
          <Icon name="plus" size={16} />
          <span>Add master</span>
        </button>
      </div>
      {adding && (
        <AddMasterForm
          needCode={needCode}
          onDone={async (text) => {
            setMessage(text ? { tone: "ok", text } : null);
            await load();
          }}
          onClose={() => setAdding(false)}
        />
      )}
      {rows === null ? (
        <p className="muted">Loading…</p>
      ) : shown.length === 0 ? (
        <p className="muted pf-empty">No masters match.</p>
      ) : (
        <ul className="pf-list" aria-label="Masters">
          {shown.map((m) => (
            <MasterRow
              key={m.id}
              master={m}
              all={rows}
              needCode={needCode}
              onDone={async (text, tone = "ok") => {
                setMessage({ text, tone });
                await load();
              }}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function AddMasterForm({ needCode = false, onDone, onClose }) {
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState(null);
  const [copied, setCopied] = useState(false);

  async function submit(e) {
    e.preventDefault();
    const problem = masterUsernameError(username);
    if (problem) return setError(problem);
    if (needCode && cleanTotp(code).length !== 6) return setError("Enter the 6-digit code from your two-step sign-in app.");
    setBusy(true);
    setError(null);
    try {
      const out = await addMaster({
        username: username.trim(),
        displayName: displayName.trim(),
        code: needCode ? cleanTotp(code) : null,
      });
      setCreated(out);
      onDone(null);
    } catch (err) {
      setError(platformErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (created) {
    return (
      <div className="pf-row pf-created" role="status" data-testid="temp-password">
        <div className="pf-row-top">
          <Icon name="key-round" size={18} />
          <span className="pf-row-title">{created.username} can sign in now</span>
        </div>
        <p className="pf-row-meta">
          Send this <strong>temporary password</strong> privately. It is shown once; they choose their own at first
          sign-in (Store ID <span className="pf-code">PLATFORM</span>).
        </p>
        <div className="pf-secret-row">
          <code className="pf-secret">{created.temporary_password}</code>
          <button
            type="button"
            className="btn-secondary"
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(created.temporary_password);
                setCopied(true);
              } catch {
                setCopied(false);
              }
            }}
          >
            <Icon name={copied ? "check" : "copy"} size={16} />
            <span>{copied ? "Copied" : "Copy"}</span>
          </button>
          <button type="button" onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    );
  }

  return (
    <form className="pf-row pf-inline-form" onSubmit={submit} aria-label="Add master" noValidate>
      <label>
        Username
        <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="off" autoFocus required aria-invalid={!!error} aria-describedby={error ? "add-master-error" : undefined} />
      </label>
      <label>
        Name <span className="muted">(optional)</span>
        <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} autoComplete="off" />
      </label>
      {needCode && <StepUpCode value={code} onChange={setCode} />}
      <div className="pf-actions">
        <button type="submit" disabled={busy}>
          {busy ? "Adding…" : "Add"}
        </button>
        <button type="button" className="btn-secondary" onClick={onClose}>
          Cancel
        </button>
      </div>
      {error && (
        <p className="pf-field-error" id="add-master-error" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}

function MasterRow({ master, all, needCode = false, onDone }) {
  const [mode, setMode] = useState(null); // remove | reset | move
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const can = masterActions(master, all);
  const name = master.display_name && master.display_name !== master.username ? master.display_name : master.username;

  async function act(fn) {
    if (needCode && cleanTotp(code).length !== 6) return setError("Enter the 6-digit code from your two-step sign-in app.");
    setBusy(true);
    setError(null);
    try {
      await fn(needCode ? cleanTotp(code) : null);
      setCode("");
      setMode(null);
    } catch (err) {
      setError(platformErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="pf-row" data-testid={`master-${master.username}`}>
      <div className="pf-row-top">
        <span className="pf-avatar" aria-hidden="true">
          {(name || "?").slice(0, 2).toUpperCase()}
        </span>
        <span className="pf-row-title">{name}</span>
        {master.is_me && <span className="pf-chip tone-info">You</span>}
        <Chip chip={masterStatus(master)} />
      </div>
      <div className="pf-row-meta">
        {[master.username, `last sign-in ${when(master.last_login_at)}`].join(" · ")}
      </div>
      <div className="pf-actions">
        {can.resetTwoStep && (
          <button type="button" className="btn-secondary" aria-expanded={mode === "reset"} onClick={() => setMode(mode === "reset" ? null : "reset")}>
            Reset two-step
          </button>
        )}
        {master.is_me && master.two_step && (
          <button type="button" className="btn-secondary" aria-expanded={mode === "move"} onClick={() => setMode(mode === "move" ? null : "move")}>
            Move to a new phone
          </button>
        )}
        {can.remove && (
          <button type="button" className="pf-btn-danger-outline" aria-expanded={mode === "remove"} onClick={() => setMode(mode === "remove" ? null : "remove")}>
            Remove
          </button>
        )}
      </div>
      {mode === "reset" && (
        <ConfirmLine
          text={`Turn off ${name}'s two-step sign-in? They are signed out and set it up again at their next sign-in.`}
          action="Reset two-step"
          busy={busy}
          code={needCode ? { value: code, onChange: setCode } : null}
          onYes={() => act(async (c) => (await resetMasterTwoStep(master.id, c), onDone(`${name}'s two-step sign-in was reset.`)))}
          onNo={() => setMode(null)}
        />
      )}
      {mode === "remove" && (
        <ConfirmLine
          text={`Remove ${name}? They are signed out everywhere and can't sign in again unless a master adds them back.`}
          action="Remove"
          danger
          busy={busy}
          code={needCode ? { value: code, onChange: setCode } : null}
          onYes={() => act(async (c) => (await removeMaster(master.id, c), onDone(`${name} was removed.`)))}
          onNo={() => setMode(null)}
        />
      )}
      {mode === "move" && <MoveTwoStep onDone={() => (setMode(null), onDone("Two-step sign-in now uses your new phone."))} />}
      {error && (
        <p className="pf-field-error" role="alert">
          {error}
        </p>
      )}
    </li>
  );
}

// "Your two-step code": the acting master's current code for a sensitive change.
function StepUpCode({ value, onChange }) {
  return (
    <label className="pf-stepup">
      Your two-step code
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        inputMode="numeric"
        autoComplete="one-time-code"
        maxLength={7}
        placeholder="123456"
        data-testid="stepup-code"
      />
    </label>
  );
}

function ConfirmLine({ text, action, danger = false, busy, code = null, onYes, onNo }) {
  return (
    <div className="pf-confirm" role="group" aria-label={action}>
      <p>{text}</p>
      {code && <StepUpCode value={code.value} onChange={code.onChange} />}
      <div className="pf-actions">
        <button type="button" className={danger ? "pf-btn-danger" : undefined} disabled={busy} onClick={onYes}>
          {busy ? "Working…" : action}
        </button>
        <button type="button" className="btn-secondary" onClick={onNo}>
          Cancel
        </button>
      </div>
    </div>
  );
}

// Your own two-step sign-in to a new phone: the current phone's code, then
// the new secret (QR) and a code from the new phone.
function MoveTwoStep({ onDone }) {
  const [current, setCurrent] = useState("");
  const [setup, setSetup] = useState(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function begin(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setSetup(await moveTwoStep(cleanTotp(current)));
    } catch (err) {
      setError(platformErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  async function confirm(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await enableTotp(setup.pending, cleanTotp(code));
      onDone();
    } catch (err) {
      setError(platformErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return !setup ? (
    <form className="pf-inline-form" onSubmit={begin} aria-label="Move two-step sign-in">
      <label>
        Code from your current phone
        <input inputMode="numeric" autoComplete="one-time-code" value={current} onChange={(e) => setCurrent(e.target.value)} autoFocus />
      </label>
      <div className="pf-actions">
        <button type="submit" disabled={busy || cleanTotp(current).length < 6}>
          Continue
        </button>
      </div>
      {error && (
        <p className="pf-field-error" role="alert">
          {error}
        </p>
      )}
    </form>
  ) : (
    <form className="pf-inline-form" onSubmit={confirm} aria-label="Confirm the new phone">
      <div className="pf-qr">
        {setup.qr_svg && <img src={setup.qr_svg} alt="QR code for your authenticator app" width="168" height="168" />}
        <code className="pf-secret">{setup.secret}</code>
      </div>
      <label>
        Code from the new phone
        <input inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} autoFocus />
      </label>
      <div className="pf-actions">
        <button type="submit" disabled={busy || cleanTotp(code).length < 6}>
          Switch to this phone
        </button>
      </div>
      {error && (
        <p className="pf-field-error" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}
