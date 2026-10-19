"""Throwaway regression server: the app on a PROD COPY with nothing real reachable.

Started by serve.sh (which blanks every credential in the environment). This file
adds three things before uvicorn starts:

- refuses to run unless DATABASE_URL points inside REG_WORK (never the live DB);
- a fake SMTP (Send PO writes .eml files into REG_WORK/outbox, nothing is sent);
- a mock Toast on 127.0.0.1:REG_MOCK_PORT:
    POST /authentication/v1/authentication/login  -> fake token
    GET  /orders/v2/ordersBulk?businessDate=D       -> D's orders rebuilt from the
         copy's own `orders` rows; for TODAY (store time) three synthetic orders are
         added: two open checks (one with tab name "Ravi Kumar", one with two checks
         "Priya" + "priya" - shown once) and one paid sale of a real item
    GET  /stock/v1/inventory                        -> the latest snapshot's counts
    GET  /labor/v1/employees                        -> one employee
    anything else (Analytics, menus)                -> 404 (pages must say so, not crash)
  Every call is appended to REG_WORK/toast_calls.jsonl.
"""
import collections
import datetime as dt
import json
import os
import pathlib
import smtplib
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

WORK = pathlib.Path(os.environ["REG_WORK"]).resolve()
DB_URL = os.environ["DATABASE_URL"]
DB_PATH = pathlib.Path(DB_URL.replace("sqlite:///", "")).resolve()
assert str(DB_PATH).startswith(str(WORK) + "/"), f"refusing: {DB_PATH} is not inside {WORK}"
assert not (os.environ.get("ORIGIN_LIVE_DIR") and str(DB_PATH).startswith(os.environ["ORIGIN_LIVE_DIR"])), "never the live database"
PORT = int(os.environ["REG_PORT"])
MOCK_PORT = int(os.environ["REG_MOCK_PORT"])
OUTBOX = WORK / "outbox"
OUTBOX.mkdir(exist_ok=True)
TZ = ZoneInfo("America/New_York")
LOG = WORK / "toast_calls.jsonl"
LOCK = threading.Lock()


# ---------- fake SMTP ----------
class FakeSMTP:
    def __init__(self, host=None, port=None, timeout=None, **kw):
        pass

    def login(self, user, password):
        pass

    def sendmail(self, sender, to, message):
        with LOCK:
            n = len(list(OUTBOX.glob("*.eml"))) + 1
            (OUTBOX / f"{n:03d}.eml").write_text(message if isinstance(message, str) else message.decode())
            (OUTBOX / f"{n:03d}.json").write_text(json.dumps({"from": sender, "to": to}))
        return {}

    def send_message(self, msg, from_addr=None, to_addrs=None):
        return self.sendmail(from_addr, to_addrs, msg.as_string())

    def quit(self):
        pass

    close = quit

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _refuse(*a, **k):
    raise RuntimeError("real SMTP blocked in the regression server")


smtplib.SMTP_SSL = _refuse
smtplib.SMTP = _refuse


# ---------- mock Toast ----------
def _db():
    # The COPY (checked above), only ever SELECTed here. Not mode=ro: a
    # read-only open of a WAL database fails before the app has made its -shm.
    return sqlite3.connect(str(DB_PATH), timeout=30)


def _ts(d):
    if d is None:
        return None
    if isinstance(d, str):
        s = d.replace("T", " ")[:26]
        try:
            d = dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S.%f")
        except ValueError:
            d = dt.datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
        d = d.replace(tzinfo=dt.timezone.utc)
    d = d.astimezone(dt.timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}+0000"


