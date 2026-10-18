"""Temp logins in the regression COPY only (refuses any other database).

Every temp user gets the password REG_PASSWORD. The App Review demo login
(app_store_review, a real user in the copy) gets the same password - in the COPY.
Writes REG_WORK/users.json: {username: {id, password, role}}.
"""
import json
import os
import pathlib

WORK = pathlib.Path(os.environ["REG_WORK"]).resolve()
from app.config import get_settings  # noqa: E402

db_path = pathlib.Path(get_settings().database_url.replace("sqlite:///", "")).resolve()
assert str(db_path).startswith(str(WORK) + "/") and "spicetown-backend" not in str(db_path), db_path

from app.auth import hash_password  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import User  # noqa: E402

PASSWORD = os.environ.get("REG_PASSWORD", "Regression-2026!")
ORDERING = ["overview", "open_orders", "items_sold", "reorder_candidates", "supplier_projection",
            "vendor_calendar", "purchase_order_cart", "delivery_review", "inventory_reports",
            "reconciliation", "category_edits", "invoice_submission", "team_tasks", "team_chat", "help"]
USERS = {
    "reg_admin": dict(role="admin", is_admin=True, hide_sales=False, can_assign=True, feats=[]),
    # staff with the whole ordering set
    "reg_staff": dict(role="staff", is_admin=False, hide_sales=False, can_assign=False, feats=ORDERING),
    # staff with only the read-side pages: Reorder + Supplier Projection, no cart
    "reg_viewer": dict(role="viewer", is_admin=False, hide_sales=False, can_assign=False,
                       feats=["reorder_candidates", "supplier_projection", "vendor_calendar", "help"]),
    # "Hide sales numbers" staff
    "reg_hide": dict(role="hide_sales", is_admin=False, hide_sales=True, can_assign=False, feats=ORDERING),
    # becomes a PO-section lead in the Fix Issues spec
    "reg_lead": dict(role="lead", is_admin=False, hide_sales=True, can_assign=True,
                     feats=["inventory_reports", "reorder_candidates", "supplier_projection",
                            "purchase_order_cart", "team_tasks", "team_chat", "help"]),
}

db = SessionLocal()
out = {}
for name, c in USERS.items():
    u = db.query(User).filter(User.username == name).one_or_none()
    if u is None:
        u = User(username=name, password_hash="!", is_admin=c["is_admin"])
        db.add(u)
    u.is_admin = c["is_admin"]
    u.hide_sales = c["hide_sales"]
    u.can_assign = c["can_assign"]
    u.allowed_features = json.dumps(c["feats"])
    u.password_hash = hash_password(PASSWORD)
    db.commit()
    db.refresh(u)
    out[name] = {"id": u.id, "password": PASSWORD, "role": c["role"]}

demo = db.query(User).filter(User.username == "app_store_review").one()
demo.password_hash = hash_password(PASSWORD)
db.commit()
out["app_store_review"] = {"id": demo.id, "password": PASSWORD, "role": "demo"}
(WORK / "users.json").write_text(json.dumps(out, indent=1))
print({k: v["id"] for k, v in out.items()})
db.close()
