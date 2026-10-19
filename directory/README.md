# KloudC RetailOps store directory

The directory tells the KloudC RetailOps app which server a store code belongs to.
Staff type a code (or scan a QR code), the app asks
`GET https://directory.<PLATFORM_DOMAIN>/v1/stores/<CODE>`, and gets back the
store's server address, name, colours, time zone and minimum app build.
The full interface is in `docs/M2_CONTRACT.md`, section 4.

It is built from one file, `directory/stores.json`, in three forms that
return the same bytes:

| Form | Where | Used for |
|---|---|---|
| Cloudflare Worker (`directory/worker/`) | `https://directory.<PLATFORM_DOMAIN>` | Production |
| Static files (Cloudflare Pages) | `https://static.<PLATFORM_DOMAIN>/v1/stores/<CODE>.json` | Fallback when the Worker is down |
| Python service (`backend/app/directory/`) | `uvicorn app.directory.main:app --port 8090` | Tests, local development, self-hosting |

`backend/tests/test_directory.py` runs the Python service and the Worker
side by side and checks that they answer the same, header for header.

**Nothing here has been deployed.** Deploying needs your Cloudflare account
and the real domain. The steps are below.

## The stores file

- `stores.example.json` (in git) holds the three fake test stores.
- `stores.json` (not in git, see `.gitignore`) is the real list. Start it by
  copying the example file and replacing the stores.

Each store needs:

- `code`: 3-24 capital letters, digits or `_`.
- `name`.
- `api_base_url`: `https://<host>` with no path. The host must end in
  `.<PLATFORM_DOMAIN>` or be listed in `DIRECTORY_EXTRA_API_HOSTS`.
- `status`: `active` or `maintenance`. With `maintenance`, the app shows
  `message` and does not log in.
- `branding`: short name, logo and icon URLs, two `#RRGGBB` colours, an IANA
  time zone and the POS name.
- `min_build`: the lowest iOS and Android build that may log in. It is
  optional and defaults to 0.

To retire a store, delete its entry. Its code then answers 404, the same as a
code that never existed.

The whole file is refused if any entry is wrong. A bad file stops the Python
service from starting, and `build_static` exits with code 2 and lists every
problem. Anything under the legacy store's domain is always refused.

Check a file without deploying:

```sh
cd backend
python -m app.directory.build_static --stores ../directory/stores.json --out /tmp/directory-check
```

## Settings

They are read from the environment, or from `backend/.env` once MT7's
settings fields are merged.

| Variable | Default | Notes |
|---|---|---|
| `PLATFORM_DOMAIN` | `retailops.invalid` | A placeholder until you choose the real domain |
| `DIRECTORY_STORES_FILE` | `directory/stores.json` | |
| `DIRECTORY_RATE_PER_MINUTE` / `_PER_HOUR` | `10` / `100` | Per client IP; `0` turns a window off |
| `DIRECTORY_CLIENT_IP_HEADER` | blank | Set to `CF-Connecting-IP` behind Cloudflare. The Worker always uses it |
| `DIRECTORY_EXTRA_API_HOSTS` | blank | A comma-separated list of extra allowed server hosts |
| `APPLE_TEAM_ID` | blank | While blank, `apple-app-site-association` lists no app |
| `ANDROID_CERT_SHA256` | blank | Comma-separated `AB:CD:...` fingerprints. While blank, `assetlinks.json` lists none |
| `APP_BUNDLE_ID` | `com.kloudc.retailops` | |
| `APP_STORE_URL` / `PLAY_STORE_URL` | blank / from the bundle id | Links on the `/s/<code>` page. The App Store URL must be a real listing (`.../id<N>`) |

These values are baked into the static files and the Worker data when you
build them. Rebuild and redeploy after changing any of them.

## Run it locally

```sh
cd backend
DIRECTORY_STORES_FILE=../directory/stores.example.json \
  python -m uvicorn app.directory.main:app --port 8090
curl -i http://127.0.0.1:8090/v1/stores/demo
```

