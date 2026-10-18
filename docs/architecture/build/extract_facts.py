"""Extract architecture facts from the backend code (read-only; imports the app, never starts it).

Run from website/backend with its venv:  SCHEDULER_ENABLED=false ... python extract.py out.json
"""
import inspect
import json
import sys

from fastapi.routing import APIRoute

from app.main import app
from app import models  # noqa: F401
from app import insights_models, issue_lead_models, po_send_models, setting_models, notification_models  # noqa: F401
from app.team import models as tm, tasks_models, chat_models  # noqa: F401
from app.db import Base
from app.features import FEATURES
from app.services import notifications as notif
from app.demo_guard import ALLOWLIST
from app.config import Settings
from app.team.jobs import TEAM_JOBS


def depnames(dep, acc):
    for d in dep.dependencies:
        c = d.call
        name = getattr(c, "__qualname__", repr(c))
        clo = ""
        if getattr(c, "__closure__", None):
            vals = [cl.cell_contents for cl in c.__closure__ if isinstance(cl.cell_contents, (str, tuple))]
            if vals:
                clo = str(vals)
        mod = getattr(c, "__module__", "")
        if name not in ("get_db",):
            acc.append(f"{mod.split('.')[-1]}.{name}{clo}")
        depnames(d, acc)
    return acc


def gate_of(deps):
    out = []
    for d in deps:
        if "require_admin" in d:
            out.append("admin")
        elif "require_feature" in d:
            out.append("feat:" + d.split("[")[-1].strip("]'\" ").replace("'", ""))
        elif "require_any_feature" in d:
            inner = d.split("(", 1)[-1].rsplit(")", 1)[0].replace("'", "").replace(" ", "")
            out.append("any:" + inner.strip(","))
        elif "get_current_user" in d or "get_optional" in d:
            continue
        elif "sales_access" in d:
            out.append("sales")
        else:
            out.append(d.split(".", 1)[-1].split("[")[0])
    seen = []
    for x in out:
        if x not in seen:
            seen.append(x)
    return seen


routes = []
for r in app.routes:
    if isinstance(r, APIRoute):
        deps = [d for d in depnames(r.dependant, []) if "demo_write_guard" not in d and "get_db" not in d]
        login = any("get_current_user" in d for d in deps) or any(
            ("require_" in d or "_user" in d or "tasks_" in d or "_ann_" in d) for d in deps
        )
        src = inspect.getsourcefile(r.endpoint).split("/app/")[-1]
        line = inspect.getsourcelines(r.endpoint)[1]
        for m in sorted(r.methods):
            routes.append(
                {
                    "method": m,
                    "path": r.path,
                    "src": f"{src}:{line}",
                    "fn": r.endpoint.__name__,
                    "gate": gate_of(deps),
                    "login": bool(deps),
                    "demo_allow": (m, r.path) in ALLOWLIST,
                }
            )

tables = []
for t in sorted(Base.metadata.tables.values(), key=lambda t: t.name):
    cols = []
    for c in t.columns:
        cols.append(
            {
                "name": c.name,
                "type": str(c.type).split("(")[0],
                "pk": bool(c.primary_key),
                "fk": [f"{fk.column.table.name}.{fk.column.name}" for fk in c.foreign_keys],
                "nullable": bool(c.nullable),
            }
        )
    tables.append({"name": t.name, "columns": cols, "module": None})

# which module defines each table
for mapper in Base.registry.mappers:
    cls = mapper.class_
    tn = getattr(cls, "__tablename__", None)
    for t in tables:
        if t["name"] == tn:
            t["module"] = cls.__module__.replace("app.", "")
            t["cls"] = cls.__name__

alerts = []
for a in notif.ALERTS:
    alerts.append({k: (v if isinstance(v, (str, int, float, bool, type(None), list, dict)) else str(v)) for k, v in a.items()})

settings_fields = list(Settings.model_fields.keys())

team_jobs = [{"id": j.id, "trigger": str(j.trigger(__import__("zoneinfo").ZoneInfo("America/New_York")))} for j in TEAM_JOBS]

json.dump(
    {
        "routes": routes,
        "tables": tables,
        "features": FEATURES,
        "alerts": alerts,
        "groups": notif.GROUPS,
        "allowlist": [list(k) for k in ALLOWLIST.keys()],
        "settings": settings_fields,
        "team_jobs": team_jobs,
    },
    open(sys.argv[1], "w"),
    indent=1,
    default=str,
)
print(len(routes), "routes", len(tables), "tables", len(alerts), "alerts", len(settings_fields), "settings")

# ---- module docstrings, sizes, alembic chain (pure file reads) ----
import ast, os, re as _re
BASE = os.path.abspath("app")
mods = []
for root, _, files in os.walk(BASE):
    if "__pycache__" in root:
        continue
    for fn in sorted(files):
        if not fn.endswith(".py") or fn == "__init__.py":
            continue
        p = os.path.join(root, fn)
        src = open(p).read()
        try:
            doc = ast.get_docstring(ast.parse(src)) or ""
        except SyntaxError:
            doc = ""
        mods.append({"path": os.path.relpath(p, os.path.dirname(BASE)), "lines": src.count("\n") + 1, "doc": " ".join(doc.split())[:420]})
alembic = []
vdir = os.path.abspath("alembic/versions")
for fn in sorted(os.listdir(vdir)):
    if not fn.endswith(".py"):
        continue
    src = open(os.path.join(vdir, fn)).read()
    rev = _re.search(r"^revision\s*(?::\s*str)?\s*=\s*['\"]([^'\"]+)", src, _re.M)
    down = _re.search(r"^down_revision\s*(?::[^=]*)?=\s*['\"]?([^'\"\n]+)", src, _re.M)
    doc = (ast.get_docstring(ast.parse(src)) or "").strip().splitlines()
    alembic.append({"file": fn, "rev": rev.group(1) if rev else "", "down": (down.group(1).strip() if down else ""), "title": doc[0] if doc else ""})
d = json.load(open(sys.argv[1]))
d["modules"] = mods
d["alembic"] = alembic
json.dump(d, open(sys.argv[1], "w"), indent=1, default=str)
print(len(mods), "modules", len(alembic), "migrations")
