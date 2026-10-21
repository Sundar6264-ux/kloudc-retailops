""""Test connection" for store integrations (contract 9.2; owner C).

``TESTERS[kind](public, secrets) -> TestResult``. Each does the cheapest
authenticated read and never sends anything (no message, no email, no AI
generation). Messages are plain English and never echo a secret. Tests
replace ``TESTERS[kind]`` with fakes; the suite's network guard refuses any
real connection.
"""

import ipaddress
import smtplib
import socket
import ssl as ssl_mod
from typing import Callable, Dict, NamedTuple, Optional
from urllib.parse import urlsplit

TIMEOUT = 10.0


class TestResult(NamedTuple):
    ok: bool
    message: str
    # Toast: one line per key pair, ({"name", "label", "ok", "state", "text"}, ...).
    parts: tuple = ()


TestResult.__test__ = False  # not a pytest test class


NOT_ALLOWED = "That address is not allowed: it points at a private or local network."


def private_target(host: Optional[str], port: int) -> bool:
    """True when `host` resolves to a non-public address (the shared server
    itself or its private network). A name that does not resolve is left to
    the connection attempt (it fails there with the usual message)."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
        except ValueError:
            return True
        if not ip.is_global:
            return True
    return False


def _url_private(url: str) -> bool:
    parts = urlsplit(url)
    return private_target(parts.hostname, parts.port or 443)


def _http_status_message(service: str, status: int) -> TestResult:
    if status in (401, 403):
        return TestResult(False, f"{service} refused these credentials (HTTP {status}). Check them and save again.")
    if status == 404:
        return TestResult(False, f"{service} could not find that account (HTTP 404). Check the IDs.")
    if status == 429:
        return TestResult(False, f"{service} is rate limiting us right now. Try again in a minute.")
    return TestResult(False, f"{service} answered HTTP {status}. Try again later.")


def _toast_transport():
    """The HTTP transport for the Toast test (None = the real network;
    tests replace this)."""
    return None


def _toast_login(client, base: str, client_id: str, secret: str):
    """(access token or None, HTTP status). Never echoes the reply body."""
    resp = client.post(
        f"{base}/authentication/v1/authentication/login",
        json={"clientId": client_id, "clientSecret": secret, "userAccessType": "TOAST_MACHINE_CLIENT"},
    )
    if resp.status_code != 200:
        return None, resp.status_code
    token_obj = (resp.json() or {}).get("token") or {}
    return token_obj.get("accessToken") or None, 200


def _part(name: str, label: str, state: str, text: str) -> dict:
    return {"name": name, "label": label, "ok": state == "working", "state": state, "text": text}


def _toast_main(client, base: str, client_id: str, secret: str, guid: str) -> dict:
    import httpx

    label = "Main keys"
    if not client_id or not secret or not guid:
        return _part("main", label, "missing",
                     "not filled in. Add the Client ID, Client secret and Restaurant ID.")
    try:
        access, status = _toast_login(client, base, client_id, secret)
        if access is None:
            if status in (401, 403) or status == 200:
                return _part("main", label, "refused",
                             "not accepted by Toast. Check the Client ID and Client secret.")
            return _part("main", label, "error", _status_text(status))
        r2 = client.get(
            f"{base}/restaurants/v1/restaurants/{guid}",
            headers={"Authorization": f"Bearer {access}", "Toast-Restaurant-External-ID": guid},
        )
        if r2.status_code in (401, 403, 404):
            return _part("main", label, "refused",
                         "Toast could not find that restaurant with these keys. Check the Restaurant ID (GUID).")
        if r2.status_code != 200:
            return _part("main", label, "error", _status_text(r2.status_code))
        name = ((r2.json() or {}).get("general") or {}).get("name") or "the restaurant"
        return _part("main", label, "working", f"working. Connected to {str(name)[:80]}.")
    except (httpx.HTTPError, OSError, ValueError) as exc:
        return _part("main", label, "error", _unreachable(exc))


def _toast_analytics(client, base: str, public: dict, secrets: dict) -> dict:
    import httpx

    from app.platform.credentials import SAME_KEYS_WARNING, _same

    label = "Analytics keys"
    a_id = (public.get("analytics_client_id") or "").strip()
    a_secret = (secrets.get("analytics_client_secret") or "").strip()
    if not a_id and not a_secret:
        return _part("analytics", label, "not_added",
                     "not added. Add them so each day's sales totals are double-checked after closing.")
    if not a_id or not a_secret:
        missing = "Analytics client secret" if a_id else "Analytics client ID"
        return _part("analytics", label, "missing", f"only half added. Add the {missing} too.")
    if _same(a_id, public.get("client_id")) or a_secret == (secrets.get("client_secret") or "").strip():
        return _part("analytics", label, "same_keys", SAME_KEYS_WARNING)
    try:
        access, status = _toast_login(client, base, a_id, a_secret)
    except (httpx.HTTPError, OSError, ValueError) as exc:
        return _part("analytics", label, "error", _unreachable(exc))
    if access is None:
        if status in (401, 403) or status == 200:
            return _part("analytics", label, "refused",
                         "not accepted by Toast. Check the Analytics client ID and secret.")
        return _part("analytics", label, "error", _status_text(status))
    return _part("analytics", label, "working", "working.")


def _status_text(status: int) -> str:
    if status == 429:
        return "Toast is busy right now (too many requests). Try again in a minute."
    return f"Toast answered with an error (HTTP {status}). Try again later."


def _unreachable(exc: Exception) -> str:
    if isinstance(exc, ValueError):
        return "Toast sent an answer we couldn't read. Try again later."
    return "could not reach Toast. Check the API address and try again."


def test_toast(public: dict, secrets: dict) -> TestResult:
    """Logs in with the main keys and reads the restaurant; logs in with the
    Analytics keys when they are added. Each pair is reported on its own
    line ("Main keys: working", "Analytics keys: not added"). The test
    passes when the main keys work: they bring in sales, menu and stock."""
    import httpx

    client_id = (public.get("client_id") or "").strip()
    guid = (public.get("restaurant_guid") or "").strip()
    secret = (secrets.get("client_secret") or "").strip()
    base = (public.get("api_base") or "https://ws-api.toasttab.com").rstrip("/")
    if not client_id or not secret or not guid:
        from app.platform.credentials import TOAST_MAIN_KEYS_MISSING

        return TestResult(False, TOAST_MAIN_KEYS_MISSING)
    if _url_private(base):
        return TestResult(False, NOT_ALLOWED)
    kw = {"transport": _toast_transport()} if _toast_transport() is not None else {}
    try:
        with httpx.Client(timeout=TIMEOUT, **kw) as client:
            main = _toast_main(client, base, client_id, secret, guid)
            analytics = _toast_analytics(client, base, public, secrets)
    except httpx.HTTPError:
        return TestResult(False, "Could not reach Toast. Check the API address and try again.")
    parts = (main, analytics)
    message = " ".join(f"{p['label']}: {p['text']}" for p in parts)
    return TestResult(main["ok"], message, parts)


def test_whatsapp(public: dict, secrets: dict) -> TestResult:
    import httpx

    token = (secrets.get("api_token") or "").strip()
    base = (public.get("base_url") or "https://gate.whapi.cloud").rstrip("/")
    if not token:
        return TestResult(False, "Enter the WhatsApp (Whapi) API token first.")
    if _url_private(base):
        return TestResult(False, NOT_ALLOWED)
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.get(f"{base}/settings", headers={"Authorization": f"Bearer {token}"})
        if resp.status_code != 200:
            return _http_status_message("WhatsApp (Whapi)", resp.status_code)
        return TestResult(True, "Connected to WhatsApp (Whapi).")
    except (httpx.HTTPError, OSError):
        return TestResult(False, "Could not reach WhatsApp (Whapi). Check the address and try again.")


def test_smtp(public: dict, secrets: dict) -> TestResult:
    host = (public.get("host") or "").strip()
    port = int(public.get("port") or 465)
    use_ssl = bool(public.get("ssl", True))
    username = (public.get("username") or "").strip()
    password = secrets.get("password") or ""
    if not host or not username or not password:
        return TestResult(False, "Enter the mail server, username and password first.")
    if private_target(host, port):
        return TestResult(False, NOT_ALLOWED)
    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=ssl_mod.create_default_context())
        else:
            server = smtplib.SMTP(host, port, timeout=TIMEOUT)
            server.starttls(context=ssl_mod.create_default_context())
        try:
            server.login(username, password)
        finally:
            try:
                server.quit()
            except (smtplib.SMTPException, OSError):
                pass
        return TestResult(True, f"Signed in to {host} (no message sent).")
    except smtplib.SMTPAuthenticationError:
        return TestResult(False, "The mail server refused the username or password.")
    except (smtplib.SMTPException, OSError, ssl_mod.SSLError):
        return TestResult(False, f"Could not connect to {host}:{port}. Check the server, port and SSL setting.")


def test_ai(public: dict, secrets: dict) -> TestResult:
    key = (secrets.get("api_key") or "").strip()
    if not key:
        return TestResult(False, "Enter the AI API key first.")
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=key, timeout=TIMEOUT, max_retries=0)
        client.models.list(limit=1)  # metadata only: no generation, no cost
        return TestResult(True, "The AI key works.")
    except Exception as exc:  # noqa: BLE001 - SDK error classes; never echo the key
        status = getattr(exc, "status_code", None)
        if status:
            return _http_status_message("The AI service", int(status))
        return TestResult(False, "Could not reach the AI service. Try again later.")


def _clover_transport():
    """The HTTP transport for the Clover test (None = the real network;
    tests replace this)."""
    return None


def test_clover(public: dict, secrets: dict) -> TestResult:
    """Reads the merchant (name) and its properties (time zone) with the
    token - the cheapest authenticated reads. Never writes to Clover."""
    from app.pos.adapters.clover.client import CloverAuthError, CloverClient, CloverError

    merchant = (public.get("merchant_id") or "").strip()
    token = (secrets.get("access_token") or "").strip()
    if not merchant or not token:
        return TestResult(False, "Enter the Clover Merchant ID and API token first, or use Connect with Clover.")
    client = CloverClient(
        merchant, token, public.get("region") or "us", transport=_clover_transport(), max_tries=1, timeout=TIMEOUT
    )
    try:
        info = client.merchant()
        try:
            tz = (client.properties() or {}).get("timezone")
        except CloverError:
            tz = None
    except CloverAuthError as exc:
        if (public.get("mode") or "") == "oauth":
            return TestResult(False, "Clover refused the saved connection. Choose Connect with Clover again.")
        return _http_status_message("Clover", exc.status or 401)
    except CloverError as exc:
        if exc.status:
            return _http_status_message("Clover", exc.status)
        return TestResult(False, "Could not reach Clover. Check the region and try again.")
    name = str((info or {}).get("name") or "the merchant")[:80]
    where = f" (time zone {str(tz)[:40]})" if tz else ""
    return TestResult(True, f"Connected to Clover: {name}{where}.")


TESTERS: Dict[str, Callable[[dict, dict], TestResult]] = {
    "toast": test_toast,
    "whatsapp": test_whatsapp,
    "smtp": test_smtp,
    "ai": test_ai,
    "clover": test_clover,
}


def run_test(kind: str, public: dict, secrets: dict) -> TestResult:
    fn = TESTERS[kind]
    try:
        result = fn(public, secrets)
    except Exception:  # noqa: BLE001 - a tester bug must not 500 or leak
        return TestResult(False, "The connection test failed unexpectedly. Try again later.")
    message = str(result.message or "")[:400]
    parts = tuple(
        {"name": str(p.get("name") or ""), "label": str(p.get("label") or "")[:40], "ok": bool(p.get("ok")),
         "state": str(p.get("state") or ""), "text": str(p.get("text") or "")[:200]}
        for p in (getattr(result, "parts", ()) or ()) if isinstance(p, dict)
    )
    # Last guard: never echo a secret back, whatever a tester wrote.
    for value in secrets.values():
        if value and len(str(value)) >= 4 and (
            str(value) in message or any(str(value) in p["text"] for p in parts)
        ):
            message = "The connection test failed. Check the values and try again."
            parts = ()
    return TestResult(bool(result.ok), message, parts)
