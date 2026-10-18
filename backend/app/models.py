import datetime as dt
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.timeutil import utcnow


def _inserted_value(context, column: str):
    """The value this INSERT row gives `column` (None if not given)."""
    try:
        return context.get_current_parameters().get(column)
    except KeyError:
        # SQLAlchemy 2.0 cannot isolate one row of a multi-row
        # `insert(MappedClass).values([...])` (it looks the row's values up
        # under "orders.<col>_m0"); they are under "<col>_m<row>".
        col = context.current_column
        row = col.index + 1 if getattr(col, "_is_multiparam_column", False) else 0
        return context.current_parameters.get(f"{column}_m{row}")


def _copy_of(column: str):
    """Column default: the value being inserted into `column` (the Toast
    column a POS-neutral column copies)."""

    def default(context):
        return _inserted_value(context, column)

    return default


def _is_modifier_default(context) -> bool:
    return _inserted_value(context, "parent_selection_guid") is not None


class Order(Base):
    """One row per sales line item, pulled from the Toast Orders API."""

    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("toast_selection_guid", name="uq_orders_selection_guid"),
        # POS-neutral upsert key (multi-store M1, migration 0060).
        Index("uq_orders_source_external_line_id", "source", "external_line_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Toast identifiers — selection_guid is the line item, unique per menu selection.
    toast_order_guid: Mapped[str] = mapped_column(String, index=True)
    toast_check_guid: Mapped[str] = mapped_column(String, index=True)
    toast_selection_guid: Mapped[str] = mapped_column(String, index=True)
    # Set only for modifier line items (e.g. "extra cheese") - the selection guid
    # of the item they modify. NULL for a normal top-level sold item.
    parent_selection_guid: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)

    # The Toast "business date" this order belongs to (YYYYMMDD -> date), not the
    # wall-clock date, since a late-night order can roll into the next calendar day.
    business_date: Mapped[dt.date] = mapped_column(Date, index=True)
    opened_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
    # When the guest actually paid (Toast's checks[].paidDate, falling back to the
    # order-level paidDate/createdDate) - NOT the same as business_date, which can
    # reflect a scheduled future pickup date instead (see skill gotcha #12). NULL
    # for rows synced before this column existed; only new/re-synced rows have it.
    paid_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True, index=True)

    item_guid: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    item_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    quantity: Mapped[float] = mapped_column(Float, default=0)
    unit_price: Mapped[float] = mapped_column(Float, default=0)
    net_price: Mapped[float] = mapped_column(Float, default=0)
    tax_amount: Mapped[float] = mapped_column(Float, default=0)
    voided: Mapped[bool] = mapped_column(Boolean, default=False)

    dining_option: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UTCDateTime(), default=utcnow, onupdate=utcnow
    )

    # POS-neutral copies of the Toast columns above (multi-store M1, migration
    # 0060; see app/pos/neutral_ids.py). Written next to the old columns, which
    # stay and are still what every report reads. An insert that does not set
    # them gets them copied from the Toast columns.
    source: Mapped[str] = mapped_column(String, default="toast", server_default="toast")
    external_order_id: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, default=_copy_of("toast_order_guid")
    )
    external_check_id: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, default=_copy_of("toast_check_guid")
    )
    external_line_id: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, default=_copy_of("toast_selection_guid")
    )
    parent_external_line_id: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, default=_copy_of("parent_selection_guid")
    )
    is_modifier: Mapped[bool] = mapped_column(
        Boolean, default=_is_modifier_default, server_default=false()
    )
    # Normalized sales channel (in_store / pickup / online / ...). NULL for
    # Toast until dining-option names are resolved (needs the Toast config API).
    channel: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    dining_option_id: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, default=_copy_of("dining_option")
    )

    @classmethod
    def top_level(cls):
        """WHERE clause for "real sold items": not a modifier line (skill
        gotcha #11). Exactly the old `parent_selection_guid IS NULL` filter, so
        the SQL (and the index it uses) is unchanged."""
        return cls.parent_selection_guid.is_(None)


class SyncCursor(Base):
    """How far a POS sync stream has got (multi-store M1, migration 0060).

    One row per (source, stream). For Toast's sales sync the cursor is the
    newest business date synced successfully (ISO date); a POS with a
    "modified since" API would keep a timestamp or token here instead.
    """

    __tablename__ = "sync_cursors"

    source: Mapped[str] = mapped_column(String, primary_key=True)
    stream: Mapped[str] = mapped_column(String, primary_key=True)
    cursor: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class PosItemMap(Base):
    """POS item id -> our item id (multi-store M1, migration 0060).

    Our item ids are the ids the rest of the app joins on (orders.item_guid,
    inventory_snapshots.item_id). For a Toast store they are Toast's item GUIDs,
    so every Toast id maps to itself. The table is what lets a store change
    POS later without losing its sales history.
    """

    __tablename__ = "pos_item_map"

    source: Mapped[str] = mapped_column(String, primary_key=True)
    external_id: Mapped[str] = mapped_column(String, primary_key=True)
    item_id: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)


