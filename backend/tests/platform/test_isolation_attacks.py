"""Cross-tenant isolation attack suite (docs/PLATFORM_CONTRACT.md 18.3).

Three stores provisioned on PostgreSQL (each its own database); store
BRAVO's text columns carry the canary ``CANARYB``. Store ALPHA's admin must
never see it, whatever the route, header, query or id.

Covered here: 1 canary crawl, 2 forged prefix, 3 header/query override,
4 IDOR, 5 scope crossing, 6 public routes, 7 file paths, 10 concurrency,
11 sessions, 12 provisioning (also test_provisioning.py), 13 credentials
(also test_credentials.py). Caches (8), jobs (9) and push (14) are track B's
tenancy tests.
"""

import concurrent.futures
import datetime as dt
import re

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import text

from tests.platform.conftest import needs_b, provision, store_login

pytestmark = needs_b

CANARY = "CANARYB"
STORES = {"ALPHA": ("ann", "alpha-owner-pass"), "BRAVO": ("bob", "bravo-owner-pass"), "CHARLIE": ("cat", "charlie-owner-pass")}


def _seed_canaries():
    """Canary text in BRAVO's own database and platform rows."""
    from app.auth import hash_password
    from app.models import User
    from app.platform import credentials
    from app.tenancy import tenant_session

    now = dt.datetime.now(dt.timezone.utc)
    with tenant_session("BRAVO") as db:
        for i in range(6):  # BRAVO has more user ids than ALPHA (IDOR probes)
            db.add(User(username=f"{CANARY.lower()}_user{i}", password_hash=hash_password("x" * 12), is_admin=False,
                        allowed_features="[]"))
        db.execute(text("UPDATE whatsapp_groups SET label = :l"), {"l": f"{CANARY} group"})
        db.execute(
            text("INSERT INTO checklist_public_links (checklist_type, slug, token, enabled, name_mode, allowed_names_json, "
                 "max_per_hour, created_at, updated_at) VALUES ('opening', 'bravo', '7391', true, 'free', '[]', 6, :n, :n)"),
            {"n": now},
        )
        db.commit()
    credentials.save_credential("BRAVO", "smtp", {"host": "smtp.canaryb.test", "username": f"{CANARY}@x.test",
                                                   "from_name": CANARY}, {"password": f"{CANARY}-secret-pw"}, actor="t")
    from app.platform.db import platform_session
    from app.platform.stores import put_settings

    with platform_session() as pdb:
        put_settings(pdb, "BRAVO", {"store_short_name": CANARY, "store_address": f"{CANARY} street"}, actor="t")


def _has_col(db, table, col) -> bool:
    return db.execute(
        text("SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c"), {"t": table, "c": col}
    ).first() is not None


@pytest.fixture
def three(client):
    for code, (user, pw) in STORES.items():
        provision(code, owner=user, password=pw)
    _seed_canaries()
    tokens = {code: store_login(client, code, u, p).json()["token"] for code, (u, p) in STORES.items()}
    return tokens


def _as(client, token):
    return {"Authorization": f"Bearer {token}"}


def test_1_canary_crawl_every_get_route(client, three):
    from app.main import app

    hdr = _as(client, three["ALPHA"])
    client = client.__class__(client.app, raise_server_exceptions=False)  # POS calls fail offline: fine
    leaks, checked = [], 0
    for route in app.routes:
        if not isinstance(route, APIRoute) or "GET" not in route.methods:
            continue
        if route.path.startswith(("/api/platform", "/c")):
            continue
        for ident in ("1", "2", "7"):
            url = re.sub(r"\{[^}]+\}", ident, route.path)
            r = client.get(url, headers=hdr, follow_redirects=False)
            checked += 1
            blob = r.text + " ".join(f"{k}:{v}" for k, v in r.headers.items())
            if CANARY.lower() in blob.lower():
                leaks.append((route.path, ident, r.status_code))
            if "{" not in route.path:
                break
    assert checked > 100
    assert leaks == []


