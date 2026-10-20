"""Clover connector (website-ready track D1 + D2; multi-store plan 4.7).

Every Clover call here goes to an ``httpx.MockTransport`` serving the
synthetic payloads in tests/fixtures/clover/ - never the network.

1. Client: windows, paging, 429 + Retry-After, 401, region hosts, headers,
   the >100-line fallback, metering, the process-wide limiter (fake clock).
2. Mapping: business date across the cutoff, lines / modifiers / per-unit
   quantities / discounts / tax / refunds / channel; catalog + signed stock.
3. Connector: business-date windows, modified-since windows, open orders,
   employees, the sales sync (idempotent re-run), capability gating and
   /api/auth/me for a Clover store, the catalog snapshot with item vendors.
"""

import datetime as dt
import json
import pathlib
import threading
import zoneinfo
from decimal import Decimal
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest

from app.pos.adapters.clover import client as cc
from app.pos.adapters.clover import mapping as cm
from app.pos.adapters.clover.client import CloverAuthError, CloverClient, CloverError, RateLimiter
from app.pos.adapters.clover.connector import CloverConnector
from app.pos.capabilities import CLOVER_CAPABILITIES, Capability
from app.pos.models import StockStatus

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "clover"
NY = zoneinfo.ZoneInfo("America/New_York")


def load(name):
    return json.loads((FIX / name).read_text())


def _filters(request):
    pairs = parse_qsl(urlsplit(str(request.url)).query)
    return [v for k, v in pairs if k == "filter"], dict(pairs)


def _in_window(order, filters):
    for f in filters:
        for op in (">=", "<"):
            if op in f:
                field, value = f.split(op, 1)
                if field not in order:
                    break
                v = int(order[field])
                if op == ">=" and not v >= int(value):
                    return False
                if op == "<" and not v < int(value):
                    return False
                break
        else:
            if f.startswith("state=") and order.get("state") != f.split("=", 1)[1]:
                return False
    return True


class FakeClover:
    """A tiny Clover: routes by path, records every request."""

    def __init__(self):
        self.requests = []
        self.orders = load("orders.json")["elements"]

    def __call__(self, request):
        self.requests.append(request)
        path = urlsplit(str(request.url)).path
        filters, params = _filters(request)
        tail = path.split("/v3/merchants/TESTMERCH0001", 1)[-1]
        limit = int(params.get("limit", 1000))
        offset = int(params.get("offset", 0))
        if tail == "":
            return httpx.Response(200, json=load("merchant.json"))
        if tail == "/properties":
            return httpx.Response(200, json=load("properties.json"))
        if tail == "/items":
            return httpx.Response(200, json=load("items.json"))
        if tail == "/item_stocks":
            return httpx.Response(200, json=load("item_stocks.json"))
        if tail == "/employees":
            return httpx.Response(200, json=load("employees.json"))
        if tail == "/orders":
            rows = [o for o in self.orders if _in_window(o, filters)]
            return httpx.Response(200, json={"elements": rows[offset: offset + limit]})
        return httpx.Response(404, json={"message": "not found"})


@pytest.fixture
def fake():
    return FakeClover()


@pytest.fixture
def connector(fake, monkeypatch):
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "business_day_cutoff_hour", 4)

    def factory():
        return CloverClient("TESTMERCH0001", "tok-1234", "sandbox", transport=httpx.MockTransport(fake),
                            sleep=lambda s: None, limiters=())

    return CloverConnector(client_factory=factory)


# ---------------------------------------------------------------------------
# 1. Client
# ---------------------------------------------------------------------------


def test_windows_never_longer_than_90_days():
    start = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
    ws = cc.windows(start, start + dt.timedelta(days=200))
    assert [(b - a).days for a, b in ws] == [90, 90, 20]
    assert ws[0][0] == start and ws[-1][1] == start + dt.timedelta(days=200)
    assert all(ws[i][1] == ws[i + 1][0] for i in range(len(ws) - 1))  # no gap, no overlap
    assert cc.windows(start, start + dt.timedelta(days=90)) == [(start, start + dt.timedelta(days=90))]
    assert cc.windows(start, start) == []


