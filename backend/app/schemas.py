import datetime as dt
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class JobStatusOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_name: str
    started_at: dt.datetime
    finished_at: Optional[dt.datetime]
    status: str
    detail: Optional[str]
    business_date: Optional[dt.date]


class InventoryUploadResult(BaseModel):
    snapshot_date: str
    rows_loaded: int
    rows_skipped: int
    columns_used: list[str]
    columns_ignored: list[str]


class SimplePOExportItem(BaseModel):
    name: str
    supplier_item_id: Optional[str] = None
    qty: float


class SimplePOExportRequest(BaseModel):
    supplier: str
    items: list[SimplePOExportItem]


class CartItemIn(BaseModel):
    item_id: Optional[str] = None
    name: str
    supplier_item_id: Optional[str] = None
    qty: float
    case_of: float = 1.0


class CartAddRequest(BaseModel):
    supplier: str
    items: list[CartItemIn]


class CartItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    supplier: str
    item_id: Optional[str]
    name: str
    supplier_item_id: Optional[str]
    qty: float
    case_of: float
    added_at: dt.datetime
    # Not stored on CartItem itself - filled in by the /cart routes from the
    # latest inventory snapshot at read time, so it always reflects Toast's
    # current cost rather than a stale value from when the item was added.
    # None for a hand-added item with no Toast item_id to look cost up from.
    unit_cost: Optional[float] = None
    total_units: float
    line_cost: Optional[float] = None
    # Additive (Sep 2026): on hand from the latest upload incl. the Container
    # duplicate row, for the cart's "(in stock: N)" bracket. Display only -
    # never part of the PO PDF. None for a hand-added item / no inventory row.
    on_hand_qty: Optional[float] = None
    container_qty: Optional[float] = None
    # Additive (PO sections): the section this line prints under on the PO
    # PDF, and why - item / vendor (a rule), automatic, line (a hand-added
    # line's own section) or other.
    section: Optional[str] = None
    section_source: Optional[str] = None
    # Additive (Sep 30): a pending removal request {at, by, by_user_id,
    # reason}, or None. Only admins remove lines; others ask.
    removal_requested: Optional[dict] = None
    # Additive (X1): the line's projection context (weekly sales, on hand,
    # suggested qty + basis) - only on GET /cart?projection=1, else None.
    # services/order_suggestion.py.
    projection: Optional[dict] = None


class CartRemovalRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=300)


class CartItemUpdate(BaseModel):
    qty: Optional[float] = None
    case_of: Optional[float] = None


class PurchaseLogCreate(BaseModel):
    item_id: str
    item_name: Optional[str] = None
    supplier: Optional[str] = None
    quantity_received: float
    unit_cost: Optional[float] = None
    received_date: dt.date
    notes: Optional[str] = None


class PurchaseLogImportResult(BaseModel):
    rows_loaded: int
    rows_skipped: int
    errors: list[str]
    total_errors: int


class DeliveryConfirmItem(BaseModel):
    item_id: str
    item_name: Optional[str] = None
    quantity_received: float


class DeliveryConfirmRequest(BaseModel):
    vendor: str
    received_date: dt.date
    items: list[DeliveryConfirmItem]


class TransferConfirmItem(BaseModel):
    item_id: str
    item_name: Optional[str] = None
    quantity: float


class TransferConfirmRequest(BaseModel):
    direction: Literal["container_to_store", "store_to_container"]
    transfer_date: dt.date
    items: list[TransferConfirmItem]


class AskTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class AskRequest(BaseModel):
    question: str
    history: list[AskTurn] = []


class PurchaseLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    item_id: str
    item_name: Optional[str]
    supplier: Optional[str]
    quantity_received: float
    unit_cost: Optional[float]
    received_date: dt.date
    notes: Optional[str]
    created_at: dt.datetime


# Multi-store MT6 (additive): "ap_email" is accepted as the POS-neutral name
# of the "toast_email" channel and read as it, before validation - so every
# other answer (including a 422's wording) is exactly as before.
def _ap_channel(value):
    return "toast_email" if value == "ap_email" else value


class InvoiceResendRequest(BaseModel):
    channels: list[Literal["whatsapp", "toast_email"]]

    @field_validator("channels", mode="before")
    @classmethod
    def _neutral_channels(cls, value):
        return [_ap_channel(v) for v in value] if isinstance(value, list) else value


class InvoiceStatusOverrideRequest(BaseModel):
    channel: Literal["whatsapp", "toast_email"]
    status: Literal["sent", "failed", "pending"]

    @field_validator("channel", mode="before")
    @classmethod
    def _neutral_channel(cls, value):
        return _ap_channel(value)


class ChecklistSubmitRequest(BaseModel):
    # {row_id: answer_value} - validated against the real schema
    # (checklist_schemas.validate_answers) before anything is saved, not
    # just type-checked here.
    answers: dict[str, str]
    # Additive (checklist builder): the version the form was loaded with
    # (the schema's "version"). Optional - app build 5 doesn't send it. The
    # submission is always stored against the current version; this only
    # lets an out-of-date form get a clear "open it again" (409).
    definition_version: Optional[int] = None


class VendorOrderSkipRequest(BaseModel):
    vendor: str
    delivery_date: dt.date


class VendorScheduleCreate(BaseModel):
    vendor: str
    delivery_weekdays: list[int]
    lead_days: int


class VendorScheduleUpdate(BaseModel):
    delivery_weekdays: Optional[list[int]] = None
    lead_days: Optional[int] = None
    active: Optional[bool] = None
