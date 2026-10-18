"""The store pack (StoreOps M2, MT8; docs/M2_CONTRACT.md section 3)."""

import json

import pytest

from app.store import pack as store_pack
from app.store.pack import PackError, load_pack_file, parse_pack
from tests.store_fixtures import BACKEND, use_store

PACKS = BACKEND / "packs"


def _pack(**sections):
    return parse_pack({"pack_version": 1, "pack_id": "t", **sections})


@pytest.mark.parametrize("name", ["generic.json", "test_toast.json", "test_clover.json", "demo.json"])
def test_committed_packs_are_valid(name):
    pack = load_pack_file(PACKS / name)
    assert pack.pack_version == 1


def test_schema_file_matches_the_models():
    committed = json.loads((PACKS / "store_pack.v1.schema.json").read_text())
    assert committed == store_pack.json_schema(), "re-run: python -m app.store pack schema > packs/store_pack.v1.schema.json"


def test_errors_are_readable_lines():
    with pytest.raises(PackError) as exc:
        parse_pack(
            {
                "pack_version": 2,
                "pack_id": "Bad Id",
                "menu": {"kitchen_groups": ["A"], "typo": 1},
                "po_sections": {"rules": [{"section": "A", "section_from": "category"}, {"lookup": "category"}]},
                "issue_leads": {"leads": [{"key": "a", "name": "A"}], "section_proposals": {"X": {"lead": "zz", "basis": "doc", "why": ""}}},
                "flyer_categories": [{"key": "a", "label": "A", "folder": "../x"}],
                "unknown_section": {},
            }
        )
    text = "\n".join(exc.value.errors)
    for needle in (
        "pack_version: only pack_version 1",
        "pack_id: must match",
        "menu.typo: Extra inputs are not permitted",
        "po_sections.rules.0: a rule needs exactly one of",
        "po_sections.rules.1: lookup needs a map",
        "issue_leads.section_proposals.X.basis",
        "flyer_categories.0.folder",
        "unknown_section: Extra inputs are not permitted",
    ):
        assert needle in text, (needle, text)
    assert str(exc.value).startswith("Store pack is not valid:")


