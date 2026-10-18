// Suite B - Marketing flyers: upload JPG / HEIC / PDF, only "Use for flyers" groups offered, send through the MOCK
// Whapi (one group set to fail), retry, history in the drawer, library search, access (demo / no feature).
// node flyers.mjs [width ...]
import { chromium, as, go, settle, hscroll, check, note, done, shot, api, mainText, sleep, USERS, W, BASE } from "./lib.mjs";
import fs from "node:fs";

const widths = process.argv.slice(2).map(Number).filter(Boolean);
const WIDTHS = widths.length ? widths : [1280, 375];
const RUN = Date.now().toString(36).slice(-5);
const LOG = `${W}/whapi_log.jsonl`;
const FAIL = `${W}/whapi_fail.txt`;
const whapi = () => (fs.existsSync(LOG) ? fs.readFileSync(LOG, "utf8").trim().split("\n").filter(Boolean).map((l) => JSON.parse(l)) : []);
const marketing = USERS.groups.filter((g) => g.marketing && g.active && g.chat_id);
const browser = await chromium.launch();

async function waitSends(page, flyerId, ms = 90000) {
  const t0 = Date.now();
  let f;
  while (Date.now() - t0 < ms) {
    f = (await api(page, "GET", `/api/flyers/${flyerId}`)).body;
    if (f?.sends?.length && f.sends.every((s) => s.status === "sent" || s.status === "failed")) return f;
    await sleep(2000);
  }
  return f;
}

async function upload(page, file, title, category) {
  const card = page.locator("section", { has: page.locator('input[type=file]') }).first();
  await page.locator("input[type=file]").first().setInputFiles(file);
  await page.locator("#" + (await page.locator("label.fly-label", { hasText: /^Title/ }).first().getAttribute("for"))).fill(title);
  const catId = await page.locator("label.fly-label", { hasText: /^(Type|Category)/ }).first().getAttribute("for");
  if (catId) await page.locator("#" + catId).selectOption({ label: category });
  return card;
}

