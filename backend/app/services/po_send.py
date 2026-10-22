"""Send a vendor's purchase order from the cart (docs/AUTOMATION_BATCH_CONTRACT.md
item 8): vendor contacts (admin-managed), a preview, and the send itself.

The send:
1. takes the vendor's cart lines with qty > 0 (what "Download PDF" puts on
   the PDF), builds the same PO PDF (`build_po_pdf`, also used by the
   Download PDF route) and a short email around it;
2. emails it through the store's Gmail account - the same SMTP + app-password
   transport the invoice emails to Toast use (services/toast_email.py);
3. only when it went out: records the order exactly like "Order placed"
   (purchase_order_log.place_sent_order - the vendor's recent "pdf" row
   becomes the placed order) and clears the cart lines that were on the PO.
   A failed send leaves the cart and placed orders untouched.

Every attempt is a `po_sends` row (who / when / to / status / message id).
The client makes an idempotency key when the Send dialog opens; a repeat
POST with it gets the same row back, never a second email. A send that may
have gone out without a confirmation ("uncertain": the connection dropped
mid-send) leaves the cart alone, and the next send for that vendor first
asks the person to check Gmail's Sent folder (confirm_resend).

WhatsApp is optional and OFF unless PO_WHATSAPP_ENABLED is set: the store's
Whapi plan is the sandbox, which can only message 5 chats, so a PO to a new
vendor chat would fail with 402.
"""

import datetime as dt
import json
import logging
import re
import smtplib
import socket
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from typing import List, Optional, Tuple

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.store import settings_view as sv
from app.models import CartItem, User
from app.po_send_models import PoSend, VendorContact
from app.services import whapi_client
from app.services.po_cart import list_cart
from app.services.po_export import export_simple_po_pdf
from app.services.toast_email import EmailNotConfigured, pilot_guard
from app.timeutil import restaurant_today, utcnow
from app.tenancy import advisory_lock, tenant_lock

logger = logging.getLogger(__name__)

# The store's name, address and phone come from the store settings
# (app/store/settings_view.py): STORE_NAME, STORE_ADDRESS, STORE_PHONE.

NOTE_MARKER = "[[NOTE]]"
MAX_NOTE = 1000
MAX_RECIPIENTS = 10
# Email CCs (W2, Oct 14): per contact (defaults) and per send.
MAX_CC = 10
# SMTP host / port / TLS come from SMTP_HOST, SMTP_PORT, SMTP_SSL.
SMTP_TIMEOUT = 30

# A "sending" row older than this is treated as interrupted (uncertain).
SENDING_STALE = dt.timedelta(minutes=10)
# How far back an uncertain send makes the next send ask first.
UNCERTAIN_LOOKBACK = dt.timedelta(hours=24)

STATUS_SENDING = "sending"
STATUS_SENT = "sent"
STATUS_PARTLY = "partly_sent"
STATUS_FAILED = "failed"
STATUS_UNCERTAIN = "uncertain"
CHANNELS = ("email", "whatsapp")

# What store users read (G29): no provider name, plan or status code. The
# reason it is off (the WhatsApp sender's plan reaches only a few chats) is
# for the operator, in config.py next to po_whatsapp_enabled.
WHATSAPP_OFF_REASON = (
    "WhatsApp sending of POs is off for this store. Email the PO, or download the PDF and share it on "
    "WhatsApp yourself."
)
WHATSAPP_CHAT_LIMIT_MESSAGE = (
    "WhatsApp couldn't reach this chat: the store's WhatsApp number can only message a few chats. Email the PO, "
    "or download the PDF and share it on WhatsApp yourself."
)

_send_lock = tenant_lock("po_send._send_lock")

_EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,100}$")
_CHAT_ID_RE = re.compile(r"^[0-9-]{5,40}@(g\.us|s\.whatsapp\.net|c\.us)$")


