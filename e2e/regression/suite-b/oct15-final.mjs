// Suite B - Oct 15 final regression pass (website): the new menu groups + old URLs, the Notifications page,
// two people's Tasks pages staying in sync through the UI (no reloads), the Insights coverage note and Fix Issues.
// Drawer edit / reassign / steps / bulk / filters / "My team today" in depth: tasks-oct15.mjs.
// Throwaway server only (setup.sh + serve.sh). node oct15-final.mjs [width ...]
import { chromium, as, go, settle, hscroll, check, done, shot, api, mainText, sleep, USERS, ignorable } from "./lib.mjs";

const widths = process.argv.slice(2).map(Number).filter(Boolean);
const WIDTHS = widths.length ? widths : [1280, 375];
const RUN = Date.now().toString(36).slice(-5);
const browser = await chromium.launch();
const hoursFromNow = (h) => new Date(Date.now() + h * 3600e3).toISOString();

async function waitFor(fn, ms = 8000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    if (await fn()) return Date.now() - t0;
    await sleep(150);
  }
  return -1;
}
async function openMenu(page, width) {
  if (width < 769) {
    const t = page.locator(".sidebar-toggle");
    if (await t.count()) await t.first().click();
    await sleep(300);
  }
}
const sidebarText = (page) => page.evaluate(() => document.querySelector("nav.sidebar")?.innerText || "");

