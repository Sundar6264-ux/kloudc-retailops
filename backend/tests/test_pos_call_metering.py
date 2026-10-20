"""POS call metering (docs/WEBSITE_READY_CONTRACT.md C7): every HTTP request
sent to a POS API counts once as usage.POS_API_CALLS - retries and logins
included - through app/pos/http.py, with a fake transport (no network)."""

import types

import httpx
import pytest

import app.config as app_config
from app.config import get_settings


@pytest.fixture
def recorded(monkeypatch):
    from app.platform import usage

    calls = []
    monkeypatch.setattr(usage, "record", lambda metric, n=1, code=None: calls.append((metric, n)))
    return calls


def test_hook_counts_each_request(recorded):
    from app.pos.http import metering_hooks

    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={}))
    with httpx.Client(transport=transport, event_hooks=metering_hooks()) as c:
        c.get("https://pos.invalid/a")
        c.post("https://pos.invalid/b", json={})
    assert recorded == [("pos_api_calls", 1), ("pos_api_calls", 1)]


def test_metering_never_breaks_a_call(monkeypatch):
    from app.platform import usage
    from app.pos.http import metering_hooks

    def boom(*a, **k):
        raise RuntimeError("metering down")

    monkeypatch.setattr(usage, "record", boom)
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": 1}))
    with httpx.Client(transport=transport, event_hooks=metering_hooks()) as c:
        assert c.get("https://pos.invalid/a").json() == {"ok": 1}


def test_toast_get_counts_login_retry_and_request(recorded, monkeypatch):
    from app.pos.adapters.toast import client as toast_client

    settings = get_settings().model_copy(update={
        "toast_client_id": "id", "toast_client_secret": "secret", "toast_restaurant_guid": "guid",
        "toast_api_base": "https://toast.invalid",
    })
    monkeypatch.setattr(app_config, "get_settings", lambda: settings)
    monkeypatch.setattr(toast_client, "get_settings", lambda: settings)
    monkeypatch.setattr(toast_client, "_TOKEN_CACHE", {})
    monkeypatch.setattr(toast_client.time, "sleep", lambda s: None)
    answers = [httpx.Response(429, headers={"Retry-After": "0"}), httpx.Response(200, json={"rows": []})]

    def handler(req):
        if req.url.path.endswith("/login"):
            return httpx.Response(200, json={"token": {"accessToken": "t", "expiresIn": 3600}})
        return answers.pop(0)

    real = httpx.Client

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    shim = types.SimpleNamespace(**{k: getattr(httpx, k) for k in dir(httpx) if not k.startswith("__")})
    shim.Client = factory
    monkeypatch.setattr(toast_client, "httpx", shim)
    assert toast_client.toast_get("/x/v1/things") == {"rows": []}
    # login + the rate-limited request + its retry
    assert recorded == [("pos_api_calls", 1)] * 3