class PoSendError(Exception):
    """A request the send / contacts endpoints refuse; `code` lets the
    website react (e.g. "check_previous" opens the "did it go out?" step)."""

    def __init__(self, status_code: int, detail: str, code: Optional[str] = None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.code = code


class _ChannelError(Exception):
    def __init__(self, message: str, maybe_sent: bool = False):
        super().__init__(message)
        self.maybe_sent = maybe_sent


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def vendor_key(name: Optional[str]) -> str:
    return " ".join((name or "").split()).lower()


def _clean_line(value: Optional[str], limit: int) -> Optional[str]:
    """One line of plain text: control characters (CR/LF included - these
    end up near email headers) dropped, spaces collapsed."""
    if value is None:
        return None
    text = "".join(ch if ch.isprintable() else " " for ch in str(value))
    text = " ".join(text.split())
    return text[:limit] or None


def _clean_text(value: Optional[str], limit: int) -> Optional[str]:
    """Multi-line plain text (notes): line breaks kept, other controls dropped."""
    if value is None:
        return None
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch == "\n" or ch.isprintable())
    text = "\n".join(line.rstrip() for line in text.split("\n")).strip()
    return text[:limit] or None


def normalize_email(value: Optional[str]) -> Optional[str]:
    text = (value or "").strip()
    if not text:
        return None
    if len(text) > 254 or not _EMAIL_RE.match(text):
        raise PoSendError(422, f"'{_clean_line(text, 80)}' isn't an email address.")
    return text


def normalize_whatsapp(value: Optional[str]) -> Optional[str]:
    """A phone number (any formatting, 10 digits = US) or a chat / group id."""
    text = (value or "").strip()
    if not text:
        return None
    if "@" in text:
        if not _CHAT_ID_RE.match(text):
            raise PoSendError(422, "A WhatsApp chat id looks like 120363...@g.us (a group) or 1860...@s.whatsapp.net.")
        return text
    digits = re.sub(r"\D", "", text)
    if len(digits) == 10:
        digits = "1" + digits
    if not 8 <= len(digits) <= 15:
        raise PoSendError(422, "A WhatsApp number needs 10 digits (US) or a country code and number.")
    return "+" + digits


def whatsapp_chat_id(value: str) -> str:
    return value if "@" in value else re.sub(r"\D", "", value) + "@s.whatsapp.net"


def _fmt_qty(value) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "0"
    return str(int(f)) if f.is_integer() else f"{f:g}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def email_configured() -> bool:
    cfg = sv.smtp_config()
    return bool(cfg.username and cfg.password)


def whatsapp_enabled() -> bool:
    return bool(get_settings().po_whatsapp_enabled)


# --------------------------------------------------------------------------
# Vendor contacts
# --------------------------------------------------------------------------


def contact_out(row: VendorContact) -> dict:
    return {
        "id": row.id,
        "vendor": row.vendor,
        "contact_name": row.contact_name,
        "email": row.email,
        "whatsapp": row.whatsapp,
        "notes": row.notes,
        "send_po": bool(row.send_po),
        # Additive (W2): this contact's default CC addresses.
        "cc": contact_cc(row),
        "created_by_name": row.created_by_name,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_by_name": row.updated_by_name,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def contact_cc(row: VendorContact) -> list:
    return [e for e in _json_list(getattr(row, "cc_json", None)) if isinstance(e, str) and e]


def normalize_cc(values, exclude=()) -> list:
    """A CC list as sent by a client: each one a valid email, duplicates and
    addresses already in `exclude` (the To list) dropped, case-insensitive,
    at most MAX_CC. Raises PoSendError(422) on a bad address or too many."""
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)):
        raise PoSendError(422, "CC must be a list of email addresses.")
    seen = {str(e).lower() for e in exclude}
    out: list = []
    for value in values:
        if not isinstance(value, str):
            raise PoSendError(422, "CC must be a list of email addresses.")
        # A pasted "a@x.com, b@y.com" line counts as two.
        for part in re.split(r"[,;\s]+", value):
            email = normalize_email(part)
            if email is None or email.lower() in seen:
                continue
            seen.add(email.lower())
            out.append(email)
    if len(out) > MAX_CC:
        raise PoSendError(422, f"At most {MAX_CC} CC addresses.")
    return out


def list_contacts(db: Session, vendor: Optional[str] = None) -> List[VendorContact]:
    stmt = select(VendorContact)
    if vendor is not None:
        stmt = stmt.where(VendorContact.vendor_key == vendor_key(vendor))
    stmt = stmt.order_by(VendorContact.vendor_key, VendorContact.id)
    return list(db.execute(stmt).scalars())


