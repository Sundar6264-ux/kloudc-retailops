"""VX12 (Oct 14): adversarial verify of X1 (suggested order qty) and X2
(schedule suggestions) - the bugs it found, pinned.

X1
- A cart line decides its own pack size, Case of 1 included: a suggestion in
  the last placed order's cases (say 2 x 12) on a line whose Case of is 1 made
  "Use 2" / the In cart box order 2 units instead of 24.
- The next delivery already ordered (Mark placed / "Order already placed") was
  still shown as "order by today", with no word of what is on that order.
- A custom lookback that ends before today put the "next delivery" in the past.
X2
- POST /schedules racing Add all / a double tap could make two schedules (two
  spellings) or answer 500 (same spelling, unique constraint).
"""

import datetime as dt
import json
import threading

from app.models import CartItem, PlacedOrder, VendorSchedule
from app.services import order_suggestion as OS
from tests.test_x1_order_suggestion import CART, PROJ, TODAY, _projection, _schedule, stocked  # noqa: F401


def _placed(db, vendor, placed_at, lines=None, for_delivery=None, source=None, status="placed"):
    row = PlacedOrder(
        supplier=vendor,
        item_count=len(lines or []),
        items_json=json.dumps(lines or []),
        status=status,
        placed_at=placed_at,
        for_delivery_date=for_delivery,
        source=source,
    )
    db.add(row)
    db.commit()
    return row


# --------------------------------------------------------------------------
# X1: the cart line's own pack size
# --------------------------------------------------------------------------


def test_cart_line_case_of_1_means_units_never_the_last_orders_cases(db):
    db.add(CartItem(supplier="Babco", item_id="c", name="C", qty=2, case_of=1))
    _placed(db, "Babco", dt.datetime(2026, 10, 1, 15, tzinfo=dt.timezone.utc),
            [{"name": "C", "item_id": "c", "qty": 1, "case_of": 12},
             {"name": "B", "item_id": "b", "qty": 1, "case_of": 6}])
    got = OS.known_case_sizes(db, "Babco", ["c", "b"])
    # c is in Babco's cart at Case of 1 -> units; b isn't -> the last order's 6.
    assert got == {"b": (6.0, "last_order")}


def test_cart_context_use_n_matches_the_lines_unit(db, admin_client, stocked):  # noqa: F811
    # naan sits in Babco's cart at Case of 1; Babco's last order had it in 16s.
    db.add(CartItem(supplier="Babco", item_id="naan", name="Naan 16pcs", qty=3, case_of=1))
    _placed(db, "Babco", dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=20),
            [{"name": "Naan 16pcs", "item_id": "naan", "qty": 2, "case_of": 16}])
    body = admin_client.get(CART, params={"projection": "1", "horizon_days": 30, "lookback_days": 28}).json()
    line = next(i for s in body["suppliers"] for i in s["items"] if i["item_id"] == "naan")
    p = line["projection"]
    assert p["case_of"] is None and p["qty"] == p["units"] and p["qty"] > 0  # units, like the line
    # The projection page agrees for this in-cart item (its box is the cart qty).
    page = _projection(admin_client)
    s = next(i for i in page["items"] if i["item_id"] == "naan")["suggested_order"]
    assert s["case_of"] is None and s["qty"] == s["units"]


# --------------------------------------------------------------------------
# X1: the next delivery already ordered
# --------------------------------------------------------------------------


def test_vendor_delivery_says_already_ordered_and_what_is_on_order(db):
    _schedule(db, "Babco", [3], 1)  # Thursdays, order a day ahead
    tue = dt.date(2026, 10, 13)
    # Placed Tue Oct 13 (store time) for Thu Oct 15's delivery; prev delivery Thu Oct 8.
    _placed(db, "babco ", dt.datetime(2026, 10, 13, 14, tzinfo=dt.timezone.utc),
            [{"name": "Naan", "item_id": "naan", "qty": 2, "case_of": 16}])
    d = OS.vendor_delivery(db, "Babco", tue)
    assert d["next_delivery"] == "2026-10-15"
    assert d["label"] == "next delivery Thu Oct 15 (already ordered Tue Oct 13)"
    assert d["ordered"]["placed_on"] == "2026-10-13" and d["ordered"]["source"] == "app"
    row = {"item_id": "naan", "avg_weekly_demand": 14.0, "on_hand_qty": 3.0,
           "projections": {"7": {"projected_demand": 14.0, "need_to_order": 11.0, "safety_buffer": 0.0}}}
    s = OS.suggest(row, 7, delivery=d)
    assert "32 on order for Thu Oct 15" in s["basis"]
    assert s["on_order_units"] == 32 and s["delivery_ordered"] is True
    assert s["qty"] == 11  # shown, never subtracted (incoming stock isn't subtracted anywhere)
    assert "_order_lines" not in OS.public_delivery(d)


