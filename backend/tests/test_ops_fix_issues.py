"""Fix Issues stock lists (ops fixes, Sep 29): Negative inventory (moved in
from Reports), Refill from container, Slow moving - Review, Dead with stock,
Dead - zero stock (Oct 2026) and Dead - newly arrived - each with the same close / reopen / Closed list /
report / CSV flow as the original four lists."""

import datetime as dt
import types
import uuid

import pytest

from app.timeutil import restaurant_today
from app.models import InventorySnapshot, IssueResolution, Order
from app.services import stock_issues
from app.services.fix_issues import ISSUE_TYPES, get_issue_list, get_summary
from app.services.supplier_projection import compute_supplier_projection

# Made-up rules in the shape these tests were written for: tests/fixtures/packs/suite.json.
pytestmark = pytest.mark.suite_pack

TODAY = restaurant_today()  # the forecast's "today", same as Supplier Projection
LATEST = TODAY
EARLY = TODAY - dt.timedelta(days=25)  # an older upload, so "first seen" means something

NEW_TYPES = [
    "negative_inventory",
    "refill_from_container",
    "slow_review",
    "dead_with_stock",
    "dead_zero_stock",
    "dead_new_arrival",
]
# Every stock list Dead - zero stock must never share an item with.
OTHER_STOCK_LISTS = [t for t in NEW_TYPES if t != "dead_zero_stock"]


@pytest.fixture(autouse=True)
def _fresh_forecast_cache():
    stock_issues.clear_forecast_cache()
    yield
    stock_issues.clear_forecast_cache()


def snap(db, item_id, name, qty, day=LATEST, price=4.99, supplier="Acme", group="RETAIL", **extra):
    db.add(
        InventorySnapshot(
            snapshot_date=day,
            item_id=item_id,
            name=name,
            category_group=group,
            category=extra.pop("category", "Snacks"),
            inventory_quantity=qty,
            price=price,
            cost=extra.pop("cost", 2.0),
            supplier=supplier,
            barcode=extra.pop("barcode", "012345678905"),
            inventory_value=extra.pop("inventory_value", None),
            **extra,
        )
    )


def sale(db, item_id, day, qty=1.0):
    db.add(
        Order(
            toast_order_guid=str(uuid.uuid4()),
            toast_check_guid=str(uuid.uuid4()),
            toast_selection_guid=str(uuid.uuid4()),
            business_date=day,
            opened_at=dt.datetime.combine(day, dt.time(16, 0), tzinfo=dt.timezone.utc),
            item_guid=item_id,
            item_name=item_id,
            quantity=qty,
        )
    )


def ago(days):
    return TODAY - dt.timedelta(days=days)


def received(days_ago):
    return f"{ago(days_ago).isoformat()}T15:00:00.000000Z"


@pytest.fixture
def store(db):
    """One upload 25 days ago and today's upload, with an item for every rule."""
    # Background item on both uploads, selling every day (regular).
    for day in (EARLY, LATEST):
        snap(db, "regular", "Regular Rice", 40, day=day)
    for d in range(1, 90):
        sale(db, "regular", ago(d), 2)

    # Negative inventory (retail yes, kitchen no).
    snap(db, "neg", "Negative Dal", -3)
    snap(db, "neg-kitchen", "Kitchen Samosa", -1, group="KITCHEN")

    # Refill from container: shelf 3 + container 20 -> flagged; shelf 8 -> not;
    # container empty -> not.
    snap(db, "toor", "Toor Dal 8lb", 3)
    snap(db, "toor-c", "Toor Dal 8lb", 20, price=None, supplier=None)
    snap(db, "atta", "Atta 10lb", 8)
    snap(db, "atta-c", "Atta 10lb", 30, price=None, supplier=None)
    snap(db, "ghee", "Ghee 1lt", 2)
    snap(db, "ghee-c", "Ghee 1lt", 0, price=None, supplier=None)

    # Slow + Review (negative on hand flag) vs slow without a review flag.
    # Since Oct 2026 Slow moving - Review only lists items with stock, so the
    # negative one is Negative inventory's alone.
    snap(db, "slow-rev", "Slow Pickle", -2)
    snap(db, "slow-ok", "Slow Chutney", 6)
    for item in ("slow-rev", "slow-ok"):
        sale(db, item, ago(20))
        sale(db, item, ago(50))
    # Slow + Review (one-day sales spike) with stock -> listed; the same item
    # at 0 on hand -> left out.
    snap(db, "slow-spike", "Spiky Sauce", 6)
    snap(db, "slow-spike-empty", "Spiky Empty", 0)
    for item in ("slow-spike", "slow-spike-empty"):
        sale(db, item, ago(15))
        sale(db, item, ago(40))
        sale(db, item, ago(60), 20)

    # Dead: no sales, stock on hand -> Dead with stock; no stock -> Dead -
    # zero stock; kitchen -> neither.
    snap(db, "dead", "Dusty Tin", 12, inventory_value=48.0)
    snap(db, "dead-empty", "Empty Tin", 0, inventory_last_received=received(70))
    snap(db, "dead-kitchen", "Kitchen Paste", 9, group="KITCHEN")
    snap(db, "dead-kitchen-empty", "Kitchen Empty", 0, group="KITCHEN")
    # Dead at 0 on the shelf but the Container row is negative -> Negative
    # inventory's (the container row), not Dead - zero stock.
    snap(db, "dead-negc", "Odd Tin", 0)
    snap(db, "dead-negc-c", "Odd Tin", -2, price=None, supplier=None)
    # Dead with shelf 0 and an empty container -> zero stock.
    snap(db, "dead-zc", "Boxed Tin", 0)
    snap(db, "dead-zc-c", "Boxed Tin", 0, price=None, supplier=None)
    # Dead with no count in Toast at all -> not "zero stock".
    snap(db, "dead-nocount", "Uncounted Tin", None)

    # Newly arrived (by Toast's last-received date; these were in the older
    # upload too, like every real catalog item).
    for day in (EARLY, LATEST):
        snap(db, "new-rcv", "New Biscuits", 10, day=day, inventory_last_received=received(10))
        snap(db, "new-sold", "New Chips", 10, day=day, inventory_last_received=received(10))
        snap(db, "new-too-soon", "New Candy", 10, day=day, inventory_last_received=received(2))
        snap(db, "new-too-old", "Old Candy", 10, day=day, inventory_last_received=received(40))
        snap(db, "restock", "Restocked Mix", 10, day=day, inventory_last_received=received(8))
        snap(db, "new-empty", "New Gone", 0, day=day, inventory_last_received=received(10))
    sale(db, "new-sold", ago(3))
    sale(db, "restock", ago(20))
    for day in (ago(12), LATEST):  # first shows up 12 days ago, no receipt date
        snap(db, "new-seen", "New Papad", 7, day=day)
    db.commit()
    return db


