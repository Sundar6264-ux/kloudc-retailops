import datetime as dt
import re
import zoneinfo
from functools import lru_cache
from typing import Optional
from urllib.parse import unquote, urlsplit

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

STORE_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_]{2,23}$")
HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
# StoreOps never serves from this domain or below it (docs/M2_CONTRACT.md
# rule 0.3): the legacy single-store site shares its login cookie across it.
REFUSED_DOMAIN = "spicetown.shop"


def refused_host(value: str) -> bool:
    """True when `value` (a URL, a host, or a cookie domain like ".x.y") is
    REFUSED_DOMAIN or ends with "." + REFUSED_DOMAIN."""
    raw = unquote((value or "").strip().lower()).replace("\\", "/")
    if not raw:
        return False
    if raw.startswith("//"):  # scheme-relative: browsers use the page's scheme
        raw = "https:" + raw
    if "://" in raw:
        host = urlsplit(raw).hostname
    else:
        host = raw.split("/", 1)[0].rsplit("@", 1)[-1].split(":", 1)[0]
    host = (host or "").strip(".")
    return host == REFUSED_DOMAIN or host.endswith("." + REFUSED_DOMAIN)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./storeops.db"

    # --- Store identity and branding (StoreOps M2, docs/M2_CONTRACT.md 1.1) ---
    # One process serves one store. Every default is a product value or blank;
    # a store's own values live in its .env. Derived values (display name,
    # public host, store features...) are computed in app/store/settings_view.py.
    # The store's code, e.g. "XYZ_GROCERY": ^[A-Z0-9][A-Z0-9_]{2,23}$, stored
    # upper case. Blank only in dev / tests (the identity check is skipped).
    store_code: str = ""
    store_name: str = ""  # blank -> product_name
    store_short_name: str = ""  # blank -> the effective store name
    store_address: str = ""  # one line, printed as is (PO PDF, PO email)
    store_phone: str = ""
    store_email: str = ""  # Reply-To on PO emails; blank -> the sending address
    # One phrase for the Ask bot / weekly digest prompts ("a ... store in ...").
    store_description: str = ""
    product_name: str = "StoreOps"
    company_name: str = "KloudC LLC"
    # The product's domain; store hosts are <host-label>.<platform_domain>.
    # ".invalid" never resolves: a placeholder until the owner picks one.
    platform_domain: str = "storeops.invalid"
    # This store's public origin (https://..., no trailing slash). Blank ->
    # https://<store code, lower case, "_" -> "-">.<platform_domain>.
    public_host: str = ""
    # Absolute https:// URL or a path on this server. Blank -> the bundled
    # StoreOps images (/branding/logo.png, /branding/icon.png).
    logo_url: str = ""
    icon_url: str = ""
    # A local image printed on documents (the PO PDF letterhead). Blank -> the
    # bundled StoreOps icon.
    document_logo_file: str = ""
    brand_color: str = "#0F766E"
    brand_secondary_color: str = "#334155"
    currency: str = "USD"
    store_locale: str = "en-US"
    # The DEMO store (plan 3.1a): every user is a demo user (nothing is sent
    # out) and the nightly reset runs.
    demo_mode: bool = False

    # --- Store rules that are settings (not pack) ---
    # The business day ends at this hour (store time): sales before it belong
    # to the previous day (Toast's closeoutHour).
    business_day_cutoff_hour: int = 4
    # YYYY-MM-DD: sales before this date are ignored (onboarding test orders).
    # Blank = no floor.
    first_sales_date: str = ""

    # --- Outside channels ---
    # Labels site origin. Blank = this store has no labels (Scan tab, Print
    # Label History and the website's Label Scanner link are hidden).
    labels_url: str = ""
    # Per-store AI switch: off -> the Ask bot is not offered and the weekly
    # digest has no written summary. ANTHROPIC_API_KEY is still needed for
    # real calls.
    ai_enabled: bool = False
    # Text of the AI notice the app shows before the first Ask bot question
    # (GET /api/app/legal). Blank -> app/legal.py DEFAULT_AI_CONSENT_NOTICE.
    ai_consent_notice: str = ""
    # JSON object: WhatsApp role key -> Whapi chat id, e.g.
    # {"store": "120363000000000000@g.us"}. Wins over the two old
    # WHAPI_*_CHECKLIST_GROUP_ID settings below; rows set on the WhatsApp
    # groups page win over both.
    whatsapp_group_chat_ids: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465
    smtp_ssl: bool = True  # True = implicit TLS (465); False = STARTTLS (587)
    smtp_username: str = ""  # blank -> GMAIL_ADDRESS
    smtp_password: str = ""  # blank -> GMAIL_APP_PASSWORD
    email_from_name: str = ""  # blank -> the effective store name
    email_fallback_domain: str = ""  # Message-ID domain when the sender has none; blank -> platform_domain

    # --- Files, links, app release ---
    # Parent of the upload folders; holds the store identity marker.
    uploads_root: str = "uploads"
    # Read-only store pack override (app/store/pack.py) - parity runs and dev.
    store_pack_file: str = ""
    # One-off: stamp an existing, unstamped database / upload folder with
    # STORE_CODE at startup (app/store/identity.py).
    store_identity_adopt: bool = False
    app_bundle_id: str = "com.kloudc.storeops"
    apple_team_id: str = ""
    android_cert_sha256: str = ""  # comma-separated SHA-256 fingerprints
    privacy_url: str = "https://kloudc.com/kloudc-privacy-policy/"
    terms_url: str = ""
    support_url: str = "https://kloudc.com/kloudc-support/"

    # --- Store directory service only (app/directory, MT10) ---
    directory_stores_file: str = "directory/stores.json"
    directory_rate_per_minute: int = 10
    directory_rate_per_hour: int = 100
    directory_client_ip_header: str = ""  # "CF-Connecting-IP" behind Cloudflare
    directory_extra_api_hosts: str = ""  # comma list of extra allowed api_base_url hosts

    # Which POS this server talks to (app/pos/registry.py, multi-store plan
    # 4.2). "toast" is the only one in M1; blank means toast.
    pos_provider: str = "toast"

    toast_api_base: str = "https://ws-api.toasttab.com"
    toast_client_id: str = ""
    toast_client_secret: str = ""
    toast_restaurant_guid: str = ""

    sales_sync_hour: int = 4
    sales_sync_minute: int = 0
    toast_timezone: str = "America/New_York"
    # Neutral name for the store's IANA time zone (multi-store MT6). Blank =
    # TOAST_TIMEZONE above (the old name, still read). When set, it wins and
    # toast_timezone is set to the same value, so both names always agree.
    store_timezone: str = ""
    # How many trailing days the sync checks for gaps and backfills automatically
    # (e.g. the service was down overnight, or missed a day for any reason).
    sales_sync_backfill_days: int = 14

    # Toast Analytics API (app/services/toast_analytics.py) - separate
    # credentials from the Orders API above (scope enterprise-metrics:read).
    # Blank = not set up: the sales accuracy re-pull is skipped and every
    # page works exactly as before.
    toast_analytics_client_id: str = ""
    toast_analytics_client_secret: str = ""
    toast_analytics_base: str = "https://ws-api.toasttab.com"
    # Sales accuracy re-pull (app/services/sales_accuracy.py): compares
    # Analytics day totals for the last N business days with our stored
    # sales and re-syncs only the dates that differ. Store-local HH:MM times,
    # comma-separated. SALES_REPULL_ENABLED=false turns the job off.
    sales_repull_enabled: bool = True
    sales_repull_days: int = 14
    sales_repull_times: str = "06:30,12:30,17:30,22:30"
    # Items Sold "today" is pulled live from Toast; repeat requests within
    # this many seconds share one pull (Overview + Items Sold + Delivery
    # Review all ask for it).
    live_sales_cache_seconds: int = 60

    # Insights from Toast Analytics (services/insights*.py, docs/
    # INSIGHTS_CONTRACT.md). The nightly job (after the 04:00 closeout)
    # fetches yesterday's reports, re-pulls recent days that changed and
    # fills older days a little at a time. INSIGHTS_NIGHTLY_ENABLED=false
    # turns it off (pages then show only what's cached).
    insights_nightly_enabled: bool = True
    insights_nightly_hour: int = 4
    insights_nightly_minute: int = 30
    # Today's numbers are fetched live; repeat requests within this many
    # seconds share one pull.
    insights_today_cache_seconds: int = 90
    # Our share of Toast's 60-a-day limit on the check and guest payment
    # reports (shared with anything else using the Analytics credentials).
    insights_check_daily_budget: int = 30
    insights_guest_daily_budget: int = 10
    # Loss watch (admins): a check is flagged at or above these amounts, a
    # 100% comp, or a discount/refund/void above N times the employee's own
    # 30-day median.
    loss_watch_large_discount: float = 50.0
    loss_watch_large_refund: float = 25.0
    loss_watch_large_void: float = 50.0
    loss_watch_median_multiple: float = 3.0
    # Odd checks (admins): open longer than this many hours, or a refund at
    # or above this amount.
    odd_check_open_hours: float = 3.0
    odd_check_large_refund: float = 25.0
    # Returning customers: card fingerprints are stored only as an HMAC with
    # this secret. Blank = a random key is created once in
    # INSIGHTS_HMAC_KEY_FILE (chmod 600) and reused. Changing the key starts
    # returning-customer history over.
    insights_hmac_secret: str = ""
    insights_hmac_key_file: str = "insights_hmac.key"

    # Open Orders "all-time" cache (app/services/open_orders.py). The full
    # scan is slow (~100+ Toast calls, minutes) so it only runs once a day;
    # the today-only refresh is cheap (one call) and runs frequently to keep
    # the common case (an order opening/closing today) fresh.
    open_orders_full_scan_hour: int = 4
    open_orders_full_scan_minute: int = 15
    open_orders_today_refresh_minutes: int = 15

    # Master switch for every scheduled job in app/scheduler.py (sales sync,
    # open-orders scans, and their startup catch-ups). On in production. The
    # dev instance sets SCHEDULER_ENABLED=false so it doesn't spend the shared
    # Toast API rate limit - especially under uvicorn --reload, where every
    # file save restarts the app and would re-run the ~100-call full scan.
    scheduler_enabled: bool = True

    # Where the built website is served from (app/main.py). Empty = the
    # repo's frontend/dist. Only for a throwaway test server pointed at a
    # scratch build; production leaves it unset.
    frontend_dist_dir: str = ""

    # Supplier Projection demand method: "availability" (demand counted only
    # over days the item was on the shelf - services/demand_forecast.py) or
    # "legacy" (plain average over calendar days). A kill switch: set
    # PROJECTION_METHOD=legacy in .env and restart to go back instantly.
    projection_method: str = "availability"

    # Inventory from the Toast API (app/services/inventory_api.py,
    # docs/AUTOMATION_BATCH_CONTRACT.md items 1 and 6).
    # INVENTORY_API_SNAPSHOTS: the 04:05 job writes an inventory snapshot
    # built from /stock + /menus (never over a CSV for the same date), and
    # Supplier Projection / Reorder / Fix Issues read live Toast stock on top
    # of the latest snapshot. Off = the job only records a side-by-side
    # comparison with that day's CSV; nothing else changes.
    inventory_api_snapshots: bool = False
    inventory_api_snapshot_hour: int = 4
    inventory_api_snapshot_minute: int = 5
    # "Inventory upload / cost refresh due" alert once the latest CSV upload
    # is this many days old (0 = never).
    inventory_csv_max_age_days: int = 7
    # Hourly barcode re-check from Toast's menu (metadata lastUpdated first,
    # full menu only when it changed). 0 = off.
    menu_barcode_recheck_minutes: int = 60
    # Live stock overlay cache.
    live_stock_cache_seconds: int = 300
    # INVENTORY_API_SNAPSHOTS above is only the starting value of the "Use
    # Toast for inventory" switch: once an admin flips it on the Inventory
    # from Toast page, the page's setting wins (services/inventory_api_switch.py).
    # Readiness (services/inventory_api_readiness.py): "ready" after this many
    # nightly comparisons in a row meet every threshold below.
    inventory_api_ready_nights: int = 7
    inventory_api_ready_qty_pct: float = 99.0  # on hand matched or explained by sales
    inventory_api_ready_price_pct: float = 99.0
    inventory_api_ready_barcode_pct: float = 99.0
    inventory_api_ready_category_pct: float = 100.0  # group + category + subcategory
    inventory_api_ready_max_unexplained_qty: int = 10
    # Also post "ready" to the invoice WhatsApp chat (WHAPI_CHAT_ID). Off by
    # default: the Whapi plan is a 5-chat sandbox - push only.
    inventory_api_ready_whatsapp: bool = False
    # A CSV uploaded after the 04:05 run: compare it with Toast again (once per
    # date) when it lands before this hour, store time - after it, sales since
    # the file aren't synced yet and would read as unexplained. 0 = never.
    inventory_api_late_csv_until_hour: int = 9

    cors_origins: str = "http://localhost:5173"

    # Ask Inventory Bot (app/services/ask_bot.py) only - nothing else depends on this.
    anthropic_api_key: str = ""

    # Session cookie sent to the browser after login. Secure=True (requires
    # HTTPS) is correct for the real deployment (Cloudflare Tunnel terminates
    # HTTPS); flip to False only for local dev over plain http://localhost.
    session_cookie_secure: bool = True
    session_lifetime_days: int = 14
    # Blank (default) scopes the cookie to the exact host that set it. Set to
    # the store's own parent domain (".<store>.<platform domain>") to share the
    # login with the store's labels site. Must start with a dot and match the
    # real parent domain, or browsers reject it as invalid.
    session_cookie_domain: str = ""

    # Invoice Submission (app/services/invoice_submission.py) - replaces the
    # old Google Form -> Sheet -> Apps Script workflow. Blank by default;
    # the feature saves the submission either way, but skips whichever
    # downstream send (WhatsApp/Toast email) isn't configured yet, recording
    # why in that submission's own _error field rather than failing the
    # whole request.
    whapi_api_token: str = ""
    whapi_base_url: str = "https://gate.whapi.cloud"
    # The WhatsApp group chat id messages get sent to (e.g. "12036...@g.us") -
    # not a secret by itself, but there's no way to look it up without the
    # token, so it lives here as its own setting rather than being guessed.
    whapi_chat_id: str = ""

    # Legacy names (still read) for the chat ids of the WhatsApp roles "store"
    # and "kitchen"; WHATSAPP_GROUP_CHAT_IDS above wins over them.
    whapi_spice_town_checklist_group_id: str = ""
    whapi_street_kitchen_checklist_group_id: str = ""

    # Gmail address + an "app password" (not the real account password -
    # generated at myaccount.google.com/apppasswords) used to send the
    # invoice/return/other email to Toast's invoicing inbox, which drafts a bill
    # automatically from it.
    gmail_address: str = ""
    gmail_app_password: str = ""
    toast_invoice_email: str = ""
    # Neutral name for the accounts-payable inbox the invoice PDF is emailed
    # to (multi-store MT6). Unset = TOAST_INVOICE_EMAIL above (the old name,
    # still read). Set but blank = the email channel is off. When set, both
    # names hold the same value.
    ap_inbox_email: Optional[str] = None
    # "Send PO" from the cart (services/po_send.py) emails through the same
    # Gmail account. Its WhatsApp option stays off unless this is true: the
    # Whapi plan is the sandbox (5 chats max), so a new vendor chat gets 402.
    po_whatsapp_enabled: bool = False

    # Public base URL used to build the WhatsApp file link - must be
    # reachable with no login, since WhatsApp group members don't have
    # dashboard accounts. Blank -> the store's public host
    # (app/store/settings_view.effective_public_base_url).
    public_base_url: str = ""
    # Relative to the backend process's working directory, same convention
    # as database_url's "./storeops.db".
    invoice_uploads_dir: str = "uploads/invoices"

    # Marketing flyers library (app/services/flyers.py): a normal folder with
    # one sub-folder per flyer category (the store pack's flyer_categories).
    # Blank = uploads/flyers too.
    flyers_dir: str = "uploads/flyers"

    # Push notifications to the mobile app (app/services/push.py +
    # notifications.py) via Firebase Cloud Messaging. Path to the Firebase
    # service-account JSON (Firebase console -> Project settings -> Service
    # accounts -> Generate new private key), relative to the backend's
    # working directory like invoice_uploads_dir, or absolute. Blank or a
    # missing file = "not set up yet": device registration and prefs still
    # work and the alert jobs still compute (and log) what they would send,
    # but nothing is sent and nothing is recorded as sent - so the first real
    # send after the file appears isn't swallowed by an earlier dry run.
    firebase_credentials_file: str = ""
    # Kill switch for the alert jobs themselves (the notification scheduler
    # in app/scheduler.py). Independent of SCHEDULER_ENABLED on purpose: the
    # alert jobs never call Toast, so dev can run them without spending the
    # shared Toast rate limit that flag exists to protect.
    notifications_enabled: bool = True

    # Team hub (app/team/, docs/TEAM_HUB_DESIGN.md). The *_enabled flags are
    # kill switches and rollout controls only - each feature ships visible in
    # the store build (design §10.2); flipping one off hides it server-side
    # without an app release.
    team_tasks_enabled: bool = True
    team_announcements_enabled: bool = True
    team_chat_enabled: bool = True
    team_poll_interval_s: int = 30
    team_chat_poll_interval_s: int = 5
    team_badge_poll_interval_s: int = 120
    # Change-feed rows (team_changes) older than this are pruned nightly; a
    # client whose cursor is older gets resync_required.
    team_changes_retention_days: int = 30
    # Task proof photos, chat and announcement images - validated and
    # re-encoded server-side (app/team/media.py). Same working-directory
    # convention as invoice_uploads_dir.
    team_uploads_dir: str = "uploads/team"
    # Logins left out of the implicit Everyone team (and so its chat channel
    # and store-wide announcements) - e.g. the App Store reviewer's demo
    # account, which must never read real staff chat. Comma-separated
    # usernames, case-insensitive. Put such an account in its own team.
    team_everyone_exclude: str = "app_store_review,appreview"
    # App Review demo logins (app/demo.py): comma-separated usernames,
    # case-insensitive. A demo user's actions never reach the outside world -
    # invoices and checklists are saved but not sent to WhatsApp/Toast, the
    # Ask bot gives a fixed answer, pushes stay inside the demo user's own
    # teams, chat is limited to people sharing a team, and the labels app
    # refuses to print. A demo user is also always left out of Everyone.
    demo_usernames: str = "app_store_review,appreview"
    # The Team hub's own background jobs (change-feed prune, and later
    # recurring-task generation and reminders). Independent of
    # SCHEDULER_ENABLED like notifications_enabled: none of them call Toast.
    team_jobs_enabled: bool = True
    # Chat retention (design §9.4): messages and their images are hard-deleted
    # after team_chat_retention_days; a deleted message's text/image is kept
    # team_deleted_message_days for moderation, then blanked.
    team_chat_retention_days: int = 365
    team_deleted_message_days: int = 30
    # Automatic tasks (app/team/auto_tasks.py, design Phase 3): made IN
    # ADDITION to the matching push alerts, one switch per rule. Each rule's
    # team is looked up by name; a missing/archived team (or one with no
    # members) skips the rule.
    # Closing-checklist tasks: OFF since Oct 15 (owner: the 9:30 PM push and
    # Home's checklist card already cover it). While off, any still open are
    # closed as "Missed" by the task job.
    team_autotask_checklist_enabled: bool = False
    team_autotask_checklist_team: str = "Managers"
    team_autotask_invoice_enabled: bool = False
    team_autotask_invoice_team: str = "Managers"
    team_autotask_order_enabled: bool = False
    team_autotask_order_team: str = "Managers"
    # Vendors have minimum orders: no "Place the order" task for fewer items
    # than this (same threshold as Reorder's VENDOR_MIN_ORDER_ITEMS).
    team_autotask_order_min_items: int = 5
    # Urgent automatic tasks also go to the location's checklist WhatsApp
    # group when some assignee has no phone registered for pushes.
    team_whatsapp_fallback: bool = False

    # "Update required" gate (GET /api/app/config): an installed build whose
    # build number is below these is blocked with a link to its store. 0 =
    # never block.
    min_supported_build_ios: int = 0
    min_supported_build_android: int = 0
    # Set APP_STORE_URL to the real listing (https://apps.apple.com/app/id<N>)
    # in .env; blank = null in JSON. PLAY_STORE_URL blank = the Play listing
    # of APP_BUNDLE_ID.
    app_store_url: str = ""
    play_store_url: str = ""

    @field_validator("store_code")
    @classmethod
    def _store_code(cls, value: str) -> str:
        value = (value or "").strip().upper()
        if value and not STORE_CODE_RE.match(value):
            raise ValueError(
                f"STORE_CODE {value!r} is not valid: 3-24 characters, letters, digits and _ (starting with a letter or digit)"
            )
        return value

    @field_validator("brand_color", "brand_secondary_color")
    @classmethod
    def _color(cls, value: str) -> str:
        value = (value or "").strip()
        if not HEX_COLOR_RE.match(value):
            raise ValueError(f"{value!r} is not a colour like #0F766E")
        return value.upper()

    @field_validator("first_sales_date")
    @classmethod
    def _first_sales_date(cls, value: str) -> str:
        value = (value or "").strip()
        if value:
            try:
                dt.date.fromisoformat(value)
            except ValueError:
                raise ValueError(f"FIRST_SALES_DATE {value!r} is not a YYYY-MM-DD date") from None
        return value

    @field_validator("business_day_cutoff_hour")
    @classmethod
    def _cutoff(cls, value: int) -> int:
        if not 0 <= int(value) <= 23:
            raise ValueError("BUSINESS_DAY_CUTOFF_HOUR must be 0-23")
        return int(value)

    @field_validator("platform_domain", "public_host", "public_base_url", "labels_url", "session_cookie_domain")
    @classmethod
    def _never_legacy_domain(cls, value: str, info) -> str:
        value = (value or "").strip()
        if value and refused_host(value):
            raise ValueError(
                f"{info.field_name.upper()} {value!r} is under a domain StoreOps never serves from "
                f"(docs/M2_CONTRACT.md rule 0.3)"
            )
        return value.rstrip("/") if info.field_name != "session_cookie_domain" else value

    @model_validator(mode="after")
    def _neutral_names(self) -> "Settings":
        """Multi-store MT6: the neutral setting wins when given, else the old
        Toast-named one is used; afterwards both attributes hold the same
        value, so code reading either name sees the same store."""
        if (self.store_timezone or "").strip():
            self.toast_timezone = self.store_timezone.strip()
        self.store_timezone = self.toast_timezone
        if self.ap_inbox_email is not None:
            self.toast_invoice_email = self.ap_inbox_email.strip()
        self.ap_inbox_email = self.toast_invoice_email
        try:
            zoneinfo.ZoneInfo(self.toast_timezone)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"STORE_TIMEZONE {self.toast_timezone!r} is not a known time zone") from None
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def frontend_dist_dir():
    """The built website directory (Settings.frontend_dist_dir, else
    frontend/dist next to the backend)."""
    import pathlib

    configured = get_settings().frontend_dist_dir
    if configured:
        return pathlib.Path(configured).resolve()
    return pathlib.Path(__file__).resolve().parents[2] / "frontend" / "dist"
