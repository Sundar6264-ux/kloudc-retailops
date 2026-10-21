"""The DEMO store for App Review on the shared service (pre-production
2026-10-21; docs/GO_LIVE_RUNBOOK.md "DEMO store").

    python -m app.platform demo-provision [--workdir DIR] [--review-password-file FILE]
    python -m app.platform demo-reset     [--workdir DIR] [--review-password-file FILE]

Same platform, same server, same app as the real stores - so App Review sees
exactly what store staff see - but every number is made up and nothing can
leave the machine:

- ``demo-provision`` (once, idempotent): creates store ``DEMO`` (pack "demo",
  POS "csv" - the upload-only POS, so no POS is ever called) when it doesn't
  exist, turns on ``demo_mode`` (every login is a view-only demo login:
  app/demo.py, app/demo_guard.py) and ``pilot_mode`` (nothing is ever sent:
  no email, WhatsApp, push, AI), then runs a reset.
- ``demo-reset`` (nightly, launchd ``...pilot.demo``; also by hand): builds a
  fresh made-up data set with scripts/demo/generate_demo_data.py (90 days of
  sales ending yesterday, catalog, vendors, invoices, checklists, tasks,
  team chat - no real names, no real products), then swaps it in with the
  verified import (app/ops/store_import.py: copy, row counts and checksums
  compared, all or nothing). While it swaps (seconds) the store is suspended
  with a plain "being refreshed" message; it is resumed even when the swap
  fails. Anything the review login could have changed is gone the next day.

The review login is ``appreview``; its password is in
``--review-password-file`` (default ``<install>/secrets/demo_review_password``,
created once with a random value, mode 600, kept across resets so App
Review's login keeps working; DEMO_REVIEW_PASSWORD overrides it). Give it to
App Review in App Store Connect > App Review Information.

Every step refuses a store other than DEMO, and refuses a DEMO store that is
not in demo mode (a real store can never be overwritten by this command).
"""

import datetime as dt
import logging
import os
import pathlib
import secrets
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[2]
GENERATOR = BACKEND_DIR / "scripts" / "demo" / "generate_demo_data.py"
DEMO_CODE = "DEMO"
DEMO_NAME = "Maple Corner Market (demo)"
DEMO_PACK = "demo"
DEMO_POS = "csv"
DEMO_TZ = "America/New_York"
DEMO_OWNER = "demoowner"  # never used to sign in: a random password nobody knows
REVIEW_USERNAME = "appreview"
REFRESH_MESSAGE = "The demo store is being refreshed with new sample data. Please try again in a minute."
ACTOR = "demo-reset"


class DemoStoreError(RuntimeError):
    pass


def _store():
    from sqlalchemy import select

    from app.platform.db import platform_session
    from app.platform.models import Store

    with platform_session() as db:
        store = db.execute(select(Store).where(Store.code == DEMO_CODE)).scalar_one_or_none()
        if store is None:
            return None
        db.expunge(store)
        return store


def _settings() -> Dict[str, Any]:
    from app.platform.db import platform_session
    from app.platform.stores import settings_values, store_by_code

    with platform_session() as db:
        return settings_values(db, store_by_code(db, DEMO_CODE))


def _turn_on_demo_settings() -> Dict[str, Any]:
    from app.platform.db import platform_session
    from app.platform.stores import put_settings

    with platform_session() as db:
        return put_settings(db, DEMO_CODE, {"demo_mode": True, "pilot_mode": True}, actor=ACTOR,
                            actor_type="system")


def _check_is_demo(store) -> None:
    if store is None:
        raise DemoStoreError("there is no DEMO store yet: run demo-provision first")
    if store.code != DEMO_CODE:
        raise DemoStoreError(f"refusing store {store.code}: only {DEMO_CODE}")
    if store.pos_provider != DEMO_POS:
        raise DemoStoreError(f"store {DEMO_CODE} uses POS {store.pos_provider!r}, not the upload-only {DEMO_POS!r}: "
                             "not a demo store, nothing changed")
    values = _settings()
    if not values.get("demo_mode"):
        raise DemoStoreError(f"store {DEMO_CODE} is not in demo mode: not a demo store, nothing changed")


def default_password_file() -> pathlib.Path:
    root = os.environ.get("PILOT_ROOT") or str(pathlib.Path.home() / "kloudc-retailops")
    return pathlib.Path(root) / "secrets" / "demo_review_password"


def generate(out: pathlib.Path, password_file: pathlib.Path, *, seed: int, days: int, items: int,
             end_date: Optional[dt.date] = None, run: Callable = subprocess.run) -> Dict[str, Any]:
    """The made-up data set into a NEW folder (a separate process: the
    generator points the app at its own SQLite file)."""
    cmd = [sys.executable, str(GENERATOR), "--out", str(out), "--seed", str(seed), "--days", str(days),
           "--items", str(items), "--timezone", DEMO_TZ, "--review-password-file", str(password_file)]
    if end_date is not None:
        cmd += ["--end-date", end_date.isoformat()]
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "TENANCY_MODE")}
    env["TENANCY_MODE"] = "single"
    done = run(cmd, cwd=str(BACKEND_DIR), env=env, capture_output=True, text=True)
    if done.returncode != 0:
        raise DemoStoreError("the demo data generator failed: " + (done.stderr or done.stdout or "")[-800:])
    return {"folder": str(out)}


