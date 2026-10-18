"""POS connector package (multi-store plan, section 4).

Everything that talks to a point-of-sale system (Toast today, Clover later)
lives under ``app.pos``. The rest of the app depends only on the POS-neutral
pieces here (capability flags, normalized models, the connector interface),
never on a specific POS.

M1 is a pure move: Spice Town's behaviour stays exactly the same. The old
module names (``app.toast_client``, ``app.services.inventory_api`` ...) keep
working as re-export shims.
"""
