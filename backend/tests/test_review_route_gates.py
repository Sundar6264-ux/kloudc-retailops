"""Every route's auth / feature gate, CSV-injection neutralizing in exports and
the one-at-a-time Open Orders full re-scan (backend review, Sep 30).

The gate tests walk `app.routes`, so a new route that forgets its
`dependencies=[...]` fails here unless it is added to an allowlist below on
purpose.
"""

import datetime as dt
import re
import threading

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.main import app
from app.models import PurchaseLogEntry
from app.services.csv_util import csv_cell, rows_to_csv
from tests.conftest import client_for, make_user

# No login needed at all.
PUBLIC = {
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/mobile-login"),
    ("POST", "/api/auth/logout"),
    ("POST", "/api/notifications/unregister"),
    ("GET", "/api/app/config"),
    ("GET", "/api/health"),
    # Public checklist link (routers/checklist_public.py): reach only the one
    # checklist a valid, enabled link token names - see
    # tests/test_public_checklist_link.py. Nothing else may join this list
    # without the same review.
    ("GET", "/api/public/checklists/{token}"),
    ("POST", "/api/public/checklists/{token}/submit"),
    ("POST", "/api/public/checklists/{token}/media"),
    ("GET", "/api/public/checklists/{token}/images/{media_token}"),
    ("GET", "/c/{path:path}"),
    ("GET", "/c"),
    # StoreOps M2 public store branding (tests/test_branding.py): the store's
    # names, colours, POS, capabilities and legal links - nothing secret -
    # and the bundled placeholder images.
    ("GET", "/api/app/branding"),
    ("GET", "/branding/{name}"),
}
# Any logged-in user, no feature grant (small lookups, own account/devices,
# team hub basics - see the notes in each router).
LOGIN_ONLY = {
    ("GET", "/api/auth/me"),
    ("POST", "/api/auth/change-password"),
    ("GET", "/api/inventory/items/search"),
    ("GET", "/api/inventory/items/by-barcode"),
    ("GET", "/api/ordering/forecast"),
    ("GET", "/api/ordering/suppliers"),
    ("GET", "/api/glossary"),
    ("POST", "/api/notifications/devices"),
    ("DELETE", "/api/notifications/devices"),
    ("GET", "/api/notifications/prefs"),
    ("PUT", "/api/notifications/prefs"),
    ("POST", "/api/notifications/test"),
    ("POST", "/api/notifications/prefs/reset"),  # own switches (Oct 15)
    ("GET", "/api/notifications/recent"),  # own pushes (Oct 15)
    ("GET", "/api/team/bootstrap"),
    ("GET", "/api/team/updates"),
    ("GET", "/api/team/media/{token}"),
    ("GET", "/api/team/announcements"),
    ("GET", "/api/team/announcements/{announcement_id}"),
    ("POST", "/api/team/announcements/{announcement_id}/seen"),
    ("POST", "/api/team/announcements/{announcement_id}/ack"),
    # Build 7 (docs/APP_REVIEW_BUILD7_CONTRACT.md): your own account and the
    # Team rules everyone agrees to - tests/test_app_review_build7.py.
    ("POST", "/api/auth/delete-account"),
    ("GET", "/api/team/rules"),
    ("POST", "/api/team/rules/accept"),
}


def _routes():
    for route in app.routes:
        if isinstance(route, APIRoute):
            for method in sorted(route.methods):
                yield method, route.path


def _call(client, method, path):
    url = re.sub(r"\{[^}]+\}", "1", path)
    body = {} if method in ("POST", "PUT", "PATCH") else None
    return client.request(method, url, json=body, follow_redirects=False)


def test_every_route_needs_login():
    anon = TestClient(app)
    wrong = []
    for method, path in _routes():
        if (method, path) in PUBLIC:
            continue
        status = _call(anon, method, path).status_code
        # /public/invoices/* sends a logged-out viewer to the login page.
        expected = 302 if path.startswith("/public/invoices/") else 401
        if status != expected:
            wrong.append((method, path, status))
    assert wrong == []


def test_every_business_route_needs_a_feature_or_admin(db):
    nobody = client_for(db, make_user(db, "no_features"))
    wrong = []
    for method, path in _routes():
        if (method, path) in PUBLIC or (method, path) in LOGIN_ONLY or path.startswith("/public/"):
            continue
        status = _call(nobody, method, path).status_code
        if status != 403:
            wrong.append((method, path, status))
    assert wrong == []