def rebuild_orders(ymd):
    day = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
    con = _db()
    rows = con.execute(
        "select toast_order_guid, toast_check_guid, toast_selection_guid, parent_selection_guid, opened_at, paid_at,"
        " item_guid, item_name, quantity, net_price, tax_amount, voided, dining_option from orders"
        " where business_date = ? order by id",
        (day,),
    ).fetchall()
    con.close()
    orders = collections.OrderedDict()
    sel_by_guid = {}
    for (og, cg, sg, parent, opened, paid, ig, name, qty, net, tax, voided, dining) in rows:
        o = orders.setdefault(og, {
            "guid": og, "entityType": "Order", "businessDate": int(ymd), "openedDate": _ts(opened),
            "createdDate": _ts(opened), "closedDate": _ts(paid) if paid else None, "paidDate": _ts(paid),
            "voided": False, "deleted": False, "displayNumber": str(len(orders) + 1),
            "diningOption": {"guid": dining} if dining else None, "checks": collections.OrderedDict(),
        })
        c = o["checks"].setdefault(cg, {"guid": cg, "entityType": "Check", "paidDate": _ts(paid),
                                        "closedDate": _ts(paid), "voided": False, "deleted": False,
                                        "selections": []})
        sel = {"guid": sg, "entityType": "MenuItemSelection", "item": {"guid": ig} if ig else None,
               "displayName": name, "quantity": qty, "price": net, "tax": tax, "voided": bool(voided),
               "modifiers": []}
        sel_by_guid[sg] = sel
        if parent and parent in sel_by_guid:
            sel_by_guid[parent]["modifiers"].append(sel)
        else:
            c["selections"].append(sel)
    out = []
    for o in orders.values():
        o["checks"] = list(o["checks"].values())
        out.append(o)
    return out


def _real_item():
    con = _db()
    row = con.execute(
        "select item_guid, item_name from orders where parent_selection_guid is null and voided = 0"
        " and item_guid is not null order by business_date desc, id desc limit 1").fetchone()
    con.close()
    return row or ("reg-item", "Regression item")


def synthetic_today(ymd):
    now = dt.datetime.now(dt.timezone.utc)
    ig, name = _real_item()

    def sel(g, price=4.99):
        return {"guid": g, "entityType": "MenuItemSelection", "item": {"guid": ig}, "displayName": name,
                "quantity": 1, "price": price, "tax": 0.0, "voided": False, "modifiers": []}

    return [
        {"guid": "reg-open-1", "entityType": "Order", "businessDate": int(ymd), "displayNumber": "41",
         "openedDate": _ts(now - dt.timedelta(minutes=95)), "createdDate": _ts(now - dt.timedelta(minutes=95)),
         "closedDate": None, "paidDate": None, "voided": False, "deleted": False, "numberOfGuests": 2,
         "server": {"guid": "reg-emp-1"},
         "checks": [{"guid": "reg-chk-1", "tabName": "Ravi Kumar", "totalAmount": 9.98, "voided": False,
                     "deleted": False, "paidDate": None, "selections": [sel("reg-sel-1"), sel("reg-sel-2")]}]},
        {"guid": "reg-open-2", "entityType": "Order", "businessDate": int(ymd), "displayNumber": "42",
         "openedDate": _ts(now - dt.timedelta(minutes=20)), "createdDate": _ts(now - dt.timedelta(minutes=20)),
         "closedDate": None, "paidDate": None, "voided": False, "deleted": False, "server": {"guid": "reg-emp-1"},
         "checks": [
             {"guid": "reg-chk-2a", "tabName": "Priya", "totalAmount": 4.99, "voided": False, "deleted": False,
              "selections": [sel("reg-sel-3")]},
             {"guid": "reg-chk-2b", "tabName": " priya ", "totalAmount": 0, "voided": False, "deleted": False,
              "selections": []},
             {"guid": "reg-chk-2c", "tabName": "42", "totalAmount": 0, "voided": False, "deleted": False,
              "selections": []}]},
        {"guid": "reg-paid-1", "entityType": "Order", "businessDate": int(ymd), "displayNumber": "43",
         "openedDate": _ts(now - dt.timedelta(minutes=10)), "createdDate": _ts(now - dt.timedelta(minutes=10)),
         "closedDate": _ts(now - dt.timedelta(minutes=8)), "paidDate": _ts(now - dt.timedelta(minutes=8)),
         "voided": False, "deleted": False,
         "checks": [{"guid": "reg-chk-3", "totalAmount": 4.99, "voided": False, "deleted": False,
                     "paidDate": _ts(now - dt.timedelta(minutes=8)), "closedDate": _ts(now - dt.timedelta(minutes=8)),
                     "selections": [sel("reg-sel-4")]}]},
    ]


