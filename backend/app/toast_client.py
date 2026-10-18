"""Old home of the Toast Orders API client - moved, this name still works.

Multi-store M1 batch MT2 moved the code, unchanged, into the Toast adapter:

    login, token cache, ToastAPIError   -> app/pos/adapters/toast/client.py
    fetch_orders_for_business_date      -> app/pos/adapters/toast/orders.py
    fetch_employees                     -> app/pos/adapters/toast/labor.py

Every name below forwards to its new home (``app.pos.compat``): reading
``toast_client.X`` gives the adapter's current X, and patching
``toast_client.X`` (monkeypatch, mock.patch) patches the adapter, so callers
see it exactly as before. ``from app.toast_client import X`` keeps working.
"""

import datetime as dt  # noqa: F401 - the old module exposed these names
import time  # noqa: F401

import httpx  # noqa: F401

from app.config import get_settings  # noqa: F401
from app.pos.adapters.toast import client as _client
from app.pos.adapters.toast import labor as _labor
from app.pos.adapters.toast import orders as _orders
from app.pos.compat import forwards_for, install_forwarding

install_forwarding(
    __name__,
    {
        **forwards_for(
            _client, ("_TOKEN_CACHE", "ToastAPIError", "_extract_access_token", "get_access_token", "_drop_token")
        ),
        **forwards_for(_labor, ("fetch_employees",)),
        **forwards_for(_orders, ("fetch_orders_for_business_date",)),
    },
)
