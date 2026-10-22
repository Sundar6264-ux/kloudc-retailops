"""Label printing, single-store parts (docs/LABEL_PRINTING_FEATURE.md):
the store feature rule, the option and address checks, the label site's
sign-in check (GET /api/labels/session) and the p0005 migration on SQLite.
The shared-service parts (requests, hand-off) are in
tests/platform/test_label_printing_platform.py."""

import importlib.util
import logging
import pathlib

import pytest
from sqlalchemy import create_engine, inspect, text

from app.config import REFUSED_DOMAIN, Settings, check_label_printing_options, get_settings
from app.platform.errors import PlatformError
from app.store import settings_view as sv
from tests.conftest import client_for, make_user

URL = "https://labels-x.retailops.invalid"


def _settings(**values) -> Settings:
    return Settings(_env_file=None, **values)


# --- 1. the store feature follows the table ----------------------------------------------


@pytest.mark.parametrize(
    "enabled, url, state, has_labels",
    [
        (None, URL, "on", True),  # legacy: an address alone means on (every store today)
        (None, "", "off", False),
        (True, "", "on_no_address", False),
        (True, URL, "on", True),
        (False, URL, "off", False),
        (False, "", "off", False),
    ],
)
def test_store_feature_table(enabled, url, state, has_labels):
    s = _settings(label_printing_enabled=enabled, labels_url=url)
    assert sv.label_printing_state(s) == state
    assert ("labels" in sv.store_features(s)) is has_labels
    assert sv.label_printing_enabled(s) is (state != "off")


def test_blank_env_values_mean_unset():
    s = _settings(label_printing_enabled="", label_printing_options="")
    assert s.label_printing_enabled is None and s.label_printing_options == {}
    s = _settings(label_printing_enabled="true", label_printing_options='{"label_size": "62x33.5"}')
    assert s.label_printing_enabled is True and sv.label_printing_options(s) == {"label_size": "62x33.5"}


# --- 2. options -------------------------------------------------------------------------


def test_options_keep_unknown_keys_and_reserved_ones():
    value = {
        "printer_profile": "brother_62mm",
        "label_size": "62x33.5",
        "variants": ["standard", "sale", "clearance", "shelf"],
        "default_variant": "standard",
        "fields": ["department", "name", "size", "price", "barcode"],
        "templates": [{"id": "shelf", "name": "Shelf tag", "variant": "shelf"}],
        "version": 1,
        "something_new": {"a": True, "b": None, "c": 1.5},
    }
    assert check_label_printing_options(value) == value
    assert check_label_printing_options(None) == {} and check_label_printing_options("  ") == {}


@pytest.mark.parametrize(
    "bad",
    [
        "not json",
        "[1, 2]",
        ["a"],
        {"Label": 1},  # key pattern
        {"9x": 1},
        {"a" * 41: 1},
        {"a": {"b": {"c": {"d": 1}}}},  # 4 levels
        {"a": [[["x"]]]},
        {"a": list(range(51))},
        {"a": "x" * 201},
        {"a": "x" * 190, **{f"k{i}": "y" * 190 for i in range(25)}},  # over 4096 bytes
        {"a": float("nan")},
    ],
)
def test_options_refused(bad):
    with pytest.raises(ValueError):
        check_label_printing_options(bad)
    with pytest.raises(Exception):
        _settings(label_printing_options=bad)


def test_invalid_stored_options_read_as_empty():
    s = _settings()
    object.__setattr__(s, "label_printing_options", {"Bad Key": 1})
    assert sv.label_printing_options(s) == {}


# --- 3. the label site address ----------------------------------------------------------


@pytest.fixture
def platform_domain(monkeypatch):
    from app.config import process_settings

    s = process_settings()
    monkeypatch.setattr(s, "platform_domain", "retailops.test")
    monkeypatch.setattr(s, "directory_extra_api_hosts", "labels.partner.example")
    return s


@pytest.mark.parametrize("good", ["https://labels-x.retailops.test", "https://labels.partner.example",
                                  " https://labels-x.retailops.test "])
def test_address_accepted(platform_domain, good):
    from app.platform.urls import validate_origin_url

    assert validate_origin_url(good, "labels_url") == good.strip()


@pytest.mark.parametrize(
    "bad",
    [
        "http://labels-x.retailops.test",
        "https://labels-x.retailops.test/",
        "https://labels-x.retailops.test/app",
        "https://labels-x.retailops.test?x=1",
        "https://labels-x.retailops.test#x",
        "https://u:p@labels-x.retailops.test",
        "https://u@labels-x.retailops.test",
        "https://labels-x.retailops.test:8443",
        "https://labels.example.com",
        "https://retailops.test.evil.example",
        f"https://labels.{REFUSED_DOMAIN}",
        "https://labels x.retailops.test",
        "javascript:alert(1)",
        "//labels-x.retailops.test",
    ],
)
def test_address_refused(platform_domain, bad):
    from app.platform.urls import validate_origin_url

    with pytest.raises(PlatformError) as exc:
        validate_origin_url(bad, "labels_url")
    assert exc.value.status == 400 and exc.value.extra["field"] == "labels_url"


def test_stored_address_is_not_rechecked_on_merge():
    # Only writes are checked: a value already stored under another domain
    # still loads (the feature works as before for that store).
    s = _settings(labels_url="https://labels.example.com")
    assert sv.labels_url(s) == "https://labels.example.com" and "labels" in sv.store_features(s)


# --- 4. GET /api/labels/session (single-store server) ------------------------------------


