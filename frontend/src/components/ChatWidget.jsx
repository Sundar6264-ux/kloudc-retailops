import { useState } from "react";
import AskBot from "./AskBot.jsx";

// Floating chat bubble, rendered once in App.jsx outside <Routes> so it's
// available on every tab and its conversation survives navigating between
// pages (the component doesn't unmount on route changes).
export default function ChatWidget() {
  const [open, setOpen] = useState(false);

  return (
    <>
      {open && (
        <div className="chat-widget-panel">
          <div className="chat-widget-header">
            <span>Ask Inventory Bot</span>
            <button
              type="button"
              className="chat-widget-close"
              onClick={() => setOpen(false)}
              aria-label="Close chat"
            >
              ✕
            </button>
          </div>
          <div className="chat-widget-body">
            <AskBot />
          </div>
        </div>
      )}
      <button
        type="button"
        className="chat-widget-toggle"
        onClick={() => setOpen((v) => !v)}
        aria-label={open ? "Close Ask Inventory Bot" : "Open Ask Inventory Bot"}
        aria-expanded={open}
      >
        {open ? "✕" : "💬"}
      </button>
    </>
  );
}
