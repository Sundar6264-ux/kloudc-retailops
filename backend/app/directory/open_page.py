"""The ``/s/{code}`` page: "Open this store in the KloudC RetailOps app" (M2 contract
4.2, 4.3).

QR codes and store links point at ``https://<PLATFORM_DOMAIN>/s/<CODE>``.
With the app installed, the universal link / app link opens the app and this
page is never seen. Without it, the page shows the store code and the store
download links. The page never looks the code up (unknown codes get the same
page, so nothing leaks) and the code is read from the URL in the browser, so
the service, the static files and the Worker serve identical bytes.
"""

import base64
import hashlib
import html
from typing import Tuple

# Plain DOM, textContent only (the code from the URL is never parsed as HTML).
_SCRIPT = (
    "(function(){"
    "var m=location.pathname.match(/^\\/s\\/([^\\/?#]+)/);"
    "if(!m)return;"
    "var c='';try{c=decodeURIComponent(m[1]);}catch(e){return;}"
    "c=c.trim().toUpperCase().replace(/-/g,'_');"
    "if(!/^[A-Z0-9][A-Z0-9_]{2,23}$/.test(c))return;"
    "document.getElementById('code').textContent=c;"
    "document.getElementById('has-code').hidden=false;"
    "})();"
)

_STYLE = (
    "body{margin:0;font:16px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;"
    "background:#f8fafc;color:#0f172a}"
    "main{max-width:28rem;margin:0 auto;padding:3rem 1rem}"
    "h1{font-size:1.5rem;margin:0 0 1rem}"
    ".code{display:inline-block;font:600 1.25rem ui-monospace,Menlo,monospace;letter-spacing:.05em;"
    "background:#e2e8f0;border-radius:.375rem;padding:.25rem .5rem}"
    ".links{display:flex;flex-direction:column;gap:.75rem;margin-top:1.5rem}"
    ".links a{display:block;text-align:center;padding:.75rem;border-radius:.5rem;background:#004bad;"
    "color:#fff;text-decoration:none;font-weight:600}"
    "@media (prefers-color-scheme:dark){body{background:#0f172a;color:#e2e8f0}.code{background:#334155}}"
)


def _sha256_csp(text: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii") + "'"


def content_security_policy() -> str:
    return (
        "default-src 'none'; "
        f"script-src {_sha256_csp(_SCRIPT)}; "
        f"style-src {_sha256_csp(_STYLE)}; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )


def open_page(product_name: str, app_store_url: str, play_store_url: str) -> Tuple[bytes, str]:
    """Returns (html bytes, Content-Security-Policy value)."""
    name = html.escape(product_name)
    links = []
    if app_store_url:
        links.append(f'<a href="{html.escape(app_store_url)}">Get {name} on the App Store</a>')
    if play_store_url:
        links.append(f'<a href="{html.escape(play_store_url)}">Get {name} on Google Play</a>')
    body = (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex">'
        f"<title>Open in {name}</title>"
        f"<style>{_STYLE}</style></head>"
        "<body><main>"
        f"<h1>Open this store in the {name} app</h1>"
        f"<p>If the {name} app is installed, this link opens it. If not, install it, "
        "then enter your store code.</p>"
        '<p id="has-code" hidden>Store code: <span class="code" id="code"></span></p>'
        f'<div class="links">{"".join(links)}</div>'
        "</main>"
        f"<script>{_SCRIPT}</script>"
        "</body></html>\n"
    )
    return body.encode("utf-8"), content_security_policy()
