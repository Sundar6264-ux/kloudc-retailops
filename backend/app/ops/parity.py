"""Daily parity check for a store in pilot: its key numbers in the shared
service against a reference SQLite database (the system the store still
runs on), read-only on both sides.

    python -m app.platform parity CODE --reference /path/store.db [--days 14] [--log-dir DIR]

Compared per closed business day (yesterday and the N-1 days before, store
time), from the sales lines both systems sync from the same POS:
lines, orders, quantity, net sales, tax and voided lines. Any difference is
a parity difference (exit code 1). Shown side by side but not counted as
differences (people still use the reference system day to day, so these
are expected to drift): the newest inventory upload and its item count,
users, invoice and checklist submissions and placed orders in the window,
and the sales sync cursor.

Every run appends one JSON line to ``<log dir>/parity-<CODE>.jsonl`` (LOG_DIR
or --log-dir; nothing is written without one), logs a summary line, and
records ``parity.run`` in the platform audit log (counts and dates only -
no amounts). Nothing is ever written to either store database, and nothing
is sent anywhere.
"""

import datetime as dt
import json
import logging
import pathlib
import sqlite3
import sys
import time
import zoneinfo
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[2]
MONEY_TOLERANCE = 0.005
QTY_TOLERANCE = 0.0005
DAY_METRICS = ("lines", "orders", "quantity", "net_sales", "tax", "voided_lines")


class ParityError(RuntimeError):
    pass


_DAY_SQL = (
    "SELECT business_date, "
    "sum(CASE WHEN voided = {f} THEN 1 ELSE 0 END) AS lines, "
    "count(DISTINCT CASE WHEN voided = {f} THEN toast_order_guid END) AS orders, "
    "sum(CASE WHEN voided = {f} THEN quantity ELSE 0 END) AS quantity, "
    "sum(CASE WHEN voided = {f} THEN net_price ELSE 0 END) AS net_sales, "
    "sum(CASE WHEN voided = {f} THEN tax_amount ELSE 0 END) AS tax, "
    "sum(CASE WHEN voided = {t} THEN 1 ELSE 0 END) AS voided_lines "
    "FROM orders WHERE {top_level} AND business_date >= {p} AND business_date <= {p} "
    "GROUP BY business_date ORDER BY business_date"
)


def _top_level_sql() -> str:
    """Order.top_level() (real sold items, not modifier lines) as SQL text -
    the one definition, the same on SQLite and PostgreSQL."""
    from app.models import Order

    return str(Order.top_level().compile(compile_kwargs={"literal_binds": True}))


def _iso(value) -> str:
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()[:10]
    return str(value)[:10]


def _norm_day(row) -> Dict[str, Any]:
    return {
        "lines": int(row[1] or 0),
        "orders": int(row[2] or 0),
        "quantity": round(float(row[3] or 0), 4),
        "net_sales": round(float(row[4] or 0), 2),
        "tax": round(float(row[5] or 0), 2),
        "voided_lines": int(row[6] or 0),
    }


class _Side:
    """One database, read-only: run(sql_template, params) -> rows."""

    def __init__(self, kind: str, conn):
        self.kind = kind
        self.conn = conn

    def sql(self, template: str) -> str:
        top = _top_level_sql()
        if self.kind == "sqlite":
            return template.format(f="0", t="1", p="?", top_level=top)
        return template.format(f="false", t="true", p="%s", top_level=top)

    def rows(self, template: str, params: Tuple = ()) -> List[tuple]:
        if self.kind == "sqlite":
            params = tuple(p.isoformat() if isinstance(p, dt.date) else p for p in params)
            return self.conn.execute(self.sql(template), params).fetchall()
        with self.conn.cursor() as cur:
            cur.execute(self.sql(template), params)
            return cur.fetchall()

    def scalar(self, template: str, params: Tuple = ()):
        try:
            rows = self.rows(template, params)
        except Exception:  # noqa: BLE001 - a missing table: not comparable
            if self.kind != "sqlite":
                self.conn.rollback()
            return None
        return rows[0][0] if rows else None


def open_reference(path) -> _Side:
    p = pathlib.Path(path).expanduser().resolve()
    if not p.is_file():
        raise ParityError(f"reference database {p} not found")
    from app.ops.store_import import connect_ro

    return _Side("sqlite", connect_ro(p))


def open_store(url: str) -> _Side:
    import psycopg

    scripts = BACKEND_DIR / "scripts" / "pg"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import sqlite_pg  # noqa: PLC0415

    conn = psycopg.connect(sqlite_pg.libpq_conninfo(url))
    conn.read_only = True
    return _Side("postgres", conn)


def day_numbers(side: _Side, first: dt.date, last: dt.date) -> Dict[str, Dict[str, Any]]:
    return {_iso(r[0]): _norm_day(r) for r in side.rows(_DAY_SQL, (first, last))}


