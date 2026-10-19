"""Row-by-row comparison of two versions of the POS sync code on a prod copy
(multi-store M1 batch MT3: the old Toast-only sync vs the POS-neutral one).

    # run each code tree on the same copy (each run gets its own scratch copy)
    python scripts/parity/sync_compare.py run --db COPY.db --code OLD/backend --out OUT/old
    python scripts/parity/sync_compare.py run --db COPY.db --code NEW/backend --out OUT/new
    # compare what they wrote and answered
    python scripts/parity/sync_compare.py diff OUT/old OUT/new

`--code` is a backend folder (its `app` package is the one that runs); this
script and the parity helpers next to it are the same for both runs.

What a run does, in a child process booted like parity_record.py (network
blocked, app threads not run, every outside service blank, "now" frozen at
noon store time the day after the newest synced business date):

1. Recorded Toast payloads. The app stores no raw order JSON, so the
   ordersBulk answers are rebuilt from the copy's own `orders` rows - one
   Toast order per toast_order_guid, its checks, selections and modifiers
   nested by parent_selection_guid - shaped exactly like Toast's JSON
   (timestamps "...000+0000", numbers as Toast sends them) and varied
   deterministically: voids at order / check / selection level, deleted
   orders and checks, payment time on the check or only on the order, a
   few bad timestamps, int and float quantities, open orders with tab names,
   promised times, servers. The menus and stock answers are the REAL ones
   recorded from Toast on Oct 11 (`--menus` / `--stock`). Analytics day
   totals are computed from the rebuilt answers, some days nudged off.
2. Every Toast call is replaced at the old module paths (the seams the tests
   use): `sales_sync` / `open_orders` / `toast_client`
   `.fetch_orders_for_business_date`, `.fetch_employees`, the Analytics
   client's `menu_day_totals` / `day_metrics` / `is_configured`, and the
   Toast HTTP GET (`app.pos.adapters.toast.client.toast_get`) for stock and
   menus. Anything else trying the network fails (blocked).
3. The scenario: sync every business date; two 429s answered by retry; a
   second pass with edits (lines removed -> pruned, orders voided, a check
   deleted, a price changed, two empty answers -> not pruned, one failure);
   the open checks full scan + today refresh + live view; Items Sold with
   today live; the sales accuracy re-pull twice; the inventory API snapshot
   written for a date without a CSV, compared for the CSV date, refused when
   the stock list is empty; the hourly menu re-check (unchanged, then
   forced); Compare now; the live stock overlay.
4. Dumps every row of `orders`, `open_order_cache`, `job_runs`,
   `toast_menu_items` and the touched `inventory_snapshots` dates (all
   columns, as SQLite stores them) plus every function's answer.

`diff` compares the two dumps row by row and answer by answer. The verdict
line is `DIFFERENCES: <n>`.
"""

import argparse
import copy
import datetime as dt
import decimal
import hashlib
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import zoneinfo
from collections import OrderedDict, defaultdict
from typing import Dict, List, Optional

HERE = pathlib.Path(__file__).resolve().parent
STORE_TZ = "America/New_York"
UTC = dt.timezone.utc
TABLES = ("orders", "open_order_cache", "job_runs", "toast_menu_items", "inventory_snapshots")
# Tables from the M1 migrations (0060 MT4, 0061 MT5): dumped when the copy has
# them (run on a copy migrated with `alembic upgrade head`), so the merged
# sync's neutral bookkeeping is compared too. table -> ORDER BY.
OPTIONAL_TABLES = {
    "sync_cursors": "source, stream",
    "pos_item_map": "source, external_id",
    "item_vendors": "item_id, vendor_name",
}
# Recorded Toast answers (business data, outside git): SYNC_COMPARE_MENUS /
# SYNC_COMPARE_STOCK, or --menus / --stock.
DEFAULT_MENUS = os.environ.get("SYNC_COMPARE_MENUS", "")
DEFAULT_STOCK = os.environ.get("SYNC_COMPARE_STOCK", "")

sys.path.insert(0, str(HERE))


def _h(*parts) -> int:
    return int(hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:12], 16)