def test_2_forged_prefix(client, three):
    a_secret = three["ALPHA"].split(".", 1)[1]
    assert client.get("/api/auth/me", headers=_as(client, f"BRAVO.{a_secret}")).status_code == 401
    assert client.get("/api/auth/me", headers=_as(client, "BRAVO.not-a-real-secret-at-all-000")).status_code == 401
    assert client.get("/api/auth/me", headers=_as(client, "PLATFORM." + a_secret)).status_code == 401


def test_3_header_and_query_override(client, three):
    hdr = _as(client, three["ALPHA"])
    r = client.get("/api/auth/me", headers={**hdr, "X-Store": "BRAVO"})
    assert r.status_code == 409 and r.json()["error"] == "store_mismatch"
    assert client.get("/api/auth/me?store=BRAVO", headers=hdr).status_code == 409
    me = client.get("/api/auth/me?store=ALPHA", headers=hdr)
    assert me.status_code == 200 and me.json()["store_code"] == "ALPHA"


def test_4_idor_on_ids_only_bravo_has(client, three):
    hdr = _as(client, three["ALPHA"])
    users = client.get("/api/auth/users", headers=hdr).json()
    assert [u["username"] for u in users] == ["ann"]
    for uid in range(2, 8):
        assert client.patch(f"/api/auth/users/{uid}/can-assign", json={"can_assign": True}, headers=hdr).status_code == 404
        assert client.delete(f"/api/auth/users/{uid}", headers=hdr).status_code == 404


def test_5_scope_crossing(client, three, master_client):
    assert client.get("/api/platform/stores", headers=_as(client, three["ALPHA"])).status_code == 403
    for url in ("/api/auth/users", "/api/settings/integrations", "/api/team/bootstrap"):
        assert master_client.get(url).status_code in (401, 403), url
    kinds = client.get("/api/settings/integrations", headers=_as(client, three["ALPHA"])).json()
    assert all(not k["configured"] for k in kinds)  # BRAVO's SMTP is not ALPHA's


def test_6_public_routes(client, three):
    anon = client.__class__(client.app)
    assert anon.get("/api/public/checklists/bravo-7391?store=ALPHA").status_code == 404
    look = anon.get("/api/public/stores/ALPHA").text
    assert CANARY not in look
    assert CANARY in anon.get("/api/public/stores/BRAVO").text  # control: the canary is really there
    assert anon.get("/c/ALPHA/bravo-7391").status_code == 404


def test_7_store_paths_refuse_escapes(three):
    from app.tenancy import store_path

    with pytest.raises(ValueError):
        store_path("..", "BRAVO", code="ALPHA")
    with pytest.raises(ValueError):
        store_path("/etc/passwd", code="ALPHA")
    assert str(store_path("team", code="ALPHA")).endswith("/ALPHA/team")


def test_10_concurrency_each_answer_from_its_own_store(client, three):
    jobs = [code for _ in range(100) for code in STORES]

    def call(code):
        r = client.get("/api/auth/me", headers=_as(client, three[code]))
        return code, r.status_code, r.json().get("store_code"), r.json().get("username")

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(call, jobs))
    assert len(results) == 300
    bad = [r for r in results if r[1] != 200 or r[2] != r[0] or r[3] != STORES[r[0]][0]]
    assert bad == []


def test_11_sessions(client, three, master_client):
    a, b = three["ALPHA"], three["BRAVO"]
    client.post("/api/auth/logout", headers=_as(client, a))
    assert client.get("/api/auth/me", headers=_as(client, a)).status_code == 401
    assert client.get("/api/auth/me", headers=_as(client, b)).status_code == 200
    master_client.post("/api/platform/stores/BRAVO/suspend", json={"reason": "test"})
    assert client.get("/api/auth/me", headers=_as(client, b)).status_code == 403
    assert client.get("/api/auth/me", headers=_as(client, three["CHARLIE"])).status_code == 200


def test_13_credentials_view_is_per_store(three):
    from app.platform.credentials import list_credentials, settings_overrides

    assert not any(v.configured for v in list_credentials("ALPHA"))
    assert settings_overrides("ALPHA") == {}
    from app.tenancy import store_settings_for

    assert store_settings_for("ALPHA").smtp_password != f"{CANARY}-secret-pw"
    assert store_settings_for("BRAVO").smtp_password == f"{CANARY}-secret-pw"
