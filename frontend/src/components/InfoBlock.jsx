import { useState } from "react";
import Icon from "../ui/icons.jsx";

// A one-line summary with an "ⓘ More" toggle for the full explanation, so
// every tab isn't leading with a wall of text (the same info icon as the
// page header's ⓘ). `children` is the full explanation, only rendered once
// expanded.
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
          {open ? <Icon name="chevron-down" size={13} style={{ transform: "rotate(180deg)" }} /> : <Icon name="info" size={13} />}
          {open ? "Less" : "More"}
        </button>
      </p>
      {open && <div className="info-block-full muted">{children}</div>}
    </div>
  );
}
