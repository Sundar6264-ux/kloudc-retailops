"""Insights - fixes from the independent verification (Oct 13).

Same fake Toast and fixtures as tests/test_insights.py (no real Toast, push or
WhatsApp).
"""

import datetime as dt

from app.insights_models import AnalyticsCache
from app.services import insights, insights_data
from tests.test_insights import (  # noqa: F401 - pytest fixtures
    NY,
    TODAY,
    YESTERDAY,
    admin_c,
    day_net,
    fake,
    filled,
)


def _hourly_rows(db, d):
    return insights_data.cached(db, "metrics_hour", [d])[d].rows


def _net_by_hour(db, d):
    out = {}
    for r in _hourly_rows(db, d):
        h = insights._hour(r)
        if h is not None:
            out[h] = out.get(h, 0.0) + insights_data._num(r.get("netSalesAmount"))
    return out


# --------------------------------------------------------------------------- weekly digest


def test_weekly_digest_for_the_open_week_uses_the_last_closed_days(db, filled):
    """The digest router asks for the week ending today (dt.date.today()),
    which is never in the cache - the compare used to come back null every
    time. It now covers the 7 closed days ending yesterday."""
    from app.services import weekly_digest

    tc = weekly_digest.get_weekly_digest_data(db, TODAY)["sales"]["toast_compare"]
    assert tc is not None
    assert tc["week_end"] == YESTERDAY.isoformat()
    assert tc["week_start"] == (YESTERDAY - dt.timedelta(days=6)).isoformat()
    week = [YESTERDAY - dt.timedelta(days=k) for k in range(7)]
    assert tc["net_sales"] == round(sum(day_net(d) for d in week), 2)
    # A later "today" (the digest generated days after the cache was filled)
    # still answers for the last closed week.
    assert insights.week_compare(db, TODAY + dt.timedelta(days=3)) == tc


# --------------------------------------------------------------------------- partial today compare


def _today_at(h, m=0, day=TODAY):
    return dt.datetime.combine(day, dt.time(h, m), tzinfo=NY)


def test_partial_today_counts_the_same_minutes_of_the_base_days(db, filled):
    """At 14:30 today holds 14:00-14:30, so the earlier days count their
    hours before 14:00 plus half of their 2 PM hour (it used to be none of
    it, so today always looked better late in an hour)."""
    last_week = TODAY - dt.timedelta(days=7)
    body = insights.compare(db, TODAY, now=_today_at(14, 30))
    assert body["partial"] is True and body["through_hour"] == 14
    by_hour = _net_by_hour(db, last_week)
    want = sum(v for h, v in by_hour.items() if 4 <= h < 14) + by_hour[14] * 0.5
    assert body["vs_last_week"]["net_sales"] == round(want, 2)
    assert body["vs_last_week"]["pct"] is not None
    assert body["vs_4wk_avg"]["weeks_used"] == 4

    on_the_hour = insights.compare(db, TODAY, now=_today_at(14, 0))
    assert on_the_hour["vs_last_week"]["net_sales"] == round(sum(v for h, v in by_hour.items() if 4 <= h < 14), 2)


def test_partial_today_after_midnight_counts_the_whole_evening(db, filled):
    """01:30 the next morning is still the same business day (closeout at
    04:00): the earlier days count every hour from 04:00 on - not just the
    hours before 1 AM, which made the compare vanish."""
    last_week = TODAY - dt.timedelta(days=7)
    body = insights.compare(db, TODAY, now=_today_at(1, 30, day=TODAY + dt.timedelta(days=1)))
    by_hour = _net_by_hour(db, last_week)
    want = sum(v for h, v in by_hour.items() if h >= 4) + sum(v for h, v in by_hour.items() if h < 1) + by_hour.get(1, 0) * 0.5
    assert body["vs_last_week"]["net_sales"] == round(want, 2)
    assert body["vs_last_week"]["net_sales"] == round(day_net(last_week), 2)
    assert body["vs_last_week"]["pct"] is not None and body["vs_4wk_avg"]["weeks_used"] == 4


def test_partial_today_never_compares_with_a_whole_base_day(db, filled):
    """A base day without its hourly report is left out (and the answer is
    stale until it's fetched) - never compared whole against today's few
    hours, which read as a big drop."""
    last_week = TODAY - dt.timedelta(days=7)
    db.query(AnalyticsCache).filter_by(report="metrics_hour", business_date=last_week).delete()
    db.commit()
    body = insights.compare(db, TODAY, now=_today_at(12, 0))
    assert body["vs_last_week"]["net_sales"] is None and body["vs_last_week"]["pct"] is None
    assert body["vs_4wk_avg"]["weeks_used"] == 3
    assert body["stale"] is True
    # A whole past day is unchanged: it compares with whole days.
    past = insights.compare(db, YESTERDAY)
    assert past["partial"] is False
    assert past["vs_last_week"]["net_sales"] == day_net(YESTERDAY - dt.timedelta(days=7))


