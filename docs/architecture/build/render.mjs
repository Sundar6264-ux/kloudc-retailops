// Render the architecture document: mermaid diagrams -> inline SVG, write the self-contained HTML,
// print the PDF (Letter, page numbers), fill the table of contents with page numbers, print again.
//
//   node render.mjs <src.html> <mermaid.min.js> <out.html> <out.pdf> <pagemap.json|-> <report.json>
//
// <pagemap.json> = {anchor: page} from find_pages.py after a first print; "-" on the first pass.
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import url from "node:url";

const here = path.dirname(url.fileURLToPath(import.meta.url));
const require = createRequire(path.join(here, "..", "..", "..", "e2e", "package.json"));
const { chromium } = require("playwright");

const [srcHtml, mermaidJs, outHtml, outPdf, pageMapFile, reportFile] = process.argv.slice(2);

// Letter 8.5in wide minus 13 mm margins each side, at 96 px/in; height minus 18 + 16 mm margins
// and room for a caption.
const CONTENT_W = Math.floor((8.5 * 96) - (26 / 25.4) * 96); // ~717 px
const MAX_FIG_H = Math.floor((11 * 96) - (34 / 25.4) * 96) - 70; // ~862 px

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: CONTENT_W + 40, height: 1200 } });
await page.goto(url.pathToFileURL(path.resolve(srcHtml)).href);
await page.addScriptTag({ path: mermaidJs });

const report = await page.evaluate(async ({ CONTENT_W, MAX_FIG_H }) => {
  const mermaid = window.mermaid;
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    theme: "base",
    themeVariables: {
      fontFamily: "-apple-system, Helvetica Neue, Helvetica, Arial, sans-serif",
      fontSize: "15px",
      primaryColor: "#fdf1ef",
      primaryBorderColor: "#b4302a",
      primaryTextColor: "#1d2430",
      lineColor: "#5b6574",
      secondaryColor: "#eef2f7",
      tertiaryColor: "#f7f8fa",
      noteBkgColor: "#fff8e1",
      noteBorderColor: "#d9b44a",
      actorBkg: "#fdf1ef",
      actorBorder: "#b4302a",
      signalColor: "#1d2430",
      labelBoxBkgColor: "#eef2f7",
    },
    flowchart: { useMaxWidth: true, htmlLabels: true, nodeSpacing: 28, rankSpacing: 38, padding: 8 },
    sequence: { useMaxWidth: true, wrap: true, width: 112, actorMargin: 22, actorFontSize: 13, messageFontSize: 13, noteFontSize: 12, boxMargin: 5, boxTextMargin: 3, noteMargin: 6, messageMargin: 26, mirrorActors: false, wrapPadding: 6 },
    er: { useMaxWidth: true, fontSize: 13 },
    gantt: { useMaxWidth: true, fontSize: 12, barHeight: 18, barGap: 4, leftPadding: 230, sectionFontSize: 13 },
  });
  const out = [];
  const pres = Array.from(document.querySelectorAll("pre.mermaid"));
  let i = 0;
  for (const pre of pres) {
    i += 1;
    const fig = pre.closest("figure");
    const id = fig ? fig.id : `m${i}`;
    const text = pre.textContent;
    try {
      const { svg } = await mermaid.render(`svg-${id}`, text);
      const holder = document.createElement("div");
      holder.className = "diagram";
      holder.innerHTML = svg;
      pre.replaceWith(holder);
      const el = holder.querySelector("svg");
      const vb = (el.getAttribute("viewBox") || "").split(/\s+/).map(Number);
      const w = vb[2] || el.getBBox().width;
      const h = vb[3] || el.getBBox().height;
      let width = Math.min(w, CONTENT_W);
      if ((h * width) / w > MAX_FIG_H) width = (w * MAX_FIG_H) / h;
      el.removeAttribute("height");
      el.style.maxWidth = "100%";
      el.style.width = `${Math.round(width)}px`;
      el.style.height = "auto";
      out.push({ id, w: Math.round(w), h: Math.round(h), scale: +(width / w).toFixed(2), ok: true });
    } catch (e) {
      out.push({ id, ok: false, error: String(e && e.message ? e.message : e).slice(0, 400) });
      const err = document.createElement("pre");
      err.textContent = "DIAGRAM ERROR: " + String(e);
      pre.replaceWith(err);
      // mermaid leaves an error svg behind
      document.querySelectorAll(`#dsvg-${id}, #svg-${id}`).forEach((n) => n.remove());
    }
  }
  // Remove anything mermaid appended to body.
  document.querySelectorAll("body > div[id^='dsvg'], body > svg[id^='svg-']").forEach((n) => n.remove());
  document.querySelectorAll("script").forEach((s) => s.remove());
  return out;
}, { CONTENT_W, MAX_FIG_H });

if (pageMapFile && pageMapFile !== "-") {
  const map = JSON.parse(fs.readFileSync(pageMapFile, "utf8"));
  await page.evaluate((map) => {
    document.querySelectorAll("nav.toc .pg").forEach((s) => {
      const p = map[s.dataset.for];
      if (p) s.textContent = String(p);
    });
  }, map);
}

const html = "<!doctype html>\n" + (await page.evaluate(() => document.documentElement.outerHTML));
fs.writeFileSync(outHtml, html);

await page.emulateMedia({ media: "print" });
await page.pdf({
  path: outPdf,
  format: "Letter",
  printBackground: true,
  displayHeaderFooter: true,
  margin: { top: "18mm", bottom: "16mm", left: "13mm", right: "13mm" },
  headerTemplate:
    '<div style="width:100%;font-size:7.5px;color:#8a93a1;padding:0 13mm;display:flex;justify-content:space-between;font-family:Helvetica,Arial,sans-serif"><span>Spice Town - Technical architecture</span><span>Oct 15 batch</span></div>',
  footerTemplate:
    '<div style="width:100%;font-size:8px;color:#5b6574;padding:0 13mm;text-align:right;font-family:Helvetica,Arial,sans-serif">Page <span class="pageNumber"></span> of <span class="totalPages"></span></div>',
  outline: true,
  tagged: true,
});

// Where each heading is, for the next pass (anchor -> heading text).
const heads = await page.evaluate(() =>
  Array.from(document.querySelectorAll("nav.toc .pg")).map((s) => {
    const h = document.getElementById(s.dataset.for);
    return { anchor: s.dataset.for, text: h ? h.textContent.replace(/\s+/g, " ").trim() : "" };
  }),
);
fs.writeFileSync(reportFile, JSON.stringify({ figures: report, headings: heads }, null, 1));
await browser.close();
const bad = report.filter((r) => !r.ok);
const small = report.filter((r) => r.ok && r.scale < 0.6);
console.log(`figures ${report.length}, errors ${bad.length}, scaled below 0.6: ${small.map((r) => `${r.id}@${r.scale}`).join(" ")}`);
for (const b of bad) console.log("ERROR", b.id, b.error);
