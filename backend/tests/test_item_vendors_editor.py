"""Item vendors editor (website-ready track D4): /api/item-vendors.

A Clover (or CSV-only) store edits who supplies each item; a Toast store's
list follows its inventory CSV, so the editor is read-only there. Edits land
in item_vendors (manual rows) and in the newest snapshot rows, so every page
reading vendors_of(row) sees them at once."""

import datetime as dt

import pytest

from app.item_vendor_models import ItemVendor
from app.models import InventorySnapshot
from app.services import item_vendors as iv
from tests.conftest import client_for, make_user
from tests.store_fixtures import use_store

DAY = dt.date(2025, 10, 15)


def _seed(db, day=DAY):
    for item_id, name, barcode, supplier in (
        ("I_RICE", "Basmati Rice 10 lb", "012345678905", None),
        ("I_ONION", "Red Onions", "", None),
        ("I_TEA", "Chai Tea", "0099;0100", None),
        ("I_TEA2", "Chai Tea", "0101", None),
    ):
        db.add(InventorySnapshot(snapshot_date=day, item_id=item_id, name=name, barcode=barcode, supplier=supplier,
                                 source_filename="api:2025-10-15T08:00:00Z"))
    db.commit()


@pytest.fixture
def clover(db, monkeypatch):
    use_store(monkeypatch, "RETAILOPS_TEST_CLOVER")
    _seed(db)
    return client_for(db, make_user(db, "owner", is_admin=True))


def _csv(text):
    return {"file": ("vendors.csv", text.encode("utf-8"), "text/csv")}


def test_toast_store_is_read_only(db, admin_client):
    _seed(db)
    r = admin_client.get("/api/item-vendors")
    assert r.status_code == 200 and r.json()["editable"] is False and "inventory file" in r.json()["read_only_reason"]
    assert admin_client.put("/api/item-vendors/items/I_RICE", json={"vendors": [{"name": "X"}]}).status_code == 409
    assert admin_client.post("/api/item-vendors/import", files=_csv("Item ID,Vendor\nI_RICE,X\n")).status_code == 409
    assert db.query(ItemVendor).count() == 0


def test_staff_and_anonymous_refused(db, clover, staff_client, anon_client):
    assert staff_client.get("/api/item-vendors").status_code == 403
    assert staff_client.put("/api/item-vendors/items/I_RICE", json={"vendors": []}).status_code == 403
    assert anon_client.get("/api/item-vendors").status_code == 401


def test_edit_list_filter_and_snapshot_follow(db, clover):
    r = clover.get("/api/item-vendors")
    body = r.json()
    assert body["editable"] and body["pos"] == "Clover" and body["catalog_total"] == 4 and body["missing_count"] == 4
    assert [i["name"] for i in body["items"]] == ["Basmati Rice 10 lb", "Chai Tea", "Chai Tea", "Red Onions"]
    put = clover.put("/api/item-vendors/items/I_RICE", json={"vendors": [
        {"name": "  Main Street   Wholesale ", "item_code": "BR-10"}, {"name": "Second Source"}]})
    assert put.status_code == 200, put.text
    assert put.json()["changed"] is True
    assert put.json()["vendors"] == [
        {"name": "Main Street Wholesale", "item_code": "BR-10", "rank": 1, "source": "manual"},
        {"name": "Second Source", "item_code": None, "rank": 2, "source": "manual"}]
    db.expire_all()
    snap = db.query(InventorySnapshot).filter_by(item_id="I_RICE").one()
    assert snap.supplier == "Main Street Wholesale;Second Source" and snap.supplier_item_id == "BR-10;"
    assert iv.vendors_of(snap) == ["Main Street Wholesale", "Second Source"]
    # The same list again changes nothing.
    assert clover.put("/api/item-vendors/items/I_RICE", json={"vendors": [
        {"name": "Main Street Wholesale", "item_code": "BR-10"}, {"name": "Second Source"}]}).json()["changed"] is False
    missing = clover.get("/api/item-vendors", params={"show": "missing"}).json()
    assert missing["missing_count"] == 3 and "I_RICE" not in [i["item_id"] for i in missing["items"]]
    assert [i["item_id"] for i in clover.get("/api/item-vendors", params={"show": "with"}).json()["items"]] == ["I_RICE"]
    assert [i["item_id"] for i in clover.get("/api/item-vendors", params={"q": "second"}).json()["items"]] == ["I_RICE"]
    assert clover.get("/api/item-vendors").json()["vendor_names"] == ["Main Street Wholesale", "Second Source"]
    # Clearing the list empties the snapshot fields.
    assert clover.put("/api/item-vendors/items/I_RICE", json={"vendors": []}).json()["vendors"] == []
    db.expire_all()
    assert db.query(InventorySnapshot).filter_by(item_id="I_RICE").one().supplier is None


@pytest.mark.parametrize("vendors, message", [
    ([{"name": ""}], "Enter a vendor name"),
    ([{"name": "A;B"}], ";"),
    ([{"name": "Acme"}, {"name": "ACME "}], "listed twice"),
    ([{"name": f"V{i}"} for i in range(11)], "at most 10"),
    ([{"name": "x" * 81}], "at most 80"),
    ([{"name": "Acme", "item_code": "a;b"}], ";"),
])
def test_bad_lists_are_refused(clover, vendors, message):
    r = clover.put("/api/item-vendors/items/I_RICE", json={"vendors": vendors})
    assert r.status_code == 400 and message in r.json()["detail"]


