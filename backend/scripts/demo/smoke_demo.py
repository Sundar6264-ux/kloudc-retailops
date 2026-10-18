"""Check a generated DEMO data folder with the real app (no server, no network).

    DEMO_REVIEW_PASSWORD=... python scripts/demo/smoke_demo.py --data /path/to/folder

Boots the FastAPI app in-process against ``<data>/storeops.db`` with the DEMO
store settings (POS_PROVIDER=csv, DEMO_MODE=true, every outside channel
blank), signs in as the review login and checks:
- /api/auth/me: a demo login on a "CSV upload" store with only
  ``csv_catalog_import``, Open Checks / Sales insights not granted;
- every parameterless GET route answers without a server error (5xx);
- the pages a reviewer opens first are non-empty;
- a write is refused (demo read-only guard) and POS-only routes say 409/403.

Prints a JSON report; exit 1 on any failure. Used by tests and after a reset.
"""

import argparse
import json
import os
import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[2]

# Routes the reviewer will look at first; each must answer 200 with data.
NON_EMPTY = {
    "/api/invoices": lambda b: len(b.get("submissions", b) if isinstance(b, dict) else b) > 0,
    "/api/checklists": lambda b: len(b.get("submissions", b) if isinstance(b, dict) else b) > 0,
    "/api/checklists/schemas": lambda b: len(b.get("checklists", b) if isinstance(b, dict) else b) > 0,
    "/api/team/tasks": lambda b: len(b.get("tasks", b) if isinstance(b, dict) else b) > 0,
    "/api/team/channels": lambda b: len(b.get("channels", b) if isinstance(b, dict) else b) > 0,
    "/api/vendor-calendar/schedules": lambda b: len(b.get("schedules", b) if isinstance(b, dict) else b) > 0,
    "/api/ordering/suppliers": lambda b: len(b.get("suppliers", b) if isinstance(b, dict) else b) > 0,
    "/api/ordering/cart": lambda b: bool(b),
    "/api/reports/negative-inventory": lambda b: bool(b),
    "/api/team/announcements": lambda b: len(b.get("announcements", b) if isinstance(b, dict) else b) > 0,
}

# Never called: these would try to reach the outside world (or stream).
SKIP = {"/api/team/updates"}


def demo_env(data: pathlib.Path) -> dict:
    sys.path.insert(0, str(BACKEND))
    from scripts.demo.generate_demo_data import demo_environment  # noqa: E402

    meta = json.loads((data / "demo_dataset.json").read_text())
    return demo_environment(data, meta.get("timezone") or "America/New_York")


def run(data: pathlib.Path, password: str) -> dict:
    os.environ.update(demo_env(data))
    os.environ["DEMO_USERNAMES"] = "appreview"
    from fastapi.routing import APIRoute
    from fastapi.testclient import TestClient

    import logging

    logging.getLogger("httpx").setLevel(logging.WARNING)
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)  # not a context manager: the schedulers never start
    report = {"errors": [], "checked": 0}
    # The app's sign-in (bearer token); the website's cookie is Secure-only.
    r = client.post("/api/auth/mobile-login", json={"username": "appreview", "password": password})
    if r.status_code != 200:
        report["errors"].append(f"login: {r.status_code} {r.text[:200]}")
        return report
    client.headers["Authorization"] = f"Bearer {r.json()['token']}"
    me = client.get("/api/auth/me").json()
    report["me"] = {k: me.get(k) for k in ("username", "demo", "pos_name", "capabilities", "is_admin")}
    if not me.get("demo"):
        report["errors"].append("/api/auth/me: demo is not true")
    if me.get("capabilities") != ["csv_catalog_import"]:
        report["errors"].append(f"/api/auth/me: capabilities {me.get('capabilities')}")
    if me.get("pos_name") != "CSV upload":
        report["errors"].append(f"/api/auth/me: pos_name {me.get('pos_name')}")
    granted = set(me.get("allowed_features") or [])
    for key in ("open_orders", "sales_insights"):
        if key in granted:
            report["errors"].append(f"/api/auth/me: {key} granted on a CSV-only store")

    for route in app.routes:
        if not isinstance(route, APIRoute) or "GET" not in route.methods or "{" in route.path:
            continue
        if route.path in SKIP or route.path.endswith(".pdf") or route.path.endswith("/export"):
            continue
        resp = client.get(route.path)
        report["checked"] += 1
        if resp.status_code >= 500:
            report["errors"].append(f"GET {route.path}: {resp.status_code} {resp.text[:160]}")
    for path, ok in NON_EMPTY.items():
        resp = client.get(path)
        if resp.status_code != 200:
            report["errors"].append(f"GET {path}: {resp.status_code}")
            continue
        try:
            if not ok(resp.json()):
                report["errors"].append(f"GET {path}: empty")
        except Exception as exc:  # noqa: BLE001
            report["errors"].append(f"GET {path}: unexpected body ({exc})")
    write = client.post("/api/ordering/cart/items", json={"supplier": "x", "name": "x", "qty": 1})
    if write.status_code != 403:
        report["errors"].append(f"POST /api/ordering/cart/items as demo: {write.status_code} (expected 403)")
    for path in ("/api/orders/open", "/api/insights/channels"):
        status = client.get(path).status_code
        if status not in (403, 409):
            report["errors"].append(f"GET {path} on a CSV-only store: {status} (expected 403/409)")
    return report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Smoke-check a generated DEMO data folder")
    p.add_argument("--data", required=True)
    args = p.parse_args(argv)
    password = os.environ.get("DEMO_REVIEW_PASSWORD") or ""
    report = run(pathlib.Path(args.data).resolve(), password)
    print(json.dumps(report, indent=2, default=str))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
