"""Generated logins for one fake store's throwaway database (MT9 e2e).

    STORE_E2E_WORK=<dir> STORE_E2E_CODE=<code> python -I seed.py

Run after `alembic upgrade head` on <work>/<code>/store.db. Creates four
logins (admin, staff, a "Hide sales numbers" staff member, the App Review
demo login) with a random password, a session for each, and writes
<work>/<code>/users.json. Nothing here is real data.
"""
import json
import os
import pathlib
import secrets
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
WORK = pathlib.Path(os.environ["STORE_E2E_WORK"]).resolve()
CODE = os.environ["STORE_E2E_CODE"]
STORE = WORK / CODE
os.environ["DATABASE_URL"] = f"sqlite:///{STORE}/store.db"
os.chdir(STORE)
sys.path.insert(0, str(REPO / "backend"))

from app.config import get_settings  # noqa: E402

assert get_settings().database_url == os.environ["DATABASE_URL"]
from app.auth import SESSION_COOKIE_NAME, create_session, hash_password  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import User  # noqa: E402

STAFF = [
    "overview", "invoice_submission", "daily_checklists", "open_orders", "items_sold", "reorder_candidates",
    "supplier_projection", "vendor_calendar", "delivery_review", "inventory_reports", "reconciliation",
    "purchase_order_cart", "team_tasks", "team_chat", "help", "ask_bot", "sales_insights", "marketing_flyers",
]
USERS = {
    "e2e_admin": dict(is_admin=True, hide_sales=False, feats=[]),
    "e2e_staff": dict(is_admin=False, hide_sales=False, feats=STAFF),
    "e2e_hide": dict(is_admin=False, hide_sales=True, feats=["overview", "items_sold", "sales_insights", "team_tasks", "help"]),
    # The App Review demo login (DEMO_USERNAMES): staff features, sends off.
    "app_store_review": dict(is_admin=False, hide_sales=False, feats=[f for f in STAFF if f != "marketing_flyers"]),
}

password = secrets.token_urlsafe(12)
db = SessionLocal()

# The rows old migrations seeded for the origin store (contract 3.5 step 3,
# what `python -m app.store init` removes). Done here too so the check runs
# on a store-shaped database before and after the store-pack batch merges.
# Built-in checklists may be re-created by the server until the store pack
# replaces them (checklist_admin.ensure_seeded).
from sqlalchemy import text  # noqa: E402
from app.services.checklist_builtin import SEED_DEFINITIONS  # noqa: E402

types_ = [d["checklist_type"] for d in SEED_DEFINITIONS]
removed = {}
# run.sh runs `python -m app.store init` first: it already removed these
# rows and then inserted the store pack's own (its "store" WhatsApp group
# too) - deleting again would take the pack's rows away.
stamped = db.execute(text("SELECT COUNT(*) FROM sqlite_master WHERE name='store_identity'")).scalar() and db.execute(
    text("SELECT COUNT(*) FROM store_identity")
).scalar()
for label, sql, params in [] if stamped else [
    ("category_overrides", "DELETE FROM category_overrides WHERE (match_type='supplier' AND match_value='Shaz International') "
     "OR created_by='category merge plan (migration 0037)'", {}),
    ("checklist_definition_versions", "DELETE FROM checklist_definition_versions WHERE definition_id IN "
     "(SELECT id FROM checklist_definitions WHERE checklist_type IN (%s))" % ",".join(f"'{t}'" for t in types_), {}),
    ("checklist_definitions", "DELETE FROM checklist_definitions WHERE checklist_type IN (%s)" % ",".join(f"'{t}'" for t in types_), {}),
    ("whatsapp_groups", "DELETE FROM whatsapp_groups WHERE key IN ('store','kitchen') AND builtin=1", {}),
]:
    removed[label] = db.execute(text(sql), params).rowcount
db.commit()
out = {"cookie": SESSION_COOKIE_NAME, "password": password, "users": {}}
for name, c in USERS.items():
    u = db.query(User).filter(User.username == name).one_or_none()
    if u is None:
        u = User(username=name, password_hash=hash_password(password), is_admin=c["is_admin"])
        db.add(u)
    u.password_hash = hash_password(password)
    u.is_admin = c["is_admin"]
    u.hide_sales = c["hide_sales"]
    u.allowed_features = json.dumps(c["feats"])
    db.commit()
    db.refresh(u)
    out["users"][name] = {"id": u.id, "token": create_session(db, u.id)[0]}
db.close()
(STORE / "users.json").write_text(json.dumps(out, indent=1))
print(f"{CODE}: {', '.join(USERS)}; removed seeded rows {removed}")
