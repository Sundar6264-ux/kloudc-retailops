# Marketing flyers - contract (Oct 4, 2026)

Owner's request: "Add to the Ops portal and mobile:
- upload a marketing flyer;
- choose the WhatsApp groups we want to circulate it to;
- Send button.

Internally, store all these pictures on the Mac mini, organized by type of flyer. The categories are
Groceries, Street Kitchen, Flowers, Halal Meat, Generic to Spice Town, and Others. In a later version,
also connect social media: Facebook page, Instagram, Google Business Profile, TikTok."

Owner's decisions (Oct 4):
- **Who can send:** a new feature permission **`marketing_flyers`**, label "Marketing flyers". Admins
  always have it; admins can tick it for staff in Users.
- **Targets:** only WhatsApp groups an admin has marked **Marketing**, so a flyer can never go to a
  staff group by accident.
- **Content:** an image or a PDF, plus an optional message (caption). There is a preview, then a confirm
  step, then Send. Send history shows delivered or failed per group.
- **Storage:** `~/Spice Town Flyers/<Category>/`, a normal Finder folder. The backend LaunchDaemon runs
  as user `sundar`, so `Path.home()` is `/Users/sundar`.

Everything is additive. Build 5/6/7 apps and the demo account must be unaffected. The demo account
doesn't get the feature, and the demo write guard blocks it by default. Never send real WhatsApp
messages in tests or audits. Mock Whapi.

## 1. Data (migration 0046, down_revision "0045")
- **`whatsapp_groups` gains `marketing BOOLEAN NOT NULL DEFAULT 0`.**
  - The two built-in staff groups stay `marketing = false`.
  - An admin may still tick any group.
- **`flyers`** table:
  - `id`, `title` (1-120 characters), `category` (key, see §2), `caption` (text, nullable, up to
    1,000 characters).
  - `file_rel_path`: relative to the flyers base directory, e.g. `Flowers/2026-10-04 Diwali Garlands (12).jpg`.
  - `original_filename`, `mime`, `size_bytes`, `sha256`, `width`, `height` (nullable, for PDFs).
  - `created_at` and `created_by` (user id + name snapshot), `updated_at`, `deleted_at` (soft delete).
