"""Item vendors as app data (multi-store M1, MT5; docs/MULTISTORE_M1_CONTRACT.md
"As built - MT5").

1. The one vendor rule (services/item_vendors.py) gives exactly what each of
   the 8 old ";" parsers gave, for every edge case (fuzzed against frozen
   copies of the old code).
2. ranked_vendors: one row per vendor, rank = listed position, codes.
3. The table follows each item's newest CSV row: refresh, CSV upload hook,
   CSV wins over manual rows, never fails an upload.
4. Migration 0061 creates the table and backfills the same rows the app
   would write; down/up/down is clean.
"""

import datetime as dt
import os
import pathlib
import random
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app.db import Base
from app.item_vendor_models import ItemVendor
from app.models import InventorySnapshot
from app.services import inventory_api, item_vendors as iv
from app.services.inventory_parser import parse_and_store
from app.services.similar_products import aligned_codes, is_junk_vendor, split_aligned, split_multi, vendor_refs

BACKEND = pathlib.Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 1. Same answers as the 8 old parsers (frozen copies, 08b43c0)
# ---------------------------------------------------------------------------


def old_reorder_split(raw):  # reorder.py:252 / supplier_projection.py:55
    if not raw:
        return []
    return [s.strip() for s in raw.split(";") if s.strip()]


def old_rules_split(raw):  # po_section_rules.py:127 / issue_leads.py:327
    return [s.strip() for s in (raw or "").split(";") if s.strip()]


def old_po_log_vendors(supplier):  # purchase_order_log.py:286
    return [v.strip() for v in (supplier or "").split(";")]


def old_split_multi(raw):  # similar_products.py:424
    if not raw:
        return []
    return [s.strip() for s in str(raw).split(";") if s.strip()]


def old_split_aligned(raw):  # similar_products.py:440
    if not raw:
        return []
    return [s.strip() for s in str(raw).split(";")]


def old_aligned_codes(supplier, supplier_item_id):
    vendors = old_split_aligned(supplier)
    codes = old_split_aligned(supplier_item_id)
    while len(codes) > len(vendors) and codes and codes[-1] == "":
        codes.pop()
    if len(codes) != len(vendors):
        return None
    return [c or None for c in codes]


def old_split_codes(value):  # product_match.py:48
    return [part.strip() for part in str(value or "").split(";") if part.strip()]


def old_override_parts(supplier):  # category_overrides.py:191 (keys looked up)
    return [part.strip().lower() for part in supplier.split(";")]


PIECES = ["Raja Foods", "raja foods", "RBest", " Babco ", "ACT_SUP_UPDT", "2234", "\tChetak", "", " ", "Vdyas "]


def _fuzz_strings(n=3000, seed=7):
    rng = random.Random(seed)
    out = [None, "", " ", ";", ";;", " ; ", "A", "A;A", "a;A", "A;;B", ";A", "A;", "ACT_SUP_UPDT"]
    for _ in range(n):
        out.append(";".join(rng.choice(PIECES) for _ in range(rng.randint(1, 6))))
    return out


def test_split_rule_matches_every_old_parser():
    from app.services import po_section_rules, issue_leads, product_match, reorder, supplier_projection

    for raw in _fuzz_strings():
        assert reorder._split_suppliers(raw) == old_reorder_split(raw), raw
        assert supplier_projection._split_suppliers(raw) == old_reorder_split(raw), raw
        assert po_section_rules._split(raw) == old_rules_split(raw), raw
        assert issue_leads._split(raw) == old_rules_split(raw), raw
        assert split_multi(raw) == old_split_multi(raw), raw
        assert split_aligned(raw) == old_split_aligned(raw), raw
        assert product_match.split_codes(raw) == old_split_codes(raw), raw
        assert (iv.vendors_of(SimpleNamespace(supplier=raw), keep_empty=True) or [""]) == old_po_log_vendors(raw), raw
        if raw:
            # category_overrides looks every part up in a dict of non-empty
            # names: the same names are found (empty parts never match).
            assert [p.lower() for p in iv.vendors_of(raw)] == [p for p in old_override_parts(raw) if p], raw


def test_aligned_codes_match_old_rule():
    rng = random.Random(11)
    codes = ["", "2524", "856977005124", " 77 ", ";"]
    for supplier in _fuzz_strings(800, seed=3):
        for _ in range(3):
            code_raw = ";".join(rng.choice(codes) for _ in range(rng.randint(0, 6))) or None
            assert aligned_codes(supplier, code_raw) == old_aligned_codes(supplier, code_raw), (supplier, code_raw)


