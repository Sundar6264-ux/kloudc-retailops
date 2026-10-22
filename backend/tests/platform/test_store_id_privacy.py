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


# --- Public store routes before sign-in (follow-up, 2026-10) ------------------------------
#
# /api/app/branding, /api/app/config and /api/app/legal give an unknown code
# exactly what they give a real one: the product's neutral answer, same
# status, shape and cache headers. Only the branding's echoed store_code
# (and so its ETag / length) differs. The store's own look needs a session.

APP_ROUTES = ("/api/app/branding", "/api/app/config", "/api/app/legal")
SAME_HEADERS = ("cache-control", "vary", "content-type", "x-content-type-options", "access-control-allow-origin")
ALPHA_LOOK = ("Alpha Fresh Market", "Alpha Fresh", "00AA00", "9 Elm Road", "555-0199")


def _anon(client):
    return client.__class__(client.app)


def _answer(c, url, code, **kwargs):
    r = c.get(url, params={"store": code} if code is not None else None, **kwargs)
    body = r.json()
    if url == "/api/app/branding":
        echoed = body.pop("store_code")
        assert echoed in (None, code), (url, echoed, code)
    return r.status_code, body, {k: r.headers.get(k) for k in SAME_HEADERS}, sorted(r.headers.keys()), r


def _alpha(master_client):
    provision("ALPHA", name="Alpha Fresh Market")
    r = master_client.put(
        "/api/platform/stores/ALPHA/settings",
        json={"store_short_name": "Alpha Fresh", "brand_color": "#00AA00", "store_address": "9 Elm Road",
              "store_phone": "555-0199"},
    )
    assert r.status_code == 200, r.text


def _assert_same(real, unknown, url):
    assert real[0] == unknown[0] == 200, (url, real[0], unknown[0], real[4].text)
    assert real[1] == unknown[1], url  # identical apart from the echoed code
    assert real[2] == unknown[2], url  # same cache headers
    assert real[3] == unknown[3], url  # same header names
    for leak in ALPHA_LOOK:
        assert leak not in real[4].text, (url, leak)


def test_app_routes_answer_real_and_unknown_codes_the_same(client, master_client):
    _alpha(master_client)
    c = _anon(client)
    for url in APP_ROUTES:
        unknown = _answer(c, url, "NOPE_X")
        _assert_same(_answer(c, url, "ALPHA"), unknown, url)
        # The same through the X-Store header, and with no code at all.
        by_header = c.get(url, headers={"X-Store": "ALPHA"})
        by_header_unknown = c.get(url, headers={"X-Store": "NOPE_X"})
        assert by_header.status_code == by_header_unknown.status_code == 200
        hb, hu = by_header.json(), by_header_unknown.json()
        hb.pop("store_code", None), hu.pop("store_code", None)
        assert hb == hu == unknown[1], url
        assert by_header.headers.get("cache-control") == by_header_unknown.headers.get("cache-control")
    # The neutral branding is the product's own.
    b = c.get("/api/app/branding", params={"store": "ALPHA"}).json()
    assert b["store_code"] == "ALPHA" and b["store_specific"] is False
    assert b["name"] == b["short_name"] == b["product"]["name"] == "KloudC RetailOps"
    assert (b["primary_color"], b["secondary_color"]) == ("#004BAD", "#2EA3F2")
    assert b["address"] is None and b["phone"] is None and b["labels_url"] is None and b["demo"] is False
    assert b["logo_url"].endswith("/branding/logo.png")
    # An invalid code: the same body with no code echoed.
    bad = c.get("/api/app/branding", params={"store": "a b!"}).json()
    assert bad.pop("store_code") is None
    b.pop("store_code")
    assert bad == b


