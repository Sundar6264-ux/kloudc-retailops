"""Prints (as CSV) the items whose Toast category disagrees with an app-side
category override - the list to fix by hand in Toast (our Toast API access is
read-only). Once Toast agrees, the next inventory upload carries the right
category and the override becomes a no-op.

READ-ONLY: opens the SQLite file with mode=ro (falling back to immutable=1
where a read-only open can't create the WAL index), never through the app's
engine, and never writes anything.

Rules come from the database's `category_overrides` table (active rows);
a database from before migration 0034 has no table, so the built-in Shaz
International rule is used instead. Items are taken from the latest upload.
Category mapping rules (match_type "category") count too: they list the items
Toast still files under an old category name.

    python scripts/category_fix_list.py /path/to/spicetown.db > fix.csv
    python scripts/category_fix_list.py /path/to/spicetown.db --all   # also items already right
"""

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path

DEFAULT_RULES = [
    # (id, match_type, match_value, category_group, category, subcategory)
    (0, "supplier", "Shaz International", "RETAIL", "Snacks & Savories", "Biscuits, Cookies & Rusk"),
]

COLUMNS = [
    "item_name",
    "supplier",
    "current_category_group",
    "current_category",
    "current_subcategory",
    "target_category_group",
    "target_category",
    "target_subcategory",
    "needs_change",
    "item_id",
]


def connect_read_only(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri()
    try:
        con = sqlite3.connect(f"{uri}?mode=ro", uri=True)
        con.execute("SELECT 1 FROM sqlite_master LIMIT 1")
        return con
    except sqlite3.OperationalError:
        print("note: plain read-only open failed; using immutable=1 (may miss the newest un-checkpointed writes)",
              file=sys.stderr)
        return sqlite3.connect(f"{uri}?immutable=1", uri=True)


def load_rules(con: sqlite3.Connection) -> list:
    has_table = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'category_overrides'"
    ).fetchone()
    if not has_table:
        return list(DEFAULT_RULES)
    return con.execute(
        "SELECT id, match_type, match_value, category_group, category, subcategory "
        "FROM category_overrides WHERE active = 1 ORDER BY id"
    ).fetchall()


def _category_match(value):
    """(group, category, subcategory-or-None) of a "category" rule, lower-cased;
    None if the match_value isn't valid (see encode_category_match)."""
    try:
        parsed = json.loads(value or "")
    except ValueError:
        return None
    if not isinstance(parsed, list) or len(parsed) != 3 or not all(isinstance(v, str) for v in parsed[:2]):
        return None
    sub = parsed[2]
    if sub is not None and not isinstance(sub, str):
        return None
    return (parsed[0].strip().lower(), parsed[1].strip().lower(), None if sub is None else sub.strip().lower())


def rule_for(rules: list, item_id: str, supplier: str, categories=(None, None, None)):
    """Same precedence as app/services/category_overrides.py: an item_id rule
    wins; then the newest supplier rule matching ANY ';'-separated supplier
    (case-insensitive, trimmed); then a category rule matching Toast's
    (group, category, subcategory) - an exact-subcategory one before an
    any-subcategory one."""
    item_rules = [r for r in rules if r[1] == "item_id" and (r[2] or "").strip() == (item_id or "").strip()]
    if item_rules:
        return item_rules[-1]
    parts = {p.strip().lower() for p in (supplier or "").split(";") if p.strip()}
    supplier_rules = [r for r in rules if r[1] == "supplier" and (r[2] or "").strip().lower() in parts]
    if supplier_rules:
        return supplier_rules[-1]
    group, category, sub = ((v or "").strip().lower() for v in categories)
    exact, any_sub = [], []
    for r in rules:
        if r[1] != "category":
            continue
        match = _category_match(r[2])
        if match is None or match[:2] != (group, category):
            continue
        if match[2] is None:
            any_sub.append(r)
        elif match[2] == sub:
            exact.append(r)
    if exact:
        return exact[-1]
    return any_sub[-1] if any_sub else None


def fix_rows(con: sqlite3.Connection, include_all: bool = False) -> list:
    rules = load_rules(con)
    latest = con.execute("SELECT MAX(snapshot_date) FROM inventory_snapshots").fetchone()[0]
    if latest is None or not rules:
        return []
    rows = []
    for item_id, name, supplier, group, category, sub in con.execute(
        "SELECT item_id, name, supplier, category_group, category, subcategory "
        "FROM inventory_snapshots WHERE snapshot_date = ?",
        (latest,),
    ):
        rule = rule_for(rules, item_id, supplier, (group, category, sub))
        if rule is None:
            continue
        target = (rule[3], rule[4], rule[5] if rule[5] is not None else sub)
        # "" on a rule = no subcategory, same as Toast's empty one.
        needs_change = (group, category, sub or "") != (target[0], target[1], target[2] or "")
        if needs_change or include_all:
            rows.append(
                {
                    "item_name": name,
                    "supplier": supplier,
                    "current_category_group": group,
                    "current_category": category,
                    "current_subcategory": sub,
                    "target_category_group": target[0],
                    "target_category": target[1],
                    "target_subcategory": target[2],
                    "needs_change": "yes" if needs_change else "no",
                    "item_id": item_id,
                }
            )
    rows.sort(key=lambda r: ((r["item_name"] or "").lower(), r["item_id"]))
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("db_path", type=Path, help="SQLite database file (opened read-only)")
    parser.add_argument("--all", action="store_true", help="also list matched items Toast already has right")
    args = parser.parse_args(argv)
    if not args.db_path.is_file():
        parser.error(f"no such file: {args.db_path}")
    con = connect_read_only(args.db_path)
    try:
        rows = fix_rows(con, include_all=args.all)
    finally:
        con.close()
    writer = csv.DictWriter(sys.stdout, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    print(f"{sum(r['needs_change'] == 'yes' for r in rows)} item(s) to change in Toast", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