def open_keys(db, issue_type):
    return {r["issue_key"] for r in get_issue_list(db, issue_type)["open"]}


# --- Rules ---------------------------------------------------------------


def test_negative_inventory_rule(store):
    rows = get_issue_list(store, "negative_inventory")["open"]
    assert {r["issue_key"] for r in rows} == {"neg", "slow-rev", "dead-negc-c"}
    neg = next(r for r in rows if r["issue_key"] == "neg")
    assert neg["on_hand_qty"] == -3
    assert neg["product"]["supplier"] == "Acme"


def test_refill_from_container_rule(store):
    rows = get_issue_list(store, "refill_from_container")["open"]
    assert [r["issue_key"] for r in rows] == ["toor"]
    assert rows[0]["store_qty"] == 3
    assert rows[0]["container_qty"] == 20
    assert rows[0]["container_item_id"] == "toor-c"


def test_refill_skips_names_with_two_priced_rows(store):
    snap(store, "toor-2", "Toor Dal 8lb", 1, price=9.99)
    store.commit()
    assert open_keys(store, "refill_from_container") == set()


def test_slow_review_uses_the_supplier_projection_forecast(store):
    rows = get_issue_list(store, "slow_review")["open"]
    assert [r["issue_key"] for r in rows] == ["slow-spike"]
    row = rows[0]
    assert row["classification"] == "slow"
    assert row["review_flags"] == ["demand_spike"]
    assert row["review_reason"] == "Sales spike capped"
    assert row["units_sold"] == 22
    assert row["sale_days"] == 3
    assert row["on_hand_qty"] == 6

    # Same class and Review flag Supplier Projection shows (3-month window).
    projected = {
        i["item_id"]: i["forecast"]
        for i in compute_supplier_projection(store, "Acme", stock_issues.FORECAST_LOOKBACK_DAYS, [30], TODAY)
    }
    assert projected["slow-spike"]["classification"] == "slow"
    assert projected["slow-spike"]["needs_review"] is True
    # Slow + Review too, but at 0 / below 0 on hand: not on this list (owner,
    # Oct 2026); the negative one is on Negative inventory.
    for key in ("slow-spike-empty", "slow-rev"):
        assert projected[key]["classification"] == "slow"
        assert projected[key]["needs_review"] is True
    assert "slow-rev" in open_keys(store, "negative_inventory")
    assert projected["slow-ok"]["classification"] == "slow"
    assert projected["slow-ok"]["needs_review"] is False


def test_dead_with_stock_rule(store):
    rows = get_issue_list(store, "dead_with_stock")["open"]
    keys = {r["issue_key"] for r in rows}
    assert "dead" in keys
    assert not keys & {"dead-empty", "dead-zc", "dead-kitchen", "regular", "slow-rev", "slow-ok"}
    dead = next(r for r in rows if r["issue_key"] == "dead")
    assert dead["classification"] == "dead"
    assert dead["inventory_value"] == 48.0


def test_dead_zero_stock_rule(store):
    rows = {r["issue_key"]: r for r in get_issue_list(store, "dead_zero_stock")["open"]}
    # Dead at 0 (shelf alone, or shelf + an empty container), including a
    # never-sold item that arrived recently but has nothing left ("new-empty"
    # - Dead - newly arrived needs stock). Not: kitchen, a negative container
    # (Negative inventory's), no count at all, anything with stock.
    assert set(rows) == {"dead-empty", "dead-zc", "new-empty"}
    row = rows["dead-empty"]
    assert row["classification"] == "dead"
    assert row["zero_stock_rule"] == "dead"
    assert row["on_hand_qty"] == 0
    assert row["container_qty"] is None
    assert row["last_received"] == received(70)
    assert row["first_seen_date"] == LATEST.isoformat()
    assert row["available_days"] > 0 and row["window_days"] == stock_issues.FORECAST_LOOKBACK_DAYS
    assert row["product"]["on_hand_qty"] == 0
    assert rows["dead-zc"]["container_qty"] == 0
    assert "dead-negc-c" in open_keys(store, "negative_inventory")


def test_dead_zero_stock_never_overlaps_the_other_lists(store):
    zero = open_keys(store, "dead_zero_stock")
    for other in OTHER_STOCK_LISTS:
        assert not zero & open_keys(store, other), other


def _fc(classification, needs_review=False, flags=()):
    return {
        "classification": classification,
        "needs_review": needs_review,
        "flags": list(flags),
        "base_daily_demand": 0.1,
        "trend_factor": 1.0,
        "units_sold": 2,
        "sale_days": 2,
        "available_days": 80.0,
        "window_days": 90,
        "recent_14_day_sales": 0,
    }


def _latest(*rows):
    return {
        item_id: InventorySnapshot(item_id=item_id, name=item_id, inventory_quantity=shelf, snapshot_date=LATEST)
        for item_id, shelf in rows
    }


@pytest.mark.parametrize(
    "on_hand,listed",
    [(5.0, True), (0.25, True), (0.0, False), (-3.0, False), (None, True)],
)
def test_slow_review_leaves_out_zero_and_negative_on_hand(on_hand, listed):
    """Owner, Oct 2026: "slow moving zero quantity need not be there". An
    item with no count at all (None) has nothing saying it's empty, so it
    stays."""
    latest = _latest(("x", on_hand))
    forecasts = {"x": {"forecast": _fc("slow", True, ["prolonged_stockout"]), "on_hand": on_hand, "container_qty": None}}
    keys = [r["item_id"] for r in stock_issues.slow_review_items(latest, forecasts)]
    assert keys == (["x"] if listed else [])