def _apply_contact(row: VendorContact, data: dict, partial: bool) -> None:
    if not partial or "vendor" in data:
        vendor = _clean_line(data.get("vendor"), 200)
        if not vendor:
            raise PoSendError(422, "Pick the vendor.")
        row.vendor = vendor
        row.vendor_key = vendor_key(vendor)
    if not partial or "contact_name" in data:
        row.contact_name = _clean_line(data.get("contact_name"), 120)
    if not partial or "email" in data:
        row.email = normalize_email(data.get("email"))
    if not partial or "whatsapp" in data:
        row.whatsapp = normalize_whatsapp(data.get("whatsapp"))
    if not partial or "notes" in data:
        row.notes = _clean_text(data.get("notes"), 1000)
    if not partial or "send_po" in data:
        value = data.get("send_po")
        row.send_po = True if value is None else bool(value)
    if "cc" in data:
        # The contact's own address never needs to be its own CC.
        cc = normalize_cc(data.get("cc"), exclude=[row.email] if row.email else [])
        row.cc_json = json.dumps(cc) if cc else None
    elif row.email and contact_cc(row):
        cc = [e for e in contact_cc(row) if e.lower() != row.email.lower()]
        row.cc_json = json.dumps(cc) if cc else None
    if not row.email and not row.whatsapp:
        raise PoSendError(422, "Add an email or a WhatsApp number.")


def create_contact(db: Session, data: dict, user: User) -> VendorContact:
    row = VendorContact(created_by_user_id=user.id, created_by_name=user.username, created_at=utcnow())
    _apply_contact(row, data, partial=False)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_contact(db: Session, contact_id: int, data: dict, user: User) -> VendorContact:
    row = db.get(VendorContact, contact_id)
    if row is None:
        raise PoSendError(404, "Contact not found.")
    try:
        _apply_contact(row, data, partial=True)
    except PoSendError:
        db.rollback()
        raise
    row.updated_by_name = user.username
    row.updated_at = utcnow()
    db.commit()
    db.refresh(row)
    return row


def delete_contact(db: Session, contact_id: int) -> None:
    row = db.get(VendorContact, contact_id)
    if row is None:
        raise PoSendError(404, "Contact not found.")
    db.delete(row)
    db.commit()


# --------------------------------------------------------------------------
# The PO: lines, PDF, email text
# --------------------------------------------------------------------------


def cart_lines(db: Session, supplier: str) -> Tuple[List[CartItem], List[dict]]:
    """(every cart row for the vendor, the lines that go on the PO). The PO
    gets the rows with qty > 0, like Download PDF; the cart page and Mark
    placed match the vendor by its exact name, so this does too."""
    rows = [r for r in list_cart(db) if r.supplier == supplier]
    items = [
        {
            "name": r.name,
            "supplier_item_id": r.supplier_item_id or None,
            "qty": r.qty,
            "item_id": r.item_id,
            "case_of": r.case_of,
        }
        for r in rows
        if r.qty is not None and r.qty > 0
    ]
    return rows, items


def po_lines(items: list) -> list:
    """The PO's printed lines: name, vendor code and qty, plus the optional
    case fields (`cases` / `case_size`) when a line has them."""
    lines = []
    for i in items:
        line = {"name": i["name"], "supplier_item_id": i.get("supplier_item_id"), "qty": i["qty"]}
        for key in ("cases", "case_size"):
            if i.get(key) not in (None, ""):
                line[key] = i[key]
        lines.append(line)
    return lines


def group_po_lines(db: Session, supplier: str, lines: list) -> Optional[list]:
    """[(section, lines)] exactly as the PDF prints them, or None when the
    grouping fails (the PO is then made ungrouped)."""
    from app.services.po_section_rules import group_for_pdf
    from app.services.reorder import latest_inventory_by_item

    try:
        return group_for_pdf(db, lines, latest_inventory_by_item(db), supplier)
    except Exception:  # noqa: BLE001
        logger.exception("PO section grouping failed for %s - building ungrouped", supplier)
        return None


def build_po_pdf(db: Session, supplier: str, items: list, prepared: Optional[dt.date] = None) -> bytes:
    """The vendor PO PDF, grouped into picking sections - shared with the
    Download PDF route and the PO downloads (services/po_download.py). Any
    grouping failure falls back to the ungrouped PDF so a PO can always be
    made."""
    lines = po_lines(items)
    sections = group_po_lines(db, supplier, lines)
    extra = {"prepared": prepared} if prepared else {}
    try:
        return export_simple_po_pdf(supplier, lines, sections, **extra)
    except Exception:  # noqa: BLE001
        if sections is None:
            raise
        logger.exception("Grouped PO PDF failed for %s - building ungrouped", supplier)
        return export_simple_po_pdf(supplier, lines, **extra)


