// Suite B - Team chat: text + @mention, photo, PDF, voice (recorded in the browser when it can make audio/mp4,
// else uploaded through the API and played), pins, seen-by, message search, report -> moderation, DM block,
// word filter, announcements with acknowledge, demo scoping. node chat.mjs [width ...]
import { chromium, as, go, settle, hscroll, check, note, done, shot, api, mainText, sleep, USERS, W, BASE } from "./lib.mjs";
import fs from "node:fs";

const widths = process.argv.slice(2).map(Number).filter(Boolean);
const WIDTHS = widths.length ? widths : [1280, 375];
const RUN = Date.now().toString(36).slice(-5);
const browser = await chromium.launch({ args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"] });

async function openKitchen(page) {
  await go(page, "/team/chat");
  await page.locator(".chat-sidebar .chat-channel", { hasText: "# Kitchen" }).first().click();
  await page.locator(".chat-conv-title", { hasText: "Kitchen" }).waitFor();
  await settle(page);
}
const bubble = (page, text) => page.locator(".chat-row", { hasText: text }).last();
async function menu(page, text, item) {
  const row = bubble(page, text);
  await row.hover();
  await row.getByRole("button", { name: "Message actions" }).click();
  const it = row.getByRole("menuitem", { name: item });
  if (!(await it.count())) { await page.keyboard.press("Escape"); return false; }
  await it.click();
  return true;
}

for (const width of WIDTHS) {
  const tag = `${RUN}-${width}`;
  console.log(`\n== Chat @${width} (run ${tag})`);
  const staff = await as(browser, "sb_staff", width, { permissions: ["microphone"] });
  const admin = await as(browser, "sb_admin", width);
  const sp = staff.page;
  const ap = admin.page;
  // team rules acceptance (the app gates on it; the website doesn't) - accept for both through the API
  await sp.goto(BASE + "/#/"); await ap.goto(BASE + "/#/");
  for (const p of [sp, ap]) await api(p, "POST", "/api/team/rules/accept", { version: 1 });

  await openKitchen(sp);
  const input = sp.getByLabel("Message", { exact: true });
  // text + @mention picked from the list
  await input.fill(`SB hello ${tag} @sb_ad`);
  const opt = sp.locator(".chat-mentions [role=option]", { hasText: "@sb_admin" });
  await opt.waitFor({ timeout: 5000 }).catch(() => {});
  check(await opt.count() > 0, `@mention list offers sb_admin @${width}`);
  if (await opt.count()) await opt.first().click();
  await input.press("Enter");
  await bubble(sp, `SB hello ${tag}`).waitFor({ timeout: 10000 });
  const mtxt = await bubble(sp, `SB hello ${tag}`).innerText();
  check(/@sb_admin/.test(mtxt), `sent message keeps the @mention @${width}`);
  // word filter
  await input.fill(`SB filter ${tag} what the sh1t`);
  await input.press("Enter");
  await bubble(sp, `SB filter ${tag}`).waitFor({ timeout: 10000 });
  // the optimistic bubble shows what was typed until the server's (filtered) copy replaces it
  await sp.waitForFunction((t) => [...document.querySelectorAll(".chat-row")].some((r) => r.innerText.includes(t) && !/Sending/.test(r.innerText)), `SB filter ${tag}`, { timeout: 10000 }).catch(() => {});
  const ftxt = await bubble(sp, `SB filter ${tag}`).innerText();
  check(!/sh1t/.test(ftxt) && /s\*{3}/.test(ftxt), `word filter masks chat text @${width}`);
  // photo
  await sp.locator('input[type=file][accept="image/*"]').setInputFiles(`${W}/fixtures/photo.jpg`);
  await sp.locator(".chat-photo-preview img").waitFor();
  await input.fill(`SB photo ${tag}`);
  await sp.getByRole("button", { name: "Send", exact: true }).click();
  await bubble(sp, `SB photo ${tag}`).locator("img").waitFor({ timeout: 15000 }).catch(() => {});
  check(await bubble(sp, `SB photo ${tag}`).locator("img").count() > 0, `photo message shows the photo @${width}`);
  // PDF
  const pdfName = `suite-b-${tag}.pdf`;
  fs.copyFileSync(`${W}/fixtures/doc.pdf`, `${W}/fixtures/${pdfName}`);
  await sp.locator('input[type=file][accept="application/pdf,.pdf"]').setInputFiles(`${W}/fixtures/${pdfName}`);
  await sp.locator(".chat-doc-preview").waitFor();
  await sp.getByRole("button", { name: "Send", exact: true }).click();
  await sleep(2500);
  const docRow = sp.locator(".chat-row", { hasText: pdfName }).last();
  check(await docRow.count() > 0, `PDF message shows its name @${width}`);
  if (await docRow.count()) {
    const popup = sp.waitForEvent("popup", { timeout: 8000 }).catch(() => null);
    const openBtn = docRow.getByRole("button", { name: /Open/ }).or(docRow.getByRole("link", { name: /Open/ }));
    if (await openBtn.count()) {
      await openBtn.first().click();
      const pp = await popup;
      check(!!pp, `PDF Open opens a new tab @${width}`);
      if (pp) await pp.close();
    } else note(`no Open control on the PDF chip @${width}: ${(await docRow.innerText()).replace(/\n/g, " | ")}`);
  }
  // voice: record in the browser if supported, else via API
  const mic = sp.getByRole("button", { name: "Record a voice note" });
  if (await mic.count()) {
    await mic.click();
    await sp.locator(".chat-recording").waitFor({ timeout: 8000 }).catch(() => {});
    await sleep(2200);
    if (await sp.locator(".chat-recording").count()) {
      await sp.getByRole("button", { name: "Send voice note" }).click();
      await sleep(3000);
      const voiceRows = sp.locator(".chat-row .chat-voice, .chat-row audio, .chat-row [class*=voice]");
      check(await voiceRows.count() > 0, `recorded voice note shows in the chat @${width}`);
    } else {
      const err = await sp.locator(".chat-composer .error").allInnerTexts();
      note(`mic button shown but recording didn't start @${width}: ${err.join(" ")}`);
    }
  } else {
    const hint = await sp.locator(".chat-hint").innerText();
    check(/record them in the app/.test(hint), `no mic here, and the hint says to record in the app @${width}`);
  }
  // voice uploaded like the app does (AAC in MP4), then played on the website
  const up = await sp.evaluate(async (b64) => {
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const fd = new FormData();
    fd.append("file", new Blob([bytes], { type: "audio/mp4" }), "voice.m4a");
    fd.append("duration_ms", "2100");
    const r = await fetch("/api/team/media", { method: "POST", body: fd, credentials: "include" });
    return { status: r.status, body: await r.json().catch(() => null) };
  }, fs.readFileSync(`${W}/fixtures/voice.m4a`).toString("base64"));
  check(up.status === 200 || up.status === 201, `voice upload accepted (${up.status} ${JSON.stringify(up.body).slice(0, 120)}) @${width}`);
  if (up.body?.token) {
    const kid = USERS.kitchen;
    const chans = await api(sp, "GET", "/api/team/channels");
    const kch = (chans.body?.items || chans.body?.channels || chans.body || []).find?.((c) => c.kind === "team" && c.name === "Kitchen");
    check(up.body.kind === "audio" && up.body.content_type === "audio/mp4", `voice upload is kind audio, audio/mp4 @${width}`);
    const sent = await api(sp, "POST", `/api/team/channels/${kch?.id}/messages`, { body: "", media_token: up.body.token, client_uuid: `sbv-${tag}` });
    check(sent.status === 200 || sent.status === 201, `voice message posted (${sent.status} ${JSON.stringify(sent.body).slice(0, 160)}) team ${kid} @${width}`);
    await openKitchen(sp);
    const audio = sp.locator(".chat-row").filter({ has: sp.locator("audio, .chat-voice, button[aria-label*=Play], button[aria-label*=play]") });
    check(await audio.count() > 0, `voice message shows a player on the website @${width}`);
  }
  await shot(sp, `chat-staff-${width}`);
  const tw = await input.evaluate((e) => e.getBoundingClientRect().width);
  check(tw >= (width <= 480 ? 200 : 150), `message box is usable (${Math.round(tw)}px wide) @${width}`);
  check((await hscroll(sp)) <= 1, `chat no h-scroll @${width}`);

  // seen-by: admin posts, staff reads, admin checks Seen by…
  await openKitchen(ap);
  const ain = ap.getByLabel("Message", { exact: true });
  await ain.fill(`SB admin note ${tag}`);
  await ain.press("Enter");
  await bubble(ap, `SB admin note ${tag}`).waitFor({ timeout: 10000 });
  await openKitchen(sp);
  await sleep(1500);
  await openKitchen(ap);
  check(await menu(ap, `SB admin note ${tag}`, "Seen by…"), `own message has Seen by… @${width}`);
  await sleep(1500);
  const seen = await ap.locator("[role=dialog]").last().innerText().catch(() => "");
  check(/sb_staff/.test(seen), `Seen by lists sb_staff (${seen.replace(/\n/g, " | ").slice(0, 120)}) @${width}`);
  await shot(ap, `chat-seenby-${width}`);
  await ap.keyboard.press("Escape");
  const closeBtn = ap.locator("[role=dialog]").last().getByRole("button", { name: /Close|Done|OK/ });
  if (await closeBtn.count()) await closeBtn.first().click();

  // pins: admin pins staff's message; staff has no Pin in a team chat
  check(await menu(ap, `SB hello ${tag}`, "Pin"), `admin can pin in a team chat @${width}`);
  await sleep(1500);
  check(await ap.locator(".chat-pins").count() > 0, `pinned bar shows @${width}`);
  await shot(ap, `chat-pinned-${width}`);
  await openKitchen(sp);
  check(!(await menu(sp, `SB admin note ${tag}`, "Pin")), `staff can't pin in a team chat @${width}`);
  check(await sp.locator(".chat-pins").count() > 0 && /SB hello/.test(await sp.locator(".chat-pins").innerText()), `staff sees the pin @${width}`);

  // report: staff reports the admin's message -> moderation
  check(await menu(sp, `SB admin note ${tag}`, "Report"), `Report in the menu of someone else's message @${width}`);
  const dlg = sp.locator("form.chat-dialog");
  await dlg.locator("textarea, input[type=text]").first().fill("Suite B report");
  await dlg.getByRole("button", { name: "Report" }).click();
  await sp.getByText("Thanks - a manager will review it.").waitFor({ timeout: 10000 }).catch(() => {});
  check(await sp.getByText("Thanks - a manager will review it.").count() > 0, `report confirmed @${width}`);
  check(!(await menu(sp, `SB hello ${tag}`, "Report")), `no Report on my own message @${width}`);
  await go(ap, "/team/moderation");
  check((await mainText(ap)).includes(`SB admin note ${tag}`), `reported message in Moderation @${width}`);
  await shot(ap, `chat-moderation-${width}`);
  // resolve it with "Delete the message": everyone then sees "Removed by a manager"
  const repCard = ap.locator("li, article, .card, div.mod-report, section").filter({ hasText: `SB admin note ${tag}` }).filter({ has: ap.getByRole("button", { name: "Resolve" }) }).last();
  if (await repCard.count()) {
    await repCard.getByRole("button", { name: "Resolve" }).click();
    await sleep(1500);
    await openKitchen(sp);
    const ct = await sp.locator(".chat-conversation").innerText();
    check(!ct.includes(`SB admin note ${tag}`) && /Removed by a manager|Message deleted/.test(ct), `resolved with delete: the message is gone for staff @${width}`);
  } else note(`couldn't find the report card to resolve @${width}`);

  // search: all chats, opens + flashes the hit
  await go(sp, "/team/chat");
  await sp.getByRole("button", { name: "Search messages" }).click();
  await sp.getByLabel("Search messages", { exact: true }).last().fill(`hello ${tag}`);
  await sp.locator(".chat-search-hit").first().waitFor({ timeout: 10000 }).catch(() => {});
  check(await sp.locator(".chat-search-hit").count() === 1, `search finds exactly the message @${width}`);
  await shot(sp, `chat-search-${width}`);
  if (await sp.locator(".chat-search-hit").count()) {
    await sp.locator(".chat-search-hit").first().click();
    await sleep(1200);
    check(await sp.locator(".chat-bubble-flash").count() > 0 || (await bubble(sp, `SB hello ${tag}`).isVisible()), `search hit opens the chat at the message @${width}`);
  }
  const s1 = await api(sp, "GET", "/api/team/messages/search?q=x");
  check(s1.status === 400 || s1.status === 422 || (s1.body?.items || []).length === 0, `1-character search refused or empty (${s1.status}) @${width}`);
  await staff.ctx.close();
  await admin.ctx.close();
}

// DM block (once): sb_chat messages sb_staff, sb_staff blocks, sb_chat can't send
{
  const a = await as(browser, "sb_chat", 1280);
  const b = await as(browser, "sb_staff", 375);
  await a.page.goto(BASE + "/#/");
  await api(a.page, "POST", "/api/team/rules/accept", { version: 1 });
  const dm = await api(a.page, "POST", "/api/team/channels/dm", { user_id: USERS.sb_staff.id });
  check(dm.status === 200, `sb_chat opens a DM with sb_staff (${dm.status})`);
  const chId = dm.body?.id ?? dm.body?.channel?.id;
  const m1 = await api(a.page, "POST", `/api/team/channels/${chId}/messages`, { body: `SB dm ${RUN}`, client_uuid: `sbdm-${RUN}` });
  check(m1.status === 200 || m1.status === 201, `DM sent (${m1.status})`);
  await go(b.page, `/team/chat?c=${chId}`);
  await b.page.locator(".chat-row", { hasText: `SB dm ${RUN}` }).waitFor({ timeout: 10000 }).catch(() => {});
  check(await b.page.locator(".chat-row", { hasText: `SB dm ${RUN}` }).count() > 0, "DM arrives (375)");
  await b.page.getByRole("button", { name: "Block", exact: true }).click();
  await sleep(1500);
  check(await b.page.getByText(/You blocked sb_chat/).count() > 0, "blocked: composer replaced by 'You blocked' (375)");
  await shot(b.page, "chat-blocked-375");
  const m2 = await api(a.page, "POST", `/api/team/channels/${chId}/messages`, { body: `SB dm after block ${RUN}`, client_uuid: `sbdm2-${RUN}` });
  check(m2.status >= 400, `blocked person can't send (${m2.status})`);
  await b.page.getByRole("button", { name: "Unblock" }).first().click();
  await sleep(1000);
  await a.ctx.close();
  await b.ctx.close();
}

// announcements with acknowledge (admin -> Kitchen; staff acks; admin sees it)
{
  const a = await as(browser, "sb_admin", 1280);
  const s = await as(browser, "sb_staff", 375);
  await a.page.goto(BASE + "/#/");
  const an = await api(a.page, "POST", "/api/team/announcements", {
    title: `SB announcement ${RUN}`, body: "Please read and acknowledge.", team_ids: [USERS.kitchen], require_ack: true, client_uuid: `sban-${RUN}`,
  });
  check(an.status === 200 || an.status === 201, `announcement posted (${an.status} ${JSON.stringify(an.body).slice(0, 160)})`);
  const anId = an.body?.id ?? an.body?.announcement?.id;
  await go(s.page, "/team/announcements");
  const card = s.page.locator("article, .card, li", { hasText: `SB announcement ${RUN}` }).last();
  check(await card.count() > 0, "staff sees the announcement (375)");
  // Sentence pills keep their case (CSS .pill capitalizes one-word statuses only).
  const waitPill = s.page.locator(".ann-pill", { hasText: /waiting/i }).first();
  if (await waitPill.count()) {
    const t = (await waitPill.innerText()).trim();
    check(/^\d+ waiting for you to confirm$/.test(t), `waiting pill reads as a sentence: "${t}"`);
  } else check(false, "a '<n> waiting for you to confirm' pill before acknowledging");
  const ack = card.getByRole("button", { name: /Acknowledge|Got it|I've read/i });
  if (await ack.count()) {
    await ack.first().click();
    await sleep(1500);
    check(true, "staff acknowledged (375)");
    const youPill = card.locator(".ann-pill", { hasText: /confirmed/i }).first();
    const yt = (await youPill.count()) ? (await youPill.innerText()).trim() : "";
    check(/^You confirmed · /.test(yt), `"You confirmed" pill keeps sentence case: "${yt}"`);
  } else check(false, `an Acknowledge button on the announcement: ${(await card.innerText().catch(() => "")).slice(0, 200)}`);
  await shot(s.page, "ann-staff-375", true);
  check((await hscroll(s.page)) <= 1, "announcements no h-scroll (375)");
  if (anId) {
    const rec = await api(a.page, "GET", `/api/team/announcements/${anId}/recipients`);
    const row = JSON.stringify(rec.body || {});
    check(rec.status === 200 && /sb_staff/.test(row), `recipients list has sb_staff (${rec.status})`);
    const me = (rec.body?.recipients || []).find((r) => r.name === "sb_staff");
    check(!!me?.acked_at && rec.body?.acked >= 1, `sb_staff shows acknowledged (${rec.body?.summary})`);
  }
  await go(a.page, "/team/announcements");
  await shot(a.page, "ann-admin-1280");
  await a.ctx.close();
  await s.ctx.close();
}

// demo scoping: no one outside its teams in people / DMs; can't post to Kitchen
{
  const d = await as(browser, "demo", 375);
  await d.page.goto(BASE + "/#/");
  const ppl = await api(d.page, "GET", "/api/team/people");
  const names = JSON.stringify(ppl.body || {});
  check(!/sb_staff|sb_admin/.test(names), "demo: people list leaves out users outside its teams");
  const dm = await api(d.page, "POST", "/api/team/channels/dm", { user_id: USERS.sb_staff.id });
  check(dm.status === 403 || dm.status === 404, `demo: DM to someone outside its teams refused (${dm.status})`);
  await go(d.page, "/team/chat");
  await shot(d.page, "chat-demo-375");
  note(`demo chat list: ${(await d.page.locator(".chat-sidebar").innerText()).replace(/\n/g, " | ").slice(0, 200)}`);
  await d.ctx.close();
}
await browser.close();
done();
