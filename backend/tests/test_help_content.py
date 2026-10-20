"""Help content (docs/DESIGN_SYSTEM.md 4 and 5.2): content/help/*.json is valid
against schema.json, every cross-reference resolves (pages, guides, glossary
terms, inline links), the writing rules hold, and GET /api/help,
GET /api/public/help and the merged /api/glossary serve it."""

import gzip
import json
import re

import pytest

from app import glossary as g
from app import help_content as hc

DASHES = re.compile("[–—]")
PLACEHOLDER = re.compile(r"\{[a-z_]+\}")
LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
BOLD = re.compile(r"\*\*([^*]+)\*\*")


@pytest.fixture(scope="module")
def raw():
    hc.reload()
    return hc.load_raw()


@pytest.fixture(scope="module")
def glossary_keys():
    return {t["key"] for t in g.get_glossary()["terms"]}


def _plain(text):
    return BOLD.sub(r"\1", LINK.sub(r"\1", text))


def _strings(value, path="$"):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _strings(v, f"{path}[{i}]")
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(v, f"{path}.{k}")


# ---------------------------------------------------------------- files


def test_content_matches_schema(raw):
    errors = hc.schema_errors(raw)
    assert errors == [], "\n".join(errors[:20])


def test_schema_check_catches_mistakes(raw):
    """The small JSON Schema subset really checks (a broken file must fail)."""
    bad = json.loads(json.dumps(raw))
    bad["pages"][0]["area"] = "kitchen"
    del bad["pages"][1]["title"]
    bad["faq"][0]["a"] = []
    bad["glossary"][0]["key"] = "no_prefix"
    errors = hc.schema_errors(bad)
    joined = "\n".join(errors)
    assert "pages[0].area" in joined
    assert "pages[1]: missing title" in joined
    assert "faq[0].a: fewer than 1" in joined
    assert "glossary[0].key" in joined


def test_ids_are_unique(raw):
    for name, field in (("pages", "id"), ("articles", "id"), ("glossary", "key")):
        ids = [x[field] for x in raw[name]]
        assert len(ids) == len(set(ids)), f"duplicate {name} ids"
    qa = [x["id"] for x in raw["faq"]] + [x["id"] for x in raw["troubleshooting"]]
    assert len(qa) == len(set(qa)), "duplicate faq / troubleshooting ids"


def test_every_reference_resolves(raw, glossary_keys):
    pages = {p["id"] for p in raw["pages"]}
    articles = {a["id"] for a in raw["articles"]}
    problems = []
    for p in raw["pages"]:
        if p["article"] and p["article"] not in articles:
            problems.append(f"page {p['id']}: article {p['article']}")
        problems += [f"page {p['id']}: term {k}" for k in p["terms"] if k not in glossary_keys]
    for a in raw["articles"]:
        problems += [f"article {a['id']}: page {x}" for x in a["pages"] if x not in pages]
        problems += [f"article {a['id']}: related {x}" for x in a["related"] if x not in articles]
    for track, t in raw["getting_started"].items():
        for s in t["steps"]:
            if s["page"] and s["page"] not in pages:
                problems.append(f"start {track}: page {s['page']}")
            if s["article"] and s["article"] not in articles:
                problems.append(f"start {track}: article {s['article']}")
    for x in raw["faq"] + raw["troubleshooting"]:
        problems += [f"{x['id']}: page {pid}" for pid in x["pages"] if pid not in pages]
    cats = {c["key"] for c in raw["glossary_categories"]}
    for t in raw["glossary"]:
        if t["category"] not in cats:
            problems.append(f"term {t['key']}: category {t['category']}")
        problems += [f"term {t['key']}: page {pid}" for pid in t["pages"] if pid not in pages]
        problems += [f"term {t['key']}: related {k}" for k in t["related"] if k not in glossary_keys]
    for path, text in _strings({k: v for k, v in raw.items() if k != "manifest"}):
        for _label, target in LINK.findall(text):
            kind, _, ident = target.partition(":")
            if kind == "page" and ident not in pages:
                problems.append(f"{path}: link to page {ident}")
            elif kind == "article" and ident not in articles:
                problems.append(f"{path}: link to article {ident}")
            elif kind == "term" and ident not in glossary_keys:
                problems.append(f"{path}: link to term {ident}")
            elif kind not in ("page", "article", "term", "https", "mailto"):
                problems.append(f"{path}: bad link {target}")
    assert problems == [], "\n".join(problems[:30])


def test_writing_rules(raw):
    problems = []
    for path, text in _strings(raw):
        if DASHES.search(text):
            problems.append(f"{path}: en/em dash")
        for ph in PLACEHOLDER.findall(text):
            if ph not in hc.ALLOWED_PLACEHOLDERS:
                problems.append(f"{path}: placeholder {ph}")
    for p in raw["pages"]:
        for where, info in [("info", p["info"])] + [(f"section {k}", s) for k, s in p["sections"].items()]:
            if len(_plain(info["summary"])) > 160:
                problems.append(f"page {p['id']} {where}: summary over 160")
            if len(info.get("points", [])) > 5:
                problems.append(f"page {p['id']} {where}: more than 5 points")
            problems += [f"page {p['id']} {where}: long point" for pt in info.get("points", []) if len(_plain(pt)) > 120]
    assert problems == [], "\n".join(problems[:30])


