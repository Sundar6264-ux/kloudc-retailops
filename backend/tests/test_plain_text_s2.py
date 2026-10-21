"""Pre-production track S2: words and help (docs/PREPROD_PLAN.md section 1).

G5 app locations, G11 tagged sections, G12 password help, G16/G25 plain
form errors, G17 store settings help, G22 plain words in store-facing help,
G23 "Submit invoice", G24 "Vendor code (SKU)", G27 sentences instead of bare
HTTP phrases, G29 no WhatsApp provider/plan/status codes."""

import glob
import json
import pathlib
import re

import pytest

from app import glossary as g
from app import help_content as hc
from app import plain_errors as pe

HELP = pathlib.Path(hc.DEFAULT_HELP_DIR)


def _load(name):
    return json.loads((HELP / name).read_text(encoding="utf-8"))


def _article(aid):
    return _load(f"articles/{aid}.json")


def _page(pid):
    return next(p for p in _load("pages.json") if p["id"] == pid)


def _text(value) -> str:
    return json.dumps(value, ensure_ascii=False)


# ------------------------------------------------------------------ G5


@pytest.mark.parametrize(
    "pid,where",
    [
        ("store_settings", "More › Setup › Store settings"),
        ("store_data", "More › Setup › Data & usage"),
        ("item_vendors", "More › Setup › Item vendors"),
        ("integrations", "More › Setup › Integrations"),
    ],
)
def test_new_setup_screens_say_where_they_are_in_the_app(pid, where):
    assert _page(pid)["app"] == {"location": where}


def test_every_app_location_starts_at_a_bottom_tab():
    starts = {"Home", "Store", "Scan", "Team", "More", "Sign in"}
    for p in _load("pages.json"):
        where = (p.get("app") or {}).get("location")
        if where:
            for place in where.split(" · "):
                assert place.split(" › ")[0] in starts, (p["id"], where)


# ------------------------------------------------------------------ G11

PAGE = {
    "id": "p",
    "sections": {
        "all": {"title": "For all", "summary": "x"},
        "toast_keys": {"pos": ["toast"], "title": "Toast keys", "summary": "Toast API client ID"},
        # A section whose KEY is "pos" (sign-up has one): not a tag.
        "pos": {"title": "Point of sale", "summary": "y"},
    },
}


@pytest.mark.parametrize("pos", ["clover", "csv"])
def test_a_tagged_section_is_left_out_for_another_pos(pos):
    assert list(hc.only_for_pos(PAGE, pos)["sections"]) == ["all", "pos"]


def test_a_tagged_section_stays_for_its_pos_and_is_labelled_for_masters():
    assert list(hc.only_for_pos(PAGE, "toast")["sections"]) == ["all", "toast_keys", "pos"]
    out = hc.only_for_pos(PAGE, None)["sections"]
    assert out["toast_keys"]["pos_label"] == "Toast only" and "pos_label" not in out["pos"]


# ------------------------------------------------------------------ G12, G17, G23, G24


def test_password_help_says_other_devices_are_signed_out():
    # routers/auth.py change_password ends every other session (SR-04).
    for text in (_text(_page("change_password")), _text(_article("account-password"))):
        assert "stay signed in." not in text.replace("This one stays signed in.", "")
        assert "Your other devices are signed out." in text


def test_change_password_really_signs_out_other_devices(db, staff):
    from app.auth import hash_password
    from tests.conftest import client_for

    staff.password_hash = hash_password("old pass word")
    db.commit()
    here, other = client_for(db, staff), client_for(db, staff)
    assert other.get("/api/auth/me").status_code == 200
    r = here.post("/api/auth/change-password",
                  json={"current_password": "old pass word", "new_password": "a brand new password"})
    assert r.status_code == 200
    assert other.get("/api/auth/me").status_code == 401
    assert here.get("/api/auth/me").status_code == 200


def test_store_settings_help_does_not_offer_the_time_zone():
    page = _page("store_settings")
    assert "time zone" not in page["info"]["summary"]
    art = _text(_article("setup-store-settings"))
    assert "**Time zone** decides" not in art
    assert "isn't changed here" in art


def test_invoice_help_names_the_submit_invoice_button():
    art = _text(_article("invoices"))
    assert "Select **Submit invoice**." in art and "Select **Submit**." not in art
    assert "**Submit invoice** is instant" in _text(_page("invoices"))