@pytest.fixture
def labels_on(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "labels_url", URL)
    monkeypatch.setattr(s, "label_printing_enabled", None)
    monkeypatch.setattr(s, "label_printing_options", {"label_size": "62x33.5"})
    return s


def test_session_answer_is_exactly_the_contract(db, labels_on):
    user = make_user(db, "maria")
    c = client_for(db, user)
    r = c.get("/api/labels/session", headers={"Accept": "application/json"})
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert list(body) == ["store_code", "user", "is_admin", "demo", "can_print", "label_printing",
                          "session_expires_at", "cache_seconds"]
    assert body["user"] == {"id": user.id, "username": "maria", "display_name": "maria"}
    assert body["is_admin"] is False and body["demo"] is False and body["can_print"] is True
    assert body["label_printing"] == {"enabled": True, "options": {"label_size": "62x33.5"}}
    assert body["session_expires_at"].endswith("Z") and body["cache_seconds"] == 60
    token = c.headers["Authorization"].split(" ", 1)[1]
    assert token not in r.text


def test_session_refusals(db, anon_client, labels_on, monkeypatch):
    assert anon_client.get("/api/labels/session").status_code == 401
    r = anon_client.get("/api/labels/session", headers={"Authorization": "Bearer _.not-a-real-token-at-all-xx"})
    assert r.status_code == 401 and r.json()["error"] == "session_expired"
    c = client_for(db, make_user(db, "maria"))
    monkeypatch.setattr(labels_on, "label_printing_enabled", False)
    r = c.get("/api/labels/session")
    assert r.status_code == 403 and r.json()["error"] == "label_printing_off"
    assert r.json()["message"] == "Label printing is turned off for this store."
    monkeypatch.setattr(labels_on, "label_printing_enabled", True)
    monkeypatch.setattr(labels_on, "labels_url", "")
    assert c.get("/api/labels/session").status_code == 403  # on, but no address yet


def test_demo_login_cannot_print(db, labels_on, monkeypatch):
    monkeypatch.setattr(labels_on, "demo_usernames", "app_store_review")
    body = client_for(db, make_user(db, "app_store_review")).get("/api/labels/session").json()
    assert body["demo"] is True and body["can_print"] is False
    monkeypatch.setattr(labels_on, "demo_mode", True)  # the DEMO store: every login
    body = client_for(db, make_user(db, "maria")).get("/api/labels/session").json()
    assert body["demo"] is True and body["can_print"] is False


def test_session_rate_limit_per_token(db, labels_on):
    c = client_for(db, make_user(db, "maria"))
    codes = [c.get("/api/labels/session").status_code for _ in range(121)]
    assert codes[:120] == [200] * 120 and codes[120] == 429
    # Another person's token is not slowed down.
    assert client_for(db, make_user(db, "sam")).get("/api/labels/session").status_code == 200


def test_token_never_logged(db, labels_on, caplog):
    c = client_for(db, make_user(db, "maria"))
    token = c.headers["Authorization"].split(" ", 1)[1]
    with caplog.at_level(logging.DEBUG):
        c.get("/api/labels/session")
        c.get("/api/labels/session", headers={"Authorization": f"Bearer {token}x"})
    assert token not in caplog.text and token.split(".", 1)[1] not in caplog.text


def test_status_card_on_a_single_store_server(db, labels_on):
    body = client_for(db, make_user(db, "boss", is_admin=True)).get("/api/label-printing").json()
    assert body == {"enabled": True, "state": "on", "url": URL, "can_request": False, "request": None}
    r = client_for(db, make_user(db, "boss2", is_admin=True)).post("/api/label-printing/request", json={})
    assert r.status_code == 409 and r.json()["error"] == "platform_mode_required"


def test_branding_carries_label_printing(anon_client, labels_on):
    body = anon_client.get("/api/app/branding").json()
    assert body["label_printing"] == {"enabled": True, "url": URL, "options": {"label_size": "62x33.5"}}
    assert body["labels_url"] == URL and "labels" in body["store_features"]


# --- 5. p0005 on SQLite: up, down, up; one open request per store and feature --------------


def _p0005():
    path = pathlib.Path(__file__).resolve().parents[1] / "alembic_platform" / "versions" / "p0005_feature_requests.py"
    spec = importlib.util.spec_from_file_location("p0005_feature_requests", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_p0005_up_down_up_on_sqlite():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from app.platform.models import PlatformBase

    eng = create_engine("sqlite://")
    PlatformBase.metadata.tables["stores"].create(eng)
    mod = _p0005()
    with eng.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            mod.upgrade()
            mod.downgrade()
            assert not {"feature_requests", "label_handoffs"} & set(inspect(conn).get_table_names())
            mod.upgrade()
        conn.execute(text("INSERT INTO stores (id, code, name, status, db_name, timezone, pos_provider, pack_id, "
                          "created_at, updated_at, created_by) VALUES (1, 'ALPHA', 'A', 'active', 'db_alpha', 'UTC', "
                          "'toast', 'p', '2026-01-01', '2026-01-01', 't')"))
        ins = text("INSERT INTO feature_requests (store_id, feature, status, requested_by, created_at) "
                   "VALUES (1, 'label_printing', :s, 'x', '2026-01-01')")
        conn.execute(ins, {"s": "declined"})
        conn.execute(ins, {"s": "requested"})
        conn.execute(ins, {"s": "cancelled"})
    with pytest.raises(Exception):
        with eng.begin() as conn:
            conn.execute(ins, {"s": "requested"})
    eng.dispose()
