"""Sends a WhatsApp message via Whapi Cloud (https://whapi.cloud), replacing
the Google Apps Script step of the old Form -> Sheet workflow that posted a
"New Invoice Submitted" message to a staff WhatsApp group.

Verified against a real Whapi account/token/group in a live session: the
account/group lookup endpoints (GET, no message sent) confirmed the token
and chat id are real, and a real POST /messages/text was confirmed
delivered to the real group. One real thing that surfaced from that test:
Whapi can take longer to respond than it takes to actually queue/send the
message - a client-side read timeout on the POST does NOT mean the message
wasn't sent (confirmed directly: a "timed out" send still showed up
delivered in the group's real message history a moment later). A timeout is
therefore reported as its own distinct, ambiguous outcome rather than a
plain failure, so a human deciding whether to hit Resend isn't misled into
assuming nothing went out - a blind resend after a real timeout can produce
a real duplicate message, which is exactly what happened during that test.

Confirmed a second time against real production use: even 45s wasn't
consistently enough (a real submission from the live server still timed out,
message still delivered anyway) - the API response itself is just slow to
come back from Whapi even though the actual WhatsApp delivery is fast. This
now runs as a FastAPI BackgroundTasks job (see invoice_submission.py::
send_invoice_notifications), not inline in the HTTP request, but that does
NOT mean a long timeout is free: the frontend polls for live status, so the
user still watches "Sending..." for however long this waits before giving
up - a 120s timeout (tried once, then reverted) made that wait worse, not
irrelevant. Kept intentionally short instead - the ambiguous-timeout
handling already makes a fast give-up safe (never reported as a confirmed
failure), so there's no correctness reason to wait long, only a UX reason
not to.

A POST timeout isn't the end of the story, though - GET /messages/list/
{chat_id} (verified live against the real account/group) returns real
message history including our own `from_me` sends with their exact text, so
a timed-out send can actually be confirmed (or ruled out) after the fact
instead of just being reported as forever "uncertain" - see
find_sent_message below, used by invoice_submission.py's reconciliation
step to turn a real send into a real "Sent" rather than leaving it stuck on
an ambiguous status a human has to manually check.
"""

import base64
import re
from typing import Optional

import httpx

from app.config import get_settings


class WhapiNotConfigured(Exception):
    pass


class WhapiAmbiguousTimeout(Exception):
    """Raised when the request to Whapi timed out - the message may or may
    not have actually been sent/delivered. Deliberately a distinct exception
    from a confirmed failure, so the caller can record an honest, different
    message instead of implying "definitely not sent."
    """


def send_whatsapp_message(text: str, to: Optional[str] = None) -> None:
    """`to` defaults to Invoice Submission's own configured group
    (whapi_chat_id) for backward compatibility with every existing caller -
    pass it explicitly (e.g. Daily Checklists' per-checklist-type group) to
    target a different chat with the same account/token.

    Real root cause found and fixed here: Invoice Submission's messages
    always contain a "View Document" link, and were timing out (see
    WhapiAmbiguousTimeout below) or landing in the ambiguous/failed state
    dramatically more often than Daily Checklists' messages - which never
    contain a link - even though both go through this exact same function.
    Checked live against real production data before assuming a cause:
    11 of the last 18 real invoice submissions needed a manual admin
    override just to show as sent, versus zero for checklists. WhatsApp
    link-preview generation (fetching the URL, parsing its Open Graph tags)
    is real added server-side work only a linked message triggers, and
    Whapi's own API (confirmed via its real docs, not guessed) exposes
    `no_link_preview` specifically to skip it. Always sending it (not just
    for invoice callers) is a safe, no-downside default - a plain-text
    checklist message has no link to preview either way.
    """
    settings = get_settings()
    target = to or settings.whapi_chat_id
    if not settings.whapi_api_token or not target:
        raise WhapiNotConfigured(
            "WHAPI_API_TOKEN and/or a destination chat id aren't set in the backend's .env yet."
        )
    try:
        resp = httpx.post(
            f"{settings.whapi_base_url}/messages/text",
            headers={"Authorization": f"Bearer {settings.whapi_api_token}"},
            json={"to": target, "body": text, "no_link_preview": True},
            timeout=25,
        )
    except httpx.TimeoutException:
        raise WhapiAmbiguousTimeout(
            "Whapi didn't respond in time - the message may have still gone through. Check the "
            "WhatsApp group before resending, to avoid posting a duplicate."
        )
    resp.raise_for_status()


