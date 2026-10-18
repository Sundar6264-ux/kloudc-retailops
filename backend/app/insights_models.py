"""Tables for Insights from the Toast Analytics API (docs/INSIGHTS_CONTRACT.md,
migration 0051).

analytics_cache: one Toast Analytics answer per (report, params) - for the
per-business-date reports, one row per date (a weekly report is split into
its dates when it's stored). Past dates are fetched once and reused; the
nightly job re-pulls the recent ones (services/insights_data.py). Only the
fields the Insights pages use are kept (payload_json), never card data.

analytics_call_log: one row per daily-capped Analytics call (check and
guest payment reports: 60 a day at Toast, shared with every other user of
the credentials) so our own budget survives a restart. Kept 7 days.

guest_visits: returning customers. One row per (payment date, card) where
the card is only an HMAC of Toast's card fingerprint with a server secret -
no card number, last 4, type, name or Toast fingerprint is ever stored.
Kept GUEST_RETENTION_DAYS (400) at most.

loss_watch_flags: checks Loss watch / Odd checks flagged (a full comp, a
large discount or refund, a check open too long...), so the 09:00 alert can
say each one once and an admin can acknowledge it. AUTOINCREMENT: the id is
in the URL of the acknowledge endpoint and must never be reused.
"""

import datetime as dt
from typing import Optional

from sqlalchemy import Date, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.timeutil import utcnow


class AnalyticsCache(Base):
    __tablename__ = "analytics_cache"
    __table_args__ = (UniqueConstraint("report", "params_hash", name="uq_analytics_cache_report_params"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report: Mapped[str] = mapped_column(String, index=True)
    params_hash: Mapped[str] = mapped_column(String)
    business_date: Mapped[Optional[dt.date]] = mapped_column(Date, nullable=True, index=True)
    fetched_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    payload_json: Mapped[str] = mapped_column(Text)


class AnalyticsCallLog(Base):
    __tablename__ = "analytics_call_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    family: Mapped[str] = mapped_column(String, index=True)  # "check" | "guest"
    business_date: Mapped[Optional[dt.date]] = mapped_column(Date, nullable=True)
    called_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    ok: Mapped[int] = mapped_column(Integer, default=0)


class GuestVisit(Base):
    __tablename__ = "guest_visits"
    __table_args__ = (UniqueConstraint("visit_date", "card_hash", name="uq_guest_visits_date_card"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    visit_date: Mapped[dt.date] = mapped_column(Date, index=True)
    # HMAC-SHA256(server secret, Toast card fingerprint), hex. Not reversible
    # without the secret; never sent to a client.
    card_hash: Mapped[str] = mapped_column(String, index=True)
    payments: Mapped[int] = mapped_column(Integer, default=1)


class LossWatchFlag(Base):
    __tablename__ = "loss_watch_flags"
    __table_args__ = (UniqueConstraint("flag_key", name="uq_loss_watch_flags_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    flag_key: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String, index=True)  # "loss" | "odd_check"
    business_date: Mapped[dt.date] = mapped_column(Date, index=True)
    check_guid: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    check_number: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    employee: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    reasons: Mapped[str] = mapped_column(String)  # comma-separated reason keys
    amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    acked_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
    acked_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    acked_by_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
