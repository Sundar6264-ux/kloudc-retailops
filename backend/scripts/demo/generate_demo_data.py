"""Build the DEMO store's made-up data into a NEW folder (plan 3.1a, MT13).

    python scripts/demo/generate_demo_data.py --out /path/to/new-folder \
        [--seed 2026] [--days 90] [--items 400] [--end-date YYYY-MM-DD]

The folder must not exist (or be empty). It gets:

    <out>/retailops.db          a fresh database at Alembic head
    <out>/uploads/invoices/    made-up invoice PDFs (each says it is a sample)
    <out>/uploads/team/        (empty) team uploads
    <out>/uploads/flyers/      (empty) flyers
    <out>/demo_dataset.json    what was generated (seed, dates, counts)

What is in the database (all made up, deterministic for the same seed and
end date - the review password hash and the migration-seeded rows' own
timestamps are the only exceptions):
- the DEMO store pack's checklists and WhatsApp role (via ``python -m
  app.store init`` when MT8's command exists, else the same rows directly),
  and none of the rows old migrations seed for one particular store;
- about ``--items`` catalog items in ~40 categories, with made-up barcodes
  (EAN-13 in the 20-29 in-store prefix range, never a real product), costs,
  prices and 8 obviously fictitious vendors;
- ``--days`` days of sales lines (orders table, source "csv"), weekly catalog
  CSV uploads loaded through the real upload code (inventory snapshots +
  item vendors), vendor delivery schedules and contacts (``.example``
  addresses), purchase log, placed orders and a few cart lines;
- invoices with sample PDFs, checklist definitions + daily submissions,
  tasks (+ recurring templates), team chat and announcements;
- staff logins named by role (no real people) with unusable random
  passwords, and the App Review login ``appreview`` whose password comes
  from DEMO_REVIEW_PASSWORD or ``--review-password-file``.

With ``--pg-url <empty PostgreSQL database>`` the finished database is
also copied into PostgreSQL (scripts/pg/copy_sqlite_to_pg.py: migrated to
the same revision, every row copied, checksums compared - 0 differences or
the run fails), for a DEMO store on the shared PostgreSQL service. The
SQLite file stays the source of truth of the generator (same rows for the
same seed on both).

Nothing here talks to the network. Python 3.9 compatible.
"""

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import pathlib
import random
import secrets
import subprocess
import sys
import zoneinfo
from typing import Dict, List, Optional, Tuple

BACKEND = pathlib.Path(__file__).resolve().parents[2]
HERE = pathlib.Path(__file__).resolve().parent
PACK_PATH = BACKEND / "packs" / "demo.json"
GENERATOR_VERSION = 1
DATASET_KEY = "demo_dataset"  # setting_changes key marking a generated DEMO database
MIN_PASSWORD_LENGTH = 10
# Features the review login does not get (same choices as the earlier App
# Review account): flyers and Build checklists are refused for demo logins,
# PO-section editing is an admin tool, and Sales insights need a POS with
# analytics.
REVIEW_LEFT_OUT = frozenset({"marketing_flyers", "checklist_builder", "category_edits", "sales_insights"})

# Settings forced for the generator process (and the subprocesses it runs).
# Every outside channel is blank: nothing can be sent while generating.
_BLANK_SECRETS = (
    "WHAPI_API_TOKEN",
    "GMAIL_APP_PASSWORD",
    "SMTP_PASSWORD",
    "ANTHROPIC_API_KEY",
    "TOAST_CLIENT_ID",
    "TOAST_CLIENT_SECRET",
    "TOAST_ANALYTICS_CLIENT_ID",
    "TOAST_ANALYTICS_CLIENT_SECRET",
    "FIREBASE_CREDENTIALS_FILE",
)


class GenerateError(RuntimeError):
    pass


# --------------------------------------------------------------------------------------------
# Environment (before anything from `app` is imported)
# --------------------------------------------------------------------------------------------


def demo_environment(out: pathlib.Path, timezone: str) -> Dict[str, str]:
    uploads = out / "uploads"
    env = {
        "DATABASE_URL": f"sqlite:///{out / 'retailops.db'}",
        "UPLOADS_ROOT": str(uploads),
        "INVOICE_UPLOADS_DIR": str(uploads / "invoices"),
        "TEAM_UPLOADS_DIR": str(uploads / "team"),
        "FLYERS_DIR": str(uploads / "flyers"),
        "STORE_CODE": "DEMO",
        "POS_PROVIDER": "csv",
        "DEMO_MODE": "true",
        "STORE_TIMEZONE": timezone,
        "TOAST_TIMEZONE": timezone,
        "SCHEDULER_ENABLED": "false",
        "NOTIFICATIONS_ENABLED": "false",
        "TEAM_JOBS_ENABLED": "false",
    }
    env.update(SAFE_OVERRIDES)
    return env


# Forced on every DEMO process (generator, smoke check, the DEMO server the
# runner starts): no credentials, and every outside base URL on the discard
# port, so nothing can leave the machine whatever the .env says.
DISCARD_URL = "http://127.0.0.1:9"
SAFE_OVERRIDES: Dict[str, str] = dict(
    {key: "" for key in _BLANK_SECRETS},
    TOAST_API_BASE=DISCARD_URL,
    TOAST_ANALYTICS_BASE=DISCARD_URL,
    WHAPI_BASE_URL=DISCARD_URL,
    SMTP_HOST="127.0.0.1",
    SMTP_PORT="9",
    AI_ENABLED="false",
    LABELS_URL="",
    AP_INBOX_EMAIL="",
    TOAST_INVOICE_EMAIL="",
    WHATSAPP_GROUP_CHAT_IDS="",
)


