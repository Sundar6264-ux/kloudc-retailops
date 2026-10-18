import { useEffect, useRef, useState } from "react";
import { askQuestion } from "../api";
import InfoBlock from "./InfoBlock.jsx";

const EXAMPLE_QUESTIONS = [
  "How much cilantro do we have on hand?",
  "What needs reordering from Raja Foods?",
  "Any items with missing barcodes?",
  "Which vendor should I switch to for a cheaper price?",
];

export default function AskBot() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function send(question) {
    const text = (question ?? input).trim();
    if (!text || loading) return;
    setInput("");
    setError(null);
    // Only role/content go back to the API - metadata (model used, tool calls)
    // is local display state, not part of the conversation the model sees.
    const history = messages.map((m) => ({ role: m.role, content: m.content }));
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setLoading(true);
    try {
      const res = await askQuestion(text, history);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: res.answer,
          modelUsed: res.model_used,
          escalated: res.escalated,
          toolCalls: res.tool_calls || [],
        },
      ]);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  function handleSubmit(e) {
    e.preventDefault();
    send();
  }

  return (
    <section className="card ask-bot">
      <InfoBlock brief="Ask questions about real inventory, sales, ordering, and reconciliation data in plain language.">
        Answers come from the same reports as the rest of the app - reorder candidates, supplier
        projection, reconciliation, vendor price comparison, items sold, price history, dead
        stock, and barcode reports - never a guessed number. It's read-only: it can't log
        purchases, confirm deliveries, or change any data. Simple single-lookup questions run on a
        fast, cheap model; anything that needs combining multiple reports or real reasoning is
        automatically handed to a stronger model - each answer below shows which one ran, and
        which report(s) it pulled from.
      </InfoBlock>

      {messages.length === 0 && (
        <div className="ask-bot-examples">
          <p className="muted" style={{ marginBottom: "0.4rem" }}>
            Try asking:
          </p>
          {EXAMPLE_QUESTIONS.map((q) => (
            <button key={q} type="button" className="ask-bot-example" onClick={() => send(q)}>
              {q}
            </button>
          ))}
        </div>
      )}

      {/* Always rendered (even with zero messages) so it acts as the flex spacer
          that keeps the form pinned to the bottom of the card from the first view. */}
      <div className="ask-bot-thread">
        {messages.map((m, i) => (
          <div key={i} className={`ask-bot-message ask-bot-${m.role}`}>
            <div className="ask-bot-bubble">{m.content}</div>
            {m.role === "assistant" && (
              <div className="ask-bot-meta muted">
                {m.escalated ? "Answered by Sonnet (complex question)" : "Answered by Haiku"}
                {m.toolCalls.length > 0 && ` · checked: ${m.toolCalls.map((t) => t.tool).join(", ")}`}
              </div>
            )}
          </div>
        ))}
        {loading && (
          <div className="ask-bot-message ask-bot-assistant">
            <div className="ask-bot-bubble muted">Thinking…</div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {error && <p className="error">{error}</p>}

      <form onSubmit={handleSubmit} className="ask-bot-form">
        <input
          type="text"
          placeholder="Ask a question about inventory, sales, or ordering…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={loading}
        />
        <button type="submit" disabled={loading || !input.trim()}>
          Ask
        </button>
      </form>
    </section>
  );
}
