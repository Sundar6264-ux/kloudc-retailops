"""The glossary reads in this store's own words (multi-store M2, MT9;
docs/M2_CONTRACT.md section 6): "{pos}" becomes the POS name and
"{store_city}" the store's time zone as people say it. For a Toast store in
New York the served text is exactly the pre-M2 wording."""
import json
import re
import types

from app import glossary as g
from app.services import fix_issues

from tests.origin_names import find_origin_names, has_origin_name


def _settings(tz):
    return types.SimpleNamespace(store_timezone=tz, toast_timezone="America/New_York")


def _body():
    return json.dumps(g.get_glossary())


def test_no_placeholder_left_and_toast_wording_kept(monkeypatch):
    monkeypatch.setattr("app.pos.gating.pos_name", lambda: "Toast")
    monkeypatch.setattr("app.config.get_settings", lambda: _settings(""))
    body = _body()
    assert "{pos}" not in body and "{store_city}" not in body
    assert "Toast" in body
    assert "store time (New York)" in body
    # The raw table still holds the placeholders (filled per request).
    assert any("{pos}" in (t["definition"] or "") + (t["rule"] or "") for t in g.TERMS)


def test_clover_store_never_says_toast(monkeypatch):
    monkeypatch.setattr("app.pos.gating.pos_name", lambda: "Clover")
    # A Clover store's provider too: terms marked "pos": ["toast"] (Toast's
    # two key pairs) are left out for it, like on a real Clover server.
    monkeypatch.setattr("app.pos.gating.pos_provider", lambda: "clover")
    monkeypatch.setattr("app.config.get_settings", lambda: _settings("America/Los_Angeles"))
    body = _body()
    assert "Toast" not in body
    assert "Clover" in body
    assert "store time (Los Angeles)" in body
    assert "New York" not in body


def test_reason_terms_are_filled_too(monkeypatch):
    monkeypatch.setattr("app.pos.gating.pos_name", lambda: "Clover")
    monkeypatch.setattr("app.config.get_settings", lambda: _settings("America/Chicago"))
    # `does` is the glossary text (this file's job); the reason labels
    # themselves live in services/fix_issues.py (store-pack batch).
    does = json.dumps([r["does"] for t in fix_issues.all_reasons().values() for r in t["reasons"]])
    assert "{pos}" not in does and "Toast" not in does
    assert "Clover" in does


def test_no_store_names_in_the_glossary(monkeypatch):
    monkeypatch.setattr("app.pos.gating.pos_name", lambda: "Toast")
    monkeypatch.setattr("app.config.get_settings", lambda: _settings(""))
    hits = [line for _, line in find_origin_names(_body())]
    assert hits == [], hits
    source = open(g.__file__, encoding="utf-8").read()
    assert not has_origin_name(source)
