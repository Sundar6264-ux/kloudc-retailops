"""Export the legacy single-store rules and settings into PRIVATE files.

StoreOps M2 (docs/M2_CONTRACT.md section 1.6). Before M2 the store's own
rules and contact details were Python constants. M2 moved them into store
settings (.env) and a store pack (JSON). The values of the store the code was
written for are needed only to prove, with the parity harness, that the
de-hardcoded code still gives that store's answers - they are never committed.

This script reads the constants from an unpacked copy of the import commit
(never from another repository) and writes, into --out-dir (outside git):

- spicetown.pack.json  a store pack (pack_id "spicetown_legacy") with every
                       rule constant;
- spicetown.env        the non-secret settings (no tokens, no passwords,
                       nothing read from any production .env);
- legacy_cases.json    an oracle: the legacy functions' answers over synthetic
                       inputs (and, with --catalog-db, every catalog item of a
                       scratch database copy) for tests/test_store_pack_legacy.py.

    cd backend
    python scripts/packs/extract_legacy_pack.py --from-commit 56e095c \
        --out-dir "$STOREOPS_PRIVATE_DIR" [--catalog-db /path/to/scratch-copy.db]

--from-commit runs `git archive <commit> backend` into a temporary folder;
--legacy-backend <dir> uses an already unpacked `backend` folder instead.
The script itself holds no store values: it copies them from the legacy code.
Where a legacy rule was written as an if-chain rather than a constant
(po_sections.section_for), the chain's structure is restated below as pack
rules, reading every table and pattern from the legacy module; the oracle in
legacy_cases.json proves the restatement gives the same answer.
"""

import argparse
import contextlib
import datetime as dt
import importlib
import inspect
import io
import itertools
import json
import os
import pathlib
import re
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from typing import Dict, List

BACKEND = pathlib.Path(__file__).resolve().parents[2]


