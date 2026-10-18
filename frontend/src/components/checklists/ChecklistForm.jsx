import { useMemo, useRef, useState } from "react";
import ChecklistFields from "./ChecklistFields.jsx";
import {
  answersForSubmit,
  formErrors,
  normalizeSchema,
  requiredSlots,
  slotAnswered,
  visibleSectionKeys,
} from "./model.js";

// The fill-in form for one checklist schema (legacy blocks and/or contract
// items), with branching, required/validation checks and a progress bar.
// `onSubmit(answers)` does the POST and returns the server's result;
// `renderResult(result)` draws the confirmation. The builder's live preview
// uses the same component with `preview` (nothing is uploaded or sent).
export default function ChecklistForm({
  schema: rawSchema,
  header,
  onSubmit,
  renderResult,
  preview = false,
  submitLabel = "Submit",
}) {
  const schema = useMemo(() => normalizeSchema(rawSchema), [rawSchema]);
  const [answers, setAnswers] = useState({});
  const [showErrors, setShowErrors] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const seed = useMemo(() => Math.floor(Math.random() * 100000) + 1, []);
  const mediaCache = useRef({});

  const visibleKeys = useMemo(() => visibleSectionKeys(schema, answers), [schema, answers]);
  const slots = requiredSlots(schema, visibleKeys);
  const answeredCount = slots.filter((s) => slotAnswered(s, answers)).length;
  const total = slots.length;
  const errors = useMemo(() => formErrors(schema, answers, visibleKeys), [schema, answers, visibleKeys]);
  const shownErrors = showErrors ? errors : {};

  function setAnswer(id, value) {
    setAnswers((prev) => {
      const next = { ...prev };
      if (value == null || value === "") delete next[id];
      else next[id] = value;
      return next;
    });
  }

  function mergeAnswers(obj) {
    setAnswers((prev) => ({ ...prev, ...obj }));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    const errorKeys = Object.keys(errors);
    if (errorKeys.length) {
      setShowErrors(true);
      setError(null);
      // Bring the first problem into view.
      requestAnimationFrame(() => {
        const el = e.target.querySelector(".clf-has-error");
        if (el) el.scrollIntoView({ block: "center", behavior: "smooth" });
      });
      return;
    }
    const finalAnswers = answersForSubmit(schema, answers, visibleKeys);
    if (schema.issues_field && finalAnswers[schema.issues_field.id]) {
      finalAnswers[schema.issues_field.id] = finalAnswers[schema.issues_field.id].trim();
      if (!finalAnswers[schema.issues_field.id]) delete finalAnswers[schema.issues_field.id];
    }
    setSubmitting(true);
    setError(null);
    try {
      const data = await onSubmit(finalAnswers);
      setResult({ data, answers: finalAnswers });
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  if (result) return renderResult(result.data, result.answers, () => {
    setResult(null);
    setAnswers({});
    setShowErrors(false);
  });

  const errorCount = Object.keys(shownErrors).length;
  return (
    <>
      {header}
      <div className="progress-bar" style={{ margin: "0.75rem 0" }}>
        <div className="progress-bar-fill" style={{ width: `${total === 0 ? 0 : (answeredCount / total) * 100}%` }} />
      </div>
      <p className="muted checklist-progress-label">
        Answered {answeredCount} of {total}
      </p>
      <form onSubmit={handleSubmit} noValidate>
        {(
          <ChecklistFields
            schema={schema}
            visibleKeys={visibleKeys}
            answers={answers}
            setAnswer={setAnswer}
            mergeAnswers={mergeAnswers}
            errors={shownErrors}
            seed={seed}
            mediaCache={mediaCache}
            preview={preview}
            confirmFill={!preview}
          />
        )}
        {errorCount > 0 && (
          <p className="error">
            Please fix {errorCount} {errorCount === 1 ? "item" : "items"} marked above before submitting.
          </p>
        )}
        {error && <p className="error">{error}</p>}
        <div className="recon-form-submit" style={{ marginTop: "1rem" }}>
          <button type="submit" disabled={submitting}>
            {submitting ? "Submitting…" : submitLabel}
          </button>
        </div>
      </form>
    </>
  );
}