def test_unknown_item_is_404(clover):
    assert clover.put("/api/item-vendors/items/NOPE", json={"vendors": [{"name": "A"}]}).status_code == 404


def test_import_preview_errors_apply_and_idempotent(db, clover):
    text = (
        "Item ID,Item name,Barcode,Vendor,Vendor item code,Rank\n"
        "I_RICE,,,Second Source,,2\n"
        "I_RICE,,,Main Street Wholesale,BR-10,1\n"
        ",,012345678905,Ignored Dup,,3\n"        # same item by barcode: a third vendor
        ",Red Onions,,Farm Co,ON-1,\n"           # by name
        ",Chai Tea,,Tea House,,\n"               # two items called Chai Tea
        "NOPE,,,Ghost,,\n"                       # unknown item
        "I_TEA,,,,,\n"                           # no vendor: skipped, not an error
        "I_TEA,,,A;B,,\n"                        # bad vendor name
        "I_TEA2,,,Tea House,,x\n"                # bad rank
    )
    pv = clover.post("/api/item-vendors/import", files=_csv(text))
    assert pv.status_code == 200, pv.text
    p = pv.json()
    assert p["applied"] is False and p["row_count"] == 9 and p["errors"] == 4
    errors = {r["line"]: r["error"] for r in p["rows"] if r["error"]}
    assert set(errors) == {6, 7, 9, 10}
    assert "more than one item" in errors[6].lower() and "No item" in errors[7] and ";" in errors[9]
    assert "Rank" in errors[10]
    assert p["rows"][6]["note"] == "No vendor on this row: skipped."
    assert p["items_to_change"] == 2 and db.query(ItemVendor).count() == 0  # preview writes nothing
    ap = clover.post("/api/item-vendors/import", params={"apply": "1"}, files=_csv(text)).json()
    assert ap["applied"] is True and ap["items_to_change"] == 2
    db.expire_all()
    rice = iv.load_vendor_table(db, ["I_RICE"])["I_RICE"]
    assert [(v.rank, v.vendor_name, v.vendor_item_code) for v in rice] == [
        (1, "Main Street Wholesale", "BR-10"), (2, "Second Source", None), (3, "Ignored Dup", None)]
    assert db.query(InventorySnapshot).filter_by(item_id="I_ONION").one().supplier == "Farm Co"
    again = clover.post("/api/item-vendors/import", params={"apply": "1"}, files=_csv(text)).json()
    assert again["items_to_change"] == 0


def test_import_refuses_bad_files(clover):
    assert clover.post("/api/item-vendors/import", files={"file": ("v.xlsx", b"x", "application/octet-stream")}).status_code == 400
    r = clover.post("/api/item-vendors/import", files=_csv("Name,Price\nA,1\n"))
    assert r.status_code == 400 and "Vendor column" in r.json()["detail"]
    assert clover.post("/api/item-vendors/import", files=_csv("")).status_code == 400
    assert clover.post("/api/item-vendors/import").status_code == 422


def test_export_and_template(db, clover):
    db.add(InventorySnapshot(snapshot_date=DAY, item_id="I_EVIL", name="=HYPERLINK(\"x\")", source_filename="api:x"))
    db.commit()
    clover.put("/api/item-vendors/items/I_RICE", json={"vendors": [{"name": "Main Street Wholesale", "item_code": "BR-10"}]})
    r = clover.get("/api/item-vendors/export.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert lines[0] == "Item ID,Item name,Barcode,Vendor,Vendor item code,Rank"
    assert "I_RICE,Basmati Rice 10 lb,012345678905,Main Street Wholesale,BR-10,1" in lines
    assert "I_ONION,Red Onions,,,," in lines
    assert any(l.startswith("I_EVIL,\"'=HYPERLINK") for l in lines)  # never a live formula
    # The export imports back unchanged.
    back = clover.post("/api/item-vendors/import", files=_csv(r.text)).json()
    assert back["errors"] == 0 and back["items_to_change"] == 0
    t = clover.get("/api/item-vendors/template.csv")
    assert t.status_code == 200 and t.text.splitlines()[0].startswith("Item ID,Item name")


def test_csv_upload_keeps_editor_vendors_for_a_non_toast_store(db, clover):
    clover.put("/api/item-vendors/items/I_ONION", json={"vendors": [{"name": "Farm Co"}]})
    # A later snapshot date arrives without vendors (e.g. a CSV with no Supplier column).
    db.add(InventorySnapshot(snapshot_date=DAY + dt.timedelta(days=1), item_id="I_ONION", name="Red Onions",
                             source_filename="upload.csv"))
    db.commit()
    stats = iv.refresh_after_csv_upload(db, ["I_ONION"])
    db.commit()
    assert stats["snapshot_rows_filled"] == 1
    row = db.query(InventorySnapshot).filter_by(item_id="I_ONION", snapshot_date=DAY + dt.timedelta(days=1)).one()
    assert row.supplier == "Farm Co"
