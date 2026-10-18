// Suite B - Team tasks: list, board (To do / In progress / Done by drag and by buttons), overdue, admin stats,
// templates (steps, schedule, weekday assignee, Create task now), comments + word filter, task report -> moderation,
// lead and demo limits. node team.mjs [width ...]
import { chromium, as, go, settle, hscroll, check, note, done, shot, api, mainText, sleep, USERS, W } from "./lib.mjs";

const widths = process.argv.slice(2).map(Number).filter(Boolean);
const WIDTHS = widths.length ? widths : [1280, 375];
const RUN = Date.now().toString(36).slice(-5);
const hoursFromNow = (h) => new Date(Date.now() + h * 3600e3).toISOString();
const browser = await chromium.launch();

for (const width of WIDTHS) {
  const tag = `${RUN}-${width}`;
  console.log(`\n== Team tasks @${width} (run ${tag})`);
  const admin = await as(browser, "sb_admin", width);
  const staff = await as(browser, "sb_staff", width);
  const ap = admin.page;
  const sp = staff.page;
  await go(ap, "/team/tasks");

  // --- admin creates a task for sb_staff through the form
  await ap.getByRole("button", { name: "New task" }).click();
  await ap.locator("form.task-create-form input[type=text]").first().fill(`SB UI task ${tag}`);
  await ap.getByRole("radio", { name: "People" }).click();
  await ap.locator(".task-people label", { hasText: /^sb_staff$/ }).locator("input").check();
  await ap.locator("form.task-create-form textarea").nth(1).fill("Step one\nStep two");
  await shot(ap, `team-create-form-${width}`);
  await ap.locator("form.task-create-form button[type=submit]").click();
  await ap.getByText(`Created "SB UI task ${tag}".`).waitFor({ timeout: 15000 });
  check(true, `admin created a task from the form @${width}`);
  check((await hscroll(ap)) <= 1, `tasks page no h-scroll @${width}`);

  // --- API setup: an overdue task and a photo-proof task for sb_staff
  const staffId = USERS.sb_staff.id;
  const od = await api(ap, "POST", "/api/team/tasks", { title: `SB overdue ${tag}`, assignee_user_ids: [staffId], due_at: hoursFromNow(-3) });
  const ph = await api(ap, "POST", "/api/team/tasks", { title: `SB photo ${tag}`, assignee_user_ids: [staffId], requires: "photo", due_at: hoursFromNow(5) });
  check(od.status === 200 && ph.status === 200, `API created overdue + photo tasks (${od.status}, ${ph.status})`);
  const odId = od.body?.tasks?.[0]?.id;
  const phId = ph.body?.tasks?.[0]?.id;

  // --- staff: list sections + overdue only
  await go(sp, "/team/tasks");
  await sp.getByRole("tab", { name: "List" }).click();
  await settle(sp);
  let txt = await mainText(sp);
  check(/Overdue \(\d+\)/i.test(txt) && txt.includes(`SB overdue ${tag}`), `staff list shows an Overdue section with the overdue task @${width}`);
  check(!(await sp.getByRole("tab", { name: "Stats" }).count()), `staff has no Stats tab @${width}`);
  await sp.getByLabel("Overdue only").check();
  await settle(sp);
  txt = await mainText(sp);
  check(txt.includes(`SB overdue ${tag}`) && !txt.includes(`SB photo ${tag}`), `Overdue only shows only overdue tasks @${width}`);
  await shot(sp, `team-overdue-${width}`);
  await sp.getByLabel("Overdue only").uncheck();

  // --- board: drag To do -> In progress, button Done, photo task to Done opens proof form
  await sp.getByRole("tab", { name: "Board" }).click();
  await settle(sp);
  const card = (id) => sp.locator(`li.board-card[data-task-id="${id}"]`);
  check(await card(odId).count() === 1, `overdue card on the board @${width}`);
  check(await sp.locator(`section.board-column-todo li[data-task-id="${odId}"]`).count() === 1, `overdue card starts in To do @${width}`);
  if (width >= 1024) {
    await card(odId).dragTo(sp.locator("section.board-column-in_progress"), { targetPosition: { x: 40, y: 16 } });
  } else {
    await card(odId).getByRole("button", { name: "Start" }).click();
  }
  await sp.locator(`section.board-column-in_progress li[data-task-id="${odId}"]`).waitFor({ timeout: 15000 }).catch(() => {});
  check(await sp.locator(`section.board-column-in_progress li[data-task-id="${odId}"]`).count() === 1,
    `${width >= 1024 ? "drag" : "Start button"} moved the card to In progress @${width}`);
  let t = await api(sp, "GET", `/api/team/tasks/${odId}`);
  check(!!t.body?.started_at && t.body?.status === "open" && t.body?.board_column === "in_progress", `server: started_at set, status open @${width}`);
  await shot(sp, `team-board-${width}`);
  // back to To do (drag at desktop), then start again by button and finish with Done
  if (width >= 1024) {
    await sp.locator(`li.board-card[data-task-id="${odId}"]`).dragTo(sp.locator("section.board-column-todo"), { targetPosition: { x: 40, y: 16 } });
    await sleep(1500);
    t = await api(sp, "GET", `/api/team/tasks/${odId}`);
    check(!t.body?.started_at && t.body?.board_column === "todo", `drag back to To do clears started_at @${width}`);
    await card(odId).getByRole("button", { name: "Start" }).click();
    await sleep(1200);
  }
  await card(odId).getByRole("button", { name: "Done" }).click();
  await sp.locator(`section.board-column-done li[data-task-id="${odId}"]`).waitFor({ timeout: 15000 }).catch(() => {});
  t = await api(sp, "GET", `/api/team/tasks/${odId}`);
  check(t.body?.status === "done", `Done button completes the task @${width}`);
  // photo task: Done opens the proof form instead of completing
  if (width >= 1024) await card(phId).dragTo(sp.locator("section.board-column-done"), { targetPosition: { x: 40, y: 16 } });
  else await card(phId).getByRole("button", { name: "Done" }).click();
  const drawer = sp.locator(".task-drawer[role=dialog]");
  await drawer.waitFor({ timeout: 10000 }).catch(() => {});
  await drawer.getByRole("button", { name: /^Mark done/ }).first().waitFor({ timeout: 10000 }).catch(() => {});
  await sleep(500); // drawer slide-in
  check(await drawer.count() === 1 && /Mark done/.test(await drawer.innerText()), `photo task to Done opens the proof form @${width}`);
  t = await api(sp, "GET", `/api/team/tasks/${phId}`);
  check(t.body?.status === "open", `photo task stays open until a photo is added @${width}`);
  await shot(sp, `team-photo-proof-${width}`);
  // attach the photo and finish
  const fileInput = drawer.locator("input[type=file]");
  if (await fileInput.count()) {
    await fileInput.setInputFiles(`${W}/fixtures/photo.jpg`);
    await drawer.getByRole("button", { name: /^Mark done$/ }).click();
    await sleep(2500);
    t = await api(sp, "GET", `/api/team/tasks/${phId}`);
    check(t.body?.status === "done" && !!t.body?.completion_photo, `photo proof completes the task @${width}`);
  } else check(false, `photo proof form has a file input @${width}`);
  await sp.keyboard.press("Escape");
  const close = sp.getByRole("button", { name: "Close details" });
  if (await close.count()) await close.click();

  // --- comments + word filter (drawer), on the UI task
  const mine = await api(sp, "GET", "/api/team/tasks?view=mine&status=open");
  const uiTask = (mine.body?.items || []).find((x) => x.title === `SB UI task ${tag}`);
  check(!!uiTask && uiTask.item_count === 2, `UI task reached sb_staff with 2 steps @${width}`);
  if (uiTask) {
    await sp.getByRole("tab", { name: "List" }).click();
    await settle(sp);
    await sp.getByRole("button", { name: `SB UI task ${tag}` }).first().click();
    await drawer.waitFor();
    await settle(sp);
    const box = drawer.getByPlaceholder("Add a comment…");
    await box.fill(`Suite B comment ${tag} this is sh1t`);
    await box.press("Enter");
    await sleep(1500);
    const dtext = await drawer.innerText();
    check(dtext.includes(`Suite B comment ${tag}`), `comment shows in the drawer @${width}`);
    check(!/sh1t/.test(dtext) && /s\*{3}/.test(dtext), `word filter masks the comment @${width}`);
    await shot(sp, `team-drawer-comment-${width}`);
    const cw = await drawer.locator(".task-comment-form input").evaluate((e) => e.getBoundingClientRect().width);
    check(cw >= 150, `comment box is usable (${Math.round(cw)}px wide) @${width}`);
    check((await hscroll(sp)) <= 1, `drawer no h-scroll @${width}`);
    await sp.getByRole("button", { name: "Close details" }).click();
    // report the admin's task (API - the website has no task Report; the app does)
    const rep = await api(sp, "POST", `/api/team/tasks/${uiTask.id}/report`, { reason: "Suite B test report" });
    check(rep.status === 200, `staff can report a task (${rep.status}) @${width}`);
    const self = await api(ap, "POST", `/api/team/tasks/${uiTask.id}/report`, { reason: "self" });
    check(self.status === 400, `can't report your own task (${self.status}) @${width}`);
    await go(ap, "/team/moderation");
    const mt = await mainText(ap);
    check(mt.includes(`SB UI task ${tag}`) || mt.includes("Suite B test report"), `the task report reaches Moderation @${width}`);
    await shot(ap, `team-moderation-${width}`);
  }

  // --- admin stats
  await go(ap, "/team/tasks");
  await ap.getByRole("tab", { name: "Stats" }).click();
  await settle(ap);
  const st = await mainText(ap);
  check(/sb_staff/.test(st), `Stats lists sb_staff @${width}`);
  check((await hscroll(ap)) <= 1, `stats no page h-scroll @${width}`);
  await shot(ap, `team-stats-${width}`);
  await ap.getByRole("tab", { name: "List" }).click();

  // --- templates: steps, on a schedule, weekday assignee, Create task now
  await go(ap, "/team/templates");
  await ap.getByRole("button", { name: "New template" }).click();
  const form = ap.locator("form.task-create-form");
  await form.locator("input[type=text]").first().fill(`SB template ${tag}`);
  await form.locator("textarea").nth(1).fill("Lights on\nCount the till");
  await form.getByRole("radio", { name: "Only when I create it" }).click();
  await form.getByRole("radio", { name: "Person" }).click().catch(() => {});
  const personSel = form.getByLabel("Person");
  if (await personSel.count()) await personSel.selectOption({ label: "sb_staff" });
  await shot(ap, `team-template-form-${width}`, true);
  await form.locator("button[type=submit]").click();
  await settle(ap);
  let tt = await mainText(ap);
  check(tt.includes(`SB template ${tag}`), `template saved and listed @${width}`);
  const tpls = await api(ap, "GET", "/api/team/templates");
  const tpl = (tpls.body?.items || tpls.body || []).find?.((x) => x.title === `SB template ${tag}`);
  check(!!tpl && tpl.repeats === false && (tpl.items || []).length === 2, `template has 2 steps and doesn't repeat @${width}`);
  const row = ap.locator("tr", { hasText: `SB template ${tag}` });
  await row.getByRole("button", { name: /Create task now/ }).click();
  await ap.locator("form.tpl-create-now button[type=submit]").click();
  await ap.getByText(new RegExp(`Created "SB template ${tag}"`)).waitFor({ timeout: 15000 }).catch(() => {});
  tt = await mainText(ap);
  check(new RegExp(`Created "SB template ${tag}"`).test(tt), `Create task now made a task @${width}`);
  const made = await api(sp, "GET", "/api/team/tasks?view=mine&status=open");
  const fromTpl = (made.body?.items || []).find((x) => x.title === `SB template ${tag}`);
  check(!!fromTpl && fromTpl.item_count === 2, `the template's task reached sb_staff with its steps @${width}`);
  // a repeating one with a weekday assignee, through the API (the form's RepeatBuilder is covered by screenshots)
  const rep2 = await api(ap, "POST", "/api/team/templates", {
    title: `SB weekly ${tag}`, rrule: "FREQ=WEEKLY;BYDAY=MO;BYHOUR=15;BYMINUTE=0", assignee_team_id: USERS.kitchen,
    items: ["a", "b"], day_assignees: { SA: { user_id: staffId } }, repeats: true,
  });
  check(rep2.status === 200 || rep2.status === 201, `repeating template with a Saturday assignee saves (${rep2.status}) @${width}`);
  await go(ap, "/team/templates");
  tt = await mainText(ap);
  check(tt.includes(`SB weekly ${tag}`) && /Sat\w*: sb_staff/.test(tt), `list shows the weekday assignee @${width}`);
  check((await hscroll(ap)) <= 1, `templates page no h-scroll @${width}`);
  await shot(ap, `team-templates-${width}`);

  await admin.ctx.close();
  await staff.ctx.close();
}