def test_every_area_and_audience_has_help(raw):
    groups = {a["group"] for a in raw["articles"]}
    assert {"basics", "home", "ordering", "stock", "sales", "team", "setup", "account", "master"} <= groups
    assert set(raw["getting_started"]) == {"owner", "staff", "master"}
    assert len(raw["faq"]) >= 30 and len(raw["troubleshooting"]) >= 18
    assert len(raw["glossary"]) >= 90


def test_every_page_has_info_and_a_guide(raw):
    for p in raw["pages"]:
        assert p["info"]["summary"], p["id"]
        assert p["article"], f"page {p['id']} has no guide"


def test_contact_is_kloudc(raw):
    c = raw["contact"]
    assert c["email"] == "support@kloudc.com"
    assert c["privacy_url"] == "https://kloudc.com/kloudc-privacy-policy/"
    assert c["terms_url"] == ""  # no Terms page yet: TERMS_URL decides


def test_platform_routes_and_master_pages_are_covered(raw):
    ids = {p["id"] for p in raw["pages"]}
    for tab in ("requests", "stores", "hosting", "audit"):
        assert f"platform_{tab}" in ids


# ---------------------------------------------------------------- API


def test_help_needs_login(anon_client):
    assert anon_client.get("/api/help").status_code == 401


def test_help_for_a_signed_in_user(staff_client, raw):
    r = staff_client.get("/api/help")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == raw["manifest"]["version"]
    assert {p["id"] for p in body["pages"]} == {p["id"] for p in raw["pages"]}
    # An article for another POS ("pos": [...], website ready A8) is left out.
    pos = hc.store_pos()
    shown = [a for a in raw["articles"] if not (a.get("pos") and pos is not None and pos not in a["pos"])]
    assert pos is None or len(shown) < len(raw["articles"]), "expected a POS-only article to be hidden"
    assert len(body["articles"]) == len(shown)
    assert set(body["getting_started"]) == {"owner", "staff", "master"}
    text = json.dumps(body)
    for ph in hc.ALLOWED_PLACEHOLDERS:
        assert ph not in text, f"{ph} not filled"
    assert body["contact"]["email"] == "support@kloudc.com"
    assert r.headers["etag"].startswith('"help-')


def test_help_etag_and_gzip(staff_client):
    first = staff_client.get("/api/help", headers={"Accept-Encoding": "gzip"})
    assert first.status_code == 200
    again = staff_client.get("/api/help", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
    assert again.content == b""


def test_encoded_gzips_large_bodies():
    raw_bytes, gz = hc.encoded({"x": "y" * 5000}, "gzip, deflate")
    assert gz and json.loads(gzip.decompress(raw_bytes)) == {"x": "y" * 5000}
    small, gz_small = hc.encoded({"x": 1}, "gzip")
    assert not gz_small and json.loads(small) == {"x": 1}


def test_public_help_is_logged_out_and_small(anon_client, raw):
    r = anon_client.get("/api/public/help")
    assert r.status_code == 200, r.text
    body = r.json()
    public = {p["id"] for p in raw["pages"] if p["public"]}
    assert {p["id"] for p in body["pages"]} == public
    assert {"login", "signup"} <= public
    assert "articles" not in body and "faq" not in body
    assert "{pos}" not in json.dumps(body)
    assert body["contact"]["terms_url"] == ""


def test_public_help_shows_terms_once_set(anon_client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "terms_url", "https://kloudc.com/terms/")
    assert anon_client.get("/api/public/help").json()["contact"]["terms_url"] == "https://kloudc.com/terms/"
    monkeypatch.setattr(get_settings(), "terms_url", "javascript:alert(1)")
    assert anon_client.get("/api/public/help").json()["contact"]["terms_url"] == ""


def test_missing_folder_never_breaks_the_server(monkeypatch, tmp_path):
    monkeypatch.setenv("HELP_CONTENT_DIR", str(tmp_path / "nowhere"))
    hc.reload()
    try:
        assert hc.content()["pages"] == []
        assert g.get_glossary()["terms"]  # status terms still served
    finally:
        monkeypatch.delenv("HELP_CONTENT_DIR")
        hc.reload()


# ---------------------------------------------------------------- glossary merge


def test_glossary_merges_concept_terms(staff_client, raw):
    body = staff_client.get("/api/glossary").json()
    assert body["version"] == 2
    keys = {t["key"] for t in body["terms"]}
    assert {t["key"] for t in raw["glossary"]} <= keys
    cats = {c["key"] for c in body["categories"]}
    assert {c["key"] for c in raw["glossary_categories"]} <= cats
    pages = {p["id"] for p in raw["pages"]}
    for t in body["terms"]:
        assert t["category"] in cats, t["key"]
        assert set(t) >= {"area", "pages", "where", "related", "aliases"}, t["key"]
        assert all(pid in pages for pid in t["pages"]), (t["key"], t["pages"])
    labels = [t["label"].lower() for t in body["terms"] if t["key"].startswith("concept_")]
    status = {t["label"].lower() for t in body["terms"] if not t["key"].startswith("concept_")}
    assert not (set(labels) & status), "a concept term repeats a status term's label"
    assert len(labels) == len(set(labels))


def test_glossary_concepts_get_store_words(staff_client):
    body = staff_client.get("/api/glossary").json()
    assert "{pos}" not in json.dumps(body)
