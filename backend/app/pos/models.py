"""Normalized POS models (multi-store plan 4.4, M1 batch MT3).

Every POS adapter turns its own payloads into these; the POS-neutral sync
jobs (``app/pos/sync/``) read only these and write our own tables. Nothing
here knows about Toast or Clover.

Conventions:
- Money and quantities are ``Decimal`` in the store's currency. An adapter
  builds them with ``to_decimal(float(...))`` from the POS's JSON number, so
  ``float(model value)`` gives back exactly the float the code used before
  M1 (a float's ``repr`` round-trips). The sync jobs write floats, as today.
- Times are timezone-aware UTC ``datetime`` or None.
- External ids are opaque strings, unique within one POS (``source``).
- Models are frozen dataclasses with tuple fields: built once by an adapter,
  never changed afterwards.

Python 3.9: ``typing.Optional`` / ``Tuple`` only (no ``X | None``).
"""

import datetime as dt
import enum
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional, Tuple


def to_decimal(value: Any) -> Optional[Decimal]:
    """A JSON number (int / float / numeric string) as an exact Decimal of its
    float value - ``float(to_decimal(x)) == float(x)`` for every finite x, so
    a sync that writes ``float(...)`` writes the same number as before M1.
    None stays None."""
    if value is None:
        return None
    return Decimal(repr(float(value)))


def to_float(value: Optional[Decimal]) -> Optional[float]:
    return None if value is None else float(value)


# ---------------------------------------------------------------------------
# Catalog and stock
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Category:
    """A three-level catalog placement (group / category / subcategory). A POS
    with a flat category list fills ``group`` only."""

    group: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None

    def as_tuple(self) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        return (self.group, self.category, self.subcategory)


@dataclass(frozen=True)
class Vendor:
    """A supplier of an item. Toast's API and Clover have none; they come
    from ``item_vendors`` (MT5)."""

    name: str
    external_id: Optional[str] = None
    item_code: Optional[str] = None


@dataclass(frozen=True)
class Item:
    """One sellable catalog item. ``external_id`` is the join key with
    ``OrderLine.item_external_id`` (today's ``item_guid``)."""

    external_id: str
    name: Optional[str]
    barcodes: Tuple[str, ...] = ()
    plu: Optional[str] = None
    # None = no fixed price (open / size / variable pricing, or $0).
    price: Optional[Decimal] = None
    cost: Optional[Decimal] = None
    # The default placement; None when the POS gives none.
    category: Optional[Category] = None
    # Every place the item appears in the POS catalog (Toast: each
    # menu / group / subgroup it is listed under). An imported placement that
    # is still one of these is kept.
    locations: Tuple[Category, ...] = ()
    # Other spellings of the name the POS uses (Toast: the menu item name with
    # and without its " - Each" / " - Container" suffix): an imported name
    # matching one of these is kept.
    other_names: Tuple[Optional[str], ...] = ()
    vendors: Tuple[Vendor, ...] = ()
    variant_of: Optional[str] = None
    # "each" / "container" for a pack-size variant, else None.
    variant_kind: Optional[str] = None
    # The POS's own name for how the item is priced (Toast pricingStrategy),
    # kept for the catalog cache; never interpreted outside the adapter.
    price_rule: Optional[str] = None
    active: bool = True
    updated_at: Optional[dt.datetime] = None

    @property
    def barcode(self) -> Optional[str]:
        """The POS's primary barcode (Toast: the menu ``sku``)."""
        return self.barcodes[0] if self.barcodes else None


@dataclass(frozen=True)
class CatalogSnapshot:
    """A whole catalog pull: the items in the POS's own order, and when the
    catalog last changed (Toast ``/menus/v2/menus`` ``lastUpdated``)."""

    items: Tuple[Item, ...]
    changed_at: Optional[dt.datetime] = None


class StockStatus(str, enum.Enum):
    # A counted item. ``quantity`` is None when the POS said "counted" but
    # sent no number (then the count is not known).
    COUNT = "count"
    # Out of stock: 0 OR BELOW (Toast's OUT_OF_STOCK hides negative counts).
    OUT = "out"
    # Stock not tracked for the item (or a status the adapter doesn't know).
    UNTRACKED = "untracked"


@dataclass(frozen=True)
class StockLevel:
    item_external_id: Optional[str]
    status: StockStatus
    quantity: Optional[Decimal] = None
    as_of: Optional[dt.datetime] = None


@dataclass(frozen=True)
class CatalogRow:
    """One row of a POS's catalog export file (CSV / XLSX), already mapped to
    our ``inventory_snapshots`` field names (``fields``)."""

    item_external_id: str
    fields: Tuple[Tuple[str, Any], ...] = ()


