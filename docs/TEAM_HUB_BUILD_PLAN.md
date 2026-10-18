# Team hub build plan (implementation contract for all agents)

The design is `docs/TEAM_HUB_DESIGN.md` (rev 2). This file fixes the choices the design left
open, and the file ownership that keeps parallel agents from colliding. **When this file and the
design disagree, this file wins.**

## Owner decisions (answers to design §13)
1. Teams seeded by migration: **Everyone** (is_everyone, membership implicit = every user), **Grocery**, **Kitchen**, **Managers** (members empty; admins assign on the website).
2. `users.can_assign` exists (default false). Admins always can assign.
3. Team task = one task, first member to complete it closes it.
4. Chat retention 12 months (setting `TEAM_CHAT_RETENTION_DAYS=365`).
5. Phase 3 auto-tasks come **in addition** to the existing push alerts, behind per-rule server flags (default ON for checklist-missing, OFF for the other two). Existing alerts are not changed.
6. WhatsApp checklist messages unchanged.
7. `generate_ahead_minutes` default 420; 12 h catch-up.
8. Keep "Hide previews" (per-user) and the 120 s badge poll.

## Hard compatibility rules
- **Python 3.9.6** (dev and prod). No `match`, no `X | Y` unions or `list[int]` in anything
  evaluated at runtime by FastAPI/pydantic - use `Optional[...]`, `List[...]` from typing. Test on
  `website/backend/.venv/bin/python`.
- SQLAlchemy 2.0 style (`Mapped`, `mapped_column`) exactly like `app/models.py`; timestamps
  `UTCDateTime()` + `utcnow` from existing code; business time America/New_York via `zoneinfo`.
- **Additive API only.** Never change or remove an existing endpoint/field: the App Store build
  (1.0.0+3/+4) must keep working against the new backend.
- New Python deps: add `python-dateutil` explicitly to requirements.txt (already installed via
  pandas). Test-only deps go in `requirements-dev.txt` (pytest, pytest-asyncio not needed).
- Mobile: iOS min 15.5, Flutter 3.47, Riverpod 2 (StateNotifier/FutureProvider style as in repo),
  go_router, Dio via existing ApiClient. New plugin allowed: `image_picker` only.
- Every new mobile widget: no overflow at 320/375/402 px (test it). App is always dark theme.
- Match surrounding code style and comment density.
- Do NOT commit or push. Do NOT touch prod folders (`/Users/sundar/spicetown-backend`,
  `/Users/sundar/spicetown-mobile`). Do NOT run `sync_dev_data.sh`. The dev backend on :8001
  runs with --reload; a syntax error takes it down - run `python -c "import app.main"` after edits.
- Migrations: run `alembic upgrade head` on the DEV db only (website/backend, dev .env).

## File ownership (parallel safety)
Backend package `website/backend/app/team/`:
| File | Owner |
|---|---|
| `app/team/__init__.py`, `models.py` (teams, team_members, team_changes, team_media), `changes.py` (record_change, updates query), `media.py` (image validation/storage/serve), `bootstrap.py`, `router_core.py` (bootstrap, updates, teams admin, media) | Phase 0 backend |
| `app/team/tasks_models.py`, `tasks_service.py`, `recurrence.py`, `router_tasks.py` | Phase 1 backend |
| `app/team/chat_models.py`, `announcements_service.py`, `chat_service.py`, `moderation.py`, `router_chat.py` | Phase 2 backend |
| `app/team/auto_tasks.py` | Phase 3 backend |
Shared files - one small, additive edit each, re-read right before editing: `app/main.py`
(include_router), `app/features.py` (keys), `app/models.py` (User.can_assign only - Phase 0),
`app/services/notifications.py` (ALERTS entries), `app/scheduler.py` (jobs), `app/routers/auth.py`
+ `app/routers/notifications.py` (Phase 0 only), `requirements*.txt`.
Migrations (fixed numbers/chain): `0026_team_core` (Phase 0), `0027_team_tasks` (down 0026),
`0028_team_chat` (down 0027), `0029_team_auto_tasks` (down 0028, only if needed).
Tests: `website/backend/tests/` - `conftest.py` owned by Phase 0; each phase adds its own
`test_team_<area>.py`.

Mobile `mobile/lib/features/team/`: Phase 0 client owns `lib/core/app_config/` (update gate) and
notification fixes; Phase 1 mobile owns `features/team/` shell + tasks + nav change; Phase 2
mobile adds `features/team/announcements/`, `features/team/chat/`, `features/team/moderation/`
and only registers its segments in the shell file Phase 1 created.
Website: Phase 0 client owns `frontend/src/pages/TeamsPage.jsx` + e2e; later web agent owns
`frontend/src/pages/Team*.jsx`, `frontend/src/components/team/`. Playwright tests: `website/e2e/`.

## Phase 0 API contract (clients build against this)
- `GET /api/app/config` (NO auth): `{"min_supported_build": {"ios": int, "android": int},
  "store_url": {"ios": str, "android": str}}` from settings `MIN_SUPPORTED_BUILD_IOS=0`,
  `MIN_SUPPORTED_BUILD_ANDROID=0`, `APP_STORE_URL`, `PLAY_STORE_URL`. The app compares with its
  build number (package_info `buildNumber`) at launch + resume; below min -> blocking "Update
  required" screen with a button to the store URL. Unreachable server -> never block.
- `GET /api/team/bootstrap` (auth): `{"teams":[{"id","name","color","is_everyone"}],
  "my_team_ids":[...], "can_assign": bool, "features": {"team_tasks": bool, "team_chat": bool},
  "flags": {"tasks_enabled": bool, "announcements_enabled": bool, "chat_enabled": bool,
  "poll_interval_s": 30, "chat_poll_interval_s": 5, "badge_poll_interval_s": 120},
  "min_supported_build": {...}, "cursor": int, "badges": {"tasks_due": 0, "unread": 0,
  "announcements_unacked": 0}}`
- `GET /api/team/updates?since=<int>` (auth): `{"cursor": int, "resync_required": bool,
  "changes": {"<entity_type>": [{"id": int, "action": str}]}, "badges": {...}}`
- Teams admin (admin only): `GET /api/team/teams` -> `[{"id","name","color","is_everyone",
  "archived","member_ids":[...]}]`; `POST /api/team/teams {name,color}`; `PATCH
  /api/team/teams/{id} {name?,color?,archived?}`; `PUT /api/team/teams/{id}/members
  {user_ids:[...]}` (400 for the Everyone team).
- Users: existing `GET /api/auth/users` rows gain `"can_assign": bool`; new `PATCH
  /api/auth/users/{id}/can-assign {"can_assign": bool}` (admin).
- Push: existing `POST /api/notifications/devices` body gains optional `previous_token` (server
  deletes that row if present). New `POST /api/notifications/unregister {"token": str}` with NO
  auth, always 200. Existing authenticated `DELETE /api/notifications/devices` unchanged.
- Media: `POST /api/team/media` (auth, multipart `file`) -> `{"id","token","url","width","height"}`;
  `GET /api/team/media/{token}` (auth + access check hook, 404 otherwise).
