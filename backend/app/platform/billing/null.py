"""The default billing provider: nothing is billed, nothing is sent."""

from typing import Any, Dict, Optional


class NullBilling:
    name = "none"

    def ensure_customer(self, store_code: str, *, name: str, email: Optional[str] = None) -> Optional[str]:
        return None

    def plan_changed(self, store_code: str, plan: str) -> Dict[str, Any]:
        return {"provider": "none", "sent": False}

    def handle_webhook(self, payload: bytes, signature: str) -> Dict[str, Any]:
        return {"provider": "none", "handled": False}
