"""Items Sold for today on a Toast store: when the live pull fails, the 503
says what the store can do about it, in plain words (final demo check,
Oct 22): a store with no Toast keys is told Toast isn't connected (not
"try again"), and a store with keys never sees an error class name such as
"(ConnectError)" or a date that isn't the one on screen."""

import httpx
import pytest

from app.config import get_settings
from app.routers import sales as sales_router
from app.services import items_sold as items_sold_mod
from app.services import sales_sync
from tests.conftest import client_for, make_user


@pytest.fixture
def admin(db):
    return client_for(db, make_user(db, "owner", is_admin=True))


@pytest.fixture
def toast_down(monkeypatch):
    calls = []

    def fake_fetch(business_date):
        calls.append(business_date)
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(sales_sync, "fetch_orders_for_business_date", fake_fetch)
    items_sold_mod.clear_live_cache()
    yield calls
    items_sold_mod.clear_live_cache()


def _keys(monkeypatch, on: bool):
    s = get_settings()
    for name in ("toast_client_id", "toast_client_secret", "toast_restaurant_guid"):
        monkeypatch.setattr(s, name, "x" if on else "")


def test_no_toast_keys_says_not_connected(admin, toast_down, monkeypatch):
    _keys(monkeypatch, False)
    for _ in range(2):  # the first failure, then the cool-down answer
        r = admin.get("/api/sales/items-sold")
        assert r.status_code == 503
        assert r.json()["detail"] == sales_router.TOAST_NOT_CONNECTED
        assert "Settings › Integrations" in r.json()["detail"]


def test_with_keys_toast_down_is_plain_words(admin, toast_down, monkeypatch):
    _keys(monkeypatch, True)
    first = admin.get("/api/sales/items-sold")
    assert first.status_code == 503
    assert first.json()["detail"] == "Toast didn't answer for today's sales just now - try again in a moment."
    again = admin.get("/api/sales/items-sold")  # inside the failure back-off
    assert again.status_code == 503
    detail = again.json()["detail"]
    assert detail == "Toast didn't answer a moment ago - try again shortly."
    assert "Error" not in detail and "20" not in detail