def test_purchase_order_log_keeps_empty_supplier_preference():
    from app.services.purchase_order_log import _resolve_item_id

    a = SimpleNamespace(item_id="a", name="Tea", supplier="Babco", price=1.0)
    b = SimpleNamespace(item_id="b", name="Tea", supplier=None, price=1.0)
    # Old: (None or "").split(";") == [""], so "" counts as listed on b.
    assert _resolve_item_id({"a": a, "b": b}, "tea", "") == "b"
    assert _resolve_item_id({"a": a, "b": b}, "tea", "Babco") == "a"


def test_category_override_supplier_rule_any_listed_vendor():
    from app.services.category_overrides import Rules

    rules = [
        SimpleNamespace(id=1, match_type="supplier", match_value=" Raja Foods ", is_active=True),
        SimpleNamespace(id=2, match_type="supplier", match_value="babco", is_active=True),
    ]
    idx = Rules(rules)
    assert idx.rule_for(None, "RBest; raja foods").id == 1
    assert idx.rule_for(None, "Raja Foods;BABCO").id == 2  # newest of the matches
    assert idx.rule_for(None, ";;") is None


# ---------------------------------------------------------------------------
# vendors_of / split_field edge cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, []),
        ("", []),
        ("   ", []),
        (";;", []),
        (" Raja Foods ;  RBest ", ["Raja Foods", "RBest"]),
        ("Chetak;Chetak", ["Chetak", "Chetak"]),  # repeats kept (old parsers kept them)
        ("Raja Foods;raja foods", ["Raja Foods", "raja foods"]),  # case kept
        ("ACT_SUP_UPDT;2234;Babco", ["ACT_SUP_UPDT", "2234", "Babco"]),  # junk kept, in place
        ("A;;B;", ["A", "B"]),
        ("\tVdyas\n", ["Vdyas"]),
    ],
)
def test_vendors_of_edge_cases(raw, expected):
    assert iv.vendors_of(raw) == expected
    assert iv.vendors_of(SimpleNamespace(supplier=raw)) == expected
    assert iv.vendors_of({"supplier": raw}) == expected


def test_vendors_of_inputs_and_keep_empty():
    assert iv.vendors_of(SimpleNamespace()) == []  # no supplier attribute
    assert iv.vendors_of({}) == []
    assert iv.vendors_of(2234.0) == ["2234.0"]  # odd non-string values don't crash
    assert iv.split_field(";2524; 856977005124 ", keep_empty=True) == ["", "2524", "856977005124"]
    assert iv.split_field(None, keep_empty=True) == []


# ---------------------------------------------------------------------------
# 2. ranked_vendors
# ---------------------------------------------------------------------------


def _ranked(supplier, codes=None):
    return [(v.rank, v.name, v.item_code) for v in iv.ranked_vendors(supplier, codes)]


def test_ranked_empty():
    assert _ranked(None) == []
    assert _ranked("") == []
    assert _ranked(" ; ;") == []


def test_ranked_order_is_listed_order():
    assert _ranked("Raja Foods;RBest;Restaurant Depot") == [
        (1, "Raja Foods", None),
        (2, "RBest", None),
        (3, "Restaurant Depot", None),
    ]


def test_ranked_duplicates_keep_first_position_and_spelling():
    assert _ranked("Real Foods;Radhey Foods;Real Foods") == [(1, "Real Foods", None), (2, "Radhey Foods", None)]
    assert _ranked("Empire Halal;Restaurant Depot;Restaurant Depot;Restaurant Depot") == [
        (1, "Empire Halal", None),
        (2, "Restaurant Depot", None),
    ]
    assert _ranked("raja foods;RBest;Raja Foods") == [(1, "raja foods", None), (2, "RBest", None)]


def test_ranked_whitespace_and_empty_slots_dont_use_a_rank():
    assert _ranked("  Babco ;; ;Chetak  ") == [(1, "Babco", None), (2, "Chetak", None)]


def test_ranked_junk_values_are_kept_where_listed():
    got = _ranked("ACT_SUP_UPDT;Babco;2234")
    assert got == [(1, "ACT_SUP_UPDT", None), (2, "Babco", None), (3, "2234", None)]
    assert [is_junk_vendor(name) for _, name, _ in got] == [True, False, True]


