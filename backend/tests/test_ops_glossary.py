"""Glossary (ops fixes G): GET /api/glossary serves every term the website
and the app reference, the rule texts quote the real thresholds from the
code (so they can't drift), and the app's bundled copy is up to date."""

import json
import pathlib
import re
import sys

import pytest

from app import glossary as g
from app.services import demand_forecast as dfc
from app.services import reorder, stock_issues
from app.services.delivery_review import QTY_SWING_THRESHOLD
from app.services.fix_issues import ISSUE_TYPES
from app.services.purchase_order_log import STATUSES
from app.services.reconciliation import DEFAULT_INVOICE_GAP_WINDOW_DAYS
from app.services.supplier_projection import _REVIEW_FLAGS
from app.services.vendor_calendar import CONFIRM_WINDOW_DAYS

BACKEND = pathlib.Path(__file__).resolve().parents[1]
WEB_SRC = BACKEND.parent / "frontend" / "src"
MOBILE_LIB = BACKEND.parents[1] / "mobile" / "lib"

KEY_RE = r"[a-z][a-z0-9_]*"


def _term(key):
    return g.TERMS_BY_KEY[key]


def _text(key):
    t = _term(key)
    return f"{t['definition']} {t['rule'] or ''}"


# ---------------------------------------------------------------- endpoint


def test_glossary_needs_login(anon_client):
    assert anon_client.get("/api/glossary").status_code == 401


def test_glossary_any_logged_in_user(db):
    from tests.conftest import client_for, make_user

    client = client_for(db, make_user(db, "nobody", features=[]))
    r = client.get("/api/glossary")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == g.GLOSSARY_VERSION
    keys = [t["key"] for t in body["terms"]]
    assert len(keys) == len(set(keys)), "duplicate glossary keys"
    cats = {c["key"] for c in body["categories"]}
    for t in body["terms"]:
        assert t["category"] in cats, t["key"]
        assert t["label"] and t["definition"], t["key"]
        assert t["tone"] in (None, "success", "warning", "danger", "info", "neutral", "violet"), t["key"]


def test_no_em_or_en_dashes():
    blob = json.dumps(g.get_glossary(), ensure_ascii=False)
    assert "—" not in blob and "–" not in blob


# ------------------------------------------------- coverage of code values


def test_every_code_value_has_a_term():
    for tier, _label in reorder.URGENCY_TIERS:
        assert f"urgency_{tier}" in g.TERMS_BY_KEY
    for klass in (dfc.REGULAR, dfc.INTERMITTENT, dfc.SLOW, dfc.DEAD):
        assert f"class_{klass}" in g.TERMS_BY_KEY
    for flag in list(_REVIEW_FLAGS) + list(stock_issues.FLAG_LABELS):
        assert f"flag_{flag}" in g.TERMS_BY_KEY, flag
    for issue_type in ISSUE_TYPES:
        assert f"issue_{issue_type}" in g.TERMS_BY_KEY, issue_type
    for status in STATUSES:
        assert f"placed_status_{status}" in g.TERMS_BY_KEY
    for status in ("pending", "in_progress", "completed", "failed"):
        assert f"invoice_{status}" in g.TERMS_BY_KEY
    for status in ("upcoming", "delivered", "missed", "skipped"):
        assert f"cal_{status}" in g.TERMS_BY_KEY


def test_labels_match_the_code():
    for tier, label in reorder.URGENCY_TIERS:
        assert _term(f"urgency_{tier}")["label"] == label
    for flag, label in stock_issues.FLAG_LABELS.items():
        assert _term(f"flag_{flag}")["label"] == label
    for issue_type, cfg in ISSUE_TYPES.items():
        assert _term(f"issue_{issue_type}")["label"] == cfg["label"]
    # The Review term lists exactly the review flags.
    review_rule = _term("review")["rule"]
    for flag in _REVIEW_FLAGS:
        assert stock_issues.FLAG_LABELS[flag] in review_rule, flag


