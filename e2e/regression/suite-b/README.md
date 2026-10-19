# Suite B - team, insights, marketing, checklists, admin (website regression)

Playwright flows on a throwaway server that runs on a **read-only copy of the prod DB** migrated to head.
Nothing here can reach Toast, WhatsApp, Gmail, Anthropic or Firebase: Toast / Analytics / Anthropic / Firebase are
blank, SMTP is a fake outbox, and Whapi points at `mock_whapi.py` (127.0.0.1:8838) with a fake token. `mkusers.py`
also swaps every WhatsApp group's chat id in the copy for a fake one.

```
export SUITE_B_WORK=/tmp/retailops-suiteB   # default; everything is written here
export SUITE_B_SOURCE_DB=/path/to/store.db           # required: the store database to copy (opened mode=ro)
export SUITE_B_FLYERS_SRC=/path/to/flyers            # optional: its flyer folder, so existing thumbnails load
./setup.sh                # .backup (mode=ro) -> alembic upgrade head -> temp users -> website from git HEAD of this repo
python3 -I mock_whapi.py &                         # port 8838, logs to $SUITE_B_WORK/whapi_log.jsonl
./serve.sh &                                       # port 8831 (refuses to start on anything but the copy)
./run.sh                  # every flow at 1280 / 1024 / 375, then the sweep (all pages x 5 roles x 3 widths)
```

Logins (copy only, password `SuiteB-test-2026`): `sb_admin`, `sb_staff` (tasks, chat, checklists, flyers, Sales
insights), `sb_chat` (chat only), `sb_hide` (Hide sales numbers + Sales insights), `sb_lead` (Assign tasks) and the
App Review demo `app_store_review`. Sessions are in `$SUITE_B_WORK/users.json`.

| script | covers |
|---|---|
| `team.mjs` | task form, overdue section + Overdue only, board drag / buttons (To do, In progress, Done), photo-proof form, comments + word filter, task report -> Moderation, admin Stats, templates (steps, weekday assignee, Create task now), lead + demo limits |
| `chat.mjs` | text + @mention, word filter, photo, PDF (Open), voice (browser recording and an uploaded AAC note playing), Seen by, pins (admin yes / staff no), report -> Moderation -> resolve with delete, message search, DM block, announcements + acknowledge, demo scoping |
| `flyers.mjs` | upload JPG / HEIC (stored as JPEG) / PDF, only "Use for flyers" groups offered, send through the mock with one group failing, Retry, document send, library search, drawer history, no chat ids, demo + no-feature refused |
| `insights.mjs` | every page per role, Also in Toast (sidebar group, note, redirects), hide_sales sees no dollars (pages + APIs), admin-only pages, Loss watch Acknowledge, Prep forecast print layout |
| `checklists.mjs` | builder list, WhatsApp groups panel (available via mock, add / rename, Use for flyers, built-ins can't be deleted, in-use 409), new checklist, publish, Settings group dropdown, Public link tab, staff fill -> summary to the chosen (mock) group, history, public `/c/<slug>-<code>` page with the store's header, bad link 404, demo posts nothing |
| `admin.mjs` | Users feature list (Insights, Flyers) and granting one, Help, Glossary, Overview per role, no em dashes, no red hover |
| `sweep.mjs` | `sweep-pages.mjs` x 5 roles x 3 widths: screenshot, console errors, failed API calls, page sideways scroll, where each role lands |

`sheet.py '<glob>' out.png cols width` makes contact sheets of `$SUITE_B_WORK/shots` for looking through the screenshots.
Delete `$SUITE_B_WORK` (it holds a full copy of the prod DB) when done.
