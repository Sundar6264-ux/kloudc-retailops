"""What a POS connector can do (multi-store plan, section 4.5).

Each store's flags come from its connector. A feature whose capability is
missing is hidden in the website and app, and a connector method whose
capability is missing raises ``NotSupported`` (HTTP 409).

Shared by the parallel M1 batches (MT2 connector, MT6 capability gating):
extend this list only additively - never rename or remove a member or change
its string value, because the values are sent to the app and stored.

Store-level switches that are not about the POS (AP inbox, labels, WhatsApp,
AI) are per-store settings, not capabilities, and are not listed here.
"""

from enum import Enum
from typing import FrozenSet


class Capability(str, Enum):
    ORDERS_BY_BUSINESS_DATE = "orders_by_business_date"  # orders fetched per business date
    ORDERS_INCREMENTAL = "orders_incremental"  # modified-since cursor (cheaper re-pulls)
    OPEN_ORDERS = "open_orders"  # Open Checks page (tabs, promised times)
    LIVE_TODAY_SALES = "live_today_sales"  # Items Sold "today"
    CATALOG = "catalog"  # items, prices, barcodes, categories
    CATALOG_CHANGE_STAMP = "catalog_change_stamp"  # cheap "did the menu change" check (Toast /menus/v2/metadata)
    STOCK_LEVELS = "stock_levels"  # live stock
    STOCK_NEGATIVE_COUNTS = "stock_negative_counts"  # Toast: no (OUT_OF_STOCK hides <0); Clover: signed decimal
    ITEM_VENDORS = "item_vendors"  # vendor/supplier on items via the API
    ITEM_COST = "item_cost"  # item cost via the API (margin without a CSV)
    RECEIVING_RECORDS = "receiving_records"  # "already ordered" evidence (Toast: CSV import only)
    EMPLOYEES = "employees"  # server names
    DAY_TOTALS = "day_totals"  # independent day totals for the sales-accuracy check
    ANALYTICS_REPORTS = "analytics_reports"  # Insights (Toast ERA)
    CARD_FINGERPRINTS = "card_fingerprints"  # Returning customers, Loss watch
    CSV_CATALOG_IMPORT = "csv_catalog_import"  # Inventory upload page
    MODIFIER_PRICE_IN_PARENT = "modifier_price_in_parent"  # semantics flag: modifier price already in the parent line


# What a Toast store uses today. Left out on purpose:
# - ORDERS_INCREMENTAL: Toast may support it, but it is unverified and today's
#   sync pulls by business date only, so turning it on would change behaviour.
# - STOCK_NEGATIVE_COUNTS, ITEM_VENDORS, ITEM_COST: Toast's API does not give them
#   (negative counts and cost come from the Toast Retail CSV; vendors from item_vendors).
TOAST_CAPABILITIES: FrozenSet[Capability] = frozenset(
    {
        Capability.ORDERS_BY_BUSINESS_DATE,
        Capability.OPEN_ORDERS,
        Capability.LIVE_TODAY_SALES,
        Capability.CATALOG,
        Capability.CATALOG_CHANGE_STAMP,
        Capability.STOCK_LEVELS,
        Capability.RECEIVING_RECORDS,
        Capability.EMPLOYEES,
        Capability.DAY_TOTALS,
        Capability.ANALYTICS_REPORTS,
        Capability.CARD_FINGERPRINTS,
        Capability.CSV_CATALOG_IMPORT,
        Capability.MODIFIER_PRICE_IN_PARENT,
    }
)

# Clover (multi-store plan 4.5, Clover column), corrected for the adapter
# (website-ready track D2, app/pos/adapters/clover):
# - ORDERS_BY_BUSINESS_DATE: Clover has no business date, but the adapter
#   derives it (merchant time zone + the store's cutoff hour) and answers
#   fetch_orders(day) with the orders created in that window, so the nightly
#   sales sync runs unchanged.
# - Left out: CATALOG_CHANGE_STAMP (no cheap stamp - the nightly snapshot
#   pulls the full catalog), DAY_TOTALS / ANALYTICS_REPORTS /
#   CARD_FINGERPRINTS (no reporting API), RECEIVING_RECORDS, ITEM_VENDORS
#   (vendors come from the Item vendors editor), CSV_CATALOG_IMPORT (the
#   column map is unverified) and MODIFIER_PRICE_IN_PARENT (Clover's line
#   price excludes modifiers: each modifier is its own priced row).
CLOVER_CAPABILITIES: FrozenSet[Capability] = frozenset(
    {
        Capability.ORDERS_BY_BUSINESS_DATE,
        Capability.ORDERS_INCREMENTAL,
        Capability.OPEN_ORDERS,
        Capability.LIVE_TODAY_SALES,
        Capability.CATALOG,
        Capability.STOCK_LEVELS,
        Capability.STOCK_NEGATIVE_COUNTS,
        Capability.ITEM_COST,
        Capability.EMPLOYEES,
    }
)

# A store with no POS connection: inventory comes from CSV uploads only (the
# DEMO store; MT13 registers the csv-only adapter).
CSV_CAPABILITIES: FrozenSet[Capability] = frozenset({Capability.CSV_CATALOG_IMPORT})
