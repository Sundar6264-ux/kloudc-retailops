"""One rule for the outside addresses a store points the app at
(docs/LABEL_PRINTING_FEATURE.md 1.1): a dedicated server's address and the
label site's address.

https, the origin only (no path, query, fragment, user-info, port or
trailing slash), the host under PLATFORM_DOMAIN or listed in
DIRECTORY_EXTRA_API_HOSTS, and never the refused legacy domain
(docs/M2_CONTRACT.md rule 0.3). The app's ApiHostPolicy applies the same
rule, so an address saved here is one the app will use.
"""

from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from app.platform.errors import PlatformError

# Plain sentences per field (G16). {domain} is the platform domain.
DEFAULT_MESSAGES: Dict[str, str] = {
    "invalid": "Not a valid URL.",
    "shape": "Use https://<host> with no path, port or trailing slash.",
    "refused": "That domain is not allowed.",
    "host": "The host must be under .{domain} or listed in DIRECTORY_EXTRA_API_HOSTS.",
}


def origin_problem(value: str) -> Optional[str]:
    """None when `value` passes the rule, else which part fails: "invalid",
    "shape", "refused" or "host"."""
    from app.config import refused_host
    from app.platform.config import extra_api_hosts, pget

    v = str(value).strip()
    if not v or any(c.isspace() or ord(c) < 32 for c in v):
        return "shape"
    try:
        parts = urlsplit(v)
        port = parts.port
    except ValueError:
        return "invalid"
    host = (parts.hostname or "").lower()
    if (
        parts.scheme != "https"
        or not host
        or parts.path not in ("",)
        or parts.query
        or parts.fragment
        or parts.username
        or parts.password
        or "@" in parts.netloc
        or port is not None
        or v.endswith("/")
        or v.endswith("?")
        or v.endswith("#")
    ):
        return "shape"
    if refused_host(v):
        return "refused"
    domain = str(pget("platform_domain") or "").strip().strip(".").lower()
    if not ((domain and host.endswith("." + domain)) or host in extra_api_hosts()):
        return "host"
    return None


def validate_origin_url(value: Any, field: str, messages: Optional[Dict[str, str]] = None) -> Optional[str]:
    """The checked origin, or None for blank. Raises PlatformError 400
    ``invalid`` (with ``field``) otherwise."""
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        problem = "invalid"
    else:
        problem = origin_problem(value)
    if problem is None:
        return value.strip()
    from app.platform.config import pget

    text = dict(DEFAULT_MESSAGES, **(messages or {}))[problem]
    domain = str(pget("platform_domain") or "").strip().strip(".").lower()
    raise PlatformError(400, "invalid", text.replace("{domain}", domain), field=field)
