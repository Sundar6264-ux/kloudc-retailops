"""Per-store configuration (StoreOps M2, docs/M2_CONTRACT.md sections 1-3).

- ``settings_view``: values derived from the store settings in
  ``app.config.Settings`` (display name, public host, store features, SMTP...).
- ``branding``: the public ``GET /api/app/branding`` body.
- ``pack``: the store pack - the store's own rules (PO sections, kitchen
  groups, sales channels, Fix Issues leads, built-in checklists, WhatsApp
  roles, flyer categories, category rules) as one JSON file.
- ``identity``: the ``store_identity`` stamp that stops a server from
  starting against another store's database or upload folder.

Commands: ``python -m app.store --help``.
"""