def _run(cmd: List[str], env: Dict[str, str]) -> subprocess.CompletedProcess:
    full = dict(os.environ)
    full.update(env)
    proc = subprocess.run(cmd, cwd=str(BACKEND), env=full, capture_output=True, text=True)
    if proc.returncode != 0:
        raise GenerateError(f"{' '.join(cmd)} failed ({proc.returncode}):\n{proc.stdout}\n{proc.stderr}")
    return proc


# --------------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------------


def ean13(body12: str) -> str:
    digits = [int(c) for c in body12]
    total = sum(d * (3 if i % 2 else 1) for i, d in enumerate(digits))
    return body12 + str((10 - total % 10) % 10)


def hex_id(rng: random.Random, n: int = 32) -> str:
    return "".join(rng.choice("0123456789abcdef") for _ in range(n))


def money(x: float) -> float:
    return float(f"{x:.2f}")


class Clock:
    """Local store time <-> UTC for the made-up days."""

    def __init__(self, timezone: str):
        self.tz = zoneinfo.ZoneInfo(timezone)

    def utc(self, day: dt.date, hour: int, minute: int = 0, second: int = 0) -> dt.datetime:
        local = dt.datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=self.tz)
        return local.astimezone(dt.timezone.utc)


# --------------------------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------------------------


