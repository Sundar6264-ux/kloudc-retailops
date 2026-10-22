"""Toast's two key pairs on Settings -> Integrations: the main keys (client
ID + secret + restaurant GUID: sales, menu, stock) and the separate Analytics
keys (double-check each day's sales totals). Plain errors when the main keys
are missing, a warning when the Analytics keys repeat the main ones, and Test
connection reports each pair on its own. Secrets never appear in a reply."""

import json

import httpx
import pytest

from app.platform import connector_tests, credentials as creds
from tests.platform.conftest import needs_b, provision, store_login

MAIN_SECRET = "toast-main-secret-AAAA1111"
ANALYTICS_SECRET = "toast-analytics-secret-BBBB2222"
GUID = "1a2b3c4d-0000-4a8b-9c0d-1e2f3a4b5c6d"


def _never_leaks(text: str) -> None:
    for value in (MAIN_SECRET, ANALYTICS_SECRET):
        assert value not in text and value[:-4] not in text


# --- plain validation (no database) ------------------------------------------


def test_only_analytics_keys_get_the_plain_main_keys_sentence():
    with pytest.raises(creds.CredentialError) as exc:
        creds.clean_public("toast", {"analytics_client_id": "a-1"})
    assert str(exc.value) == creds.TOAST_MAIN_KEYS_MISSING
    assert exc.value.field == "client_id"
    assert "client_id" not in str(exc.value)  # no raw field name


def test_other_kinds_name_the_field_as_the_form_labels_it():
    with pytest.raises(creds.CredentialError) as exc:
        creds.clean_public("smtp", {"username": "u"})
    assert str(exc.value) == "Fill in the Server for Email (SMTP)."
    with pytest.raises(creds.CredentialError) as exc:
        creds.clean_public("toast", {"client_id": "c", "restaurant_guid": "g", "api_base": "http://x.example.com"})
    assert str(exc.value).startswith("API address must be")


def test_warnings_same_keys_and_half_pairs():
    pub = {"client_id": "abc", "analytics_client_id": " ABC "}
    assert creds.credential_warnings("toast", pub, {"client_secret": "s1", "analytics_client_secret": "s2"}) == [
        creds.SAME_KEYS_WARNING]
    pub = {"client_id": "abc", "analytics_client_id": "other"}
    assert creds.credential_warnings("toast", pub, {"client_secret": "same", "analytics_client_secret": "same"}) == [
        creds.SAME_KEYS_WARNING]
    assert creds.credential_warnings("toast", pub, {"client_secret": "s1", "analytics_client_secret": "s2"}) == []
    assert "Analytics client secret" in creds.credential_warnings("toast", pub, {"client_secret": "s1"})[0]
    assert "Analytics client ID" in creds.credential_warnings(
        "toast", {"client_id": "abc"}, {"client_secret": "s1", "analytics_client_secret": "s2"})[0]
    assert creds.credential_warnings("toast", {"client_id": "abc"}, {"client_secret": "s1"}) == []
    assert creds.credential_warnings("smtp", {}, {}) == []


def test_a_restaurant_id_that_isnt_a_guid_warns_but_saves():
    # Final demo check (Oct 22): "not-a-guid" saved silently and every Toast
    # call would then fail. A plain warning; the save itself still goes on.
    for bad in ("not-a-guid", "Harbor Foods", "1a2b3c4d-5e6f-7a8b-9c0d", "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d"):
        assert creds.credential_warnings("toast", {"client_id": "c", "restaurant_guid": bad}, {"client_secret": "s"}) == [
            creds.TOAST_GUID_WARNING], bad
    for good in (GUID, " 1A2B3C4D-5E6F-7A8B-9C0D-1E2F3A4B5C6D ", ""):
        assert creds.credential_warnings("toast", {"client_id": "c", "restaurant_guid": good}, {"client_secret": "s"}) == []


# --- Test connection: each pair on its own line -------------------------------