def test_app_routes_paused_archived_moved_stores_read_like_unknown(client, master_client, platform_env, monkeypatch):
    _alpha(master_client)
    c = _anon(client)
    unknown = {url: _answer(c, url, "NOPE_X") for url in APP_ROUTES}
    master_client.post("/api/platform/stores/ALPHA/suspend", json={"reason": "unpaid", "message": "Back soon"})
    for url in APP_ROUTES:
        _assert_same(_answer(c, url, "ALPHA"), unknown[url], url)
        assert "Back soon" not in c.get(url, params={"store": "ALPHA"}).text
    master_client.post("/api/platform/stores/ALPHA/resume")
    # Moved to its own server (409 store_moved before): the same neutral answer.
    from sqlalchemy import text

    from app.platform.db import platform_engine
    from app.tenancy.cache import clear_tenant_caches

    with platform_engine().begin() as conn:
        conn.execute(text("UPDATE stores SET hosting='dedicated', api_base_url='https://alpha.retailops.test' "
                          "WHERE code='ALPHA'"))
    clear_tenant_caches()
    monkeypatch.setattr(platform_env, "dedicated_hosting_enabled", True)
    for url in APP_ROUTES:
        _assert_same(_answer(c, url, "ALPHA"), unknown[url], url)
    monkeypatch.setattr(platform_env, "dedicated_hosting_enabled", False)
    master_client.post("/api/platform/stores/ALPHA/archive", json={"confirm": "alpha"})
    for url in APP_ROUTES:
        _assert_same(_answer(c, url, "ALPHA"), unknown[url], url)


def test_forged_or_expired_tokens_get_the_neutral_answer(client, master_client):
    _alpha(master_client)
    c = _anon(client)
    for url in APP_ROUTES:
        real = _answer(c, url, None, headers={"Authorization": "Bearer ALPHA.not-a-real-session"})
        unknown = _answer(c, url, None, headers={"Authorization": "Bearer NOPE_X.not-a-real-session"})
        _assert_same(real, unknown, url)
    # A master's session is not a store session either.
    master = _answer(master_client, "/api/app/branding", None)
    assert master[1]["store_specific"] is False and "Alpha" not in master[4].text