class InventorySnapshot(Base):
    """One row per item per day, loaded from the manually uploaded Toast Retail export."""

    __tablename__ = "inventory_snapshots"
    __table_args__ = (
        UniqueConstraint("snapshot_date", "item_id", name="uq_inventory_snapshot_date_item"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    snapshot_date: Mapped[dt.date] = mapped_column(Date, index=True)
    item_id: Mapped[str] = mapped_column(String, index=True)

    name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    category_group: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    subcategory: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    inventory_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    inventory_quantity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    inventory_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    inventory_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    inventory_days_on_hand: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    gross_margin: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    gross_profit: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    last_7_day_sales: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_30_day_sales: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_90_day_sales: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_7_day_orders: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_30_day_orders: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_90_day_orders: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    supplier: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_received_from: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    inventory_last_received: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Vendor's own SKU/item number for this item - semicolon-separated in the
    # same order as `supplier` when an item has multiple vendors (mirrors that
    # field's quirk). Used on PO exports since our own item_id means nothing
    # to a vendor. See inventory_parser.py's SUPPLIER_ITEM_ID_HEADERS for the
    # source column name(s) this is matched against.
    supplier_item_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    barcode: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    source_filename: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    uploaded_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)

    # Rows built from the Toast API (source_filename "api:<iso time>",
    # services/inventory_api.py, migration 0048). NULL on CSV rows.
    # api_out_of_stock: Toast said OUT_OF_STOCK, which has no count - the
    # quantity is stored as 0; csv_quantity is then the latest CSV's count for
    # the item at build time (Negative inventory keeps a negative one).
    api_out_of_stock: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    csv_quantity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class VendorReference(Base):
    """Read-only cache of vendor cost/lead-time data from Toast's Purchasing & Receiving API.

    Scaffolded now for step 5 (ordering); not populated or read by anything in this phase.
    """

    __tablename__ = "vendors_reference"
    __table_args__ = (
        UniqueConstraint("vendor_id", "item_id", name="uq_vendor_item"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    vendor_id: Mapped[str] = mapped_column(String, index=True)
    vendor_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    item_id: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)

    cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    lead_time_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    synced_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)


class User(Base):
    """A dashboard login. There is no self-service signup or password reset -
    accounts are created and passwords set only by an admin (see routers/auth.py).
    """

    __tablename__ = "users"
    # AUTOINCREMENT (migration 0029): SQLite otherwise hands a deleted user's
    # id to the next new account, which would then inherit everything still
    # keyed by that id - DMs, tasks, uploads.
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    # JSON-encoded list of feature keys (see app/features.py) an admin has
    # granted this user - ignored entirely for an admin, who always has full
    # access. New users default to "[]" (no access) until an admin grants some.
    allowed_features: Mapped[str] = mapped_column(String, default="[]")
    # Team hub: may this non-admin assign tasks to other people (shift
    # leads)? Admins always can. See app/team/.
    can_assign: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # Migration 0033: keep sales numbers out of this person's dashboard/app.
    hide_sales: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)


class UserSession(Base):
    """Server-side session, looked up by the opaque token in the session cookie.
    Deleting a row here immediately revokes that session (used by logout and by
    an admin resetting a user's password).
    """

    __tablename__ = "user_sessions"

    token: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(UTCDateTime())


class JobRun(Base):
    """Execution log for background/manual jobs, so the dashboard can show last run + status."""

    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    job_name: Mapped[str] = mapped_column(String, index=True)
    started_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    finished_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
    status: Mapped[str] = mapped_column(String, default="running")  # running|success|failed
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    business_date: Mapped[Optional[dt.date]] = mapped_column(Date, nullable=True)


class PurchaseLogEntry(Base):
    """A manually-logged receiving record - what you actually got from a
    vendor, entered by hand. Exists because Toast's Purchasing & Receiving
    API is confirmed inaccessible with the current OAuth credentials (see
    skill gotcha #15) - `vendors_reference` is scaffolded for real Toast
    purchase data but stays empty until that scope is granted. This table is
    the manual substitute: the "purchased" leg of purchased vs. sold vs.
    counted reconciliation (Phase 3), only as complete as what gets logged.
    """

    __tablename__ = "purchase_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    item_id: Mapped[str] = mapped_column(String, index=True)
    item_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    supplier: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    quantity_received: Mapped[float] = mapped_column(Float)
    unit_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    received_date: Mapped[dt.date] = mapped_column(Date, index=True)
    notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    logged_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)