def test_paging_until_a_short_page():
    seen = []

    def handler(request):
        _, params = _filters(request)
        seen.append((int(params["limit"]), int(params["offset"])))
        n = {0: 1000, 1000: 1000, 2000: 3}[int(params["offset"])]
        return httpx.Response(200, json={"elements": [{"id": f"I{int(params['offset']) + i}"} for i in range(n)]})

    client = CloverClient("M1234567", "tok", "us", transport=httpx.MockTransport(handler), limiters=())
    items = list(client.paged("/items"))
    assert len(items) == 2003 and items[-1]["id"] == "I2002"
    assert seen == [(1000, 0), (1000, 1000), (1000, 2000)]


def test_region_hosts_headers_and_metering(monkeypatch):
    calls = []
    metered = []
    from app.platform import usage

    monkeypatch.setattr(usage, "record", lambda metric, n=1, **k: metered.append((metric, n)))

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"id": "M1234567"})

    for region, host in (("us", "api.clover.com"), ("eu", "api.eu.clover.com"), ("la", "api.la.clover.com"),
                         ("sandbox", "apisandbox.dev.clover.com"), ("nonsense", "api.clover.com")):
        CloverClient("M1234567", "secret-token", region, transport=httpx.MockTransport(handler), limiters=()).merchant()
        assert calls[-1].url.host == host
        assert calls[-1].url.path == "/v3/merchants/M1234567"
    req = calls[-1]
    assert req.headers["Authorization"] == "Bearer secret-token"
    assert "clover-connector" in req.headers["User-Agent"]
    assert metered == [("pos_api_calls", 1)] * 5


def test_429_waits_retry_after_then_succeeds():
    answers = [httpx.Response(429, headers={"Retry-After": "2"}), httpx.Response(200, json={"ok": True})]
    slept = []
    client = CloverClient("M1234567", "tok", transport=httpx.MockTransport(lambda r: answers.pop(0)),
                          sleep=slept.append, limiters=())
    assert client.get("/properties") == {"ok": True}
    assert slept == [2.0]


def test_429_gives_up_after_max_tries():
    slept = []
    client = CloverClient("M1234567", "tok", transport=httpx.MockTransport(lambda r: httpx.Response(429)),
                          sleep=slept.append, max_tries=3, limiters=())
    with pytest.raises(CloverError) as exc:
        client.get("/orders")
    assert exc.value.status == 429 and len(slept) == 2


def test_401_refreshes_once_then_fails_without_the_token_in_the_message():
    tokens = iter(["old-token-1111", "new-token-2222"])
    current = {"t": next(tokens)}
    refreshed = []

    def on_401():
        refreshed.append(1)
        current["t"] = next(tokens)

    def handler(request):
        if request.headers["Authorization"].endswith("new-token-2222"):
            return httpx.Response(200, json={"name": "ok"})
        return httpx.Response(401)

    client = CloverClient("M1234567", lambda: current["t"], transport=httpx.MockTransport(handler),
                          limiters=(), on_unauthorized=on_401)
    assert client.merchant() == {"name": "ok"} and refreshed == [1]
    bad = CloverClient("M1234567", "plain-token-3333", transport=httpx.MockTransport(lambda r: httpx.Response(401)),
                       limiters=())
    with pytest.raises(CloverAuthError) as exc:
        bad.merchant()
    assert "plain-token" not in str(exc.value) and exc.value.status == 401


