"""Which POS this server talks to (multi-store plan 4.2).

``get_connector()`` reads the ``POS_PROVIDER`` setting (default ``toast``, so
a Toast store needs no change) and returns that POS's connector. In multi
mode (docs/PLATFORM_CONTRACT.md 9.1) the setting is the current store's, and
the instance cache is keyed (store code, provider); connectors read their
credentials from ``get_settings()`` at call time, i.e. the store's own.
"""

import threading
from typing import Callable, Dict, Optional, Tuple

from app.config import get_settings
from app.pos.base import PosConnector


class UnknownPosProvider(ValueError):
    pass


def _toast() -> PosConnector:
    from app.pos.adapters.toast.connector import ToastConnector

    return ToastConnector()


def _csv() -> PosConnector:
    from app.pos.adapters.csv_only.connector import CsvOnlyConnector

    return CsvOnlyConnector()


# provider name -> factory. Adapters register here (Clover arrives in M3;
# "csv" = the CSV-only connector, M2 batch MT13).
PROVIDERS: Dict[str, Callable[[], PosConnector]] = {"toast": _toast, "csv": _csv}

_cache: Dict[Tuple[Optional[str], str], PosConnector] = {}
_lock = threading.Lock()  # global: guards the map (whose keys carry the store)


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
    from app.tenancy.context import current_store_code

    key = (current_store_code(), name)
    with _lock:
        conn = _cache.get(key)
        if conn is None:
            conn = _cache[key] = factory()
        return conn


def reset_cache(store_code: Optional[str] = None) -> None:
    """Forget the cached connectors (tests; a settings change) - of one
    store, or of every store when None."""
    with _lock:
        if store_code is None:
            _cache.clear()
        else:
            code = store_code.strip().upper()
            for key in [k for k in _cache if k[0] == code]:
                del _cache[key]