# ---------------------------------------------------------------------------
# Sales
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Employee:
    external_id: Optional[str]
    name: str
    active: bool = True


@dataclass(frozen=True)
class Check:
    """One check (bill) of an order."""

    external_id: str
    # When this check was paid (its own payment time only; see
    # ``pos.sync.sales.line_paid_at`` for the fallbacks a sales line uses).
    paid_at: Optional[dt.datetime] = None
    # The check's total as the POS states it; None when it sent none.
    total: Optional[Decimal] = None
    # The name typed on the tab (usually the customer's), as sent.
    tab_name: Optional[str] = None
    voided: bool = False
    deleted: bool = False


@dataclass(frozen=True)
class OrderLine:
    """One sold line. A modifier ("extra cheese", a spice level) is its own
    line with ``is_modifier`` and the line it modifies as
    ``parent_external_id`` - one row each in ``orders``, as today."""

    external_id: str
    check_external_id: str
    parent_external_id: Optional[str]
    is_modifier: bool
    item_external_id: Optional[str]
    name: Optional[str]
    quantity: Decimal
    # After discounts, before tax. With MODIFIER_PRICE_IN_PARENT (Toast) a
    # top-level line's amount already includes its modifiers' prices.
    net_amount: Decimal
    tax_amount: Decimal
    # The line's own void flag (an order / check void is on those models).
    voided: bool = False
    # Position of the line's check in ``Order.checks`` (None: find it by
    # ``check_external_id``). Exact even if a POS repeats or omits check ids.
    check_index: Optional[int] = None


@dataclass(frozen=True)
class Order:
    """One POS order with its checks and lines (lines in the POS's own order:
    check by check, each line followed by its modifiers)."""

    external_id: str
    business_date: dt.date
    opened_at: Optional[dt.datetime] = None
    # When the order was actually placed (Toast createdDate).
    created_at: Optional[dt.datetime] = None
    # The order-level payment time (a check without its own falls back to it).
    paid_at: Optional[dt.datetime] = None
    closed: bool = False
    closed_at: Optional[dt.datetime] = None
    # Guest-facing promised time, else the POS's own estimate.
    promised_at: Optional[dt.datetime] = None
    # Normalized channel (in_store / pickup / online / delivery_3p /
    # catering / other); None until a store maps its dining options (M2).
    channel: Optional[str] = None
    channel_raw: Optional[str] = None
    # The POS's dining option / order type id (Toast diningOption guid).
    dining_option_id: Optional[str] = None
    display_number: Optional[Any] = None
    guests: Optional[Any] = None
    server_external_id: Optional[str] = None
    # The POS's own approval state, shown as sent (Toast approvalStatus).
    approval_status: Optional[str] = None
    checks: Tuple[Check, ...] = ()
    lines: Tuple[OrderLine, ...] = ()
    voided: bool = False
    deleted: bool = False
    updated_at: Optional[dt.datetime] = None


@dataclass(frozen=True)
class SalesDay:
    """The POS's own totals for one business date (the sales accuracy check).

    ``has_item_totals`` / ``has_day_metrics``: whether the POS answered with
    item-level totals / day metrics for this date at all; amounts are 0 for a
    part it did not answer. Money as Decimal, counts as int."""

    business_date: dt.date
    has_item_totals: bool = False
    has_day_metrics: bool = False
    # Item level: the sum over sold items (what our top-level lines add up to).
    item_net: Decimal = Decimal(0)
    item_qty: Decimal = Decimal(0)
    item_gross: Decimal = Decimal(0)
    item_discounts: Decimal = Decimal(0)
    item_voids: Decimal = Decimal(0)
    # Day metrics: order-level net / gross / discounts / voids / refunds.
    net: Decimal = Decimal(0)
    gross: Decimal = Decimal(0)
    discounts: Decimal = Decimal(0)
    voids: Decimal = Decimal(0)
    refunds: Decimal = Decimal(0)
    orders: int = 0
    void_orders: int = 0
    open_orders: int = 0


@dataclass(frozen=True)
class ReceivingRecord:
    """A receipt of goods (Toast Retail CSV "last received" today)."""

    reference: str
    item_external_id: str
    received_at: Optional[dt.datetime] = None
    vendor_name: Optional[str] = None


@dataclass(frozen=True)
class AnalyticsReport:
    """Opaque, POS-specific report rows (Insights keeps its own caching and
    budget logic on top)."""

    kind: str
    rows: Tuple[Any, ...] = ()
    business_date: Optional[dt.date] = None
    fetched_at: Optional[dt.datetime] = None


@dataclass(frozen=True)
class ConnectorHealth:
    source: str
    configured: bool
    detail: Optional[str] = None
