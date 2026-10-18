"""Item vendors as app data (multi-store M1, MT5; plan section 4.9).

As built: docs/MULTISTORE_M1_CONTRACT.md, "As built - MT5".

Additive only: a new `item_vendors` table (one row per item per vendor, rank
1 = listed first), filled here from each item's newest CSV snapshot row -
the same lists the Toast Retail CSV's ";"-joined `supplier` field holds
today. Nothing existing is changed; older code never reads the table.

The fill below is a frozen copy of services/item_vendors.ranked_vendors /
refresh_from_csv (a test checks the two give the same rows), so this
migration keeps doing the same thing if that module changes later.

Revision ID: 0061
Revises: 0060
"""

import datetime as dt

from alembic import op
import sqlalchemy as sa

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def _split(raw, keep_empty=False):
    if not raw:
        return []
    parts = [s.strip() for s in str(raw).split(";")]
    return parts if keep_empty else [s for s in parts if s]


def _aligned(supplier, codes_raw):
    vendors = _split(supplier, keep_empty=True)
    codes = _split(codes_raw, keep_empty=True)
    while len(codes) > len(vendors) and codes and codes[-1] == "":
        codes.pop()
    if len(codes) != len(vendors):
        return None
    return [c or None for c in codes]


def _ranked(supplier, codes_raw):
    """[(rank, name, code)]: one per vendor (first position and spelling,
    repeats matched ignoring case), empty slots skipped, junk kept."""
    slots = _split(supplier, keep_empty=True)
    codes = _aligned(supplier, codes_raw)
    named = [v for v in slots if v]
    real_codes = _split(codes_raw)
    out = []
    index_by_key = {}
    for i, name in enumerate(slots):
        if not name:
            continue
        code = None
        if codes is not None:
            code = codes[i]
        elif len(named) == 1 and len(real_codes) == 1:
            code = real_codes[0]
        seen = index_by_key.get(name.lower())
        if seen is not None:
            if out[seen][2] is None and code:
                out[seen] = (out[seen][0], out[seen][1], code)
            continue
        index_by_key[name.lower()] = len(out)
        out.append((len(out) + 1, name, code))
    return out


def upgrade() -> None:
    op.create_table(
        "item_vendors",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("item_id", sa.String(), nullable=False),
        sa.Column("vendor_name", sa.String(), nullable=False),
        sa.Column("vendor_item_code", sa.String(), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("source_date", sa.Date(), nullable=True),
        # UTCDateTime in the model (app/db.py) is a DateTime underneath.
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("item_id", "vendor_name", name="uq_item_vendors_item_vendor"),
    )
    op.create_index("ix_item_vendors_item_id", "item_vendors", ["item_id"])
    op.create_index("ix_item_vendors_vendor_name", "item_vendors", ["vendor_name"])

    # Backfill: each item's newest CSV (non-API) snapshot row - the row the
    # Toast API snapshots carry their supplier fields from.
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            """
            SELECT s.item_id, s.snapshot_date, s.supplier, s.supplier_item_id
            FROM inventory_snapshots s
            JOIN (
                SELECT item_id, MAX(snapshot_date) AS d
                FROM inventory_snapshots
                WHERE source_filename IS NULL OR source_filename NOT LIKE 'api:%'
                GROUP BY item_id
            ) n ON s.item_id = n.item_id AND s.snapshot_date = n.d
            ORDER BY s.item_id
            """
        )
    ).fetchall()
    table = sa.table(
        "item_vendors",
        sa.column("item_id", sa.String),
        sa.column("vendor_name", sa.String),
        sa.column("vendor_item_code", sa.String),
        sa.column("rank", sa.Integer),
        sa.column("source", sa.String),
        sa.column("source_date", sa.Date),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    # Naive UTC, as UTCDateTime stores it.
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)
    batch = []
    for item_id, day, supplier, codes in rows:
        if isinstance(day, str):
            day = dt.date.fromisoformat(day[:10])
        for rank, name, code in _ranked(supplier, codes):
            batch.append(
                {
                    "item_id": item_id,
                    "vendor_name": name,
                    "vendor_item_code": code,
                    "rank": rank,
                    "source": "toast_csv",
                    "source_date": day,
                    "created_at": now,
                    "updated_at": now,
                }
            )
        if len(batch) >= 2000:
            op.bulk_insert(table, batch)
            batch = []
    if batch:
        op.bulk_insert(table, batch)


def downgrade() -> None:
    op.drop_index("ix_item_vendors_vendor_name", table_name="item_vendors")
    op.drop_index("ix_item_vendors_item_id", table_name="item_vendors")
    op.drop_table("item_vendors")
