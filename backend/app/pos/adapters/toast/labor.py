"""Toast Labor API: employees (Open Checks server names).

``fetch_employees`` (raw JSON) moved unchanged from ``app/toast_client.py`` in
multi-store M1 batch MT2 (``app.toast_client.fetch_employees`` still works).
MT3 adds ``map_employee`` (raw -> ``Employee``) with the display-name rule
that lived in ``services/open_orders.py`` (``_employee_name``).
"""

import httpx

from app.config import get_settings
from app.pos.adapters.toast import client as _client
from app.pos.models import Employee


def fetch_employees() -> list[dict]:
    """Return raw employee JSON objects from Toast's Labor API (not paginated
    in practice - the restaurant's real employee list is small enough that a
    single call returns everything; add pagination here if that ever stops
    being true).
    """
    settings = get_settings()
    with httpx.Client() as client:
        token = _client.get_access_token(client)
        headers = {
            "Authorization": f"Bearer {token}",
            "Toast-Restaurant-External-ID": settings.toast_restaurant_guid,
        }
        resp = client.get(
            f"{settings.toast_api_base}/labor/v1/employees",
            headers=headers,
            timeout=30,
        )
        if resp.status_code == 401:  # token revoked / expired early: log in again once
            _client._drop_token()
            headers["Authorization"] = f"Bearer {_client.get_access_token(client)}"
            resp = client.get(f"{settings.toast_api_base}/labor/v1/employees", headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()


def _employee_name(emp: dict) -> str:
    chosen = (emp.get("chosenName") or "").strip()
    first = (emp.get("firstName") or "").strip()
    last = (emp.get("lastName") or "").strip()
    full = " ".join(p for p in (first, last) if p)
    if chosen and full:
        return f"{full} ({chosen})"
    return chosen or full or "Unknown"


def map_employee(emp: dict) -> Employee:
    """A Toast employee -> ``Employee`` (a deleted one is not active)."""
    return Employee(external_id=emp.get("guid"), name=_employee_name(emp), active=not emp.get("deleted"))