def pdf_filename(supplier: str, day: Optional[dt.date] = None) -> str:
    safe = "".join(c if c.isalnum() else "-" for c in supplier.lower()).strip("-") or "vendor"
    safe = re.sub(r"-{2,}", "-", safe)
    return f"purchase-order-{safe}-{(day or restaurant_today()).isoformat()}.pdf"


def compose(supplier: str, items: list, day: Optional[dt.date] = None) -> Tuple[str, str]:
    """(subject, body template). The template has NOTE_MARKER as its own
    paragraph; render_body puts the sender's note there (or drops it)."""
    day = day or restaurant_today()
    cases = sum(float(i.get("qty") or 0) for i in items)
    store_name = sv.store_display_name()
    subject = f"Purchase Order - {store_name} - {_clean_line(supplier, 200)} - {day:%b} {day.day}, {day.year}"
    signature = "\n".join(line for line in (store_name, sv.store_address(), sv.store_phone()) if line)
    body = (
        f"Hello {_clean_line(supplier, 200)} team,\n\n"
        f"Please find attached {store_name}'s purchase order: {_plural(len(items), 'item')}, "
        f"{_fmt_qty(cases)} {'case' if cases == 1 else 'cases'} in total. Quantities are in cases.\n\n"
        f"{NOTE_MARKER}\n\n"
        "Please reply to this email to confirm the order, and let us know if anything is unavailable.\n\n"
        "Thank you,\n"
        f"{signature}\n\n"
        f"--\nSent with {sv.product_credit()}"
    )
    return subject, body


def render_body(template: str, note: Optional[str]) -> str:
    """The website's preview uses the same rule (poSendModel.renderBody)."""
    note = _clean_text(note, MAX_NOTE)
    if note:
        return template.replace(NOTE_MARKER, note)
    return template.replace("\n\n" + NOTE_MARKER, "")


def whatsapp_caption(supplier: str, items: list) -> str:
    cases = sum(float(i.get("qty") or 0) for i in items)
    phone = sv.store_phone()
    return (
        f"Purchase order from {sv.store_display_name()} - {_plural(len(items), 'item')}, {_fmt_qty(cases)} cases. "
        f"Please confirm." + (f" {phone}" if phone else "")
    )


# --------------------------------------------------------------------------
# Transports (tests replace these; nothing here runs without credentials)
# --------------------------------------------------------------------------


def _smtp_connect(cfg):
    """An SMTP connection per SMTP_SSL: implicit TLS, or STARTTLS."""
    from app.platform.egress import check_host

    check_host(cfg.host, cfg.port)  # OSError when it resolves to a private network (shared service)
    if cfg.ssl:
        return smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=SMTP_TIMEOUT)
    server = smtplib.SMTP(cfg.host, cfg.port, timeout=SMTP_TIMEOUT)
    server.starttls()
    return server


def send_email(
    to_addrs: list, subject: str, body: str, pdf_bytes: bytes, filename: str, cc_addrs: Optional[list] = None
) -> str:
    """Email the PO through the store's Gmail (SMTP SSL + app password - the
    invoice emails' transport). Returns our Message-ID. Raises
    EmailNotConfigured or _ChannelError (maybe_sent when the connection broke
    after the message was handed over)."""
    pilot_guard("PO email")
    cfg = sv.smtp_config()
    if not cfg.username or not cfg.password:
        raise EmailNotConfigured("Email isn't set up on the server (SMTP_USERNAME / SMTP_PASSWORD).")
    sender = cfg.username
    domain = (sender.rsplit("@", 1)[-1] if "@" in sender else "") or sv.email_fallback_domain()
    message_id = make_msgid(domain=domain)

    msg = MIMEMultipart()
    msg["From"] = formataddr((cfg.from_name, sender))
    msg["To"] = ", ".join(to_addrs)
    if cc_addrs:
        msg["Cc"] = ", ".join(cc_addrs)
    msg["Reply-To"] = sv.store_email() or sender
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = message_id
    msg.attach(MIMEText(body, "plain", "utf-8"))
    part = MIMEApplication(pdf_bytes, _subtype="pdf")
    part.add_header("Content-Disposition", "attachment", filename=filename)
    msg.attach(part)

    stage = "connect"
    server = None
    try:
        server = _smtp_connect(cfg)
        stage = "login"
        server.login(sender, cfg.password)
        stage = "send"
        refused = server.sendmail(sender, list(to_addrs) + list(cc_addrs or []), msg.as_string())
        stage = "done"
    except smtplib.SMTPAuthenticationError:
        raise _ChannelError("Gmail refused the login - check GMAIL_APP_PASSWORD on the server.")
    except smtplib.SMTPRecipientsRefused:
        raise _ChannelError("Gmail refused every recipient address - check the vendor's email.")
    except (smtplib.SMTPSenderRefused, smtplib.SMTPDataError) as exc:
        code = getattr(exc, "smtp_code", "")
        raise _ChannelError(f"Gmail refused the email ({code}).")
    except (smtplib.SMTPException, socket.timeout, OSError) as exc:
        if stage == "send":
            raise _ChannelError(
                f"The connection to Gmail broke while sending ({type(exc).__name__}) - the email may still have "
                "gone out. Check Gmail's Sent folder before sending again.",
                maybe_sent=True,
            )
        raise _ChannelError(f"Couldn't reach Gmail ({type(exc).__name__}). Nothing was sent - try again.")
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:  # noqa: BLE001 - the send itself already finished or failed
                try:
                    server.close()
                except Exception:  # noqa: BLE001
                    pass
    if refused:
        logger.warning("PO email: Gmail refused %s", sorted(refused))
    return message_id


