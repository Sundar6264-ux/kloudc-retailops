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


def test_toast(public: dict, secrets: dict) -> TestResult:
    import httpx

    client_id = (public.get("client_id") or "").strip()
    guid = (public.get("restaurant_guid") or "").strip()
    secret = (secrets.get("client_secret") or "").strip()
    base = (public.get("api_base") or "https://ws-api.toasttab.com").rstrip("/")
    if not client_id or not secret or not guid:
        return TestResult(False, "Enter the Toast client ID, client secret and restaurant GUID first.")
    if _url_private(base):
        return TestResult(False, NOT_ALLOWED)
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.post(
                f"{base}/authentication/v1/authentication/login",
                json={"clientId": client_id, "clientSecret": secret, "userAccessType": "TOAST_MACHINE_CLIENT"},
            )
            if resp.status_code != 200:
                return _http_status_message("Toast", resp.status_code)
            token_obj = (resp.json() or {}).get("token") or {}
            access = token_obj.get("accessToken")
            if not access:
                return TestResult(False, "Toast answered without a token. Check the client ID and secret.")
            r2 = client.get(
                f"{base}/restaurants/v1/restaurants/{guid}",
                headers={"Authorization": f"Bearer {access}", "Toast-Restaurant-External-ID": guid},
            )
            if r2.status_code != 200:
                return _http_status_message("Toast", r2.status_code)
            name = ((r2.json() or {}).get("general") or {}).get("name") or "the restaurant"
            return TestResult(True, f"Connected to Toast: {str(name)[:80]}.")
    except (httpx.HTTPError, OSError, ValueError):
        return TestResult(False, "Could not reach Toast. Check the API address and try again.")


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


def test_clover(public: dict, secrets: dict) -> TestResult:
    return TestResult(False, "Clover connection arrives with M3")


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
    message = str(result.message or "")[:300]
    # Last guard: never echo a secret back, whatever a tester wrote.
    for value in secrets.values():
        if value and len(str(value)) >= 4 and str(value) in message:
            message = "The connection test failed. Check the values and try again."
    return TestResult(bool(result.ok), message)
