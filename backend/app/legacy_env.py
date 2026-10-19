"""Environment names from before the KloudC RetailOps rename keep working.

The product was called StoreOps while it was built, so a server, a CI job or
a shell may still set ``STOREOPS_SECRET_KEYS``, ``STOREOPS_TEST_PG_URL``, ...
Each such variable is copied to its ``RETAILOPS_`` name unless that name is
already set (the new name always wins). Runs once, when the ``app`` package
is first imported (app/__init__.py), before any setting is read. The old
names in a ``.env`` file are read through the settings' own aliases
(app/config.py).
"""

import os
from typing import List, MutableMapping, Optional

LEGACY_ENV_PREFIX = "STOREOPS_"
ENV_PREFIX = "RETAILOPS_"


def apply(environ: Optional[MutableMapping[str, str]] = None) -> List[str]:
    """Copy every old-name variable to its new name; returns the new names set."""
    env = os.environ if environ is None else environ
    copied = []
    for key in [k for k in env if k.startswith(LEGACY_ENV_PREFIX)]:
        new = ENV_PREFIX + key[len(LEGACY_ENV_PREFIX):]
        if new not in env:
            env[new] = env[key]
            copied.append(new)
    return copied
