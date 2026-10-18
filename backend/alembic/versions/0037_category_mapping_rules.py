"""category_overrides: seed the category mapping rules from the owner's
category merge plan (docs/CATEGORY_MERGE_PLAN_2026-09-30.md).

No schema change: a mapping rule is a `category_overrides` row with
match_type "category" (services/category_overrides.py). match_value is a JSON
array of TOAST's raw [group, category, subcategory] - subcategory null = any
subcategory, "" = no subcategory. The rule's own subcategory: a value, "" =
no subcategory, NULL = keep Toast's.

The owner already moved every current item in these combos with per-item
corrections (item rules win over mapping rules), so today nothing changes;
the rules catch items Toast files under the old names from now on. Like 0034
seeding the Shaz rule, the rows are inserted here. Downgrade removes only the
rows this migration added (created_by = SEEDED_BY).

Revision ID: 0037
Revises: 0036
"""

import datetime as dt
import json

from alembic import op
import sqlalchemy as sa

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None

SEEDED_BY = "category merge plan (migration 0037)"
NOTE = "Owner's category merge plan, Sep 30 (docs/CATEGORY_MERGE_PLAN_2026-09-30.md)"
ANY = None  # any subcategory
NONE = ""  # no subcategory (match) / no subcategory (target)
KEEP = None  # target: keep Toast's subcategory

# (Toast group, Toast category, Toast subcategory) -> (group, category, subcategory).
# Toast names spelled exactly as in the Sep 29 upload.
SEED_RULES = [
    (("RETAIL", "Snacks & Savories", "Biscuits & Cookies"), ("RETAIL", "Snacks & Savories", "Biscuits, Cookies & Rusk")),
    (("RETAIL", "Snacks & Savories", "Aha Snacks"), ("RETAIL", "Snacks & Savories", "Namkeen")),
    (("RETAIL", "Snacks & Savories", "GPR Snacks"), ("RETAIL", "Snacks & Savories", "Namkeen")),
    (("RETAIL", "Snacks & Savories", "Telugu Snacks"), ("RETAIL", "Snacks & Savories", "Namkeen")),
    (("RETAIL", "Produce", "Fruits"), ("PRODUCE", "Fruits", NONE)),
    (("RETAIL", "Produce", "Vegetables"), ("PRODUCE", "Vegetables", NONE)),
    (("RETAIL", "Produce", "Leafy Vegetables"), ("PRODUCE", "Leafy Vegetables", NONE)),
    (("RETAIL", "Produce", "Roots"), ("PRODUCE", "Roots", NONE)),
    (("RETAIL", "Produce", "Fresh Herbs"), ("PRODUCE", "Fresh Herbs", NONE)),
    (("PRODUCE", "Plants", ANY), ("RETAIL", "Plants & Seeds", NONE)),
    (("RETAIL", "Powders & Rituals", ANY), ("RETAIL", "Pooja Essentials", "Powders, Chandan & Sindoor")),
    (("RETAIL", "Festival & Cultural", "Prayer Kits"), ("RETAIL", "Pooja Essentials", "Prayer Kits")),
    (("RETAIL", "Health & Wellness", "Pain Relief Balms & Ointments"), ("RETAIL", "Health & Wellness", "Pain Relief")),
    (("RETAIL", "Health & Wellness", "Health Supplements"), ("RETAIL", "Health & Wellness", "Vitamins & Supplements")),
    (
        ("RETAIL", "Pantry Staples", "Cooking Essences & Flavors"),
        ("RETAIL", "Baking & Cooking Essentials", "Food Colors & Essences"),
    ),
    (
        ("RETAIL", "Baking & Cooking Essentials", "Vanilla"),
        ("RETAIL", "Baking & Cooking Essentials", "Food Colors & Essences"),
    ),
    # Old Toast category for pooja items (no subcategory / "Pooja Essentials" /
    # "Powders & Rituals"): the exact-subcategory rule wins over the any one.
    (("RETAIL", "Spiritual & Cultural Products", ANY), ("RETAIL", "Pooja Essentials", "Pooja Essentials")),
    (
        ("RETAIL", "Spiritual & Cultural Products", "Powders & Rituals"),
        ("RETAIL", "Pooja Essentials", "Powders, Chandan & Sindoor"),
    ),
]
# Not seeded: " Men's Grooming" (leading space) - the inventory upload trims
# every text field, so Toast's name is stored as "Men's Grooming" already, and
# mapping rules compare trimmed names anyway.


def _encode(match) -> str:
    # Same encoding as services/category_overrides.encode_category_match.
    return json.dumps(list(match), ensure_ascii=False)


def upgrade() -> None:
    table = sa.table(
        "category_overrides",
        sa.column("match_type", sa.String),
        sa.column("match_value", sa.String),
        sa.column("category_group", sa.String),
        sa.column("category", sa.String),
        sa.column("subcategory", sa.String),
        sa.column("note", sa.Text),
        sa.column("created_at", sa.DateTime),
        sa.column("active", sa.Boolean),
        sa.column("created_by", sa.String),
    )
    now = dt.datetime.utcnow().replace(microsecond=0)  # naive UTC, as UTCDateTime stores it
    op.bulk_insert(
        table,
        [
            {
                "match_type": "category",
                "match_value": _encode(match),
                "category_group": target[0],
                "category": target[1],
                "subcategory": target[2],
                "note": NOTE,
                "created_at": now,
                "active": True,
                "created_by": SEEDED_BY,
            }
            for match, target in SEED_RULES
        ],
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("DELETE FROM category_overrides WHERE match_type = 'category' AND created_by = :by"),
        {"by": SEEDED_BY},
    )