def _unsold_fc(classification="slow"):
    """A forecast for an item that sat at 0 with no sale in the window."""
    flags = ["dead_inventory"] if classification == "dead" else ["insufficient_history", "prolonged_stockout"]
    return {
        **_fc(classification, needs_review=classification != "dead", flags=flags),
        "units_sold": 0,
        "sale_days": 0,
        "available_days": 0.0,
    }


@pytest.mark.parametrize("klass,rule", [("dead", "dead"), ("slow", "no_sale")])
@pytest.mark.parametrize(
    "shelf,container,listed",
    [
        (0.0, None, True),
        (0.0, 0.0, True),
        (5.0, None, False),  # Dead with stock
        (0.0, 3.0, False),  # stock in the container -> Dead with stock
        (-2.0, None, False),  # Negative inventory
        (-2.0, 5.0, False),  # negative shelf, even with container stock
        (0.0, -1.0, False),  # negative container row -> Negative inventory
        (2.0, -2.0, False),  # adds up to 0, but the container row is Negative inventory's
        (None, None, False),  # no count at all
    ],
)
def test_dead_zero_stock_quantities(db, klass, rule, shelf, container, listed):
    """Both ways onto the list (dead class; no sale in 90 days) share one
    stock rule: shelf + container exactly 0, no row below 0, a count on
    file."""
    snap(db, "x", "X Tin", shelf, day=ago(100))  # in the uploads long enough
    db.commit()
    latest = _latest(("x", shelf))
    on_hand = shelf if container is None else (shelf or 0.0) + container
    forecasts = {"x": {"forecast": _unsold_fc(klass), "on_hand": on_hand, "container_qty": container}}
    rows = stock_issues.dead_zero_stock_items(db, latest, forecasts, {})
    assert [r["item_id"] for r in rows] == (["x"] if listed else [])
    if listed:
        assert rows[0]["zero_stock_rule"] == rule
        assert rows[0]["first_seen_date"] == ago(100).isoformat()
    # Never on Dead with stock or Slow moving - Review as well.
    others = stock_issues.dead_with_stock_items(latest, forecasts) + stock_issues.slow_review_items(latest, forecasts)
    assert not (listed and others)


@pytest.mark.parametrize(
    "first_seen,received_ago,last_sold_ago,listed",
    [
        (60, None, None, True),  # in the uploads 60 days, never sold
        (59, None, None, False),  # brand new: 59 days in the uploads, never received
        (10, 60, None, True),  # new to the uploads, but last received 60 days ago
        (10, 59, None, False),  # ...59 days ago: still brand new
        # Sold once long ago: not brand new, even though it first showed up
        # in the uploads 10 days ago and was never received (verifier, Oct 4:
        # an old item missing from the backfill first shows up on Aug 24).
        (10, None, 200, True),
        (59, None, 91, True),  # ...and the same with the sale just past 90 days
        (59, 59, None, False),  # never sold, new to the uploads and received 59 days ago
        (100, 5, None, True),  # restocked recently, in the uploads for 100 days
        (100, None, 91, True),  # last sale 91 days ago
        (100, None, 90, False),  # a sale 90 days ago is within the last 90 days
        (100, None, 0, False),  # sold today
    ],
)
def test_dead_zero_stock_no_sale_rule_boundaries(db, first_seen, received_ago, last_sold_ago, listed):
    """Owner, Oct 4: an item at 0 with no sale in 90 days (or never) is
    listed whatever its forecast class - unless it's a brand-new catalog
    entry: never sold, and first seen in the uploads, or last received,
    under 60 days before the latest upload."""
    snap(db, "x", "X Tin", 0, day=ago(first_seen))
    db.commit()
    latest = _latest(("x", 0.0))
    if received_ago is not None:
        latest["x"].inventory_last_received = received(received_ago)
    last_sold = {} if last_sold_ago is None else {"x": ago(last_sold_ago)}
    forecasts = {"x": {"forecast": _unsold_fc("slow"), "on_hand": 0.0, "container_qty": None}}
    rows = stock_issues.dead_zero_stock_items(db, latest, forecasts, last_sold, today=TODAY)
    assert [r["item_id"] for r in rows] == (["x"] if listed else [])
    if listed:
        assert rows[0]["zero_stock_rule"] == "no_sale"
        assert rows[0]["classification"] == "slow"
        assert rows[0]["first_seen_date"] == ago(first_seen).isoformat()


def test_dead_zero_stock_other_classes_need_90_days_without_a_sale(db):
    """A slow / regular item at 0 that sold recently is a reorder question,
    not a dead one: not listed. The dead class needs no sale check (it
    already means no sale in 60 days while in stock)."""
    for item_id in ("s", "r", "d"):
        snap(db, item_id, item_id, 0, day=ago(100))
    db.commit()
    latest = _latest(("s", 0.0), ("r", 0.0), ("d", 0.0))
    forecasts = {
        "s": {"forecast": _fc("slow", True), "on_hand": 0.0, "container_qty": None},
        "r": {"forecast": _fc("regular"), "on_hand": 0.0, "container_qty": None},
        "d": {"forecast": _fc("dead", flags=["dead_inventory"]), "on_hand": 0.0, "container_qty": None},
    }
    last_sold = {"s": ago(30), "r": ago(1), "d": ago(75)}
    rows = stock_issues.dead_zero_stock_items(db, latest, forecasts, last_sold)
    assert [(r["item_id"], r["zero_stock_rule"]) for r in rows] == [("d", "dead")]


def test_dead_zero_stock_glossary_quotes_the_rule():
    from app import glossary as g
    from app.services.demand_forecast import DEFAULT_CONFIG

    rule = g.TERMS_BY_KEY["issue_dead_zero_stock"]["rule"]
    for days in (
        DEFAULT_CONFIG.dead_quiet_days,
        stock_issues.ZERO_STOCK_NO_SALE_DAYS,
        stock_issues.ZERO_STOCK_MIN_AGE_DAYS,
    ):
        assert f"{days} days" in rule, days
    assert "Negative inventory" in rule and "brand new" in rule


HISTORY_DAYS = 100  # daily uploads, like the real store since the backfill


def _daily(db, item_id, name, qty, first=HISTORY_DAYS - 1, **kw):
    """One row a day from `first` days ago to today. `qty` is a number, or
    a function of days-ago."""
    for d in range(first, -1, -1):
        snap(db, item_id, name, qty(d) if callable(qty) else qty, day=ago(d), **kw)


