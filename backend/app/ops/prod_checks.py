"""Production start-up checks (docs/PILOT_RUNBOOK.md "Start-up checks").

With APP_ENV=production (the default) the server refuses to start when its
configuration is unsafe; anything else (development, test, the preview) only
logs the findings. ``python -m app.ops check-config`` prints them without
starting anything (the pilot installer runs it first).

Errors (refuse to start in production):
- SESSION_COOKIE_SECURE=false (the login cookie would travel over plain http);
- CORS_ORIGINS with "*" or "null" (credentials are allowed on CORS answers);
- PRIVATE_PACKS_DIR inside a git checkout (private packs live outside git);
- multi mode: no PostgreSQL platform database; no master key ring
  (RETAILOPS_SECRET_KEYS); a development key ring (an id starting "dev", or
  a key that is also in the local dev ring file); PLATFORM_REQUIRE_TOTP=false
  (masters must use two-step sign-in); SIGNUP_ENABLED=true with no master
  account that could approve a sign-up (sign-ups always wait for a master's
  approval - there is no auto-approve).
APP_ENV: only development / dev / test are not production; any other value
(a typo, "staging") is production (and warned about), so a mistyped value
never switches these checks off.
Warnings (logged, never fatal): unknown APP_ENV, SECURITY_HEADERS=off, placeholder domain, no Firebase file, no
client-IP header behind a proxy, no website build, no STORE_CODE (single mode),
DEDICATED_HOSTING_ENABLED, no LOG_DIR.
Going public (pre-production 2026-10-21, docs/GO_LIVE_RUNBOOK.md), warnings:
PLATFORM_DOMAIN still the placeholder; PLATFORM_PUBLIC_URL not https or its
origin missing from CORS_ORIGINS; PUBLIC_HOST blank (multi mode; PUBLIC_BASE_URL in single
mode); TERMS_URL blank; a
Toast store without Analytics keys (late Toast edits caught only by the daily
re-check); no off-site backup (BACKUP_OFFSITE_DIR).
"""

import logging
import os
import pathlib
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Finding:
    level: str  # "error" | "warning"
    key: str
    message: str

    def line(self) -> str:
        return f"{self.level.upper()} {self.key}: {self.message}"


class ProductionConfigError(RuntimeError):
    def __init__(self, findings: List[Finding], app_env: str = "production"):
        self.findings = findings
        lines = "\n  ".join(f.line() for f in findings)
        super().__init__(
            "KloudC RetailOps refuses to start in production with this configuration "
            f"(APP_ENV={app_env or 'production'!s} counts as production):\n  {lines}\nFix the settings above "
            "(docs/PILOT_RUNBOOK.md), or use APP_ENV=development on a developer machine."
        )


def is_production(settings=None) -> bool:
    from app.platform.config import env_is_production

    if settings is None:
        from app.config import process_settings

        settings = process_settings()
    return env_is_production(getattr(settings, "app_env", "production"))


def _keyring_findings(settings) -> List[Finding]:
    from app.platform import crypto

    out: List[Finding] = []
    try:
        ring = crypto.load_keyring()
    except crypto.KeyRingError as exc:
        return [Finding("error", "RETAILOPS_SECRET_KEYS", f"no master key ring ({exc})")]
    except Exception as exc:  # noqa: BLE001 - a malformed ring
        return [Finding("error", "RETAILOPS_SECRET_KEYS", f"the key ring does not load ({type(exc).__name__})")]
    if ring.source == "dev-file":
        out.append(Finding("error", "RETAILOPS_SECRET_KEYS", "the development key ring is in use"))
        return out
    dev_ids = [kid for kid in ring.ids() if kid.lower().startswith("dev")]
    if dev_ids:
        out.append(Finding("error", "RETAILOPS_SECRET_KEYS", f"development key id(s) {', '.join(dev_ids)} in the ring"))
    try:
        dev_file = crypto._dev_file()  # noqa: SLF001 - the one place that knows the dev ring's path
        if dev_file.is_file():
            dev_keys = {key for _, key in crypto.parse_ring(dev_file.read_text(encoding="utf-8").strip())}
            if any(ring.key(kid) in dev_keys for kid in ring.ids()):
                out.append(Finding("error", "RETAILOPS_SECRET_KEYS", "a key of the local development ring is in the production ring"))
    except Exception:  # noqa: BLE001 - an unreadable dev file is not this check's business
        pass
    return out


def _masters_exist() -> Optional[bool]:
    """True/False, or None when the platform database can't be asked."""
    try:
        from sqlalchemy import text

        from app.platform.db import platform_engine

        with platform_engine().connect() as conn:
            n = conn.execute(text("SELECT count(*) FROM platform_users WHERE is_active")).scalar()
        return bool(n)
    except Exception:  # noqa: BLE001
        return None