class LocationTransferLogEntry(Base):
    """A manually-confirmed record of stock physically moved between an item's
    two inventory_snapshots rows - the priced/sellable "Each" row and its bare
    "Container" storage-location duplicate (see supplier_projection.py's
    `_container_qty_by_name`). Exists because real upload history shows the
    Container row's on-hand count essentially never changes on its own - real
    transfers, if they happen, aren't reliably reflected as a detectable count
    delta the way a delivery is - so like purchase_log, this is a human-
    confirmed record, not something derived purely from diffing snapshots.
    """

    __tablename__ = "location_transfers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    item_id: Mapped[str] = mapped_column(String, index=True)
    item_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # "container_to_store": moved from back storage onto the retail floor (the
    # normal restocking direction). "store_to_container": the reverse (e.g.
    # returning excess stock to storage).
    direction: Mapped[str] = mapped_column(String)
    quantity: Mapped[float] = mapped_column(Float)
    transfer_date: Mapped[dt.date] = mapped_column(Date, index=True)
    notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    logged_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)


class CartItem(Base):
    """A persistent, shared purchase-order cart row - one item, for one
    supplier, at a quantity a staff member intends to order. Exists so a
    Supplier Projection review doesn't have to end in one immediate PDF
    download: items can be added over multiple visits/days, across several
    suppliers at once, and only turned into a PDF (via the existing
    `export_simple_po_pdf`) when someone's actually ready to send the order.

    `item_id` is null for a hand-added item that isn't in Toast's inventory
    export at all (a genuinely new item being ordered for the first time) -
    `name` is always what actually prints on the PO either way.
    """

    __tablename__ = "po_cart_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    supplier: Mapped[str] = mapped_column(String, index=True)
    item_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String)
    supplier_item_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    qty: Mapped[float] = mapped_column(Float)
    # Units per case - qty is a case count (matches the PO PDF's "Qty to
    # order (in Cases)" label), so total units ordered = qty * case_of. Only
    # used for the on-screen cost estimate (see ordering.py's cart routes);
    # never printed on the PDF itself. Defaults to 1 so a plain "1 unit per
    # case" item needs no extra input.
    case_of: Mapped[float] = mapped_column(Float, default=1.0, server_default="1")

    added_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    added_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    # PO section a hand-added line (no item_id) prints under on the PO PDF
    # (migration 0038); NULL = Other. Catalog items use po_section_rules.
    section: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Migration 0040: only admins remove lines; anyone else can ask. Set while
    # a removal request is pending, cleared on cancel / reject.
    removal_requested_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
    removal_requested_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    removal_requested_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    removal_requested_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class OpenOrderCache(Base):
    """Background-refreshed snapshot of currently-open Toast orders, one row
    per open order, fully replaced on every scan (see
    services/open_orders.py::refresh_open_orders_cache) - not an append-only
    log. Exists so the "all-time open orders" dashboard view reads instantly
    from this table instead of live-scanning ~100+ historical Toast business
    dates (which takes minutes) on every page load.
    """

    __tablename__ = "open_order_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    toast_order_guid: Mapped[str] = mapped_column(String, index=True)
    # POS-neutral copy of toast_order_guid (migration 0060).
    external_order_id: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, default=_copy_of("toast_order_guid")
    )
    business_date: Mapped[dt.date] = mapped_column(Date, index=True)
    display_number: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    opened_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
    server_guid: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    server_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    num_guests: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    approval_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    total_amount: Mapped[float] = mapped_column(Float, default=0)
    num_checks: Mapped[int] = mapped_column(Integer, default=0)
    # JSON-encoded [{"name": ..., "quantity": ...}, ...] of top-level
    # (non-modifier, non-voided) line items.
    line_items_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # JSON-encoded list of the checks' Toast tab names (usually the customer's
    # name), de-duplicated - see open_orders.py::_tab_names. NULL on rows
    # cached before migration 0045 (read as []).
    tab_names_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # NULL unless every check on the order is paid (see open_orders.py::_paid_at) -
    # a paid order that's still open is normal for an online/pickup order (Toast
    # charges on placement, closes on fulfillment) rather than a stuck dine-in tab.
    paid_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
    # Guest-facing promised time (or Toast's own estimate) - lets a "paid,
    # still open" order be told apart from a genuine future order (paid
    # tonight for tomorrow's pickup, see skill gotcha #12) vs one that's
    # simply overdue.
    promised_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
    scanned_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)