def test_similar_reasons_have_terms():
    src = (BACKEND / "app" / "services" / "similar_products.py").read_text()
    body = src.split("def _reason(", 1)[1].split("\n    def ", 1)[0]
    reasons = set(re.findall(r'return "([^"]+)"', body))
    labels = {t["label"] for t in g.TERMS if t["category"] == "similar"}
    assert reasons and reasons <= labels, reasons - labels


# ------------------------------------------------ thresholds match the code


def _has(key, *numbers):
    text = _text(key)
    for n in numbers:
        assert g._n(n) in text, f"{key}: {g._n(n)!r} not in {text!r}"


def test_reorder_thresholds_in_text():
    _has("urgency_critical", reorder.CRITICAL_MIN_WEEKLY_SALES, reorder.CRITICAL_MAX_DAYS_LEFT)
    _has("urgency_order_now", reorder.EMPTY_ORDER_NOW_MIN_WEEKLY_SALES, reorder.CRITICAL_MIN_WEEKLY_SALES)
    _has("urgency_low_priority", reorder.EMPTY_ORDER_NOW_MIN_WEEKLY_SALES)
    _has(
        "vendor_urgency",
        reorder.VENDOR_MIN_ORDER_ITEMS,
        reorder.VENDOR_CRITICAL_MIN_ITEMS,
        reorder.FAST_SELLER_WEEKLY_SALES,
    )
    _has("vendor_inactive", reorder.VENDOR_INACTIVE_AFTER_DAYS)


def test_reorder_rules_behave_as_described():
    crit_week = reorder.CRITICAL_MIN_WEEKLY_SALES
    # Critical: steady seller, empty, no recent sales.
    assert reorder._urgency(0, crit_week / 7, 7, 0)["tier"] == "critical"
    # Check count: empty but sold this week.
    assert reorder._urgency(0, crit_week / 7, 7, 1)["tier"] == "check_count"
    # Empty and slow (between the two weekly thresholds) -> order now.
    mid = (reorder.EMPTY_ORDER_NOW_MIN_WEEKLY_SALES + crit_week) / 2
    assert reorder._urgency(0, mid / 7, 7, 0)["tier"] == "order_now"
    # Critical by days left.
    assert reorder._urgency(reorder.CRITICAL_MAX_DAYS_LEFT, 1, 7)["tier"] == "critical"
    # Can wait between 1x and 2x lead time; low priority beyond.
    assert reorder._urgency(10, 1, 7)["tier"] == "can_wait"
    assert reorder._urgency(15, 1, 7)["tier"] == "low_priority"


def test_forecast_thresholds_in_text():
    fc = dfc.DEFAULT_CONFIG
    _has("class_regular", fc.regular_min_sale_days)
    assert g._pct(fc.regular_min_sale_share) in _text("class_regular")
    _has("class_slow", fc.slow_max_sale_days)
    assert g._pct(fc.slow_max_sale_share) in _text("class_slow")
    _has("class_dead", fc.dead_quiet_days, fc.dead_min_available_days)
    _has("flag_insufficient_history", fc.insufficient_available_days)
    _has("flag_demand_spike", fc.spike_floor, fc.spike_multiple)
    _has("flag_unusual_trend", fc.trend_recent_days, fc.trend_baseline_days)
    _has("trend", fc.trend_min, fc.trend_max)
    assert g._pct(fc.safety_max_share) in _text("safety_buffer")
    _has("flag_prolonged_stockout", g.PROLONGED_STOCKOUT_DAYS)
    _has("flag_differs_from_history", g.DIFFERS_MIN_WEEKLY_GAP, g.DIFFERS_MIN_RATIO)


def _model(**over):
    base = {
        "available_days": 60.0,
        "current_stockout_days": 0,
        "window_days": 60,
        "censored_days": 0,
        "spike_capped": False,
        "raw_trend_factor": None,
        "classification": dfc.REGULAR,
        "base_daily_demand": 1.0,
        "trend_factor": 1.0,
    }
    base.update(over)
    return base


