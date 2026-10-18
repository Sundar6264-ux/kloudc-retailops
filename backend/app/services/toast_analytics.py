"""Old home of the Toast Analytics (ERA) client - moved, this name still works.

Multi-store M1 batch MT2 moved the whole module, unchanged, to
``app/pos/adapters/toast/analytics.py``. Every name it holds forwards there
(``app.pos.compat``): ``toast_analytics.run_report`` is the adapter's
``run_report``, and patching ``toast_analytics._sleep`` / ``_now`` /
``menu_day_totals`` (as the tests do) patches the adapter, so its callers see
it exactly as before.
"""

import collections  # noqa: F401 - the old module exposed these names
import datetime as dt  # noqa: F401
import logging  # noqa: F401
import threading  # noqa: F401
import time  # noqa: F401

import httpx  # noqa: F401

from app.pos.adapters.toast import analytics as _analytics
from app.pos.compat import defined_names, forwards_for, install_forwarding

install_forwarding(__name__, forwards_for(_analytics, defined_names(_analytics)))
