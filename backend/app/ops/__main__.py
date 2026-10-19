"""Operations commands (docs/PILOT_RUNBOOK.md).

    python -m app.ops check-config     # the production start-up checks; exit 1 on an error
    python -m app.ops ready            # the /api/ready checks from the command line; exit 1 when not ready

Store onboarding (import, private pack, pilot mode, parity) is under
``python -m app.platform`` (it needs the platform database).
"""

import argparse
import json
import os
import sys
from typing import List, Optional


def cmd_check_config(args) -> int:
    from app.ops.prod_checks import check_config, is_production

    findings = check_config()
    production = is_production()
    for f in findings:
        print(f.line())
    errors = [f for f in findings if f.level == "error"]
    where = "production" if production else "not production (APP_ENV): errors would refuse to start in production"
    print(f"{len(errors)} error(s), {len(findings) - len(errors)} warning(s); {where}")
    return 1 if errors else 0


def cmd_ready(args) -> int:
    from app.ops.health import readiness

    ok, checks = readiness()
    print(json.dumps({"status": "ready" if ok else "not_ready", "checks": checks}, indent=1))
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.ops", description="KloudC RetailOps operations commands")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check-config").set_defaults(func=cmd_check_config)
    sub.add_parser("ready").set_defaults(func=cmd_ready)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    os.environ.setdefault("SCHEDULER_ENABLED", "false")
    sys.exit(main())
