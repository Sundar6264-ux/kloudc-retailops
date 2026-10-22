"""""Build checklists" (feature `checklist_builder`, owner Oct 22: "give a
separate feature access on who can build checklists"). Per store: the
grant lives on that store's user, like every other feature.

A non-admin with the key can use every /api/checklists/admin/* route (the
Checklist builder, its public links and WhatsApp groups) and upload a picture
for a checklist item. It gives nothing else: every other route still answers
403, the Users page included, and it can't grant features to anyone. Without
the key a non-admin gets the same 403 as before. The App Review demo login is
refused even if ticked.
"""

import io
import json
import re

from PIL import Image

from fastapi.routing import APIRoute

from app.features import FEATURE_KEYS, FEATURES, get_allowed_features
from app.main import app
from app.models import ChecklistDefinition, ChecklistPublicLink, User, WhatsAppGroup
from app.routers import checklist_admin
from tests.conftest import client_for, make_user
from tests.test_review_route_gates import LOGIN_ONLY, PUBLIC

ADMIN = "/api/checklists/admin"
DEFS = f"{ADMIN}/definitions"
GROUPS = f"{ADMIN}/whatsapp-groups"
ST = "store_closing"
GROUP_A = "120363011111111111@g.us"


def _builder(db, name="builder1", extra=()):
    return client_for(db, make_user(db, name, features=["daily_checklists", "checklist_builder", *extra]))


def _routes():
    for route in app.routes:
        if isinstance(route, APIRoute):
            for method in sorted(route.methods):
                yield method, route.path


def _call(client, method, path):
    url = re.sub(r"\{[^}]+\}", "1", path)
    body = {} if method in ("POST", "PUT", "PATCH") else None
    return client.request(method, url, json=body, follow_redirects=False)


def test_feature_is_listed_with_a_plain_label():
    assert {"key": "checklist_builder", "label": "Build checklists"} in FEATURES
    assert "checklist_builder" in FEATURE_KEYS


def test_admins_have_it_implicitly_and_staff_only_when_ticked(db):
    assert "checklist_builder" in get_allowed_features(make_user(db, "boss2", is_admin=True))
    assert "checklist_builder" not in get_allowed_features(make_user(db, "chef", features=["daily_checklists"]))
    assert "checklist_builder" in get_allowed_features(make_user(db, "builder3", features=["checklist_builder"]))


def test_every_builder_route_uses_the_builder_gate():
    """No route of the builder router is left on require_admin (so a new one
    can't silently stay admin-only, or open to everyone)."""
    routes = [r for r in checklist_admin.router.routes if isinstance(r, APIRoute)]
    assert len(routes) >= 20
    for route in routes:
        deps = {d.call for d in route.dependant.dependencies}
        assert checklist_admin.require_checklist_builder in deps, route.path


def test_builder_can_use_every_builder_route(db):
    builder = _builder(db)
    wrong = []
    for method, path in _routes():
        if not path.startswith(ADMIN):
            continue
        status = _call(builder, method, path).status_code
        if status in (401, 403):
            wrong.append((method, path, status))
    assert wrong == []


def test_builder_full_flow(db):
    builder = _builder(db)
    assert builder.get(f"{ADMIN}/meta").status_code == 200
    assert {d["checklist_type"] for d in builder.get(DEFS).json()["definitions"]} >= {ST}

    # Create, edit, publish.
    r = builder.post(DEFS, json={"title": "Deli opening"})
    assert r.status_code == 200, r.text
    new_type = r.json()["checklist_type"]
    item = {"id": "i1", "type": "short_text", "title": "Lights on?", "required": True}
    draft = {"sections": [{"id": "s1", "title": "Open", "items": [item]}]}
    r = builder.put(f"{DEFS}/{new_type}", json=draft)
    assert r.status_code == 200, r.text
    r = builder.post(f"{DEFS}/{new_type}/publish", json={})
    assert r.status_code == 200, r.text
    assert builder.get(f"{DEFS}/{new_type}/versions/1").status_code == 200

    # Duplicate, reorder, turn off and on.
    r = builder.post(f"{DEFS}/{new_type}/duplicate", json={})
    assert r.status_code == 200, r.text
    order = [d["checklist_type"] for d in builder.get(DEFS).json()["definitions"]]
    r = builder.post(f"{DEFS}/order", json={"order": list(reversed(order))})
    assert r.status_code == 200, r.text
    assert builder.patch(f"{DEFS}/{new_type}", json={"active": False}).status_code == 200
    assert db.query(ChecklistDefinition).filter_by(checklist_type=new_type).one().active is False
    assert builder.patch(f"{DEFS}/{new_type}", json={"active": True}).status_code == 200

    # Draft discard and revert.
    assert builder.put(f"{DEFS}/{new_type}", json=draft).status_code == 200
    assert builder.delete(f"{DEFS}/{new_type}/draft").status_code == 200
    draft["sections"][0]["items"][0]["title"] = "Lights and fans on?"
    assert builder.put(f"{DEFS}/{new_type}", json=draft).status_code == 200
    assert builder.post(f"{DEFS}/{new_type}/publish", json={}).status_code == 200
    r = builder.post(f"{DEFS}/{new_type}/revert/1", json={})
    assert r.status_code == 200, r.text

    # Public link.
    assert builder.get(f"{ADMIN}/public-links").status_code == 200
    assert builder.put(f"{DEFS}/{ST}/public-link", json={}).status_code == 200
    assert builder.post(f"{DEFS}/{ST}/public-link/rotate").status_code == 200
    assert builder.get(f"{DEFS}/{ST}/public-link").json()["exists"] is True
    assert builder.delete(f"{DEFS}/{ST}/public-link").status_code == 200
    assert db.query(ChecklistPublicLink).count() == 0

    # WhatsApp groups.
    assert builder.get(GROUPS).status_code == 200
    r = builder.post(GROUPS, json={"chat_id": GROUP_A, "label": "Deli crew"})
    assert r.status_code == 200, r.text
    key = r.json()["key"]
    assert builder.patch(f"{GROUPS}/{key}", json={"label": "Deli team"}).status_code == 200
    assert builder.delete(f"{GROUPS}/{key}").status_code == 204
    assert db.query(WhatsAppGroup).filter_by(key=key).count() == 0