def test_signed_in_session_gets_its_store_look(client, master_client):
    _alpha(master_client)
    token = store_login(client, "ALPHA", "ann", "alpha-owner-pass").json()["token"]
    c = _anon(client)
    for params in (None, {"store": "ALPHA"}):
        r = c.get("/api/app/branding", params=params, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        b = r.json()
        assert (b["store_code"], b["name"], b["short_name"], b["primary_color"]) == (
            "ALPHA", "Alpha Fresh Market", "Alpha Fresh", "#00AA00")
        assert b["address"] == "9 Elm Road" and b["phone"] == "555-0199" and b["store_specific"] is True
        # Never kept by a shared cache.
        assert r.headers["cache-control"].startswith("private")
    for url in ("/api/app/config", "/api/app/legal"):
        r = c.get(url, params={"store": "ALPHA"}, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, (url, r.text)
    assert c.get("/api/app/legal", params={"store": "ALPHA"},
                 headers={"Authorization": f"Bearer {token}"}).headers["cache-control"].startswith("private")
    # Signed out: neutral again.
    c.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
    b = c.get("/api/app/branding", headers={"Authorization": f"Bearer {token}"}).json()
    assert b["store_specific"] is False and "Alpha" not in json.dumps(b)


# --- Public checklist links: an unknown Store ID reads like a wrong link ----------------------

CHECKLIST_HEADERS = ("cache-control", "content-type", "referrer-policy", "x-robots-tag", "x-content-type-options",
                     "x-frame-options")


def _cl(r):
    return r.status_code, r.json(), {k: r.headers.get(k) for k in CHECKLIST_HEADERS}, sorted(r.headers.keys())


def test_checklist_links_unknown_code_reads_like_a_wrong_link(client):
    provision("ALPHA")
    from app.services import checklist_public as svc

    c = _anon(client)
    key = "kitchen-4821"
    calls = [
        ("get", f"/api/public/checklists/{key}", {}),
        ("post", f"/api/public/checklists/{key}/submit", {"json": {"name": "Sam", "answers": {}}}),
        ("post", f"/api/public/checklists/{key}/media", {"files": {"file": ("a.jpg", b"x", "image/jpeg")}}),
        ("get", f"/api/public/checklists/{key}/images/abc", {}),
    ]
    for method, url, kw in calls:
        svc.limiter.reset() if hasattr(svc.limiter, "reset") else None
        real = _cl(getattr(c, method)(url, params={"store": "ALPHA"}, **kw))
        unknown = _cl(getattr(c, method)(url, params={"store": "NOPE_X"}, **kw))
        assert real == unknown, (url, real, unknown)
        assert real[0] == 404 and real[1] == {"detail": svc.NOT_ACTIVE}
    # Unknown codes count as failed lookups too: the same 429 once over the limit.
    from app.platform import throttle

    throttle.reset_limiters()
    svc.limiter.reset() if hasattr(svc.limiter, "reset") else None
    outs = [c.get(f"/api/public/checklists/{key}", params={"store": "NOPE_X"}).status_code
            for _ in range(svc.FAILED_IP_LIMIT[0] + 1)]
    assert outs[-1] == 429 and outs[0] == 404


# --- A dedicated server: every code it doesn't serve reads the same -------------------------


def test_dedicated_server_answers_unknown_and_shared_codes_the_same(client, platform_env, monkeypatch):
    provision("ALPHA")
    provision("BRAVO", owner="bob", password="bravo-owner-pass")
    monkeypatch.setattr(platform_env, "dedicated_hosting_enabled", True)
    monkeypatch.setattr(platform_env, "dedicated_store_codes", "ALPHA")
    real = store_login(client, "BRAVO", "bob", "bravo-owner-pass")
    wrong = store_login(client, "BRAVO", "bob", "wrong-pass-123")
    unknown = store_login(client, "NOPE_X", "bob", "bravo-owner-pass")
    for r in (real, wrong, unknown):
        assert r.status_code == 409, r.text
    bodies = [r.json() for r in (real, wrong, unknown)]
    for b in bodies:
        b.pop("store_code")
    assert bodies[0] == bodies[1] == bodies[2]
    assert bodies[0]["api_base_url"] == "https://app.retailops.test"
    # A forged token: the same 409 for a real and an unknown code.
    a = _anon(client).get("/api/auth/me", headers={"Authorization": "Bearer BRAVO.forged"})
    b = _anon(client).get("/api/auth/me", headers={"Authorization": "Bearer NOPE_X.forged"})
    assert a.status_code == b.status_code == 409
    ja, jb = a.json(), b.json()
    assert ja.pop("store_code") == "BRAVO" and jb.pop("store_code") == "NOPE_X" and ja == jb
    # Its own store still signs in.
    assert store_login(client, "ALPHA", "ann", "alpha-owner-pass").status_code == 200


# --- A forged token names no store ---------------------------------------------------------


def test_forged_tokens_read_the_same_for_unknown_real_paused_and_archived_stores(client, master_client):
    provision("ALPHA")
    provision("BRAVO", owner="bob", password="bravo-owner-pass")
    provision("CHARLIE", owner="cat", password="charlie-owner-pass")
    master_client.post("/api/platform/stores/BRAVO/suspend", json={"reason": "unpaid", "message": "Back soon"})
    master_client.post("/api/platform/stores/CHARLIE/archive", json={"confirm": "charlie"})
    c = _anon(client)
    for url in ("/api/auth/me", "/api/auth/users", "/api/team/bootstrap"):
        answers = []
        for code in ("NOPE_X", "ALPHA", "BRAVO", "CHARLIE"):
            r = c.get(url, headers={"Authorization": f"Bearer {code}.not-a-real-session"})
            answers.append((r.status_code, r.json()))
            assert "Back soon" not in r.text
        assert all(a == answers[0] for a in answers), (url, answers)
        assert answers[0][0] == 401
    # A real session of the paused store still hears why (pre-production G3).
    from tests.platform.conftest import store_login as _login

    master_client.post("/api/platform/stores/BRAVO/resume")
    token = _login(client, "BRAVO", "bob", "bravo-owner-pass").json()["token"]
    master_client.post("/api/platform/stores/BRAVO/suspend", json={"reason": "unpaid", "message": "Back soon"})
    r = c.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403 and r.json()["error"] == "store_suspended" and r.json()["message"] == "Back soon"


# --- Public pages with a forged token (verification, 2026-10-22) ----------------------------


def _forged_variants(code):
    return (
        {"headers": {"Authorization": f"Bearer {code}.forged"}},
        {"cookies": {"session_token": f"{code}.forged"}},
    )


def test_checklist_not_active_page_never_names_a_store_for_a_forged_token(client, master_client):
    """GET /c/... with a forged token naming a real store used to render that
    store's name and logo on the "not active" page."""
    _alpha(master_client)
    provision("BRAVO", owner="bob", password="bravo-owner-pass")
    master_client.post("/api/platform/stores/BRAVO/suspend", json={"reason": "unpaid", "message": "Back soon"})
    from app.services import checklist_public as svc

    from app.platform import throttle

    throttle.reset_limiters()
    svc.limiter.reset() if hasattr(svc.limiter, "reset") else None
    c = _anon(client)
    plain = c.get("/c/zzzz")
    assert plain.status_code == 404 and "KloudC RetailOps" in plain.text
    for code in ("ALPHA", "BRAVO", "NOPE_X"):
        for kw in _forged_variants(code):
            svc.limiter.reset() if hasattr(svc.limiter, "reset") else None
            throttle.reset_limiters()
            for path in ("/c/zzzz", f"/c/{code}/kitchen-4821", "/c"):
                cc = _anon(client)
                r = cc.get(path, **kw)
                assert r.status_code == 404, (code, path, r.status_code, r.text[:200])
                assert r.text == plain.text, (code, path)
                for leak in ALPHA_LOOK + ("Back soon",):
                    assert leak not in r.text, (code, path, leak)
    # Even a live session: the message page is the product's (the link's
    # store is in the path, not the session's).
    token = store_login(client, "ALPHA", "ann", "alpha-owner-pass").json()["token"]
    throttle.reset_limiters()
    svc.limiter.reset() if hasattr(svc.limiter, "reset") else None
    r = _anon(client).get("/c/BRAVO/kitchen-4821", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404 and r.text == plain.text


def test_checklist_api_answers_forged_tokens_like_no_token(client, master_client):
    """A forged token naming an active store used to reach the route (404)
    while an unknown code got 401 from the middleware: an existence oracle."""
    _alpha(master_client)
    provision("BRAVO", owner="bob", password="bravo-owner-pass")
    provision("CHARLIE", owner="cat", password="charlie-owner-pass")
    master_client.post("/api/platform/stores/BRAVO/suspend", json={"reason": "unpaid", "message": "Back soon"})
    master_client.post("/api/platform/stores/CHARLIE/archive", json={"confirm": "charlie"})
    from app.platform import throttle
    from app.services import checklist_public as svc

    key = "kitchen-4821"
    calls = [
        ("get", f"/api/public/checklists/{key}", {}),
        ("post", f"/api/public/checklists/{key}/submit", {"json": {"name": "Sam", "answers": {}}}),
        ("get", f"/api/public/checklists/{key}/images/abc", {}),
    ]
    for method, url, kw in calls:
        for with_store in (False, True):
            answers = []
            for code in ("NOPE_X", "ALPHA", "BRAVO", "CHARLIE"):
                for extra in _forged_variants(code):
                    throttle.reset_limiters()
                    svc.limiter.reset() if hasattr(svc.limiter, "reset") else None
                    params = {"store": code} if with_store else None
                    r = getattr(_anon(client), method)(url, params=params, **kw, **extra)
                    answers.append((code, r.status_code, r.text))
                    assert "Back soon" not in r.text
            first = answers[0][1:]
            assert all(a[1:] == first for a in answers), (url, with_store, answers)
            assert first[0] == 404 and json.loads(first[1]) == {"detail": svc.NOT_ACTIVE}, (url, first)
    # A real session of the store still reaches its own links (a wrong key: 404).
    token = store_login(client, "ALPHA", "ann", "alpha-owner-pass").json()["token"]
    throttle.reset_limiters()
    svc.limiter.reset() if hasattr(svc.limiter, "reset") else None
    r = _anon(client).get(f"/api/public/checklists/{key}", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404 and r.json() == {"detail": svc.NOT_ACTIVE}
