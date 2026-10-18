"""Platform implementations of track B's ``RegistrySource`` and
``SettingsSource`` (contract 16), installed by ``configure()`` when
``TENANCY_MODE=multi``. B caches what these return (10 s / per
settings_version); C calls ``bridge.store_changed`` after every write.
"""

import json
import logging
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import select

from app.platform.db import platform_session
from app.platform.models import Store, StoreSetting

logger = logging.getLogger(__name__)


def to_ref(store: Store):
    from app.tenancy import StoreRef

    return StoreRef(
        code=store.code,
        name=store.name,
        status=store.status,
        hosting=store.hosting,
        api_base_url=store.api_base_url,
        db_name=store.db_name,
        db_host_ref=store.db_host_ref,
        timezone=store.timezone,
        pos_provider=store.pos_provider,
        instance_id=store.instance_id,
        settings_version=int(store.settings_version or 1),
    )


class PlatformRegistrySource:
    def load(self, code: str):
        with platform_session() as db:
            store = db.execute(select(Store).where(Store.code == (code or "").upper())).scalar_one_or_none()
            return to_ref(store) if store is not None else None

    def list(self, statuses: Iterable[str]) -> List[Any]:
        with platform_session() as db:
            rows = db.execute(select(Store).where(Store.status.in_(list(statuses))).order_by(Store.code)).scalars()
            return [to_ref(s) for s in rows]

    def settings_version(self, code: str) -> int:
        with platform_session() as db:
            v = db.execute(select(Store.settings_version).where(Store.code == (code or "").upper())).scalar()
            return int(v or 0)


def store_settings_dict(code: str) -> Dict[str, Any]:
    """Whitelisted settings of one store (JSON-decoded), plus the registry
    columns that are settings (name, time zone, POS)."""
    with platform_session() as db:
        store = db.execute(select(Store).where(Store.code == (code or "").upper())).scalar_one_or_none()
        if store is None:
            return {}
        out: Dict[str, Any] = {
            "store_name": store.name,
            "store_timezone": store.timezone,
            "pos_provider": store.pos_provider,
            "min_supported_build_ios": store.min_build_ios,
            "min_supported_build_android": store.min_build_android,
        }
        for row in db.execute(select(StoreSetting).where(StoreSetting.store_id == store.id)).scalars():
            try:
                out[row.key] = json.loads(row.value)
            except ValueError:
                logger.warning("store_settings %s/%s is not JSON; ignored", code, row.key)
        return out


class PlatformSettingsSource:
    def store_settings(self, code: str) -> Dict[str, Any]:
        return store_settings_dict(code)

    def credential_overrides(self, code: str) -> Dict[str, Any]:
        from app.platform.credentials import settings_overrides

        return settings_overrides(code)


_configured = False


def configure(force: bool = False) -> bool:
    """Install the platform sources into app.tenancy (multi mode only).
    Returns True when installed."""
    global _configured
    from app.platform.config import is_multi

    if not is_multi() or (_configured and not force):
        return _configured
    from app.platform.bridge import tenancy

    t = tenancy(required=True)
    t.set_registry_source(PlatformRegistrySource())
    t.set_settings_source(PlatformSettingsSource())
    _configured = True
    return True


def lookup_record(code: str) -> Optional[Store]:
    with platform_session() as db:
        return db.execute(select(Store).where(Store.code == code)).scalar_one_or_none()