def _toast_server(*, main_ok=True, analytics_ok=True, restaurant=200):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/authentication/login"):
            body = json.loads(request.content)
            seen.append(body["clientId"])
            good = (body["clientId"] == "main-id" and main_ok) or (body["clientId"] == "an-id" and analytics_ok)
            if not good:
                return httpx.Response(401, json={"message": "bad"})
            return httpx.Response(200, json={"token": {"accessToken": f"tok-{body['clientId']}"}})
        if "/restaurants/v1/restaurants/" in request.url.path:
            if restaurant != 200:
                return httpx.Response(restaurant)
            return httpx.Response(200, json={"general": {"name": "Alpha Grill"}})
        return httpx.Response(404)

    return handler, seen


@pytest.fixture
def toast_net(monkeypatch):
    def install(**kw):
        handler, seen = _toast_server(**kw)
        monkeypatch.setattr(connector_tests, "_toast_transport", lambda: httpx.MockTransport(handler))
        monkeypatch.setattr(connector_tests, "_url_private", lambda url: False)
        return seen

    return install


MAIN_PUBLIC = {"client_id": "main-id", "restaurant_guid": GUID}
BOTH_SECRETS = {"client_secret": MAIN_SECRET, "analytics_client_secret": ANALYTICS_SECRET}


def _parts(result):
    return {p["name"]: (p["state"], p["text"]) for p in result.parts}


def test_both_pairs_working(toast_net):
    seen = toast_net()
    r = connector_tests.run_test("toast", {**MAIN_PUBLIC, "analytics_client_id": "an-id"}, BOTH_SECRETS)
    assert r.ok is True and seen == ["main-id", "an-id"]
    assert r.message == "Main keys: working. Connected to Alpha Grill. Analytics keys: working."
    assert _parts(r)["analytics"][0] == "working"
    _never_leaks(r.message + json.dumps(r.parts))


def test_analytics_not_added(toast_net):
    seen = toast_net()
    r = connector_tests.run_test("toast", MAIN_PUBLIC, {"client_secret": MAIN_SECRET})
    assert r.ok is True and seen == ["main-id"]
    assert _parts(r)["analytics"][0] == "not_added"
    assert "Analytics keys: not added." in r.message


def test_analytics_not_accepted_keeps_the_main_test_passing(toast_net):
    toast_net(analytics_ok=False)
    r = connector_tests.run_test("toast", {**MAIN_PUBLIC, "analytics_client_id": "an-id"}, BOTH_SECRETS)
    assert r.ok is True
    assert _parts(r)["main"][0] == "working" and _parts(r)["analytics"][0] == "refused"
    assert "Analytics keys: not accepted by Toast." in r.message


def test_main_refused_still_reports_analytics(toast_net):
    toast_net(main_ok=False)
    r = connector_tests.run_test("toast", {**MAIN_PUBLIC, "analytics_client_id": "an-id"}, BOTH_SECRETS)
    assert r.ok is False
    assert r.message.startswith("Main keys: not accepted by Toast.")
    assert _parts(r)["analytics"][0] == "working"


def test_wrong_restaurant_id(toast_net):
    toast_net(restaurant=404)
    r = connector_tests.run_test("toast", MAIN_PUBLIC, {"client_secret": MAIN_SECRET})
    assert r.ok is False and "Restaurant ID (GUID)" in r.message


def test_same_keys_are_not_sent_twice(toast_net):
    seen = toast_net()
    r = connector_tests.run_test(
        "toast", {**MAIN_PUBLIC, "analytics_client_id": "main-id"},
        {"client_secret": MAIN_SECRET, "analytics_client_secret": MAIN_SECRET})
    assert r.ok is True and seen == ["main-id"]
    assert _parts(r)["analytics"] == ("same_keys", creds.SAME_KEYS_WARNING)


