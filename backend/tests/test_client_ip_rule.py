"""One client-IP rule (app/platform/client_ip.py, pre-production 2026-10-21):
the client-IP header counts only from a trusted proxy (cloudflared /
Tailscale on 127.0.0.1), never from a caller that sends it itself."""

import pytest
from fastapi.testclient import TestClient

from app.platform import client_ip as rule
from app.platform import throttle
from app.services import checklist_public as svc


@pytest.fixture(autouse=True)
def _loopback_only(monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "127.0.0.1")


def test_header_counts_only_from_a_trusted_proxy():
    h = {"CF-Connecting-IP": "203.0.113.7"}
    assert rule.resolve(h, "127.0.0.1") == "203.0.113.7"  # cloudflared on this machine
    assert rule.resolve(h, "::1") == "203.0.113.7"  # ::1 goes with 127.0.0.1
    assert rule.resolve(h, "198.51.100.9") == "198.51.100.9"  # spoofed: the peer wins
    assert rule.resolve(h, None) == "unknown"
    assert rule.resolve({"CF-Connecting-IP": "not-an-ip"}, "127.0.0.1") == "127.0.0.1"
    assert rule.resolve({}, "127.0.0.1") == "127.0.0.1"


def test_trusted_list_networks_star_and_junk(monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "10.0.0.0/8, junk, /tmp/sock")
    assert rule.is_trusted_peer("10.1.2.3") and not rule.is_trusted_peer("127.0.0.1")
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    assert rule.is_trusted_peer("198.51.100.1")
    monkeypatch.delenv("FORWARDED_ALLOW_IPS")
    assert rule.is_trusted_peer("127.0.0.1") and rule.is_trusted_peer("::ffff:127.0.0.1")
    assert not rule.is_trusted_peer("testclient")


def test_last_entry_of_a_list_and_configured_header():
    h = {"X-Real-Client": "6.6.6.6, 203.0.113.8"}
    assert rule.resolve(h, "127.0.0.1", header="X-Real-Client") == "203.0.113.8"


def test_limit_keys_group_ipv6_by_64():
    assert rule.limit_key("2001:db8:1:2:aaaa::1") == "2001:db8:1:2::/64"
    assert rule.limit_key("::ffff:198.51.100.4") == "198.51.100.4"
    assert rule.limit_key("203.0.113.1") == "203.0.113.1"


def test_public_checklist_uses_the_same_rule():
    assert svc.client_ip({"cf-connecting-ip": "203.0.113.7"}, "198.51.100.2") == "198.51.100.2"
    assert svc.client_ip({"cf-connecting-ip": "2001:db8:1:2::5"}, "127.0.0.1") == "2001:db8:1:2::/64"


def _echo_app():
    from fastapi import FastAPI, Request

    app = FastAPI()

    @app.get("/ip")
    def ip(request: Request):
        return {"ip": throttle.client_ip(request)}

    return app


def test_login_throttle_key_ignores_a_forged_header_from_the_internet():
    forged = {"CF-Connecting-IP": "203.0.113.99"}
    direct = TestClient(_echo_app(), client=("198.51.100.20", 50000))
    assert direct.get("/ip", headers=forged).json() == {"ip": "198.51.100.20"}
    via_tunnel = TestClient(_echo_app(), client=("127.0.0.1", 50000))
    assert via_tunnel.get("/ip", headers=forged).json() == {"ip": "203.0.113.99"}
    # Behind the tunnel without the header (Tailscale): the peer.
    assert via_tunnel.get("/ip").json() == {"ip": "127.0.0.1"}
