"""Start-up checks for TENANCY_MODE=multi (contract 8.4): the platform
database is configured and the key ring loads (production with no ring
refuses to start; APP_ENV=development makes a dev ring)."""

import logging

from app.platform import crypto
from app.platform.db import is_configured

logger = logging.getLogger(__name__)


class PlatformStartupError(RuntimeError):
    pass


def startup_checks() -> None:
    if not is_configured():
        raise PlatformStartupError("TENANCY_MODE=multi needs PLATFORM_DATABASE_URL")
    try:
        ring = crypto.keyring()
    except crypto.KeyRingError as exc:
        raise PlatformStartupError(str(exc)) from None
    if ring.source == "dev-file":
        logger.warning("Using the DEVELOPMENT key ring (APP_ENV=development); never in production")
