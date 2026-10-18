"""A tiny, dependency-free PDF writer for the DEMO store's made-up invoices.

One page, Helvetica, plain text lines, a correct xref table - enough for
every viewer (and the app's PDF preview) to open it. Each page carries a
"SAMPLE - DEMO DATA, NOT A REAL DOCUMENT" banner.
"""

from typing import List, Sequence

BANNER = "SAMPLE - DEMO DATA, NOT A REAL DOCUMENT"


def _escape(text: str) -> str:
    text = text.encode("latin-1", "replace").decode("latin-1")
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def text_pdf(lines: Sequence[str], title: str = "Demo document") -> bytes:
    """A one-page US-letter PDF showing ``lines`` (top to bottom)."""
    content: List[str] = ["BT", "/F1 16 Tf", "72 740 Td", f"({_escape(BANNER)}) Tj", "/F1 11 Tf", "0 -28 Td"]
    for line in list(lines)[:48]:
        content.append(f"({_escape(line)}) Tj")
        content.append("0 -14 Td")
    content.append("ET")
    stream = "\n".join(content).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Title (" + _escape(title).encode("latin-1") + b") /Producer (StoreOps demo data) >>",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info {len(objects)} 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    return bytes(out)
