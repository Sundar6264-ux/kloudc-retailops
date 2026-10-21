"""CI trim (pre-production 2026-10-21): docs-only changes skip the full suite
but still run the docs name guard."""

import pathlib

import yaml

WF = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows"
DOCS = ["docs/**", "**/*.md"]


def _on(name):
    data = yaml.safe_load((WF / name).read_text())
    return data.get("on", data.get(True))


def test_full_ci_skips_docs_only_changes():
    on = _on("ci.yml")
    assert on["push"]["paths-ignore"] == DOCS and on["pull_request"]["paths-ignore"] == DOCS
    assert on["push"]["branches"] == ["main"] and "workflow_dispatch" in on


def test_docs_changes_still_get_the_name_guard():
    on = _on("docs.yml")
    assert on["push"]["paths"] == DOCS and on["pull_request"]["paths"] == DOCS
    text = (WF / "docs.yml").read_text()
    assert "tests/test_no_origin_names.py" in text
