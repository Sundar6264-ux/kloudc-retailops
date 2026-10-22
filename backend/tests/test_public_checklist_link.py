"""Public checklist link (docs/PUBLIC_CHECKLIST_LINK_CONTRACT.md): a logged-out
page + API for ONE checklist, configured by admins only.

Covers: the end-to-end happy path (page, form, submit, WhatsApp text through a
fake sender, history/detail/Overview, 9:30 PM alert, nightly task), parity with
an in-app submission, name rules, the identical 404 for every inactive link,
rate limits (incl. CF-Connecting-IP), size limits, photos (bound to the link and
uploader, never public afterwards), stale version 409, the admin gate, no data
leaks in public responses, and migration 0041 up/down (fresh, plus real
.backup copies when PUBLIC_LINK_MIGRATION_DB_COPIES is set).

Nothing here reaches WhatsApp: send_whatsapp_message is replaced by a list.
"""

import datetime as dt
import io
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import zoneinfo

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import get_settings
from app.main import app
from app.models import ChecklistPublicLink, ChecklistSubmission
from app.routers import checklist_public as public_router
from app.routers import checklists as checklists_router
from app.services import checklist_public as svc
from app.services import checklist_submission
from app.services.checklist_schemas import get_schema
from app.services.notification_triggers import closing_checklist_alerts
from app.team import auto_tasks
from app.team.models import TeamMedia
from tests.conftest import client_for, make_user

# Made-up rules in the shape these tests were written for: tests/fixtures/packs/suite.json.
pytestmark = pytest.mark.suite_pack

FSA = "kitchen_fsa"
NY = zoneinfo.ZoneInfo("America/New_York")
BACKEND = pathlib.Path(__file__).resolve().parents[1]
ADMIN_BASE = "/api/checklists/admin/definitions"
PUBLIC = "/api/public/checklists"


# --- Fixtures and helpers ---------------------------------------------------------------



@pytest.fixture(autouse=True)
def _trust_the_test_peer(monkeypatch):
    # The TestClient's peer is "testclient", not a proxy address: trust every
    # peer here so CF-Connecting-IP keys the limits as it does behind the
    # tunnel. The trusted-proxy rule itself: tests/test_client_ip_rule.py.
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")

@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    """Generous limits by default (one TestClient = one IP); the rate-limit
    tests set their own."""
    svc.limiter.reset()
    monkeypatch.setattr(svc, "IP_LIMIT", (10000, 600))
    monkeypatch.setattr(svc, "IP_PAGE_LIMIT", (10000, 600))
    monkeypatch.setattr(svc, "GLOBAL_LIMIT", (100000, 600))
    monkeypatch.setattr(svc, "FAILED_IP_LIMIT", (10000, 600))
    monkeypatch.setattr(svc, "FAILED_GLOBAL_LIMIT", (100000, 600))
    yield
    svc.limiter.reset()


@pytest.fixture
def sent(monkeypatch):
    """Fake WhatsApp: the kitchen/store groups are set, the sender records."""
    out = []
    monkeypatch.setattr(get_settings(), "whapi_kitchen_checklist_group_id", "sk-group@g.us")
    monkeypatch.setattr(get_settings(), "whapi_store_checklist_group_id", "st-group@g.us")
    monkeypatch.setattr(checklist_submission, "send_whatsapp_message", lambda text, to=None: out.append((to, text)))
    return out


@pytest.fixture
def dist(tmp_path, monkeypatch):
    d = tmp_path / "dist"
    d.mkdir()
    (d / "index.html").write_text("<!doctype html><title>Main Store</title><div id=root></div>")
    monkeypatch.setattr(get_settings(), "frontend_dist_dir", str(d))
    return d


@pytest.fixture
def anon():
    return TestClient(app)


@pytest.fixture
def checker(db):
    return make_user(db, "kitchenlead", features=["daily_checklists"])


@pytest.fixture
def checker_client(db, checker):
    return client_for(db, checker)


def good_answers(checklist_type=FSA):
    """All-clear answers for the FSA closing (first option everywhere)."""
    answers = {}
    for section in get_schema(checklist_type)["sections"]:
        for grid in section.get("grids", []):
            for row in grid["rows"]:
                answers[row["id"]] = grid["options"][0]["value"]
        for group in section.get("checkbox_groups", []):
            for item in group["items"]:
                answers[item["id"]] = "Done"
    answers["issues_text"] = "None"
    return answers


# The product suggests no names (a new list link starts empty), so the tests
# give the FSA link its two people explicitly.
NAMES = ["Dana", "Satinder"]


