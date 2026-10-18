// Browser smoke test for the dashboard: signs in, opens every page the
// account can reach, and fails on any error screen, 5xx response or browser
// console error. By default it never submits, saves or deletes anything.
// It also skips Team > Announcements unless E2E_WRITE=1: just showing an
// announcement marks it "seen" for the account, which would change the
// real seen counts on prod.
//
//   cd website/e2e && npm install && npm run install-browser
//   E2E_USER=someone E2E_PASS=secret npm run smoke
//
// E2E_WRITE=1 (dev only - refused unless E2E_BASE_URL is localhost /
// 127.0.0.1, or E2E_ALLOW_REMOTE_WRITE=1 is set on purpose) also runs write
// flows: create a task for
// yourself on Team > Tasks and mark it done. It leaves that task behind
// (titled "E2E smoke task ..."), so only use it against dev. With an admin
// account it can also:
//   - post an announcement (asking for confirmation) to the team named by
//     E2E_TEAM, have a second account (E2E_STAFF_USER / E2E_STAFF_PASS, a
//     member of that team) confirm it, and check the admin sees the count
//     go up. Use a throwaway team: its members get the push.
//   - chat in E2E_TEAM's channel: the admin posts, the staff account sees
//     it as unread (375px, no sideways scroll), opens it and replies, then
//     sends the admin a direct message that shows up unread for them. Only
//     ever in that throwaway team's channel and a DM between the two test
//     accounts, so no real person gets a push.
//   - resolve (keeping the message) the newest open chat report whose
//     REASON starts with "E2E smoke" on Team > Moderation - matched on the
//     reason only, never on the message text. The test never creates reports
//     itself - reporting pushes every admin - so seed one first (the
//     dev-only helper does it straight in the database).
//
// E2E_BASE_URL defaults to the dev backend (http://127.0.0.1:8001).
// Exit code 1 if any check fails.
import { chromium } from "playwright";

const BASE = (process.env.E2E_BASE_URL || "http://127.0.0.1:8001").replace(/\/$/, "");
const USER = process.env.E2E_USER;
const PASS = process.env.E2E_PASS;
if (!USER || !PASS) {
  console.error("Set E2E_USER and E2E_PASS (a dashboard login) first.");
  process.exit(2);
}

const WRITE = process.env.E2E_WRITE === "1";

// Hash routes (the app uses HashRouter), in sidebar order. Admin-only pages
// are only checked when the account is an admin.
const PAGES = [
  "/invoices",
  "/daily-checklists",
  "/open-orders",
  "/items-sold",
  "/reorder",
  "/supplier-projection",
  "/vendor-calendar",
  "/purchase-order",
  "/placed-orders",
  "/delivery-review",
  "/transfer-review",
  "/inventory-reports",
  "/fix-issues",
  "/reconciliation",
  "/team/tasks",
  "/team/chat",
  // Opening it marks announcements seen - only in write mode.
  ...(WRITE ? ["/team/announcements"] : []),
  "/change-password",
  "/help",
  "/glossary",
  // Own alert switches (read-only here: nothing is toggled).
  "/notifications",
  "/po-sections",
  // Marketing flyers: read-only here (meta, library, thumbnails); never sends.
  "/flyers",
];
const ADMIN_PAGES = ["/users", "/teams", "/team/templates", "/team/moderation", "/checklists/admin"];

// Write flows post, push and resolve reports - never against a real server
// by accident.
const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);
if (WRITE && !LOCAL_HOSTS.has(new URL(BASE).hostname) && process.env.E2E_ALLOW_REMOTE_WRITE !== "1") {
  console.error(
    `E2E_WRITE=1 is only allowed against localhost/127.0.0.1 (got ${BASE}). ` +
      "Set E2E_ALLOW_REMOTE_WRITE=1 too if you really mean to write to that server.",
  );
  process.exit(2);
}

const results = [];
const ok = (name, pass, info = "") => {
  results.push({ name, pass });
  console.log(`${pass ? "PASS" : "FAIL"}  ${name}${info ? `  - ${info}` : ""}`);
};

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
page.setDefaultTimeout(15000);
const consoleErrors = [];
const serverErrors = [];
page.on("console", (m) => {
  if (m.type() === "error") consoleErrors.push(m.text().slice(0, 160));
});
page.on("response", (r) => {
  if (r.status() >= 500) serverErrors.push(`${r.status()} ${r.url()}`);
});

