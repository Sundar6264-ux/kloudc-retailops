# Dedicated hosting

One store on its own server, only with master approval and only while
`DEDICATED_HOSTING_ENABLED=true` (off by default). The whole procedure
(approve, prepare, copy, flip, verify, roll back) is in
[DEDICATED_HOSTING_RUNBOOK.md](DEDICATED_HOSTING_RUNBOOK.md); the rules are
in [PLATFORM_CONTRACT.md](PLATFORM_CONTRACT.md) section 12.

Code: `backend/app/platform/hosting.py` (request -> approve -> start ->
complete / rollback, the block marked `DEDICATED HOSTING`) and track B's
`backend/app/tenancy/routing.py` (`node_serves`, `store_moved`).