- **`flyer_sends`** table:
  - `id`, `flyer_id`, `batch_id` (uuid), `channel` (`"whatsapp"` now; it exists so Facebook,
    Instagram, Google Business and TikTok can be added later), `target_key` (group key),
    `target_label` (snapshot), `target_ref` (chat id snapshot, never returned to clients).
  - `status`: `queued`, `sending`, `sent` or `failed`. Also `error` (text), `provider_message_id`.
  - `requested_by` (id + name), `requested_at`, `sent_at`, `idempotency_key`.
  - Additive (backend, Oct 4), never returned to clients: `queued_at`, `attempted_at`, `attempts`,
    `ambiguous` (the worker's bookkeeping, see section 4a). `UNIQUE (batch_id, target_key)`.
- **As built:** "id + name" columns are `created_by_user_id` / `created_by_name` and
  `requested_by_user_id` / `requested_by_name`. Both tables use AUTOINCREMENT (the flyer id is part of
  its file name, so an id is never handed out twice).
- **Timestamps** use `UTCDateTime`. Python 3.9 rules apply.

## 2. Categories (fixed list, in this order)

| key | label | folder |
|---|---|---|
| groceries | Groceries | Groceries |
| street_kitchen | Street Kitchen | Street Kitchen |
| flowers | Flowers | Flowers |
| halal_meat | Halal Meat | Halal Meat |
| spice_town | Spice Town (Generic) | Spice Town |
| others | Others | Others |

## 3. Storage
- **Base directory:** setting `FLYERS_DIR`. The default is `Path.home() / "Spice Town Flyers"`.
  Create it and the 6 category folders on first use.
- **File names:** `<Category folder>/<YYYY-MM-DD store date> <sanitized title> (<flyer id>).<ext>`.
  - No path separators, no control characters, no leading dots, at most 80 characters for the title part.
  - Never trust the client's file name for the path.
- **Changing category** moves the file to the new folder, and the rel path is updated.
- **Delete** is a soft delete. The file moves to `_Deleted/<Category>/` under the base directory. It's
  never removed outright.
- **Images:**
  - Accept JPEG, PNG, WebP, HEIC/HEIF. Pillow and pillow_heif are already in the prod venv; macOS
    `sips` is a fallback.
  - Validate by content, not by extension.
  - Normalize to JPEG (quality about 88, EXIF orientation applied, metadata stripped). Downscale so the
    longest side is at most 2560 px and the file is at most about 5 MB, for WhatsApp.
  - Store the original upload too? **No.** Store only the normalized file, to keep one canonical copy.
  - Reject images over 25 MB upload, or decompression bombs (`MAX_PIXELS`, as in `team/media.py`).
- **PDFs:**
  - Validate the `%PDF` header, at most 20 MB.
  - Store as is.
  - Render a first-page preview thumbnail if possible (pypdfium2 isn't installed, so use
    `sips -s format png` or `qlmanage -t` on macOS). Otherwise use a generic PDF icon. Thumbnails are
    cached under `<base>/.thumbs/` (hidden).
- **Thumbnails** for images: JPEG at most 480 px, cached in `.thumbs`.

## 4. API (router prefix `/api/flyers`)
Every route requires the `marketing_flyers` feature (admins implicitly). Demo users get the guard's 403.
- **`GET /api/flyers/meta`** returns:
  `{categories: [{key, label}], groups: [{key, label}], whatsapp_connected: bool, max_image_mb: 25, max_pdf_mb: 20, accept: ["image/jpeg", "image/png", "image/webp", "image/heic", "image/heif", "application/pdf"], max_groups_per_send: 25}`.
  `groups` lists active groups where `marketing = true`, in label order.
- **`POST /api/flyers`**: multipart with `file`, `title`, `category`, and optional `caption`.
  - **201** returns `Flyer`.
  - **400** gives a friendly message: unsupported or corrupt file, too big, bad category, empty title.
  - Check the request size before reading, as `team/media.py` does.
- **`GET /api/flyers?category=&q=&limit=30&before_id=`** returns `{items: [Flyer], next_before_id}`, newest first.
  - Additive (website, Oct 4): also `counts: {all: n, <category key>: n, ...}`, the number of non-deleted
    flyers per category that match `q` (the `category` filter and paging are ignored), for the
    library's category chips. Clients must treat it as optional; the website shows chips without
    numbers when it is missing.
- **`GET /api/flyers/{id}`** returns `Flyer` with `sends` (newest first).
- **`GET /api/flyers/{id}/file`**: the stored file, with the right `Content-Type`, as `Content-Disposition: inline`.
- **`GET /api/flyers/{id}/thumb`**: JPEG or PNG, max 480 px.
- **`PATCH /api/flyers/{id}`** with `{title?, category?, caption?}` returns `Flyer`.
- **`DELETE /api/flyers/{id}`** returns 204 (soft delete).
- **`POST /api/flyers/{id}/send`** with `{group_keys: [..1-25], idempotency_key: str}`.
  - **202** returns `{batch_id, sends: [Send]}`.
  - Unknown or non-marketing or inactive group: 400.
  - WhatsApp not configured: 409 "WhatsApp isn't connected".
  - The same `idempotency_key` within 24 h returns the original batch (no second send).
  - Sending in a background worker:
    - One group at a time, with a 3-6 s jittered delay between groups (anti-spam).
    - Image: Whapi `POST /messages/image` with `{to, media, caption}`.
    - PDF: Whapi `POST /messages/document` with `{to, media, filename, caption}`.
    - Check Whapi's docs for the exact media form: base64 data URL or multipart upload. Use whichever
      Whapi accepts for 5-20 MB, and follow `whapi_client.py`'s timeout/ambiguous-send reconcile pattern.
  - Each send ends as `sent` (with message id) or `failed` (with a short error).
  - A worker restart must not leave rows stuck in `sending`: on startup, mark stale `queued`/`sending`
    rows older than 15 min as `failed` ("Interrupted - retry").
- **`POST /api/flyers/{id}/sends/{send_id}/retry`**: only `failed` sends. Returns 202 and the `Send`.
- **When a batch finishes,** push one notification to the requester: "Flyer sent to N groups" or
  "Flyer: N sent, M failed". Use alert key `flyer_sent`, respect quiet hours, and register it in the
  notification settings list.
- **`Flyer`** JSON:
  `{id, title, category, category_label, caption, file_name (display only), mime, kind: "image"|"pdf", size_bytes, width, height, created_at, created_by_name, file_url, thumb_url, last_sent_at, send_counts: {sent, failed, pending}, sends?: [Send]}`.
  The URLs are relative API paths that need auth (the app uses its bearer token, as for invoice files).
- **`Send`** JSON: `{id, batch_id, channel, target_key, target_label, status, error, requested_by_name, requested_at, sent_at}`.

## 4a. Backend notes (Oct 4, as built - additive to section 4)
- **Meta:** also `max_title_chars: 120` and `max_caption_chars: 1000`. A marketing group with no chat id
  to send to (a built-in whose env setting is blank) isn't listed and can't be sent to.
- **Upload errors:** everything is 400 with a friendly `detail`, except a request without a
  Content-Length (411). The multipart body is parsed off the event loop.
  - PDF decompression bombs (backend review, Oct 4): while a PDF is checked, pypdf may inflate at
    most 64 MB in total (its cross-reference and object streams - real PDFs need a few MB), and
    LZW/RunLength there are refused: 400 "That PDF can't be used - it unpacks to far too much data."
    Page contents and images are never inflated by the check.
  - Titles and captions keep emoji joiners (ZWJ/ZWNJ - a cook emoji, Indian-script conjuncts) and
    characters newer than Python's Unicode tables (the newest emoji). Dropped: control characters
    (a line break or tab in a title becomes a space), lone surrogates, bidi embedding / override /
    isolate characters and the BOM. Text with nothing visible counts as empty. File names also drop
    other invisible format characters and Unicode noncharacters.
  - An id too big for SQLite in a URL is 404 (it was a 500).
- **Idempotency** is per (flyer, person, key) within 24 h. A replay returns 202 and the original batch.
- **Sending (Whapi):** the file is uploaded once per run (`POST /media`, raw body with its
  Content-Type, which gives a MediaID Whapi keeps 30 days), and each group gets
  `/messages/image` or `/messages/document` with that MediaID. If the upload fails, or Whapi refuses
  the MediaID (400/404/415/422), the file goes inline as a base64 data URL
  (`data:<mime>;name=<file>;base64,...`). Timeout 120 s (Whapi itself answers within 110 s).
  The MediaID is dropped for the rest of the run only when that inline resend works; a group that
  refuses the file inline too (gone, not found) fails and leaves the MediaID for the other groups
  (e2e check, Oct 4, commit 2534dd1).
- **Ambiguous sends:** a read timeout, a dropped connection, a 5xx or `sent: false` may still have
  gone out. The worker looks for our own message (same type and caption, not yet credited to another
  send) in the group's recent messages (`GET /messages/list/{chat}`, 3 tries, 5 s apart) before calling
  it failed. A failed one keeps `ambiguous`, and its retry checks the group again before resending.
  (Backend review, Oct 4) The search pages back through the group (`count=100`, then `offset`, at most
  5 pages) until it reaches messages older than the attempt, so a busy customer group can't hide the
  flyer from a retry hours later (one page of 50 used to miss it and post it again).
- **Retry** re-checks that the group is still an active marketing group (400 if not) and that
  WhatsApp is connected (409). Right before each send the worker checks again: flyer not deleted,
  group still marketing, file still there. Otherwise that send fails with the reason.
  - (Backend review, Oct 4) The worker also checks that the group still has the WhatsApp chat the
    send was queued for: a group removed and added again under the same name (same key) with
    another chat fails the queued send ("chat was changed"), and its retry goes to the new chat.
  - A send refused by these checks keeps `ambiguous`, so a later retry still looks in the group first.
  - The checks run again after the file's upload to Whapi (the first group of a run can wait minutes
    for a big PDF), right before the message is posted.
  - Retry tapped while the worker is still finishing that send's failed attempt (e.g. sending the
    push) is queued again, not dropped.
