"""Open orders come as three summaries a day (owner, 2026-09-28): 9 AM
every open order, 4 PM the ones opened since 9 AM that are still open,
9:30 PM day end - never a push per new order."""

import datetime as dt
import zoneinfo

from app.models import OpenOrderCache
from app.services import notifications
from app.services.notification_triggers import (
    OPEN_ORDER_SUMMARY_TIMES,
    TRIGGERS,
    open_orders_morning_alerts,
)

NY = zoneinfo.ZoneInfo("America/New_York")


def at(hour, minute=0):
    return dt.datetime(2026, 9, 28, hour, minute, tzinfo=NY)


def _order(db, guid, opened_local):
    db.add(
        OpenOrderCache(
            toast_order_guid=guid,
            business_date=opened_local.date(),
            display_number=guid,
            opened_at=opened_local.astimezone(dt.timezone.utc),
            total_amount=24.5,
            num_checks=1,
        )
    )
    db.commit()


def _reset(db):
    db.query(OpenOrderCache).delete()
    db.commit()


def test_summary_times_are_9_16_and_2130():
    assert OPEN_ORDER_SUMMARY_TIMES == ((9, 0), (16, 0), (21, 30))


def test_three_summaries_with_the_right_counts(db):
    _reset(db)
    _order(db, "yesterday", at(18) - dt.timedelta(days=1))  # still open from yesterday
    _order(db, "morning", at(10, 15))                        # today 9-4
    _order(db, "lunch", at(13))                              # today 9-4
    _order(db, "evening", at(18))                            # after 4 PM

    (opening,) = open_orders_morning_alerts(db, at(9))
    (today,) = open_orders_morning_alerts(db, at(16))
    (day_end,) = open_orders_morning_alerts(db, at(21, 30))

    assert opening.title == "Opening: 4 open checks"
    assert today.title == "Today: 2 open checks since 9 AM"
    assert day_end.title == "Day end: 4 open checks"
    keys = opening.dedupe_keys + today.dedupe_keys + day_end.dedupe_keys
    assert len(set(keys)) == 3, "each slot sends once per day"


def test_nothing_to_report_sends_nothing(db):
    _reset(db)
    assert open_orders_morning_alerts(db, at(9)) == []
    _order(db, "old", at(8) - dt.timedelta(days=2))
    assert open_orders_morning_alerts(db, at(16)) == [], "no new orders since 9 AM"


def test_per_order_alert_is_retired():
    assert "open_orders_new" not in TRIGGERS
    assert "open_orders_new" not in [a["key"] for a in notifications.ALERTS]
    assert "9:30 PM" in notifications.ALERTS_BY_KEY["open_orders_morning"]["description"]


def test_day_end_summary_is_before_quiet_hours():
    assert not notifications.in_quiet_hours(at(21, 30))
