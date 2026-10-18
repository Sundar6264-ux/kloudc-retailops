# Checklist names - proposal (not applied)

Owner, Sep 30: "You can rename checklists but tell me before publishing." Nothing below is live. The three
checklists keep their current names until you say go.

## Current vs proposed

| Checklist (id) | Current title | Current short name | Proposed title | Proposed short name |
|---|---|---|---|---|
| `spice_town_closing` | Spice Town - Daily Closing Checklist | Spice Town | **Store Closing** | Store Closing |
| `street_kitchen_closing` | Street Kitchen – Chef Closing Checklist | Street Kitchen | **Kitchen Closing - Chef** | Kitchen Closing - Chef |
| `street_kitchen_fsa` | Street Kitchen - Food Service Associate Closing | Street Kitchen FSA | **Kitchen Closing - Service** | Kitchen Closing - Service |

Released app (build 5) tiles, hardcoded in the app, for comparison: "Spice Town Closing Checklist", "Street
Kitchen Chef Closing Checklist", "Street Kitchen FSA Closing Checklist".

## Why

- **Short.** The longest proposed name is 25 characters. Today's are 36 to 47, so they get cut off on a
  phone tile, a history row and a notification.
- **Parallel.** Every name is "<where> Closing", plus " - <who>" when the same place has more than one. You
  can tell them apart from the first word (Store / Kitchen), and the two kitchen ones from the last word
  (Chef / Service).
- **Says what it is, not how it's made.** "Daily ... Checklist" is implied: everything on the Daily
  Checklists page is a daily checklist.
- **One dash style.** The chef checklist's title uses a long dash (–). The others use a plain hyphen. The
  new names use a plain hyphen only.
- **"Service" rather than "FSA" or "Food Service Associate".** "FSA" is an internal abbreviation that new
  staff won't know. "Food Service Associate" is too long for a tile. "Service" matches what the person does
  (the front service area: counter, food stand, chaat station). If staff always say "FSA", then
  "Kitchen Closing - FSA" is the alternative.
- **Short name = title.** They're already short, so the nightly task reads naturally: "Submit the Store
  Closing checklist". The task-title code no longer adds "closing" twice when the name already contains it.

## What changes when it's published

A rename changes only the title and short name. Questions, versions, stored submissions and answers stay as
they are. It is not a new version, and it takes one step per checklist (Checklists page settings, or `PATCH
/api/checklists/admin/definitions/{type}` with `title` and `short_name`). It can be undone the same way.

The new name then shows up in these places right away:
- The form header in the app and on the website.
- The history and detail pages. Old submissions show the current name too.
- The WhatsApp message header ("✅ *Store Closing*"). Staff searching the groups for the old name won't
  find new messages.
- The 9:30 PM notification: "Not submitted yet: Store Closing, Kitchen Closing - Chef and Kitchen
  Closing - Service".
- The nightly manager task: "Submit the Kitchen Closing - Chef checklist".
- The website Overview, once the website reads names from the server.

These places don't change until app build 6: build 5's hub tiles and its Home "not submitted yet" reminder
use names written into the app. Build 5 users would see the old tile name and then the new name at the top
of the form. That mismatch is small, but you may want to publish the rename when build 6 goes out.

## To publish

Tell me "rename the checklists", or which of the names to change, and I'll apply them on dev first and
then on prod.