for (const width of WIDTHS) {
  const tag = `${RUN}-${width}`;
  console.log(`\n== Flyers @${width} (run ${tag})`);
  const { ctx, page } = await as(browser, "sb_staff", width);
  await go(page, "/flyers");
  const meta = (await api(page, "GET", "/api/flyers/meta")).body;
  const keys = (meta?.groups || []).map((g) => g.key);
  check(keys.length === marketing.length && !keys.includes("store") && !keys.includes("kitchen"),
    `only flyer groups are offered (${keys.join(",")}) @${width}`);
  check(meta?.whatsapp_connected === true, `WhatsApp shows connected (mock) @${width}`);

  // JPG, sent to two groups, the second one fails, then Retry
  const [g1, g2] = marketing;
  fs.writeFileSync(FAIL, g2.chat_id + "\n");
  const before = whapi().length;
  await upload(page, `${W}/fixtures/flyer.jpg`, `SB jpg ${tag}`, "Groceries");
  await page.locator("textarea").first().fill(`Suite B caption ${tag} *bold*`);
  await page.locator(`#fly-new-group-${g1.key}`).check();
  await page.locator(`#fly-new-group-${g2.key}`).check();
  await shot(page, `flyers-upload-${width}`, true);
  check((await hscroll(page)) <= 1, `upload card no h-scroll @${width}`);
  await page.getByRole("button", { name: "Send…" }).click();
  const modal = page.locator("[role=dialog]").last();
  await modal.waitFor();
  const mt = await modal.innerText();
  check(mt.includes(g1.label) && mt.includes(g2.label), `confirm lists both groups @${width}`);
  await shot(page, `flyers-confirm-${width}`);
  await modal.getByRole("button", { name: /^Send to 2 groups$/ }).click();
  await page.waitForFunction(() => { const d = [...document.querySelectorAll("[role=dialog]")].pop(); return d && /failed/.test(d.innerText) && !/Sending…/.test(d.innerText); }, null, { timeout: 90000 }).catch(() => {});
  const status = await modal.innerText();
  check(/1 sent, 1 failed/.test(status), `status shows 1 sent, 1 failed (${status.replace(/\n/g, " | ").slice(0, 200)}) @${width}`);
  await shot(page, `flyers-status-${width}`);
  // retry after the group recovers
  fs.writeFileSync(FAIL, "");
  const rb = modal.getByRole("button", { name: `Retry sending to ${g2.label}` });
  check(await rb.count() === 1, `Retry offered for the failed group @${width}`);
  if (await rb.count()) {
    await rb.click();
    await modal.getByText(/Sent to 2 groups/).waitFor({ timeout: 90000 }).catch(() => {});
    check(/Sent to 2 groups/.test(await modal.innerText()), `after Retry: sent to 2 groups @${width}`);
  }
  const calls = whapi().slice(before);
  const posts = calls.filter((c) => c.method === "POST" && c.path.startsWith("/messages/"));
  check(calls.some((c) => c.path === "/media"), `file uploaded to (mock) Whapi media once or more @${width}`);
  check(posts.every((c) => c.path === "/messages/image") && posts.some((c) => c.to === g1.chat_id) && posts.some((c) => c.to === g2.chat_id),
    `image messages went only to the two flyer groups @${width}`);
  check(posts.every((c) => (c.caption || "").includes(`Suite B caption ${tag}`)), `caption sent with the flyer @${width}`);
  check(!calls.some((c) => c.path.startsWith("/messages/text")), `no text messages sent @${width}`);
  const closeBtn = modal.getByRole("button", { name: /^(Done|Close)$/ });
  if (await closeBtn.count()) await closeBtn.first().click(); else await page.keyboard.press("Escape");
  await sleep(500);

  // HEIC -> saved as JPEG
  await go(page, "/flyers");
  await upload(page, `${W}/fixtures/flyer.heic`, `SB heic ${tag}`, "Flowers");
  await page.getByRole("button", { name: "Save to library" }).click();
  await page.locator(".fly-notice").waitFor({ timeout: 30000 }).catch(() => {});
  let list = (await api(page, "GET", `/api/flyers?q=${encodeURIComponent(`SB heic ${tag}`)}`)).body;
  const heic = list?.items?.[0];
  check(heic && heic.mime === "image/jpeg" && heic.kind === "image" && heic.category === "flowers", `HEIC stored as a JPEG in Flowers (${heic?.mime}) @${width}`);
  if (heic) check(fs.existsSync(`${W}/flyers/Flowers`) && fs.readdirSync(`${W}/flyers/Flowers`).some((n) => n.includes(`(${heic.id}).jpg`)), `file in the Flowers folder @${width}`);

  // PDF -> document send to one group
  await go(page, "/flyers");
  const before2 = whapi().length;
  await upload(page, `${W}/fixtures/flyer.pdf`, `SB pdf ${tag}`, "Others");
  await page.locator(`#fly-new-group-${g1.key}`).check();
  await page.getByRole("button", { name: "Send…" }).click();
  await page.locator("[role=dialog]").last().getByRole("button", { name: /^Send to 1 group$/ }).click();
  await page.getByText(/Sent to 1 group/).waitFor({ timeout: 90000 }).catch(() => {});
  let docPosts = [];
  for (let i = 0; i < 30 && !docPosts.length; i++) { await sleep(1000); docPosts = whapi().slice(before2).filter((c) => c.path.startsWith("/messages/")); }
  check(docPosts.length === 1 && docPosts[0].path === "/messages/document" && /\.pdf$/.test(docPosts[0].filename || ""), `PDF went as a document with a .pdf name (${docPosts[0]?.filename}) @${width}`);
  const close2 = page.locator("[role=dialog]").last().getByRole("button", { name: /^(Done|Close)$/ });
  if (await close2.count()) await close2.first().click();

  // library: search + drawer history
  await go(page, "/flyers");
  await page.getByLabel("Search flyers").fill(`SB jpg ${tag}`);
  await sleep(1500);
  const cardBtn = page.locator("button.fly-card", { hasText: `SB jpg ${tag}` });
  check(await cardBtn.count() === 1, `library search finds the flyer @${width}`);
  await shot(page, `flyers-library-${width}`);
  if (await cardBtn.count()) {
    await cardBtn.click();
    await settle(page);
    const drawer = page.locator("[role=dialog]").last();
    const dt = await drawer.innerText();
    check(dt.includes(g1.label) && dt.includes(g2.label) && /Sent/.test(dt), `drawer history lists both groups @${width}`);
    check(!/120363/.test(dt), `no chat ids shown in the drawer @${width}`);
    await shot(page, `flyers-drawer-${width}`);
    check((await hscroll(page)) <= 1, `drawer no h-scroll @${width}`);
    await page.getByRole("button", { name: "Close details" }).click();
  }
  // API never returns chat ids
  const full = JSON.stringify((await api(page, "GET", `/api/flyers?limit=30`)).body);
  check(!/120363/.test(full), `flyer API never returns chat ids @${width}`);
  await ctx.close();
}

// access
for (const role of ["demo", "sb_hide"]) {
  const { ctx, page } = await as(browser, role, 375);
  await go(page, "/flyers");
  check(!page.url().endsWith("/flyers"), `${role}: /flyers not reachable (landed ${page.url().replace(BASE, "")})`);
  const r = await api(page, "GET", "/api/flyers/meta");
  check(r.status === 403, `${role}: /api/flyers/meta refused (${r.status})`);
  await ctx.close();
}
await browser.close();
done();