_STOCK = {}


def stock_rows():
    # Built once (the copy's latest counts don't change during a run): the
    # app's live-stock fetch has a short timeout, a slow answer would look
    # like Toast being down.
    if "rows" not in _STOCK:
        _STOCK["rows"] = _stock_rows()
    return _STOCK["rows"]


def _stock_rows():
    con = _db()
    # the newest snapshot date's counts (one indexed read - the per-item
    # "latest row" join took ~30 s on a busy machine)
    rows = con.execute(
        "select item_id, inventory_quantity from inventory_snapshots"
        " where snapshot_date = (select max(snapshot_date) from inventory_snapshots)").fetchall()
    con.close()
    out = []
    for guid, qty in rows:
        if qty is None:
            out.append({"guid": guid, "status": "IN_STOCK", "quantity": None})
        elif qty <= 0:
            out.append({"guid": guid, "status": "OUT_OF_STOCK", "quantity": None})
        else:
            out.append({"guid": guid, "status": "QUANTITY", "quantity": qty})
    return out


class MockToast(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        u = urlparse(self.path)
        with LOCK, open(LOG, "a") as f:
            f.write(json.dumps({"t": time.time(), "m": self.command, "path": u.path, "q": u.query,
                                "status": status}) + "\n")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        if urlparse(self.path).path == "/authentication/v1/authentication/login":
            return self._send(200, {"token": {"accessToken": "regression-mock", "expiresIn": 86400}})
        return self._send(404, {"message": "mock: unknown POST"})

    def do_GET(self):
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)
        if p == "/orders/v2/ordersBulk":
            ymd = q.get("businessDate", [""])[0]
            page = int(q.get("page", ["1"])[0])
            size = int(q.get("pageSize", ["100"])[0])
            data = rebuild_orders(ymd)
            if ymd == dt.datetime.now(TZ).strftime("%Y%m%d"):
                data = data + synthetic_today(ymd)
            return self._send(200, data[(page - 1) * size: page * size])
        if p == "/stock/v1/inventory":
            return self._send(200, stock_rows())
        if p == "/labor/v1/employees":
            return self._send(200, [{"guid": "reg-emp-1", "firstName": "Regression", "lastName": "Server",
                                     "deleted": False}])
        return self._send(404, {"message": "mock: unknown GET"})


def start_mock():
    # warm the stock answer in the background (the first live-stock call has a short timeout)
    threading.Thread(target=lambda: (time.sleep(5), stock_rows()), daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", MOCK_PORT), MockToast)
    threading.Thread(target=srv.serve_forever, daemon=True).start()


if __name__ == "__main__":
    sys.path.insert(0, str(pathlib.Path(os.environ["REG_REPO"]) / "backend"))
    from app.config import get_settings  # noqa: E402

    s = get_settings()
    assert pathlib.Path(s.database_url.replace("sqlite:///", "")).resolve() == DB_PATH
    assert not s.whapi_api_token and not s.scheduler_enabled and not s.firebase_credentials_file
    assert not s.anthropic_api_key and not s.notifications_enabled
    assert s.toast_api_base == f"http://127.0.0.1:{MOCK_PORT}", s.toast_api_base
    start_mock()
    from app.main import app  # noqa: E402
    from app.services import po_send  # noqa: E402

    po_send.smtplib.SMTP_SSL = FakeSMTP
    import uvicorn  # noqa: E402

    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