def enable(admin_client, checklist_type=FSA, **fields):
    body = {"enabled": True}
    if checklist_type == FSA and fields.get("name_mode", "list") == "list":
        body["allowed_names"] = list(NAMES)
    body.update(fields)
    r = admin_client.put(f"{ADMIN_BASE}/{checklist_type}/public-link", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def token_of(link):
    """The public API key: "{slug}-{code}" (the whole last path segment)."""
    return link["path"][len("/c/"):]


def code_of(link):
    return link["path"].rsplit("-", 1)[1]


def png_bytes(size=(16, 16)):
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


def make_photo_checklist(admin_client, title="Cooler photo", closing=False):
    r = admin_client.post(ADMIN_BASE, json={"title": title, "settings": {"closing": closing, "whatsapp_group": "store"}})
    ctype = r.json()["checklist_type"]
    admin_client.put(f"{ADMIN_BASE}/{ctype}", json={"sections": [{"id": "s1", "items": [
        {"id": "q_ok", "type": "multiple_choice", "title": "Cooler closed?", "required": True,
         "options": [{"value": "Yes"}, {"value": "No", "issue_tag": "issue"}]},
        {"id": "q_photo", "type": "file_upload", "title": "Photo", "required": True,
         "file": {"max_files": 2, "types": ["image"]}},
    ]}]})
    assert admin_client.post(f"{ADMIN_BASE}/{ctype}/publish", json={}).status_code == 200
    return ctype


# --- Admin ------------------------------------------------------------------------------


def test_admin_get_suggests_the_service_link_but_creates_nothing(db, admin_client):
    r = admin_client.get(f"{ADMIN_BASE}/{FSA}/public-link")
    assert r.status_code == 200
    body = r.json()
    assert body["exists"] is False and body["enabled"] is False and body["url"] is None
    assert body["slug"] == "kitchen" and body["name_mode"] == "list"
    # No staff names are built into the product: a new link starts with an
    # empty name list that the store's admin fills in.
    assert body["allowed_names"] == [] and body["max_per_hour"] == 6
    assert db.query(ChecklistPublicLink).count() == 0
    # Creating without "enabled" leaves it off.
    r = admin_client.put(f"{ADMIN_BASE}/{FSA}/public-link", json={})
    assert r.status_code == 200 and r.json()["exists"] is True and r.json()["enabled"] is False
    link = r.json()
    assert link["url"] == f"{get_settings().public_base_url.rstrip('/')}{link['path']}"
    assert link["path"].startswith("/c/kitchen-") and len(code_of(link)) == svc.CODE_LENGTH
    assert code_of(link).isdigit() and link["code"] == code_of(link) and link["short"] is True


def test_admin_validation(db, admin_client):
    bad = [
        ({"slug": "Kitchen Service"}, "lowercase"),
        ({"slug": "a"}, "lowercase"),
        ({"slug": "a" * 21}, "lowercase"),
        ({"slug": "kitchen--service"}, "lowercase"),
        ({"slug": "-kitchen"}, "lowercase"),
        ({"max_per_hour": 0}, "hourly limit"),
        ({"max_per_hour": 61}, "hourly limit"),
        ({"max_per_hour": True}, "hourly limit"),
        ({"name_mode": "anyone"}, "name_mode"),
        ({"allowed_names": ["<b>Boss</b>"]}, "Not a valid name"),
        ({"allowed_names": "Dana"}, "must be a list"),
        ({"enabled": "yes"}, "true or false"),
        ({"enabled": True, "allowed_names": []}, "at least one name"),
        ({"token": "x" * 22}, "Unknown field"),
    ]
    for payload, words in bad:
        r = admin_client.put(f"{ADMIN_BASE}/{FSA}/public-link", json=payload)
        assert r.status_code == 422, payload
        assert words in r.json()["detail"], (payload, r.json())
    assert db.query(ChecklistPublicLink).count() == 0
    # Free text with no names is fine; names are cleaned and de-duplicated.
    r = admin_client.put(f"{ADMIN_BASE}/{FSA}/public-link",
                         json={"enabled": True, "name_mode": "free_text", "allowed_names": [" Dana ", "dana", "Satinder"]})
    assert r.status_code == 200 and r.json()["allowed_names"] == ["Dana", "Satinder"]
    # Another checklist can't take the same slug.
    r = admin_client.put(f"{ADMIN_BASE}/store_closing/public-link", json={"slug": "kitchen"})
    assert r.status_code == 422 and "already uses" in r.json()["detail"]
    assert admin_client.put(f"{ADMIN_BASE}/nope/public-link", json={}).status_code == 404


def test_every_admin_route_is_admin_only(db, admin_client, anon):
    from app.features import FEATURE_KEYS

    staff = client_for(db, make_user(db, "allfeatures", features=sorted(FEATURE_KEYS)))
    calls = [
        ("GET", "/api/checklists/admin/public-links"),
        ("GET", f"{ADMIN_BASE}/{FSA}/public-link"),
        ("PUT", f"{ADMIN_BASE}/{FSA}/public-link"),
        ("POST", f"{ADMIN_BASE}/{FSA}/public-link/rotate"),
        ("DELETE", f"{ADMIN_BASE}/{FSA}/public-link"),
    ]
    routes = {(m, r.path) for r in app.routes if hasattr(r, "methods") for m in r.methods}
    assert {(m, p) for m, p in routes if "public-link" in p} == {
        ("GET", "/api/checklists/admin/public-links"),
        ("GET", f"{ADMIN_BASE}/{{checklist_type}}/public-link"),
        ("PUT", f"{ADMIN_BASE}/{{checklist_type}}/public-link"),
        ("POST", f"{ADMIN_BASE}/{{checklist_type}}/public-link/rotate"),
        ("DELETE", f"{ADMIN_BASE}/{{checklist_type}}/public-link"),
    }
    for method, url in calls:
        body = {"enabled": False} if method == "PUT" else None
        assert anon.request(method, url, json=body).status_code == 401, url
        assert staff.request(method, url, json=body).status_code == 403, url
    assert db.query(ChecklistPublicLink).count() == 0
    assert admin_client.put(f"{ADMIN_BASE}/{FSA}/public-link", json={}).status_code == 200
    assert admin_client.post(f"{ADMIN_BASE}/{FSA}/public-link/rotate").status_code == 200
    listing = admin_client.get("/api/checklists/admin/public-links").json()["links"]
    assert {r["checklist_type"]: r["exists"] for r in listing}[FSA] is True
    assert admin_client.delete(f"{ADMIN_BASE}/{FSA}/public-link").json()["exists"] is False
    assert db.query(ChecklistPublicLink).count() == 0


# --- Happy path -------------------------------------------------------------------------


def test_end_to_end_like_an_in_app_submission(db, admin_client, checker_client, anon, sent, dist):
    link = enable(admin_client)
    token = token_of(link)

    # The page: the website's index.html with locked-down headers.
    page = anon.get(link["path"])
    assert page.status_code == 200 and "<div id=root>" in page.text
    assert page.headers["referrer-policy"] == "no-referrer"
    assert page.headers["cache-control"] == "no-store"
    assert "noindex" in page.headers["x-robots-tag"]
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]

    form = anon.get(f"{PUBLIC}/{token}")
    assert form.status_code == 200
    body = form.json()
    # "store" (additive, Store ID privacy 2026-10): whose page this is, only
    # behind a working link (the public branding no longer names the store).
    assert set(body) == {"checklist_type", "title", "description", "schema", "name_mode", "allowed_names", "store"}
    assert set(body["store"]) == {"name", "short_name", "logo_url"} and body["store"]["name"]
    assert body["checklist_type"] == FSA and body["name_mode"] == "list"
    assert body["allowed_names"] == ["Dana", "Satinder"]
    assert body["schema"]["version"] == get_schema(FSA)["version"]
    assert body["schema"]["sections"] == get_schema(FSA)["sections"]
    assert body["schema"]["settings"] == {"all_as_usual": True}

    answers = good_answers()
    answers["g0_0"] = "No"  # flags "Not Turned Off"
    r = anon.post(f"{PUBLIC}/{token}/submit", json={"name": " dana ", "answers": answers,
                                                     "definition_version": body["schema"]["version"]})
    assert r.status_code == 200, r.text
    result = r.json()
    assert set(result) == {"ok", "all_clear", "issue_summary"}
    assert result["ok"] is True and result["all_clear"] is False
    assert "Stoves of the Hot Food Stand" in result["issue_summary"]

    entry = db.query(ChecklistSubmission).one()
    assert entry.submitted_by_user_id == 0 and entry.submitted_by_name == "Dana"
    assert entry.via_public_link is True and entry.public_name == "Dana"
    assert entry.whatsapp_sent is True and entry.whatsapp_error is None
    assert len(sent) == 1
    to, text = sent[0]
    assert to == "sk-group@g.us"
    assert text.split("\n")[1].startswith("Dana (public link) · ")
    assert "*1 issue*\n\n*Turn off Heat*\n❌ Stoves of the Hot Food Stand" in text
    db.refresh(db.query(ChecklistPublicLink).one())
    assert db.query(ChecklistPublicLink).one().last_used_at is not None
    assert admin_client.get(f"{ADMIN_BASE}/{FSA}/public-link").json()["last_used_at"] is not None

    # History / detail / Overview: admins and staff with Daily Checklists.
    for client in (admin_client, checker_client):
        items = client.get("/api/checklists", params={"checklist_type": FSA}).json()["items"]
        assert [i["id"] for i in items] == [entry.id]
        row = items[0]
        assert row["via_public_link"] is True and row["public_name"] == "Dana"
        assert row["submitted_by_name"] == "Dana" and row["submitted_by_user_id"] == 0
        detail = client.get(f"/api/checklists/{entry.id}").json()
        assert detail["via_public_link"] is True and detail["answers"] == answers
        today = dt.datetime.now(NY).date().isoformat()
        tiles = {c["checklist_type"]: c for c in client.get(f"/api/checklists/overview?date={today}").json()["checklists"]}
        assert tiles[FSA]["submitted_on_date"] is True and tiles[FSA]["latest"]["submitted_by_name"] == "Dana"
    # Someone else's in-app submission is still private to them (and admins).
    other = make_user(db, "asha", features=["daily_checklists"])
    db.add(ChecklistSubmission(checklist_type="store_closing", submitted_by_user_id=other.id,
                               submitted_by_name="asha", answers_json="{}", all_clear=True, answers_hash="h1",
                               created_at=dt.datetime.now(dt.timezone.utc)))
    db.commit()
    ids = [i["submitted_by_name"] for i in checker_client.get("/api/checklists").json()["items"]]
    assert ids == ["Dana"]
    # Without Daily Checklists nobody sees anything (route gate).
    assert client_for(db, make_user(db, "nofeat", features=["team_chat"])).get("/api/checklists").status_code == 403

    # 9:30 PM alert and the nightly task count it as submitted.
    now = dt.datetime.now(NY)
    alerts = closing_checklist_alerts(db, now)
    assert len(alerts) == 1 and "Chef" in alerts[0].body
    assert "Food Service Associate" not in alerts[0].body
    assert auto_tasks._submitted_checklists(db, now.date())[FSA] == "Dana"


