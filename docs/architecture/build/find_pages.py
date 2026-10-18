"""Find the PDF page of every table-of-contents heading (second render pass fills them in).

    python find_pages.py <out.pdf> <report.json> <pagemap.json>

Needs pypdf. Headings are searched in order, each from the page of the previous one, by their
normalised text (number + title); the contents pages themselves are skipped.
"""
import json
import re
import sys
import unicodedata

import pypdf


def norm(s):
    return re.sub(r"[^a-z0-9]+", "", unicodedata.normalize("NFKC", s).lower())


def main():
    pdf, report, out = sys.argv[1:4]
    pages = [norm(p.extract_text() or "") for p in pypdf.PdfReader(pdf).pages]
    heads = json.load(open(report))["headings"]
    # Skip the cover and the contents: start after the last page that holds the list of figures.
    start = 0
    for i, t in enumerate(pages):
        if "listoffigures" in t:
            start = i + 1
            break
    found, pos, missing = {}, start, []
    for h in heads:
        key = norm(h["text"])[:40]
        hit = None
        for i in range(pos, len(pages)):
            if key and key in pages[i]:
                hit = i
                break
        if hit is None:
            # titles can wrap across a line break oddly: try a shorter key
            short = key[:22]
            for i in range(pos, len(pages)):
                if short and short in pages[i]:
                    hit = i
                    break
        if hit is None:
            missing.append(h["text"])
            continue
        found[h["anchor"]] = hit + 1
        pos = hit
    json.dump(found, open(out, "w"), indent=1)
    print(f"{len(found)} of {len(heads)} headings placed; missing: {missing[:8]}")


if __name__ == "__main__":
    main()