def test_exception_flag_literals_match_the_glossary():
    flags = lambda m, legacy=1.0: dfc.exception_flags(m, 5, legacy, True)  # noqa: E731
    n = g.PROLONGED_STOCKOUT_DAYS
    assert "prolonged_stockout" in flags(_model(current_stockout_days=n))
    assert "prolonged_stockout" not in flags(_model(current_stockout_days=n - 1))
    share = g.PROLONGED_STOCKOUT_SHARE
    assert "prolonged_stockout" in flags(_model(censored_days=int(60 * share) + 1))
    assert "prolonged_stockout" not in flags(_model(censored_days=int(60 * share)))
    # differs_from_history: gap of DIFFERS_MIN_WEEKLY_GAP/week and ratio DIFFERS_MIN_RATIO.
    gap, ratio = g.DIFFERS_MIN_WEEKLY_GAP, g.DIFFERS_MIN_RATIO
    old_weekly = gap / (ratio - 1)  # new = ratio x old, new - old = gap
    new_daily = ratio * old_weekly / 7
    assert "differs_from_history" in flags(_model(base_daily_demand=new_daily), old_weekly / 7)
    assert "differs_from_history" not in flags(_model(base_daily_demand=new_daily * 0.95), old_weekly / 7)
    # Unusual trend: outside [trend_min, trend_max].
    fc = dfc.DEFAULT_CONFIG
    assert "unusual_trend" in flags(_model(raw_trend_factor=fc.trend_max + 0.01))
    assert "unusual_trend" not in flags(_model(raw_trend_factor=fc.trend_max))


def test_other_thresholds_in_text():
    _has("issue_refill_from_container", stock_issues.REFILL_STORE_MAX_QTY)
    _has("issue_dead_new_arrival", stock_issues.NEW_ARRIVAL_MIN_DAYS, stock_issues.NEW_ARRIVAL_MAX_DAYS)
    _has("issue_slow_review", stock_issues.FORECAST_LOOKBACK_DAYS)
    _has("sales_window", stock_issues.FORECAST_LOOKBACK_DAYS)
    _has("delivery_suggested_qty", QTY_SWING_THRESHOLD)
    _has("transfer_direction", QTY_SWING_THRESHOLD)
    _has("cal_delivered", CONFIRM_WINDOW_DAYS)
    _has("cal_missed", CONFIRM_WINDOW_DAYS)
    _has("recon_invoice_gap", DEFAULT_INVOICE_GAP_WINDOW_DAYS)
    _has("placed_status_pdf", 24, 48)


def test_automation_batch_terms_quote_the_code():
    """Oct 11 automation batch: sales accuracy, live data, Inventory from
    Toast and Send PO terms quote the defaults and constants the code uses."""
    from app.config import Settings
    from app.services import inventory_api, po_send, sales_accuracy

    d = {name: f.default for name, f in Settings.model_fields.items()}
    _has("sales_live_today", d["live_sales_cache_seconds"])
    _has("sales_accuracy_match", d["sales_repull_days"])
    assert g._times(d["sales_repull_times"]) in _text("sales_accuracy_match")
    _has("sales_accuracy_fixed", sales_accuracy.UNPAID_RESYNC_HOURS)
    _has("live_stock", d["live_stock_cache_seconds"] / 60)
    _has("inventory_upload_due", d["inventory_csv_max_age_days"])
    _has("barcode_menu_recheck", d["menu_barcode_recheck_minutes"])
    _has("send_po", po_send.MAX_RECIPIENTS, po_send.MAX_NOTE)
    _has("po_send_uncertain", po_send.UNCERTAIN_LOOKBACK.total_seconds() / 3600)
    assert g._pct(inventory_api.MIN_BUILD_SHARE) in _text("inventory_api_snapshot")
    assert "4:05 AM" in _text("inventory_api_snapshot")
    # Every Send PO status the server writes has a term.
    for status in (po_send.STATUS_SENDING, po_send.STATUS_SENT, po_send.STATUS_PARTLY,
                   po_send.STATUS_FAILED, po_send.STATUS_UNCERTAIN):
        assert f"po_send_{status}" in g.TERMS_BY_KEY, status
    # Every sales accuracy verdict the job records has a term.
    for verdict in ("match", "fixed"):
        assert f"sales_accuracy_{verdict}" in g.TERMS_BY_KEY
    assert "sales_accuracy_differs" in g.TERMS_BY_KEY and "sales_accuracy_waiting" in g.TERMS_BY_KEY