def test_partial_today_over_http_keeps_its_shape(db, filled, admin_c):
    body = admin_c.get(f"/api/insights/compare?date={TODAY.isoformat()}").json()
    assert body["partial"] is True and isinstance(body["through_hour"], int)
    for k in ("generated_at", "stale", "source", "coverage", "vs_last_week", "vs_4wk_avg", "vs_last_year"):
        assert k in body


# --------------------------------------------------------------------------- daily budget counts every POST


def _real_client(monkeypatch, script):
    from tests.test_sales_accuracy_job import Clock

    from app.config import get_settings
    from app.pos.adapters.toast import analytics as toast_analytics
    from tests.test_insights import REST

    s = get_settings()
    monkeypatch.setattr(s, "toast_analytics_client_id", "cid")
    monkeypatch.setattr(s, "toast_analytics_client_secret", "never-shown")
    monkeypatch.setattr(s, "toast_analytics_base", "https://analytics.test")
    monkeypatch.setattr(s, "toast_restaurant_guid", REST)
    monkeypatch.setattr(s, "insights_hmac_secret", "test-hmac-secret-0123456789abcdef")
    clock = Clock()
    monkeypatch.setattr(toast_analytics, "_sleep", clock.sleep)
    monkeypatch.setattr(toast_analytics, "_now", clock.now)
    toast_analytics.reset_state()
    client = script.client()
    monkeypatch.setattr(insights_data, "_run_report", lambda *a, **k: toast_analytics.run_report(*a, client=client, **k))


def test_check_report_retries_count_against_the_daily_budget(db, monkeypatch):
    """Toast's 60-a-day limit counts every report POST. A 500 that the
    client retries, or a 409 that makes it ask again, is another POST - each
    is logged in analytics_call_log, and the budget stops the retries."""
    from app.config import get_settings
    from app.insights_models import AnalyticsCallLog
    from app.pos.adapters.toast import analytics as toast_analytics
    from tests.test_insights import check_rows
    from tests.test_sales_accuracy_job import Script, login_ok

    script = login_ok(Script())
    script.on("POST", "/era/v1/check/day", (500, {}), (200, "g-1"), (200, "g-2"))
    script.on("GET", "/era/v1/check/g-1", (409, {"message": "try again"}))
    script.on("GET", "/era/v1/check/g-2", (200, check_rows(YESTERDAY)))
    _real_client(monkeypatch, script)
    monkeypatch.setattr(get_settings(), "insights_check_daily_budget", 5)
    insights_data.fetch_range(db, "check", YESTERDAY, YESTERDAY)
    assert script.count("POST", "/era/v1/check/day") == 3
    assert db.query(AnalyticsCallLog).filter_by(family="check").count() == 3
    assert db.query(AnalyticsCallLog).filter_by(family="check", ok=1).count() == 1
    assert insights_data.daily_left(db, "check") == 2

    # Budget of 1 left: the first POST fails with a 500, the retry is refused
    # by the budget instead of going to Toast.
    script2 = login_ok(Script())
    script2.on("POST", "/era/v1/check/day", (500, {}))
    _real_client(monkeypatch, script2)
    monkeypatch.setattr(get_settings(), "insights_check_daily_budget", 4)
    out = insights_data.fill(db, "check", [YESTERDAY - dt.timedelta(days=1)], max_calls=5, today_bd=TODAY)
    assert script2.count("POST", "/era/v1/check/day") == 1
    assert "budget" in (out["error"] or "")
    assert insights_data.daily_left(db, "check") == 0
    toast_analytics.reset_state()


# --------------------------------------------------------------------------- add-ons: choices Toast files under another guid