def unpack_commit(commit: str, dest: pathlib.Path) -> pathlib.Path:
    data = subprocess.run(
        ["git", "archive", "--format=tar", commit, "backend"],
        cwd=str(BACKEND.parent),
        check=True,
        capture_output=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(str(dest))
    return dest / "backend"


def import_legacy(legacy_backend: pathlib.Path, scratch: pathlib.Path):
    """Import the legacy modules (and nothing from this checkout)."""
    os.environ.update(
        {
            "DATABASE_URL": f"sqlite:///{scratch / 'legacy-import.db'}",
            "SCHEDULER_ENABLED": "false",
            "NOTIFICATIONS_ENABLED": "false",
            "TEAM_JOBS_ENABLED": "false",
        }
    )
    os.chdir(str(scratch))  # no .env is read
    sys.path[:] = [str(legacy_backend)] + [p for p in sys.path if pathlib.Path(p or ".").resolve() != BACKEND]
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    mods = {}
    for name in (
        "app.config",
        "app.services.po_sections",
        "app.services.fix_issues",
        "app.services.delivery_suggestions",
        "app.services.insights",
        "app.services.insights_data",
        "app.services.schedule_suggestions",
        "app.services.issue_leads",
        "app.services.whatsapp_groups",
        "app.services.flyers",
        "app.services.po_send",
        "app.services.ask_bot",
        "app.pos.sync.accuracy",
    ):
        mods[name.rsplit(".", 1)[-1]] = importlib.import_module(name)
    origin = pathlib.Path(mods["po_sections"].__file__).resolve()
    if legacy_backend.resolve() not in origin.parents:
        raise SystemExit(f"Imported the wrong po_sections: {origin}")
    return mods


# ---------------------------------------------------------------------------
# Pack
# ---------------------------------------------------------------------------


def _literals(func) -> List[str]:
    """String literals in a function's body, in source order."""
    import ast

    tree = ast.parse(inspect.getsource(func).lstrip())
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
    return out


def po_section_rules(po, kitchen_groups) -> dict:
    """legacy po_sections.section_for, restated as ordered pack rules. Every
    table and pattern comes from the legacy module (`po`); the oracle checks
    the result. Literal group / category names used by the if-chain are read
    from the function's own source and checked to be there."""
    source_literals = set(_literals(po.section_for))
    names = {
        "fish_group": "FISH MARKET",
        "frozen_fish": "Frozen Fish",
        "frozen_meat_fish": "Frozen - Meat & Fish",
        "fish_fresh": "Fish - Fresh",
        "meat_group": "HALAL MEAT",
        "meat_fresh": "Meat - Fresh",
        "flowers_group": "FRESH FLOWERS",
        "flowers": "Flowers & Leaves",
        "not_brand_sweet": r"ice ?cream|kulfi|\bmix\b",
        "sweets_desserts": "Frozen - Sweets & Desserts",
        "brand_coconut": r"^vadilal\b.*(shred|grated|coconut slice)",
        "frozen_veg": "Frozen - Vegetables",
        "frozen_cat": "Frozen Foods",
        "frozen_word": r"\bfrozen\b",
        "frozen_sweets_sub": "Frozen Sweets & Mithai",
        "produce_group": "PRODUCE",
        "produce_cat": "Produce",
        "fruits": "Fruits",
        "produce_fruits": "Produce - Fruits",
        "plants": "Plants",
        "plants_seeds": "Plants & Seeds",
        "produce_veg": "Produce - Vegetables",
    }
    missing = [v for v in names.values() if v not in source_literals]
    if missing:
        raise SystemExit(f"The legacy section_for no longer has {missing} - update the restatement")
    # The kitchen-made groups section_for sends to "Other": the excluded
    # groups that the function names, in source order.
    other_groups = [x for x in _literals(po.section_for) if x in kitchen_groups]
    if len(other_groups) != 2:
        raise SystemExit(f"Expected two kitchen groups in section_for, found {other_groups}")
    n = names
    frozen_any = {"any": [{"category": [n["frozen_cat"]]}, {"name_regex": n["frozen_word"]}]}
    pairs = lambda rules: [{"name_regex": p, "section": s} for p, s in rules]  # noqa: E731
    sub_map = {f"{c}|{s}": sec for (c, s), sec in po._SUBCATEGORY_SECTIONS.items()}
    rules = [
        {"when": {"group": [n["fish_group"]], "category": [n["frozen_fish"]]}, "section": n["frozen_meat_fish"]},
        {"when": {"group": [n["fish_group"]]}, "section": n["fish_fresh"]},
        {"when": {"group": [n["meat_group"]]}, "section": n["meat_fresh"]},
        {"when": {"group": [n["flowers_group"]]}, "section": n["flowers"]},
        {"when": {"group": other_groups}, "section": po.OTHER},
        {
            "when": {"name_regex": po._FROZEN_SWEET_BRAND.pattern, "name_not_regex": n["not_brand_sweet"]},
            "section": n["sweets_desserts"],
        },
        {"when": {"name_regex": n["brand_coconut"]}, "section": n["frozen_veg"]},
        {
            "when": {"category": [n["frozen_cat"]], "subcategory": [n["frozen_sweets_sub"]]},
            "first_match": pairs(po._FROZEN_KEYWORDS[:1]),
        },
        {"when": {"category": [n["frozen_cat"]], "subcategory": [n["frozen_sweets_sub"]]}, "section": n["sweets_desserts"]},
        {"when": {"category": [n["frozen_cat"]], "subcategory": ["", n["frozen_cat"]]}, "first_match": pairs(po._NOT_REALLY_FROZEN)},
        {"when": frozen_any, "first_match": pairs(po._FROZEN_KIND_RULES)},
        {"when": {"category": [n["frozen_cat"]]}, "lookup": "subcategory", "map": dict(po._FROZEN_SUBCATEGORY_SECTIONS)},
        {"when": frozen_any, "first_match": pairs(po._FROZEN_FRUIT_RULE)},
        {"when": frozen_any, "section": n["frozen_veg"]},
        {
            "when": {
                "any": [
                    {"group": [n["produce_group"]], "subcategory": [n["fruits"]]},
                    {"category": [n["produce_cat"]], "subcategory": [n["fruits"]]},
                    {"group": [n["produce_group"]], "category": [n["fruits"]]},
                ]
            },
            "section": n["produce_fruits"],
        },
        {"when": {"group": [n["produce_group"]], "category": [n["plants"]]}, "section": n["plants_seeds"]},
        {"when": {"any": [{"group": [n["produce_group"]]}, {"category": [n["produce_cat"]]}]}, "section": n["produce_veg"]},
        {"lookup": "category_subcategory", "map": sub_map},
        {"lookup": "category", "map": dict(po._CATEGORY_SECTIONS)},
    ]
    return {"other_section": po.OTHER, "extra_sections": [], "rules": rules}


def kitchen_label(insights_data) -> str:
    rule = insights_data.build_menu_map({"menus": []}).get("kitchen_rule") or ""
    suffix = " menus, without their bottled/canned drinks groups"
    if not rule.endswith(suffix):
        raise SystemExit(f"Unexpected kitchen_rule text: {rule!r}")
    return rule[: -len(suffix)]


def document_title(issue_leads) -> str:
    m = re.search(r'"document": \{\s*"title": "([^"]+)"', inspect.getsource(issue_leads.leads_page))
    if not m:
        raise SystemExit("Couldn't find the leads document title")
    return m.group(1)


def build_pack(m) -> dict:
    il = m["issue_leads"]
    return {
        "pack_version": 1,
        "pack_id": "spicetown_legacy",
        "label": "Legacy rules from the StoreOps import commit (parity only - never commit)",
        "menu": {
            "excluded_category_groups": sorted(m["fix_issues"].EXCLUDED_CATEGORY_GROUPS),
            "kitchen_groups": sorted(m["delivery_suggestions"].KITCHEN_GROUPS),
            "grocery_menus": sorted(m["insights"].GROCERY_MENUS),
            "kitchen_menu_prefixes": [m["insights_data"].KITCHEN_MENU_PREFIX],
            "kitchen_label": kitchen_label(m["insights_data"]),
        },
        "po_sections": po_section_rules(m["po_sections"], m["fix_issues"].EXCLUDED_CATEGORY_GROUPS),
        "house_vendors": {"prefixes": list(m["schedule_suggestions"].HOUSE_VENDOR_PREFIXES)},
        # The legacy channel_of is exactly the generic Toast rules (the
        # oracle below checks it).
        "channels": {
            "empty": "Other",
            "rules": [
                {"equals": ["no dining option"], "channel": "Other"},
                {"starts_with": ["in-store", "in store"], "channel": "In-store"},
                {"equals": ["pick up", "pickup", "take out", "takeout"], "channel": "Pick up"},
            ],
            "split_on": " - ",
            "rename": {"online ordering": "Online ordering"},
            "default": "Other",
        },
        "issue_leads": {
            "document_title": document_title(il),
            "leads": [
                {
                    "key": d["key"],
                    "name": d["name"],
                    "title": d["title"],
                    "departments": d.get("departments"),
                    "vendors": list(d.get("vendors") or []),
                }
                for d in il.DOC_LEADS
            ],
            "fallback": dict(il.DOC_FALLBACK),
            "section_proposals": {
                k: {"lead": lead, "basis": basis, "why": why} for k, (lead, basis, why) in il.SECTION_PROPOSALS.items()
            },
            "section_notes": dict(il.SECTION_NOTES),
        },
        # Built-in checklists and category rules stay in that store's own
        # database (migration rows); the pack adds none.
        "checklists": [],
        "whatsapp_roles": [{"key": k, "label": label} for k, label in m["whatsapp_groups"].BUILTIN_GROUPS],
        "flyer_categories": [{"key": k, "label": label, "folder": folder} for k, label, folder in m["flyers"].CATEGORIES],
        "category_rules": [],
    }


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def store_description(ask_bot) -> str:
    text = " ".join(ask_bot.SYSTEM_PROMPT_TEMPLATE.split())
    m = re.search(r"You are the Ask Inventory Bot for (.+?), (a .+?)\. You answer", text)
    if not m:
        raise SystemExit("Couldn't find the store description in the Ask bot prompt")
    return m.group(2)


def fallback_domain(po_send) -> str:
    m = re.search(r'rsplit\("@", 1\)\[-1\] or "([^"]+)"', inspect.getsource(po_send))
    if not m:
        raise SystemExit("Couldn't find the PO email fallback domain")
    return m.group(1)


def build_env(m) -> List[str]:
    fields = m["config"].Settings.model_fields
    acc = m["accuracy"]
    values = [
        ("STORE_NAME", m["po_send"].STORE_NAME),
        ("STORE_ADDRESS", m["po_send"].STORE_ADDRESS),
        ("STORE_PHONE", m["po_send"].STORE_PHONE),
        ("STORE_DESCRIPTION", store_description(m["ask_bot"])),
        ("STORE_TIMEZONE", fields["toast_timezone"].default),
        ("FIRST_SALES_DATE", acc.FIRST_REAL_SALES_DATE.isoformat()),
        ("BUSINESS_DAY_CUTOFF_HOUR", str(acc.CLOSEOUT_HOUR)),
        ("AP_INBOX_EMAIL", fields["toast_invoice_email"].default),
        ("PUBLIC_BASE_URL", fields["public_base_url"].default),
        ("FLYERS_DIR", fields["flyers_dir"].default),
        ("EMAIL_FALLBACK_DOMAIN", fallback_domain(m["po_send"])),
        ("APP_STORE_URL", fields["app_store_url"].default),
        ("PLAY_STORE_URL", fields["play_store_url"].default),
        ("AI_ENABLED", "true"),
        ("POS_PROVIDER", "toast"),
    ]
    lines = [
        "# Legacy store settings for parity runs only (docs/M2_CONTRACT.md 1.6).",
        "# PRIVATE - never commit. No secrets: tokens and passwords are not here.",
        f"# Written {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')} by scripts/packs/extract_legacy_pack.py",
        "# LABELS_URL is left out on purpose: the legacy labels site is under a",
        "# domain StoreOps refuses (contract rule 0.3).",
    ]
    for key, value in values:
        lines.append(f"{key}={json.dumps(value)}" if any(ch in value for ch in " #'\"$") else f"{key}={value}")
    return lines


# ---------------------------------------------------------------------------
# Oracle
# ---------------------------------------------------------------------------


def _synthetic_items(po, kitchen_groups) -> List[tuple]:
    """Inputs that reach every branch of the legacy section_for."""
    groups = ["", "RETAIL", "FISH MARKET", "HALAL MEAT", "FRESH FLOWERS", "PRODUCE", " produce ", "fish market"]
    groups += sorted(kitchen_groups) + [g.lower() for g in sorted(kitchen_groups)]
    categories = sorted({c for c, _ in po._SUBCATEGORY_SECTIONS} | set(po._CATEGORY_SECTIONS)
                        | {"", "Frozen Foods", "Frozen Fish", "Produce", "Fruits", "Plants", "Vegetables", "Unknown"})
    subcats = sorted({s for _, s in po._SUBCATEGORY_SECTIONS} | set(po._FROZEN_SUBCATEGORY_SECTIONS)
                     | {"", "Frozen Foods", "Frozen Sweets & Mithai", "Fruits", "Leafy Vegetables", "Other Sub"})
    names = [
        "", "Plain Item", "Frozen Mango Kulfi", "Vadilal Kaju Katli", "Vadilal Kaju Katli Ice Cream",
        "Vadilal Grated Coconut", "Vadilal Shredded Coconut", "Dried Figs", "Dry Ginger", "Coconut Milk (Can)",
        "Desiccated Coconut", "Everest Masala", "Veg Puffs", "Samosa 20pc", "Gulab Jamun", "Sambar Mix",
        "Frozen Coconut", "Chole Masala Frozen", "Fish Fry", "Malabar Paratha", "Mango Pulp Frozen",
        "Frozen Peas", "Frozen Unknown", "Kaju Roll", "Mango Sandwich", "Ice Cream 500 ml", "  FROZEN okra ",
        "Vadilal Motichoor Laddu Mix",
    ]
    out = []
    for g in groups:
        for c in categories:
            for s in subcats[:12] if c not in ("Frozen Foods", "Produce", "") else subcats:
                out.append((g, c, s, names[(len(out) * 7) % len(names)]))
    for g, c, s in itertools.product(["", "PRODUCE", "RETAIL"], ["Frozen Foods", "Produce", "Fruits", ""],
                                     ["", "Frozen Foods", "Frozen Sweets & Mithai", "Fruits", "Ice Creams"]):
        for name in names:
            out.append((g, c, s, name))
    for (c, s) in po._SUBCATEGORY_SECTIONS:
        out.append(("RETAIL", c, s, "Item"))
        out.append(("RETAIL", f" {c} ", f" {s}", "Item"))
    for pattern, _ in po._FROZEN_KEYWORDS + po._NOT_REALLY_FROZEN:
        word = re.sub(r"[\\^$()|?*+\[\]{}.]", " ", pattern.split("|")[0]).strip() or "x"
        out.append(("RETAIL", "Frozen Foods", "", f"{word} item"))
        out.append(("RETAIL", "Snacks & Savories", "", f"frozen {word}"))
    return out


def _catalog_items(path: str) -> List[tuple]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT DISTINCT category_group, category, subcategory, name FROM inventory_snapshots"
        ).fetchall()
    finally:
        conn.close()
    return [tuple(r) for r in rows]