// --- lead (can_assign) and demo limits, once
{
  const { ctx, page } = await as(browser, "sb_lead", 1280);
  await go(page, "/team/tasks");
  check(!(await page.getByRole("tab", { name: "Stats" }).count()), "lead: no Stats tab");
  await page.getByRole("button", { name: "New task" }).click();
  check(await page.getByRole("radio", { name: "People" }).count() === 1, "lead: can assign to people");
  const st = await api(page, "GET", "/api/team/task-stats?days=30");
  check(st.status === 403, `lead: task-stats refused (${st.status})`);
  await go(page, "/team/templates");
  check(!page.url().includes("/team/templates"), `lead: templates page (admin only) redirects (${page.url()})`);
  await ctx.close();
}
{
  const { ctx, page } = await as(browser, "demo", 1280);
  await go(page, "/team/tasks");
  check(!(await page.getByRole("tab", { name: "Stats" }).count()), "demo: no Stats tab");
  const st = await api(page, "GET", "/api/team/task-stats?days=30");
  check(st.status === 403, `demo: task-stats refused (${st.status})`);
  const cr = await api(page, "POST", "/api/team/tasks", { title: "demo write", assignee_user_ids: [USERS.sb_staff.id] });
  check(cr.status === 403, `demo: a task for someone outside its teams is refused (${cr.status})`);
  const own = await api(page, "POST", "/api/team/tasks", { title: "demo own task (sandbox)" });
  check(own.status === 200, `demo: its own personal task is allowed (sandbox) (${own.status})`);
  await page.getByRole("tab", { name: "Board" }).click();
  await settle(page);
  await shot(page, "team-demo-board-1280");
  note(`demo board text: ${(await mainText(page)).slice(0, 160).replace(/\n/g, " | ")}`);
  await ctx.close();
}
await browser.close();
done();