def test_parity_with_an_in_app_submission(db, admin_client, anon, sent, monkeypatch):
    """Same answers, once in the app and once through the link: the stored
    rows match except who sent it, and the WhatsApp texts match except the
    submitter line (and the time)."""
    link = enable(admin_client)
    staff = client_for(db, make_user(db, "asha", features=["daily_checklists"]))
    queued = []
    monkeypatch.setattr(checklists_router, "send_checklist_notification", lambda sid: queued.append(sid))
    monkeypatch.setattr(public_router, "send_checklist_notification", lambda sid: queued.append(sid))
    for answers in (good_answers(), dict(good_answers(), g0_3="No", issues_text="Fryer oil low")):
        queued.clear()
        version = get_schema(FSA)["version"]
        a = staff.post(f"/api/checklists/{FSA}/submit", json={"answers": answers, "definition_version": version})
        b = anon.post(f"{PUBLIC}/{token_of(link)}/submit",
                      json={"name": "Satinder", "answers": answers, "definition_version": version})
        assert a.status_code == 200 and b.status_code == 200, (a.text, b.text)
        assert len(queued) == 2  # both queued the same WhatsApp step
        app_row = db.get(ChecklistSubmission, a.json()["id"])
        pub_row = db.query(ChecklistSubmission).filter(ChecklistSubmission.via_public_link.is_(True)).order_by(
            ChecklistSubmission.id.desc()).first()
        skip = {"id", "submitted_by_user_id", "submitted_by_name", "via_public_link", "public_name", "answers_hash",
                "created_at"}
        cols = [c.name for c in ChecklistSubmission.__table__.columns if c.name not in skip]
        assert {c: getattr(app_row, c) for c in cols} == {c: getattr(pub_row, c) for c in cols}
        assert b.json() == {"ok": True, "all_clear": app_row.all_clear, "issue_summary": app_row.issue_summary}
        texts = []
        for row in (app_row, pub_row):
            sent.clear()
            checklist_submission._attempt_whatsapp(db, row)
            texts.append(sent[0])
        (to_a, text_a), (to_b, text_b) = texts
        assert to_a == to_b == "sk-group@g.us"

        def strip(text):
            lines = text.split("\n")
            return lines[:1] + lines[2:]  # line 2 is "<name> · <time>"

        assert strip(text_a) == strip(text_b)
        assert text_a.split("\n")[1].startswith("asha · ")
        assert text_b.split("\n")[1].startswith("Satinder (public link) · ")
        # The API row for both has the same shape (public adds only the two fields' values).
        out_a = checklist_submission.submission_out(app_row, db)
        out_b = checklist_submission.submission_out(pub_row, db)
        assert set(out_a) == set(out_b)
        for key in ("answers", "details", "all_clear", "issue_summary", "definition_version", "checklist_title"):
            assert out_a[key] == out_b[key]


def test_a_replayed_submit_is_stored_and_sent_once(db, admin_client, anon, sent):
    token = token_of(enable(admin_client))
    payload = {"name": "Dana", "answers": good_answers()}
    assert anon.post(f"{PUBLIC}/{token}/submit", json=payload).status_code == 200
    assert anon.post(f"{PUBLIC}/{token}/submit", json=payload).status_code == 200
    assert db.query(ChecklistSubmission).count() == 1 and len(sent) == 1
    # A different person with the same answers is a new submission.
    assert anon.post(f"{PUBLIC}/{token}/submit", json=dict(payload, name="Satinder")).status_code == 200
    assert db.query(ChecklistSubmission).count() == 2 and len(sent) == 2


# --- Names --------------------------------------------------------------------------------


def test_name_rules(db, admin_client, anon, sent):
    token = token_of(enable(admin_client))
    url = f"{PUBLIC}/{token}/submit"
    for name, words in [(None, "Pick your name"), ("", "Pick your name"), ("Bob", "from the list"), (5, "Pick")]:
        r = anon.post(url, json={"name": name, "answers": good_answers()})
        assert r.status_code == 400 and words in r.json()["detail"], (name, r.json())
    assert db.query(ChecklistSubmission).count() == 0
    assert anon.post(url, json={"name": "SATINDER", "answers": good_answers()}).status_code == 200
    assert db.query(ChecklistSubmission).one().submitted_by_name == "Satinder"

    enable(admin_client, name_mode="free_text")
    bad = ["A", "x" * 61, "<script>alert(1)</script>", "Ana *bold*", "_Ana_", "Ana`", "Robert'); DROP", "12",
           "Ana‮X", "a.", "   "]
    for name in bad:
        r = anon.post(url, json={"name": name, "answers": dict(good_answers(), issues_text=f"n {len(name)}")})
        assert r.status_code == 400, name
    assert db.query(ChecklistSubmission).count() == 1
    for i, (name, stored) in enumerate([("José O'Neil-Smith", "José O'Neil-Smith"), ("  Mary   Ann\n", "Mary Ann"),
                                        ("रमेश कुमार", "रमेश कुमार"), ("Dr. K. Rao", "Dr. K. Rao")]):
        r = anon.post(url, json={"name": name, "answers": dict(good_answers(), issues_text=f"ok {i}")})
        assert r.status_code == 200, (name, r.text)
        assert db.query(ChecklistSubmission).order_by(ChecklistSubmission.id.desc()).first().submitted_by_name == stored
    assert all(text.split("\n")[1].split(" · ")[0].endswith(" (public link)") for _, text in sent)