// The dashboard polls job status, so "network idle" never arrives: wait for
// the DOM, then give its data requests a moment.
const settle = async () => {
  await page.waitForLoadState("domcontentloaded");
  await page.waitForTimeout(1500);
};
const go = async (path) => {
  await page.goto(`${BASE}/#${path}`);
  await page.waitForTimeout(1800);
};

try {
  await page.goto(`${BASE}/`);
  await page.locator("input").first().fill(USER);
  await page.locator('input[type="password"]').fill(PASS);
  await page.locator('button[type="submit"]').click();
  await settle();
  const signedIn = await page.getByRole("button", { name: /log out/i }).isVisible().catch(() => false);
  ok("sign in", signedIn);
  if (!signedIn) throw new Error("sign-in failed");
  // The login screen's own "am I signed in?" probe 401s by design - only
  // count console errors from here on.
  consoleErrors.length = 0;

  const me = await page.request.get(`${BASE}/api/auth/me`);
  const isAdmin = me.ok() ? Boolean((await me.json()).is_admin) : false;

  for (const path of [...PAGES, ...(isAdmin ? ADMIN_PAGES : [])]) {
    const before = serverErrors.length;
    await go(path);
    const crashed = await page
      .getByText(/something went wrong|unexpected application error|"detail":"Not Found"/i)
      .first()
      .isVisible()
      .catch(() => false);
    const heading = await page.locator("main h1").first().textContent().catch(() => "");
    ok(`page ${path}`, !crashed && serverErrors.length === before, (heading || "").trim().slice(0, 40));
  }

  if (isAdmin) {
    await go("/teams");
    const everyone = await page.getByText("Everyone", { exact: true }).first().isVisible().catch(() => false);
    ok("teams: the Everyone team is listed", everyone);
    await go("/users");
    const assignColumn = await page.getByRole("columnheader", { name: "Assign tasks" }).isVisible().catch(() => false);
    ok("users: Assign tasks column", assignColumn);

    // Read-only: the schedule editor's live preview is a POST that saves
    // nothing (the backend just works out the next dates).
    await go("/team/templates");
    await page.getByRole("button", { name: "New schedule" }).click();
    const preview = await page.getByText(/^Next 5$/).first().waitFor({ timeout: 8000 }).then(() => true).catch(() => false);
    ok("recurring tasks: preview shows the next 5 due dates", preview);
    await page.getByRole("button", { name: "Cancel" }).click();
  }

  const me2 = me.ok() ? await me.json() : {};
  const hasTasks = isAdmin || (me2.allowed_features || []).includes("team_tasks");
  if (WRITE && hasTasks) {
    const title = `E2E smoke task ${Date.now()}`;
    await go("/team/tasks");
    await page.getByRole("button", { name: "New task" }).click();
    await page.getByLabel("Title").fill(title);
    await page.getByRole("button", { name: "Create task" }).click();
    const created = await page.getByText(`Created "${title}".`).waitFor({ timeout: 8000 }).then(() => true).catch(() => false);
    ok("tasks: create a task for myself", created);
    await page.getByRole("button", { name: title }).click();
    await page.getByRole("dialog", { name: "Task details" }).waitFor();
    await page.getByRole("button", { name: "Mark done" }).click();
    const doneMsg = await page.getByText("Marked done.").waitFor({ timeout: 8000 }).then(() => true).catch(() => false);
    const history = await page.getByText(/completed it/).first().waitFor({ timeout: 8000 }).then(() => true).catch(() => false);
    ok("tasks: mark it done (shows in history)", doneMsg && history);
    await page.getByRole("button", { name: "Close details" }).click();
    const inFinished = await page.getByText(/Recently finished/).first().isVisible().catch(() => false);
    ok("tasks: it moves to Recently finished", inFinished);
  }

  const STAFF_USER = process.env.E2E_STAFF_USER;
  const STAFF_PASS = process.env.E2E_STAFF_PASS;
  const TEAM = process.env.E2E_TEAM;
  if (WRITE && isAdmin && STAFF_USER && STAFF_PASS && TEAM) {
    const annTitle = `E2E smoke announcement ${Date.now()}`;
    await go("/team/announcements");
    await page.getByRole("button", { name: "New announcement" }).click();
    await page.getByLabel("Title").fill(annTitle);
    await page.getByLabel("Message").fill("Please confirm you've read this.");
    await page.getByRole("checkbox", { name: TEAM, exact: true }).check();
    await page.getByRole("button", { name: "Post announcement" }).click();
    const posted = await page.getByText(/^Posted/).waitFor({ timeout: 8000 }).then(() => true).catch(() => false);
    const card = page.locator("article.announcement-card", { hasText: annTitle });
    const zero = await card
      .getByRole("button", { name: /^0 of \d+ acknowledged$/ })
      .waitFor({ timeout: 8000 })
      .then(() => true)
      .catch(() => false);
    ok("announcements: post one to a team, 0 acknowledged", posted && zero);

    // The staff account confirms it in its own browser session.
    const staffCtx = await browser.newContext({ viewport: { width: 375, height: 800 } });
    const staff = await staffCtx.newPage();
    await staff.goto(`${BASE}/`);
    await staff.locator("input").first().fill(STAFF_USER);
    await staff.locator('input[type="password"]').fill(STAFF_PASS);
    await staff.locator('button[type="submit"]').click();
    await staff.waitForTimeout(1500);
    // After sign-in only: the login screen's own probe 401s by design.
    staff.on("console", (m) => {
      if (m.type() === "error") consoleErrors.push(`[staff] ${m.text().slice(0, 150)}`);
    });
    await staff.goto(`${BASE}/#/team/announcements`);
    const staffCard = staff.locator("article.announcement-card", { hasText: annTitle });
    await staffCard.getByRole("button", { name: "I've read this" }).click();
    const confirmed = await staffCard.getByText(/You confirmed/).waitFor({ timeout: 8000 }).then(() => true).catch(() => false);
    const noOverflow = await staff.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
    ok("announcements: staff confirms it (375px, no sideways scroll)", confirmed && noOverflow);
    await staffCtx.close();

    // Same hash URL = no navigation, so reload to fetch the new count now
    // rather than waiting for the page's own 30 s refresh.
    await page.reload();
    await settle();
    const one = await card
      .getByRole("button", { name: /^1 of \d+ acknowledged$/ })
      .waitFor({ timeout: 8000 })
      .then(() => true)
      .catch(() => false);
    ok("announcements: the admin sees 1 acknowledged", one);
    if (one) {
      await card.getByRole("button", { name: /acknowledged$/ }).click();
      const drawer = page.getByRole("dialog", { name: "Who has read it" });
      const listed = await drawer
        .getByRole("cell", { name: STAFF_USER, exact: true })
        .waitFor({ timeout: 8000 })
        .then(() => true)
        .catch(() => false);
      ok("announcements: recipients list names who confirmed", listed);
      await drawer.getByRole("button", { name: "Close" }).click();
    }
  }

  if (WRITE && isAdmin && STAFF_USER && STAFF_PASS && TEAM) {
    const stamp = Date.now();
    const teamRow = (p) => p.locator("button.chat-channel", { hasText: `# ${TEAM}` }).first();
    const bubble = (p, text) => p.locator(".chat-bubble", { hasText: text }).first();
    const visible = (loc) => loc.waitFor({ timeout: 10000 }).then(() => true).catch(() => false);

    // Admin posts in the throwaway team's channel.
    const adminMsg = `E2E smoke chat ${stamp}`;
    await go("/team/chat");
    await teamRow(page).click();
    await page.getByLabel("Message", { exact: true }).fill(adminMsg);
    await page.getByLabel("Message", { exact: true }).press("Enter");
    const sent = await visible(bubble(page, adminMsg));
    await page.waitForTimeout(1200);
    const notFailed = !(await page.getByText("Not sent").first().isVisible().catch(() => false));
    ok("chat: admin sends in the team channel", sent && notFailed);

    // Staff (a phone-width browser) sees it unread, opens it and replies.
    const staffCtx = await browser.newContext({ viewport: { width: 375, height: 800 } });
    const staff = await staffCtx.newPage();
    await staff.goto(`${BASE}/`);
    await staff.locator("input").first().fill(STAFF_USER);
    await staff.locator('input[type="password"]').fill(STAFF_PASS);
    await staff.locator('button[type="submit"]').click();
    await staff.waitForTimeout(1500);
    staff.on("console", (m) => {
      if (m.type() === "error") consoleErrors.push(`[staff] ${m.text().slice(0, 150)}`);
    });
    await staff.goto(`${BASE}/#/team/chat`);
    const unread = await visible(teamRow(staff).locator(".chat-unread"));
    const listFits = await staff.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
    ok("chat: staff sees the channel as unread (375px, no sideways scroll)", unread && listFits);
    await teamRow(staff).click();
    const got = await visible(bubble(staff, adminMsg));
    const reply = `E2E smoke reply ${stamp}`;
    await staff.getByLabel("Message", { exact: true }).fill(reply);
    await staff.getByLabel("Message", { exact: true }).press("Enter");
    const replied = await visible(bubble(staff, reply));
    const convFits = await staff.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
    ok("chat: staff opens it and replies (375px, no sideways scroll)", got && replied && convFits);

    // The reply reaches the admin's open conversation by polling.
    const arrived = await bubble(page, reply).waitFor({ timeout: 15000 }).then(() => true).catch(() => false);
    ok("chat: the reply shows up for the admin without reloading", arrived);

    // Staff starts a DM with the admin.
    await staff.getByRole("button", { name: /back to conversations/i }).click();
    await staff.getByRole("button", { name: "New message" }).click();
    await staff.getByPlaceholder("Search people").fill(USER);
    await staff.locator(".chat-new-dm-list button.chat-channel", { hasText: USER }).first().click();
    const dm = `E2E smoke dm ${stamp}`;
    await staff.getByLabel("Message", { exact: true }).fill(dm);
    await staff.getByLabel("Message", { exact: true }).press("Enter");
    const dmSent = await visible(bubble(staff, dm));
    await staffCtx.close();

    // Same route with the list already loaded - reload rather than wait for
    // the page's own 10 s refresh.
    await go("/team/chat");
    await page.reload();
    await settle();
    // Match the conversation name exactly - the team channel's preview line
    // ("zz_staff: ...") contains the name too.
    const dmRow = page
      .locator("button.chat-channel")
      .filter({ has: page.locator(".chat-channel-name", { hasText: new RegExp(`^${STAFF_USER}$`) }) })
      .first();
    const dmUnread = await visible(dmRow.locator(".chat-unread"));
    await dmRow.click();
    const dmGot = await visible(bubble(page, dm));
    ok(
      "chat: a direct message arrives unread and opens",
      dmSent && dmUnread && dmGot,
      `sent=${dmSent} unread=${dmUnread} opened=${dmGot}`,
    );
  }

  if (WRITE && isAdmin) {
    await go("/team/moderation");
    // Matched on the reason line only - a real report of a message that
    // happens to contain "E2E smoke" must never be touched.
    const report = page
      .locator("article.moderation-report")
      .filter({ has: page.locator(".moderation-reason", { hasText: /Reason:\s*E2E smoke/ }) })
      .first();
    if (await report.isVisible().catch(() => false)) {
      // Resolve without deleting anything.
      const del = report.getByRole("checkbox", { name: /Delete the message/ });
      if (await del.isChecked().catch(() => false)) await del.uncheck();
      await report.getByLabel(/^Note/).fill("E2E smoke: handled");
      await report.getByRole("button", { name: "Resolve" }).click();
      const resolved = await page.getByText("Report resolved.").waitFor({ timeout: 8000 }).then(() => true).catch(() => false);
      await page.getByRole("tab", { name: "Resolved" }).click();
      const listed = await page
        .locator("article.moderation-report", { hasText: "E2E smoke: handled" })
        .first()
        .waitFor({ timeout: 8000 })
        .then(() => true)
        .catch(() => false);
      ok("moderation: resolve a report (moves to Resolved)", resolved && listed);
    } else {
      console.log("SKIP  moderation: no open \"E2E smoke\" report seeded");
    }
  }

  ok("no server (5xx) errors", serverErrors.length === 0, serverErrors.slice(0, 3).join(" | "));
  ok("no browser console errors", consoleErrors.length === 0, consoleErrors.slice(0, 3).join(" | "));
} catch (e) {
  ok("run completed", false, String(e).slice(0, 200));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass).length;
console.log(`\n${results.length - failed}/${results.length} checks passed`);
process.exit(failed ? 1 : 0);
