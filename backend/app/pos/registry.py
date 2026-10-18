"""Which POS this server talks to (multi-store plan 4.2).

``get_connector()`` reads the ``POS_PROVIDER`` setting (default ``toast``, so
Spice Town needs no change) and returns that POS's connector. Step 1 runs one
server copy per store, so one setting is enough; Step 2 will look it up per
store.
"""

import threading
from typing import Callable, Dict, Optional

from app.config import get_settings
from app.pos.base import PosConnector


class UnknownPosProvider(ValueError):
    pass


def _toast() -> PosConnector:
    from app.pos.adapters.toast.connector import ToastConnector

    return ToastConnector()


# provider name -> factory. Adapters register here (Clover arrives in M3).
PROVIDERS: Dict[str, Callable[[], PosConnector]] = {"toast": _toast}

_cache: Dict[str, PosConnector] = {}
_lock = threading.Lock()


def provider_name(provider: Optional[str] = None) -> str:
    name = provider if provider is not None else get_settings().pos_provider
    # Blank or whitespace-only means Toast (the same rule as app.pos.gating had).
    return (name or "").strip().lower() or "toast"


def get_connector(provider: Optional[str] = None) -> PosConnector:
    """The connector for ``provider`` (default: the POS_PROVIDER setting).
    Connectors keep no per-call state, so one instance per provider is reused."""
    name = provider_name(provider)
    factory = PROVIDERS.get(name)
    if factory is None:
        raise UnknownPosProvider(f"Unknown POS_PROVIDER {name!r}; known: {', '.join(sorted(PROVIDERS))}")
    with _lock:
        conn = _cache.get(name)
        if conn is None:
            conn = _cache[name] = factory()
        return conn


def reset_cache() -> None:
    """Forget the cached connectors (tests; a settings change)."""
    with _lock:
        _cache.clear()
