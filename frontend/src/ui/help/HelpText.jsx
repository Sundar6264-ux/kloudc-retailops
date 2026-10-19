// Renders help text with its two inline marks: **bold** and links
// ([text](page:id) / (article:id) / (term:key) / (https://...) / (mailto:...)).
// Links are plain hash hrefs ("#/help?a=reorder"), so they work inside the
// router and on the logged-out pages alike. `links={false}` renders link text
// only (for logged-out screens where the target can't open).
import { useHelp } from "./HelpProvider.jsx";
import { hashHref, parseInline, slug, targetHref } from "./helpModel.js";

// The DOM id of an article heading ("On this page" jumps to it).
export function headingDomId(block) {
  return `kc-h-${block.id || slug(block.text)}`;
}

export function HelpText({ text, links = true, onNavigate }) {
  const help = useHelp();
  const parts = parseInline(text);
  return (
    <>
      {parts.map((p, i) => {
        if (p.type === "bold") return <strong key={i}>{p.text}</strong>;
        if (p.type !== "link") return <span key={i}>{p.text}</span>;
        if (!links) return <span key={i}>{p.text}</span>;
        const href = targetHref(p.kind, p.target, { pages: help.content.pages });
        if (!href) return <span key={i}>{p.text}</span>;
        if (href.external) {
          const external = p.kind === "url";
          return (
            <a
              key={i}
              className="kc-help-link"
              href={href.external}
              {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
            >
              {p.text}
            </a>
          );
        }
        return (
          <a key={i} className={`kc-help-link kc-help-link-${p.kind}`} href={hashHref(href.path)} onClick={onNavigate}>
            {p.text}
          </a>
        );
      })}
    </>
  );
}

// A list of paragraphs / steps / bullets / callouts (an article's blocks, an
// FAQ answer, a troubleshooting fix).
export function HelpBlocks({ blocks, links = true, examples = {}, onNavigate }) {
  return (
    <>
      {(blocks || []).map((b, i) => {
        switch (b.type) {
          case "h":
            return (
              <h3 key={i} id={headingDomId(b)} className="kc-help-h">
                <HelpText text={b.text} links={links} onNavigate={onNavigate} />
              </h3>
            );
          case "steps":
            return (
              <ol key={i} className="kc-help-steps">
                {(b.items || []).map((it, j) => (
                  <li key={j}>
                    <HelpText text={it} links={links} onNavigate={onNavigate} />
                  </li>
                ))}
              </ol>
            );
          case "bullets":
            return (
              <ul key={i} className="kc-help-bullets">
                {(b.items || []).map((it, j) => (
                  <li key={j}>
                    <HelpText text={it} links={links} onNavigate={onNavigate} />
                  </li>
                ))}
              </ul>
            );
          case "tip":
          case "note":
          case "warning":
            return (
              <div key={i} className={`kc-help-callout kc-help-callout-${b.type}`} role="note">
                <span className="kc-help-callout-label">{b.type === "tip" ? "Tip" : b.type === "note" ? "Good to know" : "Careful"}</span>
                <p>
                  <HelpText text={b.text} links={links} onNavigate={onNavigate} />
                </p>
              </div>
            );
          case "example": {
            const Example = examples[b.id];
            return Example ? <Example key={i} /> : null;
          }
          default:
            return (
              <p key={i} className="kc-help-p">
                <HelpText text={b.text} links={links} onNavigate={onNavigate} />
              </p>
            );
        }
      })}
    </>
  );
}