def test_half_an_analytics_pair(toast_net):
    toast_net()
    r = connector_tests.run_test("toast", {**MAIN_PUBLIC, "analytics_client_id": "an-id"}, {"client_secret": MAIN_SECRET})
    assert _parts(r)["analytics"] == ("missing", "only half added. Add the Analytics client secret too.")


def test_main_keys_missing_is_one_plain_sentence(toast_net):
    toast_net()
    r = connector_tests.run_test("toast", {"analytics_client_id": "an-id"}, {"analytics_client_secret": ANALYTICS_SECRET})
    assert r.ok is False and r.message == creds.TOAST_MAIN_KEYS_MISSING and r.parts == ()


def test_a_part_echoing_a_secret_is_dropped(monkeypatch):
    def leaky(public, secrets):
        return connector_tests.TestResult(True, "fine", ({"name": "main", "label": "Main keys", "ok": True,
                                                          "state": "working", "text": f"used {MAIN_SECRET}"},))

    monkeypatch.setitem(connector_tests.TESTERS, "toast", leaky)
    r = connector_tests.run_test("toast", MAIN_PUBLIC, {"client_secret": MAIN_SECRET})
    assert r.parts == () and MAIN_SECRET not in r.message


# --- the routes ---------------------------------------------------------------


@pytest.fixture
def store_admin(client):
    provision("ALPHA")
    token = store_login(client, "ALPHA", "ann", "alpha-owner-pass").json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@needs_b
def test_save_only_analytics_keys_is_refused_in_plain_words(store_admin):
    r = store_admin.put("/api/settings/integrations/toast",
                        json={"public": {"analytics_client_id": "an-id"},
                              "secrets": {"analytics_client_secret": ANALYTICS_SECRET}})
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == "invalid" and body["message"] == creds.TOAST_MAIN_KEYS_MISSING
    assert body["field"] == "client_id"
    _never_leaks(r.text)
    # Main public fields but no secret: the same sentence, about the secret.
    r = store_admin.put("/api/settings/integrations/toast", json={"public": MAIN_PUBLIC})
    assert r.status_code == 400 and r.json()["message"] == creds.TOAST_MAIN_KEYS_MISSING
    assert r.json()["field"] == "client_secret"


@needs_b
def test_save_same_keys_warns_but_saves(store_admin):
    r = store_admin.put("/api/settings/integrations/toast",
                        json={"public": {**MAIN_PUBLIC, "analytics_client_id": "main-id"},
                              "secrets": {"client_secret": MAIN_SECRET, "analytics_client_secret": MAIN_SECRET}})
    assert r.status_code == 200, r.text
    assert r.json()["configured"] is True and r.json()["warnings"] == [creds.SAME_KEYS_WARNING]
    _never_leaks(r.text)
    # Separate keys, the analytics secret kept (blank = keep): no warning.
    r = store_admin.put("/api/settings/integrations/toast",
                        json={"public": {"analytics_client_id": "an-id"},
                              "secrets": {"analytics_client_secret": ANALYTICS_SECRET, "client_secret": ""}})
    assert r.status_code == 200 and r.json()["warnings"] == []


@needs_b
def test_test_connection_answers_each_pair(store_admin, toast_net):
    toast_net(analytics_ok=False)
    store_admin.put("/api/settings/integrations/toast",
                    json={"public": {**MAIN_PUBLIC, "analytics_client_id": "an-id"}, "secrets": BOTH_SECRETS})
    r = store_admin.post("/api/settings/integrations/toast/test")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert [(p["label"], p["state"]) for p in body["parts"]] == [("Main keys", "working"), ("Analytics keys", "refused")]
    _never_leaks(r.text)
    # Unsaved values with only Analytics keys: the plain sentence, not a raw field name.
    store_admin.delete("/api/settings/integrations/toast")
    r = store_admin.post("/api/settings/integrations/toast/test",
                         json={"public": {"analytics_client_id": "an-id"}})
    assert r.status_code == 400 and r.json()["message"] == creds.TOAST_MAIN_KEYS_MISSING
