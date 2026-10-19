"""Legacy equivalence (KloudC RetailOps M2, MT8; docs/M2_CONTRACT.md 3.6).

The private legacy pack (scripts/packs/extract_legacy_pack.py, never
committed) must reproduce the rule functions of the import commit exactly:
section_for over every catalog item of a scratch database copy plus synthetic
cases for every branch, channel_of, the menu sets, the house vendor rule, the
kitchen-menu rule and the Fix Issues lead proposals. The legacy answers were
recorded by the extraction script into legacy_cases.json (same private
folder). Skipped on machines without the private files (CI).
"""

import json

import pytest

from app.store import pack as store_pack
from tests.store_fixtures import PRIVATE_DIR

PACK = PRIVATE_DIR / "origin.pack.json"
CASES = PRIVATE_DIR / "legacy_cases.json"

pytestmark = pytest.mark.skipif(
    not (PACK.is_file() and CASES.is_file()), reason="private legacy pack not on this machine (M2 contract 1.6)"
)


@pytest.fixture(scope="module")
def legacy():
    return store_pack.load_pack_file(PACK), json.loads(CASES.read_text(encoding="utf-8"))


def test_section_for_matches_every_recorded_case(legacy):
    pack, cases = legacy
    wrong = [c for c in cases["section_for"] if store_pack.section_for(*c[:4], pack=pack) != c[4]]
    assert len(cases["section_for"]) > 1000
    assert not wrong, wrong[:20]


def test_builtin_sections_match(legacy):
    pack, cases = legacy
    assert list(store_pack.builtin_sections(pack)) == cases["builtin_sections"]


def test_channel_of_matches(legacy):
    pack, cases = legacy
    assert [[d, store_pack.channel_of(d, pack=pack)] for d, _ in cases["channel_of"]] == cases["channel_of"]


def test_menu_sets_and_kitchen_rule_match(legacy, monkeypatch):
    pack, cases = legacy
    assert sorted(store_pack.excluded_category_groups(pack)) == cases["excluded_category_groups"]
    assert sorted(store_pack.kitchen_groups(pack)) == cases["kitchen_groups"]
    assert sorted(store_pack.grocery_menus(pack)) == cases["grocery_menus"]
    store_pack.set_override(pack)
    from app.services import insights_data

    assert [[m, g, insights_data.is_kitchen_path(m, g)] for m, g, _ in cases["is_kitchen_path"]] == cases["is_kitchen_path"]
    assert insights_data.build_menu_map({"menus": []})["kitchen_rule"] == cases["kitchen_rule"]


def test_house_vendor_rule_matches(legacy):
    from app.services.schedule_suggestions import vendor_key

    pack, cases = legacy
    got = [[v, store_pack.is_house_vendor_key(vendor_key(v), pack=pack)] for v, _ in cases["house_vendor"]]
    assert got == cases["house_vendor"]


def test_flyer_categories_and_whatsapp_roles_match(legacy):
    pack, cases = legacy
    assert [[c.key, c.label, c.folder] for c in pack.flyer_categories] == cases["flyer_categories"]
    assert [[r.key, r.label] for r in pack.whatsapp_roles] == cases["whatsapp_roles"]


def test_issue_lead_proposals_match(legacy):
    pack, cases = legacy
    store_pack.set_override(pack)
    from app.services import issue_leads

    legacy_leads = cases["issue_leads"]
    assert [{k: d.get(k) for k in ("key", "name", "title", "departments", "vendors")} for d in legacy_leads["doc_leads"]] == [
        {k: d.get(k) for k in ("key", "name", "title", "departments", "vendors")} for d in issue_leads.DOC_LEADS
    ]
    assert issue_leads.DOC_FALLBACK == legacy_leads["doc_fallback"]
    assert {k: list(v) for k, v in issue_leads.SECTION_PROPOSALS.items()} == legacy_leads["section_proposals"]
    assert issue_leads.SECTION_NOTES == legacy_leads["section_notes"]


@pytest.mark.legacy_pack
def test_legacy_settings_reproduce_the_legacy_strings():
    """With the private settings the store-facing text is the legacy text."""
    from app.services import po_send
    from app.store import settings_view as sv

    subject, body = po_send.compose("Acme Foods", [{"name": "x", "qty": 2}])
    assert sv.store_display_name() in subject
    assert body.endswith("\n".join([sv.store_display_name(), sv.store_address(), sv.store_phone()]) + "\n\n--\nSent with KloudC RetailOps by KloudC LLC")
    assert po_send.whatsapp_caption("Acme Foods", [{"qty": 1}]).endswith(sv.store_phone())
    assert sv.first_sales_date() is not None and sv.ai_enabled()