def test_open_order_threshold_matches_the_app():
    src = MOBILE_LIB / "features" / "orders" / "models" / "open_order.dart"
    if not src.exists():
        pytest.skip("mobile repo not checked out next to website/")
    m = re.search(r"bool get isStale\s*=>[^;]*\)\s*>\s*(\d+)", src.read_text())
    assert m, "isStale rule not found in open_order.dart"
    assert int(m.group(1)) == g.OPEN_ORDER_STALE_MINUTES
    assert f"{g.OPEN_ORDER_STALE_MINUTES // 60} hours" in _text("open_order_too_long")


# ------------------------------------------- clients reference real keys


def _literals(expr, quote):
    return re.findall(quote + "(" + KEY_RE + ")" + quote, expr)


def _web_keys():
    keys = set()
    for path in WEB_SRC.rglob("*.js*"):
        text = path.read_text(encoding="utf-8")
        keys.update(re.findall(r'\b(?:k|termKey)="(' + KEY_RE + ')"', text))
        for expr in re.findall(r"\b(?:k|termKey)=\{([^{}]*)\}", text):
            keys.update(_literals(expr, '"'))
        for expr in re.findall(r"\bconst k = ([^;]+);", text):
            keys.update(_literals(expr, '"'))
    glossary_js = (WEB_SRC / "glossary.jsx").read_text(encoding="utf-8")
    for block in re.findall(r"export const [A-Z_]+_TERMS = \{(.*?)\};", glossary_js, re.S):
        keys.update(re.findall(r':\s*"(' + KEY_RE + ')"', block))
    return keys


def _app_keys():
    keys = set()
    for path in MOBILE_LIB.rglob("*.dart"):
        if path.name == "glossary_data.dart":
            continue
        text = path.read_text(encoding="utf-8")
        for expr in re.findall(r"\btermKey:\s*([^,\n]+)", text):
            # `someTermKeys['server_value']` is a map lookup (the map's
            # values are checked below), not a glossary key.
            keys.update(_literals(re.sub(r"\[[^\]]*\]", "", expr), "'"))
    key_file = MOBILE_LIB / "core" / "glossary" / "glossary_keys.dart"
    if key_file.exists():
        keys.update(re.findall(r":\s*'(" + KEY_RE + ")'", key_file.read_text(encoding="utf-8")))
    return keys


def test_website_references_only_real_keys():
    keys = _web_keys()
    # Sanity: the scan actually finds the wiring (a broken regex would pass vacuously).
    assert {"urgency_critical", "class_intermittent", "review", "issue_came_back"} <= keys
    missing = sorted(k for k in keys if k not in g.TERMS_BY_KEY)
    assert not missing, f"website references unknown glossary keys: {missing}"


def test_app_references_only_real_keys():
    if not MOBILE_LIB.exists():
        pytest.skip("mobile repo not checked out next to website/")
    keys = _app_keys()
    assert "urgency_critical" in keys and "review" in keys, "app wiring not found"
    missing = sorted(k for k in keys if k not in g.TERMS_BY_KEY)
    assert not missing, f"app references unknown glossary keys: {missing}"


def test_app_bundled_copy_is_current():
    target = MOBILE_LIB / "core" / "glossary" / "glossary_data.dart"
    if not target.exists():
        pytest.skip("mobile repo not checked out next to website/")
    sys.path.insert(0, str(BACKEND / "scripts"))
    try:
        import export_glossary
    finally:
        sys.path.pop(0)
    bundled = export_glossary.bundled_json(target.read_text(encoding="utf-8"))
    assert bundled == json.loads(json.dumps(g.get_glossary())), (
        "mobile glossary_data.dart is stale - run: .venv312/bin/python scripts/export_glossary.py"
    )
