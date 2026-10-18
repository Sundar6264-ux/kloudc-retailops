// Suite B - Team tasks, Oct 15 (website): the shared live feed (long-poll), the drawer staying live, Edit /
// Reassign / steps, a 409 showing the current task, filters, admin bulk Done, "My team today", empty-team
// warnings, lead tasks "N left of M" with the main button opening Fix Issues, and the demo / staff limits.
// Throwaway server only (setup.sh + serve.sh). node tasks-oct15.mjs [width ...]
import { chromium, as, go, settle, hscroll, check, done, shot, api, mainText, sleep, USERS } from "./lib.mjs";

const widths = process.argv.slice(2).map(Number).filter(Boolean);
const WIDTHS = widths.length ? widths : [1280, 375];
const RUN = Date.now().toString(36).slice(-5);
const hoursFromNow = (h) => new Date(Date.now() + h * 3600e3).toISOString();
const browser = await chromium.launch();
const staffId = USERS.sb_staff.id;
const kitchen = USERS.kitchen;

async function waitText(page, text, ms = 6000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    if ((await mainText(page)).includes(text)) return Date.now() - t0;
    await sleep(150);
  }
  return -1;
}

for (const width of WIDTHS) {
  const tag = `${RUN}-${width}`;
  console.log(`\n== Tasks Oct 15 @${width} (run ${tag})`);
  const admin = await as(browser, "sb_admin", width);
  const staff = await as(browser, "sb_staff", width);
  const ap = admin.page;
  const sp = staff.page;
  const longPolls = [];
  ap.on("request", (r) => r.url().includes("/api/team/updates") && longPolls.push(r.url()));
  await go(ap, "/team/tasks");
  await ap.getByRole("tab", { name: "List" }).click();
  await ap.getByRole("tab", { name: "All" }).click();
  await settle(ap, 20000);

  // --- filters + My team today are on the existing page
  check(await ap.getByLabel("Search task titles").count() === 1, `search box @${width}`);
  check(await ap.getByLabel("Kind of task").count() === 1, `kind filter @${width}`);
  check((await mainText(ap)).includes("My team today:"), `"My team today" strip for the admin @${width}`);
  check(longPolls.some((u) => u.includes("wait=20") && u.includes("include=tasks")), `Tasks page long-polls the feed (wait=20, include=tasks) @${width}`);

  // --- live: a task staff makes shows up on the admin's open page without a reload
  await go(sp, "/team/tasks");
  const live = await api(sp, "POST", "/api/team/tasks", { title: `OCT15 live ${tag}`, due_at: hoursFromNow(4) });
  check(live.status === 200, `staff created a personal task (${live.status})`);
  const liveId = live.body?.tasks?.[0]?.id;
  const seenIn = await waitText(ap, `OCT15 live ${tag}`);
  check(seenIn >= 0 && seenIn < 4000, `admin's list shows it without a reload (${seenIn} ms) @${width}`);

  // --- drawer: Edit title + add a step, history says so
  const mine = await api(ap, "POST", "/api/team/tasks", {
    title: `OCT15 edit ${tag}`, assignee_user_ids: [staffId], due_at: hoursFromNow(6), items: ["First step"],
  });
  const editId = mine.body?.tasks?.[0]?.id;
  await waitText(ap, `OCT15 edit ${tag}`);
  await ap.getByRole("button", { name: `OCT15 edit ${tag}` }).click();
  const drawer = ap.locator(".task-drawer");
  await drawer.getByRole("button", { name: "Edit" }).click();
  await drawer.locator(".task-edit-form input[type=text]").first().fill(`OCT15 edited ${tag}`);
  await drawer.getByRole("button", { name: "+ Add step" }).click();
  await drawer.getByRole("textbox", { name: "Step 2", exact: true }).fill("Second step");
  await shot(ap, `tasks15-edit-${width}`);
  await drawer.getByRole("button", { name: "Save" }).click();
  await drawer.getByText("Saved.").waitFor({ timeout: 8000 });
  let dtext = await drawer.innerText();
  check(dtext.includes(`OCT15 edited ${tag}`) && dtext.includes("Second step"), `drawer shows the saved title and new step @${width}`);
  check(/edited the steps \(1 added\)/.test(dtext), `history: "edited the steps (1 added)" @${width}`);

  // --- live drawer: staff finishes it while the admin has it open
  const t0 = Date.now();
  await api(sp, "POST", `/api/team/tasks/${editId}/complete`);
  let doneSeen = -1;
  while (Date.now() - t0 < 5000) {
    if ((await drawer.innerText()).includes("Done by")) { doneSeen = Date.now() - t0; break; }
    await sleep(150);
  }
  check(doneSeen >= 0, `open drawer shows "Done by" when staff finishes it (${doneSeen} ms) @${width}`);
  await ap.getByRole("button", { name: "Close details" }).click();

  // --- 409: someone edits while the admin's Edit form is open -> the error + the task as it is now
  const c = await api(ap, "POST", "/api/team/tasks", { title: `OCT15 race ${tag}`, assignee_user_ids: [staffId] });
  const raceId = c.body?.tasks?.[0]?.id;
  await waitText(ap, `OCT15 race ${tag}`);
  await ap.getByRole("button", { name: `OCT15 race ${tag}` }).click();
  await drawer.getByRole("button", { name: "Edit" }).click();
  await drawer.locator(".task-edit-form input[type=text]").first().fill(`OCT15 race mine ${tag}`);
  const other = await as(browser, "sb_admin", 1280);
  await go(other.page, "/no-access");
  await api(other.page, "PATCH", `/api/team/tasks/${raceId}`, { description: `changed elsewhere ${tag}` });
  await drawer.getByRole("button", { name: "Save" }).click();
  await drawer.getByRole("alert").waitFor({ timeout: 8000 });
  dtext = await drawer.innerText();
  check(/changed by someone else/i.test(dtext), `409 message shown @${width}`);
  check(dtext.includes(`changed elsewhere ${tag}`) && !dtext.includes(`OCT15 race mine ${tag}`), `drawer shows the task as it is now @${width}`);
  await other.ctx.close();

  // --- Reassign: an empty team is labelled and can't be picked; Kitchen can
  await drawer.getByRole("button", { name: "Reassign" }).click();
  const sel = drawer.locator(".task-edit-form select");
  const managers = sel.locator("option", { hasText: "Managers" });
  check((await managers.count()) === 1 && (await managers.textContent()).includes("(no members)") &&
    // (Playwright's isDisabled() reads false for an <option> inside an <optgroup>; ask the DOM.)
    (await managers.evaluate((o) => o.disabled)),
    `Reassign: Managers shows "(no members)" and is disabled @${width}`);
  await sel.selectOption(`team:${kitchen}`);
  await drawer.getByRole("button", { name: "Save" }).click();
  await drawer.getByText("Reassigned.").waitFor({ timeout: 8000 });
  check((await drawer.innerText()).includes("Team: Kitchen"), `reassigned to Kitchen @${width}`);
  await ap.getByRole("button", { name: "Close details" }).click();

  // --- New task form: an empty team is labelled + disabled
  await ap.getByRole("button", { name: "New task" }).click();
  await ap.getByRole("radio", { name: "Team" }).click();
  const opt = ap.locator("form.task-create-form select[aria-label=Team] option", { hasText: "Managers" });
  check((await opt.count()) === 1 && (await opt.isDisabled()), `New task: Managers (no members) disabled @${width}`);
  await ap.locator("form.task-create-form").getByRole("button", { name: "Cancel" }).click();

  // --- search + bulk Done
  for (const n of [1, 2]) await api(ap, "POST", "/api/team/tasks", { title: `OCT15 bulk ${tag} ${n}`, assignee_user_ids: [staffId] });
  await ap.getByLabel("Search task titles").fill(`OCT15 bulk ${tag}`);
  await sleep(1200);
  let txt = await mainText(ap);
  check(txt.includes(`OCT15 bulk ${tag} 1`) && !txt.includes(`OCT15 live ${tag}`), `search narrows the list @${width}`);
  await ap.getByLabel("Select every open task shown").check();
  check((await mainText(ap)).includes("2 selected"), `bulk bar: 2 selected @${width}`);
  await shot(ap, `tasks15-bulk-${width}`);
  await ap.locator(".task-bulk-bar").getByRole("button", { name: "Mark done" }).click();
  await ap.getByText("Marked done: 2.").waitFor({ timeout: 8000 });
  check(true, `bulk Mark done closed both @${width}`);
  await ap.getByRole("button", { name: "Clear filters" }).click();

  // --- Can't do is visible on the admin's list
  await api(sp, "POST", `/api/team/tasks/${liveId}/cant-do`, { reason: "Cooler locked" });
  const cantIn = await waitText(ap, "Cooler locked");
  check(cantIn >= 0, `"Can't do" (with its reason) shows on the open list (${cantIn} ms) @${width}`);
  check(/Can't do \(\d+\)/i.test(await mainText(ap)), `"Can't do" section @${width}`);

  // --- lead task: progress + main button to Fix Issues
  await ap.getByLabel("Search task titles").fill("Fix Issues");
  await sleep(1200);
  const leadBtn = ap.getByRole("button", { name: /^Fix Issues: \d+ left of \d+$/ }).first();
  if (await leadBtn.count()) {
    await leadBtn.click();
    await drawer.locator(".task-progress").waitFor({ timeout: 8000 });
    dtext = await drawer.innerText();
    check(/\d+ left of \d+/.test(dtext), `lead drawer shows "N left of M" @${width}`);
    const link = drawer.getByRole("link", { name: "Open the Fix Issues list" });
    check((await link.count()) === 1 && /#\/fix-issues\?lead=\d+/.test(await link.getAttribute("href")), `main button opens that lead's Fix Issues list @${width}`);
    await sleep(500); // the drawer slides in
    await shot(ap, `tasks15-lead-${width}`);
    await ap.getByRole("button", { name: "Close details" }).click();
  } else {
    check(false, `a lead task "Fix Issues: N left of M" is on the copy @${width}`);
  }
  await ap.getByRole("button", { name: "Clear filters" }).click();
  check((await hscroll(ap)) <= 1, `admin tasks page no h-scroll @${width}`);
  const errs = ap.errors.filter((e) => !/status of 409/.test(e)); // the 409 above is on purpose
  check(errs.length === 0, `admin: no console errors @${width} ${errs.slice(0, 2).join(" | ")}`);
  const bad = ap.bad.filter((b) => !b.startsWith("409 PATCH"));
  check(bad.length === 0, `admin: no failed API calls but the expected 409 @${width} ${bad.slice(0, 3).join(" | ")}`);

  // --- staff: filters, no bulk, no strip, no person filter
  await go(sp, "/team/tasks");
  await sp.getByRole("tab", { name: "List" }).click();
  await settle(sp, 20000);
  txt = await mainText(sp);
  check(!txt.includes("My team today:"), `staff: no "My team today" @${width}`);
  check((await sp.getByLabel("Select every open task shown").count()) === 0, `staff: no bulk select @${width}`);
  check((await sp.getByLabel("Person").count()) === 0, `staff: no person filter @${width}`);
  check((await hscroll(sp)) <= 1, `staff tasks page no h-scroll @${width}`);
  check(sp.errors.length === 0 && sp.bad.length === 0, `staff: no errors @${width} ${[...sp.errors, ...sp.bad].slice(0, 2).join(" | ")}`);

  // --- lead (Assign tasks): strip yes, bulk no
  const lead = await as(browser, "sb_lead", width);
  await go(lead.page, "/team/tasks");
  await lead.page.getByRole("tab", { name: "List" }).click();
  await settle(lead.page, 20000);
  check((await mainText(lead.page)).includes("My team today:"), `lead: "My team today" strip @${width}`);
  check((await lead.page.getByLabel("Select every open task shown").count()) === 0, `lead: no bulk select @${width}`);
  await lead.ctx.close();

  // --- demo (App Review): nothing new to write
  const demo = await as(browser, "demo", width);
  const dp = demo.page;
  await go(dp, "/team/tasks");
  await dp.getByRole("tab", { name: "List" }).click();
  await settle(dp, 20000);
  txt = await mainText(dp);
  check(!txt.includes("My team today:"), `demo: no "My team today" @${width}`);
  check((await dp.getByLabel("Select every open task shown").count()) === 0, `demo: no bulk select @${width}`);
  const demoTask = dp.locator("button.task-title-button").first();
  if (await demoTask.count()) {
    await demoTask.click();
    await dp.locator(".task-drawer").getByText("History").waitFor({ timeout: 8000 });
    check((await dp.locator(".task-drawer").getByRole("button", { name: "Reassign" }).count()) === 0, `demo: no Reassign @${width}`);
    const edit = dp.locator(".task-drawer").getByRole("button", { name: "Edit" });
    if (await edit.count()) {
      await edit.click();
      check((await dp.locator(".task-steps-edit").count()) === 0, `demo: Edit never offers step editing @${width}`);
    }
  }
  check((await hscroll(dp)) <= 1, `demo tasks page no h-scroll @${width}`);
  check(dp.bad.length === 0, `demo: no refused calls @${width} ${dp.bad.slice(0, 3).join(" | ")}`);
  await demo.ctx.close();

  await admin.ctx.close();
  await staff.ctx.close();
}

await browser.close();
done();
