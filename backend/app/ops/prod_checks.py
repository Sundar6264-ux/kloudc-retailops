"""Production start-up checks (docs/PILOT_RUNBOOK.md "Start-up checks").

With APP_ENV=production (the default) the server refuses to start when its
configuration is unsafe; anything else (development, test, the preview) only
logs the findings. ``python -m app.ops check-config`` prints them without
starting anything (the pilot installer runs it first).

Errors (refuse to start in production):
- SESSION_COOKIE_SECURE=false (the login cookie would travel over plain http);
- CORS_ORIGINS with "*" (credentials are allowed on CORS answers);
- multi mode: no PostgreSQL platform database; no master key ring
  (RETAILOPS_SECRET_KEYS); a development key ring (an id starting "dev", or
  a key that is also in the local dev ring file); PLATFORM_REQUIRE_TOTP=false
  (masters must use two-step sign-in); SIGNUP_ENABLED=true with no master
  account that could approve a sign-up (sign-ups always wait for a master's
  approval - there is no auto-approve).
Warnings (logged, never fatal): placeholder domain, no Firebase file, no
client-IP header behind a proxy, no website build, no STORE_CODE (single mode),
DEDICATED_HOSTING_ENABLED, no LOG_DIR.
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
    def __init__(self, findings: List[Finding]):
        self.findings = findings
        lines = "\n  ".join(f.line() for f in findings)
        super().__init__(
            "KloudC RetailOps refuses to start in production with this configuration "
            f"(APP_ENV=production):\n  {lines}\nFix the settings above (docs/PILOT_RUNBOOK.md), or use "
            "APP_ENV=development on a developer machine."
        )


def is_production(settings=None) -> bool:
    from app.platform.config import PRODUCTION_ENVS

    if settings is None:
        from app.config import process_settings

        settings = process_settings()
    return str(getattr(settings, "app_env", "production") or "production").strip().lower() in PRODUCTION_ENVS


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


def check_config(settings=None, *, check_database: bool = True) -> List[Finding]:
    """Every finding for these settings (production rules applied whatever
    APP_ENV is; ``enforce`` decides what is fatal)."""
    from app.config import frontend_dist_dir, process_settings
    from app.platform.config import require_totp

    s = settings if settings is not None else process_settings()
    out: List[Finding] = []
    multi = str(getattr(s, "tenancy_mode", "single")) == "multi"

    if not bool(getattr(s, "session_cookie_secure", True)):
        out.append(Finding("error", "SESSION_COOKIE_SECURE", "must be true (the login cookie is https-only)"))
    origins = [o.strip() for o in str(getattr(s, "cors_origins", "") or "").split(",") if o.strip()]
    if "*" in origins:
        out.append(Finding("error", "CORS_ORIGINS", '"*" with credentials lets any site use a signed-in session'))

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
        raise ProductionConfigError(errors)
    return findings
