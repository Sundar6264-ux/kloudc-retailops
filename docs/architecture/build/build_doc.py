"""Assemble the architecture document source (HTML with mermaid blocks).

    python3 build_doc.py <data dir> <out.src.html>

<data dir> holds facts.json (extract_facts.py), clientmap.json (client_map.py) and rowcounts.txt
(read-only `select count(*)` per table from production). render.mjs then renders the diagrams,
writes the self-contained SPICETOWN_ARCHITECTURE.html and prints the PDF. See README.md.
"""
import datetime as dt
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import appendices  # noqa: E402
import s1_overview  # noqa: E402
import s2_deploy  # noqa: E402
import s3_security  # noqa: E402
import s4_data  # noqa: E402
import s5_backend  # noqa: E402
import s6_website  # noqa: E402
import s7_app  # noqa: E402
import s8a_flows  # noqa: E402
import s8b_flows  # noqa: E402
import s8c_flows  # noqa: E402
import s9_ops  # noqa: E402
from doc_core import Doc, esc  # noqa: E402

WEB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
MOBILE = os.path.join(os.path.dirname(WEB), "mobile")

CSS = r"""
:root { --ink:#1d2430; --muted:#5b6574; --line:#d7dce3; --soft:#f4f6f9; --accent:#b4302a; --accent-soft:#fbeceb; --code:#f1f3f6; }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { margin: 0; background: #fff; color: var(--ink); font: 10.5pt/1.45 -apple-system, "Helvetica Neue", Helvetica, Arial, sans-serif; }
main { max-width: 190mm; margin: 0 auto; padding: 0 2mm; }
h1, h2, h3 { line-height: 1.2; color: var(--ink); break-after: avoid; }
h1 { font-size: 21pt; margin: 0 0 6mm; padding-bottom: 2mm; border-bottom: 2px solid var(--accent); }
h1.chapter { break-before: page; }
h2 { font-size: 14pt; margin: 7mm 0 2.5mm; }
h3 { font-size: 11.5pt; margin: 5mm 0 2mm; }
h1 .num, h2 .num, h3 .num { color: var(--accent); margin-right: 1.5mm; }
p { margin: 0 0 2.5mm; }
ul { margin: 0 0 3mm; padding-left: 5mm; }
li { margin: 0 0 1mm; }
code { font: 8.6pt/1.3 "SF Mono", Menlo, Consolas, monospace; background: var(--code); padding: 0 2px; border-radius: 2px; overflow-wrap: anywhere; }
table { width: 100%; border-collapse: collapse; margin: 2mm 0 4mm; font-size: 9pt; table-layout: fixed; }
th, td { border: 1px solid var(--line); padding: 1.4mm 1.8mm; text-align: left; vertical-align: top; overflow-wrap: anywhere; }
th { background: var(--soft); font-weight: 600; }
thead { display: table-header-group; }
tr { break-inside: avoid; }
table.compact th, table.compact td { padding: 1mm 1.5mm; }
table.small { font-size: 8.2pt; }
table.small code { font-size: 7.6pt; }
table.tiny { font-size: 7.4pt; }
table.tiny code { font-size: 7pt; }
table.endpoints code { background: none; padding: 0; }
.muted { color: var(--muted); }
.tag { display: inline-block; font-size: 7pt; padding: 0 3px; border-radius: 2px; border: 1px solid var(--line); }
.tag.secret { color: var(--accent); border-color: var(--accent); }
.callout { border-left: 3px solid var(--accent); background: var(--accent-soft); padding: 2.5mm 3.5mm; margin: 3mm 0 4mm; break-inside: avoid; }
figure { margin: 3mm 0 5mm; break-inside: avoid; page-break-inside: avoid; text-align: center; }
figure pre.mermaid { margin: 0; background: none; text-align: center; }
figure svg { max-width: 100% !important; height: auto; }
figure svg .messageText, figure svg .noteText, figure svg .loopText, figure svg .labelText { paint-order: stroke fill !important; stroke: #fff !important; stroke-width: 3.5px !important; stroke-linejoin: round !important; }
figcaption { font-size: 8.8pt; color: var(--muted); margin-top: 1.5mm; text-align: left; }
.cover { height: 240mm; display: flex; flex-direction: column; justify-content: center; }
.cover .brand { color: var(--accent); font-weight: 700; letter-spacing: .08em; text-transform: uppercase; font-size: 10pt; }
.cover h1.title { font-size: 30pt; border: 0; margin: 4mm 0 3mm; padding: 0; }
.cover .sub { font-size: 13pt; color: var(--muted); margin-bottom: 12mm; }
.cover table { width: 120mm; font-size: 9.5pt; }
nav.toc { break-before: page; }
nav.toc h1 { break-before: auto; }
nav.toc ol { list-style: none; margin: 0; padding: 0; }
nav.toc li { margin: 0; }
nav.toc a { display: flex; color: var(--ink); text-decoration: none; padding: .6mm 0; }
nav.toc a .t { flex: 0 1 auto; }
nav.toc a .dots { flex: 1 1 auto; border-bottom: 1px dotted #b8bfca; margin: 0 2mm 1.2mm; }
nav.toc a .pg { flex: 0 0 auto; color: var(--muted); min-width: 7mm; text-align: right; }
nav.toc .l1 > a { font-weight: 700; margin-top: 2.2mm; }
nav.toc .l2 > a { padding-left: 5mm; font-size: 9.6pt; }
nav.toc .l3 > a { padding-left: 10mm; font-size: 9pt; color: var(--muted); }
.figlist { font-size: 8.8pt; columns: 2; column-gap: 8mm; }
.figlist div { break-inside: avoid; margin-bottom: .8mm; }
a { color: var(--accent); }
@media screen { main { padding: 10mm 6mm 30mm; } h1.chapter { margin-top: 16mm; } }
@page { size: Letter; }
"""