def _toast_stores_without_analytics() -> Optional[List[str]]:
    """Codes of active stores whose saved Toast connection has no Analytics
    client ID, or None when the platform database can't be asked."""
    try:
        import json

        from sqlalchemy import text

        from app.platform.db import platform_engine

        with platform_engine().connect() as conn:
            rows = conn.execute(text(
                "SELECT s.code, c.public_fields FROM store_credentials c JOIN stores s ON s.id = c.store_id "
                "WHERE c.kind = 'toast' AND s.status = 'active' ORDER BY s.code"
            )).all()
    except Exception:  # noqa: BLE001
        return None
    out = []
    for code, public in rows:
        try:
            fields = json.loads(public or "{}")
        except ValueError:
            fields = {}
        if not str(fields.get("analytics_client_id") or "").strip():
            out.append(code)
    return out


def _origin(url: str) -> str:
    from urllib.parse import urlsplit

    parts = urlsplit(url.strip())
    return f"{parts.scheme}://{parts.netloc}".lower() if parts.scheme and parts.netloc else ""


def go_public_findings(s, *, multi: bool, check_database: bool = True) -> List[Finding]:
    """Warnings for a server with a public address (never fatal: the pilot
    runs on its private address until the owner switches)."""
    out: List[Finding] = []
    public = str(getattr(s, "platform_public_url", "") or "").strip()
    domain = str(getattr(s, "platform_domain", "") or "").strip()
    origins = [o.strip().rstrip("/").lower() for o in str(getattr(s, "cors_origins", "") or "").split(",") if o.strip()]
    if multi and public and (not domain or domain.endswith(".invalid")):
        out.append(Finding("warning", "PLATFORM_DOMAIN",
                           f"still the placeholder {domain or 'retailops.invalid'!r}: set kloudc.com (email "
                           "Message-IDs and app links use it)"))
    if public:
        if not public.lower().startswith("https://"):
            out.append(Finding("warning", "PLATFORM_PUBLIC_URL", "not an https:// address"))
        elif _origin(public) and _origin(public) not in origins:
            out.append(Finding("warning", "CORS_ORIGINS",
                               f"does not list the public address {_origin(public)} (the website there can't sign in)"))
        if multi and not str(getattr(s, "public_host", "") or "").strip():
            # PUBLIC_BASE_URL is a per-store setting in multi mode; every
            # store's links fall back to PUBLIC_HOST, else to
            # https://<store>.<PLATFORM_DOMAIN>, which the tunnel doesn't serve.
            out.append(Finding("warning", "PUBLIC_HOST",
                               f"blank: store links (WhatsApp files, checklist and invoice links) would point at "
                               f"https://<store>.{domain or 'retailops.invalid'}; set it to {_origin(public) or public}"))
        elif not multi and not str(getattr(s, "public_base_url", "") or "").strip():
            out.append(Finding("warning", "PUBLIC_BASE_URL", "blank: file links in WhatsApp messages have no address"))
        if not str(getattr(s, "terms_url", "") or "").strip():
            out.append(Finding("warning", "TERMS_URL", "blank: sign-in and About show no Terms of Service link"))
    if not os.environ.get("BACKUP_OFFSITE_DIR", "").strip():
        out.append(Finding("warning", "BACKUP_OFFSITE_DIR",
                           "no off-site backup: backups stay on this machine only (docs/GO_LIVE_RUNBOOK.md)"))
    if multi and check_database:
        missing = _toast_stores_without_analytics()
        if missing:
            out.append(Finding("warning", "TOAST_ANALYTICS",
                               f"store(s) {', '.join(missing)} have no Toast Analytics keys: late Toast edits are "
                               "caught only by the daily re-check (Store setup > Integrations > Toast)"))
    return out


