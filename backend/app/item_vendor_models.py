"""Item vendors: who supplies each item, kept as app data (multi-store M1,
MT5; migration 0061; docs/MULTISTORE_M1_CONTRACT.md "As built - MT5").

Today the vendor list only exists as the Toast Retail CSV's ";"-joined
`supplier` field on each inventory snapshot row. This table holds the same
list one row per vendor, so a store whose POS has no vendor field (Clover,
plan section 4.9) can keep its vendors here.

For a Toast store the table is a mirror of each item's newest CSV row (the CSV
wins): services/item_vendors.py refreshes it on every CSV upload, and the
0061 migration backfilled it. Nothing reads it for a Toast store's page in M1 -
the pages keep reading the snapshot's own field through
item_vendors.vendors_of().

Kept out of models.py (a file every batch edits), like setting_models.py -
imported by services/item_vendors.py, which inventory_parser.py (always
loaded) imports.
"""

import datetime as dt
from typing import Optional

from sqlalchemy import Date, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.timeutil import utcnow


class ItemVendor(Base):
    __tablename__ = "item_vendors"
    __table_args__ = (UniqueConstraint("item_id", "vendor_name", name="uq_item_vendors_item_vendor"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # The item's id (for Toast, its GUID - the same as inventory_snapshots.item_id).
    item_id: Mapped[str] = mapped_column(String, index=True)
    # As the source spelled it (trimmed). One row per vendor: a name listed
    # twice in Toast ("Chetak;Chetak") is one row at its first position.
    vendor_name: Mapped[str] = mapped_column(String, index=True)
    # The vendor's own code for this item (the CSV's `supplier item id` slot
    # for this vendor), when known.
    vendor_item_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # 1 = listed first. Ranks follow the order the source lists the vendors.
    rank: Mapped[int] = mapped_column(Integer)
    # "toast_csv" (filled from the CSV upload) or "manual" (typed in the app,
    # for a POS without vendors - no editor in M1).
    source: Mapped[str] = mapped_column(String)
    # The snapshot (CSV) date the row was taken from; NULL for manual rows.
    source_date: Mapped[Optional[dt.date]] = mapped_column(Date, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
