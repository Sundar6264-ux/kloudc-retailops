import { useState } from "react";

export default function PasswordInput({ value, onChange, placeholder, autoComplete, autoFocus, style }) {
  const [visible, setVisible] = useState(false);

  return (
    <div style={{ position: "relative", display: "flex", minWidth: 0, ...style }}>
      <input
        type={visible ? "text" : "password"}
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        autoComplete={autoComplete}
        autoFocus={autoFocus}
        style={{ flex: 1, minWidth: 0, paddingRight: "2.6rem" }}
      />
      <button
        type="button"
        onClick={() => setVisible((v) => !v)}
        aria-label={visible ? "Hide password" : "Show password"}
        style={{
          position: "absolute",
          right: "0.35rem",
          top: "50%",
          transform: "translateY(-50%)",
          // index.css's mobile `button { width: 100% }` rule otherwise
          // stretches this absolutely-positioned toggle to cover the whole
          // field, hiding the real input underneath it entirely (a real
          // reported bug - only the "Show" label was visible, centered,
          // with no way to tap into the password field at all).
          width: "auto",
          background: "none",
          border: "none",
          padding: "0.15rem 0.35rem",
          cursor: "pointer",
          fontSize: "0.78rem",
          fontWeight: 600,
          color: "var(--accent)",
        }}
      >
        {visible ? "Hide" : "Show"}
      </button>
    </div>
  );
}