@pytest.fixture
def daily_store(db):
    """Daily uploads for the last 100 days (so the forecast sees real
    stockouts, like on prod), with an item for every stock list and every
    way an item at 0 can go."""
    _daily(db, "bg", "Regular Rice", 40)
    for d in range(1, 90):
        sale(db, "bg", ago(d), 2)

    # Dead - zero stock, "no sale" way: at 0 the whole window, so the
    # forecast classes them slow, not dead.
    _daily(db, "zs-never", "Never Sold Tin", 0)
    _daily(db, "zs-long-ago", "Old Sale Tin", 0)
    sale(db, "zs-long-ago", ago(120))
    _daily(db, "zs-box", "Boxed Tin", 0)  # shelf 0 + an empty container
    _daily(db, "zs-box-c", "Boxed Tin", 0, price=None, supplier=None)
    # New to the uploads 20 days ago, but last received 70 days ago.
    _daily(db, "zs-rcv-old", "Relisted Tin", 0, first=20, inventory_last_received=received(70))
    # Dead - zero stock, "dead" way: in stock until 20 days ago, never sold.
    _daily(db, "zs-dead", "Dead Empty Tin", lambda d: 5 if d >= 20 else 0)

    # At 0, but on no list.
    _daily(db, "zs-sold-30", "Recent Sale Tin", 0)  # sold 30 days ago
    sale(db, "zs-sold-30", ago(30))
    _daily(db, "zs-new", "Brand New Tin", 0, first=20)  # brand new, never received
    _daily(db, "zs-rcv-new", "Fresh Tin", 0, first=20, inventory_last_received=received(10))
    _daily(db, "zs-nocount", "Uncounted Tin", None)  # no count in Toast
    _daily(db, "zs-kitchen", "Kitchen Tin", 0, group="KITCHEN")

    # Negative inventory: a negative shelf, a negative container row, and a
    # shelf + container that adds up to 0 only because the container is
    # negative (that container row is Negative inventory's; the item isn't
    # zero stock).
    _daily(db, "zs-neg", "Minus Tin", -1)
    _daily(db, "zs-negc", "Odd Tin", 0)
    _daily(db, "zs-negc-c", "Odd Tin", -2, price=None, supplier=None)
    _daily(db, "zs-offset", "Offset Tin", 2)
    _daily(db, "zs-offset-c", "Offset Tin", -2, price=None, supplier=None)

    # The other stock lists, so each one has something on it.
    _daily(db, "dws", "Dusty Jar", 12)  # Dead with stock
    _daily(db, "dna", "New Snack", 10, first=10)  # Dead - newly arrived
    _daily(db, "sr", "Slow Pickle", 6)  # Slow moving - Review (sales spike)
    for d, qty in ((15, 1), (40, 1), (60, 20)):
        sale(db, "sr", ago(d), qty)
    _daily(db, "rf", "Toor Dal 8lb", 3)  # Refill from container
    _daily(db, "rf-c", "Toor Dal 8lb", 20, price=None, supplier=None)
    for d in range(1, 90):
        sale(db, "rf", ago(d))
    db.commit()
    return db


def test_dead_zero_stock_no_sale_rule_on_daily_uploads(daily_store):
    rows = {r["issue_key"]: r for r in get_issue_list(daily_store, "dead_zero_stock")["open"]}
    assert set(rows) == {"zs-never", "zs-long-ago", "zs-box", "zs-rcv-old", "zs-dead"}
    # Dead in the forecast AND unsold for 90 days: listed once, as dead.
    assert rows["zs-dead"]["zero_stock_rule"] == "dead"
    assert rows["zs-dead"]["classification"] == "dead"
    for key in ("zs-never", "zs-long-ago", "zs-box", "zs-rcv-old"):
        assert rows[key]["zero_stock_rule"] == "no_sale", key
        assert rows[key]["classification"] == "slow", key
        assert rows[key]["units_sold"] == 0 and rows[key]["on_hand_qty"] == 0, key
    assert rows["zs-never"]["first_seen_date"] == ago(HISTORY_DAYS - 1).isoformat()
    assert rows["zs-never"]["product"]["last_sold_date"] is None
    assert rows["zs-long-ago"]["product"]["last_sold_date"] == ago(120).isoformat()
    assert rows["zs-rcv-old"]["first_seen_date"] == ago(20).isoformat()
    assert rows["zs-rcv-old"]["last_received"] == received(70)
    assert rows["zs-box"]["container_qty"] == 0
    # Same counts as the summary.
    summary = {s["issue_type"]: s["open_count"] for s in get_summary(daily_store)}
    assert summary["dead_zero_stock"] == 5


def test_dead_zero_stock_old_item_missing_from_the_backfill_is_not_brand_new(daily_store):
    """Verifier, Oct 4 prod data: the backfilled days (Jun-Aug) leave out
    items Toast wasn't counting then, so an old item can first show up in
    the uploads on Aug 24. Thums Up Glass Light 300ml - sold Jun 3, nothing
    since, at 0, never received - was held back as "brand new" for that.
    A sale on record proves it's not a new catalog entry; an item that has
    never sold still needs 60 days in the uploads (or a receipt date)."""
    _daily(daily_store, "zs-gap", "Glass Bottle Soda", 0, first=40)  # missing from the older uploads
    sale(daily_store, "zs-gap", ago(120))  # sold before it ever showed up in an upload
    daily_store.commit()
    rows = {r["issue_key"]: r for r in get_issue_list(daily_store, "dead_zero_stock")["open"]}
    assert rows["zs-gap"]["zero_stock_rule"] == "no_sale"
    assert rows["zs-gap"]["first_seen_date"] == ago(40).isoformat()
    assert rows["zs-gap"]["product"]["last_sold_date"] == ago(120).isoformat()
    # Never sold and only 20 days in the uploads: still brand new.
    assert "zs-new" not in rows and "zs-rcv-new" not in rows