def test_display_name_cleans_a_bad_stored_name():
    assert svc.display_name("Ana *x*\n_y_ `z`") == "Ana x y z"
    assert svc.display_name("") == "Unknown"


# --- Inactive links: one identical 404 -----------------------------------------------


def _responses(anon, token, path, answers):
    out = [
        anon.get(f"{PUBLIC}/{token}"),
        anon.post(f"{PUBLIC}/{token}/submit", json={"name": "Dana", "answers": answers}),
        anon.post(f"{PUBLIC}/{token}/media", files={"file": ("a.png", png_bytes(), "image/png")}),
        anon.get(f"{PUBLIC}/{token}/images/abcdefghijklmnopqrstuvwx"),
    ]
    page = anon.get(path)
    return [(r.status_code, r.json()) for r in out], (page.status_code, page.text)


def test_unknown_disabled_rotated_deleted_all_look_the_same(db, admin_client, anon, sent, dist):
    link = enable(admin_client)
    token, slug = token_of(link), link["path"][3:].rsplit("-", 1)[0]
    expected_api = [(404, {"detail": "This link is not active."})] * 4
    seen_pages = set()
    answers = good_answers()

    def check(tok, path):
        api, page = _responses(anon, tok, path, answers)
        assert api == expected_api, (tok, api)
        assert page[0] == 404 and "This link is not active" in page[1]
        seen_pages.add(page)

    code = code_of(link)
    other = "5" + code[1:] if code[0] != "5" else "6" + code[1:]
    check("A" * 22, f"/c/{slug}-{'A' * 22}")          # unknown token
    check(f"{slug}-{other}", f"/c/{slug}-{other}")      # wrong code
    check("short", f"/c/{slug}-short")                  # malformed token
    check(token + "x", f"/c/{token}x")
    check(code, f"/c/{code}")                           # the code alone
    check(f"other-slug-{code}", f"/c/other-slug-{code}")  # right code, wrong slug
    assert anon.get("/c").status_code == 404

    admin_client.put(f"{ADMIN_BASE}/{FSA}/public-link", json={"enabled": False})   # disabled
    check(token, link["path"])
    admin_client.put(f"{ADMIN_BASE}/{FSA}/public-link", json={"enabled": True})
    assert anon.get(f"{PUBLIC}/{token}").status_code == 200
    rotated = admin_client.post(f"{ADMIN_BASE}/{FSA}/public-link/rotate").json()   # rotated
    assert token_of(rotated) != token and rotated["rotated_at"]
    check(token, link["path"])
    assert anon.get(f"{PUBLIC}/{token_of(rotated)}").status_code == 200
    assert anon.get(rotated["path"]).status_code == 200
    admin_client.patch(f"{ADMIN_BASE}/{FSA}", json={"active": False})   # checklist turned off
    check(token_of(rotated), rotated["path"])
    admin_client.patch(f"{ADMIN_BASE}/{FSA}", json={"active": True})
    admin_client.delete(f"{ADMIN_BASE}/{FSA}/public-link")   # deleted
    check(token_of(rotated), rotated["path"])
    assert len(seen_pages) == 1
    assert db.query(ChecklistSubmission).count() == 0 and sent == []


# --- Rate limits ----------------------------------------------------------------------


def test_per_ip_limit_uses_cf_connecting_ip(db, admin_client, anon, monkeypatch):
    token = token_of(enable(admin_client))
    monkeypatch.setattr(svc, "IP_LIMIT", (3, 600))
    a = {"CF-Connecting-IP": "203.0.113.7"}
    for _ in range(3):
        assert anon.get(f"{PUBLIC}/{token}", headers=a).status_code == 200
    r = anon.get(f"{PUBLIC}/{token}", headers=a)
    assert r.status_code == 429 and "Too many tries" in r.json()["detail"]
    # Unknown tokens count too (no free guessing).
    assert anon.get(f"{PUBLIC}/{'B' * 22}", headers=a).status_code == 429
    # Another visitor behind Cloudflare is not affected; nor is the direct peer.
    assert anon.get(f"{PUBLIC}/{token}", headers={"CF-Connecting-IP": "2001:db8::1"}).status_code == 200
    assert anon.get(f"{PUBLIC}/{token}").status_code == 200
    # A junk header falls back to the peer address.
    assert svc.client_ip({"cf-connecting-ip": "not an ip"}, "10.0.0.5") == "10.0.0.5"
    assert svc.client_ip({"cf-connecting-ip": " 198.51.100.4 "}, "127.0.0.1") == "198.51.100.4"


def test_global_limit_caps_forged_ips(db, admin_client, anon, monkeypatch):
    token = token_of(enable(admin_client))
    monkeypatch.setattr(svc, "GLOBAL_LIMIT", (5, 600))
    codes = [anon.get(f"{PUBLIC}/{token}", headers={"CF-Connecting-IP": f"198.51.100.{i}"}).status_code
             for i in range(8)]
    assert codes == [200] * 5 + [429] * 3


def test_page_limit(db, admin_client, anon, dist, monkeypatch):
    link = enable(admin_client)
    monkeypatch.setattr(svc, "IP_PAGE_LIMIT", (2, 600))
    assert [anon.get(link["path"]).status_code for _ in range(3)] == [200, 200, 429]


def test_hourly_submission_limit_per_link(db, admin_client, anon, sent):
    token = token_of(enable(admin_client, max_per_hour=2))
    url = f"{PUBLIC}/{token}/submit"
    for i, name in enumerate(["Dana", "Satinder"]):
        h = {"CF-Connecting-IP": f"203.0.113.{i}"}
        assert anon.post(url, json={"name": name, "answers": good_answers()}, headers=h).status_code == 200
    r = anon.post(url, json={"name": "Dana", "answers": dict(good_answers(), issues_text="x")},
                  headers={"CF-Connecting-IP": "203.0.113.9"})
    assert r.status_code == 429 and "limit for this hour" in r.json()["detail"]
    assert db.query(ChecklistSubmission).count() == 2
    # In-app submissions don't count against the link.
    assert svc.submissions_last_hour(db, db.query(ChecklistPublicLink).one()) == 2


# --- Size limits and photos --------------------------------------------------------------


