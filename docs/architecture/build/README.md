# Architecture document build

`../SPICETOWN_ARCHITECTURE.html` is generated - edit the section modules here, not the HTML.

```bash
bash docs/architecture/build/build.sh <scratch dir> [<out.pdf>]    # e.g. ~/spicetown-dev/SPICETOWN_ARCHITECTURE.pdf
PROD_DB=~/spicetown-backend/backend/spicetown.db bash ...           # also refresh row counts (read-only, mode=ro)
```

| File | Role |
|---|---|
| `extract_facts.py` | Imports `app.main` (never starts it) with the dev venv: routes + gates, tables + columns, feature keys, notification catalog, demo allowlist, settings names, team jobs, module docstrings, migration chain -> `data/facts.json` |
| `client_map.py` | Static analysis: website page -> api.js functions -> endpoints; app screen -> providers -> repositories -> Dio calls -> `data/clientmap.json` |
| `data/rowcounts.txt` | `select count(*)` per table, read-only from production |
| `doc_core.py` | HTML helpers (headings + TOC, tables, mermaid figures, sequence-text sanitising) |
| `s1_overview.py` ... `s9_ops.py`, `appendices.py` | The sections, written from the code, the contracts in `docs/` and the Oct 15 discovery reports |
| `build_doc.py` | Assembles `doc.src.html` (mermaid source blocks, CSS, cover, contents) |
| `render.mjs` | Playwright Chromium: mermaid@10.9.1 (pinned, local file) -> inline SVG sized to stay readable, writes the self-contained HTML (no scripts), prints Letter PDF with page numbers and bookmarks |
| `find_pages.py` | Reads the first PDF (pypdf) to put page numbers in the contents; `render.mjs` then prints again |

Rules: no secret values anywhere (configuration is listed by name only); the code wins over older docs.
