"""Toast timestamps (multi-store plan 4.2).

Moved unchanged from ``services/inventory_api.py`` in M1 batch MT2
(``inventory_api.parse_toast_time`` still works). The order mapping keeps its
own stricter copy, ``orders._parse_toast_timestamp`` (moved there from
``sales_sync`` in MT3): it logs a bad value and accepts only the
"...%f%z" form, as the sales sync always did, so the two were not merged.
"""

import datetime as dt
from typing import Optional


def parse_toast_time(value) -> Optional[dt.datetime]:
    """Toast's "2026-10-11T15:41:42.977+0000" (no colon in the offset - see
    gotcha #4) -> aware UTC datetime."""
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return dt.datetime.strptime(value, fmt).astimezone(dt.timezone.utc)
        except (TypeError, ValueError):
            continue
    return None
