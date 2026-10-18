"""POS-neutral sync jobs (multi-store plan 4.2 / 4.8, M1 batch MT3).

Each job asks the store's connector (``app.pos.registry.get_connector()``)
for normalized models (``app.pos.models``) and writes our own tables - the
same rows, job_runs names and details as before M1:

    sales.py        orders            <- services/sales_sync.py
    open_orders.py  open_order_cache  <- services/open_orders.py
    accuracy.py     orders re-sync    <- services/sales_accuracy.py
    catalog.py      inventory_snapshots (API build), toast_menu_items, the
                    live stock overlay <- services/inventory_api.py

The old modules still work under their old names (``app.pos.compat``
forwarding), including patching them in tests. Nothing in here reads a POS
payload: Toast JSON stops at ``app/pos/adapters/toast``.
"""
