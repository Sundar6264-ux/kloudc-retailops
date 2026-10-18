import datetime as dt
import zoneinfo

from app.config import get_settings


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def restaurant_timezone() -> zoneinfo.ZoneInfo:
    return zoneinfo.ZoneInfo(get_settings().store_timezone)


def restaurant_today() -> dt.date:
    """"Today" in the restaurant's own timezone, not the server's - matters
    since the server and the restaurant aren't guaranteed to be in the same zone.
    """
    return dt.datetime.now(restaurant_timezone()).date()


def to_restaurant_date(value: dt.datetime) -> dt.date:
    """Converts a UTC-aware timestamp to the calendar date it falls on in the
    restaurant's own timezone - a payment at 11:30pm ET is still "today" even
    though it's already past midnight UTC.
    """
    return value.astimezone(restaurant_timezone()).date()