def test_body_size_and_shape_limits(db, admin_client, anon):
    token = token_of(enable(admin_client))
    url = f"{PUBLIC}/{token}/submit"
    huge = {"name": "Dana", "answers": {"issues_text": "x" * (svc.MAX_SUBMIT_BYTES + 10)}}
    assert anon.post(url, json=huge).status_code == 413
    for body in ({"name": "Dana", "answers": {"a": "x" * (svc.MAX_ANSWER_VALUE + 1)}},
                 {"name": "Dana", "answers": {"a": 5}},
                 {"name": "Dana", "answers": ["x"]},
                 {"name": "Dana", "answers": {}, "definition_version": "2"},
                 {"name": "Dana", "answers": {}, "media_keys": ["k"]}):
        assert anon.post(url, json=body).status_code == 422, body
    r = anon.post(url, content=b"not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert db.query(ChecklistSubmission).count() == 0
    # The FSA closing has no photo question: no uploads at all.
    r = anon.post(f"{PUBLIC}/{token}/media", files={"file": ("a.png", png_bytes(), "image/png")})
    assert r.status_code == 400 and db.query(TeamMedia).count() == 0


def test_photos_are_bound_to_the_link_and_never_public(db, admin_client, anon, checker, checker_client, sent, monkeypatch):
    ctype = make_photo_checklist(admin_client)
    link = enable(admin_client, ctype, name_mode="free_text")
    token = token_of(link)
    media_url = f"{PUBLIC}/{token}/media"

    # Size / type limits are team media's.
    r = anon.post(media_url, files={"file": ("a.txt", b"hello", "text/plain")})
    assert r.status_code == 415
    r = anon.post(media_url, files={"file": ("a.png", b"not really a png", "image/png")})
    assert r.status_code == 415
    r = anon.post(media_url, content=b"x", headers={"Content-Type": "multipart/form-data; boundary=x",
                                                   "Content-Length": str(20 * 1024 * 1024)})
    assert r.status_code == 413
    r = anon.post(media_url, files={"other": ("a.png", png_bytes(), "image/png")})
    assert r.status_code == 422

    up = anon.post(media_url, files={"file": ("cooler.png", png_bytes(), "image/png")})
    assert up.status_code == 200, up.text
    photo = up.json()
    assert set(photo) == {"id", "key", "width", "height"}  # no URL, nothing to view
    row = db.get(TeamMedia, photo["id"])
    assert row.uploaded_by_user_id is None
    assert (row.owner_type, row.owner_id) == ("checklist_public_upload", db.query(ChecklistPublicLink).one().id)
    # Not readable by anyone but admins while pending.
    assert anon.get(f"/api/team/media/{photo['key']}").status_code == 401
    assert checker_client.get(f"/api/team/media/{photo['key']}").status_code == 404
    assert anon.get(f"{PUBLIC}/{token}/images/{photo['key']}").status_code == 404

    answers = {"q_ok": "Yes", "q_photo": json.dumps([photo["id"]])}
    submit = f"{PUBLIC}/{token}/submit"
    # Without / with a wrong key: refused (someone else's upload on the same link).
    assert anon.post(submit, json={"name": "Ravi", "answers": answers}).status_code == 400
    r = anon.post(submit, json={"name": "Ravi", "answers": answers, "media_keys": {str(photo["id"]): "x" * 32}})
    assert r.status_code == 400 and r.json()["detail"] == "Unknown photo - upload it again."
    # A staff user's private upload can't be grafted on through the link.
    own = checker_client.post("/api/checklists/media", files={"file": ("p.png", png_bytes(), "image/png")}).json()
    r = anon.post(submit, json={"name": "Ravi", "answers": {"q_ok": "Yes", "q_photo": json.dumps([own["id"]])},
                                "media_keys": {str(own["id"]): own["token"]}})
    assert r.status_code == 400
    # With its key: attached to the submission.
    r = anon.post(submit, json={"name": "Ravi", "answers": answers, "media_keys": {str(photo["id"]): photo["key"]}})
    assert r.status_code == 200, r.text
    entry = db.query(ChecklistSubmission).one()
    db.refresh(row)
    assert (row.owner_type, row.owner_id) == ("checklist", entry.id)
    # Used once only.
    r = anon.post(submit, json={"name": "Mina", "answers": answers, "media_keys": {str(photo["id"]): photo["key"]}})
    assert r.status_code == 400
    # Afterwards: still not public; logged-in users who can see the submission can.
    assert anon.get(f"/api/team/media/{photo['key']}").status_code == 401
    assert anon.get(f"{PUBLIC}/{token}/images/{photo['key']}").status_code == 404
    assert admin_client.get(f"/api/team/media/{photo['key']}").status_code == 200
    assert checker_client.get(f"/api/team/media/{photo['key']}").status_code == 200
    outsider = client_for(db, make_user(db, "outsider", features=["team_chat"]))
    assert outsider.get(f"/api/team/media/{photo['key']}").status_code == 404
    detail = admin_client.get(f"/api/checklists/{entry.id}").json()
    assert str(photo["id"]) in detail["media"]
    # Unused public uploads are pruned like other unattached uploads.
    stale = anon.post(media_url, files={"file": ("old.png", png_bytes(), "image/png")}).json()
    old = db.get(TeamMedia, stale["id"])
    old.created_at = old.created_at - dt.timedelta(days=2)
    db.commit()
    from app.team.media import prune_unattached_media

    assert prune_unattached_media(db) == 1 and db.get(TeamMedia, stale["id"]) is None


def test_photo_upload_rate_per_link(db, admin_client, anon, monkeypatch):
    ctype = make_photo_checklist(admin_client)
    token = token_of(enable(admin_client, ctype, name_mode="free_text"))
    monkeypatch.setattr(svc, "MEDIA_PER_LINK_HOUR", 2)
    codes = [anon.post(f"{PUBLIC}/{token}/media", files={"file": ("a.png", png_bytes(), "image/png")},
                       headers={"CF-Connecting-IP": f"203.0.113.{i}"}).status_code for i in range(3)]
    assert codes == [200, 200, 429]


def test_form_images_only_for_that_checklist(db, admin_client, anon):
    upload = admin_client.post("/api/checklists/media", files={"file": ("sign.png", png_bytes(), "image/png")}).json()
    other = admin_client.post("/api/checklists/media", files={"file": ("x.png", png_bytes(), "image/png")}).json()
    r = admin_client.post(ADMIN_BASE, json={"title": "Sign check", "sections": [{"id": "s1", "items": [
        {"id": "img", "type": "image", "title": "The open sign", "image_url": upload["url"]},
        {"id": "q", "type": "multiple_choice", "title": "Sign off?", "required": True, "options": [{"value": "Yes"}]},
    ]}]})
    ctype = r.json()["checklist_type"]
    assert admin_client.post(f"{ADMIN_BASE}/{ctype}/publish", json={}).status_code == 200
    token = token_of(enable(admin_client, ctype, name_mode="free_text"))
    form = anon.get(f"{PUBLIC}/{token}").json()
    items = [i for s in form["schema"]["sections"] for i in s.get("items", [])]
    image_url = next(i["image_url"] for i in items if i["type"] == "image")
    assert image_url == f"{PUBLIC}/{token}/images/{upload['token']}"
    r = anon.get(image_url)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert anon.get(f"{PUBLIC}/{token}/images/{other['token']}").status_code == 404
    # The FSA link can't reach the sign checklist's picture.
    fsa = token_of(enable(admin_client))
    assert anon.get(f"{PUBLIC}/{fsa}/images/{upload['token']}").status_code == 404


# --- Versions, errors, leaks ------------------------------------------------------------


def test_stale_version_is_409(db, admin_client, anon, sent):
    token = token_of(enable(admin_client))
    old_version = anon.get(f"{PUBLIC}/{token}").json()["schema"]["version"]
    detail = admin_client.get(f"{ADMIN_BASE}/{FSA}").json()
    sections = detail["editable"]["sections"]
    sections[0]["items"].append({"id": "q_new", "type": "multiple_choice", "title": "Mopped?", "required": True,
                                 "options": [{"value": "Yes"}]})
    assert admin_client.post(f"{ADMIN_BASE}/{FSA}/publish", json={"sections": sections}).status_code == 200
    r = anon.post(f"{PUBLIC}/{token}/submit",
                  json={"name": "Dana", "answers": good_answers(), "definition_version": old_version})
    assert r.status_code == 409 and "changed since you opened it" in r.json()["detail"]
    new = anon.get(f"{PUBLIC}/{token}").json()["schema"]["version"]
    assert new == old_version + 1
    r = anon.post(f"{PUBLIC}/{token}/submit",
                  json={"name": "Dana", "answers": dict(good_answers(), q_new="Yes"), "definition_version": new})
    assert r.status_code == 200, r.text


def test_errors_never_show_internals(db, admin_client, anon, monkeypatch):
    token = token_of(enable(admin_client))

    def boom(*a, **k):
        raise RuntimeError("secret internals /Users/someone")

    monkeypatch.setattr(svc, "public_view", boom)
    r = anon.get(f"{PUBLIC}/{token}")
    assert r.status_code == 500 and r.json() == {"detail": "Something went wrong. Try again."}
    monkeypatch.setattr(public_router, "create_submission", boom)
    r = anon.post(f"{PUBLIC}/{token}/submit", json={"name": "Dana", "answers": good_answers()})
    assert r.status_code == 500 and "secret" not in r.text and "Traceback" not in r.text


def test_public_responses_leak_nothing_else(db, admin_client, anon, sent):
    other_link = enable(admin_client, "store_closing", name_mode="free_text", slug="store-closing")
    link = enable(admin_client)
    token = token_of(link)
    db.add(ChecklistSubmission(checklist_type=FSA, submitted_by_user_id=1, submitted_by_name="secretchef",
                               answers_json='{"x": "private note"}', all_clear=True, answers_hash="hx",
                               created_at=dt.datetime.now(dt.timezone.utc)))
    db.commit()
    texts = [anon.get(f"{PUBLIC}/{token}").text,
             anon.post(f"{PUBLIC}/{token}/submit", json={"name": "Dana", "answers": good_answers()}).text,
             anon.post(f"{PUBLIC}/{token}/submit", json={"name": "Nobody", "answers": {}}).text,
             anon.post(f"{PUBLIC}/{token}/submit", json={"name": "Dana", "answers": {}}).text]
    for text in texts:
        for secret in ("secretchef", "private note", "boss", f"store-closing-{code_of(other_link)}", "whatsapp", "sk-group",
                       "submitted_by", "\"closing\"", "created_at"):
            assert secret not in text, (secret, text[:200])
    assert set(json.loads(texts[1])) == {"ok", "all_clear", "issue_summary"}
    assert all(set(json.loads(t)) == {"detail"} for t in texts[2:])
    # The form is only this checklist's.
    form = json.loads(texts[0])
    assert form["checklist_type"] == FSA and "store_closing" not in texts[0]
    # Every public JSON response is no-store / no-referrer.
    r = anon.get(f"{PUBLIC}/{token}")
    assert r.headers["cache-control"] == "no-store" and r.headers["referrer-policy"] == "no-referrer"
    # Nothing else answers without a login.
    for url in ("/api/checklists", f"/api/checklists/schema/{FSA}", "/api/checklists/overview",
                f"{ADMIN_BASE}/{FSA}/public-link", "/api/team/bootstrap"):
        assert anon.get(url).status_code == 401, url


# --- Migration 0041 -------------------------------------------------------------------


def _alembic(db_file, *args):
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_file}")
    alembic = pathlib.Path(sys.executable).parent / "alembic"
    return subprocess.run([str(alembic), *args], cwd=BACKEND, env=env, check=True, capture_output=True, text=True)