for (const width of WIDTHS) {
  const tag = `${RUN}-${width}`;
  console.log(`\n== Oct 15 final @${width} (run ${tag})`);

  // ---------- 1. menu groups + old URLs ----------
  const admin = await as(browser, "sb_admin", width);
  const ap = admin.page;
  await go(ap, "/");
  await openMenu(ap, width);
  const groups = await ap.locator("nav.sidebar .sidebar-group-toggle:not(.sidebar-subgroup-toggle)").allInnerTexts();
  const g = groups.map((s) => s.replace(/\s+\d+$/, "").trim());
  for (const want of ["Team", "Ordering", "Stock", "Sales & Insights", "Setup"]) {
    check(g.some((x) => x.startsWith(want)), `admin menu has group "${want}" @${width} (${g.join(", ")})`);
  }
  for (const gone of ["Marketing", "Reports", "Inventory & stock"]) {
    check(!g.some((x) => x.startsWith(gone)), `admin menu has no old group "${gone}" @${width}`);
  }
  const st = await sidebarText(ap);
  for (const link of ["Overview", "Invoices", "Daily Checklists", "Notifications", "Change password"]) {
    check(st.includes(link), `admin menu shows "${link}" @${width}`);
  }
  await shot(ap, `o15-menu-admin-${width}`);
  if (width < 769 && (await ap.locator(".sidebar-backdrop").count())) await ap.mouse.click(width - 8, 400); // close the phone menu
  check(!(await mainText(ap)).includes("Quick actions"), `Overview: no Quick actions @${width}`);
  check((await mainText(ap)).includes("Weekly Digest"), `Overview: Weekly Digest kept @${width}`);

  const redirects = [
    ["/transfer-review", /#\/delivery-review/, "Delivery Review"],
    ["/glossary", /#\/(help|glossary)/, "Glossary"],
    ["/insights/menu", /#\/insights\//, null],
  ];
  for (const [from, to, txt] of redirects) {
    await go(ap, from);
    const url = ap.url();
    check(to.test(url), `${from} lands on ${url.replace(/.*#/, "#")} @${width}`);
    if (txt) check((await mainText(ap)).includes(txt), `${from} page shows "${txt}" @${width}`);
  }

  // staff + demo menus
  for (const who of ["sb_staff", "demo"]) {
    const s = await as(browser, who, width);
    await go(s.page, "/");
    await openMenu(s.page, width);
    const t = await sidebarText(s.page);
    check(!t.includes("Setup") && !t.includes("Users"), `${who}: no Setup in the menu @${width}`);
    check(t.includes("Notifications"), `${who}: Notifications in the footer @${width}`);
    if (who === "demo") check(!/Flyers|Vendor contacts|PO sections/.test(t), `demo: no Flyers / Vendor contacts / PO sections @${width}`);
    check(s.page.errors.length === 0, `${who}: no console errors on Overview @${width} ${s.page.errors.slice(0, 2).join(" | ")}`);
    await s.ctx.close();
  }

  // ---------- 2. Notifications page ----------
  await go(ap, "/notifications");
  let nt = await mainText(ap);
  for (const h of ["Needs action", "Team", "Daily summary", "Admin"]) check(nt.includes(h), `Notifications: "${h}" group @${width}`);
  const ms = ap.getByRole("switch", { name: "Morning summary" });
  check((await ms.count()) === 1 && (await ms.getAttribute("aria-checked")) === "true", `admin: Morning summary on by default @${width}`);
  const before = await api(ap, "GET", "/api/notifications/prefs");
  const wasExplicit = (before.body?.items || []).some((i) => i.explicit);
  await ms.click();
  const flipped = await waitFor(async () => (await ms.getAttribute("aria-checked")) === "false");
  check(flipped >= 0, `Morning summary switched off @${width}`);
  await waitFor(async () => (await mainText(ap)).includes("Recommended: on"));
  check((await mainText(ap)).includes("Recommended: on"), `"Recommended: on" hint after switching off @${width}`);
  const after = await api(ap, "GET", "/api/notifications/prefs");
  check((after.body?.items || []).find((i) => i.key === "morning_summary")?.enabled === false, `server saved Morning summary off @${width}`);
  const reset = ap.getByRole("button", { name: "Reset to recommended" });
  check((await reset.count()) === 1, `Reset to recommended offered once a choice is made @${width}`);
  if (await reset.count()) {
    await reset.click();
    await waitFor(async () => (await ms.getAttribute("aria-checked")) === "true");
    check((await ms.getAttribute("aria-checked")) === "true", `Reset puts Morning summary back on @${width}`);
    if (!wasExplicit) check((await reset.count()) === 0, `Reset button gone after reset @${width}`);
  }
  await ap.locator("summary", { hasText: "Store defaults (admins)" }).click();
  await waitFor(async () => (await mainText(ap)).includes("Staff"));
  check(/Staff/.test(await mainText(ap)) && /Admins/.test(await mainText(ap)), `Store defaults shows Staff / Admins @${width}`);
  await shot(ap, `o15-notifications-${width}`, true);
  check((await hscroll(ap)) <= 1, `Notifications: no sideways scroll @${width}`);
  const demo = await as(browser, "demo", width);
  await go(demo.page, "/notifications");
  const dt = await mainText(demo.page);
  check(!dt.includes("Reset to recommended") && !dt.includes("Store defaults"), `demo: no Reset / Store defaults @${width}`);
  check(demo.page.bad.length === 0, `demo: no refused calls on Notifications @${width} ${demo.page.bad.slice(0, 2).join(" | ")}`);
  await demo.ctx.close();

  // ---------- 3. two people in sync through the UI ----------
  const staff = await as(browser, "sb_staff", width);
  const sp = staff.page;
  const made = await api(ap, "POST", "/api/team/tasks", {
    title: `O15 sync ${tag}`, assignee_user_ids: [USERS.sb_staff.id], due_at: hoursFromNow(5), items: ["Step one"],
  });
  check(made.status === 200, `admin made a task for staff (${made.status})`);
  await go(ap, "/team/tasks");
  await ap.getByRole("tab", { name: "List" }).click();
  await ap.getByRole("tab", { name: "All" }).click();
  await settle(ap, 20000);
  await go(sp, "/team/tasks");
  await settle(sp, 20000);
  const staffSees = await waitFor(async () => (await mainText(sp)).includes(`O15 sync ${tag}`));
  check(staffSees >= 0, `staff's Tasks page shows the new task (${staffSees} ms) @${width}`);
  // admin opens it; staff finishes it from the UI; the admin's open drawer follows
  await ap.getByRole("button", { name: `O15 sync ${tag}` }).click();
  const adrawer = ap.locator(".task-drawer");
  await adrawer.waitFor({ timeout: 8000 });
  await sp.getByRole("button", { name: `O15 sync ${tag}` }).click();
  const sdrawer = sp.locator(".task-drawer");
  await sdrawer.waitFor({ timeout: 8000 });
  // tick the step in the staff UI, then Mark done
  const step = sdrawer.getByRole("checkbox").first();
  if (await step.count()) {
    await step.click();
    const ticked = await waitFor(async () => /Step one/.test(await adrawer.innerText()) && (await adrawer.getByRole("checkbox").first().isChecked().catch(() => false)));
    check(ticked >= 0, `admin's open drawer shows staff's step tick (${ticked} ms) @${width}`);
  }
  const t0 = Date.now();
  await sdrawer.getByRole("button", { name: "Mark done" }).first().click();
  let seen = -1;
  while (Date.now() - t0 < 8000) {
    if ((await adrawer.innerText()).includes("Done by")) { seen = Date.now() - t0; break; }
    await sleep(150);
  }
  check(seen >= 0 && seen < 5000, `admin's drawer shows "Done by" after staff's UI click (${seen} ms) @${width}`);
  await shot(ap, `o15-sync-admin-${width}`);
  await shot(sp, `o15-sync-staff-${width}`);
  // admin reopens from the UI; staff's page follows
  const reopen = adrawer.getByRole("button", { name: /Reopen|Undo/ });
  if (await reopen.count()) {
    await reopen.first().click();
    const back = await waitFor(async () => !(await sdrawer.innerText().catch(() => "")).includes("Done by"));
    check(back >= 0, `staff's drawer follows the admin's Reopen (${back} ms) @${width}`);
  }
  // Overview's live Items Sold asks Toast (blank on purpose): its 503 is expected.
  const apErr = ap.errors.filter((e) => !/status of 503/.test(e) || !ap.bad.every((b) => ignorable(b)));
  check(apErr.length === 0, `admin: no console errors in the sync flow @${width} ${[...apErr, ...ap.bad].slice(0, 4).join(" | ")}`);
  check(sp.errors.length === 0 && sp.bad.length === 0, `staff: no errors in the sync flow @${width} ${[...sp.errors, ...sp.bad].slice(0, 2).join(" | ")}`);
  await staff.ctx.close();

  // ---------- 4. Insights coverage note ----------
  await go(ap, "/insights/loss-watch");
  await settle(ap, 60000);
  const it = await mainText(ap);
  check(!/filled in the background/i.test(it), `Loss watch: no old "filled in the background" wording @${width}`);
  check(!/Some days are still coming in/i.test(it) || /isn't in yet|are added automatically|arrives/i.test(it), `Loss watch: any coverage note is the plain-words one @${width}`);
  const ins = await api(ap, "GET", "/api/insights/loss-watch?days=7");
  if (ins.status === 200) {
    const b = ins.body || {};
    check(!b.stale ? b.note == null : typeof b.note === "string", `API: note only when stale (stale=${b.stale}) @${width}`);
    check(b.coverage == null || Array.isArray(b.coverage.missing_dates ?? []), `API: coverage.missing_dates is a list @${width}`);
  } else {
    check(false, `GET /api/insights/loss-watch -> ${ins.status}`);
  }
  await shot(ap, `o15-insights-${width}`);
  check((await hscroll(ap)) <= 1, `Loss watch: no sideways scroll @${width}`);

  // ---------- 5. Fix Issues ----------
  ap.bad.length = 0;
  await go(ap, "/fix-issues");
  await settle(ap, 60000);
  const ft = await mainText(ap);
  check(/Fix Issues/.test(ft), `Fix Issues page loads @${width}`);
  check((await hscroll(ap)) <= 1, `Fix Issues: no sideways scroll @${width}`);
  check(ap.bad.filter((b) => !/items-sold|open-orders|live/.test(b)).length === 0, `Fix Issues: no failed API calls @${width} ${ap.bad.slice(0, 2).join(" | ")}`);
  await shot(ap, `o15-fixissues-${width}`);
  await admin.ctx.close();
}

await browser.close();
done();
