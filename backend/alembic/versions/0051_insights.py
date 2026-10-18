"""Insights from the Toast Analytics API (docs/INSIGHTS_CONTRACT.md).

analytics_cache: cached Analytics answers, one row per (report, params) -
per business date for the daily reports.
analytics_call_log: our own count of the daily-capped Analytics calls (check
and guest payment reports, 60 a day at Toast), so the budget survives a
restart.
guest_visits: returning customers - (payment date, HMAC of the card
fingerprint) only; no card data. Kept 400 days at most.
loss_watch_flags: flagged checks for Loss watch / Odd checks and the 09:00
alert, with who acknowledged them.

AUTOINCREMENT on every table (ids are in URLs and logs). Additive; the
downgrade drops all four tables.

Revision ID: 0051
Revises: 0050
"""

from alembic import op
import sqlalchemy as sa

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analytics_cache",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("report", sa.String(), nullable=False),
        sa.Column("params_hash", sa.String(), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("report", "params_hash", name="uq_analytics_cache_report_params"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_analytics_cache_report", "analytics_cache", ["report"])
    op.create_index("ix_analytics_cache_business_date", "analytics_cache", ["business_date"])

    op.create_table(
        "analytics_call_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("family", sa.String(), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=True),
        sa.Column("called_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ok", sa.Integer(), nullable=False, server_default="0"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_analytics_call_log_family", "analytics_call_log", ["family"])
    op.create_index("ix_analytics_call_log_called_at", "analytics_call_log", ["called_at"])

    op.create_table(
        "guest_visits",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("visit_date", sa.Date(), nullable=False),
        sa.Column("card_hash", sa.String(), nullable=False),
        sa.Column("payments", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("visit_date", "card_hash", name="uq_guest_visits_date_card"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_guest_visits_visit_date", "guest_visits", ["visit_date"])
    op.create_index("ix_guest_visits_card_hash", "guest_visits", ["card_hash"])

    op.create_table(
        "loss_watch_flags",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("flag_key", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("check_guid", sa.String(), nullable=True),
        sa.Column("check_number", sa.String(), nullable=True),
        sa.Column("employee", sa.String(), nullable=True),
        sa.Column("reasons", sa.String(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acked_by_user_id", sa.Integer(), nullable=True),
        sa.Column("acked_by_name", sa.String(), nullable=True),
        sa.UniqueConstraint("flag_key", name="uq_loss_watch_flags_key"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_loss_watch_flags_kind", "loss_watch_flags", ["kind"])
    op.create_index("ix_loss_watch_flags_business_date", "loss_watch_flags", ["business_date"])


def downgrade() -> None:
    op.drop_index("ix_loss_watch_flags_business_date", table_name="loss_watch_flags")
    op.drop_index("ix_loss_watch_flags_kind", table_name="loss_watch_flags")
    op.drop_table("loss_watch_flags")
    op.drop_index("ix_guest_visits_card_hash", table_name="guest_visits")
    op.drop_index("ix_guest_visits_visit_date", table_name="guest_visits")
    op.drop_table("guest_visits")
    op.drop_index("ix_analytics_call_log_called_at", table_name="analytics_call_log")
    op.drop_index("ix_analytics_call_log_family", table_name="analytics_call_log")
    op.drop_table("analytics_call_log")
    op.drop_index("ix_analytics_cache_business_date", table_name="analytics_cache")
    op.drop_index("ix_analytics_cache_report", table_name="analytics_cache")
    op.drop_table("analytics_cache")
