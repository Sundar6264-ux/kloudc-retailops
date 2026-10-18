"""Evaluate the similar-products matcher against the hand-labelled set.

    # against the frozen fixture (what the pytest uses)
    .venv312/bin/python -m tests.similar_products_eval
    # against a real DB (read-only; use a COPY of the dev DB)
    .venv312/bin/python -m tests.similar_products_eval --db /path/to/copy.db
    # print every query's results
    .venv312/bin/python -m tests.similar_products_eval --verbose

Labels (`tests/data/similar_products_labels.json`) were made by hand, by a
separate labeller that never saw the matcher: for each query item, every
other item in the store graded 2 (same product: other brand / size) or 1
(close relative a manager would want to see). Everything else is 0.

Metrics (reported over queries that have at least one relevant item):
- precision@5: of the first (up to) 5 results shown, the share graded 1-2.
- strict precision@5: hits in the top 5 / min(5, number of relevant items)
  - so returning nothing, or too little, counts against it.
- recall@8 (grade 2): grade-2 items found in the 8 results the UI shows /
  min(8, number of grade-2 items) - a query with 16 same-product items can
  only ever show 8 of them.
- raw recall@8 (grade 2): the same divided by ALL grade-2 items.
- nothing-found rate: queries with relevant items where nothing came back.
Queries labelled with no relevant items report how many results were shown
anyway (false alarms).
"""

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

HERE = Path(__file__).resolve().parent
LABELS_PATH = HERE / "data" / "similar_products_labels.json"
FIXTURE_PATH = HERE / "data" / "similar_products_catalog.tsv"
FIXTURE_COLUMNS = ["item_id", "name", "category_group", "category", "subcategory", "supplier", "price"]


def load_labels(path: Path = LABELS_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_fixture(path: Path = FIXTURE_PATH):
    from app.services.similar_products import CatalogItem

    items = []
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            items.append(
                CatalogItem(
                    item_id=row["item_id"],
                    name=row["name"],
                    category_group=row["category_group"] or None,
                    category=row["category"] or None,
                    subcategory=row["subcategory"] or None,
                    supplier=row["supplier"] or None,
                    price=float(row["price"]) if row["price"] else None,
                )
            )
    return items


def load_db(db_path: str):
    from sqlalchemy import create_engine, func, select
    from sqlalchemy.orm import Session

    from app.models import InventorySnapshot
    from app.services.similar_products import _snap_to_item

    engine = create_engine(f"sqlite:///file:{db_path}?mode=ro&uri=true")
    with Session(engine) as db:
        latest = db.execute(select(func.max(InventorySnapshot.snapshot_date))).scalar()
        rows = db.execute(select(InventorySnapshot).where(InventorySnapshot.snapshot_date == latest)).scalars()
        return [_snap_to_item(r) for r in rows]


def split(labels: dict, part: Optional[str]) -> dict:
    """Queries alternate dev / test in file order: rules were tuned on "dev"
    only, "test" is held out."""
    if part not in ("dev", "test"):
        return labels
    keep = 0 if part == "dev" else 1
    return {**labels, "queries": [q for i, q in enumerate(labels["queries"]) if i % 2 == keep]}


def evaluate(index, labels: dict, k: int = 5, shown: int = 8, verbose: bool = False) -> Dict[str, float]:
    p_at_k: List[float] = []
    strict_p: List[float] = []
    recall: List[float] = []
    raw_recall: List[float] = []
    empty = 0
    no_match_queries = 0
    false_alarms = 0
    graded_queries = 0
    for q in labels["queries"]:
        grades = {r["item_id"]: r["grade"] for r in q["relevant"]}
        results = index.similar(q["item_id"], limit=shown)
        ids = [m.item.item_id for m in results]
        if verbose:
            print(f"\n## {q['name']}  ({q.get('topic')})")
            for m in results:
                print(f"   [{grades.get(m.item.item_id, 0)}] {m.score:.2f} {m.item.name}  - {m.reason}")
            missed = [r["name"] for r in q["relevant"] if r["grade"] == 2 and r["item_id"] not in ids]
            if missed:
                print(f"   missed grade-2: {missed}")
        if not grades:
            no_match_queries += 1
            false_alarms += len(ids)
            continue
        graded_queries += 1
        top = ids[:k]
        hits = sum(1 for i in top if grades.get(i, 0) >= 1)
        if top:
            p_at_k.append(hits / len(top))
        else:
            empty += 1
        strict_p.append(hits / min(k, len(grades)))
        strong = [i for i, g in grades.items() if g == 2]
        if strong:
            found = sum(1 for i in strong if i in ids)
            recall.append(found / min(shown, len(strong)))
            raw_recall.append(found / len(strong))

    def mean(xs: List[float]) -> float:
        return round(sum(xs) / len(xs), 3) if xs else 0.0

    return {
        "queries": len(labels["queries"]),
        "graded_queries": graded_queries,
        "precision_at_5": mean(p_at_k),
        "strict_precision_at_5": mean(strict_p),
        "recall_at_8_grade2": mean(recall),
        "raw_recall_at_8_grade2": mean(raw_recall),
        "nothing_found_rate": round(empty / graded_queries, 3) if graded_queries else 0.0,
        "no_match_queries": no_match_queries,
        "false_alarms_on_no_match_queries": false_alarms,
    }


def main(argv: Optional[List[str]] = None) -> int:
    sys.path.insert(0, str(HERE.parent))
    from app.services.similar_products import SimilarIndex

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", help="SQLite DB to read (read-only); default = frozen fixture")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--part", choices=["all", "dev", "test"], default="all")
    args = parser.parse_args(argv)
    items = load_db(args.db) if args.db else load_fixture()
    index = SimilarIndex(items)
    labels = load_labels()
    for part in (["dev", "test", "all"] if args.part == "all" else [args.part]):
        metrics = evaluate(index, split(labels, part), verbose=args.verbose and part != "all")
        print(part, json.dumps(metrics))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
