"""open_order_cache.tab_names_json: the Toast check tab names (customer names)

Owner, Oct 4: "show the tab name on all the open orders because tab name has
the customer name". JSON list of each check's `tabName`, de-duplicated (see
app/services/open_orders.py::_tab_names). Nullable - rows cached before this
read as no tab names until the next scan (every 15 min) refills them.

Additive: one nullable column.

Revision ID: 0045
Revises: 0044
"""

from alembic import op
import sqlalchemy as sa

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("open_order_cache", sa.Column("tab_names_json", sa.Text, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("open_order_cache") as batch:
        batch.drop_column("tab_names_json")