- **Stuck rows:** at startup, and every 5 min while the worker is idle, queued/sending rows whose
  batch has had no activity for 15 min (and that this process isn't holding) become `failed`
  ("Interrupted - retry"). A row that was `sending` is marked ambiguous. Nothing is resent
  automatically.
- **Push `flyer_sent`:** title "Flyer sent to N groups" / "Flyer: N sent, M failed", body = the flyer
  title, data `{flyer_id, batch_id}`, route `flyers`. Only to the requester. During quiet hours it's
  skipped, not held until morning. The dedupe key carries the counts, so a retry that changes the
  outcome pushes again.
- **PATCH** returns the Flyer with `sends`. A new title or category renames or moves the file (same
  date and id). If someone renamed a file in Finder or moved it to another category folder, the
  "(<id>)" in its name still finds it.
- **WhatsApp groups:** `POST /whatsapp-groups` also accepts an optional `marketing`.
- **Dev:** the dev `.env` sets `FLYERS_DIR=/Users/sundar/spicetown-dev/flyers-dev`, so test flyers
  never land in the real `~/Spice Town Flyers`.

## 5. WhatsApp groups admin (existing `/api/checklists/admin/whatsapp-groups`)
- `GET` items gain `marketing: bool`.
- `PATCH /{key}` accepts `{marketing: bool}`.
- **Website "Manage WhatsApp groups" panel:** a "Use for flyers" toggle per row, plus a hint about who
  receives flyers.
- **Flyers page:** if there are no marketing groups, it says so and links admins to that panel.

## 6. Website
- **A new page "Flyers"**, in the sidebar under a Marketing heading, gated by `marketing_flyers`.
  1. **Upload card:**
     - drag-and-drop or choose a file, with an image or PDF preview;
     - title (prefilled from the file name), category dropdown, caption textarea with a character count
       and a WhatsApp formatting hint;
     - "Save to library" (store without sending) and "Send…".
  2. **Send step:**
     - a marketing-group checklist (select all / none);
     - a confirm modal listing the group names and a preview;
     - live per-group status that polls `GET /flyers/{id}` every 3 s while anything is pending.
  3. **Library:**
     - category tabs or chips with counts, search, a thumbnail grid;
     - a detail drawer: big preview, open the file, edit, delete, send to more groups, retry failed,
       full send history.
- The **feature label in Users** is "Marketing flyers". Help gets a section. Glossary terms:
  "Marketing group" and "Flyer" - keys `marketing_group` and `flyer` (backend, Oct 4), in a new
  glossary category `marketing` ("Marketing").
- **CSS gotcha:** custom buttons need `:hover:not(:disabled)`. It must work at 375 px with no sideways scroll.

## 7. App (build 8; version stays 2.0.0+7 until the owner says so)
- **Where:** More → **Flyers**, and a Home search entry ("flyer", "marketing", "whatsapp blast",
  "promotion"). Gated by `marketing_flyers`. Never shown to demo users.
- **Library screen:** category chips, a thumbnail grid or list, pull to refresh, paging.
- **New flyer:**
  - pick a photo (library or camera) or a PDF (files);
  - title, category, caption;
  - choose marketing groups;
  - a preview, then **Send** with a confirm sheet listing the groups.
  - Upload uses `MultipartFile`. The body is never empty (see the empty-FormData bug class).
- **Detail:** preview, share or open, per-group status (polls while pending), retry failed, send to
  more groups, edit, delete.
- **Layout:** works across `test/support/device_matrix.dart`: iPhone SE at 1x and 2x text, 390, 430,
  iPad mini and Pro in both orientations, Split View 678 and 904, and with the keyboard up. Sheets
  must not collapse when scrolled.

## 8. Later (not now)
`channel` and `target_key` exist so Facebook Page, Instagram, Google Business Profile and TikTok
posting can be added as more channels. Each needs OAuth app setup and the owner's accounts.
