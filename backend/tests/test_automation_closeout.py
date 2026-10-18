"""Automation batch close-out (Oct 11): small live-report gaps found while
walking the contract. Every Toast call is mocked."""

from app.routers import orders as orders_router


def test_all_time_open_checks_csv_refreshes_today_like_the_screen(admin_client, monkeypatch):
    """The all-time CSV used to read the cache as it was, so a download could
    miss checks the screen (which refreshes today's part first) showed."""
    calls = []
    monkeypatch.setattr(orders_router, "ensure_today_fresh", lambda db: calls.append("fresh") or False)
    monkeypatch.setattr(
        orders_router,
        "get_open_orders",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("day mode must not run in this test")),
    )

    resp = admin_client.get("/api/orders/open/export?mode=all_time")
    assert resp.status_code == 200, resp.text
    assert calls == ["fresh"]
    assert "open-checks-all-time.csv" in resp.headers.get("content-disposition", "")


def test_day_mode_csv_stays_a_single_live_read(admin_client, monkeypatch):
    calls = []
    monkeypatch.setattr(orders_router, "ensure_today_fresh", lambda db: calls.append("fresh") or False)
    monkeypatch.setattr(orders_router, "get_open_orders", lambda *a, **k: [])
    resp = admin_client.get("/api/orders/open/export?mode=day")
    assert resp.status_code == 200, resp.text
    assert calls == []