def _cols(con, table):
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def _check_up(db_file, submissions_before):
    con = sqlite3.connect(db_file)
    try:
        assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0041"
        assert {"via_public_link", "public_name"} <= _cols(con, "checklist_submissions")
        from app.db import Base

        assert _cols(con, "checklist_public_links") == {c.name for c in Base.metadata.tables["checklist_public_links"].columns}
        assert _cols(con, "checklist_submissions") == {c.name for c in Base.metadata.tables["checklist_submissions"].columns}
        assert con.execute("SELECT count(*) FROM checklist_public_links").fetchone()[0] == 0  # nothing seeded
        rows = con.execute("SELECT id, answers_json, via_public_link FROM checklist_submissions ORDER BY id").fetchall()
        assert [(r[0], r[1]) for r in rows] == submissions_before
        assert all(r[2] is None for r in rows)
    finally:
        con.close()


def _check_down(db_file, submissions_before):
    con = sqlite3.connect(db_file)
    try:
        assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0040"
        assert "checklist_public_links" not in {r[0] for r in con.execute("SELECT name FROM sqlite_master")}
        assert not {"via_public_link", "public_name"} & _cols(con, "checklist_submissions")
        rows = con.execute("SELECT id, answers_json FROM checklist_submissions ORDER BY id").fetchall()
        assert rows == submissions_before
    finally:
        con.close()


def _up_down_up(db_file):
    con = sqlite3.connect(db_file)
    before = con.execute("SELECT id, answers_json FROM checklist_submissions ORDER BY id").fetchall()
    con.close()
    _alembic(db_file, "upgrade", "0041")
    _check_up(db_file, before)
    _alembic(db_file, "downgrade", "0040")
    _check_down(db_file, before)
    _alembic(db_file, "upgrade", "0041")
    _check_up(db_file, before)


def test_migration_0041_up_and_down_on_a_fresh_database(tmp_path):
    db_file = tmp_path / "fresh.db"
    _alembic(db_file, "upgrade", "0040")
    con = sqlite3.connect(db_file)
    con.execute(
        "INSERT INTO checklist_submissions (checklist_type, submitted_by_user_id, submitted_by_name, answers_json, "
        "all_clear, answers_hash, whatsapp_sent, whatsapp_ambiguous, created_at, definition_version) VALUES "
        "('kitchen_fsa', 1, 'asha', '{\"a\": \"b\"}', 1, 'h', 0, 0, '2026-09-29 01:00:00', 2)"
    )
    con.commit()
    con.close()
    _up_down_up(db_file)


