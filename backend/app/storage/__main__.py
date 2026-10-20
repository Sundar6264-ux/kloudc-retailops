"""Storage commands (docs/WEBSITE_READY_CONTRACT.md C1).

    python -m app.storage migrate --to s3 [--store CODE] [--dry-run] [--verify]
    python -m app.storage status
"""

import argparse
import json
import logging
import sys
from typing import List, Optional


def _boot() -> None:
    import app.main  # noqa: F401 - settings + (multi mode) the platform registry


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m app.storage")
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("migrate", help="copy local store files to S3 / R2")
    m.add_argument("--to", required=True, choices=["s3"])
    m.add_argument("--store", default=None, help="one store ID (default: every store)")
    m.add_argument("--dry-run", action="store_true")
    m.add_argument("--verify", action="store_true")
    sub.add_parser("status", help="the configured backend (no keys)")
    args = p.parse_args(argv)
    _boot()
    if args.cmd == "status":
        from app.storage import storage_status

        print(json.dumps(storage_status(), indent=2))
        return 0
    from app.storage.migrate import main_migrate

    return main_migrate(args)


if __name__ == "__main__":
    sys.exit(main())