def test_dead_zero_stock_never_double_lists_an_item(daily_store):
    """No item is on Dead - zero stock and any other stock list, by item_id
    (a Refill row's container id included) or by product name (Negative
    inventory lists a Container row under its own id)."""
    lists = {t: get_issue_list(daily_store, t)["open"] for t in NEW_TYPES}
    zero = lists["dead_zero_stock"]
    assert {r["zero_stock_rule"] for r in zero} == {"dead", "no_sale"}
    zero_ids = {r["item_id"] for r in zero}
    zero_names = {r["name"] for r in zero}
    for other in OTHER_STOCK_LISTS:
        rows = lists[other]
        assert rows, f"{other} is empty - the overlap check would prove nothing"
        ids = {r["item_id"] for r in rows} | {r["container_item_id"] for r in rows if r.get("container_item_id")}
        assert not zero_ids & ids, other
        assert not zero_names & {r["name"] for r in rows}, other
    # The negative rows that sit next to a zero total are Negative inventory's.
    assert {"zs-neg", "zs-negc-c", "zs-offset-c"} <= {r["item_id"] for r in lists["negative_inventory"]}
    assert not {"zs-negc", "zs-offset"} & zero_ids


def test_dead_new_arrival_rule(store):
    rows = {r["issue_key"]: r for r in get_issue_list(store, "dead_new_arrival")["open"]}
    # Brand-new products only: "restock" sold before its latest delivery and
    # "new-sold" sold after it, so neither is listed.
    assert set(rows) == {"new-rcv", "new-seen"}
    assert rows["new-rcv"]["arrived_via"] == "received"
    assert rows["new-rcv"]["days_since_arrival"] == 10
    assert rows["new-seen"]["arrived_via"] == "first_upload"
    assert rows["new-seen"]["arrived_date"] == ago(12).isoformat()
    # Kept for older app builds: always a never-sold item now.
    for row in rows.values():
        assert row["arrival_kind"] == "New item"
        assert row["last_sold_before_arrival"] is None


def test_dead_new_arrival_leaves_out_restocks(store):
    """An item that has ever sold is a restock, not a new product - even when
    it hasn't sold since this delivery, and however long ago that sale was."""
    for day in (EARLY, LATEST):
        snap(store, "restock-old", "Old Favourite", 15, day=day, inventory_last_received=received(9))
    sale(store, "restock-old", ago(80))
    store.commit()
    keys = open_keys(store, "dead_new_arrival")
    assert "restock" not in keys
    assert "restock-old" not in keys
    assert keys == {"new-rcv", "new-seen"}


def test_forecast_lists_empty_when_forecast_switched_off(store, monkeypatch):
    monkeypatch.setattr(stock_issues, "get_settings", lambda: types.SimpleNamespace(projection_method="legacy"))
    assert get_issue_list(store, "slow_review")["open"] == []
    assert get_issue_list(store, "dead_with_stock")["open"] == []
    assert get_issue_list(store, "dead_zero_stock")["open"] == []
    # The non-forecast lists don't depend on it.
    assert open_keys(store, "refill_from_container") == {"toor"}


def test_forecast_is_cached_per_upload(store, monkeypatch):
    calls = []
    real = stock_issues._compute_forecasts

    def counting(*args):
        calls.append(1)
        return real(*args)

    monkeypatch.setattr(stock_issues, "_compute_forecasts", counting)
    get_issue_list(store, "slow_review")
    get_issue_list(store, "dead_with_stock")
    assert len(calls) == 1
    # A new upload recomputes straight away.
    snap(store, "brand-new", "Brand New", 5, day=LATEST + dt.timedelta(days=1))
    store.commit()
    get_issue_list(store, "slow_review")
    assert len(calls) == 2


# --- API: summary, reasons, close / reopen, report, CSV --------------------


def test_summary_lists_every_type_with_counts(store, admin_client):
    items = admin_client.get("/api/issues/summary").json()["items"]
    types_ = [i["issue_type"] for i in items]
    # The original four stay first (the App Store app reads them by key).
    assert types_[:4] == ["missing_barcode", "invalid_barcode", "duplicate", "dead_stock"]
    assert set(NEW_TYPES) <= set(types_)
    counts = {i["issue_type"]: i["open_count"] for i in items}
    assert counts["negative_inventory"] == 3
    assert counts["refill_from_container"] == 1
    assert counts["slow_review"] == 1
    assert counts["dead_zero_stock"] == 3
    assert counts["dead_new_arrival"] == 2
    # Sep 30: came_back_count is additive (FIXISSUES_CART_CONTRACT); the old fields stay.
    # Oct 14 (W4): open_by_lead / open_no_lead are additive too.
    assert all(
        set(i) == {"issue_type", "label", "open_count", "closed_count", "came_back_count", "open_by_lead", "open_no_lead"}
        for i in items
    )


def test_reasons_for_new_types(admin_client):
    reasons = admin_client.get("/api/issues/reasons").json()
    for t in NEW_TYPES:
        keys = [r["key"] for r in reasons[t]["reasons"]]
        assert "fixed" in keys and keys[-2:] == ["not_an_issue", "other"], t
    labels = {r["key"]: r["label"] for r in reasons["refill_from_container"]["reasons"]}
    assert labels["fixed"] == "Moved stock to the shelf"
    labels = {r["key"]: r["label"] for r in reasons["negative_inventory"]["reasons"]}
    assert labels["fixed"] == "Count corrected in Toast"
    assert "sold_before_received" in labels
    labels = {r["key"]: r["label"] for r in reasons["dead_zero_stock"]["reasons"]}
    assert labels == {
        "discontinued": "Discontinued - won't reorder",
        "deactivated": "Deactivated in Toast",
        "seasonal": "Seasonal - will restock",
        "reorder": "Reorder - it should sell",
        "fixed": "Count corrected in Toast",
        "not_an_issue": "Not an issue (duplicate / false alarm)",
        "other": "Other",
    }
    assert reasons["dead_zero_stock"]["label"] == "Dead - zero stock"


