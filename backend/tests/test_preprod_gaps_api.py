"""Pre-production gaps, store-server side (docs/PREPROD_PLAN.md section 1,
track S1): why a session ended (G1), the POS fields in /me and branding (G6,
G9, G19, G20), demo refusals with their code (G15/G21), item IDs with "/"
(G18) and the item vendors page reading less (G14). Additive answers only:
every old key keeps its value."""

import datetime as dt
import json

import pytest

from app.auth import hash_password
from app.features import FEATURES
from app.item_vendor_models import ItemVendor
from app.models import InventorySnapshot, UserSession
from app.services import item_vendors as iv
from tests.conftest import client_for, make_user
from tests.store_fixtures import use_store

ALL_FEATURES = [f["key"] for f in FEATURES]
DAY = dt.date(2025, 10, 15)


# --- G1: why a session stopped working -----------------------------------------------------------


def _with_password(db, user, password):
    user.password_hash = hash_password(password)
    db.commit()
    return user


def test_change_password_signs_out_the_other_devices_with_a_reason(db):
    user = _with_password(db, make_user(db, "ravi"), "old-password-1")
    here, there = client_for(db, user), client_for(db, user)
    r = here.post("/api/auth/change-password", json={"current_password": "old-password-1",
                                                     "new_password": "new-password-22"})
    assert r.status_code == 200, r.text
    assert here.get("/api/auth/me").status_code == 200
    r = there.get("/api/auth/me")
    assert r.status_code == 401
    body = r.json()
    assert body["error"] == "password_changed" and body["detail"] == body["message"]
    assert "password was changed" in body["message"]
    # Any route answers the same, not only /me.
    assert there.get("/api/team/rules").json().get("error") == "password_changed"


def test_admin_password_reset_tells_that_users_devices(db, admin_client):
    user = make_user(db, "sam")
    phone = client_for(db, user)
    assert phone.get("/api/auth/me").status_code == 200
    r = admin_client.post(f"/api/auth/users/{user.id}/set-password", json={"new_password": "a-brand-new-pass"})
    assert r.status_code == 200, r.text
    r = phone.get("/api/auth/me")
    assert r.status_code == 401 and r.json()["error"] == "password_changed"
    # The ended rows can't be used again, whatever their state.
    db.expire_all()
    rows = db.query(UserSession).filter(UserSession.user_id == user.id).all()
    assert rows and all(r.expires_at.year == 2000 for r in rows)


def test_expired_unknown_and_signed_out_sessions_say_session_expired(db):
    user = make_user(db, "lee")
    c = client_for(db, user)
    row = db.query(UserSession).filter(UserSession.user_id == user.id).one()
    row.expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)
    db.commit()
    r = c.get("/api/auth/me")
    assert r.status_code == 401
    assert r.json() == {"detail": "Session expired or invalid", "error": "session_expired",
                        "message": "Session expired or invalid"}
    c2 = client_for(db, user)
    assert c2.post("/api/auth/logout").status_code == 200
    assert c2.get("/api/auth/me").json()["error"] == "session_expired"
    from fastapi.testclient import TestClient

    from app.main import app

    anon = TestClient(app)
    anon.headers.update({"Authorization": "Bearer _.nope"})
    assert anon.get("/api/auth/me").json()["error"] == "session_expired"
    # No token at all keeps the old answer.
    assert TestClient(app).get("/api/auth/me").json() == {"detail": "Not logged in"}


# --- G6 / G9 / G20: the POS in /me and branding ---------------------------------------------------


def test_me_says_whether_the_pos_is_connected(db, monkeypatch):
    use_store(monkeypatch, "RETAILOPS_TEST_TOAST")
    me = client_for(db, make_user(db, "clerk")).get("/api/auth/me").json()
    assert me["pos_word"] == "Toast" and me["pos_connected"] is False  # no Toast keys in tests
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "toast_client_id", "id", raising=False)
    monkeypatch.setattr(s, "toast_client_secret", "secret", raising=False)
    monkeypatch.setattr(s, "toast_restaurant_guid", "guid", raising=False)
    assert client_for(db, make_user(db, "clerk2")).get("/api/auth/me").json()["pos_connected"] is True


def test_csv_store_has_nothing_to_connect_and_says_your_pos(db, monkeypatch, anon_client):
    use_store(monkeypatch, "DEMO")
    me = client_for(db, make_user(db, "boss3", is_admin=True)).get("/api/auth/me").json()
    assert me["pos_name"] == "CSV upload"  # unchanged for older builds
    assert me["pos_word"] == "your POS" and me["pos_connected"] is None and me["pos_provider"] == "csv"
    body = anon_client.get("/api/app/branding").json()
    assert body["pos_provider"] == "csv" and body["pos_word"] == "your POS" and body["pos_name"] == "CSV upload"


def test_clover_store_advertises_its_pages_only_once_connected(db, monkeypatch, anon_client):
    from app.pos import gating

    use_store(monkeypatch, "RETAILOPS_TEST_CLOVER")
    clerk = client_for(db, make_user(db, "clerk", features=["open_orders", "invoice_submission"]))
    me = clerk.get("/api/auth/me").json()
    assert me["pos_connected"] is False and me["capabilities"] == []
    assert me["allowed_features"] == ["invoice_submission"]
    assert anon_client.get("/api/app/branding").json()["capabilities"] == []
    assert anon_client.get("/api/app/branding").json()["pos_word"] == "Clover"
    conn = gating._connector("clover")
    monkeypatch.setattr(conn, "is_configured", lambda: True)
    me = clerk.get("/api/auth/me").json()
    assert me["pos_connected"] is True and "open_orders" in me["capabilities"]
    assert me["allowed_features"] == ["open_orders", "invoice_submission"]
    assert "open_orders" in anon_client.get("/api/app/branding").json()["capabilities"]