Tests (the Worker tests need Node 18 or later):

```sh
cd backend && python -m pytest -q tests/test_directory.py
cd directory/worker && node --test test/directory.test.mjs
```

## Deploy (owner steps; not done yet)

You need:

- the real platform domain, added to Cloudflare with its DNS on Cloudflare;
- a Cloudflare account;
- Node 18 or later on the computer you deploy from. Wrangler runs through
  `npx`, so nothing has to be installed in this repo.

1. **Set the domain.** Choose the domain and put it in `backend/.env` as
   `PLATFORM_DOMAIN=<domain>`. Use the same domain for the app's
   `PLATFORM_DOMAIN` build define.
2. **Write the real stores file.** Copy `stores.example.json` to
   `stores.json` and fill in the real stores. Every `api_base_url` must be
   under the domain from step 1.
3. **Build both outputs:**
   ```sh
   cd backend
   python -m app.directory.build_static --stores ../directory/stores.json \
     --out ../directory/dist --worker-data ../directory/worker/src/data.json
   ```
4. **Deploy the Worker:**
   ```sh
   cd directory/worker
   cp wrangler.toml.example wrangler.toml
   # In wrangler.toml, replace retailops.invalid with your domain in `routes`,
   # and set account_id (or export CLOUDFLARE_ACCOUNT_ID).
   npx wrangler login
   npx wrangler deploy --dry-run   # check the bundle first
   npx wrangler deploy
   ```
   `custom_domain = true` makes Cloudflare create the DNS record for
   `directory.<domain>`. The `[[ratelimits]]` block uses Cloudflare's Workers
   rate-limit binding, which allows 10 lookups a minute per IP across all
   Worker copies. If `wrangler deploy` rejects that block, check Cloudflare's
   current rate-limit binding syntax, or delete the block: the Worker's own
   per-copy limit and the rule in step 6 still apply.
5. **Deploy the static fallback** to Cloudflare Pages:
   ```sh
   npx wrangler pages project create retailops-directory-static   # first time only
   npx wrangler pages deploy ../dist --project-name retailops-directory-static
   ```
   Then, in the Cloudflare dashboard, open the Pages project, go to Custom
   domains and add `static.<domain>`. `dist/404.html` must stay in the
   upload. Without it, Pages answers unknown codes with 200 instead of 404.
6. **Add a rate-limit rule.** In the dashboard, go to Security, then WAF,
   then Rate limiting rules. Add a rule for requests whose URI path starts
   with `/v1/stores/` on `directory.<domain>`, counted per IP, that blocks
   after the limit. Pick the closest period your plan allows to 10 requests
   a minute.
7. **Serve the association files on the bare domain.** The same
   `/.well-known/apple-app-site-association`, `/.well-known/assetlinks.json`
   and `/s/*` must answer on `https://<domain>` itself. QR codes link to
   `https://<domain>/s/<CODE>`. Either add `{ pattern = "<domain>",
   custom_domain = true }` to the Worker's `routes`, or add `<domain>` as a
   second custom domain on the Pages project. Each store server serves the
   same two files too. They claim only `/s/*`: public checklist links
   (`/c/...`) must keep opening in the browser, because the app has no page
   for them. Don't add `/c/` paths to the app's Android intent filter
   either until the app can show them.
8. **Fill in the app ids.** Once the Apple Team ID and the Play upload-key
   fingerprint exist, set `APPLE_TEAM_ID` and `ANDROID_CERT_SHA256`, then
   repeat steps 3-5.
9. **Check the live directory:**
   ```sh
   curl -i https://directory.<domain>/v1/stores/<CODE>
   curl -i https://static.<domain>/v1/stores/<CODE>.json
   curl -i https://<domain>/.well-known/apple-app-site-association
   ```
   Each should answer 200 with `content-type: application/json`. An unknown
   code should answer 404.

To change the list later, edit `stores.json` and repeat steps 3-5. The
Worker serves the new data as soon as it is deployed. The app keeps 200
answers cached for up to 5 minutes.
