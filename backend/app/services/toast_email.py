"""Emails an invoice/return/other submission's PDF to Toast's invoicing inbox, replicating the
old Google Apps Script step - Toast turns an email like this into an
automatic draft bill on their end. Sent via Gmail SMTP using an "app
password" (myaccount.google.com/apppasswords), not the real account
password - same auth style Google's own Apps Script mail-merge add-ons use.
"""

import pathlib
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from app.config import get_settings
from app.services.toast_pdf import toast_friendly_pdf
from app.store import settings_view as sv

TYPE_LABELS = {
    "invoice": "Invoice",
    "return": "Return",
    "other": "Something else",
    "untracked": "Untracked",
    # Never emailed (see NO_TOAST_TYPES); listed so the map stays complete.
    "transfer": "Transfer",
}


class EmailNotConfigured(Exception):
    pass


def ap_inbox_address(settings) -> str:
    """Where the invoice PDF goes: AP_INBOX_EMAIL, else the old
    TOAST_INVOICE_EMAIL (multi-store MT6; Settings keeps the two equal)."""
    address = getattr(settings, "ap_inbox_email", None)
    if address is None:
        address = getattr(settings, "toast_invoice_email", "")
    return (address or "").strip()


def send_toast_invoice_email(
    vendor: str,
    submission_type: str,
    total_amount: Optional[float],
    submitted_by_name: Optional[str],
    pdf_path: pathlib.Path,
    pdf_filename: str,
    source_path: Optional[pathlib.Path] = None,
) -> None:
    """`pdf_path` is the stored PDF (the upload itself, or its converted
    copy for a photo). `source_path` is the original upload when it was a
    photo - the attachment is built from it for best quality. Either way
    the attachment goes through toast_pdf first (Letter-sized pages + a
    text layer, see that module); the stored files are never modified."""
    settings = get_settings()
    smtp = sv.smtp_config(settings)
    if not smtp.username or not smtp.password:
        raise EmailNotConfigured(
            "GMAIL_ADDRESS and/or GMAIL_APP_PASSWORD aren't set in the backend's .env yet."
        )
    inbox = ap_inbox_address(settings)
    if not inbox:
        raise EmailNotConfigured("AP_INBOX_EMAIL is blank in the backend's .env: the invoice-inbox email is off.")

    type_label = TYPE_LABELS.get(submission_type, "Something else")
    total_str = f"${total_amount:,.2f}" if total_amount is not None else "$Unknown"

    msg = MIMEMultipart()
    msg["From"] = smtp.username
    msg["To"] = inbox
    # Matches the original Apps Script's subject exactly: `${docType} - ${vendorName} - $${docTotal}`.
    msg["Subject"] = f"{type_label} - {vendor} - {total_str}"

    body = (
        f"New {type_label} submitted.\n\n"
        f"Vendor: {vendor}\n"
        f"Total: {total_str}\n"
        f"Submitted by: {submitted_by_name or 'Unknown'}\n\n"
        f"(See attached file: {pdf_filename})"
    )
    msg.attach(MIMEText(body, "plain"))

    source = source_path or pdf_path
    with toast_friendly_pdf(source) as attach_path:
        if attach_path == source and source != pdf_path:
            # Conversion of the photo failed - fall back to the stored PDF,
            # never attach the raw photo under a .pdf name.
            attach_path = pdf_path
        with open(attach_path, "rb") as f:
            attachment = MIMEApplication(f.read(), _subtype="pdf")
    attachment.add_header("Content-Disposition", "attachment", filename=pdf_filename)
    msg.attach(attachment)

    with _connect(smtp) as server:
        server.login(smtp.username, smtp.password)
        server.sendmail(smtp.username, [inbox], msg.as_string())


def _connect(smtp):
    """SMTP_SSL (implicit TLS) or STARTTLS, per the SMTP_* settings."""
    if smtp.ssl:
        return smtplib.SMTP_SSL(smtp.host, smtp.port)
    server = smtplib.SMTP(smtp.host, smtp.port)
    server.starttls()
    return server