def find_sent_message(text: str, to: Optional[str] = None, count: int = 10) -> bool:
    """True if `text` shows up as one of our own recent outgoing messages in
    the chat `to` (same default-to-invoice-group behavior as
    send_whatsapp_message) - used to reconcile a WhapiAmbiguousTimeout into
    a real confirmed outcome. Callers should pass a message body that's
    unique enough that an exact match here is conclusive (e.g. Invoice
    Submission's text embeds a unique per-submission file link; Daily
    Checklists' embeds the exact submission time), not just a plausible
    guess. Fails closed (returns False) on any error from this GET - an
    inability to check is not evidence the message went through.
    """
    settings = get_settings()
    target = to or settings.whapi_chat_id
    if not settings.whapi_api_token or not target:
        return False
    try:
        resp = httpx.get(
            f"{settings.whapi_base_url}/messages/list/{target}",
            headers={"Authorization": f"Bearer {settings.whapi_api_token}"},
            params={"count": count},
            timeout=15,
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        return False
    for msg in resp.json().get("messages", []):
        if msg.get("from_me") and msg.get("text", {}).get("body") == text:
            return True
    return False


GROUPS_PAGE_SIZE = 100  # Whapi: count defaults to 100, max 500
GROUPS_MAX_PAGES = 20


def list_groups() -> list:
    """Every WhatsApp group the store's number belongs to, as
    [{"chat_id", "name"}] - read-only (GET /groups, paged with count /
    offset; response {"groups": [...], "count", "total", "offset"}).
    Raises WhapiNotConfigured without a token, httpx.HTTPError on a failed
    request. Used by the checklist builder's "Available on WhatsApp" list
    (services/whatsapp_groups.py)."""
    settings = get_settings()
    if not settings.whapi_api_token:
        raise WhapiNotConfigured("WHAPI_API_TOKEN isn't set in the backend's .env.")
    out = []
    offset = 0
    for _ in range(GROUPS_MAX_PAGES):
        resp = httpx.get(
            f"{settings.whapi_base_url}/groups",
            headers={"Authorization": f"Bearer {settings.whapi_api_token}"},
            params={"count": GROUPS_PAGE_SIZE, "offset": offset},
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()
        page = body.get("groups") if isinstance(body, dict) else body
        page = page if isinstance(page, list) else []
        for group in page:
            if isinstance(group, dict) and isinstance(group.get("id"), str):
                name = group.get("name") if isinstance(group.get("name"), str) else ""
                out.append({"chat_id": group["id"], "name": name})
        offset += len(page)
        total = body.get("total") if isinstance(body, dict) else None
        if len(page) < GROUPS_PAGE_SIZE or (isinstance(total, int) and offset >= total):
            break
    return out


# --- Media messages (marketing flyers - services/flyer_sender.py) ----------------------------
#
# Checked against Whapi's docs (Oct 4): whapi.readme.io sendmessageimage,
# sendmessagedocument and uploadmedia; support.whapi.cloud "Send video,
# audio, image, document" and "Timeout or very slow sending of large media
# messages".
# - POST /messages/image {to, media, caption?} and POST /messages/document
#   {to, media, filename?, caption?}; the answer is {"sent": true,
#   "message": {"id": ...}}.
# - `media` may be a URL, a base64 data URL
#   ("data:<mime>;name=<file>;base64,<data>"), a multipart file, or a MediaID
#   from POST /media (raw file body with its Content-Type -> {"media":
#   [{"id": ...}]}; Whapi keeps it 30 days).
# - Whapi waits at most 110 s before answering a media request, and a send
#   that timed out on our side may still have gone out on theirs.
# A flyer goes to up to 25 groups, so the sender uploads the file ONCE
# (upload_media) and sends every group that MediaID - a 20 MB PDF is one
# 20 MB upload instead of 25 x 27 MB of base64. If the upload fails (or a
# MediaID is refused), the file goes inline as a data URL instead
# (media_data_url).

MEDIA_TIMEOUT = 120  # longer than Whapi's own 110 s, so we normally hear its answer
MEDIA_KINDS = ("image", "document")
# How far before the attempt started a matching message may be stamped and
# still count as ours (clock differences between us and WhatsApp).
RECONCILE_SKEW_SECONDS = 60
# GET /messages/list/{chat} is newest first, `count` (max 500) per page,
# `offset` to page back. A busy customer group can have many messages after
# ours (a retry may come hours later), so the search pages back until it
# reaches messages older than the attempt - at most this many pages.
RECONCILE_PAGE_SIZE = 100
RECONCILE_MAX_PAGES = 5


class WhapiMediaError(Exception):
    """A media request that didn't go through. maybe_sent=True when it may
    have reached WhatsApp anyway (the connection dropped while waiting for
    the answer, or a 5xx / gateway timeout) - check the group before
    resending. status_code is Whapi's HTTP status, when there was one."""

    def __init__(self, message: str, status_code: Optional[int] = None, maybe_sent: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.maybe_sent = maybe_sent


_STATUS_TEXT = {
    400: "WhatsApp refused the message (400)",
    401: "WhatsApp login failed (401) - check the Whapi token",
    402: "The Whapi plan's limit was reached (402)",
    403: "WhatsApp refused the message (403) - is the store's number still in the group?",
    404: "WhatsApp group not found (404)",
    413: "The file is too large for WhatsApp (413)",
    415: "WhatsApp doesn't accept this file type (415)",
    429: "Too many messages - WhatsApp asked us to slow down (429)",
}


def _media_status_error(code: int) -> WhapiMediaError:
    if code >= 500 or code == 408:
        return WhapiMediaError(
            f"WhatsApp's service didn't confirm the message ({code}) - it may still have gone out. "
            "Check the group before retrying.",
            status_code=code,
            maybe_sent=True,
        )
    return WhapiMediaError(_STATUS_TEXT.get(code, f"WhatsApp refused the message ({code})"), status_code=code)


def _require_token() -> str:
    settings = get_settings()
    if not settings.whapi_api_token:
        raise WhapiNotConfigured("WHAPI_API_TOKEN isn't set in the backend's .env.")
    return settings.whapi_api_token


def media_data_url(data: bytes, mime: str, filename: str) -> str:
    """The base64 form Whapi documents: data:<mime>;name=<file>;base64,<data>.
    The name is reduced to plain characters so it can't break the URL."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("._") or "file"
    return f"data:{mime};name={name};base64,{base64.b64encode(data).decode('ascii')}"


def upload_media(data: bytes, mime: str) -> str:
    """POST /media with the raw file -> its MediaID, reusable for 30 days.
    Uploading sends nothing to anyone. Raises WhapiNotConfigured,
    httpx.HTTPError or ValueError (no id in the answer)."""
    token = _require_token()
    resp = httpx.post(
        f"{get_settings().whapi_base_url}/media",
        headers={"Authorization": f"Bearer {token}", "Content-Type": mime},
        content=data,
        timeout=MEDIA_TIMEOUT,
    )
    resp.raise_for_status()
    body = resp.json()
    media = body.get("media") if isinstance(body, dict) else None
    first = media[0] if isinstance(media, list) and media else None
    media_id = first.get("id") if isinstance(first, dict) else None
    if not isinstance(media_id, str) or not media_id:
        raise ValueError("Whapi didn't return a media id")
    return media_id


def send_media_message(
    kind: str, to: str, media: str, caption: Optional[str] = None, filename: Optional[str] = None
) -> dict:
    """POST /messages/image or /messages/document; `media` is a MediaID or a
    data URL. Returns Whapi's JSON answer ({} if it wasn't JSON). Raises
    WhapiNotConfigured, WhapiAmbiguousTimeout (no answer in time - it may
    have been sent) or WhapiMediaError."""
    if kind not in MEDIA_KINDS:
        raise ValueError(f"unknown media kind {kind!r}")
    token = _require_token()
    if not to:
        raise WhapiNotConfigured("No WhatsApp chat id to send to.")
    payload = {"to": to, "media": media}
    if caption:
        payload["caption"] = caption
    if kind == "document" and filename:
        payload["filename"] = filename
    try:
        resp = httpx.post(
            f"{get_settings().whapi_base_url}/messages/{kind}",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=MEDIA_TIMEOUT,
        )
    except httpx.ReadTimeout:
        raise WhapiAmbiguousTimeout(
            "WhatsApp didn't answer in time - the flyer may still have gone out. Check the group before retrying."
        )
    except (httpx.ReadError, httpx.RemoteProtocolError, httpx.CloseError):
        # The request was sent; the connection broke while waiting for the answer.
        raise WhapiMediaError(
            "The connection to WhatsApp dropped - the flyer may still have gone out. Check the group before "
            "retrying.",
            maybe_sent=True,
        )
    except httpx.TransportError as exc:
        # Connect / write / pool failures: the request never fully reached Whapi.
        raise WhapiMediaError(f"Couldn't reach WhatsApp ({type(exc).__name__})")
    if resp.status_code >= 400:
        raise _media_status_error(resp.status_code)
    try:
        body = resp.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _norm_caption(value) -> str:
    if not isinstance(value, str):
        return ""
    return "\n".join(line.rstrip() for line in value.replace("\r\n", "\n").strip().split("\n"))


def find_sent_media(
    to: str,
    kind: str,
    caption: Optional[str],
    since_epoch: float,
    filename: Optional[str] = None,
    count: int = RECONCILE_PAGE_SIZE,
    exclude_ids: Optional[set] = None,
    max_pages: int = RECONCILE_MAX_PAGES,
) -> Optional[dict]:
    """Our own `kind` message in chat `to`, stamped at/after since_epoch
    (less RECONCILE_SKEW_SECONDS), with the same caption (and, for a
    document, the same file name when Whapi reports one) and not one of
    `exclude_ids` (messages already credited to another send) - the
    message dict, or None. Used to settle a send that timed out, and before
    retrying one that may have gone through. Pages back (newest first) while
    a full page is still newer than the attempt, so a busy group's chatter
    can't hide our message. Fails closed: any error is "not found"."""
    settings = get_settings()
    if not settings.whapi_api_token or not to:
        return None
    oldest_wanted = since_epoch - RECONCILE_SKEW_SECONDS
    want_caption = _norm_caption(caption)
    offset = 0
    first_ids = set()
    for _ in range(max(1, max_pages)):
        params = {"count": count}
        if offset:
            params["offset"] = offset
        try:
            resp = httpx.get(
                f"{settings.whapi_base_url}/messages/list/{to}",
                headers={"Authorization": f"Bearer {settings.whapi_api_token}"},
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            body = resp.json()
        except (httpx.HTTPError, ValueError):
            return None
        messages = body.get("messages") if isinstance(body, dict) else None
        messages = [m for m in messages if isinstance(m, dict)] if isinstance(messages, list) else []
        for msg in messages:
            if _is_our_media(msg, kind, want_caption, oldest_wanted, filename, exclude_ids):
                return msg
        stamps = [m.get("timestamp") for m in messages if isinstance(m.get("timestamp"), (int, float))]
        if len(messages) < count or not stamps or min(stamps) < oldest_wanted:
            return None  # the last page, or already older than the attempt
        if messages[0].get("id") in first_ids:
            return None  # the same page again: paging isn't working, stop
        first_ids.add(messages[0].get("id"))
        offset += len(messages)
    return None


def _is_our_media(msg: dict, kind: str, want_caption: str, oldest_wanted: float, filename, exclude_ids) -> bool:
    if not msg.get("from_me") or msg.get("type") != kind:
        return False
    if exclude_ids and msg.get("id") in exclude_ids:
        return False
    stamp = msg.get("timestamp")
    if not isinstance(stamp, (int, float)) or stamp < oldest_wanted:
        return False
    content = msg.get(kind) if isinstance(msg.get(kind), dict) else {}
    if _norm_caption(content.get("caption")) != want_caption:
        return False
    if kind == "document" and filename:
        name = content.get("file_name") or content.get("filename")
        if isinstance(name, str) and name and name.lower() != filename.lower():
            return False
    return True
