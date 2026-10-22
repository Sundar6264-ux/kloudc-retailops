// "Download" with a small file-type menu (Excel / PDF / CSV) for a PO -
// on the cart, the Send PO dialog, Placed Orders and the Recent PO sends
// log. The file is made by the server from the PO as it is stored today.
import { useState } from "react";
import { downloadPo } from "../../api";
import Menu, { MenuItem } from "../../ui/Menu.jsx";
import { PO_FORMATS } from "./poDownloadModel.js";

function DownloadGlyph() {
  return (
    <svg className="po-btn-icon" width="14" height="14" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path
        d="M8 2.5v7.5M4.75 7 8 10.25 11.25 7M3 13.25h10"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// target: { kind: "order" | "send", id } or { kind: "cart", supplier }.
// onDownload(format): optional - replaces the default download (the cart
// keeps its own PDF route, which also logs the PDF on Placed Orders).
// label: the button's words ("Download" by default; a count can follow).
// fixed: inside a scrolling table, so the menu isn't cut off (ui/Menu.jsx).
export default function PoDownloadMenu({
  target,
  supplier,
  onDownload,
  disabled = false,
  label = "Download",
  buttonClass = "btn-secondary po-dl-btn",
  align = "end",
  onError,
  testId,
  fixed = false,
}) {
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);

  async function pick(format, close) {
    close();
    setBusy(format);
    setError(null);
    onError?.(null);
    try {
      if (onDownload) await onDownload(format);
      else await downloadPo(target, format, supplier);
    } catch (err) {
      const message = err?.message || "The file couldn't be made. Try again.";
      if (onError) onError(message);
      else setError(message);
    } finally {
      setBusy(null);
    }
  }

  const name = supplier || target?.supplier || "this order";
  return (
    <span className="po-dl">
      {disabled || busy ? (
        <button type="button" className={buttonClass} disabled>
          <DownloadGlyph />
          <span>{busy ? "Preparing…" : label}</span>
        </button>
      ) : (
        <Menu
          className="po-dl-menu"
          buttonClass={buttonClass}
          label={`Download the PO for ${name}`}
          align={align}
          testId={testId}
          fixed={fixed}
          button={
            <>
              <DownloadGlyph />
              <span>{label}</span>
              <span className="po-dl-caret" aria-hidden="true">
                ▾
              </span>
            </>
          }
        >
          {(close) =>
            PO_FORMATS.map((f) => (
              <MenuItem key={f.value} hint={f.hint} onSelect={() => pick(f.value, close)} testId={`po-dl-${f.value}`}>
                {f.label}
              </MenuItem>
            ))
          }
        </Menu>
      )}
      {error && (
        <span className="error po-dl-error" role="alert">
          {error}
        </span>
      )}
    </span>
  );
}
