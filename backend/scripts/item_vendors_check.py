"""Check (or re-sync) the item_vendors table against the inventory snapshots
(multi-store M1, MT5 - docs/MULTISTORE_M1_CONTRACT.md "As built - MT5").

The table should hold, for every item, the vendors of its newest CSV
snapshot row (one row per vendor, rank = listed order). The CSV upload keeps
it in step; this script shows how far it is from that, and can put it right
if an upload's refresh ever failed (the upload logs
"item_vendors: refresh after CSV upload failed").

    python scripts/item_vendors_check.py --db /path/to/storeops.db            # read-only check
    python scripts/item_vendors_check.py --db /path/to/storeops.db --refresh  # re-sync, then check

The database path is required (the app's .env is never used to pick it).
The check only reads; --refresh writes only the item_vendors table.
"""

import argparse
import json
import os
import pathlib
import sys
import time


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True, help="path to the SQLite database")
    parser.add_argument("--refresh", action="store_true", help="re-sync the table from the snapshots (writes)")
    args = parser.parse_args(argv)

    db_path = pathlib.Path(args.db).resolve()
    if not db_path.is_file():
        print(f"No database at {db_path}", file=sys.stderr)
        return 2
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ.setdefault("SCHEDULER_ENABLED", "false")
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

    from app.db import SessionLocal, engine
    from app.services import item_vendors

    assert engine.url.database == str(db_path), engine.url.database
    db = SessionLocal()
    try:
        out = {"database": str(db_path)}
        if args.refresh:
            started = time.monotonic()
            out["refresh"] = item_vendors.refresh_from_csv(db)
            db.commit()
            out["refresh_seconds"] = round(time.monotonic() - started, 2)
        out["check"] = item_vendors.compare_with_snapshots(db)
        db.rollback()
    finally:
        db.close()
    print(json.dumps(out, indent=2, default=str))
    return 0 if out["check"]["mismatched_items"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
