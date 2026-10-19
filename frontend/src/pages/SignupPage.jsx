import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { checkStoreCode, submitSignup } from "../api";
import PasswordInput from "../components/PasswordInput.jsx";
import { useBranding } from "../store/StoreContext.jsx";
import { normalizeStoreCode } from "../login/loginModel.js";
import {
  EMPTY_SIGNUP,
  MIN_PASSWORD,
  POS_OPTIONS,
  availabilityReason,
  SIGNUP_STORAGE_KEY,
  codeProblem,
  pendingUrl,
  signupBody,
  suggestCode,
  timezoneOptions,
  validateSignup,
} from "../signup/signupModel.js";
import "../platform/platform.css";

function browserTimezones() {
  try {
    return typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : [];
  } catch {
    return [];
  }
}

function browserTimezone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "";
  } catch {
    return "";
  }
}

// "Create your store" (docs/PLATFORM_CONTRACT.md 13.3, 14): a request the
// master admin approves. The store stays pending until then; approval sets
// it up and the owner signs in with the Store ID and login chosen here.
export default function SignupPage() {
  const brand = useBranding();
  const navigate = useNavigate();
  const zones = useMemo(() => timezoneOptions(browserTimezones()), []);
  const [form, setForm] = useState(() => {
    const tz = browserTimezone();
    return { ...EMPTY_SIGNUP, timezone: zones.includes(tz) ? tz : "" };
  });
  const [codeTouched, setCodeTouched] = useState(false);
  const [availability, setAvailability] = useState(null); // {code, available, reason}
  const [touched, setTouched] = useState({});
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState(null);
  const checkSeq = useRef(0);

  const code = normalizeStoreCode(form.requested_code);
  const codeAvailability = availability && availability.code === code ? availability : null;
  const errors = validateSignup(form, { codeAvailability });
  const show = (k) => (submitted || touched[k]) && errors[k];

  function set(key, value) {
    setForm((f) => {
      const next = { ...f, [key]: value };
      // Suggest a Store ID from the name until the person types their own.
      if (key === "store_name" && !codeTouched) next.requested_code = suggestCode(value);
      return next;
    });
  }

  // Live availability check (debounced) once the Store ID looks right.
  useEffect(() => {
    if (codeProblem(code)) return undefined;
    const mine = ++checkSeq.current;
    const t = setTimeout(async () => {
      try {
        const r = await checkStoreCode(code);
        if (mine === checkSeq.current) setAvailability({ code, available: !!r?.available, reason: availabilityReason(r) });
      } catch {
        if (mine === checkSeq.current) setAvailability(null); // the server checks again on submit
      }
    }, 400);
    return () => clearTimeout(t);
  }, [code]);

  async function handleSubmit(e) {
    e.preventDefault();
    setSubmitted(true);
    setServerError(null);
    if (Object.keys(errors).length) return;
    setBusy(true);
    try {
      const r = await submitSignup(signupBody(form));
      try {
        localStorage.setItem(SIGNUP_STORAGE_KEY, JSON.stringify({ id: r.request_id, t: r.status_token }));
      } catch {
        // the link in the address bar still works
      }
      navigate(pendingUrl(r.request_id, r.status_token));
    } catch (err) {
      if (err.status === 409 && err.body?.error === "code_unavailable") {
        // Taken between the live check and the submit: show it on the field.
        const reason = availabilityReason({ available: false, reason: err.body.reason, message: err.body.message });
        setAvailability({ code, available: false, reason });
        setServerError(reason || err.message);
      } else if (err.status === 429) setServerError("Too many requests from here. Please try again in an hour.");
      else if (err.status === 404) setServerError("Sign-up is closed right now. Please contact support.");
      else if (!err.status) setServerError("Can't reach the server. Check your connection and try again.");
      else setServerError(err.message || "Couldn't send the request. Please check the form and try again.");
    } finally {
      setBusy(false);
    }
  }

  const field = (key, label, input, { full = false, hint = null } = {}) => (
    <label className={`signup-field${full ? " full" : ""}`}>
      {label}
      {input}
      {show(key) ? <span className="field-error">{errors[key]}</span> : hint}
    </label>
  );
  const text = (key, props = {}) => (
    <input
      type="text"
      name={key}
      value={form[key]}
      onChange={(e) => set(key, e.target.value)}
      onBlur={() => setTouched((t) => ({ ...t, [key]: true }))}
      {...props}
    />
  );

  const codeHint = codeAvailability ? (
    codeAvailability.available ? (
      <span className="field-ok">Available</span>
    ) : (
      <span className="field-error">{codeAvailability.reason || "That Store ID is taken. Choose another."}</span>
    )
  ) : (
    <span className="muted field-hint">Your team types this to sign in. Letters, digits and _.</span>
  );

  return (
    <div className="signup-page">
      <form className="card signup-card" onSubmit={handleSubmit} noValidate>
        <div className="page-header" style={{ paddingRight: 0, marginBottom: "1rem" }}>
          <h1>Create your store</h1>
          <p className="muted">
            Tell us about your store. We review every request; once it's approved your store is set up and you sign in
            with the Store ID and login you choose here.
          </p>
        </div>
        <div className="signup-grid">
          {field("store_name", "Store name", text("store_name", { autoComplete: "organization", maxLength: 80 }), {
            full: true,
          })}
          {field(
            "requested_code",
            "Store ID",
            <input
              type="text"
              name="requested_code"
              data-testid="signup-code"
              value={form.requested_code}
              onChange={(e) => {
                setCodeTouched(true);
                set("requested_code", e.target.value.replace(/[^A-Za-z0-9_\- ]/g, "").slice(0, 24));
              }}
              onBlur={() => {
                setTouched((t) => ({ ...t, requested_code: true }));
                set("requested_code", normalizeStoreCode(form.requested_code));
              }}
              autoCapitalize="characters"
              spellCheck={false}
            />,
            { hint: codeHint },
          )}
          {field(
            "pos_provider",
            "Point of sale",
            <select name="pos_provider" value={form.pos_provider} onChange={(e) => set("pos_provider", e.target.value)}>
              {POS_OPTIONS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>,
          )}
          {field("contact_name", "Your name", text("contact_name", { autoComplete: "name" }))}
          {field(
            "contact_email",
            "Email",
            text("contact_email", { type: "email", autoComplete: "email", inputMode: "email" }),
          )}
          {field("contact_phone", "Phone (optional)", text("contact_phone", { type: "tel", autoComplete: "tel" }))}
          {field(
            "timezone",
            "Store time zone",
            <select name="timezone" value={form.timezone} onChange={(e) => set("timezone", e.target.value)}>
              <option value="">Choose…</option>
              {zones.map((z) => (
                <option key={z} value={z}>
                  {z.replace(/_/g, " ")}
                </option>
              ))}
            </select>,
          )}
          {field("address", "Address (optional)", text("address", { autoComplete: "street-address" }), { full: true })}
          {field("owner_username", "Your username", text("owner_username", { autoComplete: "username" }), {
            hint: <span className="muted field-hint">You'll be the store's first admin.</span>,
          })}
          <span />
          {field(
            "owner_password",
            "Password",
            <PasswordInput
              value={form.owner_password}
              onChange={(e) => set("owner_password", e.target.value)}
              autoComplete="new-password"
            />,
            { hint: <span className="muted field-hint">At least {MIN_PASSWORD} characters.</span> },
          )}
          {field(
            "owner_password_confirm",
            "Password again",
            <PasswordInput
              value={form.owner_password_confirm}
              onChange={(e) => set("owner_password_confirm", e.target.value)}
              autoComplete="new-password"
            />,
          )}
          {field(
            "notes",
            "Anything we should know? (optional)",
            <textarea name="notes" rows={3} maxLength={2000} value={form.notes} onChange={(e) => set("notes", e.target.value)} />,
            { full: true },
          )}
          <div className="signup-honeypot" aria-hidden="true">
            <label>
              Website
              <input
                type="text"
                name="website"
                tabIndex={-1}
                autoComplete="off"
                value={form.website}
                onChange={(e) => set("website", e.target.value)}
              />
            </label>
          </div>
          <div className="full">
            <label className="signup-check">
              <input
                type="checkbox"
                name="accept_terms"
                checked={form.accept_terms}
                onChange={(e) => set("accept_terms", e.target.checked)}
              />
              <span>
                I accept the{" "}
                {brand.legal?.termsUrl ? (
                  <a href={brand.legal.termsUrl} target="_blank" rel="noopener noreferrer">
                    terms of service
                  </a>
                ) : (
                  "terms of service"
                )}
                {brand.legal?.privacyUrl && (
                  <>
                    {" "}
                    and the{" "}
                    <a href={brand.legal.privacyUrl} target="_blank" rel="noopener noreferrer">
                      privacy policy
                    </a>
                  </>
                )}
                .
              </span>
            </label>
            {show("accept_terms") && <span className="field-error">{errors.accept_terms}</span>}
          </div>
        </div>
        {serverError && (
          <p className="error" role="alert" style={{ marginTop: "0.8rem" }}>
            {serverError}
          </p>
        )}
        {submitted && Object.keys(errors).length > 0 && (
          <p className="error" style={{ marginTop: "0.8rem" }}>
            Please fix the highlighted fields.
          </p>
        )}
        <div className="signup-actions">
          <button type="submit" disabled={busy} style={{ width: "auto" }}>
            {busy ? "Sending…" : "Request my store"}
          </button>
          <Link to="/login">Already have a Store ID? Sign in</Link>
        </div>
      </form>
    </div>
  );
}
