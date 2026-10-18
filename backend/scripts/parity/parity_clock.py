"""A frozen clock for the parity harness (no extra dependency).

Every "now" in the app goes through `dt.datetime.now(...)`,
`dt.datetime.utcnow()` or `dt.date.today()` on the `datetime` module
(`import datetime as dt` everywhere in app/ - checked when this was written).
`freeze()` swaps `datetime.datetime` / `datetime.date` on the module for
subclasses whose now()/today() return one fixed instant. Constructing a
datetime still gives a real `datetime` object, and `isinstance` checks against
the swapped names keep working (metaclass below), so nothing downstream sees
a strange type.

It must be called AFTER the app is imported: FastAPI / pydantic read the type
annotations (`Optional[dt.date]`) at import time, and those must stay the real
classes.

Not frozen on purpose: time.time() / time.monotonic(). The app uses them only
for in-process cache lifetimes and retry back-off, which never reach a
response within one short recording.
"""

import datetime as _datetime_module
from typing import Optional

_REAL_DATETIME = _datetime_module.datetime
_REAL_DATE = _datetime_module.date

_frozen_utc: Optional[_REAL_DATETIME] = None


class _DatetimeMeta(type):
    def __instancecheck__(cls, obj):
        return isinstance(obj, _REAL_DATETIME)

    def __subclasscheck__(cls, sub):
        return issubclass(sub, _REAL_DATETIME)


class _DateMeta(type):
    def __instancecheck__(cls, obj):
        return isinstance(obj, _REAL_DATE)

    def __subclasscheck__(cls, sub):
        return issubclass(sub, _REAL_DATE)


def _local_now() -> _REAL_DATETIME:
    """The frozen instant as a naive local time (what datetime.now() gives)."""
    return _frozen_utc.astimezone().replace(tzinfo=None)


class FrozenDatetime(_REAL_DATETIME, metaclass=_DatetimeMeta):
    def __new__(cls, *args, **kwargs):
        # Real objects only: datetime(2026, 1, 1), combine(), strptime(),
        # fromisoformat() on this class all come back as plain datetimes.
        return _REAL_DATETIME(*args, **kwargs)

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return _local_now()
        return _frozen_utc.astimezone(tz)

    @classmethod
    def utcnow(cls):
        return _frozen_utc.replace(tzinfo=None)

    @classmethod
    def today(cls):
        return _local_now()


class FrozenDate(_REAL_DATE, metaclass=_DateMeta):
    def __new__(cls, *args, **kwargs):
        return _REAL_DATE(*args, **kwargs)

    @classmethod
    def today(cls):
        return _local_now().date()


def freeze(instant: _REAL_DATETIME) -> None:
    """Freezes "now" at `instant` (timezone-aware) for the whole process."""
    global _frozen_utc
    if instant.tzinfo is None:
        raise ValueError("freeze() needs a timezone-aware datetime")
    _frozen_utc = instant.astimezone(_datetime_module.timezone.utc)
    _datetime_module.datetime = FrozenDatetime
    _datetime_module.date = FrozenDate


def unfreeze() -> None:
    global _frozen_utc
    _frozen_utc = None
    _datetime_module.datetime = _REAL_DATETIME
    _datetime_module.date = _REAL_DATE


def frozen_at() -> Optional[_REAL_DATETIME]:
    return _frozen_utc
