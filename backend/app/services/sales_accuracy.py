"""Old home of the sales accuracy re-pull - moved, this name still works.

Multi-store M1 batch MT3 moved the job, POS-neutral, to
``app/pos/sync/accuracy.py`` (the POS's day totals come from the connector:
``get_day_totals`` -> ``SalesDay``; for Toast the same two Analytics reports,
``app/pos/adapters/toast/day_totals.py``). Every name the old module had
forwards there (``app.pos.compat``): reading ``sales_accuracy.X`` gives the
moved X, and patching ``sales_accuracy.X`` (``utcnow``, ``_sleep``,
``run_sales_repull`` ...) patches the moved code.

``sales_accuracy.toast_analytics`` is still the Toast Analytics client module,
so ``monkeypatch.setattr(sales_accuracy.toast_analytics, "menu_day_totals",
fake)`` still feeds the job (through the Toast connector).
"""

import datetime as dt  # noqa: F401 - the old module exposed these names
import json  # noqa: F401
import logging  # noqa: F401
import threading  # noqa: F401
import time  # noqa: F401

from app.pos.compat import defined_names, forwards_for, install_forwarding
from app.pos.sync import accuracy as _accuracy
from app.services import toast_analytics  # noqa: F401 - the Analytics client, as before

# Everything the moved module holds (functions, constants, the run lock, the
# _sleep / utcnow test seams, and imported helpers such as get_settings).
install_forwarding(__name__, forwards_for(_accuracy, defined_names(_accuracy)))