def test_addons_split_choices_toast_files_under_another_modifiers_guid(db, fake):
    """Real Toast MODIFIER rows (Oct 10): a sauce chosen on a platter comes
    back under the guid of the platter's size ("Regular") with the sauce's
    own name. Each (guid, name) is its own choice and joins its option by
    name. Keyed by guid alone the size swallowed the sauces under whichever
    name came first."""
    menus = {
        "lastUpdated": "2026-10-11T15:41:42.977+0000",
        "menus": [
            {
                "name": "STREET KITCHEN",
                "menuGroups": [
                    {"name": "Curries", "menuItems": [{"guid": "k1", "name": "Chicken Curry", "modifierGroupReferences": [1]}]},
                    {"name": "Chaat Bar", "menuItems": [{"guid": "k2", "name": "Samosa Chaat", "modifierGroupReferences": [3]}]},
                ],
            }
        ],
        "modifierGroupReferences": {
            "1": {"referenceId": 1, "name": "Size", "modifierOptionReferences": [10, 11]},
            "3": {"referenceId": 3, "name": "Choose a Signature Sauce", "modifierOptionReferences": [12]},
            # Offered on no item directly: only ever filed under another guid.
            "4": {"referenceId": 4, "name": "Pick a sauce", "modifierOptionReferences": [13]},
        },
        "modifierOptionReferences": {
            "10": {"referenceId": 10, "guid": "regular", "name": "Regular", "modifierGroupReferences": [4]},
            "11": {"referenceId": 11, "guid": "large", "name": "Large", "modifierGroupReferences": [4]},
            "12": {"referenceId": 12, "guid": "butter", "name": "Butter Masala Sauce"},
            "13": {"referenceId": 13, "guid": "korma", "name": "Korma Sauce"},
        },
    }
    insights_data.refresh_menu_map(db, force=True, fetch_last_updated_fn=lambda: None, fetch_menus_fn=lambda: menus)
    for d in (YESTERDAY, YESTERDAY - dt.timedelta(days=1)):
        insights_data.store(db, "menu_item", d, [
            {"menuItemGuid": "k1", "menuItemName": "Chicken Curry", "quantitySold": 10, "netSalesAmount": 120.0},
            {"menuItemGuid": "k2", "menuItemName": "Samosa Chaat", "quantitySold": 8, "netSalesAmount": 56.0},
        ])
        insights_data.store(db, "menu_modifier", d, [
            {"modifierGuid": "regular", "modifierName": "Regular", "quantitySold": 6, "netSalesAmount": 0.0},
            {"modifierGuid": "regular", "modifierName": "Butter Masala Sauce", "quantitySold": 5, "netSalesAmount": 0.0},
            {"modifierGuid": "regular", "modifierName": "Korma Sauce", "quantitySold": 1, "netSalesAmount": 0.0},
            {"modifierGuid": "large", "modifierName": "Large", "quantitySold": 4, "netSalesAmount": 8.0},
            {"modifierGuid": "large", "modifierName": "Korma Sauce", "quantitySold": 2, "netSalesAmount": 0.0},
            {"modifierGuid": "butter", "modifierName": "Butter Masala Sauce", "quantitySold": 4, "netSalesAmount": 0.0},
            {"modifierGuid": "large", "modifierName": "Mystery Topping", "quantitySold": 1, "netSalesAmount": 1.0},
        ])
    db.commit()
    body = insights.addons(db, 2)
    by = {m["name"]: m for m in body["modifiers"]}
    assert by["Regular"]["qty"] == 12 and by["Regular"]["group"] == "Size" and by["Regular"]["modifier_guid"] == "regular"
    assert by["Regular"]["attach_rate"] == round(12 / 20, 3)
    assert by["Large"]["qty"] == 8 and by["Large"]["net_sales"] == 16.0 and by["Large"]["attach_rate"] == round(8 / 20, 3)
    # 4 on the chaat + 5 inside Regular, each day.
    assert by["Butter Masala Sauce"]["qty"] == 18 and by["Butter Masala Sauce"]["group"] == "Choose a Signature Sauce"
    # 18 chosen vs 16 chaats: the menu doesn't list every item offering it.
    assert by["Butter Masala Sauce"]["attach_rate"] is None
    # Only ever filed under another guid, offered on no item: no attach rate.
    assert by["Korma Sauce"]["qty"] == 6 and by["Korma Sauce"]["modifier_guid"] == "korma"
    assert by["Korma Sauce"]["group"] == "Pick a sauce" and by["Korma Sauce"]["attach_rate"] is None
    # Not on the menu at all: kept under its own name, no group.
    assert by["Mystery Topping"]["qty"] == 2 and by["Mystery Topping"]["group"] is None
    assert sum(m["qty"] for m in body["modifiers"]) == 2 * (6 + 5 + 1 + 4 + 2 + 4 + 1)


