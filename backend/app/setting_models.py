"""Settings an admin changes on a page, with who / when / history
(migration 0052). The first one is the "Use Toast for inventory" switch
(services/inventory_api_switch.py).

Kept out of models.py (a file every batch edits) - imported by
services/inventory_api_switch.py, which the app always loads through
routers/inventory_api.py.
"""

import datetime as dt
from typing import Optional

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.timeutil import utcnow


class SettingChange(Base):
    """One row per change of an admin-set setting; never updated or deleted.
    The newest row for a key is the value in force (it wins over the .env
    default); the older rows are the history the page shows.

    `value` / `previous_value` are JSON. `previous_source` says where the
    value before this change came from: "db" (an earlier row) or "env" (the
    server setting, while nobody had changed it here yet).
    """

    __tablename__ = "setting_changes"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String, index=True)
    value: Mapped[str] = mapped_column(Text)
    previous_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    previous_source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    changed_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    changed_by_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    changed_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