def test_ranked_codes_follow_slots():
    # Raja Foods has no code: empty slots keep their place.
    assert _ranked("Raja Foods;RBest;Restaurant Depot", ";2524;856977005124") == [
        (1, "Raja Foods", None),
        (2, "RBest", "2524"),
        (3, "Restaurant Depot", "856977005124"),
    ]
    # Trailing empty code slot ignored.
    assert _ranked("A;B", "1;2;") == [(1, "A", "1"), (2, "B", "2")]
    # Misaligned: a single code is kept only for a single vendor.
    assert _ranked("A", "1;2") == [(1, "A", None)]
    assert _ranked("A", "7") == [(1, "A", "7")]
    assert _ranked("A;B", "7") == [(1, "A", None), (2, "B", None)]
    # A repeated vendor takes the first code found across its slots.
    assert _ranked("Real Foods;Radhey Foods;Real Foods", ";R1;RF2") == [
        (1, "Real Foods", "RF2"),
        (2, "Radhey Foods", "R1"),
    ]
    assert _ranked("Chetak;Chetak", "C1;C2") == [(1, "Chetak", "C1")]


def test_ranked_matches_vendor_refs_apart_from_junk():
    """The table's (name, code) per vendor equals what the PO / similar
    products code already shows (vendor_refs), which only drops junk."""
    rng = random.Random(5)
    for supplier in _fuzz_strings(1000, seed=9):
        code_raw = ";".join(rng.choice(["", "11", "22"]) for _ in range(rng.randint(0, 5))) or None
        ours = [(v.name, v.item_code) for v in iv.ranked_vendors(supplier, code_raw) if not is_junk_vendor(v.name)]
        theirs = [(r["name"], r["supplier_item_id"]) for r in vendor_refs(supplier, code_raw)]
        assert ours == theirs, (supplier, code_raw)


def test_render_round_trip():
    for supplier, codes in [
        ("Raja Foods;RBest;Restaurant Depot", ";2524;856977005124"),
        ("Chetak;Chetak", None),
        ("A", "7"),
        (None, None),
    ]:
        ranked = iv.ranked_vendors(supplier, codes)
        s, c = iv.render_vendor_fields(reversed(ranked))  # any order in
        assert iv.ranked_vendors(s, c) == ranked
    assert iv.render_vendor_fields([]) == (None, None)
    assert iv.render_vendor_fields(iv.ranked_vendors("A;B")) == ("A;B", None)


# ---------------------------------------------------------------------------
# 3. The table
# ---------------------------------------------------------------------------

D1 = dt.date(2026, 10, 10)
D2 = dt.date(2026, 10, 11)
D3 = dt.date(2026, 10, 12)


def _snap(db, day, item_id, supplier, codes=None, source="retail-items.csv"):
    db.add(
        InventorySnapshot(
            snapshot_date=day, item_id=item_id, name=item_id, supplier=supplier, supplier_item_id=codes, source_filename=source
        )
    )
    db.flush()


def _table(db):
    return {
        item: [(r.rank, r.vendor_name, r.vendor_item_code, r.source, r.source_date) for r in rows]
        for item, rows in iv.load_vendor_table(db).items()
    }


def test_api_prefix_matches_inventory_api():
    assert iv.API_SOURCE_PREFIX == inventory_api.API_SOURCE_PREFIX


def test_refresh_follows_newest_csv_row_not_api_rows(db):
    _snap(db, D1, "tea", "Babco;Chetak")
    _snap(db, D2, "tea", "Chetak;Babco", "C9;")  # newer CSV: order changed
    _snap(db, D3, "tea", "API Vendor", source="api:2026-10-12T08:05:00Z")  # API rows never count
    _snap(db, D1, "rice", "Raja Foods;raja foods;ACT_SUP_UPDT")
    _snap(db, D1, "salt", None)
    stats = iv.refresh_from_csv(db)
    assert stats["items_checked"] == 3
    assert _table(db) == {
        "tea": [(1, "Chetak", "C9", "toast_csv", D2), (2, "Babco", None, "toast_csv", D2)],
        "rice": [(1, "Raja Foods", None, "toast_csv", D1), (2, "ACT_SUP_UPDT", None, "toast_csv", D1)],
    }
    # Same as what the API snapshot would carry forward for each item.
    carried = inventory_api.latest_csv_row_per_item(db)
    for item, rows in _table(db).items():
        assert [r[1] for r in rows] == [v.name for v in iv.ranked_vendors(carried[item].supplier)]
    # Idempotent: nothing to change the second time.
    again = iv.refresh_from_csv(db)
    assert again["items_changed"] == 0 and again["rows_written"] == 0 and again["rows_redated"] == 0
    assert iv.compare_with_snapshots(db)["mismatched_items"] == 0