def git_head(path):
    try:
        return subprocess.check_output(["git", "-C", path, "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001
        return "?"


def app_version():
    try:
        for line in open(os.path.join(MOBILE, "pubspec.yaml")):
            if line.startswith("version:"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "?"


def main():
    data_dir, out = sys.argv[1], sys.argv[2]
    facts = json.load(open(os.path.join(data_dir, "facts.json")))
    clientmap = json.load(open(os.path.join(data_dir, "clientmap.json")))
    rowcounts = {}
    for line in open(os.path.join(data_dir, "rowcounts.txt")):
        if "|" in line:
            k, v = line.strip().split("|", 1)
            rowcounts[k] = int(v)
    meta = {
        "web_head": git_head(WEB),
        "app_head": git_head(MOBILE),
        "app_version": app_version(),
        "alembic_head": facts["alembic"][-1]["rev"],
        "routes": len(facts["routes"]),
        "tables": len(facts["tables"]),
        "modules": len(facts["modules"]),
        "web_pages": len(clientmap["web"]),
        "app_screens": len(clientmap["app"]),
        "repos": len(clientmap["repo_eps"]),
        "generated": dt.date.today().isoformat(),
    }
    ctx = {"facts": facts, "clientmap": clientmap, "rowcounts": rowcounts, "meta": meta, "web_src": os.path.join(WEB, "frontend", "src")}

    body = Doc()
    for mod in (s1_overview, s2_deploy, s3_security, s4_data, s5_backend, s6_website, s7_app, s8a_flows, s8b_flows, s8c_flows, s9_ops, appendices):
        mod.render(body, ctx)

    toc = ['<nav class="toc" id="toc"><h1>Contents</h1><ol>']
    for level, anchor, number, title in body.toc:
        toc.append(
            f'<li class="l{level}"><a href="#{anchor}"><span class="t">{esc(number)}&nbsp; {esc(title)}</span>'
            f'<span class="dots"></span><span class="pg" data-for="{anchor}"></span></a></li>'
        )
    toc.append("</ol>")
    toc.append('<h2 id="figures">List of figures</h2><div class="figlist">')
    for n, cap in body.figs:
        short = cap.split(". ")[0].split(":")[0]
        toc.append(f'<div><a href="#fig-{n}">Figure {n}</a> - {short}</div>')
    toc.append("</div></nav>")

    cover = f"""
<section class="cover">
  <div class="brand">Spice Town &middot; Operations platform</div>
  <h1 class="title">Technical architecture</h1>
  <div class="sub">How the website, the mobile app and the server are connected, and how every piece of data is served - end to end.</div>
  <table>
    <tr><th>Website + backend</th><td><code>website</code> @ <code>{esc(meta['web_head'])}</code> (branch ops-fixes)</td></tr>
    <tr><th>Mobile app</th><td><code>mobile</code> @ <code>{esc(meta['app_head'])}</code>, version <code>{esc(meta['app_version'])}</code></td></tr>
    <tr><th>Database schema</th><td>alembic head <code>{esc(meta['alembic_head'])}</code></td></tr>
    <tr><th>State described</th><td>After the Oct 15 batch (speed, insights, notifications, team tasks, navigation)</td></tr>
    <tr><th>Generated</th><td>{esc(meta['generated'])}</td></tr>
  </table>
</section>"""

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spice Town Architecture</title>
<style>{CSS}</style>
</head>
<body>
<main>
{cover}
{''.join(toc)}
{body.html()}
</main>
</body>
</html>
"""
    open(out, "w").write(html)
    print(f"wrote {out}: {len(body.figs)} figures, {len(body.toc)} toc entries, {len(html)//1024} KB")


if __name__ == "__main__":
    main()
