"""Temp users + sessions in the Suite B PROD COPY only (asserts the DB path).

Also points every marketing WhatsApp group at a fake chat id (the mock Whapi), so
nothing in the copy can name a real customer group. Writes <work>/users.json."""
import json
import os
import pathlib
import sys

W = pathlib.Path(os.environ["SUITE_B_WORK"]).resolve()
sys.path.insert(0, os.path.join(os.environ.get("SUITE_B_REPO") or str(pathlib.Path(__file__).resolve().parents[3]), "backend"))
from app.config import get_settings  # noqa: E402

assert get_settings().database_url == f"sqlite:///{W}/prodcopy.db", get_settings().database_url
from app.auth import SESSION_COOKIE_NAME, create_session, hash_password  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import User  # noqa: E402
from app.team.models import Team, TeamMember  # noqa: E402
from app.timeutil import utcnow  # noqa: E402
from sqlalchemy import text  # noqa: E402

PASSWORD = os.environ.get("SUITE_B_PASSWORD", "SuiteB-test-2026")
db = SessionLocal()
TEAM = ["team_tasks", "team_chat", "help"]
users = {
    "sb_admin": dict(is_admin=True, can_assign=False, hide_sales=False, feats=[]),
    # staff with the features this suite covers
    "sb_staff": dict(is_admin=False, can_assign=False, hide_sales=False,
                     feats=["overview", "daily_checklists", "marketing_flyers", "sales_insights", *TEAM]),
    # staff with only chat + help (no tasks, no flyers, no insights)
    "sb_chat": dict(is_admin=False, can_assign=False, hide_sales=False, feats=["team_chat", "help"]),
    "sb_hide": dict(is_admin=False, can_assign=False, hide_sales=True,
                    feats=["overview", "sales_insights", "daily_checklists", "items_sold", *TEAM]),
    "sb_lead": dict(is_admin=False, can_assign=True, hide_sales=False,
                    feats=["overview", "daily_checklists", "inventory_reports", *TEAM]),
}
kitchen = db.query(Team).filter(Team.name == "Kitchen").one()
out = {"cookie": SESSION_COOKIE_NAME, "password": PASSWORD, "kitchen": kitchen.id}
for name, c in users.items():
    u = db.query(User).filter(User.username == name).one_or_none()
    if u is None:
        u = User(username=name, password_hash=hash_password(PASSWORD), is_admin=c["is_admin"])
        db.add(u)
    u.password_hash = hash_password(PASSWORD)
    u.is_admin = c["is_admin"]
    u.can_assign = c["can_assign"]
    u.hide_sales = c["hide_sales"]
    u.allowed_features = json.dumps(c["feats"])
    db.commit()
    db.refresh(u)
    if not db.get(TeamMember, (kitchen.id, u.id)):
        db.add(TeamMember(team_id=kitchen.id, user_id=u.id, created_at=utcnow()))
        db.commit()
    out[name] = {"id": u.id, "token": create_session(db, u.id)[0]}
# The App Review demo login, with a known password IN THE COPY ONLY.
demo = db.query(User).filter(User.username == "app_store_review").one()
demo.password_hash = hash_password(PASSWORD)
db.commit()
out["demo"] = {"id": demo.id, "token": create_session(db, demo.id)[0], "username": "app_store_review"}
# Fake chat ids for every group with a chat id (the copy's real ones are customer groups).
rows = db.execute(text("select id, key from whatsapp_groups where chat_id is not null order by id")).fetchall()
for i, (gid, key) in enumerate(rows):
    db.execute(text("update whatsapp_groups set chat_id = :c where id = :i"),
               {"c": f"1203630000000{900 + i:05d}@g.us", "i": gid})
db.commit()
out["groups"] = [dict(r._mapping) for r in db.execute(text(
    "select key, label, marketing, active, chat_id from whatsapp_groups order by id"))]
(W / "users.json").write_text(json.dumps(out, indent=1))
print({k: (v["id"] if isinstance(v, dict) and "id" in v else v) for k, v in out.items() if k != "groups"})
db.close()