def test_refresh_same_list_newer_date_only_moves_the_date(db):
    _snap(db, D1, "tea", "Babco;Chetak")
    iv.refresh_from_csv(db)
    ids_before = [r.id for r in db.query(ItemVendor).order_by(ItemVendor.rank)]
    _snap(db, D2, "tea", " Babco ; Chetak")
    stats = iv.refresh_from_csv(db, ["tea"])
    assert stats["items_changed"] == 0 and stats["rows_redated"] == 2
    db.expire_all()
    rows = db.query(ItemVendor).order_by(ItemVendor.rank).all()
    assert [r.id for r in rows] == ids_before
    assert [r.source_date for r in rows] == [D2, D2]


def test_csv_wins_over_manual_rows_but_empty_csv_keeps_them(db):
    now = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc)
    for item in ("tea", "salt"):
        db.add(ItemVendor(item_id=item, vendor_name="Typed Vendor", rank=1, source="manual", created_at=now, updated_at=now))
    _snap(db, D1, "tea", "Babco")
    _snap(db, D1, "salt", "")
    db.flush()
    iv.refresh_from_csv(db)
    t = _table(db)
    assert t["tea"] == [(1, "Babco", None, "toast_csv", D1)]
    assert t["salt"] == [(1, "Typed Vendor", None, "manual", None)]


def test_vendor_dropped_in_toast_is_dropped_from_table(db):
    _snap(db, D1, "tea", "Babco;Chetak")
    iv.refresh_from_csv(db)
    _snap(db, D2, "tea", "")
    iv.refresh_from_csv(db, ["tea"])
    assert _table(db) == {}


def test_items_without_csv_rows_are_left_alone(db):
    now = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc)
    db.add(ItemVendor(item_id="ghost", vendor_name="Typed", rank=1, source="manual", created_at=now, updated_at=now))
    _snap(db, D1, "api-only", "X", source="api:2026-10-10T08:00:00Z")
    db.flush()
    iv.refresh_from_csv(db)
    assert _table(db) == {"ghost": [(1, "Typed", None, "manual", None)]}
    assert iv.refresh_from_csv(db, [])["items_checked"] == 0


def test_refresh_handles_more_items_than_one_sql_batch(db):
    for i in range(iv._CHUNK + 30):
        _snap(db, D1, f"item{i:04d}", "Babco;Chetak" if i % 2 else "Raja Foods")
    stats = iv.refresh_from_csv(db, [f"item{i:04d}" for i in range(iv._CHUNK + 30)])
    assert stats["items_checked"] == iv._CHUNK + 30
    assert stats["rows_written"] == (iv._CHUNK + 30) // 2 * 2 + (iv._CHUNK + 30 + 1) // 2
    assert iv.table_vendors_of(db, "item0001") == ["Babco", "Chetak"]


CSV1 = (
    "Item ID,Name,Supplier,Supplier Item ID,Inventory Quantity\n"
    "tea,Tea,Raja Foods;RBest;Restaurant Depot,;2524;856977005124,5\n"
    "rice,Rice,Chetak;Chetak,,2\n"
    "salt,Salt,,,1\n"
).encode()


def test_csv_upload_fills_the_table(db):
    result = parse_and_store(db, "retail-items.csv", CSV1, D1)
    assert result["rows_loaded"] == 3
    # Additive since: rows_with_case_size (receiving units; null without those columns).
    assert set(result) == {"snapshot_date", "rows_loaded", "rows_skipped", "columns_used", "columns_ignored",
                           "api_rows_replaced", "rows_with_case_size"}
    assert _table(db) == {
        "tea": [
            (1, "Raja Foods", None, "toast_csv", D1),
            (2, "RBest", "2524", "toast_csv", D1),
            (3, "Restaurant Depot", "856977005124", "toast_csv", D1),
        ],
        "rice": [(1, "Chetak", None, "toast_csv", D1)],
    }
    # Next day's file reorders tea's vendors and drops rice's.
    csv2 = CSV1.replace(b"Raja Foods;RBest;Restaurant Depot,;2524;856977005124", b"RBest;Raja Foods,2524;").replace(
        b"Chetak;Chetak", b""
    )
    parse_and_store(db, "retail-items.csv", csv2, D2)
    assert _table(db) == {"tea": [(1, "RBest", "2524", "toast_csv", D2), (2, "Raja Foods", None, "toast_csv", D2)]}
    # An older day's file uploaded late doesn't overwrite the newer list.
    parse_and_store(db, "retail-items.csv", CSV1, D1)
    assert _table(db)["tea"][0][1] == "RBest"
    assert iv.compare_with_snapshots(db)["mismatched_items"] == 0