def test_glossary_calls_it_vendor_code():
    term = next(t for t in _load("glossary.json") if t["key"] == "concept_sku")
    assert term["label"] == "Vendor code (SKU)"
    assert "sku" in [a.lower() for a in term["aliases"]]  # search still finds "SKU"


# ------------------------------------------------------------------ G22

JARGON = re.compile(r"\b(sync\w*|re-sync\w*|server\w*|guid|snapshot\w*|sku\w*|database\w*|gmail|whapi|backend)\b", re.I)
# UI labels the screens still show, and the Toast field name people must find.
ALLOWED = (
    "Sales sync",
    "own server",
    "Toast calls it the Restaurant GUID",
    "Vendor code (SKU)",
)
SKIP_KEYS = {"id", "route", "article", "terms", "related", "pages", "key", "feature", "aliases", "symptom_id"}


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)
    elif isinstance(value, dict):
        for k, v in value.items():
            if k not in SKIP_KEYS:
                yield from _strings(v)


def _store_facing():
    for p in _load("pages.json"):
        if p.get("area") != "platform" and p.get("audience") != ["master"]:
            yield f"page {p['id']}", p
    for f in sorted(glob.glob(str(HELP / "articles" / "*.json"))):
        a = json.loads(pathlib.Path(f).read_text(encoding="utf-8"))
        if a.get("audience") != ["master"] and not a["id"].startswith("master-"):
            yield f"article {a['id']}", a
    for name in ("faq.json", "troubleshooting.json"):
        for x in _load(name):
            if x.get("audience") != ["master"] and x.get("area") != "platform":
                yield f"{name} {x['id']}", x
    for t in _load("glossary.json"):
        if t.get("category") != "concept_platform":
            yield f"term {t['key']}", t
    gs = _load("getting-started.json")
    yield "getting started (owner)", gs["owner"]
    yield "getting started (staff)", gs["staff"]


def test_store_facing_help_uses_plain_words():
    found = []
    for where, item in _store_facing():
        for s in _strings(item):
            for a in ALLOWED:
                s = s.replace(a, "")
            m = JARGON.search(s)
            if m:
                found.append(f"{where}: {m.group(0)!r} in {s[:90]!r}")
    assert not found, "\n".join(found)


# ------------------------------------------------------------------ G29

PROVIDER_WORDS = ("Whapi", "whapi", "402", "sandbox", "WhatsApp plan", "Gmail", "WHAPI_")


def test_glossary_and_help_never_name_the_whatsapp_provider_or_gmail(monkeypatch):
    import types

    settings = types.SimpleNamespace(store_timezone="America/New_York", toast_timezone="America/New_York",
                                     store_name="Corner Market", pos_provider="toast")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    monkeypatch.setattr("app.pos.gating.pos_provider", lambda: "toast")
    monkeypatch.setattr("app.pos.gating.pos_name", lambda: "Toast")
    glossary = g.get_glossary()
    shown = json.dumps(
        [{k: v for k, v in t.items() if k not in ("aliases", "keywords", "key")} for t in glossary["terms"]]
        if isinstance(glossary, dict) and "terms" in glossary else glossary,
        ensure_ascii=False,
    )
    # Aliases are search words ("check gmail" still finds the term).
    shown = re.sub(r'"aliases": \[[^\]]*\]', "", shown)
    help_text = json.dumps(hc.help_body(), ensure_ascii=False)
    help_text = re.sub(r'"order": \d+', "", help_text)  # an article's sort number
    for word in PROVIDER_WORDS:
        assert word not in shown, word
        assert word not in help_text, word


def test_po_whatsapp_reasons_are_plain():
    from app.services import po_send

    for text in (po_send.WHATSAPP_OFF_REASON, po_send.WHATSAPP_CHAT_LIMIT_MESSAGE):
        for word in PROVIDER_WORDS + ("plan",):
            assert word not in text, (word, text)
    assert "off for this store" in po_send.WHATSAPP_OFF_REASON