def test_addons_place_a_misfiled_choice_with_the_option_on_the_same_item(db, fake):
    """Two options share a name ("Hot" as a curry spice level and "Hot" as
    a wings sauce): a "Hot" row filed under the wings' size guid is the
    wings sauce - the option offered on the same item."""
    menus = {
        "lastUpdated": "2026-10-11T15:41:42.977+0000",
        "menus": [{"name": "STREET KITCHEN", "menuGroups": [
            {"name": "Curries", "menuItems": [{"guid": "k1", "name": "Chicken Curry", "modifierGroupReferences": [1]}]},
            {"name": "Appetizers", "menuItems": [{"guid": "k2", "name": "Wings", "modifierGroupReferences": [2, 3]}]},
        ]}],
        "modifierGroupReferences": {
            "1": {"referenceId": 1, "name": "Spice Level", "modifierOptionReferences": [10]},
            "2": {"referenceId": 2, "name": "Size", "modifierOptionReferences": [11]},
            "3": {"referenceId": 3, "name": "Choose Sauce on Wings", "modifierOptionReferences": [12]},
        },
        "modifierOptionReferences": {
            "10": {"referenceId": 10, "guid": "spice-hot", "name": "Hot"},
            "11": {"referenceId": 11, "guid": "six", "name": "6pc"},
            "12": {"referenceId": 12, "guid": "sauce-hot", "name": "Hot"},
        },
    }
    insights_data.refresh_menu_map(db, force=True, fetch_last_updated_fn=lambda: None, fetch_menus_fn=lambda: menus)
    insights_data.store(db, "menu_item", YESTERDAY, [
        {"menuItemGuid": "k1", "menuItemName": "Chicken Curry", "quantitySold": 10, "netSalesAmount": 99.0},
        {"menuItemGuid": "k2", "menuItemName": "Wings", "quantitySold": 5, "netSalesAmount": 40.0},
    ])
    insights_data.store(db, "menu_modifier", YESTERDAY, [
        {"modifierGuid": "spice-hot", "modifierName": "Hot", "quantitySold": 4, "netSalesAmount": 0.0},
        {"modifierGuid": "six", "modifierName": "6pc", "quantitySold": 5, "netSalesAmount": 0.0},
        {"modifierGuid": "six", "modifierName": "Hot", "quantitySold": 3, "netSalesAmount": 0.0},
    ])
    db.commit()
    by = {(m["name"], m["group"]): m for m in insights.addons(db, 1)["modifiers"]}
    assert by[("Hot", "Spice Level")]["qty"] == 4 and by[("Hot", "Spice Level")]["attach_rate"] == 0.4
    assert by[("Hot", "Choose Sauce on Wings")]["qty"] == 3
    assert by[("Hot", "Choose Sauce on Wings")]["modifier_guid"] == "sauce-hot"
    assert by[("6pc", "Size")]["qty"] == 5 and by[("6pc", "Size")]["attach_rate"] == 1.0


# --------------------------------------------------------------------------- prep forecast names


def test_prep_forecast_shows_the_items_current_name(db, fake):
    """Real data: "Veg Samosa Each" was renamed "Veg Samosa - 1pc"; the prep
    list showed the old name (the oldest sample's). It now shows the menu's
    current name, or else the newest name Toast reported."""
    def menus(name):
        return {
            "lastUpdated": "2026-10-11T15:41:42.977+0000",
            "menus": [{"name": "STREET KITCHEN", "menuGroups": [
                {"name": "Street Snacks", "menuItems": [{"guid": "s1", "name": name}]},
            ]}],
        }

    day = TODAY + dt.timedelta(days=1)
    samples = [day - dt.timedelta(days=7 * k) for k in range(1, 9)]
    for i, d in enumerate(samples):  # newest first
        name = "Veg Samosa - 1pc" if i < 3 else "Veg Samosa Each"
        insights_data.store(db, "menu_item", d, [{"menuItemGuid": "s1", "menuItemName": name, "quantitySold": 20, "netSalesAmount": 30.0}])
    db.commit()
    insights_data.refresh_menu_map(db, force=True, fetch_last_updated_fn=lambda: None, fetch_menus_fn=lambda: menus("Veg Samosa (1 pc)"))
    (item,) = insights.prep_forecast(db, day)["items"]
    assert item["name"] == "Veg Samosa (1 pc)"
    insights_data.refresh_menu_map(db, force=True, fetch_last_updated_fn=lambda: None, fetch_menus_fn=lambda: menus(""))
    (item,) = insights.prep_forecast(db, day)["items"]
    assert item["name"] == "Veg Samosa - 1pc"
