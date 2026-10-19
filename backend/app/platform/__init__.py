"""The KloudC RetailOps platform (control plane; docs/PLATFORM_CONTRACT.md, track C).

- ``models`` / ``db``: the platform database (``alembic -n platform``)
- ``signup`` / ``provisioning`` / ``stores``: sign-up by request, master
  approval, automatic provisioning, suspend / resume / archive
- ``masters`` / ``login``: master admins and the one login page
- ``crypto`` / ``credentials`` / ``connector_tests``: the integrations vault
- ``hosting``: dedicated server moves (DEDICATED_HOSTING_ENABLED, off)
- ``sources``: the registry / settings sources for ``app.tenancy``
- ``routes``: the HTTP API; ``__main__``: ``python -m app.platform ...``
"""