def test_vendor_delivery_ordered_rules_follow_the_calendar(db):
    _schedule(db, "Babco", [3], 1)
    tue = dt.date(2026, 10, 13)
    # Placed on the previous delivery day (Oct 8) = last cycle's; a preview PDF;
    # an outside order tied to NEXT week's delivery - none of them order Oct 15.
    _placed(db, "Babco", dt.datetime(2026, 10, 8, 14, tzinfo=dt.timezone.utc))
    _placed(db, "Babco", dt.datetime(2026, 10, 13, 14, tzinfo=dt.timezone.utc), status="pdf")
    _placed(db, "Babco", dt.datetime(2026, 10, 13, 15, tzinfo=dt.timezone.utc), for_delivery=dt.date(2026, 10, 22),
            source="outside")
    d = OS.vendor_delivery(db, "Babco", tue)
    assert d["ordered"] is None and d["label"].endswith("(order by Wed Oct 14)")
    # "Order already placed" from the Oct 15 order-due counts (no lines to show).
    _placed(db, "Babco", dt.datetime(2026, 10, 13, 16, tzinfo=dt.timezone.utc), for_delivery=dt.date(2026, 10, 15),
            source="outside")
    d = OS.vendor_delivery(db, "Babco", tue)
    assert d["ordered"]["source"] == "outside" and "(already ordered Tue Oct 13)" in d["label"]
    assert d["_order_lines"] == {}


def test_projection_order_window_hides_internal_lines(db, admin_client, stocked):  # noqa: F811
    _schedule(db, "Babco", [0, 1, 2, 3, 4, 5, 6], 0)
    _placed(db, "Babco", dt.datetime.now(dt.timezone.utc), [{"name": "Naan", "item_id": "naan", "qty": 4}])
    body = _projection(admin_client)
    d = body["order_window"]["delivery"]
    assert "_order_lines" not in d and d["ordered"] is not None
    naan = next(i for i in body["items"] if i["item_id"] == "naan")["suggested_order"]
    assert "4 on order for" in naan["basis"] and "already ordered" in naan["basis"]


def test_custom_lookback_ending_in_the_past_still_shows_the_real_next_delivery(db, admin_client, stocked):  # noqa: F811
    _schedule(db, "Babco", [TODAY.weekday()], 0)  # delivers today's weekday, order same day
    end = TODAY - dt.timedelta(days=10)
    r = admin_client.get(PROJ, params={"supplier": "Babco", "start": (end - dt.timedelta(days=28)).isoformat(),
                                      "end": end.isoformat(), "horizons": "7", "method": "legacy"})
    d = r.json()["order_window"]["delivery"]
    assert d["next_delivery"] == TODAY.isoformat() and d["label"].endswith("(order by today)")


# --------------------------------------------------------------------------
# X2: POST /schedules can't race Add all / a double tap
# --------------------------------------------------------------------------


def test_parallel_schedule_posts_make_one_schedule_and_no_500(db, admin_client, monkeypatch):
    import time

    import app.routers.vendor_calendar as VC

    real = VC.create_vendor_schedule

    def slow(*a, **k):  # widen the check-then-insert gap
        time.sleep(0.2)
        return real(*a, **k)

    monkeypatch.setattr(VC, "create_vendor_schedule", slow)
    results = []

    def post(name):
        r = admin_client.post("/api/vendor-calendar/schedules",
                              json={"vendor": name, "delivery_weekdays": [1], "lead_days": 1})
        results.append(r.status_code)

    threads = [threading.Thread(target=post, args=(n,)) for n in ("Raja Foods", "Raja Foods", "raja foods", "Raja Foods ")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [200, 400, 400, 400], results
    db.expire_all()
    assert db.query(VendorSchedule).count() == 1