def test_network_error_is_a_clover_error():
    def boom(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(CloverError):
        CloverClient("M1234567", "tok", transport=httpx.MockTransport(boom), limiters=()).merchant()


def test_more_than_100_lines_fetches_the_line_items():
    big = {"id": "BIG", "lineItems": {"elements": [{"id": f"L{i}"} for i in range(100)]}}
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path.endswith("/orders"):
            return httpx.Response(200, json={"elements": [big]})
        return httpx.Response(200, json={"elements": [{"id": f"L{i}"} for i in range(140)]})

    client = CloverClient("M1234567", "tok", transport=httpx.MockTransport(handler), limiters=())
    start = dt.datetime(2025, 10, 1, tzinfo=dt.timezone.utc)
    orders = client.orders(start, start + dt.timedelta(days=1))
    assert len(orders[0]["lineItems"]["elements"]) == 140
    assert paths[-1] == "/v3/merchants/M1234567/orders/BIG/line_items"


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_rate_limiter_16_per_second_on_a_fake_clock():
    clock = FakeClock()
    lim = RateLimiter(16, 5, clock=clock, sleep=clock.sleep)
    for _ in range(16):
        with lim.slot():
            pass
    assert clock.sleeps == [] and clock.now == 0.0
    with lim.slot():
        pass
    assert clock.now == pytest.approx(1.0)  # the 17th waits for the window
    for _ in range(15):
        with lim.slot():
            pass
    assert clock.now == pytest.approx(1.0)


def test_rate_limiter_caps_concurrency():
    lim = RateLimiter(1000, 2)
    lim.acquire()
    lim.acquire()
    got = threading.Event()

    def third():
        lim.acquire()
        got.set()

    t = threading.Thread(target=third)
    t.start()
    assert not got.wait(0.2)
    lim.release()
    assert got.wait(2.0)
    t.join()
    assert lim.in_flight == 2


def test_limiters_are_per_token_and_shared_per_app():
    cc.reset_limiters()
    a, b = cc.token_limiter("token-a"), cc.token_limiter("token-b")
    assert a is cc.token_limiter("token-a") and a is not b
    assert (a.rate, a.concurrency) == (16, 5)
    app = cc.app_limiter()
    assert app is cc.app_limiter() and (app.rate, app.concurrency) == (50, 10)
    assert all("token" not in k for k in cc._token_limiters)  # keys are hashes, never tokens
    cc.reset_limiters()


# ---------------------------------------------------------------------------
# 2. Mapping
# ---------------------------------------------------------------------------


def test_business_date_follows_the_cutoff():
    t = dt.datetime(2025, 10, 14, 7, 30, tzinfo=dt.timezone.utc)  # 03:30 EDT
    assert cm.business_date_of(t, NY, 4) == dt.date(2025, 10, 13)
    assert cm.business_date_of(t, NY, 0) == dt.date(2025, 10, 14)
    start, end = cm.business_day_bounds(dt.date(2025, 10, 13), NY, 4)
    assert start == dt.datetime(2025, 10, 13, 8, 0, tzinfo=dt.timezone.utc)
    assert end == dt.datetime(2025, 10, 14, 8, 0, tzinfo=dt.timezone.utc)
    # The business day the clocks go back in (Nov 1 04:00 EDT - Nov 2 04:00 EST) is 25 hours long.
    s2, e2 = cm.business_day_bounds(dt.date(2025, 11, 1), NY, 4)
    assert (e2 - s2) == dt.timedelta(hours=25)


def test_order_lines_modifiers_units_discounts_tax():
    raw = load("orders.json")["elements"]
    o = cm.map_order(raw[0], NY, 4)
    assert o.external_id == "ORD1" and o.business_date == dt.date(2025, 10, 13)
    assert o.closed and o.channel == "in_store" and o.channel_raw == "Dine In" and o.server_external_id == "EMP1"
    assert o.paid_at == dt.datetime(2025, 10, 14, 7, 33, 20, tzinfo=dt.timezone.utc)
    lines = {l.external_id: l for l in o.lines}
    assert [l.external_id for l in o.lines] == ["L1", "L1:M1", "L2", "L3"]
    assert lines["L1"].net_amount == Decimal("18.99") and lines["L1"].tax_amount == Decimal("1.21")
    assert lines["L1:M1"].is_modifier and lines["L1:M1"].parent_external_id == "L1"
    assert lines["L1:M1"].net_amount == Decimal("1.00")
    assert lines["L2"].net_amount == Decimal("17.09")  # 10 % off 18.99
    assert lines["L3"].quantity == Decimal("2.5") and lines["L3"].net_amount == Decimal("3.73")  # 2.5 lb x 1.49
    assert o.checks[0].total == Decimal("40.61") and not o.voided


def test_refunded_order_is_voided_and_channel_from_order_type():
    o = cm.map_order(load("orders.json")["elements"][1], NY, 4)
    assert o.voided and o.checks[0].voided and o.lines[0].voided
    assert o.channel == "pickup" and o.business_date == dt.date(2025, 10, 14)


def test_open_order_and_amount_discount():
    raw = load("orders.json")["elements"][2]
    assert cm.is_open(raw)
    o = cm.map_order(raw, NY, 4)
    assert not o.closed and o.lines[0].net_amount == Decimal("3.99") and o.channel == "in_store"


def test_channel_words():
    assert cm.channel_of({"label": "DoorDash Delivery"})[0] == "delivery_3p"
    assert cm.channel_of({"label": "To Go"})[0] == "pickup"
    assert cm.channel_of({"label": "Catering"})[0] == "catering"
    assert cm.channel_of({"label": "Weird"})[0] == "other"
    assert cm.channel_of(None) == ("in_store", None, None)


def test_catalog_items_primary_category_prices_cost_and_stock():
    raw = load("items.json")["elements"]
    items = {i["id"]: cm.map_item(i) for i in raw}
    rice = items["ITEM_RICE"]
    assert rice.category.group == "Pantry"  # lowest sortOrder wins
    assert [c.group for c in rice.locations] == ["Pantry", "Rice & Grains"]
    assert rice.barcode == "012345678905" and rice.plu == "RICE10"
    assert rice.price == Decimal("18.99") and rice.cost == Decimal("12.50") and rice.active
    onion = items["ITEM_ONION"]
    assert onion.price == Decimal("1.49") and onion.price_rule == "PER_UNIT:lb" and onion.barcodes == ()
    assert items["ITEM_GIFT"].price is None and items["ITEM_GIFT"].category is None
    assert items["ITEM_OLD"].active is False
    stock = {s.item_external_id: s for s in cm.stock_from_items(raw)}
    assert stock["ITEM_ONION"].status is StockStatus.COUNT and stock["ITEM_ONION"].quantity == Decimal("-3.5")
    assert stock["ITEM_GIFT"].status is StockStatus.UNTRACKED


def test_employees():
    emps = [cm.map_employee(e) for e in load("employees.json")["elements"]]
    assert [(e.external_id, e.name, e.active) for e in emps] == [("EMP1", "Alex Sample", True),
                                                                  ("EMP2", "Former Worker", False)]


# ---------------------------------------------------------------------------
# 3. Connector
# ---------------------------------------------------------------------------


def test_capabilities_corrected():
    assert Capability.ORDERS_BY_BUSINESS_DATE in CLOVER_CAPABILITIES
    for cap in (Capability.DAY_TOTALS, Capability.ANALYTICS_REPORTS, Capability.CARD_FINGERPRINTS,
                Capability.CATALOG_CHANGE_STAMP, Capability.ITEM_VENDORS, Capability.MODIFIER_PRICE_IN_PARENT):
        assert cap not in CLOVER_CAPABILITIES


def test_fetch_orders_asks_for_the_business_day_window(connector, fake):
    orders = connector.fetch_orders(dt.date(2025, 10, 13))
    assert [o.external_id for o in orders] == ["ORD1"]
    req = [r for r in fake.requests if r.url.path.endswith("/orders")][0]
    filters, params = _filters(req)
    start, end = cm.business_day_bounds(dt.date(2025, 10, 13), NY, 4)
    assert filters == [f"createdTime>={cc.to_ms(start)}", f"createdTime<{cc.to_ms(end)}"]
    assert "lineItems.modifications" in params["expand"] and params["limit"] == "1000"
    nxt = connector.fetch_orders(dt.date(2025, 10, 14))
    assert sorted(o.external_id for o in nxt) == ["ORD2", "ORD3"]
    assert all(o.business_date == dt.date(2025, 10, 14) for o in nxt)


def test_fetch_orders_modified_splits_into_90_day_windows(connector, fake):
    until = dt.datetime(2025, 10, 20, tzinfo=dt.timezone.utc)
    orders, cursor = connector.fetch_orders_modified(until - dt.timedelta(days=200), until)
    assert {o.external_id for o in orders} == {"ORD1", "ORD2", "ORD3"}
    assert cursor == until.isoformat()
    order_reqs = [r for r in fake.requests if r.url.path.endswith("/orders")]
    assert len(order_reqs) == 3
    assert all(_filters(r)[0][0].startswith("modifiedTime>=") for r in order_reqs)


def test_open_orders_and_employees(connector):
    open_orders = connector.fetch_open_orders([dt.date(2025, 10, 14)])
    assert [o.external_id for o in open_orders] == ["ORD3"]
    assert [e.name for e in connector.fetch_employees()] == ["Alex Sample", "Former Worker"]


def test_catalog_snapshot_and_stock(connector):
    snap = connector.fetch_catalog_snapshot()
    assert len(snap.items) == 4 and snap.changed_at == cc.from_ms(1760500000000)
    stock = {s.item_external_id: s.quantity for s in connector.fetch_stock()}
    assert stock == {"ITEM_RICE": Decimal("12.0"), "ITEM_ONION": Decimal("-3.5")}
    assert connector.merchant_facts()["timezone"] == "America/New_York"


def test_unsupported_methods_raise_not_supported(connector):
    from app.pos.base import NotSupported

    with pytest.raises(NotSupported):
        connector.get_day_totals(dt.date(2025, 10, 1), dt.date(2025, 10, 2))
    with pytest.raises(NotSupported):
        connector.catalog_changed_at()


def test_not_connected_is_pos_unavailable(monkeypatch):
    from app.pos.base import PosUnavailable

    monkeypatch.delenv("CLOVER_MERCHANT_ID", raising=False)
    monkeypatch.delenv("CLOVER_API_TOKEN", raising=False)
    conn = CloverConnector()
    assert conn.is_configured() is False and conn.health().configured is False
    with pytest.raises(PosUnavailable):
        conn.fetch_employees()


def test_sales_sync_is_idempotent_and_meters(db, connector, monkeypatch):
    from app.models import Order as OrderRow
    from app.platform import usage
    from app.pos import registry
    from app.pos.sync import sales

    metered = []
    monkeypatch.setattr(usage, "record", lambda metric, n=1, **k: metered.append((metric, n)))
    monkeypatch.setattr(registry, "get_connector", lambda provider=None: connector)
    day = dt.date(2025, 10, 13)
    job = sales.sync_sales_for_date(db, day)
    assert job.status == "success", job.detail
    rows = db.query(OrderRow).filter(OrderRow.business_date == day).all()
    assert len(rows) == 4  # L1, its modifier, L2, L3
    again = sales.sync_sales_for_date(db, day)
    assert again.status == "success"
    db.expire_all()
    assert db.query(OrderRow).filter(OrderRow.business_date == day).count() == 4
    assert ("orders_synced", 4) in metered
    other = sales.sync_sales_for_date(db, dt.date(2025, 10, 14))
    assert other.status == "success"
    assert db.query(OrderRow).filter(OrderRow.business_date == dt.date(2025, 10, 14)).count() == 2


def test_clover_store_gates_until_connected(db, monkeypatch):
    from app.pos import gating, registry
    from tests.conftest import client_for, make_user
    from tests.store_fixtures import use_store

    use_store(monkeypatch, "RETAILOPS_TEST_CLOVER")
    monkeypatch.delenv("CLOVER_MERCHANT_ID", raising=False)
    monkeypatch.delenv("CLOVER_API_TOKEN", raising=False)
    registry.reset_cache()
    assert gating.pos_name() == "Clover"
    assert not gating.has_capability(Capability.OPEN_ORDERS)
    user = make_user(db, "clerk", features=["open_orders", "sales_insights", "invoice_submission"])
    me = client_for(db, user).get("/api/auth/me").json()
    assert "analytics_reports" not in me["capabilities"] and "day_totals" not in me["capabilities"]
    assert "orders_by_business_date" in me["capabilities"]
    assert "sales_insights" not in me["allowed_features"] and "open_orders" in me["allowed_features"]
    # Connected (single mode: the CLOVER_* settings): the POS gates open.
    monkeypatch.setenv("CLOVER_MERCHANT_ID", "TESTMERCH0001")
    monkeypatch.setenv("CLOVER_API_TOKEN", "tok-env-5555")
    registry.reset_cache()
    assert gating.has_capability(Capability.OPEN_ORDERS, Capability.ORDERS_BY_BUSINESS_DATE)
    assert not gating.has_capability(Capability.DAY_TOTALS)
    registry.reset_cache()


def test_connection_tester_reads_the_merchant(fake, monkeypatch):
    from app.platform import connector_tests

    monkeypatch.setattr(connector_tests, "_clover_transport", lambda: httpx.MockTransport(fake))
    ok = connector_tests.run_test("clover", {"merchant_id": "TESTMERCH0001", "region": "sandbox"},
                                  {"access_token": "tok-test-7777"})
    assert ok.ok and ok.message == "Connected to Clover: Sample Corner Market (time zone America/New_York)."
    monkeypatch.setattr(connector_tests, "_clover_transport",
                        lambda: httpx.MockTransport(lambda r: httpx.Response(401)))
    bad = connector_tests.run_test("clover", {"merchant_id": "TESTMERCH0001", "region": "us"},
                                   {"access_token": "tok-test-7777"})
    assert not bad.ok and "refused" in bad.message and "tok-test" not in bad.message
    missing = connector_tests.run_test("clover", {"merchant_id": "", "region": "us"}, {})
    assert not missing.ok and "Merchant ID" in missing.message


def test_catalog_rows_for_a_clover_store_use_item_vendors_and_pos_cost(db):
    from app.item_vendor_models import ItemVendor
    from app.pos.sync.catalog import build_inventory_rows

    db.add(ItemVendor(item_id="ITEM_RICE", vendor_name="Main Street Wholesale", vendor_item_code="BR-10", rank=1,
                      source="manual"))
    db.add(ItemVendor(item_id="ITEM_RICE", vendor_name="Second Source", rank=2, source="manual"))
    db.commit()
    raw = load("items.json")["elements"]
    items = [cm.map_item(i) for i in raw]
    stock = cm.stock_from_items(raw)
    rows, meta = build_inventory_rows(db, items, stock, snapshot_date=dt.date(2025, 10, 15),
                                      pos_is_catalog_source=True)
    assert set(rows) == {"ITEM_RICE", "ITEM_ONION", "ITEM_GIFT"}  # hidden item skipped, none need a CSV row
    rice = rows["ITEM_RICE"]
    assert rice["supplier"] == "Main Street Wholesale;Second Source" and rice["supplier_item_id"] == "BR-10;"
    assert rice["cost"] == 12.5 and rice["gross_profit"] == 6.49
    assert rows["ITEM_ONION"]["inventory_quantity"] == -3.5 and rows["ITEM_ONION"]["supplier"] is None
    # Toast's rule is unchanged: without a CSV row nothing is built.
    toast_rows, _ = build_inventory_rows(db, items, stock, snapshot_date=dt.date(2025, 10, 15))
    assert toast_rows == {}


def test_empty_stock_is_fine_for_a_clover_catalog(db, monkeypatch):
    from app.pos.sync import catalog as cat

    meta = {"stats": {"rows": 3}}
    monkeypatch.setattr(cat, "_pos_is_catalog_source", lambda: True)
    assert cat.implausible_build(db, [], meta) is None
    monkeypatch.setattr(cat, "_pos_is_catalog_source", lambda: False)
    assert cat.implausible_build(db, [], meta) == "Toast's stock list came back empty"
