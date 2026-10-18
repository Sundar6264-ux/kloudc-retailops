"""Vendor contacts and the log of purchase orders sent from the app
(docs/AUTOMATION_BATCH_CONTRACT.md item 8, migration 0050).

Kept out of models.py (a file every batch edits) - imported by
services/po_send.py, which the app always loads through routers/po_send.py.
"""

import datetime as dt
from typing import Optional

from sqlalchemy import Boolean, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.timeutil import utcnow


class VendorContact(Base):
    """Who to send a vendor's purchase orders to. Managed by admins on the
    website's Vendor contacts page. A vendor can have several contacts (a
    sales rep and the office); `send_po` contacts with an email are the
    default recipients of "Send PO".

    `vendor` is the name as typed (normally Toast's spelling, picked from the
    supplier list); `vendor_key` is it trimmed and lowercased, which is how a
    cart vendor finds its contacts ("Raja Foods " and "raja foods" are the
    same vendor, as in the critical-vendor dismissals).
    """

    __tablename__ = "vendor_contacts"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vendor: Mapped[str] = mapped_column(String)
    vendor_key: Mapped[str] = mapped_column(String, index=True)
    contact_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # A phone number (digits, any formatting) or a WhatsApp chat/group id
    # ("...@g.us"). Only used when PO_WHATSAPP_ENABLED is on.
    whatsapp: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    send_po: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    # Default CC addresses for this contact's POs (JSON list of emails, at
    # most 10; migration 0056). Send PO pre-fills its CC list from them.
    cc_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_by_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_by_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    updated_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)


class PoSend(Base):
    """One "Send PO" attempt: who sent which vendor's PO, to whom, how it
    went, and the order it was recorded as. Never deleted - this is the send
    log.

    - idempotency_key: made by the client when the Send dialog opens; a
      repeat POST with the same key (double click, network retry) gets this
      row back instead of a second email.
    - status: sending -> sent | partly_sent | failed | uncertain.
      "uncertain" = the email (or WhatsApp) may have gone out although we
      didn't get a confirmation (the connection dropped mid-send); the cart
      is left alone and a new send asks the person to check first.
    - email_* / whatsapp_*: per channel; *_status is None when that channel
      wasn't asked for. email_message_id is our RFC 5322 Message-ID (search
      for it in Gmail's Sent folder); whatsapp_message_id is Whapi's id.
    - items_json: exactly the lines on the emailed PDF ({name,
      supplier_item_id, qty, item_id, case_of}); cart_item_ids_json: the
      cart lines they came from (only those are cleared on success).
    - placed_order_id: the placed_orders row the send was recorded as.
    """

    __tablename__ = "po_sends"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_po_sends_idempotency_key"), {"sqlite_autoincrement": True})

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(String)
    supplier: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, default="sending")

    subject: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pdf_filename: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    items_json: Mapped[str] = mapped_column(Text, default="[]")
    cart_item_ids_json: Mapped[str] = mapped_column(Text, default="[]")

    email_to_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    email_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    email_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    email_message_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # The CC addresses this send used (JSON list; NULL = none - migration 0056).
    email_cc_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    whatsapp_to: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    whatsapp_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    whatsapp_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    whatsapp_message_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    placed_order_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sent_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sent_by_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    finished_at: Mapped[Optional[dt.datetime]] = mapped_column(UTCDateTime(), nullable=True)