def send_whatsapp(to: str, caption: str, pdf_bytes: bytes, filename: str) -> Optional[str]:
    """The PO PDF as a WhatsApp document through Whapi (only called when
    PO_WHATSAPP_ENABLED). Returns Whapi's message id."""
    chat = whatsapp_chat_id(to)
    try:
        try:
            media = whapi_client.upload_media(pdf_bytes, "application/pdf")
        except (httpx.HTTPError, ValueError):
            media = whapi_client.media_data_url(pdf_bytes, "application/pdf", filename)
        answer = whapi_client.send_media_message("document", chat, media, caption=caption, filename=filename)
    except whapi_client.WhapiNotConfigured as exc:
        logger.info("PO WhatsApp not configured: %s", exc)
        raise _ChannelError("WhatsApp isn't set up for this store yet. An admin can connect it in Setup › Integrations.")
    except whapi_client.WhapiAmbiguousTimeout:
        raise _ChannelError(
            "WhatsApp didn't answer in time - the PO may still have gone out. Check the chat before sending again.",
            maybe_sent=True,
        )
    except whapi_client.WhapiMediaError as exc:
        message = str(exc)
        if exc.status_code == 402:
            message = WHATSAPP_CHAT_LIMIT_MESSAGE
        message = message.replace("the flyer", "the PO")
        raise _ChannelError(message, maybe_sent=exc.maybe_sent)
    if isinstance(answer, dict) and answer.get("sent") is False:
        raise _ChannelError("WhatsApp didn't confirm the message - check the chat before sending again.", maybe_sent=True)
    message = answer.get("message") if isinstance(answer, dict) else None
    return message.get("id") if isinstance(message, dict) else None


# --------------------------------------------------------------------------
# Send log
# --------------------------------------------------------------------------