def test_new_routes_have_the_right_gate(db):
    """A user with every feature EXCEPT the one a route belongs to gets 403."""
    from app.features import FEATURE_KEYS

    cases = {
        "items_sold": [("GET", "/api/sales/items-sold"), ("GET", "/api/sales/items-sold/export")],
        "open_orders": [("GET", "/api/orders/open/export")],
        "reconciliation": [("GET", "/api/reconciliation/invoice-gaps/export?start=2026-09-01&end=2026-09-02"),
                           ("GET", "/api/reconciliation/purchases/export")],
        "purchase_order_cart": [("GET", "/api/ordering/orders"), ("POST", "/api/ordering/orders/1/order-again")],
        "supplier_projection": [("GET", "/api/ordering/similar-items?item_id=x")],
        "inventory_reports": [("GET", "/api/issues/summary"), ("GET", "/api/issues/dead_with_stock/export")],
        "category_edits": [("GET", "/api/categories/items"), ("POST", "/api/categories/items/bulk"),
                           ("GET", "/api/categories/renames"), ("POST", "/api/categories/changes/undo"),
                           ("PUT", "/api/categories/cart-lines/1"), ("GET", "/api/categories/cart-preview")],
    }
    for feature, calls in cases.items():
        user = make_user(db, "all_but_" + feature, features=sorted(FEATURE_KEYS - {feature}))
        client = client_for(db, user)
        for method, url in calls:
            assert _call(client, method, url).status_code == 403, (feature, method, url)
    staff = client_for(db, make_user(db, "all_features", features=sorted(FEATURE_KEYS)))
    for method, url in [("GET", "/api/admin/category-overrides"), ("PATCH", "/api/auth/users/1/hide-sales")]:
        assert _call(staff, method, url).status_code == 403, url


def test_checklist_builder_admin_routes_are_admin_only(db):
    """Every /api/checklists/admin/* route (the checklist builder) needs an
    admin: a staff user granted every feature, daily_checklists included,
    gets 403 on each one."""
    from app.features import FEATURE_KEYS

    staff = client_for(db, make_user(db, "checklist_staff", features=sorted(FEATURE_KEYS)))
    admin_routes = [(m, p) for m, p in _routes() if p.startswith("/api/checklists/admin")]
    assert len(admin_routes) >= 12
    wrong = [(m, p, s) for m, p in admin_routes if (s := _call(staff, m, p).status_code) != 403]
    assert wrong == []


def test_csv_cell_neutralizes_formulas():
    assert csv_cell("=HYPERLINK(\"http://x\")") == "\"'=HYPERLINK(\"\"http://x\"\")\""
    assert csv_cell("+cmd") == "'+cmd"
    assert csv_cell("-2+3") == "'-2+3"
    assert csv_cell("@SUM(A1)") == "'@SUM(A1)"
    assert csv_cell("\tx") == "'\tx"
    # Numbers (incl. negative on-hand) and ordinary text are unchanged.
    assert csv_cell(-3.5) == "-3.5"
    assert csv_cell("-12") == "-12"
    assert csv_cell("+1.5e3") == "+1.5e3"
    assert csv_cell(None) == ""
    assert csv_cell("Rice, 10 lb") == '"Rice, 10 lb"'
    assert rows_to_csv([{"A": "=1+1", "B": 2}]) == "A,B\n'=1+1,2\n"


def test_purchase_log_export_neutralizes_staff_text(db, admin_client):
    db.add(
        PurchaseLogEntry(
            item_id="i1", item_name="=cmd|'/c calc'!A1", supplier="@Vendor", quantity_received=-2,
            received_date=dt.date(2026, 9, 29), notes="+note",
        )
    )
    db.commit()
    lines = admin_client.get("/api/reconciliation/purchases/export").text.splitlines()
    assert lines[1] == "2026-09-29,'=cmd|'/c calc'!A1,'@Vendor,-2.0,,'+note"


def test_open_orders_rescan_runs_one_at_a_time(db, admin_client, monkeypatch):
    from app.routers import orders as orders_router

    started, release = threading.Event(), threading.Event()

    class _Job:
        status, detail = "success", "full scan"

    def slow_scan(_db, full=False):
        started.set()
        release.wait(5)
        return _Job()

    monkeypatch.setattr(orders_router, "refresh_open_orders_cache", slow_scan)
    first = {}
    t = threading.Thread(target=lambda: first.setdefault("r", admin_client.post("/api/orders/open/all-time/rescan")))
    t.start()
    assert started.wait(5)
    second = admin_client.post("/api/orders/open/all-time/rescan")
    release.set()
    t.join(5)
    assert second.status_code == 409
    assert first["r"].status_code == 200
    assert admin_client.post("/api/orders/open/all-time/rescan").status_code == 200  # lock released


def test_removed_margin_report_answers_old_app_builds(admin_client, anon_client):
    from app.routers.reports import MARGIN_REMOVED_DETAIL

    for url in ("/api/reports/margin", "/api/reports/margin/export"):
        r = admin_client.get(url)
        assert r.status_code == 410
        assert r.json()["detail"] == MARGIN_REMOVED_DETAIL
        assert anon_client.get(url).status_code == 401
