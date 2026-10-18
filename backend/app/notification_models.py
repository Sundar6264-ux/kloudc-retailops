"""Per-user push log (migration 0058, docs/BATCH_OCT15_CONTRACT.md "As built -
NOTIFICATIONS"). notification_log says an alert went out and to how many;
this says to whom - one row per user a push actually reached a device of
(alerts, task pushes, chat, announcements). Written after the send, never on
a dry run; what it powers is per-user volume and "Recent notifications".

Kept out of models.py (a file every batch edits), like setting_models.py -
imported by services/notifications.py, which the app always loads.
"""

import datetime as dt
from typing import Optional

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.timeutil import utcnow


class PushDelivery(Base):
    __tablename__ = "push_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    # The alert key (services/notifications.py ALERTS) or a team push key
    # ("chat_message", "chat_report", ...).
    key: Mapped[str] = mapped_column(String, index=True)
    # Only the title (e.g. "Order today: Babco", "Alex in Kitchen") - never
    # the body, which can hold a chat message's text.
    title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    sent_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