@pytest.mark.parametrize("code", [400, 401, 402, 403, 404, 408, 413, 415, 429, 500, 503, 418])
def test_whatsapp_send_errors_never_show_a_status_code_or_provider(code):
    from app.services import whapi_client

    exc = whapi_client._media_status_error(code)
    msg = str(exc)
    assert exc.status_code == code  # still there for the logs
    assert not re.search(r"\(\d{3}\)|\b\d{3}\b", msg), msg
    for word in PROVIDER_WORDS + ("plan",):
        assert word not in msg, msg
    assert msg.endswith((".", "?"))


def test_whatsapp_not_set_up_names_no_setting():
    from app.services import whapi_client

    with pytest.raises(whapi_client.WhapiNotConfigured) as e:
        whapi_client.send_whatsapp_message("hello", to="123@g.us")
    text = str(e.value)
    assert "WHAPI" not in text and ".env" not in text and "backend" not in text


def test_po_send_whatsapp_not_set_up_is_plain(monkeypatch):
    from app.services import po_send, whapi_client

    def boom(*a, **k):
        raise whapi_client.WhapiNotConfigured("WHAPI_API_TOKEN isn't set")

    monkeypatch.setattr(whapi_client, "upload_media", boom)
    monkeypatch.setattr(whapi_client, "send_media_message", boom)
    with pytest.raises(po_send._ChannelError) as e:
        po_send.send_whatsapp("123@g.us", "PO", b"%PDF", "po.pdf")
    assert "WHAPI" not in str(e.value) and "server" not in str(e.value)


# ------------------------------------------------------------------ G25 (unit)


def _err(t, loc=("body", "qty"), msg="raw", ctx=None, inp="secret typed value"):
    e = {"type": t, "loc": loc, "msg": msg, "input": inp}
    if ctx:
        e["ctx"] = ctx
    return e


@pytest.mark.parametrize(
    "err,sentence",
    [
        (_err("missing"), "This is required."),
        (_err("int_parsing"), "Must be a whole number."),
        (_err("float_parsing"), "Must be a number."),
        (_err("greater_than_equal", ctx={"ge": 0}), "Must be 0 or more."),
        (_err("less_than_equal", ctx={"le": 100.0}), "Must be 100 or less."),
        (_err("string_too_long", ctx={"max_length": 120}), "Use at most 120 characters."),
        (_err("string_too_short", ctx={"min_length": 8}), "Use at least 8 characters."),
        (_err("string_too_short", ctx={"min_length": 1}), "This can't be blank."),
        (_err("literal_error", ctx={"expected": "'a' or 'b'"}), "Choose one of: a or b."),
        (_err("bool_parsing"), "Must be yes or no."),
        (_err("date_from_datetime_parsing"), "Must be a date, like 2026-10-21."),
        (_err("value_error", msg="Value error, pick a vendor first"), "Pick a vendor first."),
        (_err("something_new"), "That isn't a valid value."),
    ],
)
def test_each_validator_error_reads_as_a_sentence(err, sentence):
    assert pe.plain_message(err) == sentence


def test_validation_body_keeps_the_old_shape_and_adds_a_message():
    body = pe.validation_body([
        _err("int_parsing", loc=("body", "qty")),
        _err("missing", loc=("body", "items", 0, "vendor_item_code")),
    ])
    assert body["error"] == "invalid_input"
    assert body["message"] == "Quantity: must be a whole number. (1 more to fix)"
    assert body["fields"] == {"qty": "Must be a whole number.", "vendor_item_code": "This is required."}
    assert [d["loc"] for d in body["detail"]] == [["body", "qty"], ["body", "items", 0, "vendor_item_code"]]
    assert all("input" not in d and "ctx" not in d for d in body["detail"])
    assert "secret typed value" not in json.dumps(body)
    assert "Input should" not in json.dumps(body)


def test_a_whole_form_error_has_no_field_name():
    body = pe.validation_body([_err("json_invalid", loc=("body", 12))])
    assert body["message"] == "That couldn't be read. Try again."


# ------------------------------------------------------------------ G25 + G27 (through the app)


def test_a_bad_form_answers_422_in_plain_words(staff_client):
    r = staff_client.post("/api/auth/change-password", json={"current_password": "x"})
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "invalid_input"
    assert body["message"] == "New password: this is required."
    assert isinstance(body["detail"], list) and body["detail"][0]["loc"][-1] == "new_password"
    assert body["detail"][0]["msg"] == "This is required."
    # The typed password is never echoed back.
    assert '"x"' not in r.text and "Field required" not in r.text


