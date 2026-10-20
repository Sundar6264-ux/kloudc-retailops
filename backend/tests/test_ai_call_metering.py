"""AI requests are counted as the store's ``ai_calls`` usage (website ready
Integrate; interface I3). No request leaves the machine: a mock transport
answers."""

import anthropic
import httpx
import pytest

from app.services import anthropic_client


def test_client_counts_every_request(monkeypatch):
    seen = []
    monkeypatch.setattr("app.platform.usage.record", lambda metric, n=1, code=None: seen.append(metric))
    monkeypatch.setattr(anthropic_client, "get_settings", lambda: type("S", (), {"anthropic_api_key": "sk-test"})())
    client = anthropic_client.get_anthropic_client()
    hooks = client._client.event_hooks["request"]
    assert anthropic_client.record_ai_call in hooks

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "m",
            "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1},
        })

    client._client._transport = httpx.MockTransport(answer)
    client.messages.create(model="m", max_tokens=5, messages=[{"role": "user", "content": "hi"}])
    assert seen == ["ai_calls"]


def test_metering_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr("app.platform.usage.record", boom)
    anthropic_client.record_ai_call(None)  # no exception


def test_missing_key_still_fails_clearly(monkeypatch):
    monkeypatch.setattr(anthropic_client, "get_settings", lambda: type("S", (), {"anthropic_api_key": ""})())
    with pytest.raises(anthropic_client.AnthropicNotConfigured):
        anthropic_client.get_anthropic_client()
    assert anthropic.Anthropic  # the SDK is importable
