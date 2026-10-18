# Team hub release checklist

What has to happen, in order, to take the Team hub (branch `team-hub` in both repos) live.
The backend goes first and is safe on its own: every API change is additive, so the
App Store build people have today keeps working.

## 1. Backend to prod

1. Merge `team-hub` → `dev` → `main` (website repo).
2. In `/Users/sundar/spicetown-backend`: `git pull`.
3. Install the one new pinned dependency and run the migrations (0026 → 0028):
   ```
   cd /Users/sundar/spicetown-backend/backend
   .venv/bin/pip install -r requirements.txt
   .venv/bin/alembic upgrade head
   ```
   (Take a copy of the prod DB first: `sqlite3 <prod db> ".backup /tmp/prod-before-team.db"`.)
4. Restart (the owner runs this in a normal Terminal):
   `sudo launchctl kickstart -k system/com.spicetown.backend`
5. Optional, same window: the Python 3.12 switch - `website/README.md` → "Python 3.12 upgrade
   (cutover plan)", with rollback.
6. Check: `curl -s https://ops.spicetown.shop/api/app/config` answers; the website shows Team ›
   Tasks / Recurring tasks / Announcements / Moderation for an admin, and Teams under Users.

**Settings to review in prod `.env`** (defaults in brackets):
- `APP_STORE_URL` - the real App Store link for the "Update required" button [a search URL].
- `MIN_SUPPORTED_BUILD_IOS` / `_ANDROID` [0 = never block]. Only raise after the new build is live.
- `TEAM_AUTOTASK_CHECKLIST_ENABLED` [true] - nightly "Submit the closing checklist" tasks for the
  **Managers** team. **Add members to Managers on the website first**, or only admins see them.
- `TEAM_AUTOTASK_INVOICE_ENABLED`, `TEAM_AUTOTASK_ORDER_ENABLED`, `TEAM_WHATSAPP_FALLBACK` [false].
- `TEAM_TASKS_ENABLED`, `TEAM_ANNOUNCEMENTS_ENABLED`, `TEAM_CHAT_ENABLED` [true] - kill switches.

**Before the new app is out:** people on the current build will see the new alert types (Task
assigned, Announcement, Chat message…) in More › Notifications, and a task push opens Home
instead of the task. Harmless, but it's why the app release should follow soon after.

## 2. Team setup (website, admin)

- Users: grant `Team tasks` / `Team chat` to staff; tick "Assign tasks" for shift leads.
- Teams: put people in Grocery / Kitchen / Managers (Everyone is automatic).

## 3. App release (1.1.0)

1. In `/Users/sundar/spicetown-mobile`: merge `team-hub` into `main`, pull, set
   `version: 1.1.0+5` in `pubspec.yaml`, `flutter build ios --config-only --release`, archive and
   upload from Xcode.
2. **App Store Connect → App Privacy** (chat and photos are new data):
   - *User Content → Other User Content* (messages, task notes): collected, linked to the user,
     used for App Functionality, not for tracking.
   - *User Content → Photos or Videos* (task proof and chat photos): same answers.
   - Existing answers (name/user ID for login, device ID for push) unchanged.
3. **Privacy policy**: add a line that staff messages and photos are stored on Spice Town's own
   server, visible to the people in that conversation and to admins for moderation, and deleted
   after 12 months.
4. **App Review information → Notes** (paste):
   > Internal staff app for Spice Town employees. Accounts are issued by the owner; there is no
   > public sign-up and no public content. This version adds a Team tab: tasks (with photo proof),
   > announcements and staff chat. Chat safety: long-press any message → Report (admins review
   > reports in More › Admin › Reports and can delete messages); open a direct message → ⋮ →
   > Block. Demo account below is a regular staff member in a "Review" team with sample tasks and
   > messages.
5. **Demo account**: create a staff login `appreview` with Team tasks + Team chat, put it in a
   team named "Review" with an admin, and seed 2–3 tasks, one announcement and a few messages.
   Give its username/password in App Review information. Remove it after approval.
6. Release: automatic release, phased release off.
7. After it's live: optionally raise `MIN_SUPPORTED_BUILD_IOS` to 5 so everyone updates.

## 4. Google Play (when the Android release is prepared)

Same content rules: the Data safety form (messages and photos, collected, linked, app
functionality, encrypted in transit, deletion on request via the owner), the UGC declaration
(report + block + moderation present), and a reviewer login.