def _db_copies():
    raw = os.environ.get("PUBLIC_LINK_MIGRATION_DB_COPIES", "")
    return [p for p in raw.split(os.pathsep) if p]


@pytest.mark.skipif(not _db_copies(), reason="set PUBLIC_LINK_MIGRATION_DB_COPIES to .backup copies of dev / prod")
@pytest.mark.parametrize("source", _db_copies() or ["-"])
def test_migration_0041_up_and_down_on_real_db_copies(tmp_path, source):
    """PUBLIC_LINK_MIGRATION_DB_COPIES=/tmp/dev-copy.db:/tmp/prod-copy.db (made
    with sqlite3 "file:...?mode=ro" ".backup ..."), at revision 0040. Copied
    again into tmp_path, so the given files are never modified."""
    db_file = tmp_path / "copy.db"
    shutil.copyfile(source, db_file)
    con = sqlite3.connect(db_file)
    assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0040"
    con.close()
    _up_down_up(db_file)


# --- Verification pass (Oct 1): attacker findings, fixed ------------------------------------


def test_one_ip_over_its_limit_does_not_use_up_the_global_cap(db, admin_client, anon, monkeypatch):
    """One client hammering the link (no forged header needed) used to count
    every blocked request toward the global cap and lock everyone out."""
    token = token_of(enable(admin_client))
    monkeypatch.setattr(svc, "IP_LIMIT", (3, 600))
    monkeypatch.setattr(svc, "GLOBAL_LIMIT", (10, 600))
    attacker = {"CF-Connecting-IP": "203.0.113.66"}
    codes = [anon.get(f"{PUBLIC}/{'Z' * 22}", headers=attacker).status_code for _ in range(30)]
    assert codes == [404] * 3 + [429] * 27
    assert anon.get(f"{PUBLIC}/{token}", headers={"CF-Connecting-IP": "203.0.113.67"}).status_code == 200


def test_page_paths_trailing_slash_and_nested(db, admin_client, anon, dist):
    link = enable(admin_client)
    assert anon.get(link["path"] + "/").status_code == 200  # some chat apps add a slash
    for path in ("/c/", "/c/a/b", link["path"] + "/index.html", link["path"] + "//", "/c/" + "a" * 5000):
        r = anon.get(path)
        assert r.status_code == 404 and "This link is not active" in r.text, path
        assert r.headers["content-type"].startswith("text/html") and r.headers["cache-control"] == "no-store"
    # The website itself is still served by the static mount. The mount is
    # made at import time only when frontend/dist exists (a checkout or CI
    # job that has not built the website has none).
    if any(getattr(route, "name", None) == "frontend" for route in app.routes):
        assert anon.get("/").status_code == 200


def test_malformed_bodies_are_client_errors_not_500(db, admin_client, anon):
    token = token_of(enable(admin_client))
    nested = b'{"name": "Dana", "answers": ' + b"[" * 100000 + b"]" * 100000 + b"}"
    r = anon.post(f"{PUBLIC}/{token}/submit", content=nested, headers={"Content-Type": "application/json"})
    assert r.status_code in (400, 413), r.text
    ctype = make_photo_checklist(admin_client)
    ptoken = token_of(enable(admin_client, ctype, name_mode="free_text"))
    files = [("file", ("a.png", png_bytes(), "image/png")), ("file", ("b.png", png_bytes(), "image/png"))]
    r = anon.post(f"{PUBLIC}/{ptoken}/media", files=files)
    assert r.status_code == 400 and r.json() == {"detail": "Send one photo as 'file'."}
    r = anon.post(f"{PUBLIC}/{ptoken}/media", data={f"f{i}": "x" for i in range(10)},
                  files={"file": ("a.png", png_bytes(), "image/png")})
    assert r.status_code == 400
    assert db.query(TeamMedia).count() == 0


# --- Short codes (owner, Oct 1: "something easily memorizable") ------------------------


def test_code_format_and_trivial_codes(db):
    for code in ("0000", "1111", "1234", "4321", "6789", "1212", "4545"):
        assert svc.is_trivial_code(code), code
    for code in ("4821", "1235", "9071", "1122"):
        assert not svc.is_trivial_code(code), code
    for _ in range(300):
        code = svc.new_code(db)
        assert len(code) == 4 and code.isdigit() and code[0] != "0" and not svc.is_trivial_code(code)
    assert svc.is_short_code("4821") and not svc.is_short_code("A" * 22) and not svc.is_short_code(None)


def test_codes_are_unique_and_rotate_changes_them(db, admin_client, monkeypatch):
    a = enable(admin_client)
    # The next code the generator draws is a's code: it's skipped.
    draws = iter([int(code_of(a)), 4821, 5930])
    monkeypatch.setattr(svc.secrets, "randbelow", lambda n: next(draws))
    b = enable(admin_client, "store_closing", name_mode="free_text")
    assert code_of(b) == ("4821" if code_of(a) != "4821" else "5930")
    assert b["slug"] == "store" and b["path"] == f"/c/store-{code_of(b)}"
    monkeypatch.undo()
    old = code_of(a)
    rotated = admin_client.post(f"{ADMIN_BASE}/{FSA}/public-link/rotate").json()
    assert code_of(rotated) != old and code_of(rotated) != code_of(b) and rotated["short"] is True
    assert rotated["slug"] == "kitchen"


def test_default_slug_is_one_word_and_avoids_a_taken_one(db, admin_client):
    assert enable(admin_client)["slug"] == "kitchen"
    chef = admin_client.put(f"{ADMIN_BASE}/kitchen_closing/public-link", json={}).json()
    assert chef["slug"] == "chef"
    # A builder checklist: its title's first word, or the next free word.
    r = admin_client.post(ADMIN_BASE, json={"title": "Chef Fridge Temps", "sections": [{"id": "s1", "items": [
        {"id": "q", "type": "multiple_choice", "title": "OK?", "required": True, "options": [{"value": "Yes"}]}]}]})
    ctype = r.json()["checklist_type"]
    fridge = admin_client.put(f"{ADMIN_BASE}/{ctype}/public-link", json={}).json()
    assert fridge["slug"] == "temps"
    r = admin_client.put(f"{ADMIN_BASE}/kitchen_closing/public-link", json={"slug": "ab"})
    assert r.status_code == 200 and r.json()["slug"] == "ab" and r.json()["path"].startswith("/c/ab-")