def _disconnect_demo_sessions(db_name: str) -> int:
    """Close other sessions on the DEMO database (the running server's idle
    pooled ones; its pool checks a connection before use, so it simply opens
    a new one after the resume). DEMO's database only."""
    from sqlalchemy import text

    from app.ops.store_import import _admin_engine

    if "demo" not in db_name.lower():
        raise DemoStoreError(f"refusing to disconnect sessions on {db_name}")
    admin = _admin_engine()
    try:
        with admin.connect() as conn:
            rows = conn.execute(
                text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                     "WHERE datname = :n AND pid <> pg_backend_pid()"),
                {"n": db_name},
            ).all()
        return len(rows)
    finally:
        admin.dispose()


def reset(*, workdir=None, password_file=None, seed: Optional[int] = None, days: int = 90, items: int = 400,
          end_date: Optional[dt.date] = None, log: Callable[[str], None] = print,
          generate_fn: Callable = generate) -> Dict[str, Any]:
    """Fresh made-up data for DEMO (module docstring)."""
    from app.ops.store_import import import_sqlite
    from app.platform.db import platform_session
    from app.platform.stores import resume, suspend

    store = _store()
    _check_is_demo(store)
    password_file = pathlib.Path(password_file).expanduser() if password_file else default_password_file()
    base = pathlib.Path(workdir).expanduser() if workdir else pathlib.Path(tempfile.gettempdir())
    base.mkdir(parents=True, exist_ok=True)
    build = pathlib.Path(tempfile.mkdtemp(prefix="demo-build-", dir=str(base)))
    day = end_date or dt.datetime.now(dt.timezone.utc).date()
    # A new seed each day (stable within the day), so the sample data moves.
    seed = seed if seed is not None else int(day.strftime("%Y%m%d"))
    try:
        log(f"DEMO: building made-up data (seed {seed}, {days} days, {items} items)")
        generate_fn(build / "data", password_file, seed=seed, days=days, items=items, end_date=end_date)
        suspended_here = False
        if store.status == "active":
            with platform_session() as db:
                suspend(db, DEMO_CODE, actor=ACTOR, reason="nightly demo data refresh", message=REFRESH_MESSAGE)
            suspended_here = True
        try:
            closed = _disconnect_demo_sessions(store.db_name)
            log(f"DEMO: swapping the data in ({closed} idle session(s) closed)")
            report = import_sqlite(DEMO_CODE, build / "data" / "retailops.db", workdir=str(build / "import"),
                                   replace=True, keep_owner=False, uploads_from=str(build / "data" / "uploads"),
                                   actor=ACTOR, log=log)
        finally:
            if suspended_here:
                with platform_session() as db:
                    resume(db, DEMO_CODE, actor=ACTOR)
        _turn_on_demo_settings()
    finally:
        shutil.rmtree(build, ignore_errors=True)
    report.pop("table_rows", None)
    return {"store_code": DEMO_CODE, "seed": seed, "review_username": REVIEW_USERNAME,
            "review_password_file": str(password_file), "import": report}


def provision(*, workdir=None, password_file=None, seed: Optional[int] = None, days: int = 90, items: int = 400,
              end_date: Optional[dt.date] = None, log: Callable[[str], None] = print,
              generate_fn: Callable = generate) -> Dict[str, Any]:
    """Create DEMO when missing (idempotent), demo + pilot mode on, then a reset."""
    from app.auth import hash_password
    from app.platform.provisioning import create_store_manually

    store = _store()
    created = False
    if store is None:
        log("DEMO: creating the store (pack demo, upload-only POS)")
        out = create_store_manually(code=DEMO_CODE, name=DEMO_NAME, pack_id=DEMO_PACK, timezone=DEMO_TZ,
                                    pos_provider=DEMO_POS, owner_username=DEMO_OWNER,
                                    owner_password_hash=hash_password(secrets.token_urlsafe(24)),
                                    actor=ACTOR, run="inline")
        if out.get("status") != "active":
            raise DemoStoreError(f"provisioning DEMO did not finish: {out.get('status')} {out.get('error') or ''}")
        created = True
        store = _store()
    elif store.pos_provider != DEMO_POS:
        raise DemoStoreError(f"a store {DEMO_CODE} already exists with POS {store.pos_provider!r}: not touched")
    _turn_on_demo_settings()
    result = reset(workdir=workdir, password_file=password_file, seed=seed, days=days, items=items,
                   end_date=end_date, log=log, generate_fn=generate_fn)
    result["created"] = created
    return result
