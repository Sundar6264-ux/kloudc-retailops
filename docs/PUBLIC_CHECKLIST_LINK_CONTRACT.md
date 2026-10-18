# Public checklist link - contract (Oct 1)

Owner: "For the food service checklist, create a link (public access) with spicetown.shop with any slug next to it
that opens a public page to submit the kitchen service checklist. We only have 2 part-time service persons and I
don't want them to have full app access. Make sure it's configurable only by admins, in the app and the website."

## Storage (migration 0041, down_revision "0040")
`checklist_public_links`: id, checklist_type (FK-ish to checklist_definitions.checklist_type, unique), slug (e.g.
"kitchen-service"), token (now a 4-digit code - see Short links; older rows: 22 random letters/digits), enabled bool, name_mode ("list" | "free_text"),
allowed_names JSON list, max_per_hour int default 6, created_at/by, updated_at/by, rotated_at/by,
last_used_at. `checklist_submissions` gains nullable `via_public_link` bool (+ the typed `public_name`), additive.
Seed nothing - the admin creates it (but for convenience the lead may create one for `street_kitchen_fsa` disabled).

## Short links (Oct 1, second request)
Owner: "is there a way we can shorten the code for public links, something easily memorizable". Format: a word plus
4 digits, e.g. `https://ops.spicetown.shop/c/kitchen-4821`. No migration (the `token` column holds the code).
- New links and Rotate store a 4-digit code (`secrets`, 1000-9999, never 0000/1111-style, 1234/9876-style or
  1212-style), unique across links (`token` keeps its unique constraint). Rotate always changes the 4 digits.
- Slug ("link word"): default one short word - `kitchen` (Kitchen Closing - Service), `chef` (Kitchen Closing -
  Chef), `store` (Store Closing), else the title's first word (next word / a number if taken). Admins can edit it:
  lowercase letters, digits, single hyphens, 2-20 chars, unique across links.
- A lookup needs BOTH the slug and the code: the public API takes `{key}` = `{slug}-{code}` (the whole last path
  segment of the page URL; the page sends exactly that). The 4 digits alone, or with another word, are the same 404.
  Every link is compared in constant time (`hmac.compare_digest`, no early exit).
- Old links (22-character token, made before this change) keep working until rotated: as `/c/{slug}-{token}`, as
  API key `{slug}-{token}`, and as the bare token (what the first page version sent). Rotating gives them a code.
  Prod on Oct 1 had one: `street_kitchen_fsa`, slug `kitchen-service`, enabled, long token, used once.
- Brute force: per IP, 10 failed lookups (unknown / wrong / disabled / rotated link, API or page) in 10 minutes ->
  429 on every lookup from that IP (valid ones too) until the window drains; plus 200 failed lookups / 10 min for
  all IPs together (bounds forged CF-Connecting-IP). On top of the per-IP request limit, the global limit, the
  link's max_per_hour and the name list.
- Admin responses add `code` (the 4 digits, null for an old long link) and `short` (bool). The website panel and
  the app screen show the URL big without `https://`, copy/share it, and the Rotate confirm says the 4 digits
  change.

## Public URL
`https://ops.spicetown.shop/c/{slug}-{code}` (was `{slug}-{token}`) (server route `GET /c/{path}` - NOT behind login - serves a small
standalone page or the SPA's public route; must work with the existing static-file mount and HashRouter. Unknown,
disabled or rotated links -> a friendly "This link is not active" page, 404 status. No other app data reachable.)

## Public API (no auth; only by a valid enabled link; `{token}` below = `{slug}-{code}`)
- `GET /api/public/checklists/{token}` -> `{checklist_type, title, description, schema (current published, same JSON
  as /api/checklists/schema), name_mode, allowed_names}`.
- `POST /api/public/checklists/{token}/submit` `{name, answers, definition_version?}` -> `{ok: true, all_clear,
  issue_summary}` only (no ids, no other data). Name required (from the list when name_mode=list, else 2-60 chars).
  Same validation/flags/WhatsApp/alert logic as a normal submit; submitted_by_user_id null,
  submitted_by_name = name, via_public_link true. 409 stale version like the normal submit.
- `POST /api/public/checklists/{token}/media` (photo items only; size/type limits as the normal endpoint; media
  attached to the submission; not readable publicly afterwards).
- Rate limit: max_per_hour submissions per link and a per-IP limit (e.g. 20 requests/10 min) -> 429 with a message.
- Cloudflare passes `CF-Connecting-IP`; use it when present.

## Admin API (require_admin)
`GET/PUT /api/checklists/admin/definitions/{type}/public-link` (create/update: enabled, slug, name_mode,
allowed_names, max_per_hour), `POST .../public-link/rotate` (new token; old URL dies), `DELETE .../public-link`.
Response includes the full URL and last_used_at.

## Clients
- Website: in the Checklist builder (admin), a "Public link" panel per checklist: on/off, slug, names list or free
  text, limit, copy URL, QR code (optional, client-side), rotate (confirm), last used. History/detail shows
  "via public link" + name. The public page itself: mobile-first, the checklist form (reuse the staff form
  components), a "Your name" field/picker at the top, success screen "Submitted - thank you", no navigation to the
  rest of the site.
- App (build 6, admins only): on the checklist hub or in More > Admin, a "Public checklist links" screen: list
  checklists with their link status; on/off, copy/share link (share sheet), rotate (confirm), names list editor,
  last used. History cards show "via public link".