def test_slug_and_code_must_both_match(db, admin_client, anon, dist):
    link = enable(admin_client)
    key, code = token_of(link), code_of(link)
    assert anon.get(f"{PUBLIC}/{key}").status_code == 200
    assert anon.get(link["path"]).status_code == 200
    for bad in (code, f"store-{code}", f"kitchenx-{code}", f"kitchen-{code}0"):
        assert anon.get(f"{PUBLIC}/{bad}").status_code == 404, bad
        assert anon.get(f"/c/{bad}").status_code == 404, bad
    # A renamed word moves the link: the old word stops working.
    renamed = admin_client.put(f"{ADMIN_BASE}/{FSA}/public-link", json={"slug": "prep"}).json()
    assert renamed["path"] == f"/c/prep-{code}"
    assert anon.get(f"{PUBLIC}/{key}").status_code == 404
    assert anon.get(f"{PUBLIC}/prep-{code}").status_code == 200


def test_short_link_word_is_case_insensitive(db, admin_client, anon, dist):
    """Typed from memory on a phone: /c/Kitchen-4821 works like /c/kitchen-4821."""
    link = enable(admin_client)
    key = token_of(link)
    for variant in (key.capitalize(), key.upper()):
        assert anon.get(f"{PUBLIC}/{variant}").status_code == 200, variant
        assert anon.get(f"/c/{variant}").status_code == 200, variant


def test_old_long_token_stays_case_sensitive(db, admin_client, anon, dist):
    enable(admin_client)
    row = db.query(ChecklistPublicLink).one()
    old = "Ab" + svc.new_token()[2:]
    row.slug, row.token = "kitchen-service", old
    db.commit()
    assert anon.get(f"{PUBLIC}/kitchen-service-{old}").status_code == 200
    assert anon.get(f"{PUBLIC}/kitchen-service-{old.lower()}").status_code == 404


def test_old_long_token_links_keep_working_until_rotated(db, admin_client, anon, sent, dist):
    """Links made before the short codes (prod: kitchen-service + 22 chars)."""
    enable(admin_client)
    row = db.query(ChecklistPublicLink).one()
    old = svc.new_token()
    row.slug, row.token = "kitchen-service", old
    db.commit()
    link = admin_client.get(f"{ADMIN_BASE}/{FSA}/public-link").json()
    assert link["path"] == f"/c/kitchen-service-{old}" and link["short"] is False and link["code"] is None
    assert anon.get(link["path"]).status_code == 200
    # The new page sends "{slug}-{token}"; the first page version sent the bare token.
    for key in (f"kitchen-service-{old}", old):
        assert anon.get(f"{PUBLIC}/{key}").status_code == 200, key
    r = anon.post(f"{PUBLIC}/{old}/submit", json={"name": "Dana", "answers": good_answers()})
    assert r.status_code == 200 and r.json()["ok"] is True
    # Wrong slug with the old token: no.
    assert anon.get(f"{PUBLIC}/store-{old}").status_code == 404
    rotated = admin_client.post(f"{ADMIN_BASE}/{FSA}/public-link/rotate").json()
    assert rotated["short"] is True and rotated["path"] == f"/c/kitchen-service-{rotated['code']}"
    for key in (f"kitchen-service-{old}", old):
        assert anon.get(f"{PUBLIC}/{key}").status_code == 404
    assert anon.get(link["path"]).status_code == 404
    assert anon.get(f"{PUBLIC}/{token_of(rotated)}").status_code == 200


def test_failed_lookups_per_ip_are_limited(db, admin_client, anon, dist, monkeypatch):
    link = enable(admin_client)
    key, code = token_of(link), code_of(link)
    monkeypatch.setattr(svc, "FAILED_IP_LIMIT", (10, 600))
    a = {"CF-Connecting-IP": "203.0.113.50"}
    wrong = [c for c in (f"{n:04d}" for n in range(1000, 1100)) if c != code]
    codes = [anon.get(f"{PUBLIC}/kitchen-{c}", headers=a).status_code for c in wrong[:12]]
    assert codes == [404] * 10 + [429] * 2
    # Now even the right link is refused for that IP (no guessing through the
    # submit / media / page routes either) ...
    r = anon.get(f"{PUBLIC}/{key}", headers=a)
    assert r.status_code == 429 and "Too many tries" in r.json()["detail"]
    assert anon.post(f"{PUBLIC}/{key}/submit", json={"name": "Dana", "answers": {}}, headers=a).status_code == 429
    assert anon.get(link["path"], headers=a).status_code == 429
    # ... while another IP is fine.
    assert anon.get(f"{PUBLIC}/{key}", headers={"CF-Connecting-IP": "203.0.113.51"}).status_code == 200
    # Page misses count too.
    b = {"CF-Connecting-IP": "203.0.113.52"}
    assert [anon.get(f"/c/kitchen-{c}", headers=b).status_code for c in wrong[:11]] == [404] * 10 + [429]
    # Successful lookups don't count.
    c = {"CF-Connecting-IP": "203.0.113.53"}
    assert all(anon.get(f"{PUBLIC}/{key}", headers=c).status_code == 200 for _ in range(15))


def test_failed_lookups_global_cap(db, admin_client, anon, monkeypatch):
    key = token_of(enable(admin_client))
    monkeypatch.setattr(svc, "FAILED_GLOBAL_LIMIT", (5, 600))
    codes = [anon.get(f"{PUBLIC}/kitchen-0{i}00", headers={"CF-Connecting-IP": f"198.51.100.{i}"}).status_code
             for i in range(1, 8)]
    assert codes == [404] * 5 + [429] * 2
    assert anon.get(f"{PUBLIC}/{key}", headers={"CF-Connecting-IP": "198.51.100.99"}).status_code == 429


def test_failed_lookups_ipv6_counted_per_64(db, admin_client, anon, monkeypatch):
    """Review (Oct 1): one IPv6 client holds a whole /64, so a fresh address
    per guess must not dodge FAILED_IP_LIMIT - the /64 is one identity."""
    key = token_of(enable(admin_client))
    monkeypatch.setattr(svc, "FAILED_IP_LIMIT", (10, 600))
    codes = [
        anon.get(f"{PUBLIC}/kitchen-{2000 + i}", headers={"CF-Connecting-IP": f"2001:db8:1:2::{i + 1:x}"}).status_code
        for i in range(12)
    ]
    assert codes == [404] * 10 + [429] * 2
    # Same /64, a new address: still blocked, even for the right link.
    assert anon.get(f"{PUBLIC}/{key}", headers={"CF-Connecting-IP": "2001:db8:1:2:ffff::1"}).status_code == 429
    # Another /64 is a different visitor.
    assert anon.get(f"{PUBLIC}/{key}", headers={"CF-Connecting-IP": "2001:db8:1:3::1"}).status_code == 200
    assert svc.client_ip({"cf-connecting-ip": "2001:db8:1:2:aaaa:bbbb:cccc:dddd"}, None) == "2001:db8:1:2::/64"
    assert svc.client_ip({"cf-connecting-ip": "::ffff:198.51.100.4"}, None) == "198.51.100.4"
    assert svc.client_ip({}, "203.0.113.9") == "203.0.113.9"