def test_an_unknown_route_keeps_not_found_for_older_apps_and_adds_a_sentence(staff_client):
    r = staff_client.get("/api/no-such-feature-here")
    assert r.status_code == 404
    body = r.json()
    # Older app builds read exactly "Not Found" as "this server doesn't have it yet".
    assert body["detail"] == "Not Found"
    assert body["error"] == "not_found" and body["message"].endswith(".") and "Not Found" not in body["message"]


def test_a_wrong_method_keeps_its_phrase_and_adds_a_sentence(anon_client):
    r = anon_client.delete("/api/health")
    assert r.status_code == 405
    assert r.json()["detail"] == "Method Not Allowed" and r.json()["error"] == "not_found"


def test_a_route_404_without_a_sentence_gets_one():
    body = pe.bare_phrase_body(404, None, route_matched=True)
    assert body["detail"] == body["message"] and body["detail"] != "Not Found"
    assert pe.bare_phrase_body(500, "Internal Server Error", route_matched=True)["detail"].startswith("Something went wrong")
    # A real sentence is left alone.
    assert pe.bare_phrase_body(404, "No such vendor", route_matched=True) is None
    assert pe.bare_phrase_body(418, None, route_matched=True) is None


def test_the_server_error_reply_is_plain():
    from app.ops import http

    src = pathlib.Path(http.__file__).read_text()
    assert "Something went wrong on our side. Try again in a minute." in src


# ------------------------------------------------------------------ G16


@pytest.mark.parametrize(
    "key,value,expect",
    [
        ("brand_color", "red", "Main colour: "),
        ("ai_enabled", "maybe", "AI features: must be yes or no."),
    ],
)
def test_store_setting_errors_name_the_field_in_plain_words(key, value, expect):
    from app.tenancy import validate_store_setting

    with pytest.raises(ValueError) as e:
        validate_store_setting(key, value)
    text = str(e.value)
    assert text.startswith(expect), text
    assert "Input should" not in text and key not in text and "Value error" not in text


def test_store_setting_link_error_is_plain():
    from app.platform.errors import PlatformError
    from app.platform.stores import _https_setting

    with pytest.raises(PlatformError) as e:
        _https_setting("logo_url", "http://example.test/logo.png")
    assert e.value.message == "Logo link: must be a web address starting with https://."


def test_platform_messages_have_no_milestone_names():
    from app.platform.errors import MESSAGES

    assert not any(re.search(r"\bM\d\b", m) for m in MESSAGES.values())


def test_store_facing_glossary_terms_use_plain_words(monkeypatch):
    monkeypatch.setattr("app.pos.gating.pos_provider", lambda: "toast")
    monkeypatch.setattr("app.pos.gating.pos_name", lambda: "Toast")
    found = []
    for t in g.get_glossary()["terms"]:
        if t.get("area") == "platform" or t.get("category") == "concept_platform":
            continue
        for k, v in t.items():
            if k in ("aliases", "key", "related", "pages") or not isinstance(v, str):
                continue
            for a in ALLOWED:
                v = v.replace(a, "")
            m = JARGON.search(v)
            if m and m.group(0).lower() not in ("sku",):
                found.append(f"{t['key']}.{k}: {m.group(0)!r}")
    assert not found, found


def test_store_route_errors_name_no_settings_files_or_vendors():
    """Error sentences in the store's routes never tell a store user about
    env settings, the server or the AI vendor's API (G22)."""
    bad = re.compile(r"ANTHROPIC_API_KEY|backend/\.env|Claude API error|on the server|missing on server|on this server")
    found = []
    for path in sorted((pathlib.Path(__file__).resolve().parents[1] / "app" / "routers").glob("*.py")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "detail=" in line and bad.search(line):
                found.append(f"{path.name}:{n}: {line.strip()[:100]}")
    assert not found, found


def test_an_unknown_route_under_the_website_mount_keeps_not_found(tmp_path):
    # With frontend/dist present (production), the StaticFiles mount at "/"
    # is the scope's endpoint for every unknown path; it is not a real route.
    from fastapi.staticfiles import StaticFiles

    from app.main import _route_matched

    assert _route_matched({"endpoint": StaticFiles(directory=str(tmp_path))}) is False
    assert _route_matched({}) is False
    assert _route_matched({"endpoint": lambda: None}) is True
