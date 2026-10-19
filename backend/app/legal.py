"""Legal links and the per-store AI consent notice (M2 batch MT13).

One place for what the website and app show under "About", in the login
footer, and before the first Ask Inventory Bot question:
- the company and its privacy / terms / support links (KloudC LLC by
  default; settings PRIVACY_URL / TERMS_URL / SUPPORT_URL / COMPANY_NAME);
- the AI notice (Apple guideline 5.1.2(i): say which outside AI service gets
  data and get the person's permission before sending any). The text is the
  setting AI_CONSENT_NOTICE, blank = DEFAULT_AI_CONSENT_NOTICE below; the AI
  switch is the setting AI_ENABLED (contract section 1.3).

Every setting is read with getattr and the contract default (rule 8), so
this works before and after MT7 adds the fields to app/config.py.
"""

import hashlib
from typing import Optional

from app.config import get_settings

DEFAULT_COMPANY = "KloudC LLC"
DEFAULT_PRODUCT = "KloudC RetailOps"
DEFAULT_PRIVACY_URL = "https://kloudc.com/kloudc-privacy-policy/"
DEFAULT_SUPPORT_URL = "https://kloudc.com/kloudc-support/"

AI_PROVIDER = "Anthropic"
AI_PROVIDER_SERVICE = "Claude"

DEFAULT_AI_CONSENT_NOTICE = (
    "Ask Inventory Bot uses an outside AI service. When you ask a question, "
    "your question and the store data needed to answer it (such as item names, "
    "stock counts, sales figures and vendor names) are sent to Anthropic, the "
    "company that provides the Claude AI model, to write the answer. Do not "
    "type personal information about customers or staff into your question. "
    "Nothing is sent until you agree, and nothing is sent if you choose "
    "Not now. You can read more in the Privacy Policy."
)


def _setting(name: str, default):
    value = getattr(get_settings(), name, default)
    return default if value is None else value


def _url_or_none(value: Optional[str]) -> Optional[str]:
    value = (value or "").strip()
    return value or None


def company_name() -> str:
    return (str(_setting("company_name", DEFAULT_COMPANY)) or "").strip() or DEFAULT_COMPANY


def product_name() -> str:
    return (str(_setting("product_name", DEFAULT_PRODUCT)) or "").strip() or DEFAULT_PRODUCT


def legal_links() -> dict:
    """{company, privacy_url, terms_url, support_url} - the same block as
    branding's "legal" (contract section 2). terms_url is None until the
    owner sets TERMS_URL."""
    return {
        "company": company_name(),
        "privacy_url": _url_or_none(_setting("privacy_url", DEFAULT_PRIVACY_URL)),
        "terms_url": _url_or_none(_setting("terms_url", "")),
        "support_url": _url_or_none(_setting("support_url", DEFAULT_SUPPORT_URL)),
    }


def ai_enabled() -> bool:
    return bool(_setting("ai_enabled", False))


def ai_consent_notice() -> str:
    text = (str(_setting("ai_consent_notice", "")) or "").strip()
    return text or DEFAULT_AI_CONSENT_NOTICE


def ai_notice_version(text: Optional[str] = None) -> str:
    """Short, stable id of the notice text: a client asks again when it
    changes (first 12 hex of sha256)."""
    text = ai_consent_notice() if text is None else text
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def legal_payload() -> dict:
    """GET /api/app/legal (public, no login)."""
    notice = ai_consent_notice()
    return {
        "schema": 1,
        "product": product_name(),
        **legal_links(),
        "ai": {
            "enabled": ai_enabled(),
            "provider": AI_PROVIDER,
            "service": AI_PROVIDER_SERVICE,
            "notice": notice,
            "notice_version": ai_notice_version(notice),
        },
        "demo": bool(_setting("demo_mode", False)),
    }