def test_csv_upload_endpoint_answer_unchanged_and_table_filled(admin_client, db):
    res = admin_client.post(
        "/api/inventory/upload", params={"snapshot_date": D1.isoformat()}, files={"file": ("items.csv", CSV1, "text/csv")}
    )
    assert res.status_code == 200, res.text
    # The same keys as before MT5 (the response model drops api_rows_replaced).
    assert list(res.json()) == ["snapshot_date", "rows_loaded", "rows_skipped", "columns_used", "columns_ignored"]
    db.expire_all()
    assert iv.table_vendors_of(db, "tea") == ["Raja Foods", "RBest", "Restaurant Depot"]


def test_a_failing_table_refresh_never_fails_the_upload(db, monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(iv, "refresh_from_csv", boom)
    result = parse_and_store(db, "retail-items.csv", CSV1, D1)
    assert result["rows_loaded"] == 3
    assert db.query(InventorySnapshot).count() == 3
    assert db.query(ItemVendor).count() == 0
    assert "item_vendors: refresh after CSV upload failed" in caplog.text


def test_a_failing_refresh_rolls_back_only_its_own_writes(db, monkeypatch):
    real_flush = db.flush

    def flaky_refresh(session, item_ids):
        session.add(ItemVendor(item_id="x", vendor_name="half", rank=1, source="toast_csv",
                               created_at=dt.datetime.now(dt.timezone.utc), updated_at=dt.datetime.now(dt.timezone.utc)))
        real_flush()
        raise RuntimeError("mid-way")

    monkeypatch.setattr(iv, "refresh_from_csv", flaky_refresh)
    parse_and_store(db, "retail-items.csv", CSV1, D1)
    assert db.query(InventorySnapshot).count() == 3
    assert db.query(ItemVendor).count() == 0


def test_model_and_capability_untouched():
    from app.pos.capabilities import TOAST_CAPABILITIES, Capability

    # Toast's API has no vendor field: vendors still come from the CSV.
    assert Capability.ITEM_VENDORS not in TOAST_CAPABILITIES
    assert "item_vendors" in Base.metadata.tables


# ---------------------------------------------------------------------------
# 4. Migration 0061
# ---------------------------------------------------------------------------


def _alembic(db_file, *args):
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_file}")
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=BACKEND, env=env, check=True, capture_output=True, text=True
    )


SEED = [
    # (snapshot_date, item_id, supplier, supplier_item_id, source_filename)
    ("2026-10-10", "tea", "Babco;Chetak", None, "retail-items-a.csv"),
    ("2026-10-11", "tea", "Chetak;Babco;chetak", "C9;;", "retail-items-b.csv"),
    ("2026-10-12", "tea", "API", None, "api:2026-10-12T08:05:00Z"),
    ("2026-10-11", "rice", "Raja Foods;RBest;Restaurant Depot", ";2524;856977005124", "retail-items-b.csv"),
    ("2026-10-11", "junk", "ACT_SUP_UPDT;2234", None, None),
    ("2026-06-01", "old", " Vdyas ; Vdyas ", None, "backfill:2026-06"),
    ("2026-10-11", "salt", None, None, "retail-items-b.csv"),
    ("2026-10-12", "api-only", "X", None, "api:2026-10-12T08:05:00Z"),
]