def test_without_the_key_a_non_admin_gets_the_same_403(db, anon_client):
    everything_else = client_for(
        db, make_user(db, "all_but", features=sorted(FEATURE_KEYS - {"checklist_builder"}))
    )
    for method, path in _routes():
        if not path.startswith(ADMIN):
            continue
        r = _call(everything_else, method, path)
        assert r.status_code == 403, (method, path)
        assert r.json()["detail"] == "Admin privileges required"
        assert _call(anon_client, method, path).status_code == 401, (method, path)
    assert everything_else.post(DEFS, json={"title": "X"}).status_code == 403


def test_demo_login_is_refused_even_if_ticked(db):
    demo = client_for(db, make_user(db, "app_store_review", features=sorted(FEATURE_KEYS)))
    for method, path in _routes():
        if path.startswith(ADMIN):
            assert _call(demo, method, path).status_code == 403, (method, path)


def test_the_key_gives_nothing_else(db):
    """A builder-only user is refused everywhere a user with no features is,
    except the builder routes and the picture upload the builder uses."""
    builder = client_for(db, make_user(db, "builder_only", features=["checklist_builder"]))
    nobody = client_for(db, make_user(db, "nobody"))
    extra_allowed = {("POST", "/api/team/media")}
    wrong = []
    for method, path in _routes():
        if (method, path) in PUBLIC or (method, path) in LOGIN_ONLY or path.startswith("/public/"):
            continue
        if path.startswith(ADMIN) or (method, path) in extra_allowed:
            continue
        if _call(nobody, method, path).status_code != 403:
            continue
        status = _call(builder, method, path).status_code
        if status != 403:
            wrong.append((method, path, status))
    assert wrong == []


def test_builder_is_not_admin_and_cant_grant_features(db):
    builder = _builder(db)
    me = builder.get("/api/auth/me").json()
    assert me["is_admin"] is False
    assert "checklist_builder" in me["allowed_features"]

    other = make_user(db, "cook2", features=["daily_checklists"])
    for target in (builder.user, other):
        r = builder.patch(f"/api/auth/users/{target.id}/features", json={"allowed_features": sorted(FEATURE_KEYS)})
        assert r.status_code == 403, r.status_code
    assert builder.get("/api/auth/users").status_code == 403
    assert builder.get("/api/auth/features").status_code == 403
    assert builder.post("/api/auth/users", json={"username": "x", "password": "longenough1"}).status_code == 403
    db.expire_all()
    assert json.loads(db.get(User, other.id).allowed_features) == ["daily_checklists"]
    assert json.loads(db.get(User, builder.user.id).allowed_features) == ["daily_checklists", "checklist_builder"]


def test_admin_can_tick_it_on_the_users_page(db, admin_client):
    target = make_user(db, "builder2", features=["daily_checklists"])
    features = [f["key"] for f in admin_client.get("/api/auth/features").json()["features"]]
    assert "checklist_builder" in features
    r = admin_client.patch(
        f"/api/auth/users/{target.id}/features", json={"allowed_features": ["daily_checklists", "checklist_builder"]}
    )
    assert r.status_code == 200, r.text
    staff = client_for(db, db.get(User, target.id))
    assert "checklist_builder" in staff.get("/api/auth/me").json()["allowed_features"]
    assert staff.get(DEFS).status_code == 200


def test_builder_can_upload_a_checklist_picture_and_see_it(db):
    """The builder's picture items upload through POST /api/team/media - a
    builder without Team may upload images (not voice notes or PDFs)."""
    builder = client_for(db, make_user(db, "builder_pics", features=["checklist_builder"]))
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 30, 30)).save(buf, format="JPEG")
    r = builder.post("/api/team/media", files={"file": ("p.jpg", buf.getvalue(), "image/jpeg")})
    assert r.status_code == 200, r.text
    assert builder.get(r.json()["url"]).status_code == 200
    r = builder.post("/api/team/media", files={"file": ("a.pdf", b"%PDF-1.4 x", "application/pdf")})
    assert r.status_code in (400, 415, 422), r.status_code
    nobody = client_for(db, make_user(db, "no_pics", features=["daily_checklists"]))
    r = nobody.post("/api/team/media", files={"file": ("p.jpg", buf.getvalue(), "image/jpeg")})
    assert r.status_code == 403
