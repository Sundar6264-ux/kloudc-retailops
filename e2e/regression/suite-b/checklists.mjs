// Suite B - Daily checklists + Checklist builder: WhatsApp groups panel (available on WhatsApp via the MOCK, add, use
// for flyers, delete), new checklist, Settings WhatsApp group dropdown, publish, Public link tab, staff fill + history
// (summary posted to the mock group), the public /c/<slug>-<code> page with the Spice Town header, demo never posts.
// node checklists.mjs [width ...]
import { chromium, as, go, settle, hscroll, check, note, done, shot, api, mainText, sleep, USERS, W, BASE } from "./lib.mjs";
import fs from "node:fs";

const widths = process.argv.slice(2).map(Number).filter(Boolean);
const WIDTHS = widths.length ? widths : [1280, 375];
const RUN = Date.now().toString(36).slice(-5);
const LOG = `${W}/whapi_log.jsonl`;
const whapi = () => (fs.existsSync(LOG) ? fs.readFileSync(LOG, "utf8").trim().split("\n").filter(Boolean).map((l) => JSON.parse(l)) : []);
const ADM = "/api/checklists/admin";
const browser = await chromium.launch();

for (const width of WIDTHS) {
  const tag = `${RUN}-${width}`;
  console.log(`\n== Checklists @${width} (run ${tag})`);
  const { ctx, page } = await as(browser, "sb_admin", width);
  await go(page, "/checklists/admin");
  let txt = await mainText(page);
  check(/Store Closing/.test(txt) && /Kitchen Closing/.test(txt), `builder lists the checklists @${width}`);
  check((await hscroll(page)) <= 1, `builder list no h-scroll @${width}`);
  await shot(page, `ck-builder-${width}`);

  // ---- WhatsApp groups panel
  await page.getByRole("button", { name: "WhatsApp groups" }).click();
  const panel = page.locator("[role=dialog][aria-label='WhatsApp groups']");
  await panel.waitFor();
  await settle(page);
  let pt = await panel.innerText();
  check(/Spice Town staff group/.test(pt) && /Built-in/i.test(pt) && /Use for flyers/.test(pt), `panel lists groups with Built-in + Use for flyers @${width}`);
  check(/Suite B Customers Vernon/.test(pt), `Available on WhatsApp lists the (mock) groups @${width}`);
  check(!/120363\d{6,}/.test(pt), `no full chat ids in the panel @${width}`);
  await shot(page, `ck-groups-panel-${width}`);
  const label = `SB group ${tag}`;
  const avail = panel.locator("li", { hasText: "Suite B Customers Manchester" });
  if (await avail.getByRole("button", { name: "Add…" }).count()) {
    await avail.getByRole("button", { name: "Add…" }).click();
    await panel.getByLabel("Name shown in the checklist settings").fill(label);
    await panel.getByRole("button", { name: "Add", exact: true }).click();
    await panel.locator("li.wag-row", { hasText: label }).waitFor({ timeout: 10000 }).catch(() => {});
  } else {
    // added in an earlier width: rename it to this run's label
    const texts = await panel.locator("li.wag-row").allInnerTexts();
    const prev = panel.locator("li.wag-row").nth(texts.findIndex((t) => /SB group/.test(t)));
    await prev.getByRole("button", { name: "Rename" }).click();
    await prev.getByLabel("Group name").fill(label);
    await prev.getByRole("button", { name: "Save" }).click();
    await sleep(1200);
  }
  const row = panel.locator("li.wag-row", { hasText: label });
  check(await row.count() === 1, `group added / renamed in the panel @${width}`);
  const groups = (await api(page, "GET", `${ADM}/whatsapp-groups`)).body?.items || [];
  const mine = groups.find((g) => g.label === label);
  check(!!mine && mine.marketing === false && mine.builtin === false, `new group starts not used for flyers @${width}`);
  await row.getByText("Use for flyers").click();
  await sleep(1500);
  let fm = (await api(page, "GET", "/api/flyers/meta")).body;
  check((fm?.groups || []).some((g) => g.label === label), `"Use for flyers" puts it in the flyers' group list @${width}`);
  await row.getByText("Use for flyers").click();
  await sleep(1500);
  fm = (await api(page, "GET", "/api/flyers/meta")).body;
  check(!(fm?.groups || []).some((g) => g.label === label), `unticking takes it out of the flyers list @${width}`);
  const builtinRow = panel.locator("li.wag-row", { hasText: "Spice Town staff group" });
  check(await builtinRow.getByRole("button", { name: "Delete" }).count() === 0, `built-in groups can't be deleted @${width}`);
  await panel.getByRole("button", { name: "Close" }).first().click();

  // ---- new checklist through the modal
  await page.getByRole("button", { name: /New checklist/ }).click();
  const modal = page.locator("form[aria-label='New checklist']");
  await modal.locator("input").first().fill(`SB check ${tag}`);
  await modal.locator("button[type=submit]").click();
  await page.waitForURL(/#\/checklists\/admin\/.+/, { timeout: 15000 }).catch(() => {});
  const type = decodeURIComponent(page.url().split("/checklists/admin/")[1] || "");
  check(!!type, `new checklist opens its editor (${type}) @${width}`);
  // questions through the draft API (the editor's own widgets are covered by its node tests + screenshots)
  const det = (await api(page, "GET", `${ADM}/definitions/${encodeURIComponent(type)}`)).body;
  const sections = [{ id: "s1", title: "Checks", description: null, items: [
    { id: "q1", type: "multiple_choice", title: "Freezers below 0°F?", description: null, required: true,
      options: [{ value: "Yes", issue_tag: null }, { value: "No", issue_tag: "issue" }] },
    { id: "q2", type: "short_text", title: "Anything else?", description: null, required: false },
  ] }];
  const put = await api(page, "PUT", `${ADM}/definitions/${encodeURIComponent(type)}`, {
    title: `SB check ${tag}`, sections, base_draft_updated_at: det?.draft_updated_at ?? null });
  check(put.status === 200, `draft saved (${put.status} ${JSON.stringify(put.body).slice(0, 160)}) @${width}`);
  await go(page, `/checklists/admin/${encodeURIComponent(type)}`);
  await shot(page, `ck-editor-${width}`);
  check((await hscroll(page)) <= 1, `editor no h-scroll @${width}`);
  await page.getByRole("button", { name: "Publish" }).first().click();
  const pub = page.locator("[role=dialog]").last();
  await pub.getByRole("button", { name: "Publish" }).click();
  await sleep(2000);
  let d2 = (await api(page, "GET", `${ADM}/definitions/${encodeURIComponent(type)}`)).body;
  check(d2?.current_version >= 1 && !d2?.has_draft, `published (v${d2?.current_version}) @${width}`);

  // ---- Settings: WhatsApp group dropdown
  await page.getByRole("tab", { name: "Settings" }).click();
  const sel = page.locator("label.clb-setting", { hasText: "WhatsApp group" }).locator("select");
  const opts = await sel.locator("option").allInnerTexts();
  check(opts[0] === "None (no WhatsApp message)" && opts.includes("Spice Town staff group") && opts.includes(label), `group dropdown: None + every active group (${opts.length}) @${width}`);
  await sel.selectOption({ label });
  await page.getByRole("button", { name: "Save settings" }).click();
  await sleep(1500);
  d2 = (await api(page, "GET", `${ADM}/definitions/${encodeURIComponent(type)}`)).body;
  check(d2?.settings?.whatsapp_group === mine?.key, `settings saved with the new group (${d2?.settings?.whatsapp_group}) @${width}`);
  check(await page.getByRole("button", { name: "Manage WhatsApp groups" }).count() === 1, `Manage WhatsApp groups link under the dropdown @${width}`);
  await shot(page, `ck-settings-${width}`, true);
  // a group in use can't be deleted (409 names the checklist)
  const del = await api(page, "DELETE", `${ADM}/whatsapp-groups/${mine?.key}`);
  check(del.status === 409 && JSON.stringify(del.body).includes(`SB check ${tag}`), `in-use group delete -> 409 naming the checklist (${del.status}) @${width}`);

  // ---- Public link tab
  await page.getByRole("tab", { name: "Public link" }).click();
  await settle(page);
  const slugBox = page.getByLabel("Link word");
  await slugBox.fill(`sb${tag.replace(/[^a-z0-9]/g, "")}`.slice(0, 20));
  await page.getByRole("switch", { name: "Link on" }).check();
  await page.getByLabel("Type a name").check();
  await page.getByRole("button", { name: /^(Create link|Save link)$/ }).click();
  await page.locator("[data-testid=public-link-url]").waitFor({ timeout: 10000 }).catch(() => {});
  const url = (await page.locator("[data-testid=public-link-url]").innerText().catch(() => "")).trim();
  check(/\/c\/[a-z0-9-]+-\d{4}$/.test(url), `link is /c/<slug>-<4 digits> (${url}) @${width}`);
  await shot(page, `ck-public-tab-${width}`);
  const links = (await api(page, "GET", `${ADM}/public-links`)).body?.links || [];
  check(links.some((l) => l.checklist_type === type && l.enabled && l.short), `public-links list shows it enabled @${width}`);
  await ctx.close();

  // ---- staff fills it; summary goes to the (mock) group
  {
    const s = await as(browser, "sb_staff", width);
    const before = whapi().length;
    await go(s.page, "/daily-checklists");
    await shot(s.page, `ck-hub-staff-${width}`);
    await s.page.locator("button.checklist-tile", { hasText: `SB check ${tag}` }).click();
    await settle(s.page);
    await s.page.getByRole("button", { name: /^\W*No$/ }).click();
    await s.page.locator("input[type=text]").last().fill(`Suite B note ${tag}`);
    await s.page.getByRole("button", { name: "Submit" }).click();
    await s.page.getByText(/Submitted/).first().waitFor({ timeout: 15000 }).catch(() => {});
    check(/Submitted/.test(await mainText(s.page)), `staff submission confirmed @${width}`);
    await shot(s.page, `ck-submitted-${width}`);
    let posts = [];
    for (let i = 0; i < 15 && !posts.length; i++) { await sleep(1000); posts = whapi().slice(before).filter((c) => c.path === "/messages/text"); }
    check(posts.length === 1 && posts[0].to === USERS.groups.find((g) => g.key === mine?.key)?.chat_id || posts.length === 1 && /@g\.us$/.test(posts[0].to || ""),
      `one WhatsApp summary posted to the chosen group (${posts.length} ${posts[0]?.to}) @${width}`);
    check(posts.length === 1 && (posts[0].body || "").includes(`SB check ${tag}`), `summary names the checklist @${width}`);
    await go(s.page, "/daily-checklists");
    const hist = s.page.getByRole("button", { name: /History|See all|View history/ }).first();
    if (await hist.count()) { await hist.click(); await settle(s.page); }
    check((await mainText(s.page)).includes(`SB check ${tag}`), `submission in the history @${width}`);
    await shot(s.page, `ck-history-${width}`);
    check((await hscroll(s.page)) <= 1, `history no h-scroll @${width}`);
    await s.ctx.close();
  }

  // ---- public page, no login
  if (url) {
    const path = url.slice(url.indexOf("/c/"));
    const pctx = await browser.newContext({ viewport: { width, height: width < 600 ? 812 : 900 } });
    const p = await pctx.newPage();
    const resp = await p.goto(BASE + path);
    check(resp.status() === 200, `public page 200 (${resp.status()}) @${width}`);
    await p.locator(".clp-brand").waitFor({ timeout: 10000 }).catch(() => {});
    const brand = await p.locator(".clp-brand").innerText().catch(() => "");
    check(/Spice Town/.test(brand) && await p.locator(".clp-brand img").count() === 1, `public page has the Spice Town header + logo @${width}`);
    const logoOk = await p.locator(".clp-brand img").evaluate((i) => i.complete && i.naturalWidth > 0).catch(() => false);
    check(logoOk, `header logo loads @${width}`);
    check(/SB check/.test(await p.locator("body").innerText()), `public page shows the checklist @${width}`);
    await shot(p, `ck-public-page-${width}`, true);
    check((await hscroll(p)) <= 1, `public page no h-scroll @${width}`);
    const nameBox = p.getByPlaceholder("First and last name");
    if (await nameBox.count()) await nameBox.fill("Suite Bee");
    else await p.locator(".clp-name-option").first().click();
    await p.getByRole("button", { name: "Yes", exact: true }).click();
    await p.getByRole("button", { name: "Submit" }).click();
    await p.getByText("Submitted - thank you").waitFor({ timeout: 15000 }).catch(() => {});
    check(await p.getByText("Submitted - thank you").count() === 1, `public submission thanked @${width}`);
    await shot(p, `ck-public-done-${width}`);
    const bad = await p.goto(BASE + "/c/nope-0000");
    check(bad.status() === 404 && /This link is not active/.test(await p.locator("body").innerText()), `unknown link: 404 + "not active" (${bad.status()}) @${width}`);
    await shot(p, `ck-public-bad-${width}`);
    await pctx.close();
  }

  // ---- demo fills it: saved, never posted
  {
    const d = await as(browser, "demo", width);
    const before = whapi().length;
    await go(d.page, "/daily-checklists");
    const tile = d.page.locator("button.checklist-tile", { hasText: `SB check ${tag}` });
    if (await tile.count()) {
      await tile.click();
      await settle(d.page);
      await d.page.getByRole("button", { name: "Yes", exact: true }).click();
      await d.page.getByRole("button", { name: "Submit" }).click();
      await sleep(4000);
      check(/Submitted/.test(await mainText(d.page)), `demo submission saved @${width}`);
      check(!whapi().slice(before).some((c) => c.path.startsWith("/messages/")), `demo submission posts nothing to WhatsApp @${width}`);
    } else note(`demo doesn't see the new checklist tile @${width}`);
    await go(d.page, "/checklists/admin");
    check(!d.page.url().includes("/checklists/admin"), `demo: builder not reachable @${width}`);
    await d.ctx.close();
  }
}
// lead / hide: no builder
for (const role of ["sb_lead", "sb_hide"]) {
  const { ctx, page } = await as(browser, role, 1280);
  await go(page, "/checklists/admin");
  check(!page.url().includes("/checklists/admin"), `${role}: builder not reachable`);
  const r = await api(page, "GET", `${ADM}/whatsapp-groups`);
  check(r.status === 403, `${role}: groups API refused (${r.status})`);
  await ctx.close();
}
await browser.close();
done();