def test_bad_json_and_missing_files_are_pack_errors(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    with pytest.raises(PackError, match="not valid JSON"):
        load_pack_file(bad)
    with pytest.raises(PackError, match="can't read"):
        load_pack_file(tmp_path / "missing.json")


def test_bad_regex_and_checklist_are_refused():
    with pytest.raises(PackError, match="not a valid regular expression"):
        _pack(po_sections={"rules": [{"when": {"name_regex": "("}, "section": "A"}]})
    with pytest.raises(PackError, match="checklists"):
        _pack(checklists=[{"checklist_type": "c", "title": "C", "settings": {"whatsapp_group": "nope"},
                           "schema": {"sections": [{"id": "s", "items": []}]}}],
              whatsapp_roles=[{"key": "store", "label": "Store"}])


def test_empty_pack_is_the_generic_defaults():
    pack = _pack()
    generic = load_pack_file(PACKS / "generic.json")
    assert pack.model_dump(exclude={"pack_id", "label"}) == generic.model_dump(exclude={"pack_id", "label"})
    # A new store: the category is the PO section; nothing is kitchen-made;
    # every menu is grocery; no house vendor; the admin is the only lead.
    assert store_pack.section_for("RETAIL", "Snacks", "Chips", "Lays", pack=pack) == "Snacks"
    assert store_pack.section_for("RETAIL", "", "", "Lays", pack=pack) == "Other"
    assert store_pack.excluded_category_groups(pack) == frozenset()
    assert store_pack.is_grocery_menu_set({"anything"}, pack=pack)
    assert not store_pack.is_house_vendor_key("acme foods", pack=pack)
    assert pack.issue_leads.leads == [] and pack.issue_leads.fallback.key == "admin"
    assert [c.key for c in pack.flyer_categories] == ["groceries", "offers", "others"]
    assert [r.key for r in pack.whatsapp_roles] == ["store"]
    assert [c.checklist_type for c in pack.checklists] == ["daily_closing"]


def test_every_rule_outcome_type():
    pack = load_pack_file(PACKS / "test_toast.json")
    f = lambda *a: store_pack.section_for(*a, pack=pack)  # noqa: E731
    assert f(" kitchen ", "Hot Food", "", "Wrap") == "Other"  # section (group upper-cased + trimmed)
    assert f("RETAIL", "Frozen Foods", "", "Vanilla Ice Cream") == "Frozen - Ice Cream"  # first_match
    assert f("RETAIL", "Snacks", "", "Frozen Pizza") == "Frozen - Snacks"  # any: name regex
    assert f("RETAIL", "Frozen Foods", "Frozen Vegetables", "Peas") == "Frozen - Vegetables"  # lookup subcategory
    assert f("RETAIL", "Frozen Foods", "Other", "Peas") == "Frozen - Other"  # no map hit -> next rule
    assert f("PRODUCE", "Fruit", "", "Apple") == "Produce"
    assert f("RETAIL", "Snacks", "Chips", "Lays") == "Chips"  # category|subcategory
    assert f("RETAIL", " Snacks ", "", "Pretzels") == "Snacks"  # category map (trimmed)
    assert f("RETAIL", "Beverages", "", "Cola") == "Drinks"
    assert f("RETAIL", "Bakery", "", "Bread") == "Bakery"  # section_from category
    assert f(None, None, None, None) == "Other"
    assert store_pack.section_names(pack)[-1] == "Other"
    assert "Seasonal" in store_pack.builtin_sections(pack) and "Other" not in store_pack.builtin_sections(pack)
    clover = load_pack_file(PACKS / "test_clover.json")
    assert store_pack.section_for("RETAIL", "Dairy", "", "Milk", pack=clover) == "Dairy & Eggs"
    assert store_pack.section_for("RETAIL", "Bakery", "", "Bread", pack=clover) == "Bakery"


def test_when_clauses():
    pack = _pack(po_sections={"rules": [
        {"when": {"subcategory": [""], "category": ["Misc"]}, "section": "Blank sub"},
        {"when": {"name_regex": "^acme", "name_not_regex": "mix"}, "section": "Acme"},
        {"when": {"any": [{"group": ["A"]}, {"group": ["B"], "category": ["C"]}]}, "section": "AB"},
        {"lookup": "group", "map": {" deli ": "Deli"}},
    ]})
    f = lambda *a: store_pack.section_for(*a, pack=pack)  # noqa: E731
    assert f("X", "Misc", "  ", "x") == "Blank sub"
    assert f("X", "Misc", "Sub", "x") == "Other"
    assert f("X", "", "", " ACME Chips ") == "Acme" and f("X", "", "", "Acme Mix") == "Other"
    assert f("a", "", "", "x") == "AB" and f("B", "C", "", "x") == "AB" and f("B", "D", "", "x") == "Other"
    assert f("deli", "", "", "x") == "Deli"


def test_channel_rules():
    pack = _pack()
    c = lambda s: store_pack.channel_of(s, pack=pack)  # noqa: E731
    assert c(None) == c("") == c("No Dining Option") == "Other"
    assert c("In-Store Purchase") == "In-store" and c("in store bar") == "In-store"
    assert c("Pick Up") == c("TAKEOUT") == "Pick up"
    assert c("Online Ordering - Takeout") == "Online ordering"
    assert c("DoorDash - Delivery") == c("DoorDash - Takeout") == "DoorDash"
    assert c(" - Delivery") == "- Delivery"  # trimmed first, so nothing to split (the legacy rule)
    custom = _pack(channels={"empty": "None", "rules": [{"equals": ["kiosk"], "channel": "Kiosk"}],
                             "split_on": "/", "rename": {"web": "Website"}, "default": "Misc"})
    assert store_pack.channel_of("", pack=custom) == "None"
    assert store_pack.channel_of("KIOSK", pack=custom) == "Kiosk"
    assert store_pack.channel_of("Web / Pickup", pack=custom) == "Website"
    assert store_pack.channel_of("/x", pack=custom) == "Misc"


def test_house_vendor_prefixes_compare_like_vendor_key():
    from app.services.schedule_suggestions import vendor_key

    pack = load_pack_file(PACKS / "test_toast.json")
    for name, house in (("Test Toast Market", True), ("TestToastMarket - Deli", True), ("Test Toast Market, LLC", True),
                        ("Acme Foods", False), ("Toast Test Market", False)):
        assert store_pack.is_house_vendor_key(vendor_key(name), pack=pack) is house, name


def test_active_pack_precedence_file_then_database_then_generic(db, monkeypatch, tmp_path):
    from app.config import get_settings
    from app.store.__main__ import write_pack_row

    assert store_pack.current_pack().pack_id == "generic"
    assert store_pack.current_source().startswith("generic.json")
    clover = load_pack_file(PACKS / "test_clover.json")
    write_pack_row(db, clover, "test")
    db.commit()
    store_pack.reload_pack()
    assert store_pack.current_pack().pack_id == "storeops_test_clover"
    assert store_pack.current_source().startswith("database")
    # The same pack again writes nothing.
    assert write_pack_row(db, clover, "test") is False
    monkeypatch.setattr(get_settings(), "store_pack_file", str(PACKS / "test_toast.json"))
    store_pack.reload_pack()
    assert store_pack.current_pack().pack_id == "storeops_test_toast"
    assert "STORE_PACK_FILE" in store_pack.current_source()


def test_a_broken_stored_pack_falls_back_to_generic(db, caplog):
    from app.setting_models import SettingChange

    db.add(SettingChange(key="store_pack", value=json.dumps({"pack_version": 1, "pack_id": "x", "bogus": 1})))
    db.commit()
    store_pack.reload_pack()
    assert store_pack.current_pack().pack_id == "generic"
    assert "not valid" in caplog.text


def test_po_sections_page_lists_the_pack_sections(admin_client, monkeypatch):
    use_store(monkeypatch, "STOREOPS_TEST_TOAST")
    names = [row["section"] for row in admin_client.get("/api/categories/sections").json()["items"]]
    for section in store_pack.builtin_sections():
        assert section in names


def test_fake_store_rules_reach_the_services(monkeypatch):
    from app.services import delivery_suggestions, fix_issues, flyers, insights, insights_data, po_sections
    from app.services import schedule_suggestions, whatsapp_groups

    use_store(monkeypatch, "STOREOPS_TEST_TOAST")
    assert fix_issues.excluded_groups() == {"KITCHEN", "CATERING"} == fix_issues.EXCLUDED_CATEGORY_GROUPS
    assert delivery_suggestions.KITCHEN_GROUPS == {"KITCHEN", "CATERING"}
    assert insights.GROCERY_MENUS == {"retail", "produce"}
    assert insights_data.is_kitchen_path("Deli Kitchen", "Sandwiches")
    assert not insights_data.is_kitchen_path("Deli Kitchen", "Drinks - Soda")
    assert insights_data.build_menu_map({"menus": []})["kitchen_rule"].startswith("Deli Kitchen menus")
    assert insights.channel_of("Uber Eats - Delivery") == "Uber Eats"
    assert po_sections.section_for("RETAIL", "Beverages", "", "Cola") == "Drinks"
    assert "Seasonal" in po_sections.BUILTIN_SECTIONS
    assert schedule_suggestions._is_house("test toast market")
    assert [k for k, _, _ in flyers.categories()] == ["groceries", "offers", "others"]
    assert whatsapp_groups.BUILTIN_GROUPS == (("store", "Store staff group"),)


@pytest.mark.pack_checklists
def test_checklist_fallback_serves_the_pack_checklists(monkeypatch):
    from app.services import checklist_schemas

    use_store(monkeypatch, "STOREOPS_TEST_TOAST")
    schemas = checklist_schemas.list_schemas()
    assert list(schemas) == ["daily_closing"]
    assert checklist_schemas.short_name("daily_closing") == "Closing"
    assert checklist_schemas.whatsapp_group("daily_closing") == "store"
    assert checklist_schemas.closing_checklist_types() == ["daily_closing"]


@pytest.mark.pack_checklists
def test_admin_seeding_writes_the_pack_checklists(db, monkeypatch):
    from app.models import ChecklistDefinition
    from app.services.checklist_admin import ensure_seeded

    use_store(monkeypatch, "STOREOPS_TEST_CLOVER")  # no checklists section -> generic's
    assert ensure_seeded(db) is True
    rows = db.query(ChecklistDefinition).all()
    assert [(r.checklist_type, r.created_by) for r in rows] == [("daily_closing", "store pack")]
    assert ensure_seeded(db) is False


def test_pack_commands(tmp_path, capsys):
    from app.store.__main__ import main

    assert main(["pack", "validate", str(PACKS / "test_toast.json")]) == 0
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"pack_version": 1, "pack_id": "BAD"}))
    assert main(["pack", "validate", str(bad)]) == 1
    assert "pack_id" in capsys.readouterr().err
    assert main(["pack", "show"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["pack"]["pack_id"] == "generic"
    assert main(["pack", "schema"]) == 0
    assert json.loads(capsys.readouterr().out)["title"].startswith("StoreOps store pack")
