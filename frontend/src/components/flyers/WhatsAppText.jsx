import { parseWhatsApp } from "./flyerModel.js";

// A caption as WhatsApp shows it: *bold*, _italic_, ~strikethrough~ and
// ```monospace```. Built from text segments, never from HTML.
export default function WhatsAppText({ text, className = "" }) {
  const segments = parseWhatsApp(text);
  return (
    <span className={`fly-wa-text ${className}`}>
      {segments.map((s, i) => {
        let node = s.text;
        if (s.mono) node = <code>{node}</code>;
        if (s.strike) node = <s>{node}</s>;
        if (s.italic) node = <em>{node}</em>;
        if (s.bold) node = <strong>{node}</strong>;
        return <span key={i}>{node}</span>;
      })}
    </span>
  );
}