def _json_list(text: Optional[str]) -> list:
    try:
        value = json.loads(text or "[]")
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def send_out(row: PoSend) -> dict:
    return {
        "id": row.id,
        "supplier": row.supplier,
        "status": row.status,
        "subject": row.subject,
        "pdf_filename": row.pdf_filename,
        "item_count": row.item_count,
        "email_to": _json_list(row.email_to_json),
        # Additive (W2): the CC addresses this send used.
        "email_cc": _json_list(getattr(row, "email_cc_json", None)),
        "email_status": row.email_status,
        "email_error": row.email_error,
        "email_message_id": row.email_message_id,
        "whatsapp_to": row.whatsapp_to,
        "whatsapp_status": row.whatsapp_status,
        "whatsapp_error": row.whatsapp_error,
        "whatsapp_message_id": row.whatsapp_message_id,
        "placed_order_id": row.placed_order_id,
        "sent_by_name": row.sent_by_name,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


def list_sends(db: Session, supplier: Optional[str] = None, limit: int = 20) -> List[PoSend]:
    stmt = select(PoSend).order_by(PoSend.created_at.desc(), PoSend.id.desc())
    rows = list(db.execute(stmt).scalars())
    if supplier is not None:
        key = vendor_key(supplier)
        rows = [r for r in rows if vendor_key(r.supplier) == key]
    return rows[:limit]


def _effective_status(row: PoSend, now: dt.datetime) -> str:
    if row.status == STATUS_SENDING and row.created_at and now - row.created_at > SENDING_STALE:
        return STATUS_UNCERTAIN
    return row.status


def unresolved_uncertain(db: Session, supplier: str) -> Optional[PoSend]:
    """The vendor's newest send in the last day when it may have gone out
    unconfirmed - unless something went out after it, or the order was
    marked placed after it (the "It went out - record as placed" answer)."""
    from app.models import PlacedOrder

    now = utcnow()
    for row in list_sends(db, supplier, limit=20):
        if row.created_at and now - row.created_at > UNCERTAIN_LOOKBACK:
            return None
        status = _effective_status(row, now)
        if status in (STATUS_SENT, STATUS_PARTLY):
            return None
        if status == STATUS_UNCERTAIN:
            placed_after = db.execute(
                select(PlacedOrder.id)
                .where(
                    PlacedOrder.supplier == row.supplier,
                    PlacedOrder.status == "placed",
                    PlacedOrder.placed_at >= row.created_at,
                )
                .limit(1)
            ).scalar_one_or_none()
            return None if placed_after is not None else row
    return None


def _in_flight(db: Session, supplier: str) -> Optional[PoSend]:
    now = utcnow()
    for row in list_sends(db, supplier, limit=5):
        if row.status == STATUS_SENDING and _effective_status(row, now) == STATUS_SENDING:
            return row
    return None


# --------------------------------------------------------------------------
# Preview and send
# --------------------------------------------------------------------------


def _contacts_for(db: Session, supplier: str) -> List[VendorContact]:
    return list_contacts(db, supplier)


def preview(db: Session, supplier: str, user: User) -> dict:
    rows, items = cart_lines(db, supplier)
    if not rows:
        raise PoSendError(400, f"Nothing in the cart for {supplier}.")
    contacts = _contacts_for(db, supplier)
    subject, body = compose(supplier, items)
    recipients = []
    for c in contacts:
        if c.send_po and c.email and c.email.lower() not in {r.lower() for r in recipients}:
            recipients.append(c.email)
    # The default recipients' CCs, in contact order, minus anyone already in To.
    default_cc: list = []
    taken = {r.lower() for r in recipients}
    for c in contacts:
        if not (c.send_po and c.email):
            continue
        for e in contact_cc(c):
            if e.lower() not in taken:
                taken.add(e.lower())
                default_cc.append(e)
    settings = get_settings()
    wa_contacts = [
        {"id": c.id, "label": " - ".join(x for x in (c.contact_name, c.whatsapp) if x)} for c in contacts if c.whatsapp
    ]
    last = list_sends(db, supplier, limit=5)
    uncertain = unresolved_uncertain(db, supplier)
    return {
        "supplier": supplier,
        "items": [
            {"name": i["name"], "supplier_item_id": i["supplier_item_id"], "qty": i["qty"]} for i in items
        ],
        "item_count": len(items),
        "total_qty": round(sum(float(i["qty"]) for i in items), 3),
        "zero_qty_lines": len(rows) - len(items),
        "contacts": [contact_out(c) for c in contacts],
        "recipients": recipients,
        # Additive (W2): CCs pre-filled in the dialog (editable there).
        "default_cc": default_cc[:MAX_CC],
        "max_cc": MAX_CC,
        "subject": subject,
        "body_template": body,
        "note_marker": NOTE_MARKER,
        "max_note": MAX_NOTE,
        "pdf_filename": pdf_filename(supplier),
        "from_address": sv.smtp_config().username or None,
        "email_configured": email_configured(),
        "whatsapp": {
            "enabled": whatsapp_enabled(),
            "reason": None if whatsapp_enabled() else WHATSAPP_OFF_REASON,
            "contacts": wa_contacts,
        },
        "can_manage_contacts": bool(user.is_admin),
        "recent_sends": [send_out(r) for r in last],
        "uncertain_previous": send_out(uncertain) if uncertain is not None else None,
    }


def _check_recipients(contacts: List[VendorContact], recipients: list) -> list:
    """The chosen addresses, each one an email on this vendor's contacts -
    the store's Gmail never mails an address an admin didn't save."""
    known = {c.email.lower(): c.email for c in contacts if c.email}
    chosen: list = []
    for value in recipients or []:
        text = (value or "").strip() if isinstance(value, str) else ""
        if not text:
            continue
        email = known.get(text.lower())
        if email is None:
            raise PoSendError(
                422, f"{_clean_line(text, 80)} isn't one of this vendor's contacts. An admin can add it on Vendor contacts."
            )
        if email not in chosen:
            chosen.append(email)
    if len(chosen) > MAX_RECIPIENTS:
        raise PoSendError(422, f"Pick at most {MAX_RECIPIENTS} recipients.")
    return chosen


def send_po(
    db: Session,
    supplier: str,
    user: User,
    idempotency_key: str,
    recipients: Optional[list] = None,
    note: Optional[str] = None,
    channels: Optional[list] = None,
    whatsapp_contact_id: Optional[int] = None,
    confirm_resend: bool = False,
    cc: Optional[list] = None,
) -> Tuple[PoSend, bool]:
    """Send `supplier`'s PO. Returns (row, duplicate); duplicate = this
    idempotency key was already used (its row is returned, nothing sent)."""
    key = (idempotency_key or "").strip()
    if not _KEY_RE.match(key):
        raise PoSendError(422, "Missing or invalid idempotency key.")
    channels = list(dict.fromkeys(channels or ["email"]))
    if not channels or any(c not in CHANNELS for c in channels):
        raise PoSendError(422, "channels must be email and/or whatsapp.")

    with _send_lock, advisory_lock(db, "po_send"):
        existing = db.execute(select(PoSend).where(PoSend.idempotency_key == key)).scalar_one_or_none()
        if existing is not None:
            return _replay(existing, supplier)

        busy = _in_flight(db, supplier)
        if busy is not None:
            raise PoSendError(
                409, f"{busy.sent_by_name or 'Someone'} is sending {supplier}'s PO right now.", code="in_progress"
            )
        uncertain = unresolved_uncertain(db, supplier)
        if uncertain is not None and not confirm_resend:
            raise PoSendError(
                409,
                f"The last send of {supplier}'s PO may have gone out without a confirmation. Check Gmail's Sent "
                "folder (or the WhatsApp chat) before sending again.",
                code="check_previous",
            )

        contacts = _contacts_for(db, supplier)
        to_addrs: list = []
        cc_addrs: list = []
        wa_to: Optional[str] = None
        if "email" in channels:
            if not email_configured():
                raise PoSendError(503, "Email isn't set up on the server (GMAIL_ADDRESS / GMAIL_APP_PASSWORD).")
            to_addrs = _check_recipients(contacts, recipients or [])
            if not to_addrs:
                raise PoSendError(422, f"Pick who to email. {supplier} needs an email on Vendor contacts.")
            # Per-send CCs (W2): any valid address, at most MAX_CC - pre-filled
            # from the contacts' defaults and editable in the dialog.
            cc_addrs = normalize_cc(cc, exclude=to_addrs)
        if "whatsapp" in channels:
            if not whatsapp_enabled():
                raise PoSendError(422, WHATSAPP_OFF_REASON, code="whatsapp_off")
            match = [c for c in contacts if c.whatsapp and c.id == whatsapp_contact_id]
            if not match:
                raise PoSendError(422, f"Pick which of {supplier}'s WhatsApp contacts to send to.")
            wa_to = match[0].whatsapp

        cleaned_note = _clean_text(note, MAX_NOTE * 10)
        if cleaned_note and len(cleaned_note) > MAX_NOTE:
            raise PoSendError(422, f"The note is too long (at most {MAX_NOTE} characters).")
        cart_rows, items = cart_lines(db, supplier)
        if not items:
            raise PoSendError(400, f"Nothing to send - {supplier} has no cart lines with a quantity above 0.")
        subject, template = compose(supplier, items)
        row = PoSend(
            idempotency_key=key,
            supplier=supplier,
            status=STATUS_SENDING,
            subject=subject,
            body=render_body(template, note),
            pdf_filename=pdf_filename(supplier),
            item_count=len(items),
            items_json=json.dumps(items),
            cart_item_ids_json=json.dumps([r.id for r in cart_rows]),
            email_to_json=json.dumps(to_addrs) if "email" in channels else None,
            email_cc_json=json.dumps(cc_addrs) if cc_addrs else None,
            email_status="pending" if "email" in channels else None,
            whatsapp_to=wa_to,
            whatsapp_status="pending" if "whatsapp" in channels else None,
            sent_by_user_id=user.id,
            sent_by_name=user.username,
            created_at=utcnow(),
        )
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = db.execute(select(PoSend).where(PoSend.idempotency_key == key)).scalar_one()
            return _replay(existing, supplier)
        db.refresh(row)

    row_id = row.id
    try:
        _deliver(db, row, user, items)
    except Exception as exc:  # noqa: BLE001 - never leave a row stuck at "sending"
        logger.exception("PO send %s failed unexpectedly", row_id)
        db.rollback()
        row = db.get(PoSend, row_id)
        if row.status == STATUS_SENDING:
            problem = f"Unexpected error ({type(exc).__name__})."
            if STATUS_SENT in (row.email_status, row.whatsapp_status):
                # It went out; only recording the order failed (placed_order_id
                # stays empty - the website says to use Order placed).
                row.status = STATUS_SENT
            else:
                row.status = STATUS_FAILED
                if row.email_status == "pending":
                    row.email_status, row.email_error = STATUS_FAILED, problem
                if row.whatsapp_status == "pending":
                    row.whatsapp_status, row.whatsapp_error = STATUS_FAILED, problem
            row.finished_at = utcnow()
            db.commit()
            db.refresh(row)
    return row, False


def _replay(existing: PoSend, supplier: str) -> Tuple[PoSend, bool]:
    if existing.supplier != supplier:
        raise PoSendError(409, "This send key was already used for another vendor - reopen Send PO.")
    if _effective_status(existing, utcnow()) == STATUS_SENDING:
        raise PoSendError(409, f"{supplier}'s PO is still being sent.", code="in_progress")
    return existing, True


def _deliver(db: Session, row: PoSend, user: User, items: list) -> None:
    from app.services.purchase_order_log import place_sent_order

    pdf_bytes = build_po_pdf(db, row.supplier, items)
    if row.email_status == "pending":
        try:
            cc_addrs = _json_list(row.email_cc_json)
            if cc_addrs:
                row.email_message_id = send_email(
                    _json_list(row.email_to_json), row.subject, row.body, pdf_bytes, row.pdf_filename, cc_addrs
                )
            else:
                # Same call as before CCs existed (tests and fakes rely on it).
                row.email_message_id = send_email(
                    _json_list(row.email_to_json), row.subject, row.body, pdf_bytes, row.pdf_filename
                )
            row.email_status = STATUS_SENT
        except EmailNotConfigured as exc:
            row.email_status, row.email_error = STATUS_FAILED, str(exc)
        except _ChannelError as exc:
            row.email_status = STATUS_UNCERTAIN if exc.maybe_sent else STATUS_FAILED
            row.email_error = str(exc)
        db.commit()
    if row.whatsapp_status == "pending":
        try:
            row.whatsapp_message_id = send_whatsapp(
                row.whatsapp_to, whatsapp_caption(row.supplier, items), pdf_bytes, row.pdf_filename
            )
            row.whatsapp_status = STATUS_SENT
        except _ChannelError as exc:
            row.whatsapp_status = STATUS_UNCERTAIN if exc.maybe_sent else STATUS_FAILED
            row.whatsapp_error = str(exc)
        db.commit()

    statuses = [s for s in (row.email_status, row.whatsapp_status) if s is not None]
    if all(s == STATUS_SENT for s in statuses):
        status = STATUS_SENT
    elif STATUS_SENT in statuses:
        status = STATUS_PARTLY
    elif STATUS_UNCERTAIN in statuses:
        status = STATUS_UNCERTAIN
    else:
        status = STATUS_FAILED

    if status in (STATUS_SENT, STATUS_PARTLY):
        placed, _duplicate = place_sent_order(db, row.supplier, user, items, _json_list(row.cart_item_ids_json))
        row.placed_order_id = placed.id
    row.status = status
    row.finished_at = utcnow()
    db.commit()
    db.refresh(row)


def failure_message(row: PoSend) -> str:
    """One plain sentence for a send that didn't go out (or might have)."""
    parts = []
    if row.email_status in (STATUS_FAILED, STATUS_UNCERTAIN) and row.email_error:
        parts.append(f"Email: {row.email_error}")
    if row.whatsapp_status in (STATUS_FAILED, STATUS_UNCERTAIN) and row.whatsapp_error:
        parts.append(f"WhatsApp: {row.whatsapp_error}")
    lead = (
        "The PO may have gone out - not confirmed. The cart was left as it was."
        if row.status == STATUS_UNCERTAIN
        else "The PO was not sent. The cart was left as it was."
    )
    return " ".join([lead] + parts)