def info_numbers(side: _Side, first: dt.date, last: dt.date) -> Dict[str, Any]:
    newest = side.scalar("SELECT max(snapshot_date) FROM inventory_snapshots")
    items = None
    if newest is not None:
        items = side.scalar("SELECT count(*) FROM inventory_snapshots WHERE snapshot_date = {p}",
                            (dt.date.fromisoformat(_iso(newest)),))
    start = dt.datetime.combine(first, dt.time.min)
    end = dt.datetime.combine(last + dt.timedelta(days=1), dt.time.min)
    if side.kind == "sqlite":
        start_p, end_p = start.isoformat(sep=" "), end.isoformat(sep=" ")
    else:
        start_p, end_p = start, end
    return {
        "newest_inventory_upload": _iso(newest) if newest is not None else None,
        "inventory_items": items,
        "users": side.scalar("SELECT count(*) FROM users"),
        "invoice_submissions": side.scalar(
            "SELECT count(*) FROM invoice_submissions WHERE created_at >= {p} AND created_at < {p}", (start_p, end_p)),
        "checklist_submissions": side.scalar(
            "SELECT count(*) FROM checklist_submissions WHERE created_at >= {p} AND created_at < {p}",
            (start_p, end_p)),
        "placed_orders": side.scalar(
            "SELECT count(*) FROM placed_orders WHERE placed_at >= {p} AND placed_at < {p}", (start_p, end_p)),
        "sales_cursor": side.scalar("SELECT max(cursor) FROM sync_cursors WHERE stream = 'orders_business_date'"),
    }


def _close(a, b, metric: str) -> bool:
    if isinstance(a, int) and isinstance(b, int):
        return a == b
    tol = QTY_TOLERANCE if metric == "quantity" else MONEY_TOLERANCE
    return abs(float(a) - float(b)) <= tol


def compare(store: Dict[str, Dict[str, Any]], reference: Dict[str, Dict[str, Any]], days: List[str]) -> List[Dict]:
    out = []
    empty = {m: 0 for m in DAY_METRICS}
    for day in days:
        mine, ref = store.get(day, empty), reference.get(day, empty)
        for metric in DAY_METRICS:
            if not _close(mine[metric], ref[metric], metric):
                out.append({"date": day, "metric": metric, "store": mine[metric], "reference": ref[metric]})
    return out


def run_parity(
    code: str,
    reference_path,
    *,
    days: int = 14,
    today: Optional[dt.date] = None,
    log_dir: Optional[str] = None,
    store_url: Optional[str] = None,
    timezone: Optional[str] = None,
    audit_run: bool = True,
) -> Dict[str, Any]:
    """Compare; returns the report (``ok`` False when any sales number differs)."""
    started = time.monotonic()
    code = (code or "").strip().upper()
    if days < 1 or days > 120:
        raise ParityError("--days must be 1-120")
    if store_url is None or timezone is None:
        from sqlalchemy import select

        from app.platform import bridge
        from app.platform.db import platform_session
        from app.platform.models import Store

        with platform_session() as pdb:
            store = pdb.execute(select(Store).where(Store.code == code)).scalar_one_or_none()
            if store is None:
                raise ParityError(f"no store {code}")
            store_url = store_url or bridge.store_url(store.db_name, store.db_host_ref)
            timezone = timezone or store.timezone
    tz = zoneinfo.ZoneInfo(timezone or "America/New_York")
    today = today or dt.datetime.now(tz).date()
    last = today - dt.timedelta(days=1)
    first = today - dt.timedelta(days=days)
    window = [(first + dt.timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]

    ref = open_reference(reference_path)
    pilot = open_store(store_url)
    try:
        ref_days, pilot_days = day_numbers(ref, first, last), day_numbers(pilot, first, last)
        ref_info, pilot_info = info_numbers(ref, first, last), info_numbers(pilot, first, last)
    finally:
        ref.conn.close()
        pilot.conn.close()
    differences = compare(pilot_days, ref_days, window)
    report = {
        "store_code": code,
        "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "reference": pathlib.Path(reference_path).name,
        "from": first.isoformat(),
        "to": last.isoformat(),
        "days_compared": len(window),
        "differences": differences,
        "days_with_differences": sorted({d["date"] for d in differences}),
        "info": {"store": pilot_info, "reference": ref_info},
        "totals": {
            "store_lines": sum(v["lines"] for v in pilot_days.values()),
            "reference_lines": sum(v["lines"] for v in ref_days.values()),
        },
        "ok": not differences,
        "seconds": round(time.monotonic() - started, 2),
    }
    _write_log(report, log_dir)
    if differences:
        logger.warning("Parity %s: %d difference(s) on %s", code, len(differences), ", ".join(report["days_with_differences"]))
    else:
        logger.info("Parity %s: OK, %d days compared (%s..%s)", code, len(window), first, last)
    if audit_run:
        try:
            from app.platform.audit import audit

            audit("parity.run", actor_type="system", actor_name="parity", store_code=code,
                  details={"ok": report["ok"], "from": report["from"], "to": report["to"],
                           "differences": len(differences), "days_with_differences": report["days_with_differences"]})
        except Exception:  # noqa: BLE001 - the log line above is the record that matters
            logger.exception("parity audit row failed")
    return report


def _write_log(report: Dict[str, Any], log_dir: Optional[str]) -> Optional[pathlib.Path]:
    folder = (log_dir or "").strip()
    if not folder:
        from app.config import process_settings

        folder = (process_settings().log_dir or "").strip()
    if not folder:
        return None
    path = pathlib.Path(folder).expanduser() / f"parity-{report['store_code']}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(report, sort_keys=True, default=str) + "\n")
    return path