# ---------------------------------------------------------------------------
# Rebuilding Toast ordersBulk answers from `orders` rows
# ---------------------------------------------------------------------------


def _parse_db_time(value) -> Optional[dt.datetime]:
    if value is None:
        return None
    t = dt.datetime.fromisoformat(str(value))
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)


def _toast_time(t: Optional[dt.datetime], variant: int = 0) -> Optional[str]:
    if t is None:
        return None
    t = t.astimezone(UTC)
    ms = t.microsecond // 1000
    if variant == 1:
        return f"{t:%Y-%m-%dT%H:%M:%S}.{ms:03d}+00:00"
    if variant == 2:
        local = t.astimezone(zoneinfo.ZoneInfo(STORE_TZ))
        return f"{local:%Y-%m-%dT%H:%M:%S}.{ms:03d}{local:%z}"
    return f"{t:%Y-%m-%dT%H:%M:%S}.{ms:03d}+0000"


def _number(value, h: int):
    """Toast sends JSON numbers; a whole number sometimes comes as an int."""
    if value is None:
        return None
    if float(value).is_integer() and h % 2 == 0:
        return int(value)
    return value


def build_answers(db_path: str, today: dt.date) -> Dict:
    """{"pass1": {date: [order json]}, "pass2": {...}, "employees": [...],
    "open_guids": [...]} - deterministic for a given copy."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM orders ORDER BY business_date, toast_order_guid, id").fetchall()
    cached_open = {r[0] for r in con.execute("SELECT toast_order_guid FROM open_order_cache")}
    con.close()

    by_order: "OrderedDict[str, list]" = OrderedDict()
    for r in rows:
        by_order.setdefault(r["toast_order_guid"], []).append(r)

    pass1: Dict[str, list] = defaultdict(list)
    open_guids = []
    for oguid, orows in by_order.items():
        day = orows[0]["business_date"]
        h = _h(oguid)
        opened = _parse_db_time(orows[0]["opened_at"])
        recent = dt.date.fromisoformat(day) >= today - dt.timedelta(days=40)
        is_open = oguid in cached_open or (recent and h % 97 == 0)
        order = {"guid": oguid}
        variant = 1 if h % 13 == 0 else (2 if h % 19 == 0 else 0)
        if h % 31 == 0:
            order["openedDate"] = "bad-time"
        elif opened is not None:
            order["openedDate"] = _toast_time(opened, variant)
        if h % 11 != 0 and opened is not None:
            order["createdDate"] = _toast_time(opened - dt.timedelta(minutes=3 + h % 40))
        if orows[0]["dining_option"] is not None or h % 5:
            order["diningOption"] = {"guid": orows[0]["dining_option"]} if orows[0]["dining_option"] else None
        order["displayNumber"] = str(h % 300)
        order["server"] = {"guid": f"emp-{h % 6}"} if h % 9 else None
        order["numberOfGuests"] = (h % 4) or None
        order["approvalStatus"] = ("APPROVED", "NEEDS_APPROVAL", None)[h % 3]
        if not is_open:
            order["closedDate"] = _toast_time((opened or _parse_db_time(orows[0]["paid_at"]) or dt.datetime(2026, 6, 1, tzinfo=UTC)) + dt.timedelta(minutes=30))
        else:
            open_guids.append(oguid)
            if h % 3 == 0 and opened is not None:
                order["promisedDate"] = _toast_time(opened + dt.timedelta(hours=h % 30))
            elif h % 3 == 1 and opened is not None:
                order["estimatedFulfillmentDate"] = _toast_time(opened + dt.timedelta(minutes=h % 90))

        # checks, in first-seen order
        checks: "OrderedDict[str, list]" = OrderedDict()
        for r in orows:
            checks.setdefault(r["toast_check_guid"], []).append(r)
        all_void = all(r["voided"] for r in orows)
        void_kind = h % 5 if all_void else None  # 0 order voided, 1 order deleted, 2/3 check level, 4 lines
        if void_kind == 0:
            order["voided"] = True
        elif void_kind == 1:
            order["deleted"] = True
        paid_on_order = h % 7 == 0 and len({r["paid_at"] for r in orows}) == 1
        if paid_on_order and orows[0]["paid_at"] is not None:
            order["paidDate"] = _toast_time(_parse_db_time(orows[0]["paid_at"]))
        order["checks"] = []
        for cguid, crows in checks.items():
            ch = _h(cguid)
            check = {"guid": cguid}
            paid = _parse_db_time(crows[0]["paid_at"])
            if paid is not None and not paid_on_order and not (all_void and void_kind is not None and void_kind < 4 and h % 2):
                check["paidDate"] = _toast_time(paid)
            if void_kind == 2:
                check["voided"] = True
            elif void_kind == 3:
                check["deleted"] = True
            if is_open and ch % 3 == 0:
                check["tabName"] = ("Ravi", " ravi ", "Priya K", order["displayNumber"], "")[ch % 5]
            selections: Dict[str, dict] = {}
            top = []
            for r in crows:
                sh = _h(r["toast_selection_guid"])
                sel = {"guid": r["toast_selection_guid"]}
                if r["item_guid"] is not None or sh % 4:
                    sel["item"] = {"guid": r["item_guid"]} if r["item_guid"] is not None else None
                sel["displayName"] = r["item_name"]
                sel["quantity"] = _number(r["quantity"], sh)
                sel["price"] = _number(r["net_price"], sh >> 3)
                if r["tax_amount"] or sh % 3:
                    sel["tax"] = _number(r["tax_amount"], sh >> 5)
                if r["voided"] and (void_kind is None or void_kind == 4):
                    sel["voided"] = True
                elif sh % 4 == 0:
                    sel["voided"] = False
                selections[r["toast_selection_guid"]] = sel
                parent = r["parent_selection_guid"]
                if parent is not None and parent in selections:
                    selections[parent].setdefault("modifiers", []).append(sel)
                else:
                    top.append(sel)
            check["selections"] = top
            check["totalAmount"] = round(sum(float(r["net_price"] or 0) + float(r["tax_amount"] or 0) for r in crows if r["parent_selection_guid"] is None), 2)
            order["checks"].append(check)
        pass1[day].append(order)

    # Pass 2: Toast changed some closed days after we synced them.
    days = sorted(pass1)
    pass2 = copy.deepcopy(pass1)
    edits = []
    for i, day in enumerate(days[-40:]):
        orders = pass2[day]
        dh = _h(day)
        if i in (5, 17):
            pass2[day] = []  # Toast hiccup: an empty answer must never prune
            edits.append((day, "empty answer"))
            continue
        if not orders:
            continue
        o = orders[dh % len(orders)]
        kind = dh % 5
        if kind == 0 and o["checks"] and o["checks"][0]["selections"]:
            o["checks"][0]["selections"].pop(0)
            edits.append((day, "line removed"))
        elif kind == 1:
            o["voided"] = True
            edits.append((day, "order voided"))
        elif kind == 2 and o["checks"]:
            o["checks"][-1]["deleted"] = True
            edits.append((day, "check deleted"))
        elif kind == 3 and o["checks"] and o["checks"][0]["selections"]:
            o["checks"][0]["selections"][0]["price"] = round(float(o["checks"][0]["selections"][0].get("price") or 0) * 0.9, 2)
            edits.append((day, "price changed"))
        else:
            orders.pop(dh % len(orders))
            edits.append((day, "order moved away"))
    failing = days[-3]

    employees = [
        {"guid": "emp-0", "firstName": "Asha", "lastName": "R"},
        {"guid": "emp-1", "firstName": "Vikram", "lastName": "S", "chosenName": "Vik"},
        {"guid": "emp-2", "chosenName": "Manager"},
        {"guid": "emp-3", "firstName": "Old", "lastName": "Staff", "deleted": True},
        {"guid": "emp-4"},
        {"firstName": "No", "lastName": "Guid"},
    ]
    return {
        "pass1": dict(pass1),
        "pass2": dict(pass2),
        "edits": edits,
        "failing_day": failing,
        "employees": employees,
        "open_guids": open_guids,
    }


def analytics_totals(answers: Dict[str, list], days: List[str]):
    """(menu, metrics) as the Analytics client returns them: {date: {...}},
    top-level non-voided sums of the answers, a few days nudged off."""
    menu, metrics = {}, {}
    for i, day in enumerate(days):
        orders = answers.get(day, [])
        net = qty = 0.0
        count = 0
        for o in orders:
            o_void = bool(o.get("voided")) or bool(o.get("deleted"))
            had = False
            for c in o.get("checks") or []:
                c_void = bool(c.get("voided")) or bool(c.get("deleted"))
                for s in c.get("selections") or []:
                    if o_void or c_void or s.get("voided"):
                        continue
                    net += float(s.get("price") or 0)
                    qty += float(s.get("quantity") or 0)
                    had = True
            count += 1 if had else 0
        d = dt.date.fromisoformat(day)
        nudge = (0.0, 0.0, 4.25, 0.0, 0.0, 0.0, 0.004, 0.0, 12.0, 0.0)[i % 10]
        if i % 10 != 9:
            menu[d] = {"netSalesAmount": round(net + nudge, 4), "quantitySold": round(qty, 4), "grossSalesAmount": round(net * 1.02, 4),
                       "discountAmount": round(net * 0.02, 4), "voidAmount": 0.0, "refundAmount": 0.0}
        if i % 10 != 4:
            metrics[d] = {"netSalesAmount": round(net, 4), "grossSalesAmount": round(net * 1.03, 4), "discountAmount": 1.5,
                          "voidOrdersAmount": 0.0, "refundAmount": 0.0, "ordersCount": float(count + (1 if i % 10 == 7 else 0)),
                          "voidOrdersCount": 0.0, "openOrdersCount": float(i % 3)}
    return menu, metrics


# ---------------------------------------------------------------------------
# Child: the scenario
# ---------------------------------------------------------------------------


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (dt.datetime, dt.date)):
        return {"__time__": value.isoformat(), "tz": str(getattr(value, "tzinfo", None))}
    if isinstance(value, decimal.Decimal):
        return {"__decimal__": str(value)}
    if isinstance(value, float):
        return {"__float__": repr(value)}
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if hasattr(value, "__dict__") or hasattr(value, "__slots__"):
        names = getattr(value, "__slots__", None) or [k for k in vars(value) if not k.startswith("_")]
        out = {"__type__": type(value).__name__}
        for n in names:
            if n.startswith("_"):
                continue
            out[n] = _jsonable(getattr(value, n, None))
        return out
    return repr(value)


def _job(job) -> Dict:
    return {"job_name": job.job_name, "status": job.status, "detail": job.detail, "business_date": str(job.business_date)}


def _unwrap(recorded):
    """The Oct 11 recordings keep Toast's answer under "data" (with the
    fetch time beside it); a bare answer is used as is."""
    if isinstance(recorded, dict) and "data" in recorded and "menus" not in recorded:
        return recorded["data"]
    return recorded


def child(args) -> int:
    import parity_sandbox

    if os.environ.get("PYTHONHASHSEED") != "0" or os.environ.get("DATABASE_URL") != f"sqlite:///{args.db}":
        raise SystemExit("The child must be started by sync_compare.py run (harness environment missing).")
    parity_sandbox.install_network_guard()
    parity_sandbox.install_thread_guard()
    sys.path.insert(0, str(pathlib.Path(args.code).resolve()))

    import httpx

    import app.main  # noqa: F401 - the whole app, as in production
    from app.config import get_settings
    from app.db import SessionLocal, engine

    settings = get_settings()
    if str(engine.url) != f"sqlite:///{args.db}" or settings.toast_client_id or settings.whapi_api_token:
        raise SystemExit(f"Refusing to run: engine is {engine.url}")
    app_dir = pathlib.Path(sys.modules["app"].__file__).resolve().parent
    if app_dir.parent != pathlib.Path(args.code).resolve():
        raise SystemExit(f"Wrong code tree imported: {app_dir}")

    import parity_clock

    now = dt.datetime.fromisoformat(args.now)
    parity_clock.freeze(now)
    today = now.astimezone(zoneinfo.ZoneInfo(STORE_TZ)).date()

    from app import toast_client
    from app.pos.adapters.toast import client as toast_http
    from app.services import inventory_api, inventory_api_switch, items_sold, open_orders, sales_accuracy, sales_sync, toast_analytics
    from app.services.reorder import latest_inventory_by_item

    fixtures = json.loads(pathlib.Path(args.fixtures).read_text())
    menus_json = _unwrap(json.loads(pathlib.Path(args.menus).read_text()))
    stock_json = _unwrap(json.loads(pathlib.Path(args.stock).read_text()))
    days = sorted(fixtures["pass1"])
    out: Dict = {"python": sys.version.split()[0], "now": now.isoformat(), "code": str(app_dir)}
    calls: List[str] = []
    state = {"answers": fixtures["pass1"], "fail": set(), "throttle": {}, "stock_empty": False}

    def fake_fetch(business_date):
        key = business_date.isoformat()
        calls.append(f"orders {key}")
        if key in state["fail"]:
            raise RuntimeError(f"Toast said 503 for {key}")
        left = state["throttle"].get(key)
        if left:
            state["throttle"][key] = left[1:]
            req = httpx.Request("GET", "https://toast.invalid/orders/v2/ordersBulk")
            resp = httpx.Response(429, headers={"Retry-After": left[0]}, request=req)
            raise httpx.HTTPStatusError("429 Too Many Requests", request=req, response=resp)
        return copy.deepcopy(state["answers"].get(key, []))

    def fake_employees():
        calls.append("employees")
        return copy.deepcopy(fixtures["employees"])

    def fake_toast_get(path, timeout=90.0, max_tries=4):
        calls.append(f"GET {path}")
        if path.startswith("/stock/"):
            return [] if state["stock_empty"] else copy.deepcopy(stock_json)
        if path == "/menus/v2/menus":
            return copy.deepcopy(menus_json)
        if path == "/menus/v2/metadata":
            return {"lastUpdated": menus_json.get("lastUpdated")}
        raise AssertionError(path)

    menu_totals, metric_totals = analytics_totals(fixtures["pass2"], days)

    def fake_menu(start, end):
        calls.append(f"menu {start}..{end}")
        return {d: dict(v) for d, v in menu_totals.items() if start <= d <= end}

    def fake_metrics(start, end):
        calls.append(f"metrics {start}..{end}")
        return {d: dict(v) for d, v in metric_totals.items() if start <= d <= end}

    for mod in (sales_sync, open_orders, toast_client):
        setattr(mod, "fetch_orders_for_business_date", fake_fetch)
    for mod in (open_orders, toast_client):
        setattr(mod, "fetch_employees", fake_employees)
    toast_http.toast_get = fake_toast_get
    toast_analytics.is_configured = lambda: True
    toast_analytics.menu_day_totals = fake_menu
    toast_analytics.day_metrics = fake_metrics
    sleeps: List[float] = []
    time.sleep = lambda s: sleeps.append(s)
    # Epoch time too (Items Sold reports when its live pull ran from it).
    frozen_epoch = now.timestamp()
    time.time = lambda: frozen_epoch
    sales_accuracy._sleep = lambda s: sleeps.append(s)
    inventory_api._after_new_snapshot = lambda: calls.append("after_new_snapshot")

    db = SessionLocal()
    try:
        # 1. every business date, as Toast answered at first
        out["pass1"] = [_job(sales_sync.sync_sales_for_date(db, dt.date.fromisoformat(d))) for d in days]
        # 2. 429s answered by the retry (one Retry-After that isn't a number)
        state["throttle"] = {days[-1]: ["2", "soon"], days[-2]: ["1"]}
        out["throttled"] = [_job(sales_sync.sync_sales_for_date(db, dt.date.fromisoformat(d))) for d in (days[-1], days[-2])]
        out["retry_raw"] = len(sales_sync.fetch_orders_with_retry(dt.date.fromisoformat(days[-4]), sleep=sleeps.append))
        # 3. Toast changed things after we synced
        state["answers"] = fixtures["pass2"]
        state["fail"] = {fixtures["failing_day"]}
        out["pass2"] = [_job(sales_sync.sync_sales_for_date(db, dt.date.fromisoformat(d))) for d in days[-40:]]
        state["fail"] = set()
        out["latest_sync_job"] = _job(sales_sync.latest_job_run(db, sales_sync.JOB_NAME))
        # 4. open checks
        out["open_full"] = _job(open_orders.refresh_open_orders_cache(db, full=True))
        state["throttle"] = {today.isoformat(): ["1"]}
        out["open_today"] = _job(open_orders.refresh_open_orders_cache(db, full=False))
        out["open_cached"] = _jsonable(open_orders.get_cached_open_orders(db))
        out["open_cached_emp1"] = _jsonable(open_orders.get_cached_open_orders(db, employee_guid="emp-1"))
        last_open_day = dt.date.fromisoformat(days[-1])
        out["open_live"] = _jsonable(open_orders.get_open_orders(last_open_day))
        out["open_live_emp0"] = _jsonable(open_orders.get_open_orders(last_open_day, employee_guid="emp-0"))
        out["employees"] = _jsonable(open_orders.get_employees(force_refresh=True))
        # 5. Items Sold (today live: the newest dates' answers serve as today's)
        state["answers"] = dict(fixtures["pass2"])
        state["answers"][today.isoformat()] = fixtures["pass2"][days[-1]]
        items_sold.clear_live_cache()
        out["items_sold_today"] = _jsonable(items_sold.get_items_sold(db, today, today))
        out["items_sold_week"] = _jsonable(items_sold.get_items_sold(db, today - dt.timedelta(days=6), today, fresh=True))
        out["items_sold_past"] = _jsonable(items_sold.get_items_sold(db, dt.date.fromisoformat(days[-10]), dt.date.fromisoformat(days[-8])))
        state["answers"] = fixtures["pass2"]
        # 6. the sales accuracy re-pull, twice (the second one waits / matches)
        out["repull_1"] = _job(sales_accuracy.run_sales_repull(db, days=14))
        out["repull_2"] = _job(sales_accuracy.run_sales_repull(db, days=14))
        out["accuracy_report"] = _jsonable(sales_accuracy.accuracy_report(db, 30))
        # 7. inventory from the (recorded) Toast API
        inventory_api_switch.is_on = lambda db=None: True
        csv_day = inventory_api.latest_csv_date(db)
        out["snapshot_write"] = _job(inventory_api.run_api_snapshot(db, write=True, snapshot_date=csv_day + dt.timedelta(days=1)))
        out["snapshot_compare"] = _job(inventory_api.run_api_snapshot(db, write=False, snapshot_date=csv_day))
        state["stock_empty"] = True
        out["snapshot_implausible"] = _job(inventory_api.run_api_snapshot(db, write=True, snapshot_date=csv_day + dt.timedelta(days=2)))
        state["stock_empty"] = False
        out["late_csv_due"] = list(inventory_api.late_csv_rerun_due(db, csv_day))
        out["menu_recheck"] = _job(inventory_api.refresh_menu_cache(db))
        out["menu_recheck_forced"] = _job(inventory_api.refresh_menu_cache(db, force=True))
        out["compare_now"] = _jsonable(inventory_api.compare_now(db, refresh=True))
        inventory_api.clear_live_stock_cache()
        latest = latest_inventory_by_item(db)
        overlaid = inventory_api.overlay_live_stock(latest)
        out["overlay"] = {
            k: [repr(getattr(v, "inventory_quantity", None)), getattr(v, "inventory_status", None), type(v).__name__,
                repr(inventory_api.effective_quantity(v))]
            for k, v in sorted(overlaid.items())
        }
        out["live_meta"] = _jsonable(inventory_api.live_stock_meta())
        out["status"] = _jsonable(inventory_api.status(db))
    finally:
        db.close()
    out["calls"] = calls
    out["sleeps"] = sleeps
    out["blocked_network"] = parity_sandbox.blocked_attempts()
    out["suppressed_threads"] = parity_sandbox.suppressed_threads()

    outdir = pathlib.Path(args.out)
    (outdir / "results.json").write_text(json.dumps(out, indent=1, sort_keys=True, default=repr))
    engine.dispose()
    return 0


def dump_tables(db_path: str, outdir: pathlib.Path, since: str) -> Dict[str, int]:
    # The scratch copy (the app switched it to WAL): a plain connection.
    con = sqlite3.connect(db_path)
    counts = {}
    for table in TABLES:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
        where = ""
        if table == "inventory_snapshots":
            where = f" WHERE snapshot_date >= '{since}' OR source_filename LIKE 'api:%'"
        order = "item_id" if table == "toast_menu_items" else "id"
        n = 0
        with open(outdir / f"{table}.jsonl", "w") as fh:
            for row in con.execute(f"SELECT * FROM {table}{where} ORDER BY {order}"):
                fh.write(json.dumps([[c, repr(v)] for c, v in zip(cols, row)]) + "\n")
                n += 1
        counts[table] = n
    present = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    for table, order in OPTIONAL_TABLES.items():
        if table not in present:
            continue
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
        n = 0
        with open(outdir / f"{table}.jsonl", "w") as fh:
            for row in con.execute(f"SELECT * FROM {table} ORDER BY {order}"):
                fh.write(json.dumps([[c, repr(v)] for c, v in zip(cols, row)]) + "\n")
                n += 1
        counts[table] = n
    con.close()
    return counts


def run(args) -> int:
    src = str(pathlib.Path(args.db).resolve())
    out = pathlib.Path(args.out).resolve()
    if out.exists():
        if not args.force:
            raise SystemExit(f"{out} exists (use --force)")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    scratch = pathlib.Path(tempfile.mkdtemp(prefix="synccmp-", dir=args.scratch_root))
    started = time.time()
    try:
        from parity_record import copy_database, default_now
        from parity_sandbox import harness_env

        work = scratch / "work.db"
        copy_database(src, str(work))
        now = dt.datetime.fromisoformat(args.now) if args.now else default_now(str(work))
        if now.tzinfo is None:
            now = now.replace(tzinfo=zoneinfo.ZoneInfo(STORE_TZ))
        today = now.astimezone(zoneinfo.ZoneInfo(STORE_TZ)).date()
        fixtures = build_answers(str(work), today)
        fx = scratch / "fixtures.json"
        fx.write_text(json.dumps(fixtures, sort_keys=True))
        digest = hashlib.sha256(fx.read_bytes()).hexdigest()
        env = dict(os.environ)
        env.update(harness_env(str(work), str(scratch)))
        cmd = [sys.executable, str(HERE / "sync_compare.py"), "child", "--db", str(work), "--code", args.code, "--out", str(out),
               "--now", now.isoformat(), "--fixtures", str(fx), "--menus", args.menus, "--stock", args.stock]
        print(f"[{args.code}] running the scenario (now = {now.isoformat()})", flush=True)
        child_run = subprocess.run(cmd, cwd=str(scratch), env=env)
        if child_run.returncode != 0:
            print(f"child failed ({child_run.returncode})", file=sys.stderr)
            return child_run.returncode
        since = (today - dt.timedelta(days=1)).isoformat()
        counts = dump_tables(str(work), out, since)
        meta = {
            "code": str(pathlib.Path(args.code).resolve()),
            "source_db": src,
            "now": now.isoformat(),
            "fixtures_sha256": digest,
            "orders_in_answers": sum(len(v) for v in fixtures["pass1"].values()),
            "business_dates": len(fixtures["pass1"]),
            "open_orders_in_answers": len(fixtures["open_guids"]),
            "pass2_edits": fixtures["edits"],
            "rows": counts,
            "seconds": round(time.time() - started, 1),
        }
        (out / "meta.json").write_text(json.dumps(meta, indent=1, sort_keys=True))
        print(json.dumps({k: meta[k] for k in ("rows", "seconds", "orders_in_answers", "business_dates")}), flush=True)
        return 0
    finally:
        if args.keep_scratch:
            print(f"scratch kept: {scratch}")
        else:
            shutil.rmtree(scratch, ignore_errors=True)


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


def _first_diff(a, b, path="$"):
    if type(a) is not type(b):
        return f"{path}: {a!r:.200} != {b!r:.200}"
    if isinstance(a, dict):
        for k in list(a) + [k for k in b if k not in a]:
            if k not in a or k not in b:
                return f"{path}.{k}: only in {'new' if k in b else 'old'}"
            d = _first_diff(a[k], b[k], f"{path}.{k}")
            if d:
                return d
        if list(a) != list(b):
            return f"{path}: key order differs"
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: length {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            d = _first_diff(x, y, f"{path}[{i}]")
            if d:
                return d
        return None
    return None if a == b else f"{path}: {a!r:.200} != {b!r:.200}"


def diff(args) -> int:
    a, b = pathlib.Path(args.a), pathlib.Path(args.b)
    problems = []
    report = []
    optional = [t for t in OPTIONAL_TABLES if (a / f"{t}.jsonl").exists() or (b / f"{t}.jsonl").exists()]
    for table in (*TABLES, *optional):
        fa, fb = a / f"{table}.jsonl", b / f"{table}.jsonl"
        if not (fa.exists() and fb.exists()):
            problems.append(f"{table}: dumped on one side only")
            continue
        ra = fa.read_text().splitlines()
        rb = fb.read_text().splitlines()
        same = sum(1 for x, y in zip(ra, rb) if x == y)
        differ = [i for i, (x, y) in enumerate(zip(ra, rb)) if x != y]
        report.append(f"  {table:<20} rows {len(ra):>7} vs {len(rb):>7}   identical {same:>7}   differing {len(differ) + abs(len(ra) - len(rb))}")
        if len(ra) != len(rb):
            problems.append(f"{table}: {len(ra)} rows vs {len(rb)}")
        for i in differ[:5]:
            problems.append(f"{table} row {i}: {_first_diff(json.loads(ra[i]), json.loads(rb[i]))}")
        if len(differ) > 5:
            problems.append(f"{table}: ... {len(differ) - 5} more differing rows")
    xa = json.loads((a / "results.json").read_text())
    xb = json.loads((b / "results.json").read_text())
    skip = {"code", "python"}
    answers = 0
    for key in sorted(set(xa) | set(xb)):
        if key in skip:
            continue
        answers += 1
        d = _first_diff(xa.get(key), xb.get(key), key)
        if d:
            problems.append(f"answer {d}")
    for key in ("blocked_network", "suppressed_threads"):
        if xa.get(key) or xb.get(key):
            problems.append(f"{key}: old {xa.get(key)} new {xb.get(key)}")
    ma, mb = json.loads((a / "meta.json").read_text()), json.loads((b / "meta.json").read_text())
    if ma["fixtures_sha256"] != mb["fixtures_sha256"]:
        problems.append("the two runs were fed different Toast answers (fixtures differ)")
    print(f"old: {ma['code']}\nnew: {mb['code']}")
    print(f"Toast answers: {ma['business_dates']} business dates, {ma['orders_in_answers']} orders, "
          f"{ma['open_orders_in_answers']} open; fixtures sha256 {ma['fixtures_sha256'][:16]} / {mb['fixtures_sha256'][:16]}")
    print("Tables (every column, row by row):")
    print("\n".join(report))
    print(f"Function answers compared: {answers} ({len(xa.get('calls', []))} Toast calls old, {len(xb.get('calls', []))} new)")
    for p in problems[:60]:
        print("  - " + p)
    print(f"DIFFERENCES: {len(problems)}")
    return 1 if problems else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--db", required=True)
    r.add_argument("--code", required=True, help="backend folder whose app package runs")
    r.add_argument("--out", required=True)
    r.add_argument("--now")
    r.add_argument("--menus", default=DEFAULT_MENUS)
    r.add_argument("--stock", default=DEFAULT_STOCK)
    r.add_argument("--scratch-root")
    r.add_argument("--keep-scratch", action="store_true")
    r.add_argument("--force", action="store_true")
    c = sub.add_parser("child")
    for name in ("--db", "--code", "--out", "--now", "--fixtures", "--menus", "--stock"):
        c.add_argument(name, required=True)
    d = sub.add_parser("diff")
    d.add_argument("a")
    d.add_argument("b")
    args = p.parse_args(argv)
    return {"run": run, "child": child, "diff": diff}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
