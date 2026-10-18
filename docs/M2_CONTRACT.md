# StoreOps M2 contract

M2 of the multi-store plan (plan section 11.3, MT7a-MT13), built in the
StoreOps repositories. Each batch appends an "As built" section below.

## As built - MT7a part 1 (repositories)

**Repositories (both private, owner `Sundar6264-ux`, default branch `main`):**
- `storeops` (server + web): https://github.com/Sundar6264-ux/storeops
- `storeops-app` (Flutter app): https://github.com/Sundar6264-ux/storeops-app

Local paths: `/Users/sundar/storeops/storeops`, `/Users/sundar/storeops/storeops-app`.
Each has exactly one remote, `origin`, pointing at the StoreOps GitHub
repository. No remote points at Spice Town's GitHub or local folders.

**Secret and data scan of the full history (both repos, before any push).**
Every blob reachable from every ref was checked for private keys, AWS keys,
Google service-account JSON, Google API keys, Anthropic keys, GitHub tokens,
Slack tokens/webhooks, Stripe live keys, long literal password/secret/token
assignments, and for committed `.env`, database, signing-key, Firebase config,
CSV/XLSX/TSV/PDF files. A second pass looked at every quoted
password/secret/token/api-key literal added in any commit.

Server (source: `multistore-m1` @ `f384bdc`, 291 commits):
- business data: 5 CSV exports under `docs/` (category review, fixes approved /
  rejected, merge pass 2, Shaz fix) and `backend/tests/data/similar_products_catalog.tsv`
  (the store's catalog: item ids, names, suppliers, prices);
- a real hard-coded initial admin password in `backend/scripts/seed_admin.py`
  (present since the first commit);
- not secrets: `frontend/.env.mobile` (only the API base URL), the fake
  `SECRET` in `backend/tests/test_sales_accuracy_job.py`, test passwords.

=> **History dropped.** `storeops` is a fresh import: one commit of the
`multistore-m1` tree with the 5 CSVs, the catalog TSV and its labels JSON left
out, and `seed_admin.py` changed to read `SEED_ADMIN_USERNAME` /
`SEED_ADMIN_PASSWORD` from the environment (or prompt). Re-scan of the new
history: no findings beyond the known non-secrets above.

App (source: `ops-fixes` @ `f99c2e0`, 232 commits):
- only finding: `lib/core/notifications/firebase_options.dart` (2 versions)
  holds the Spice Town Firebase project's client config, including a Google
  API key. Firebase client keys are identifiers shipped inside every app build,
  not secrets; the StoreOps app gets its own Firebase project (MT7a app
  identity), which replaces this file.
- no keys, env files, databases, signing files or business data.

=> **History kept** for `storeops-app`.

**Other changes in this batch (server):**
- root `.gitignore`: env files (except `.env.example` and `frontend/.env.mobile`),
  databases, signing keys, Firebase config files, `node_modules` (also as a
  symlink), and the store catalog fixture paths;
- `backend/tests/test_ops_similar_products.py`: the 4 tests that need the
  labelled catalog fixture skip with a reason when it is absent
  (file: 28 passed, 4 skipped).

**Deferred:** a synthetic catalog + labels fixture so the similar-products
quality-floor tests run again in StoreOps.