def test_toast_store_capabilities_are_unchanged(db, admin_client):
    from app.pos import gating

    assert gating.capability_values() == [c.value for c in gating.Capability if c in gating.store_capabilities()]


# --- G15 / G21: demo refusals carry their code -----------------------------------------------------


@pytest.fixture
def demo_client(db, monkeypatch):
    use_store(monkeypatch, "RETAILOPS_TEST_CLOVER")
    return client_for(db, make_user(db, "app_store_review", is_admin=True, features=ALL_FEATURES))


@pytest.mark.parametrize("method,path,body", [
    ("put", "/api/item-vendors/items/I_RICE", {"vendors": []}),
    ("post", "/api/settings/exports", None),
    ("post", "/api/auth/users", {"username": "x", "password": "long-enough-1"}),
])
def test_demo_refusals_have_the_demo_code(demo_client, method, path, body):
    kwargs = {"json": body} if body is not None else {}
    r = getattr(demo_client, method)(path, **kwargs)
    assert r.status_code == 403, r.text
    assert r.json()["error"] == "demo_read_only" and r.json()["detail"] == r.json()["message"]


def test_demo_po_contacts_refusal_has_the_code(demo_client):
    r = demo_client.get("/api/ordering/vendor-contacts")
    assert r.status_code == 403 and r.json()["error"] == "demo_read_only"
    assert "vendor contacts" in r.json()["detail"]


# --- G18 / G14 / G19: item vendors ------------------------------------------------------------------


def _seed(db):
    for item_id, name, barcode in (("I_RICE", "Basmati Rice", "0123"), ("CAT/42", "Slash Item", ""),
                                   ("a/b/c", "Deep Slash", "")):
        db.add(InventorySnapshot(snapshot_date=DAY, item_id=item_id, name=name, barcode=barcode,
                                 source_filename="api:2025-10-15T08:00:00Z"))
    db.commit()


@pytest.fixture
def clover(db, monkeypatch):
    use_store(monkeypatch, "RETAILOPS_TEST_CLOVER")
    _seed(db)
    return client_for(db, make_user(db, "owner", is_admin=True))


@pytest.mark.parametrize("item_id,url_id", [("CAT/42", "CAT%2F42"), ("CAT/42", "CAT/42"), ("a/b/c", "a/b/c")])
def test_item_ids_with_a_slash_can_be_edited(db, clover, item_id, url_id):
    r = clover.put(f"/api/item-vendors/items/{url_id}", json={"vendors": [{"name": "North Supply"}]})
    assert r.status_code == 200, r.text
    assert r.json()["item_id"] == item_id and r.json()["vendors"][0]["name"] == "North Supply"
    db.expire_all()
    assert [v.vendor_name for v in db.query(ItemVendor).filter_by(item_id=item_id)] == ["North Supply"]


def test_unknown_or_blank_item_ids_are_404(clover):
    assert clover.put("/api/item-vendors/items/NOPE/1", json={"vendors": []}).status_code == 404
    assert clover.put("/api/item-vendors/items/" + "x" * 201, json={"vendors": []}).status_code == 404


def test_item_vendors_page_reads_only_what_it_shows(db, clover):
    clover.put("/api/item-vendors/items/I_RICE", json={"vendors": [{"name": "Main Wholesale", "item_code": "R1"}]})
    # A vendor row of an item no longer in the catalog is not listed.
    db.add(ItemVendor(item_id="GONE", vendor_name="Old Vendor", rank=1, source="manual"))
    db.commit()
    body = clover.get("/api/item-vendors").json()
    assert body["catalog_total"] == 3 and body["missing_count"] == 2 and body["total"] == 3
    assert body["vendor_names"] == ["Main Wholesale"]
    assert body["pos"] == "Clover" and body["pos_word"] == "Clover"
    rice = next(i for i in body["items"] if i["item_id"] == "I_RICE")
    assert rice["barcode"] == "0123" and rice["vendors"][0]["item_code"] == "R1"
    assert clover.get("/api/item-vendors", params={"q": "r1"}).json()["total"] == 1
    page = clover.get("/api/item-vendors", params={"limit": 1, "offset": 1}).json()
    assert [i["name"] for i in page["items"]] == ["Deep Slash"] or len(page["items"]) == 1
    # catalog_rows holds plain rows, not whole snapshot objects.
    rows = iv.catalog_rows(db)
    assert set(rows) == {"I_RICE", "CAT/42", "a/b/c"} and not isinstance(rows["I_RICE"], InventorySnapshot)
    assert iv.catalog_item(db, "CAT/42").name == "Slash Item" and iv.catalog_item(db, "nope") is None


def test_csv_store_item_vendors_say_your_pos(db, monkeypatch):
    use_store(monkeypatch, "DEMO")
    _seed(db)
    owner = client_for(db, make_user(db, "owner", is_admin=True))
    body = owner.get("/api/item-vendors").json()
    assert body["pos"] == "CSV upload" and body["pos_word"] == "your POS"


def test_branding_body_keeps_the_m2_keys_first(anon_client):
    keys = list(json.loads(anon_client.get("/api/app/branding").content))
    # Additive keys after the M2 ones, in order: the POS facts (G9), then
    # label printing (docs/LABEL_PRINTING_FEATURE.md 1.3).
    assert keys[-3:] == ["pos_provider", "pos_word", "label_printing"]
