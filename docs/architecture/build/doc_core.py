"""Small HTML helpers shared by the section modules (build_doc.py assembles them)."""
import html
import re


class Raw(str):
    """A cell / text that is already HTML (not escaped again)."""


def esc(s):
    if isinstance(s, Raw):
        return s
    return html.escape("" if s is None else str(s))


def code(s):
    return Raw(f"<code>{esc(s)}</code>")


_SEQ_MSG = re.compile(r"^(\s*\S+?\s*(?:-->>|->>|-->|->|--x|-x|--\)|-\))[+-]?\s*[^:]+?:)(.*)$")


def sanitize(src):
    """Mermaid sequence diagrams treat ';' as a line break and '#' as an entity start, and an
    arrow inside message text can confuse the lexer: replace them in message and note text."""
    lines = src.strip("\n").splitlines()
    if not lines or not lines[0].strip().startswith("sequenceDiagram"):
        return src
    out = []
    for ln in lines:
        m = _SEQ_MSG.match(ln)
        head, text = (m.group(1), m.group(2)) if m else (None, None)
        if head is None and re.match(r"^\s*(Note (?:over|left of|right of) [^:]+:)(.*)$", ln):
            mm = re.match(r"^(\s*Note (?:over|left of|right of) [^:]+:)(.*)$", ln)
            head, text = mm.group(1), mm.group(2)
        if head is not None:
            text = text.replace(";", ",").replace("#", "No.").replace("->", "→")
            ln = head + text
        out.append(ln)
    # Few lanes: give each lane more room so messages wrap less (the page is ~700 px wide).
    lanes = sum(1 for ln in out if re.match(r"^\s*(participant|actor)\s", ln))
    if lanes:
        width = int(max(112, min(190, 700 / lanes - 22)))
        if width > 112:
            out.insert(0, '%%{init: {"sequence": {"width": ' + str(width) + "}}}%%")
    return "\n".join(out)


class Doc:
    def __init__(self):
        self.toc = []  # (level, id, number, title)
        self.parts = []
        self.fig_no = 0
        self.figs = []

    def add(self, s):
        self.parts.append(s)

    def h(self, level, number, title, anchor=None):
        anchor = anchor or "s-" + re.sub(r"[^a-z0-9]+", "-", f"{number} {title}".lower()).strip("-")
        if level <= 3:
            self.toc.append((level, anchor, number, title))
        cls = ' class="chapter"' if level == 1 else ""
        num = f'<span class="num">{esc(number)}</span> ' if number else ""
        self.add(f'<h{level} id="{anchor}"{cls}>{num}{esc(title)}</h{level}>')
        return anchor

    def p(self, text):
        self.add(f"<p>{text}</p>")

    def ul(self, items):
        self.add("<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>")

    def fig(self, mermaid, caption, size=None):
        """A mermaid diagram. size: None (fit width), 'small' (max 70% width), 'tall' (sequence that
        may be scaled to fit one page)."""
        self.fig_no += 1
        n = self.fig_no
        mermaid = sanitize(mermaid)
        cls = "figure" + (f" fig-{size}" if size else "")
        self.figs.append((n, caption))
        self.add(
            f'<figure class="{cls}" id="fig-{n}"><pre class="mermaid">{html.escape(mermaid.strip())}</pre>'
            f'<figcaption><b>Figure {n}.</b> {caption}</figcaption></figure>'
        )

    def table(self, headers, rows, cls="", widths=None, caption=None):
        out = [f'<table class="{cls}">']
        if caption:
            out.append(f"<caption>{caption}</caption>")
        if widths:
            out.append("<colgroup>" + "".join(f'<col style="width:{w}">' for w in widths) + "</colgroup>")
        out.append("<thead><tr>" + "".join(f"<th>{esc(h)}</th>" for h in headers) + "</tr></thead><tbody>")
        for r in rows:
            out.append("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>")
        out.append("</tbody></table>")
        self.add("".join(out))

    def note(self, text, kind="note"):
        self.add(f'<div class="callout {kind}">{text}</div>')

    def html(self):
        return "\n".join(self.parts)
