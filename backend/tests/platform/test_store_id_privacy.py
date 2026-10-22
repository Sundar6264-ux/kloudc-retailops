"""Store ID privacy (owner, 2026-10; PLATFORM_CONTRACT 13.4 and 4.5 "As
built"): guessing Store IDs must not tell which stores exist or what they
are called.

- The public lookup answers every valid code the same way on the shared
  service (status, shape, cache headers, api_base_url); only ``code``
  differs. No name, logo, colours or POS name.
- Every sign-in failure (unknown store, unknown user, wrong password,
  archived or not-ready store) gives the same 401 body.
- A paused store's message only after the username and password are right.
"""

import json

from tests.platform.conftest import provision, store_login

CACHE_KEYS = ("cache-control", "access-control-allow-origin", "content-type", "x-content-type-options")


def _look(client, code):
    r = client.get(f"/api/public/stores/{code}")
    body = r.json()
    assert body.pop("code") == code
    return r.status_code, body, {k: r.headers.get(k) for k in CACHE_KEYS}, r


def test_unknown_and_real_codes_read_the_same(client, master_client):
    provision("ALPHA", name="Alpha Fresh Market")
    master_client.put("/api/platform/stores/ALPHA/settings",
                      json={"store_short_name": "Alpha", "brand_color": "#00AA00"})
    real = _look(client, "ALPHA")
    unknown = _look(client, "NOPE_X")
    assert real[0] == unknown[0] == 200
    assert real[1] == unknown[1]  # identical apart from the code
    assert real[2] == unknown[2]  # same cache headers
    assert set(real[3].headers) == set(unknown[3].headers)
    for leak in ("Alpha Fresh Market", "Alpha\"", "00AA00", "Toast", "branding", "name"):
        assert leak not in real[3].text, leak
    # Paused, archived: still the same answer as an unknown code.
    master_client.post("/api/platform/stores/ALPHA/suspend", json={"reason": "unpaid", "message": "Back soon"})
    assert _look(client, "ALPHA")[1] == unknown[1]
    master_client.post("/api/platform/stores/ALPHA/resume")
    master_client.post("/api/platform/stores/ALPHA/archive", json={"confirm": "alpha"})
    assert _look(client, "ALPHA")[:3] == unknown[:3]


def test_every_sign_in_failure_reads_the_same(client, master_client):
    provision("ALPHA")
    provision("BRAVO", owner="bob", password="bravo-owner-pass")
    master_client.post("/api/platform/stores/BRAVO/archive", json={"confirm": "bravo"})
    bodies = []
    for code, user, pw in [
        ("NOPE_X", "ann", "alpha-owner-pass"),  # unknown store
        ("ALPHA", "nobody", "alpha-owner-pass"),  # unknown user
        ("ALPHA", "ann", "wrong-pass-123"),  # wrong password
        ("BRAVO", "bob", "bravo-owner-pass"),  # archived store, right password
    ]:
        r = store_login(client, code, user, pw)
        assert r.status_code == 401, (code, user, r.text)
        bodies.append(r.json())
    assert all(b == bodies[0] for b in bodies), bodies
    assert bodies[0] == {"detail": "That Store ID, username or password isn't right. Check all three and try again."}


def test_paused_message_only_after_correct_credentials(client, master_client):
    provision("ALPHA")
    master_client.post("/api/platform/stores/ALPHA/suspend", json={"reason": "unpaid", "message": "Back soon"})
    unknown = store_login(client, "NOPE_X", "ann", "alpha-owner-pass").json()
    for user, pw in (("ann", "wrong-pass-123"), ("nobody", "alpha-owner-pass")):
        r = store_login(client, "ALPHA", user, pw)
        assert r.status_code == 401 and r.json() == unknown
        assert "Back soon" not in json.dumps(r.json())
    r = store_login(client, "ALPHA", "ann", "alpha-owner-pass")
    assert r.status_code == 403 and r.json()["error"] == "store_suspended" and r.json()["message"] == "Back soon"