def build_catalog(rng: random.Random, target: int) -> List[dict]:
    from scripts.demo import demo_catalog as vocab  # noqa: E402 (path set in main)

    base: List[dict] = []
    for group, cats in vocab.CATALOG.items():
        for category, (subcategory, vendor, (lo, hi), taxable, products) in cats.items():
            for name, sizes in products:
                for size in sizes:
                    base.append(
                        {
                            "group": group,
                            "category": category,
                            "subcategory": subcategory,
                            "vendor": vendor,
                            "name": f"{name} {size}",
                            "lo": lo,
                            "hi": hi,
                            "taxable": taxable,
                        }
                    )
    items = list(base)
    # Fill up to `target` with "Organic" / "Family size" variants (made-up, generic).
    variants = ["Organic", "Family Size", "Value Pack"]
    v = 0
    while len(items) < target:
        src = base[v % len(base)]
        tag = variants[(v // len(base)) % len(variants)]
        items.append(dict(src, name=f"{tag} {src['name']}"))
        v += 1
    if target < len(items):
        # Fewer than the base list (tests): an even spread over every department.
        keep = sorted(rng.sample(range(len(items)), target))
        items = [items[i] for i in keep]

    out = []
    for i, it in enumerate(items):
        price = money(rng.uniform(it["lo"], it["hi"]))
        if it["name"].startswith(("Organic", "Family Size", "Value Pack")):
            price = money(price * 1.25)
        cost = money(price * rng.uniform(0.55, 0.78))
        vendor = it["vendor"]
        vendors = [vendor]
        secondary = vocab.SECONDARY_VENDOR.get(vendor)
        if secondary and rng.random() < 0.15:
            vendors.append(secondary)
        out.append(
            {
                "item_id": f"DEMO-{10001 + i}",
                "name": it["name"],
                "group": it["group"],
                "category": it["category"],
                "subcategory": it["subcategory"],
                "price": price,
                "cost": cost,
                "vendors": vendors,
                "vendor_codes": [f"{''.join(w[0] for w in v.split())}-{2000 + i}" for v in vendors],
                "barcode": ean13(f"2{rng.randint(0, 9)}{i:05d}{rng.randint(0, 99999):05d}"),
                "taxable": it["taxable"],
            }
        )
    # Popularity: a few best sellers, a long tail (rank order shuffled).
    ranks = list(range(1, len(out) + 1))
    rng.shuffle(ranks)
    for item, rank in zip(out, ranks):
        item["weight"] = 1.0 / (rank ** 0.85)
    return out


# --------------------------------------------------------------------------------------------
# The generator
# --------------------------------------------------------------------------------------------


class DemoGenerator:
    def __init__(self, db, out: pathlib.Path, seed: int, days: int, items: int, end_date: dt.date, timezone: str):
        self.db = db
        self.out = out
        self.seed = seed
        self.days = days
        self.n_items = items
        self.end = end_date  # data runs through the day before end_date
        self.start = end_date - dt.timedelta(days=days)
        self.clock = Clock(timezone)
        self.timezone = timezone
        self.counts: Dict[str, int] = {}
        self.users: Dict[str, object] = {}
        self.catalog: List[dict] = []
        self.sold: Dict[dt.date, Dict[str, float]] = {}

    def rng(self, part: str) -> random.Random:
        return random.Random(f"{self.seed}:{part}")

    @property
    def dates(self) -> List[dt.date]:
        return [self.start + dt.timedelta(days=i) for i in range(self.days)]

    def stamp(self, day: dt.date, hour: int = 9, minute: int = 0) -> dt.datetime:
        return self.clock.utc(day, hour, minute)

    # --- users and teams -------------------------------------------------------------------

    def make_users(self, review_password: str) -> None:
        from app.auth import hash_password
        from app.features import FEATURE_KEYS
        from app.models import User
        from app.team.models import Team, TeamMember
        from scripts.demo import demo_catalog as vocab

        created = self.stamp(self.start - dt.timedelta(days=30))
        teams = {t.name: t for t in self.db.query(Team).all()}
        everything = json.dumps(sorted(FEATURE_KEYS))
        for username, is_admin, can_assign, team_names in vocab.STAFF:
            user = User(
                username=username,
                # Nobody signs in as staff: a random password nobody knows.
                password_hash=hash_password(secrets.token_urlsafe(24)),
                is_admin=is_admin,
                can_assign=can_assign,
                allowed_features=everything,
                created_at=created,
            )
            self.db.add(user)
            self.db.flush()
            self.users[username] = user
            for name in team_names:
                if name in teams:
                    self.db.add(TeamMember(team_id=teams[name].id, user_id=user.id, created_at=created))
        reviewer = User(
            username=vocab.REVIEW_USERNAME,
            password_hash=hash_password(review_password),
            is_admin=False,
            # Demo logins can't assign anyway (tasks_service); keep the flag honest.
            can_assign=False,
            allowed_features=json.dumps(sorted(FEATURE_KEYS - REVIEW_LEFT_OUT)),
            created_at=created,
        )
        self.db.add(reviewer)
        self.db.flush()
        self.users[vocab.REVIEW_USERNAME] = reviewer
        if "Grocery" in teams:
            self.db.add(TeamMember(team_id=teams["Grocery"].id, user_id=reviewer.id, created_at=created))
        self.db.commit()
        self.counts["users"] = len(self.users)

    # --- catalog, sales, stock ---------------------------------------------------------------

    def make_sales(self) -> None:
        from sqlalchemy import insert

        from app.models import Order

        rng = self.rng("orders")
        self.catalog = build_catalog(self.rng("catalog"), self.n_items)
        weights = [it["weight"] for it in self.catalog]
        rows = []
        channels = [("In store", 0.84), ("Pick up", 0.11), ("Online ordering", 0.05)]
        for d_index, day in enumerate(self.dates):
            weekday_factor = {5: 1.35, 6: 1.25, 4: 1.1}.get(day.weekday(), 1.0)
            trend = 1.0 + 0.10 * d_index / max(self.days, 1)
            n_orders = max(20, int(rng.gauss(115, 12) * weekday_factor * trend))
            sold: Dict[str, float] = {}
            for o in range(n_orders):
                order_id = f"demo-o-{day:%Y%m%d}-{o:04d}"
                check_id = f"demo-c-{day:%Y%m%d}-{o:04d}"
                minute_of_day = int(rng.triangular(8 * 60, 21 * 60, 17 * 60))
                paid = self.clock.utc(day, minute_of_day // 60, minute_of_day % 60, rng.randint(0, 59))
                opened = paid - dt.timedelta(minutes=rng.randint(1, 6))
                r = rng.random()
                dining = channels[0][0] if r < channels[0][1] else channels[1][0] if r < 0.95 else channels[2][0]
                n_lines = min(9, max(1, int(rng.expovariate(1 / 3.0)) + 1))
                picks = rng.choices(self.catalog, weights=weights, k=n_lines)
                voided_order = rng.random() < 0.004
                for li, it in enumerate(picks):
                    qty = 1 if rng.random() < 0.8 else rng.choice([2, 2, 3, 4])
                    unit = it["price"]
                    net = money(unit * qty * (0.9 if rng.random() < 0.05 else 1.0))
                    tax = money(net * 0.0635) if it["taxable"] else 0.0
                    line_id = f"demo-l-{day:%Y%m%d}-{o:04d}-{li}"
                    rows.append(
                        {
                            "toast_order_guid": order_id,
                            "toast_check_guid": check_id,
                            "toast_selection_guid": line_id,
                            "parent_selection_guid": None,
                            "business_date": day,
                            "opened_at": opened,
                            "paid_at": paid,
                            "item_guid": it["item_id"],
                            "item_name": it["name"],
                            "quantity": float(qty),
                            "unit_price": unit,
                            "net_price": net,
                            "tax_amount": tax,
                            "voided": voided_order,
                            "dining_option": dining,
                            "created_at": paid,
                            "updated_at": paid,
                            "source": "csv",
                            "external_order_id": order_id,
                            "external_check_id": check_id,
                            "external_line_id": line_id,
                            "parent_external_line_id": None,
                            "is_modifier": False,
                            "channel": None,
                            "dining_option_id": dining,
                        }
                    )
                    if not voided_order:
                        sold[it["item_id"]] = sold.get(it["item_id"], 0.0) + qty
            self.sold[day] = sold
        for i in range(0, len(rows), 2000):
            self.db.execute(insert(Order), rows[i : i + 2000])
        self.db.commit()
        self.counts["order_lines"] = len(rows)
        self.counts["orders"] = len({r["toast_order_guid"] for r in rows})

    def _window(self, day: dt.date, n: int, item: dict) -> float:
        units = 0.0
        for k in range(1, n + 1):
            units += self.sold.get(day - dt.timedelta(days=k), {}).get(item["item_id"], 0.0)
        return money(units * item["price"])

    def make_stock_and_uploads(self) -> None:
        """Simulate stock day by day (sales out, vendor deliveries in) and
        load a catalog CSV every Monday and on the last day through the real
        upload code, like a CSV-only store would."""
        from app.models import PurchaseLogEntry
        from app.pos.adapters.csv_only.template import template_csv
        from app.services.inventory_parser import parse_and_store
        from scripts.demo import demo_catalog as vocab

        rng = self.rng("stock")
        days_total = max(self.days, 1)
        stock: Dict[str, float] = {}
        par: Dict[str, int] = {}
        for it in self.catalog:
            avg = sum(self.sold[d].get(it["item_id"], 0.0) for d in self.dates) / days_total
            cycle = 7 / max(1, len(vocab.VENDORS[it["vendors"][0]][0]))
            par[it["item_id"]] = int(math.ceil(avg * (cycle + 3))) + 4
            stock[it["item_id"]] = float(par[it["item_id"]])
        last_day = self.dates[-1]
        upload_days = [d for d in self.dates if d.weekday() == 0] + [last_day]
        purchase_from = last_day - dt.timedelta(days=21)
        clerk = self.users.get("stock_clerk")
        uploads = 0
        purchases = 0
        for day in self.dates:
            for it in self.catalog:
                stock[it["item_id"]] -= self.sold[day].get(it["item_id"], 0.0)
            # Deliveries arrive in the morning of the vendor's weekdays.
            for it in self.catalog:
                vendor = it["vendors"][0]
                if day.weekday() in vocab.VENDORS[vendor][0] and stock[it["item_id"]] < par[it["item_id"]] * 0.6:
                    qty = par[it["item_id"]] - max(0.0, stock[it["item_id"]])
                    stock[it["item_id"]] = float(par[it["item_id"]])
                    if day >= purchase_from:
                        self.db.add(
                            PurchaseLogEntry(
                                item_id=it["item_id"],
                                item_name=it["name"],
                                supplier=vendor,
                                quantity_received=float(round(qty)),
                                unit_cost=it["cost"],
                                received_date=day,
                                notes=None,
                                logged_by_user_id=clerk.id if clerk is not None else None,
                                created_at=self.stamp(day, 10, rng.randint(0, 59)),
                            )
                        )
                        purchases += 1
            if day in upload_days:
                rows = []
                for idx, it in enumerate(self.catalog):
                    qty = round(stock[it["item_id"]])
                    barcode: Optional[str] = it["barcode"]
                    supplier = "; ".join(it["vendors"])
                    codes = "; ".join(it["vendor_codes"])
                    if day == last_day:
                        # A few things for Fix Issues to find (made up on purpose).
                        if idx % 67 == 5:
                            qty = -rng.randint(1, 4)  # negative count
                        if idx % 53 == 7:
                            barcode = None  # missing barcode
                        if idx % 97 == 11:
                            supplier, codes = "", ""  # no vendor
                    rows.append(
                        {
                            "item_id": it["item_id"],
                            "name": it["name"],
                            "category_group": it["group"],
                            "category": it["category"],
                            "subcategory": it["subcategory"],
                            "inventory_status": "In stock" if qty > 0 else "Out of stock",
                            "inventory_quantity": qty,
                            "cost": f"{it['cost']:.2f}",
                            "price": f"{it['price']:.2f}",
                            "supplier": supplier,
                            "supplier_item_id": codes,
                            "barcode": barcode,
                            "last_7_day_sales": f"{self._window(day, 7, it):.2f}",
                            "last_30_day_sales": f"{self._window(day, 30, it):.2f}",
                            "last_90_day_sales": f"{self._window(day, 90, it):.2f}",
                        }
                    )
                content = template_csv(rows).encode("utf-8")
                parse_and_store(self.db, f"demo-catalog-{day.isoformat()}.csv", content, day)
                uploads += 1
        self.db.commit()
        self._fix_upload_times(upload_days)
        self.counts["catalog_items"] = len(self.catalog)
        self.counts["catalog_uploads"] = uploads
        self.counts["purchase_log"] = purchases

    def _fix_upload_times(self, days: List[dt.date]) -> None:
        """parse_and_store stamps rows with "now"; give each upload the
        morning of its own day so the data is the same on every run."""
        from sqlalchemy import update

        from app.item_vendor_models import ItemVendor
        from app.models import InventorySnapshot

        for day in days:
            self.db.execute(
                update(InventorySnapshot)
                .where(InventorySnapshot.snapshot_date == day)
                .values(uploaded_at=self.stamp(day, 7, 30))
            )
        when = self.stamp(days[-1], 7, 30)
        self.db.execute(update(ItemVendor).values(created_at=when, updated_at=when))
        self.db.commit()

    # --- vendors, ordering ---------------------------------------------------------------------

    def make_vendors_and_orders(self) -> None:
        from app.models import CartItem, PlacedOrder, VendorSchedule
        from app.po_send_models import VendorContact
        from app.services.po_send import vendor_key
        from scripts.demo import demo_catalog as vocab

        rng = self.rng("vendors")
        manager = self.users["store_manager"]
        lead = self.users["grocery_lead"]
        created = self.stamp(self.start, 9)
        for vendor, (weekdays, lead_days, email) in vocab.VENDORS.items():
            self.db.add(
                VendorSchedule(
                    vendor=vendor, delivery_weekdays=json.dumps(weekdays), lead_days=lead_days, active=True,
                    created_at=created,
                )
            )
            self.db.add(
                VendorContact(
                    vendor=vendor,
                    vendor_key=vendor_key(vendor),
                    contact_name="Order desk",
                    email=email,
                    whatsapp=None,
                    notes="Made-up demo vendor.",
                    send_po=True,
                    created_by_user_id=manager.id,
                    created_by_name=manager.username,
                    created_at=created,
                )
            )
        # Placed orders: one per vendor delivery over the last 4 weeks.
        last_day = self.dates[-1]
        by_vendor: Dict[str, List[dict]] = {}
        for it in self.catalog:
            by_vendor.setdefault(it["vendors"][0], []).append(it)
        placed = 0
        for day in self.dates:
            if day < last_day - dt.timedelta(days=28):
                continue
            for vendor, (weekdays, lead_days, _email) in vocab.VENDORS.items():
                delivery = day + dt.timedelta(days=lead_days)
                if delivery.weekday() not in weekdays or not by_vendor.get(vendor):
                    continue
                lines = rng.sample(by_vendor[vendor], k=min(len(by_vendor[vendor]), rng.randint(5, 14)))
                items = [
                    {"name": it["name"], "supplier_item_id": it["vendor_codes"][0], "qty": rng.randint(2, 12)}
                    for it in lines
                ]
                who = manager if rng.random() < 0.5 else lead
                self.db.add(
                    PlacedOrder(
                        supplier=vendor,
                        item_count=len(items),
                        items_json=json.dumps(items),
                        placed_by_user_id=who.id,
                        placed_by_name=who.username,
                        placed_at=self.stamp(day, 15, rng.randint(0, 59)),
                        status="placed",
                        source=None,
                        note=None,
                        for_delivery_date=delivery,
                    )
                )
                placed += 1
        # A few lines waiting in the cart.
        cart = 0
        for vendor in ("Example Dry Goods Wholesale", "Demo Snack Wholesale"):
            for it in rng.sample(by_vendor.get(vendor, []), k=min(4, len(by_vendor.get(vendor, [])))):
                self.db.add(
                    CartItem(
                        supplier=vendor,
                        item_id=it["item_id"],
                        name=it["name"],
                        supplier_item_id=it["vendor_codes"][0],
                        qty=float(rng.randint(2, 10)),
                        case_of=1.0,
                        added_by_user_id=lead.id,
                        added_at=self.stamp(last_day, 16, rng.randint(0, 59)),
                        section=None,
                    )
                )
                cart += 1
        self.db.commit()
        self.counts["vendors"] = len(vocab.VENDORS)
        self.counts["placed_orders"] = placed
        self.counts["cart_items"] = cart

    # --- invoices --------------------------------------------------------------------------

    def make_invoices(self) -> None:
        from app.demo import DEMO_NOT_SENT
        from app.models import InvoiceSubmission
        from scripts.demo import demo_catalog as vocab
        from scripts.demo.fake_pdf import text_pdf

        rng = self.rng("invoices")
        folder = self.out / "uploads" / "invoices"
        folder.mkdir(parents=True, exist_ok=True)
        by_vendor: Dict[str, List[dict]] = {}
        for it in self.catalog:
            by_vendor.setdefault(it["vendors"][0], []).append(it)
        submitters = [self.users["store_manager"], self.users["grocery_lead"], self.users["stock_clerk"]]
        n = 0
        last_day = self.dates[-1]
        for day in self.dates:
            for vendor, (weekdays, _lead, _email) in vocab.VENDORS.items():
                if day.weekday() not in weekdays or rng.random() > 0.35 or not by_vendor.get(vendor):
                    continue
                lines = rng.sample(by_vendor[vendor], k=min(len(by_vendor[vendor]), rng.randint(4, 12)))
                total = 0.0
                text = [
                    f"{vendor}",
                    "123 Sample Street, Example City (made-up address)",
                    "",
                    f"INVOICE  DEMO-INV-{day:%Y%m%d}-{n:03d}",
                    f"Date: {day.isoformat()}",
                    f"Bill to: {vocab.STORE_NAME}",
                    "",
                    "Qty  Item                                   Unit      Amount",
                ]
                for it in lines:
                    qty = rng.randint(2, 24)
                    amount = money(qty * it["cost"])
                    total += amount
                    text.append(f"{qty:>3}  {it['name'][:36]:<36}  {it['cost']:>7.2f}  {amount:>9.2f}")
                total = money(total)
                text += ["", f"TOTAL DUE: {total:.2f}", "", "This invoice is sample data for the KloudC RetailOps demo."]
                kind = "return" if rng.random() < 0.06 else "invoice"
                pdf = text_pdf(text, title=f"Sample invoice - {vendor}")
                token = hex_id(rng, 32)
                filename = f"{token}.pdf"
                (folder / filename).write_bytes(pdf)
                who = submitters[n % len(submitters)]
                created = self.stamp(day, rng.randint(10, 17), rng.randint(0, 59))
                closed = day < last_day - dt.timedelta(days=10) or rng.random() < 0.3
                self.db.add(
                    InvoiceSubmission(
                        vendor=vendor,
                        submission_type=kind,
                        transfer_direction=None,
                        total_amount=total,
                        notes=None if rng.random() < 0.8 else "Two cases arrived damaged - credit requested.",
                        submitted_by_user_id=who.id,
                        submitted_by_name=who.username,
                        original_filename=f"invoice-{day.isoformat()}.pdf",
                        stored_filename=filename,
                        content_type="application/pdf",
                        file_hash=hashlib.sha256(pdf).hexdigest(),
                        file_token=hex_id(rng, 32),
                        pdf_filename=filename,
                        whatsapp_sent=False,
                        whatsapp_error=DEMO_NOT_SENT,
                        whatsapp_ambiguous=False,
                        toast_email_sent=False,
                        toast_email_error=DEMO_NOT_SENT,
                        toast_closed_at=created + dt.timedelta(days=2) if closed else None,
                        toast_closed_by_name="store_manager" if closed else None,
                        created_at=created,
                    )
                )
                n += 1
        self.db.commit()
        self.counts["invoices"] = n

    # --- checklists ----------------------------------------------------------------------------

    def ensure_pack_rows(self, pack: dict) -> None:
        """The pack's checklists and WhatsApp roles, when ``app.store init``
        (MT8) did not already write them."""
        from app.models import ChecklistDefinition, ChecklistDefinitionVersion, WhatsAppGroup
        from app.services import checklist_engine as engine

        created = self.stamp(self.start - dt.timedelta(days=30))
        have = {r.key for r in self.db.query(WhatsAppGroup).all()}
        for role in pack.get("whatsapp_roles", []):
            if role["key"] not in have:
                self.db.add(
                    WhatsAppGroup(
                        key=role["key"], label=role["label"], chat_id=None, builtin=True, active=True,
                        created_at=created, created_by="demo generator",
                    )
                )
        have_types = {r.checklist_type for r in self.db.query(ChecklistDefinition).all()}
        for c in pack.get("checklists", []):
            if c["checklist_type"] in have_types:
                continue
            schema = engine.normalize_schema(c["schema"])
            problems = engine.validate_schema(schema)
            if problems:
                raise GenerateError(f"demo.json checklist {c['checklist_type']}: {problems}")
            row = ChecklistDefinition(
                checklist_type=c["checklist_type"],
                title=c["title"],
                short_name=c.get("short_name"),
                description=c.get("description"),
                active=True,
                sort_order=c.get("sort_order", 0),
                settings_json=json.dumps(engine.normalize_settings(c.get("settings") or {})),
                current_version=1,
                created_at=created,
                created_by="demo generator",
                updated_at=created,
                updated_by="demo generator",
            )
            self.db.add(row)
            self.db.flush()
            self.db.add(
                ChecklistDefinitionVersion(
                    definition_id=row.id, version=1, schema_json=json.dumps(schema), note="DEMO pack", created_at=created
                )
            )
        self.db.commit()

    def make_checklist_submissions(self, pack: dict) -> None:
        from app.models import ChecklistSubmission
        from app.services import checklist_engine as engine
        from app.services.checklist_submission import hash_answers

        rng = self.rng("checklists")
        n = 0
        openers = [self.users["assistant_manager"], self.users["grocery_lead"]]
        closers = [self.users["store_manager"], self.users["cashier_one"], self.users["deli_lead"]]
        for c in pack.get("checklists", []):
            schema = engine.normalize_schema(c["schema"])
            is_closing = bool((c.get("settings") or {}).get("closing"))
            for day in self.dates[-45:]:
                if rng.random() < 0.06:
                    continue  # a day nobody filled it in
                answers: Dict[str, str] = {}
                for section in schema["sections"]:
                    for item in section["items"]:
                        if item["type"] in ("multiple_choice", "dropdown"):
                            options = [o["value"] for o in item["options"]]
                            answers[item["id"]] = options[0] if rng.random() < 0.9 else rng.choice(options[1:])
                        elif item["type"] in ("paragraph", "short_text") and rng.random() < 0.08:
                            answers[item["id"]] = rng.choice(
                                ["Back door sticks a little.", "Customer asked for larger rice bags.",
                                 "Price tag missing on the oat drink."]
                            )
                engine.validate_answers(schema, answers)
                all_clear, summary = engine.compute_status(schema, answers)
                who = (closers if is_closing else openers)[day.toordinal() % 2]
                self.db.add(
                    ChecklistSubmission(
                        checklist_type=c["checklist_type"],
                        submitted_by_user_id=who.id,
                        submitted_by_name=who.username,
                        answers_json=json.dumps(answers),
                        all_clear=all_clear,
                        issue_summary=summary,
                        answers_hash=hash_answers(c["checklist_type"], who.id, answers),
                        whatsapp_sent=False,
                        whatsapp_error=None,
                        whatsapp_ambiguous=False,
                        created_at=self.stamp(day, 21 if is_closing else 7, rng.randint(0, 45)),
                        definition_version=1,
                    )
                )
                n += 1
        self.db.commit()
        self.counts["checklist_submissions"] = n

    # --- team: tasks, chat, announcements -----------------------------------------------------

    def make_team(self) -> None:
        from app.team.chat_models import (
            Announcement,
            AnnouncementAudience,
            AnnouncementRecipient,
            Channel,
            ChannelMember,
            Message,
        )
        from app.team.chat_service import ensure_team_channels
        from app.team.models import Team, TeamMember
        from app.team.tasks_models import Task, TaskEvent, TaskTemplate
        from scripts.demo import demo_catalog as vocab

        rng = self.rng("team")
        teams = {t.name: t for t in self.db.query(Team).all()}
        last_day = self.dates[-1]
        manager = self.users["store_manager"]
        staff = [u for name, u in self.users.items() if name != vocab.REVIEW_USERNAME]

        # Tasks: the last 14 days, mostly done, some still open (a few overdue).
        n_tasks = 0
        for day in self.dates[-14:]:
            for title, team_name in rng.sample(vocab.TASK_TITLES, k=4):
                team = teams.get(team_name)
                due = self.stamp(day, rng.choice([10, 12, 15, 18]))
                creator = manager if rng.random() < 0.6 else self.users["assistant_manager"]
                is_recent = day >= last_day - dt.timedelta(days=1)
                status = "open" if is_recent and rng.random() < 0.7 else ("done" if rng.random() < 0.9 else "cant_do")
                members = [
                    m.user_id for m in self.db.query(TeamMember).filter(TeamMember.team_id == team.id).all()
                ] if team else []
                doer = next((u for u in staff if u.id in members), manager)
                task = Task(
                    title=title,
                    description=None,
                    requires="none",
                    priority="urgent" if rng.random() < 0.1 else "normal",
                    assignee_team_id=team.id if team else None,
                    assignee_user_id=None if team else manager.id,
                    due_at=due,
                    status=status,
                    completed_by_user_id=doer.id if status != "open" else None,
                    completed_by_name=doer.username if status != "open" else None,
                    completed_at=due - dt.timedelta(minutes=rng.randint(5, 90)) if status != "open" else None,
                    cant_do_reason="Ran out of supplies - will finish tomorrow." if status == "cant_do" else None,
                    created_by_user_id=creator.id,
                    created_by_name=creator.username,
                    created_at=due - dt.timedelta(hours=6),
                    updated_at=due,
                )
                self.db.add(task)
                self.db.flush()
                self.db.add(
                    TaskEvent(task_id=task.id, user_id=creator.id, user_name=creator.username, kind="created",
                              created_at=task.created_at)
                )
                if status != "open":
                    self.db.add(
                        TaskEvent(task_id=task.id, user_id=doer.id, user_name=doer.username, kind=status,
                                  created_at=task.completed_at)
                    )
                n_tasks += 1
        for title, team_name, rule in (
            ("Check cooler temperatures", "Kitchen", "FREQ=DAILY;BYHOUR=8;BYMINUTE=0"),
            ("Face aisles 1-4", "Grocery", "FREQ=DAILY;BYHOUR=20;BYMINUTE=0"),
            ("Review open invoices", "Managers", "FREQ=WEEKLY;BYDAY=FR;BYHOUR=11;BYMINUTE=0"),
        ):
            team = teams.get(team_name)
            if team is None:
                continue
            self.db.add(
                TaskTemplate(
                    title=title, description=None, requires="none", priority="normal", assignee_team_id=team.id,
                    rrule=rule, timezone=self.timezone, generate_ahead_minutes=420, active=True,
                    created_by_user_id=manager.id, created_by_name=manager.username,
                    created_at=self.stamp(self.start), updated_at=self.stamp(self.start),
                )
            )
        self.db.commit()
        self.counts["tasks"] = n_tasks

        # Chat: team channels with a few messages a day over the last 3 weeks.
        ensure_team_channels(self.db)
        channels = {c.team_id: c for c in self.db.query(Channel).filter(Channel.kind == "team").all()}
        n_msgs = 0
        last_ids: Dict[int, int] = {}
        for day in self.dates[-21:]:
            for team_name, lines in vocab.CHAT_LINES.items():
                team = teams.get(team_name)
                if team is None or team.id not in channels or rng.random() < 0.35:
                    continue
                if team.is_everyone:
                    senders = staff
                else:
                    ids = {m.user_id for m in self.db.query(TeamMember).filter(TeamMember.team_id == team.id).all()}
                    senders = [u for u in staff if u.id in ids] or [manager]
                for k in range(rng.randint(1, 3)):
                    sender = rng.choice(senders)
                    msg = Message(
                        channel_id=channels[team.id].id,
                        sender_user_id=sender.id,
                        sender_name=sender.username,
                        body=rng.choice(lines),
                        client_uuid=f"demo-{hex_id(rng, 24)}",
                        created_at=self.stamp(day, rng.randint(9, 20), rng.randint(0, 59)),
                    )
                    self.db.add(msg)
                    self.db.flush()
                    last_ids[channels[team.id].id] = msg.id
                    n_msgs += 1
        # Staff have read everything; the reviewer sees a few unread.
        for user in staff:
            for channel_id, last_id in last_ids.items():
                self.db.add(ChannelMember(channel_id=channel_id, user_id=user.id, last_read_message_id=last_id))
        self.db.commit()
        self.counts["messages"] = n_msgs

        everyone = next((t for t in teams.values() if t.is_everyone), None)
        for i, (title, body) in enumerate(vocab.ANNOUNCEMENTS):
            when = self.stamp(last_day - dt.timedelta(days=12 - 5 * i), 9)
            ann = Announcement(
                title=title, body=body, require_ack=(i == 2), pinned=(i == 0),
                created_by_user_id=manager.id, created_by_name=manager.username, created_at=when,
            )
            self.db.add(ann)
            self.db.flush()
            if everyone is not None:
                self.db.add(AnnouncementAudience(announcement_id=ann.id, team_id=everyone.id))
            for user in self.users.values():
                self.db.add(
                    AnnouncementRecipient(
                        announcement_id=ann.id, user_id=user.id, user_name=user.username,
                        seen_at=when + dt.timedelta(hours=2) if user.username != vocab.REVIEW_USERNAME else None,
                        acked_at=when + dt.timedelta(hours=3) if (i == 2 and user.username != vocab.REVIEW_USERNAME) else None,
                    )
                )
        self.db.commit()
        self.counts["announcements"] = len(vocab.ANNOUNCEMENTS)

    # --- marker ----------------------------------------------------------------------------

    def write_marker(self) -> dict:
        from app.setting_models import SettingChange

        info = {
            "generator_version": GENERATOR_VERSION,
            "store_code": "DEMO",
            "seed": self.seed,
            "days": self.days,
            "items": self.n_items,
            "first_date": self.start.isoformat(),
            "last_date": self.dates[-1].isoformat(),
            "end_date": self.end.isoformat(),
            "timezone": self.timezone,
            "counts": dict(sorted(self.counts.items())),
        }
        self.db.add(
            SettingChange(
                key=DATASET_KEY,
                value=json.dumps(info, sort_keys=True),
                previous_value=None,
                previous_source=None,
                changed_by_user_id=None,
                changed_by_name="demo generator",
                changed_at=self.stamp(self.end, 3, 30),
                note="Made-up DEMO store data (scripts/demo/generate_demo_data.py)",
            )
        )
        self.db.commit()
        return info


# --------------------------------------------------------------------------------------------
# Store init (MT8's command when present, else the same rows here)
# --------------------------------------------------------------------------------------------


def store_init(env: Dict[str, str]) -> str:
    """Prepare the fresh database as the DEMO store. Returns how."""
    if (BACKEND / "app" / "store" / "__main__.py").exists():
        _run([sys.executable, "-m", "app.store", "init", "--code", "DEMO", "--pack", str(PACK_PATH)], env)
        return "app.store init"
    return "built-in"


def clear_migration_seeds(db) -> None:
    """A brand-new database only holds the rows old migrations seeded; the
    ones made for one particular store go (contract 3.5 step 3). On a fresh
    file that is every row of these tables, so they are emptied. The generic
    teams of migration 0026 stay."""
    from sqlalchemy import delete

    from app.models import CategoryOverride, ChecklistDefinition, ChecklistDefinitionVersion, WhatsAppGroup

    for model in (ChecklistDefinitionVersion, ChecklistDefinition, CategoryOverride, WhatsAppGroup):
        db.execute(delete(model))
    db.commit()


def review_password(args) -> str:
    password = os.environ.get("DEMO_REVIEW_PASSWORD") or ""
    if not password and args.review_password_file:
        path = pathlib.Path(args.review_password_file)
        if path.exists():
            password = path.read_text().strip()
        else:
            password = secrets.token_urlsafe(12)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(password + "\n")
            os.chmod(path, 0o600)
    if len(password) < MIN_PASSWORD_LENGTH:
        raise GenerateError(
            f"Set DEMO_REVIEW_PASSWORD (at least {MIN_PASSWORD_LENGTH} characters) or pass "
            "--review-password-file (created with a random password when missing)."
        )
    return password


def generate(args) -> dict:
    out = pathlib.Path(args.out).resolve()
    if out.exists() and any(out.iterdir()):
        raise GenerateError(f"{out} is not empty - the generator only writes into a new folder.")
    password = review_password(args)
    timezone = args.timezone
    zoneinfo.ZoneInfo(timezone)  # fail early on a bad zone
    if args.end_date:
        end = dt.date.fromisoformat(args.end_date)
    else:
        end = dt.datetime.now(zoneinfo.ZoneInfo(timezone)).date()
    out.mkdir(parents=True, exist_ok=True)
    for sub in ("invoices", "team", "flyers"):
        (out / "uploads" / sub).mkdir(parents=True, exist_ok=True)

    env = demo_environment(out, timezone)
    _run([sys.executable, "-m", "alembic", "upgrade", "head"], env)
    os.environ.update(env)  # before `app` is imported
    sys.path.insert(0, str(BACKEND))

    from app.db import SessionLocal

    pack = json.loads(PACK_PATH.read_text())
    db = SessionLocal()
    try:
        clear_migration_seeds(db)
    finally:
        db.close()
    how = store_init(env)
    db = SessionLocal()
    try:
        gen = DemoGenerator(db, out, args.seed, args.days, args.items, end, timezone)
        gen.ensure_pack_rows(pack)
        gen.make_users(password)
        gen.make_sales()
        gen.make_stock_and_uploads()
        gen.make_vendors_and_orders()
        gen.make_invoices()
        gen.make_checklist_submissions(pack)
        gen.make_team()
        info = gen.write_marker()
    finally:
        db.close()
    # One self-contained file: fold the WAL back in before the folder is moved.
    from app.db import engine

    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    engine.dispose()
    info["store_init"] = how
    if getattr(args, "pg_url", ""):
        info["postgres"] = copy_to_postgres(out / "retailops.db", args.pg_url)
    (out / "demo_dataset.json").write_text(json.dumps(info, indent=2, sort_keys=True) + "\n")
    return info


def copy_to_postgres(sqlite_file: pathlib.Path, pg_url: str) -> dict:
    """The generated SQLite database -> an empty PostgreSQL database
    (dialect branch, platform track A). Fails unless the copy is exact."""
    sys.path.insert(0, str(BACKEND / "scripts" / "pg"))
    import copy_sqlite_to_pg
    from sqlalchemy.engine import make_url

    problems = copy_sqlite_to_pg.copy(str(sqlite_file), pg_url, log=lambda *a: None)
    if problems:
        raise GenerateError("PostgreSQL copy differs: " + "; ".join(problems[:10]))
    return {"database": make_url(pg_url).database, "differences": 0}


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", required=True, help="new folder to build into (must not exist or be empty)")
    p.add_argument("--seed", type=int, default=2026)
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--items", type=int, default=400)
    p.add_argument("--end-date", default="", help="data runs through the day before this (default: today, store time)")
    p.add_argument(
        "--timezone",
        default=os.environ.get("STORE_TIMEZONE") or "America/New_York",
        help="store time zone (default STORE_TIMEZONE or America/New_York)",
    )
    p.add_argument("--review-password-file", default="", help="file holding the review login password")
    p.add_argument("--pg-url", default="", help="also copy the result into this EMPTY PostgreSQL database")
    args = p.parse_args(argv)
    if args.days < 7 or args.days > 400:
        p.error("--days must be 7-400")
    if args.items < 20 or args.items > 2000:
        p.error("--items must be 20-2000")
    return args


def main(argv=None) -> int:
    sys.path.insert(0, str(BACKEND))
    try:
        info = generate(parse_args(argv))
    except GenerateError as exc:
        print(f"generate_demo_data: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(info, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