def test_migration_0061_backfill_matches_app_and_round_trips(tmp_path):
    db_file = tmp_path / "m.db"
    _alembic(db_file, "upgrade", "0060")
    con = sqlite3.connect(db_file)
    con.executemany(
        "INSERT INTO inventory_snapshots (snapshot_date, item_id, name, supplier, supplier_item_id, source_filename, uploaded_at)"
        " VALUES (?, ?, ?, ?, ?, ?, '2026-10-12 08:00:00')",
        [(d, i, i, s, c, src) for d, i, s, c, src in SEED],
    )
    con.commit()
    con.close()

    _alembic(db_file, "upgrade", "0061")
    con = sqlite3.connect(db_file)
    try:
        cols = {row[1] for row in con.execute("PRAGMA table_info(item_vendors)")}
        assert cols == {c.name for c in Base.metadata.tables["item_vendors"].columns}
        got = con.execute(
            "SELECT item_id, rank, vendor_name, vendor_item_code, source, source_date FROM item_vendors ORDER BY item_id, rank"
        ).fetchall()
        indexes = {r[1] for r in con.execute("PRAGMA index_list(item_vendors)")}
        stamps = con.execute("SELECT DISTINCT created_at FROM item_vendors").fetchall()
    finally:
        con.close()

    # What the app's own refresh writes for the same rows.
    newest = {}
    for d, item, s, c, src in SEED:
        if src and src.startswith("api:"):
            continue
        if item not in newest or d > newest[item][0]:
            newest[item] = (d, s, c)
    want = sorted(
        (item, v.rank, v.name, v.item_code, "toast_csv", d)
        for item, (d, s, c) in newest.items()
        for v in iv.ranked_vendors(s, c)
    )
    assert got == want
    assert ("tea", 1, "Chetak", "C9", "toast_csv", "2026-10-11") in got
    assert not [g for g in got if g[0] in ("api-only", "salt")]
    assert {"ix_item_vendors_item_id", "ix_item_vendors_vendor_name"} <= indexes
    assert len(stamps) == 1 and stamps[0][0]

    _alembic(db_file, "downgrade", "0060")
    con = sqlite3.connect(db_file)
    assert not con.execute("SELECT name FROM sqlite_master WHERE name = 'item_vendors'").fetchall()
    assert con.execute("SELECT COUNT(*) FROM inventory_snapshots").fetchone()[0] == len(SEED)
    con.close()
    _alembic(db_file, "upgrade", "head")
    con = sqlite3.connect(db_file)
    again = con.execute(
        "SELECT item_id, rank, vendor_name, vendor_item_code, source, source_date FROM item_vendors ORDER BY item_id, rank"
    ).fetchall()
    con.close()
    assert again == got


def test_migration_parser_is_a_faithful_copy_of_the_app_rule():
    import importlib.util

    spec = importlib.util.spec_from_file_location("m0061", BACKEND / "alembic" / "versions" / "0061_item_vendors.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rng = random.Random(21)
    for supplier in _fuzz_strings(1500, seed=13):
        code_raw = ";".join(rng.choice(["", "11", "22", " "]) for _ in range(rng.randint(0, 5))) or None
        assert mod._ranked(supplier, code_raw) == [
            (v.rank, v.name, v.item_code) for v in iv.ranked_vendors(supplier, code_raw)
        ], (supplier, code_raw)


def test_check_script_reports_and_refreshes(tmp_path):
    db_file = tmp_path / "s.db"
    _alembic(db_file, "upgrade", "head")
    con = sqlite3.connect(db_file)
    con.execute(
        "INSERT INTO inventory_snapshots (snapshot_date, item_id, name, supplier, source_filename, uploaded_at)"
        " VALUES ('2026-10-11', 'tea', 'Tea', 'Babco;Chetak', 'retail-items.csv', '2026-10-11 08:00:00')"
    )
    con.commit()
    con.close()
    script = BACKEND / "scripts" / "item_vendors_check.py"
    env = dict(os.environ, DATABASE_URL="sqlite:////nonexistent/never-used.db")
    check = subprocess.run([sys.executable, str(script), "--db", str(db_file)], cwd=BACKEND, env=env, capture_output=True, text=True)
    assert check.returncode == 1, check.stderr  # the row came in after the migration's backfill
    import json

    assert json.loads(check.stdout)["check"]["mismatched_items"] == 1
    fixed = subprocess.run(
        [sys.executable, str(script), "--db", str(db_file), "--refresh"], cwd=BACKEND, env=env, capture_output=True, text=True
    )
    assert fixed.returncode == 0, fixed.stderr
    out = json.loads(fixed.stdout)
    assert out["refresh"]["rows_written"] == 2 and out["check"]["mismatched_items"] == 0
    con = sqlite3.connect(db_file)
    assert con.execute("SELECT vendor_name FROM item_vendors ORDER BY rank").fetchall() == [("Babco",), ("Chetak",)]
    con.close()
    missing = subprocess.run([sys.executable, str(script), "--db", str(tmp_path / "nope.db")], capture_output=True, text=True)
    assert missing.returncode == 2
