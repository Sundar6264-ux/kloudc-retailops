"""Errors raised by the tenancy layer (docs/PLATFORM_CONTRACT.md section 16)."""

from typing import Optional


class TenancyError(Exception):
    """Base class for every tenancy error."""


class NoStoreContext(TenancyError, RuntimeError):
    """Store data was asked for, but no store is selected (multi mode only)."""

    def __init__(self, message: str = "No store is selected for this request or job."):
        super().__init__(message)


class UnknownStore(TenancyError, LookupError):
    """No store with this code in the registry (or not visible here)."""

    def __init__(self, code: Optional[str]):
        self.code = code
        super().__init__(f"Unknown store {code!r}")


class StoreNotActive(TenancyError):
    """The store exists but is not active (pending, suspended...)."""

    def __init__(self, code: str, status: str):
        self.code = code
        self.status = status
        super().__init__(f"Store {code} is {status}")


class StoreMoved(TenancyError):
    """The store is served by another server (dedicated hosting, section 12)."""

    def __init__(self, store):
        self.store = store
        super().__init__(f"Store {getattr(store, 'code', store)} is served elsewhere")


class StoreIdentityError(TenancyError, RuntimeError):
    """A database or upload folder belongs to another store (M2 1.5). The
    server must not start / the engine must not be used."""


class StoreUnavailable(TenancyError):
    """The store's database cannot be reached right now (answer 503)."""

    def __init__(self, code: str, reason: str = ""):
        self.code = code
        super().__init__(f"Store {code} database unavailable{': ' + reason if reason else ''}")
