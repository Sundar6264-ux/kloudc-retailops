"""Toast adapter (multi-store plan 4.6): today's Toast code, moved here
unchanged in M1 batch MT2.

    client.py      login + the one token cache + toast_get (Standard API GETs
                   with 429 backoff and re-login on 401)   <- app/toast_client.py,
                                                              services/inventory_api.py
    orders.py      ordersBulk by business date              <- app/toast_client.py
    labor.py       employees                                <- app/toast_client.py
    stock.py       /stock/v1/inventory                      <- services/inventory_api.py
    catalog.py     /menus/v2 menus + metadata, menu parsing <- services/inventory_api.py
    timeparse.py   Toast's "+0000" timestamps               <- services/inventory_api.py
    analytics.py   Analytics (ERA) client                   <- services/toast_analytics.py
    csv_import.py  Toast Retail "retail items" export map   <- services/inventory_parser.py
    connector.py   ToastConnector (the PosConnector for Toast)

The old module names still work (see ``app.pos.compat``). This package's
``__init__`` imports nothing on purpose, so importing one piece never drags
in the others (pandas, for one, is only needed by csv_import).

Cross-module references inside the adapter go through the module
(``_client.get_access_token(...)``), never ``from ... import name``, so a
test that patches a name on its owning module (or on the old module path,
which forwards there) is seen by every caller.
"""