class VendorSchedule(Base):
    """One-time manual config a vendor's recurring delivery pattern: which
    weekday(s) it delivers on, and how many days before each delivery the
    order needs to be placed by. This is the only manual input the Vendor
    Order Calendar needs - everything else (what to order and how much, which
    deliveries actually came in, which were missed) is derived automatically
    from this plus data the app already collects elsewhere. See
    services/vendor_calendar.py.
    """

    __tablename__ = "vendor_schedules"
    __table_args__ = (UniqueConstraint("vendor", name="uq_vendor_schedule_vendor"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vendor: Mapped[str] = mapped_column(String, index=True)
    # JSON-encoded list of Python weekday ints (Monday=0 ... Sunday=6), e.g.
    # "[0, 3]" for a vendor that delivers every Monday and Thursday.
    delivery_weekdays: Mapped[str] = mapped_column(String)
    lead_days: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)


class VendorOrderSkip(Base):
    """One skipped order cycle on the Vendor Order Calendar: "we're not
    ordering from this vendor for this delivery". Keyed by the delivery date
    the order was for, so the order-due event stops being flagged urgent and
    that delivery shows as skipped instead of missed. Deleting the row undoes it.
    """

    __tablename__ = "vendor_order_skips"
    __table_args__ = (UniqueConstraint("vendor", "delivery_date", name="uq_vendor_order_skip"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vendor: Mapped[str] = mapped_column(String, index=True)
    delivery_date: Mapped[dt.date] = mapped_column(Date, index=True)
    skipped_by_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    # Migration 0053 (W1): NULL = "Skip this week"; "other" = hidden for this
    # cycle with a note ("Other"). Both count as skipped everywhere.
    reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    skipped_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class InvoiceSubmission(Base):
    """A supplier invoice/return/other document submitted from the dashboard -
    replaces the old Google Form workflow. Submitting one saves the uploaded
    file, then fires two independent downstream notifications: a WhatsApp
    message (via Whapi) with a link back to the file, and an email to
    Toast's invoicing inbox with the file attached as a PDF (sent for every
    submission type, matching the original Apps Script's unconditional email
    step), which Toast automatically turns into a draft bill. The row is
    always saved first and each downstream send is tracked/retried
    independently (`whatsapp_sent`/`toast_email_sent` + their `_error`
    columns) so a WhatsApp or email outage never loses the submission itself
    - see services/invoice_submission.py.
    """

    __tablename__ = "invoice_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    vendor: Mapped[str] = mapped_column(String, index=True)
    # "invoice" | "return" | "other" - every type gets emailed to Toast
    # (matches the original Apps Script, which never gated the email step).
    # Also "untracked" (no Toast email; no longer offered for new submissions
    # but still accepted) and "transfer" (store <-> container stock movement,
    # WhatsApp only) - see NO_TOAST_TYPES in services/invoice_submission.py.
    submission_type: Mapped[str] = mapped_column(String)
    # Only for submission_type == "transfer": "store_to_container" |
    # "container_to_store" (migration 0042). NULL for every other type.
    transfer_direction: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    total_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    submitted_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Denormalized snapshot of the submitter's username at submission time,
    # so the record still reads correctly even if that user account is later
    # deleted - matches logged_by_user_id's usage elsewhere in this file,
    # just with a display name kept alongside since this table is shown as
    # a list of past submissions, not just used for attribution.
    submitted_by_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    original_filename: Mapped[str] = mapped_column(String)
    stored_filename: Mapped[str] = mapped_column(String)
    content_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # SHA-256 of the uploaded file's raw bytes - lets a new upload be checked
    # against every previous one for an exact-file duplicate (a real
    # reported case: the same PDF/photo re-submitted by mistake) before
    # WhatsApp/Toast email are attempted. Nullable so existing pre-migration
    # rows just never match anything, rather than needing a backfill.
    file_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    # Unguessable token used to build the public (no-login) file link sent
    # over WhatsApp - see routers/invoices.py's public router for why that
    # one route is deliberately not behind the usual auth dependency.
    file_token: Mapped[str] = mapped_column(String, unique=True, index=True)
    # Filename of the PDF actually attached to the Toast email - equal to
    # stored_filename when the upload was already a PDF, otherwise a
    # separately-saved converted copy (see services/file_convert.py). NULL
    # if no PDF exists yet (conversion failed, or this is an "other" upload).
    pdf_filename: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    whatsapp_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    whatsapp_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # True only for a WhapiAmbiguousTimeout that reconciliation couldn't
    # confirm either way (Whapi didn't respond in time, and the message
    # doesn't show up in the chat's own recent history either - see
    # whapi_client.py). Kept separate from whatsapp_error so the UI can
    # tell "definitely failed, safe to resend" apart from "unconfirmed,
    # check WhatsApp before resending to avoid a duplicate" - both leave
    # whatsapp_sent False and whatsapp_error set, but they aren't the same
    # situation for a human deciding whether to hit Resend.
    whatsapp_ambiguous: Mapped[bool] = mapped_column(Boolean, default=False)
    toast_email_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    toast_email_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Set to the overriding admin's username when that channel's sent/error
    # fields were last set by an admin's manual override (see
    # services/invoice_submission.py::override_channel_status), not a real
    # Whapi/Toast-email attempt - cleared the moment a real automated attempt
    # (or mark_pending) touches that channel again, so it never lingers as a
    # stale label on a status the real system has since updated on its own.
    # Exists so the UI can visibly distinguish "confirmed by a real send"
    # from "an admin said so" - a silent override would be indistinguishable
    # from reality, which matters for something staff treat as a record of
    # what actually went out.
    whatsapp_status_overridden_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    toast_email_status_overridden_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # A human's manual confirmation that Toast actually finished processing
    # this into a real, closed bill - there is no Purchasing API access and
    # no webhook (see the StoreOps skill notes), so that event is
    # genuinely unobservable by this app on its own. NULL = not yet
    # confirmed. Set only via mark_toast_closed()/the matching route - never
    # inferred from whatsapp_sent/toast_email_sent, which only mean "we
    # handed it off successfully", not "Toast is done with it".
    toast_closed_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
    toast_closed_by_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)

    # Delete request/approval + soft-delete (trash), so removing a submission
    # is never an instant, unrecoverable action - see services/
    # invoice_submission.py's delete-workflow functions for the state
    # machine these columns represent. NULL deleted_at = active/visible
    # everywhere; non-NULL = sitting in trash, hidden from the normal list,
    # recoverable via restore_submission until an admin permanently deletes it.
    delete_requested_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
    delete_requested_by_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    deleted_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
    deleted_by_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class PlacedOrder(Base):
    """A record that a supplier's purchase-order PDF was actually generated
    for sending - the closest real, already-existing action to "an order was
    placed" (see `routers/ordering.py::export_purchase_order_pdf`). Before
    this, the app had no persisted concept of order history at all: the cart
    (`CartItem`) is live/mutable state that a human clears by hand once an
    order is sent, and the PDF export itself was stateless - so "Recent
    Orders Placed" (a mobile Home request) had nothing real to show. This is
    the smallest addition that makes that section real instead of fabricated:
    one row per PDF export, not a full order-management system.

    `items_json` is a plain snapshot ([{"name", "supplier_item_id", "qty"}])
    of exactly what was exported - not a live reference to `CartItem` rows,
    since those can be edited/cleared afterward and this is a historical
    record. No dollar total is stored: `SimplePOExportRequest` (the actual
    export payload) never carries cost or `item_id`, only name/SKU/qty (see
    po_export.py - cost is deliberately kept off the vendor-facing document),
    so there's no real total to attach without inventing one.

    `status` (migration 0032): "pdf" = a PO PDF was generated/shared but the
    order hasn't been confirmed; "placed" = someone pressed "Mark placed".
    Recording every PDF as its own "placed" order made a preview + Mark
    placed show up as two orders, so a supplier now has at most one recent
    "pdf" row (updated on each export) that Mark placed turns into the
    "placed" row - see services/purchase_order_log.py.
    """

    __tablename__ = "placed_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    supplier: Mapped[str] = mapped_column(String, index=True)
    item_count: Mapped[int] = mapped_column(Integer)
    items_json: Mapped[str] = mapped_column(Text)

    placed_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    placed_by_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    placed_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)

    status: Mapped[str] = mapped_column(String, default="placed", server_default="placed")

    # Migration 0053 (W1, Today-tab order actions): "outside" = someone said
    # "Order already placed" (phone, WhatsApp, the vendor's rep) - no lines,
    # nothing from the cart. NULL = placed from the app/website.
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # The delivery this order is for, when the action came from an Orders
    # due row (the Vendor Calendar then matches it to exactly that delivery).
    for_delivery_date: Mapped[Optional[dt.date]] = mapped_column(Date, nullable=True)


class ChecklistSubmission(Base):
    """A completed Daily Closing Checklist (any checklist type) -
    a native replacement for the two Google Forms of the same name (see
    services/checklist_schemas.py for the exact question set, reproduced
    from those forms' own real data). This app's DB is the sole source of
    truth here; the old Forms/Apps Script workflow is no longer used for
    these two checklists at all.

    `submitted_by_user_id`/`submitted_by_name` come ONLY from the real,
    authenticated session - never from a freely-typed name field, which is
    exactly the trust problem this native replacement exists to fix (the
    original Forms had an "Employee Name"/"Chef Name" dropdown anyone could
    pick regardless of who actually filled it out).
    """

    __tablename__ = "checklist_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # A checklist_definitions.checklist_type ("spice_town_closing",
    # "street_kitchen_closing", "street_kitchen_fsa", or one an admin created).
    checklist_type: Mapped[str] = mapped_column(String, index=True)

    submitted_by_user_id: Mapped[int] = mapped_column(Integer, index=True)
    # Denormalized snapshot, same reasoning as every other "_by_name" column
    # in this file - the record still reads correctly if the account is
    # later deleted.
    submitted_by_name: Mapped[str] = mapped_column(String)

    # JSON-encoded {row_id: answer_value} map, keyed by the stable ids in
    # checklist_schemas.py (e.g. "1_0", "g3_5") - see that module's
    # docstring for why positional ids rather than slugified label text.
    answers_json: Mapped[str] = mapped_column(Text)

    # Both computed server-side by checklist_schemas.compute_status() at
    # submission time, from answers_json - never trusted from the client.
    all_clear: Mapped[bool] = mapped_column(Boolean)
    issue_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # A hash of (checklist_type, submitted_by_user_id, answers_json) used
    # only to detect an accidental duplicate/replayed submission (a double
    # form-submit tap, a client retry after a slow response) within a short
    # window - see services/checklist_submission.py::create_submission.
    answers_hash: Mapped[str] = mapped_column(String, index=True)

    whatsapp_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    whatsapp_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    whatsapp_ambiguous: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)

    # The checklist version (checklist_definition_versions.version) this was
    # validated against and renders with (migration 0039). NULL = submitted
    # before the checklist builder = version 1.
    definition_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Public checklist link (migration 0041, docs/PUBLIC_CHECKLIST_LINK_CONTRACT.md):
    # True when filled on the logged-out page /c/{slug}-{token}. Such a row has
    # submitted_by_user_id = 0 (no account; the API also says 0 because app
    # build 5 parses it as a non-null int) and submitted_by_name = public_name,
    # the name the person picked / typed (plain text, checked by
    # services/checklist_public.clean_name). NULL on every older row = False.
    via_public_link: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    public_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class ChecklistPublicLink(Base):
    """A logged-out link for ONE checklist (migration 0041, admin-only to
    configure; services/checklist_public.py). The URL is
    {PUBLIC_BASE_URL}/c/{slug}-{token}, e.g. /c/kitchen-4821: `token` is a
    4-digit code for links made or rotated since Oct 1 (an older link keeps
    its 22-character token until rotated). Lookups need the slug AND the
    code (constant-time compare). Rotating replaces the code, so the old URL
    stops working."""

    __tablename__ = "checklist_public_links"
    __table_args__ = (
        UniqueConstraint("checklist_type", name="uq_checklist_public_links_type"),
        UniqueConstraint("slug", name="uq_checklist_public_links_slug"),
        UniqueConstraint("token", name="uq_checklist_public_links_token"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    checklist_type: Mapped[str] = mapped_column(String)
    slug: Mapped[str] = mapped_column(String)
    token: Mapped[str] = mapped_column(String)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # "list" (pick a name from allowed_names) | "free_text" (type a name)
    name_mode: Mapped[str] = mapped_column(String, default="list")
    allowed_names_json: Mapped[str] = mapped_column(Text, default="[]")
    max_per_hour: Mapped[int] = mapped_column(Integer, default=6)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    created_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    rotated_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
    rotated_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_used_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)


class ChecklistDefinition(Base):
    """A checklist admins can edit on the website (checklist builder, migration
    0039). The questions live in versions (ChecklistDefinitionVersion):
    publishing creates a new version and points `current_version` at it, so a
    submission always renders with the version it was submitted against.
    `draft_json` is an unpublished edit (items form), NULL when there's none.
    title / short_name / description / settings / active / sort_order are not
    versioned - they apply immediately. See services/checklist_admin.py.
    """

    __tablename__ = "checklist_definitions"
    __table_args__ = (UniqueConstraint("checklist_type", name="uq_checklist_definitions_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    checklist_type: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    short_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    # JSON: {"closing": bool, "whatsapp_group": "store" | "kitchen" | null, "all_as_usual": bool}
    settings_json: Mapped[str] = mapped_column(Text, default="{}")
    current_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    draft_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    draft_updated_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
    draft_updated_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    created_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class ChecklistDefinitionVersion(Base):
    """One published version of a checklist's questions. Immutable once
    written. schema_json is either the original legacy shape (the three seeded
    checklists' early versions) or items form ({"format": "items", ...}) - see
    services/checklist_engine.py."""

    __tablename__ = "checklist_definition_versions"
    __table_args__ = (
        UniqueConstraint("definition_id", "version", name="uq_checklist_definition_versions_def_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    definition_id: Mapped[int] = mapped_column(Integer, ForeignKey("checklist_definitions.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    schema_json: Mapped[str] = mapped_column(Text)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    created_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class IssueResolution(Base):
    """A closed Fix Issues item (missing/invalid barcode, duplicate, dead stock)
    with the reason picked when closing it. One row per (issue_type,
    issue_key): closing again updates it, reopening deletes it. issue_key is
    the item_id, except for duplicates, where it's the lowercased shared name
    (the whole duplicate group closes together).
    """

    __tablename__ = "issue_resolutions"
    __table_args__ = (UniqueConstraint("issue_type", "issue_key", name="uq_issue_resolution"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    issue_type: Mapped[str] = mapped_column(String, index=True)
    issue_key: Mapped[str] = mapped_column(String)
    item_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    item_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    reason: Mapped[str] = mapped_column(String)
    reason_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    closed_by_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    closed_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    # Migration 0040: a "more time" close (fix_issues.SNOOZE_DAYS) comes back
    # on this store-local day if the item still shows the problem. NULL on a
    # snooze reason = closed day + its days (rows closed before 0040).
    snooze_until: Mapped[Optional[dt.date]] = mapped_column(Date, nullable=True)


class IssueResolutionEvent(Base):
    """Fix Issues history (migration 0040): one row per close and per reopen,
    never deleted - IssueResolution only holds the current state. `how` on a
    reopen: snooze_ended (the "more time" ran out and it still shows),
    manual (someone pressed Reopen)."""

    __tablename__ = "issue_resolution_events"
    __table_args__ = (Index("ix_issue_resolution_events_type_key", "issue_type", "issue_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    issue_type: Mapped[str] = mapped_column(String)
    issue_key: Mapped[str] = mapped_column(String)
    item_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    item_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    event: Mapped[str] = mapped_column(String)  # closed | reopened
    reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    reason_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    how: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    by_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    snooze_until: Mapped[Optional[dt.date]] = mapped_column(Date, nullable=True)
    at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class DeviceToken(Base):
    """A phone registered for push notifications - one row per FCM
    registration token. Keyed by the token, not (user, device): FCM hands the
    same phone the same token no matter who logs in on it, so when a second
    user logs in on a shared store phone the row simply moves to them
    (services/notifications.py::register_device) and the first user stops
    getting that phone's alerts. Rows are removed on logout and whenever FCM
    reports the token as no longer valid (app uninstalled, token rotated).
    """

    __tablename__ = "device_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    token: Mapped[str] = mapped_column(String, unique=True)
    # "ios" | "android"
    platform: Mapped[str] = mapped_column(String)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    last_seen_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)


class NotificationPref(Base):
    """A user's on/off choice for one alert (services/notifications.py
    ALERTS). Only an explicit choice gets a row - no row means enabled, so a
    newly added alert reaches everyone with access without a backfill.
    """

    __tablename__ = "notification_prefs"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_notification_pref_user_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    key: Mapped[str] = mapped_column(String)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class NotificationLog(Base):
    """One row per alert actually delivered through Firebase, keyed by its
    dedupe key (e.g. "order_due:Babco:2026-09-28") - the only thing stopping
    the frequent jobs (every 10/30 min) from re-sending the same alert on
    every run. Written only after a real send, never on a dry run with
    Firebase unconfigured.
    """

    __tablename__ = "notification_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String, index=True)
    dedupe_key: Mapped[str] = mapped_column(String, unique=True)
    sent_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    recipient_count: Mapped[int] = mapped_column(Integer, default=0)


class CategoryOverride(Base):
    """An app-side correction of Toast's category for some items (services/
    category_overrides.py). Toast API access is read-only, so a category that
    is wrong in Toast is corrected here until someone fixes it in Toast - e.g.
    every item of one supplier -> RETAIL / Snacks & Savories / Biscuits,
    Cookies & Rusk. Applied when items are read (never written into
    inventory_snapshots, which keeps Toast's real values).

    match_type "supplier": the rule matches an item when ANY of its
    ";"-separated suppliers equals match_value (case-insensitive, trimmed), so
    new items from that vendor are covered too. match_type "item_id": one
    item; wins over any supplier rule. match_type "category" (a category
    mapping rule): match_value is a JSON array of Toast's raw [group,
    category, subcategory] (subcategory null = any, "" = none); loses to item
    and supplier rules. A NULL subcategory keeps the item's own; "" = none.
    """

    __tablename__ = "category_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_type: Mapped[str] = mapped_column(String)
    match_value: Mapped[str] = mapped_column(String)
    category_group: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String)
    subcategory: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Who / when (migration 0036): username that added the rule, and the last
    # change to it (e.g. its deactivation). NULL on rules made before 0036.
    created_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    updated_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
    updated_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class PoSectionRule(Base):
    """An owner edit of the PO section an item prints under on the vendor PO
    PDF (services/po_section_rules.py; automatic sections come from
    services/po_sections.py). rule_type "item" (match_value = item_id),
    "vendor" (match_value = vendor name, any of an item's ";"-separated
    suppliers) or "rename" (match_value = a section name, `section` = its new
    name - a rename, or a merge when the new name already exists).

    Never deleted: a change deactivates the old row (updated_at / updated_by)
    and adds a new one. created_batch / updated_batch group the rows one
    change touched (Undo last change). Toast categories (category_overrides)
    are separate and unaffected.
    """

    __tablename__ = "po_section_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_type: Mapped[str] = mapped_column(String)
    match_value: Mapped[str] = mapped_column(String)
    section: Mapped[str] = mapped_column(String)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    created_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    updated_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
    updated_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_batch: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    updated_batch: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class WhatsAppGroup(Base):
    """A WhatsApp group a checklist's summary can be posted to
    (settings.whatsapp_group = `key`; services/whatsapp_groups.py, migration
    0044). The two built-ins (store, kitchen) have chat_id NULL = use the
    env setting (WHAPI_SPICE_TOWN_/STREET_KITCHEN_CHECKLIST_GROUP_ID); they
    can be renamed or turned off but never deleted. Admins add more from the
    groups the store's WhatsApp number belongs to."""

    __tablename__ = "whatsapp_groups"
    __table_args__ = (UniqueConstraint("key", name="uq_whatsapp_groups_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String)
    label: Mapped[str] = mapped_column(String)
    chat_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    created_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    updated_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
    updated_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Migration 0046: an admin ticked "Use for flyers" - only these groups
    # can receive a marketing flyer (services/flyers.py). The built-ins start
    # False like every other group.
    marketing: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class Flyer(Base):
    """A marketing flyer (image or PDF) in the library
    (docs/MARKETING_FLYERS_CONTRACT.md; services/flyers.py, migration 0046).
    The file is on disk under FLYERS_DIR at file_rel_path, e.g.
    "Flowers/2026-10-04 Diwali Garlands (12).jpg" - an image is stored only
    as its normalized JPEG, a PDF as uploaded. Never hard-deleted: a delete
    sets deleted_at and moves the file under _Deleted/."""

    __tablename__ = "flyers"
    # The id is part of the file name, so it must never be handed out twice.
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String, index=True)
    caption: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_rel_path: Mapped[str] = mapped_column(String)
    original_filename: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    mime: Mapped[str] = mapped_column(String)
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String, index=True)
    # Pixels for an image; NULL for a PDF.
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    created_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_by_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    updated_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
    deleted_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True, index=True)


class FlyerSend(Base):
    """One flyer going to one target (a marketing WhatsApp group) as part of
    a send batch. status: queued -> sending -> sent | failed; a failed one
    can be retried (back to queued). target_ref is the chat id snapshot and
    is never returned to clients. `channel` is "whatsapp" today - it exists
    so social media posting can be added later as more channels."""

    __tablename__ = "flyer_sends"
    __table_args__ = (
        UniqueConstraint("batch_id", "target_key", name="uq_flyer_sends_batch_target"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    flyer_id: Mapped[int] = mapped_column(Integer, index=True)
    batch_id: Mapped[str] = mapped_column(String, index=True)
    channel: Mapped[str] = mapped_column(String, default="whatsapp", server_default="whatsapp")
    target_key: Mapped[str] = mapped_column(String)
    target_label: Mapped[str] = mapped_column(String)
    target_ref: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, index=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    provider_message_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    requested_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    requested_by_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    requested_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    sent_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    # Worker bookkeeping (never returned): when it last went into the queue
    # (the request, or a retry), when the worker last started sending it,
    # how many attempts, and whether the last one may have gone through
    # anyway (a timeout) - a retry then checks the group before resending.
    queued_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
    attempted_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    ambiguous: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class CriticalVendorDismissal(Base):
    """An admin hid a Critical vendor from the Today tile and the
    critical-vendors push, for everyone (docs/CRITICAL_VENDOR_DISMISS_CONTRACT.md;
    services/critical_dismissals.py, migration 0047). The Reorder Candidates
    report still lists the vendor and its urgency unchanged.

    kind: close (until something new turns critical for it - an item not in
    critical_item_ids_json - or `until` = created_at + 14 days), skip_week
    (until next Monday 00:00 store time) or snooze (until a chosen time).
    Never deleted: it ends by setting cleared_at / cleared_reason
    (show_again, new_critical_item, expired, superseded)."""

    __tablename__ = "critical_vendor_dismissals"
    # "Show again" takes an id, so an id must never be handed out twice.
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # The vendor name exactly as the Reorder Candidates report groups it.
    vendor: Mapped[str] = mapped_column(String, index=True)
    kind: Mapped[str] = mapped_column(String)
    reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    until: Mapped[dt.datetime] = mapped_column(UTCDateTime())
    critical_item_ids_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_by_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    cleared_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True, index=True)
    cleared_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cleared_by_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    cleared_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class ToastMenuItem(Base):
    """The latest /menus/v2/menus pull, one row per menu item guid (= the
    inventory item_id). Written by services/inventory_api.py (the hourly
    barcode re-check and the API snapshot build), replaced as a whole when
    the menu's lastUpdated changes. Migration 0048."""

    __tablename__ = "toast_menu_items"

    item_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    sku: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    plu: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pricing_strategy: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    menu_last_updated: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
    fetched_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)


class SuggestionDismissal(Base):
    """Someone said "not this one" to a computed suggestion (docs/
    AUTOMATION_BATCH_CONTRACT.md items 4, 5, 7; migration 0049): a suggested
    delivery (services/delivery_suggestions.py), a suggested "Closed in
    Toast" (services/invoice_close_suggestions.py) or a suggested vendor
    schedule (services/schedule_suggestions.py). The suggestions themselves
    are computed on request and never stored. Undo deletes the row.

    kind: delivery | invoice_close | vendor_schedule. suggestion_key is built
    by the service that made the suggestion (see each one's `*_key`)."""

    __tablename__ = "suggestion_dismissals"
    __table_args__ = (UniqueConstraint("kind", "suggestion_key", name="uq_suggestion_dismissal"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String, index=True)
    suggestion_key: Mapped[str] = mapped_column(String)
    dismissed_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    dismissed_by_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
