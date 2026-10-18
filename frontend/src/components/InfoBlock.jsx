import { useState } from "react";

// A one-line summary with a "?" toggle for the full explanation, so every
// tab isn't leading with a wall of text. `children` is the full explanation,
// only rendered once expanded.
export default function InfoBlock({ brief, children }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="info-block">
      <p className="muted info-block-brief">
        {brief}{" "}
        <button
          type="button"
          className="info-toggle"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
        >
          {open ? "Less ▲" : "? More"}
        </button>
      </p>
      {open && <div className="info-block-full muted">{children}</div>}
    </div>
  );
}