def check_config(settings=None, *, check_database: bool = True) -> List[Finding]:
    """Every finding for these settings (production rules applied whatever
    APP_ENV is; ``enforce`` decides what is fatal)."""
    from app.config import frontend_dist_dir, process_settings
    from app.platform.config import NON_PRODUCTION_ENVS, PRODUCTION_ENVS, require_totp

    s = settings if settings is not None else process_settings()
    out: List[Finding] = []
    multi = str(getattr(s, "tenancy_mode", "single")) == "multi"

    if not bool(getattr(s, "session_cookie_secure", True)):
        out.append(Finding("error", "SESSION_COOKIE_SECURE", "must be true (the login cookie is https-only)"))
    origins = [o.strip() for o in str(getattr(s, "cors_origins", "") or "").split(",") if o.strip()]
    if "*" in origins:
        out.append(Finding("error", "CORS_ORIGINS", '"*" with credentials lets any site use a signed-in session'))
    if any(o.lower() == "null" for o in origins):
        out.append(Finding("error", "CORS_ORIGINS",
                           '"null" with credentials lets sandboxed frames and local files use a signed-in session'))
    env = str(getattr(s, "app_env", "") or "").strip().lower()
    if env and env not in PRODUCTION_ENVS + NON_PRODUCTION_ENVS:
        out.append(Finding("warning", "APP_ENV",
                           f"{env!r} is not production / development / dev / test: treated as production"))
    if str(getattr(s, "security_headers", "enforce") or "enforce").strip().lower() == "off":
        out.append(Finding("warning", "SECURITY_HEADERS", "off: no CSP, frame or HSTS headers are sent"))
    packs_dir = str(getattr(s, "private_packs_dir", "") or "").strip()
    if packs_dir:
        from app.ops.private_pack import git_work_tree

        tree = git_work_tree(packs_dir)
        if tree is not None:
            out.append(Finding("error", "PRIVATE_PACKS_DIR",
                               f"inside the git checkout {tree}: private packs live outside git"))

    if multi:
        url = str(getattr(s, "platform_database_url", "") or "").strip()
        if not url.startswith("postgresql"):
            out.append(Finding("error", "PLATFORM_DATABASE_URL", "must be a PostgreSQL database in production"))
        out.extend(_keyring_findings(s))
        if not require_totp():
            out.append(Finding("error", "PLATFORM_REQUIRE_TOTP", "must not be false: masters use two-step sign-in"))
        if bool(getattr(s, "signup_enabled", True)) and check_database and url.startswith("postgresql"):
            masters = _masters_exist()
            if masters is False:
                out.append(Finding("error", "SIGNUP_ENABLED",
                                   "sign-up is open but no master account exists to approve requests "
                                   "(python -m app.platform create-master, or SIGNUP_ENABLED=false)"))
            elif masters is None:
                out.append(Finding("warning", "SIGNUP_ENABLED", "could not check for a master account (database?)"))
        domain = str(getattr(s, "platform_domain", "") or "")
        if domain.endswith(".invalid") and not str(getattr(s, "platform_public_url", "") or "").strip():
            out.append(Finding("warning", "PLATFORM_PUBLIC_URL",
                               "no public address yet (PLATFORM_DOMAIN is a placeholder): links in emails and "
                               "set-up links point nowhere"))
        if bool(getattr(s, "dedicated_hosting_enabled", False)):
            out.append(Finding("warning", "DEDICATED_HOSTING_ENABLED", "on - only with the owner's approval"))
    else:
        if not str(getattr(s, "store_code", "") or "").strip():
            out.append(Finding("warning", "STORE_CODE", "blank: the store identity check is skipped"))

    firebase = str(getattr(s, "firebase_credentials_file", "") or "").strip()
    if not firebase or not pathlib.Path(firebase).expanduser().is_file():
        out.append(Finding("warning", "FIREBASE_CREDENTIALS_FILE", "not set up: push notifications are a dry run"))
    if not str(getattr(s, "directory_client_ip_header", "") or "").strip() and not os.environ.get("FORWARDED_ALLOW_IPS"):
        out.append(Finding("warning", "DIRECTORY_CLIENT_IP_HEADER",
                           "behind a proxy or tunnel set it (or FORWARDED_ALLOW_IPS), or every visitor shares one "
                           "address for the login lockout"))
    try:
        if not (frontend_dist_dir() / "index.html").is_file():
            out.append(Finding("warning", "FRONTEND_DIST_DIR", "no website build: only the API is served"))
    except Exception:  # noqa: BLE001
        pass
    if not str(getattr(s, "log_dir", "") or "").strip():
        out.append(Finding("warning", "LOG_DIR", "blank: logs go to stdout/stderr only (no error.log)"))
    out.extend(go_public_findings(s, multi=multi, check_database=check_database))
    return out


def enforce(settings=None) -> List[Finding]:
    """Start-up: production refuses on any error; elsewhere everything is
    only logged. Returns the findings."""
    s = settings
    if s is None:
        from app.config import process_settings

        s = process_settings()
    findings = check_config(s)
    production = is_production(s)
    errors = [f for f in findings if f.level == "error"]
    for f in findings:
        if f.level == "error" and production:
            continue
        logger.warning("Config check: %s", f.line())
    if errors and production:
        raise ProductionConfigError(errors, str(getattr(s, "app_env", "") or "production").strip())
    return findings