def build_cases(m, catalog_db: str) -> dict:
    po = m["po_sections"]
    items = _synthetic_items(po, m["fix_issues"].EXCLUDED_CATEGORY_GROUPS)
    if catalog_db:
        items += _catalog_items(catalog_db)
    seen = set()
    section_cases = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        section_cases.append([*item, po.section_for(*item)])
    dining = [
        None, "", "  ", "No Dining Option", "In-Store Purchase", "in store", "Pick Up", "pickup", "Take Out",
        "TAKEOUT", "Online Ordering - Takeout", "online ordering", "DoorDash - Delivery", "DoorDash - Takeout",
        "Uber Eats - Delivery", "Catering - Pick Up", "Catering - Delivery", "Kiosk", " - Delivery", "Grubhub",
        "In-Store Purchase - Bar", "Online Ordering", "Delivery",
    ]
    channel_cases = [[d, m["insights"].channel_of(d)] for d in dining]
    ss = m["schedule_suggestions"]
    vendors = ["Best Foods", "Acme Foods Inc", ""]
    for prefix in ss.HOUSE_VENDOR_PREFIXES:
        vendors += [prefix, prefix.title(), f"{prefix} - Kitchen", f"{prefix} llc", f"x {prefix}", prefix[:-1]]
    house_cases = [[v, ss._is_house(ss.vendor_key(v))] for v in vendors]
    idata = m["insights_data"]
    prefix = idata.KITCHEN_MENU_PREFIX
    menus = [prefix.upper(), f"{prefix.title()} (GH)", f"{prefix} catering", "Retail", "Produce", "Kitchen", prefix[:-1]]
    groups = ["Drinks - Soda / Sparkling", "Drinks - House Specials", "Mains", ""]
    kitchen_cases = [[mn, g, idata.is_kitchen_path(mn, g)] for mn in menus for g in groups]
    return {
        "schema": 1,
        "section_for": section_cases,
        "builtin_sections": list(po.BUILTIN_SECTIONS),
        "channel_of": channel_cases,
        "house_vendor": house_cases,
        "is_kitchen_path": kitchen_cases,
        "excluded_category_groups": sorted(m["fix_issues"].EXCLUDED_CATEGORY_GROUPS),
        "kitchen_groups": sorted(m["delivery_suggestions"].KITCHEN_GROUPS),
        "grocery_menus": sorted(m["insights"].GROCERY_MENUS),
        "flyer_categories": [list(c) for c in m["flyers"].CATEGORIES],
        "whatsapp_roles": [list(g) for g in m["whatsapp_groups"].BUILTIN_GROUPS],
        "kitchen_rule": idata.build_menu_map({"menus": []}).get("kitchen_rule"),
        "issue_leads": {
            "doc_leads": m["issue_leads"].DOC_LEADS,
            "doc_fallback": m["issue_leads"].DOC_FALLBACK,
            "section_proposals": {k: list(v) for k, v in m["issue_leads"].SECTION_PROPOSALS.items()},
            "section_notes": m["issue_leads"].SECTION_NOTES,
        },
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-commit", help="unpack `backend` from this commit of this repository (e.g. 56e095c)")
    src.add_argument("--legacy-backend", help="an unpacked legacy `backend` folder")
    ap.add_argument("--out-dir", default=os.environ.get("STOREOPS_PRIVATE_DIR", ""))
    ap.add_argument("--catalog-db", default="", help="a scratch copy of a database to take catalog items from")
    args = ap.parse_args(argv)
    if not args.out_dir:
        raise SystemExit("--out-dir (or STOREOPS_PRIVATE_DIR) is required")
    out = pathlib.Path(args.out_dir).expanduser().resolve()
    if BACKEND.parent in out.parents or out == BACKEND.parent:
        if out != BACKEND / "packs" / "private" and (BACKEND / "packs" / "private") not in out.parents:
            raise SystemExit(f"{out} is inside the repository; use a folder outside git (or backend/packs/private)")
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="legacy-pack-") as tmp:
        tmp_path = pathlib.Path(tmp)
        legacy = unpack_commit(args.from_commit, tmp_path) if args.from_commit else pathlib.Path(args.legacy_backend)
        scratch = tmp_path / "scratch"
        scratch.mkdir()
        with contextlib.redirect_stdout(sys.stderr):
            mods = import_legacy(legacy.resolve(), scratch)
            pack = build_pack(mods)
            env_lines = build_env(mods)
            cases = build_cases(mods, args.catalog_db)
            icons = sorted((legacy / "app" / "assets").glob("*-icon.jpg"))
            legacy_icon = icons[0].read_bytes() if icons else None
    if legacy_icon is not None:
        env_lines.append(f"DOCUMENT_LOGO_FILE={json.dumps(str(out / 'document_logo.jpg'))}")
        (out / "document_logo.jpg").write_bytes(legacy_icon)
    (out / "spicetown.pack.json").write_text(json.dumps(pack, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "spicetown.env").write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    (out / "legacy_cases.json").write_text(json.dumps(cases, ensure_ascii=False) + "\n", encoding="utf-8")
    # Staff names for the fresh-store test (docs/M2_CONTRACT.md 3.6).
    names = sorted({d["name"] for d in pack["issue_leads"]["leads"]} | {pack["issue_leads"]["fallback"]["name"]})
    (out / "forbidden_tokens.txt").write_text("\n".join(names) + "\n", encoding="utf-8")
    for name in ("spicetown.pack.json", "spicetown.env", "legacy_cases.json", "forbidden_tokens.txt", "document_logo.jpg"):
        if not (out / name).exists():
            continue
        os.chmod(out / name, 0o600)
    print(
        json.dumps(
            {
                "out_dir": str(out),
                "pack_rules": len(pack["po_sections"]["rules"]),
                "section_cases": len(cases["section_for"]),
                "catalog_db": bool(args.catalog_db),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
