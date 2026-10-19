# KloudC RetailOps - App Review notes (DRAFT)

Draft for App Store Connect -> App Review Information (and Google Play's
"App access" instructions). Batch MT13; plan sections 3.1a and 11.3.
**Nothing here has been submitted.** The owner fills the bracketed values and
pastes the "Notes" block. Keep it under Apple's 4,000-character limit (the
block below is about 2,600).

Before submitting, the DEMO store must be live (docs/DEMO_STORE.md) and the
owner checklist done (docs/OWNER_CHECKLIST_STORE_RECORDS.md).

## Sign-in information (App Store Connect fields)

| Field | Value |
|---|---|
| Sign-in required | Yes |
| User name | `appreview` |
| Password | `[DEMO_REVIEW_PASSWORD - from the DEMO server's .env, or <data-dir>/review_password]` |
| Contact | `[owner name, phone, e-mail]` |

The store code is not a field of its own: it goes first in the Notes.

## Notes (paste as is, after filling the brackets)

```
STORE CODE: DEMO   (enter it on the first screen, then sign in)
User name: appreview
Password: (in the Sign-in fields above)

KloudC RetailOps is a back-office app for the staff of independent retail stores
(grocery and convenience): stock and reorder lists, supplier orders,
invoice submission, daily opening/closing checklists, and team tasks and
chat. There is no public sign-up: each store's manager creates its staff
logins, and each store has its own server, chosen by its store code.

The DEMO store is a complete made-up grocery store for review: about 400
products, 90 days of sales, 8 fictitious suppliers, sample invoices (each
PDF is marked SAMPLE), checklists, tasks and team chat. No real person,
business or customer data is in it, and it is rebuilt every night.

The review login can open every screen and try the everyday actions:
submit a test invoice or checklist, post in its team's chat, and start or
complete a task assigned to its team. Those stay inside the demo. An action
that would change the store's records (stock, supplier orders, settings,
other people's items) shows "The demo account can look around but can't
change the store's real data." Nothing is ever sent outside the demo (no
e-mail, WhatsApp, AI request or push notification to anyone else).

Suggested tour (about 3 minutes):
1. Home: today's tasks, orders due and recent invoices.
2. Inventory > Reorder: items running low by supplier; open one supplier's
   order calendar.
3. Invoices: open a sample invoice PDF. "Upload invoice" lets you scan or
   pick a document; your own test uploads stay private to the demo login.
4. Checklists: open yesterday's Closing Checklist and its flagged items.
5. Team: tasks board (open, in progress, done), team chat, announcements.
6. More > About: version, Privacy Policy and Support links (KloudC LLC).
7. More > Account > Delete my account: explains how a store's staff
   account is deleted (the shared demo account itself cannot be deleted).

Features that need a point-of-sale connection (open checks, sales
insights) and the label-printing scanner are not used by the DEMO store,
so they are hidden there.

AI: the optional "Ask Inventory Bot" sends a question and the store data
needed to answer it to Anthropic (Claude). It is off for the DEMO store.
Where a store turns it on, the app shows that notice and asks for
permission before anything is sent.

Permissions, each asked only when the feature is used: camera (scan
invoices and barcodes, task photos), photo library (attach or save a
photo), microphone (voice messages in team chat), Face ID (optional quick
sign-in), notifications (task and order alerts). Location is not used.
```

## Things to check right before submitting

- The DEMO server answers: `GET https://<demo host>/api/app/legal` returns
  `"demo": true`, and the app resolves store code `DEMO` (directory, MT10).
- Sign in with the exact password in the Sign-in fields, on a phone, the day
  of submission. The password survives the nightly reset.
- The tour above matches the build being submitted (screen names change with
  MT9/MT12; update the tour, not the facts).
- If the app's permission strings change (MT7a app identity), update the
  Permissions paragraph to match `ios/Runner/Info.plist`.
- If a reviewer asks for write access, the answer is the DEMO_MODE rule
  (every login is read-only); do not give them a real store's login.
- After approval: the old App Review login in the previous app's store is
  removed by that store's owner (plan 3.1a); nothing in KloudC RetailOps depends on it.

## Google Play "App access"

Same values: "All or some functionality is restricted" -> add instructions:
name "DEMO store", user name `appreview`, password as above, and "Enter
store code DEMO on the first screen, then sign in." Paste the Notes tour in
"Any other information".