@pytest.mark.parametrize(
    "issue_type,key,reason",
    [
        ("negative_inventory", "neg", "sold_before_received"),
        ("refill_from_container", "toor", "container_count_wrong"),
        ("slow_review", "slow-spike", "reviewed_ok"),
        ("dead_with_stock", "dead", "markdown"),
        ("dead_zero_stock", "dead-empty", "discontinued"),
        ("dead_zero_stock", "dead-zc", "deactivated"),
        ("dead_zero_stock", "new-empty", "reorder"),
        ("dead_new_arrival", "new-rcv", "give_time"),
    ],
)
def test_close_and_reopen(store, admin_client, issue_type, key, reason):
    res = admin_client.post("/api/issues/close", json={"issue_type": issue_type, "issue_keys": [key], "reason": reason})
    assert res.status_code == 200 and res.json() == {"closed": 1}
    data = admin_client.get(f"/api/issues/{issue_type}").json()
    assert key not in {r["issue_key"] for r in data["open"]}
    closed = next(r for r in data["closed"] if r["issue_key"] == key)
    assert closed["reason"] == reason and closed["closed_by"] == "boss"
    assert closed["product"] is not None

    report = admin_client.get("/api/issues/report").json()
    assert report["total"] == 1
    item = report["days"][0]["items"][0]
    assert item["issue_type"] == issue_type
    assert item["issue_label"] == ISSUE_TYPES[issue_type]["label"]

    summary = {i["issue_type"]: i for i in admin_client.get("/api/issues/summary").json()["items"]}
    assert summary[issue_type]["closed_count"] == 1

    res = admin_client.post("/api/issues/reopen", json={"issue_type": issue_type, "issue_keys": [key]})
    assert res.json() == {"reopened": 1}
    assert key in open_keys(store, issue_type)


def test_bulk_close_and_other_needs_text(store, admin_client):
    body = {"issue_type": "dead_new_arrival", "issue_keys": ["new-rcv", "new-seen"], "reason": "other"}
    assert admin_client.post("/api/issues/close", json=body).status_code == 400
    body["reason_detail"] = "Put on the end cap"
    assert admin_client.post("/api/issues/close", json=body).json() == {"closed": 2}
    assert open_keys(store, "dead_new_arrival") == set()


def test_fixed_comes_back_when_a_newer_upload_still_shows_it(store, admin_client):
    admin_client.post(
        "/api/issues/close", json={"issue_type": "refill_from_container", "issue_keys": ["toor"], "reason": "fixed"}
    )
    assert open_keys(store, "refill_from_container") == set()
    # Closed two days before the latest upload -> the upload still shows it.
    res = store.query(IssueResolution).filter_by(issue_type="refill_from_container").one()
    res.closed_at = dt.datetime.combine(ago(2), dt.time(15, 0), tzinfo=dt.timezone.utc)
    store.commit()
    row = next(r for r in get_issue_list(store, "refill_from_container")["open"] if r["issue_key"] == "toor")
    assert row["came_back"]["reason"] == "fixed"


def test_dead_zero_stock_closes_like_the_other_dead_lists(store, admin_client):
    """"Count corrected" is re-checked (back as came-back if a later upload
    still shows it); the other reasons stay closed; restocking it moves the
    closed row to "no longer detected" (it's Dead with stock now)."""
    for key, reason in (("dead-empty", "fixed"), ("dead-zc", "seasonal")):
        res = admin_client.post(
            "/api/issues/close", json={"issue_type": "dead_zero_stock", "issue_keys": [key], "reason": reason}
        )
        assert res.json() == {"closed": 1}
    for r in store.query(IssueResolution).filter_by(issue_type="dead_zero_stock"):
        r.closed_at = dt.datetime.combine(ago(2), dt.time(15, 0), tzinfo=dt.timezone.utc)
    store.commit()
    data = get_issue_list(store, "dead_zero_stock")
    back = {r["issue_key"]: r for r in data["open"] if r.get("reopened")}
    assert set(back) == {"dead-empty"}
    assert back["dead-empty"]["reopened"]["how"] == "fixed_but_still_showing"
    assert data["came_back_count"] == 1
    assert {r["issue_key"] for r in data["closed"]} == {"dead-zc"}
    closed = data["closed"][0]
    assert closed["reason_label"] == "Seasonal - will restock" and closed["snooze_until"] is None

    store.query(InventorySnapshot).filter_by(item_id="dead-zc").update({"inventory_quantity": 4})
    store.commit()
    stock_issues.clear_forecast_cache()
    data = get_issue_list(store, "dead_zero_stock")
    assert data["closed"][0]["issue_key"] == "dead-zc" and data["closed"][0]["no_longer_detected"] is True
    assert "dead-zc" in open_keys(store, "dead_with_stock")

    report = admin_client.get("/api/issues/report").json()
    assert report["days"][0]["by_type"] == {"dead_zero_stock": 2}
    assert {i["issue_label"] for i in report["days"][0]["items"]} == {"Dead - zero stock"}
    lines = admin_client.get("/api/issues/dead_zero_stock/export?status=closed").text.splitlines()
    assert lines[0].endswith("Closed Reason,Closed By,Closed On")
    assert any(line.startswith("Boxed Tin,") and "Seasonal - will restock" in line for line in lines[1:])


def test_dead_zero_stock_reasons_have_glossary_terms(admin_client):
    reasons = {r["key"]: r for r in admin_client.get("/api/issues/reasons").json()["dead_zero_stock"]["reasons"]}
    assert reasons["seasonal"]["glossary_key"] == "reason_dead_zero_stock_seasonal"
    assert reasons["deactivated"]["glossary_key"] == "reason_deactivated"
    assert reasons["reorder"]["glossary_key"] == "reason_reorder"
    assert reasons["discontinued"]["glossary_key"] == "reason_discontinued"
    assert reasons["fixed"]["outcome"] == "rechecked"
    assert all(reasons[k]["outcome"] == "stays_closed" for k in ("seasonal", "deactivated", "reorder", "discontinued"))
    assert all(r["does"] for r in reasons.values())


def test_no_longer_detected_after_shelf_refilled(store, admin_client):
    admin_client.post(
        "/api/issues/close",
        json={"issue_type": "refill_from_container", "issue_keys": ["toor"], "reason": "fixed"},
    )
    store.query(InventorySnapshot).filter_by(item_id="toor").update({"inventory_quantity": 12})
    store.commit()
    data = get_issue_list(store, "refill_from_container")
    assert data["open"] == []
    assert data["closed"][0]["no_longer_detected"] is True


