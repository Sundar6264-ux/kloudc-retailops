"""Website ready A7 + A8: help and glossary per POS.

A7: a CSV-only store reads "your POS" wherever the text says {pos}; "CSV
upload" (the provider's internal name) never appears. A8: entries marked
``"pos": [...]`` (content/help/schema.json) reach only stores on that POS;
masters and signed-out readers see them labelled with the POS."""

import json
import types

import pytest

from app import glossary as g
from app import help_content as hc

PROVIDERS = ("toast", "clover", "csv")
NAMES = {"toast": "Toast", "clover": "Clover", "csv": "CSV upload"}


@pytest.fixture
def store_on(monkeypatch):
    def _set(provider):
        settings = types.SimpleNamespace(
            store_timezone="America/New_York", toast_timezone="America/New_York", store_name="Corner Market",
            pos_provider=provider,
        )
        monkeypatch.setattr("app.config.get_settings", lambda: settings)
        monkeypatch.setattr("app.pos.gating.pos_provider", lambda: provider)
        monkeypatch.setattr("app.pos.gating.pos_name", lambda: NAMES[provider])

    return _set


def _rendered():
    return json.dumps(hc.help_body(), ensure_ascii=False) + json.dumps(g.get_glossary(), ensure_ascii=False)


def test_csv_only_store_never_reads_csv_upload_or_a_pos_brand(store_on):
    store_on("csv")
    text = _rendered()
    assert "CSV upload" not in text
    assert "{pos}" not in text
    assert "your POS" in text
    for brand in ("Toast API", "Toast's developer portal", "**Toast**", "**Clover**"):
        assert brand not in text, brand
    assert "No POS connection" in text  # the CSV-only note on Integrations


def test_clover_store_sees_no_toast_only_entries(store_on):
    store_on("clover")
    text = json.dumps(hc.help_body(), ensure_ascii=False)
    assert "Toast API client ID" not in text and "**Clover**" in text


def test_toast_store_sees_its_entries(store_on):
    store_on("toast")
    text = json.dumps(hc.help_body(), ensure_ascii=False)
    assert "Toast API client ID" in text and "**Clover**" not in text and "No POS connection" not in text


SYNTHETIC = {
    "pages": [
        {"id": "a", "info": {"summary": "x", "points": []}},
        {"id": "b", "pos": ["toast"], "info": {"summary": "Toast Purchasing API", "points": []}},
    ],
    "articles": [
        {"id": "x", "blocks": [{"type": "p", "text": "for all"},
                               {"type": "note", "pos": ["toast"], "text": "Toast Purchasing API orders"}]},
    ],
    "glossary": [{"key": "purchasing_api", "pos": ["toast", "clover"], "label": "Purchasing API"}],
}


@pytest.mark.parametrize("pos", ["clover", "csv"])
def test_marked_entries_are_dropped_for_another_pos(pos):
    out = json.dumps(hc.only_for_pos(SYNTHETIC, pos))
    assert "Toast Purchasing API" not in out and "for all" in out
    assert ("Purchasing API" in out) is (pos == "clover")


def test_marked_entries_stay_for_their_pos():
    out = hc.only_for_pos(SYNTHETIC, "toast")
    assert [p["id"] for p in out["pages"]] == ["a", "b"]
    assert "pos_label" not in json.dumps(out)


def test_masters_and_signed_out_readers_see_labels():
    out = hc.only_for_pos(SYNTHETIC, None)
    assert out["pages"][1]["pos_label"] == "Toast only"
    assert out["articles"][0]["blocks"][1]["pos_label"] == "Toast only"
    assert out["glossary"][0]["pos_label"] == "Toast / Clover only"
    public = json.dumps(hc.public_help_body())
    assert "{pos}" not in public


def test_schema_accepts_pos_and_refuses_unknown_pos():
    schema = hc.load_schema()
    raw = hc.load_raw()
    assert hc.schema_errors(raw, schema) == []
    block = {"type": "note", "text": "x", "pos": ["square"]}
    assert hc.validate_schema(block, schema["$defs"]["Block"], schema)
    assert hc.validate_schema({"type": "note", "text": "x", "pos": ["toast"]}, schema["$defs"]["Block"], schema) == []


@pytest.mark.parametrize("provider", PROVIDERS)
def test_every_help_and_glossary_entry_renders_for_every_pos(store_on, provider):
    store_on(provider)
    text = _rendered()
    assert "{pos}" not in text and "{store}" not in text
