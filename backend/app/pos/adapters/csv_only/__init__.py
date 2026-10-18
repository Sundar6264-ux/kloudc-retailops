"""CSV-only connector (multi-store plan 3.1a / 4.5, M2 batch MT13).

For a store whose point of sale has no API we can use - and for the DEMO
store, which must never call a POS. The catalog and stock come only from
CSV / XLSX files a person uploads on the Inventory upload page; there is no
orders, open-checks, employees, day-totals or analytics API, so every one of
those methods raises ``NotSupported`` (HTTP 409) and the M1 gating hides the
matching pages and drops the matching scheduled jobs.

    template.py   the StoreOps catalog CSV template: canonical headers (the
                  same names the upload page already reads) + common aliases
    connector.py  CsvOnlyConnector (the PosConnector for POS_PROVIDER=csv)

Nothing in this package imports an HTTP client: it cannot reach a POS.
"""