@pytest.mark.parametrize(
    "issue_type,header",
    [
        ("negative_inventory", "On Hand"),
        ("refill_from_container", "Store Qty,Container Qty"),
        ("slow_review", "Why Review"),
        ("dead_with_stock", "Stock Value"),
        ("dead_zero_stock", "On Hand,Days In Stock (90),Last Sold,Last Received,Cost"),
        ("dead_new_arrival", "Arrived,Days Since,On Hand"),
    ],
)
def test_csv_export(store, admin_client, issue_type, header):
    res = admin_client.get(f"/api/issues/{issue_type}/export")
    assert res.status_code == 200
    first_line = res.text.splitlines()[0]
    assert first_line.startswith("Product,Category,Vendor,")
    assert header in first_line
    assert len(res.text.splitlines()) >= 2
    closed = admin_client.get(f"/api/issues/{issue_type}/export?status=closed")
    assert "Closed Reason" in closed.text.splitlines()[0]


def test_refill_csv_has_both_quantities(store, admin_client):
    lines = admin_client.get("/api/issues/refill_from_container/export").text.splitlines()
    assert lines[1].startswith("Toor Dal 8lb,Snacks,Acme,3,20")


# --- Verifier additions (Sep 29) ----------------------------------------------


def test_first_seen_ignores_an_item_missing_only_from_a_thin_day(store):
    """The backfilled days list fewer items than a real upload. An old item
    that happens to be missing from the first upload of the 30-day stretch
    is not a new arrival."""
    for day in (ago(40), ago(12), LATEST):  # not in the EARLY upload
        snap(store, "old-gap", "Old Masala", 6, day=day)
    store.commit()
    assert "old-gap" not in open_keys(store, "dead_new_arrival")
    assert "new-seen" in open_keys(store, "dead_new_arrival")


def _close(admin_client, store, issue_type, key, reason, days_ago):
    res = admin_client.post("/api/issues/close", json={"issue_type": issue_type, "issue_keys": [key], "reason": reason})
    assert res.json() == {"closed": 1}
    row = store.query(IssueResolution).filter_by(issue_type=issue_type, issue_key=key).one()
    row.closed_at = dt.datetime.combine(ago(days_ago), dt.time(15, 0), tzinfo=dt.timezone.utc)
    store.commit()
    store.expire_all()


def test_negative_close_covers_one_occurrence(store, admin_client):
    _close(admin_client, store, "negative_inventory", "neg", "sold_before_received", 5)
    assert "neg" not in open_keys(store, "negative_inventory")  # still the same occurrence
    snap(store, "neg", "Negative Dal", 4, day=ago(3))  # count went positive after the close...
    store.commit()
    rows = {r["issue_key"]: r for r in get_issue_list(store, "negative_inventory")["open"]}
    assert "neg" in rows  # ...and is negative again today: a new occurrence
    assert "came_back" not in rows["neg"]


def test_negative_sticky_reason_stays_closed(store, admin_client):
    _close(admin_client, store, "negative_inventory", "neg", "loose_weighed", 5)
    snap(store, "neg", "Negative Dal", 4, day=ago(3))
    store.commit()
    assert "neg" not in open_keys(store, "negative_inventory")


def test_refill_close_covers_one_occurrence(store, admin_client):
    _close(admin_client, store, "refill_from_container", "toor", "keep_in_container", 5)
    assert "toor" not in open_keys(store, "refill_from_container")
    snap(store, "toor", "Toor Dal 8lb", 12, day=ago(3))  # shelf was refilled after the close
    snap(store, "toor-c", "Toor Dal 8lb", 8, day=ago(3), price=None, supplier=None)
    store.commit()
    assert "toor" in open_keys(store, "refill_from_container")


def test_refill_not_an_issue_stays_closed(store, admin_client):
    _close(admin_client, store, "refill_from_container", "toor", "not_an_issue", 5)
    snap(store, "toor", "Toor Dal 8lb", 12, day=ago(3))
    store.commit()
    assert "toor" not in open_keys(store, "refill_from_container")


def _count_computes(monkeypatch):
    calls = []
    real = stock_issues._compute_forecasts

    def counting(*args):
        calls.append(1)
        return real(*args)

    monkeypatch.setattr(stock_issues, "_compute_forecasts", counting)
    return calls


def test_warm_up_fills_the_cache(store, monkeypatch):
    from app.services.fix_issues import warm_fix_issues_cache

    calls = _count_computes(monkeypatch)
    warm_fix_issues_cache(store.get_bind())
    assert len(calls) == 1
    assert open_keys(store, "slow_review") == {"slow-spike"}
    assert len(calls) == 1  # the page load used the warmed result
    warm_fix_issues_cache(store.get_bind(), refresh=True)  # e.g. after the nightly sales sync
    assert len(calls) == 2


def test_warm_up_never_raises_and_a_failure_is_not_cached(store, monkeypatch):
    from app.services.fix_issues import warm_fix_issues_cache

    def boom(*args):
        raise RuntimeError("db gone")

    monkeypatch.setattr(stock_issues, "_compute_forecasts", boom)
    warm_fix_issues_cache(store.get_bind())  # logged, not raised
    assert get_issue_list(store, "slow_review")["open"] == []
    monkeypatch.undo()
    assert open_keys(store, "slow_review") == {"slow-spike"}  # recomputed, not a cached empty result


def test_inventory_upload_warms_the_cache_after_responding(store, admin_client, monkeypatch):
    import app.routers.inventory as inventory_router

    warmed = []
    monkeypatch.setattr(inventory_router, "warm_fix_issues_cache", lambda bind: warmed.append(bind))
    csv = b"Item ID,Name,Price,Quantity\nup-1,Uploaded Item,1.99,3\n"
    res = admin_client.post(
        "/api/inventory/upload",
        files={"file": ("items.csv", csv, "text/csv")},
        params={"snapshot_date": (LATEST + dt.timedelta(days=1)).isoformat()},
    )
    assert res.status_code == 200, res.text
    assert len(warmed) == 1

    # A failed upload doesn't warm anything.
    res = admin_client.post("/api/inventory/upload", files={"file": ("bad.csv", b"Name\nx\n", "text/csv")})
    assert res.status_code == 400
    assert len(warmed) == 1


