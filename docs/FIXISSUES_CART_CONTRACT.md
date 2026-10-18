# Contract: Fix Issues "more time" + came-back flag, cart removal by admins (Sep 30)

## 1. Fix Issues
- Every close reason gets a glossary definition (`backend/app/glossary.py`, key `reason_<issue_type>_<reason>` or a
  shared `reason_<reason>` when the meaning is the same across lists) saying what it means AND what happens next
  (stays closed for good / re-checked against the next upload / comes back after N days / one occurrence only).
- Reason `give_time` ("New item - giving it more time", Dead - newly arrived) = SNOOZE for 7 days
  (`SNOOZE_DAYS = {"give_time": 7}` - a mapping so other reasons can be given a snooze later). When a snoozed
  closure is older than its snooze: if the item is still detected, it moves back to OPEN (delete/deactivate the
  resolution, record a reopen in history), else it stays out (gone).
  The reopen happens in a daily job (09:00 America/New_York, same scheduler style as other alerts, respects
  SCHEDULER_ENABLED/NOTIFICATIONS_ENABLED) AND lazily when the list is read (so the list is right even if the
  job hasn't run).
- One combined push per day (alert key `fix_issues_reopened`, dedupe per day, quiet hours respected) to admins +
  users with the `inventory_reports` feature: title "Fix Issues: N items back", body lists up to 3 names "+ N more",
  tap opens Fix Issues. Nothing sent when N = 0. Registered in the notification settings list like other alerts.
- "Came back" flag: every open issue row gets additive fields
  `times_closed` (int), `came_back` (existing object, keep its shape) and new `reopened`:
  `{"at": iso, "reason": key, "reason_label": text, "how": "snooze_ended" | "fixed_but_still_showing" | "detected_again"}`
  or null. Rows with `reopened` or `came_back` count as "came back". Summary gets an additive `came_back_count` per type.
  `GET /api/issues/{type}?came_back=1` filters to them. History table keeps every close/reopen (no deletes):
  add what's needed (migration 0040, down_revision "0039") - e.g. `issue_resolution_events`.
- Closing again a came-back item works as before.

## 2. Cart removal - admins only
- `DELETE /api/ordering/cart/items/{id}`, `DELETE /api/ordering/cart?supplier=`, and any qty update that removes a
  line -> admin only (403 for others with detail "Only an admin can remove items from the cart. Use Request
  removal."). Adding and changing qty (>= 1) stays as today. "Mark placed" (which clears that supplier's cart) stays
  allowed for cart users.
- Requests: `POST /api/ordering/cart/items/{id}/removal-request` `{reason?}` (cart users) -> marks the line
  (`removal_requested_at/by/reason`, additive on cart items, migration 0040). `DELETE .../removal-request` (requester
  or admin) cancels. Admin: `POST .../removal-request/approve` (deletes the line) / `.../reject`.
- Cart GET items gain additive `removal_requested` `{at, by, reason}` | null and top-level `can_remove: bool` for
  the current user. One push to admins when a request is made (alert key `cart_removal_request`, combined if
  several within 10 minutes, quiet hours).
- Build 5 (released) non-admins hitting delete get the 403 message as a snackbar - acceptable.
