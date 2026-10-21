# Parity investigation (2026-10-21)

Plain summary: **the pilot was right when it copied the history; it simply
never re-checks closed days, because the pilot store has no Toast Analytics
keys entered.** The reference system re-checked those days after Toast
changed them (late edits) and the pilot did not. No bug in the sales sync,
the void/refund handling, the business-date cutoff or the modifier lines.

How it was checked: read-only, aggregate queries on both sides
(`default_transaction_read_only=on` on the pilot's PostgreSQL, `mode=ro` on
the reference SQLite). Lines were compared by Toast selection GUID (sets and
amounts only). No customer data is in this file; orders are named A, B, C.

## 1. What the 07:30 report on 2026-10-20 said

| Day | Metric | Reference | Pilot |
|---|---|---|---|
| 2026-10-15 | lines / qty / net | 915 / 1274.205 / 5152.34 | 912 / 1271.015 / 5139.94 |
| 2026-10-18 | lines / qty / net / tax | 1121 / 1421.795 / 6277.87 / 82.87 | 1119 / 1419.795 / 6423.13 / 82.39 |

The report of 2026-10-19 (14 days, 2026-10-05..10-18) was clean. There is no
newer report yet: the next run is 07:30 today (this was written at 00:45).

## 2. Line-by-line (by GUID)

All other days 2026-10-06..2026-10-17 match line for line (same GUIDs, same
quantity, net, tax, void flag).

**2026-10-15** - 3 lines exist only in the reference, all on one check
(order C). Toast added them to a check that was already closed and synced.
The reference wrote them at **06:30 store time on 2026-10-20** (its sales
re-pull run). Their sum is exactly the difference: net 12.40, qty 3.19,
3 lines, tax 0.

**2026-10-18** - two orders were edited after the day was synced:

| Order | Change in Toast (seen in the reference) | Effect on pilot - reference |
|---|---|---|
| A | all 9 lines brought to net 0.00 (fully comped/discounted later; lines not voided, now paid) | +155.60 net |
| B | 2 lines added (one taxed 0.48) and 2 lines re-priced 0.99 -> 0.94 | -10.44 net + 0.10 net, -2 lines, -2 qty, -0.48 tax |

155.60 + 0.10 - 10.44 = **145.26** = 6423.13 - 6277.87. Tax and line counts
match the same way. The reference made these changes at **22:30 store time
on 2026-10-19** (its re-pull run). In the pilot, both orders still show the
lines as unpaid (the payment landed later).

**2026-10-19** (not in a report yet; it will be in today's 07:30 report):
4 lines only in the reference (+14.78 net, written by its 22:30 re-pull on
2026-10-20) and 13 lines whose net and paid state changed later (pilot
higher by 145.66). Expect today's report to list 2026-10-15, 10-18 and 10-19.

## 3. Root cause

1. Both systems sync each closed business date once, at night.
2. Toast keeps changing closed days (items added to a closed check, a late
   comp or discount, a late payment) - up to several days later.
3. The reference catches these with its **sales re-pull** (06:30, 12:30,
   17:30, 22:30): it asks Toast Analytics for each of the last 14 days and
   re-syncs only the days that changed.
4. The pilot has the same job, scheduled per store and running on time, but
   every run logs **"Sales accuracy re-pull skipped: Toast Analytics is not
   configured"**: the pilot store's Toast connection has no Analytics client
   ID or secret (checked: the stored Toast credential has no
   `analytics_client_id`). Without them the job does nothing at all - not
   even the "unpaid lines" re-sync, which doesn't need Analytics.
5. The first report was clean because the pilot's history was imported from
   the reference on 2026-10-19 (the imported lines carry the reference's own
   timestamps). Every Toast edit after that import reached only the reference.

Ruled out: void handling (no void flag differs), refunds (none in these
lines), business-date cutoff (no line moved between days), modifier lines
(every differing line is a top-level item), pilot sync errors (none logged).
Side note, reference only: its re-sync updates amounts without touching the
line's `updated_at`; harmless, no action.

## 4. Fix plan

**Configuration (owner, 2 minutes) - this alone fixes it.** In the pilot,
Store setup > Integrations > Toast, enter the **Toast Analytics client ID and
secret** (the same pair the reference uses). The next re-pull (06:30, 12:30,
17:30 or 22:30) sees that 2026-10-15/18/19 differ from Toast and re-syncs
them; the following 07:30 report should be clean.

**Code (track P, so it can't happen silently again):**

1. **Re-pull without Analytics keys**: instead of skipping, re-sync the last
   7 closed business days once a day (06:30 run) and, at every run, any day
   that still holds unpaid lines synced more than a few hours ago. About 30
   small Toast requests a day. With Analytics keys the smarter job runs as
   today.
2. **Say it out loud**: the parity report adds a plain problem line
   ("Sales double-check is off: Toast Analytics keys are missing") and, per
   differing day, counts only of lines found only on one side / changed;
   `check-config` warns for every Toast store without Analytics keys; the
   Integrations screen shows "Add your Toast Analytics keys so late edits
   are caught".
3. Tests with fakes: a late-added line and a late comp on a closed day are
   picked up by the fallback; the parity report names the missing keys.

**Go-live gate** (decision, reversible): after the keys are entered, switch
store staff over after **3 clean 07:30 reports in a row** (the 14 days before
the import were exact and the cause is understood), rather than restarting a
full 7-day count.