def test_startup_warm_up_is_a_scheduler_job(monkeypatch):
    from app import scheduler

    jobs = {}

    class FakeScheduler:
        def __init__(self, **_):
            pass

        def add_job(self, func, trigger=None, id=None, **_):
            jobs[id] = func

        def start(self):
            pass

    monkeypatch.setattr(scheduler, "BackgroundScheduler", FakeScheduler)
    monkeypatch.setattr(scheduler, "_scheduler", None)
    settings = scheduler.get_settings()
    monkeypatch.setattr(settings, "scheduler_enabled", True)
    try:
        scheduler.start_scheduler()
    finally:
        scheduler._scheduler = None
    assert jobs["startup_fix_issues_warmup"] is scheduler.warm_fix_issues_cache

    # SCHEDULER_ENABLED=false -> no jobs at all, the warm-up included.
    jobs.clear()
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    assert scheduler.start_scheduler() is None
    assert jobs == {}


def test_sold_columns_are_units_not_dollars(store, admin_client):
    """Toast's last_30_day_sales is dollars; last_30_day_orders is units."""
    store.query(InventorySnapshot).filter_by(item_id="toor", snapshot_date=LATEST).update(
        {"last_30_day_sales": 29.98, "last_30_day_orders": 2.0}
    )
    store.commit()
    row = get_issue_list(store, "refill_from_container")["open"][0]
    assert row["product"]["last_30_day_units"] == 2.0
    assert row["product"]["last_30_day_sales"] == 29.98
    lines = admin_client.get("/api/issues/refill_from_container/export").text.splitlines()
    header = lines[0].split(",")
    assert lines[1].split(",")[header.index("Sold (30 days)")] == "2"


# --- Forecast cache vs category edits (review, Sep 30) ------------------------


def test_stale_background_refresh_does_not_overwrite_a_newer_entry(store, monkeypatch):
    """A refresh that started before a clear (category edit) must not write
    its old-rules result over the entry made after the clear."""
    from app.services.fix_issues import EXCLUDED_CATEGORY_GROUPS
    from app.services.reorder import latest_inventory_by_item

    latest = latest_inventory_by_item(store)
    stock_issues.forecasts_by_item(store, latest, EXCLUDED_CATEGORY_GROUPS)
    (key,) = stock_issues._cache.keys()
    old_generation = stock_issues._generation

    stock_issues.clear_forecast_cache()  # e.g. a category edit
    fresh = stock_issues.forecasts_by_item(store, latest, EXCLUDED_CATEGORY_GROUPS)
    assert key in stock_issues._cache

    monkeypatch.setattr(stock_issues, "_compute_forecasts", lambda *a: {"stale": "old rules"})
    stock_issues._refresh_in_background(
        store.get_bind(), key, set(EXCLUDED_CATEGORY_GROUPS), key[1], old_generation
    )
    assert stock_issues._cache[key]["data"] is fresh

    # A refresh from the current generation still updates the entry.
    stock_issues._refresh_in_background(
        store.get_bind(), key, set(EXCLUDED_CATEGORY_GROUPS), key[1], stock_issues._generation
    )
    assert stock_issues._cache[key]["data"] == {"stale": "old rules"}


def test_cache_miss_uses_category_rules_committed_after_latest_was_loaded(store):
    """A request loads the inventory, then a category edit (in another
    request) moves an item to Market Kitchen and clears the cache: the
    forecast this request caches must already leave that item out."""
    from app.models import CategoryOverride
    from app.services.fix_issues import EXCLUDED_CATEGORY_GROUPS
    from app.services.reorder import latest_inventory_by_item

    latest = latest_inventory_by_item(store)  # loaded (with rules) before the edit
    store.add(
        CategoryOverride(
            match_type="item_id", match_value="dead", category_group="KITCHEN",
            category="Kitchen", subcategory=None, active=True,
        )
    )
    store.commit()
    stock_issues.clear_forecast_cache()
    forecasts = stock_issues.forecasts_by_item(store, latest, EXCLUDED_CATEGORY_GROUPS)
    assert "dead" not in forecasts
    assert "regular" in forecasts


def test_event_menu_food_is_left_out_like_the_kitchen(store):
    """Toast's "Event Menu" group (in-store food for events, since Sep 28)
    has no barcodes or shelf stock - like Market Kitchen / Catering Menu."""
    snap(store, "ev-samosa", "Veg Samosa", None, group="Event Menu", barcode=None, supplier=None)
    for d in (1, 2):
        sale(store, "ev-samosa", ago(d), 30)
    store.commit()
    for issue_type in ISSUE_TYPES:
        assert "ev-samosa" not in open_keys(store, issue_type), issue_type


# --- Pre-App-Store audit: retired issue types, empty exports ---------------------


def test_closed_report_skips_a_retired_issue_type(store, admin_client):
    _close(admin_client, store, "negative_inventory", "neg", "fixed", 1)
    store.add(
        IssueResolution(
            issue_type="retired_type", issue_key="x", item_name="Old Thing", reason="weird_reason",
            closed_by_name="someone", closed_at=dt.datetime.combine(ago(1), dt.time(15, 0), tzinfo=dt.timezone.utc),
        )
    )
    store.commit()
    res = admin_client.get("/api/issues/report")
    assert res.status_code == 200, res.text
    body = res.json()
    types = {i["issue_type"] for d in body["days"] for i in d["items"]}
    assert "retired_type" not in types and "negative_inventory" in types
    assert body["total"] == sum(len(d["items"]) for d in body["days"])
    # The label helpers don't raise for an unknown type either.
    from app.services.fix_issues import reason_label, reason_outcome

    assert reason_label("retired_type", "weird_reason") == "weird_reason"
    assert reason_outcome("retired_type", "weird_reason") == "stays_closed"


def test_export_of_a_type_without_extra_columns_uses_the_base_ones(store, admin_client, monkeypatch):
    from app.routers import issues as issues_router

    monkeypatch.delitem(issues_router._EXPORT_COLUMNS, "negative_inventory")
    res = admin_client.get("/api/issues/negative_inventory/export")
    assert res.status_code == 200
    assert res.text.splitlines()[0].startswith("Product,Category,Vendor")


def test_empty_export_still_has_its_header_row(db, admin_client):
    for status in ("open", "closed"):
        res = admin_client.get(f"/api/issues/negative_inventory/export?status={status}")
        assert res.status_code == 200
        lines = res.text.splitlines()
        assert len(lines) == 1 and lines[0].startswith("Product,Category,Vendor,")
