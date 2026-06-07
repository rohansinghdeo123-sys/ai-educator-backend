"""add daily quota usage table

Revision ID: 20260607_0004
Revises: 20260606_0003
Create Date: 2026-06-07
"""

from alembic import op
import sqlalchemy as sa


revision = "20260607_0004"
down_revision = "20260606_0003"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if _has_table("daily_quota_usage"):
        return

    op.create_table(
        "daily_quota_usage",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("quota_key", sa.String(), nullable=False),
        sa.Column("user_hash", sa.String(), nullable=False),
        sa.Column("quota_name", sa.String(), nullable=False),
        sa.Column("quota_date", sa.Date(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_daily_quota_usage_quota_key", "daily_quota_usage", ["quota_key"], unique=True)
    op.create_index("ix_daily_quota_usage_user_hash", "daily_quota_usage", ["user_hash"])
    op.create_index("ix_daily_quota_usage_quota_name", "daily_quota_usage", ["quota_name"])
    op.create_index("ix_daily_quota_usage_quota_date", "daily_quota_usage", ["quota_date"])


def downgrade() -> None:
    if not _has_table("daily_quota_usage"):
        return

    op.drop_index("ix_daily_quota_usage_quota_date", table_name="daily_quota_usage")
    op.drop_index("ix_daily_quota_usage_quota_name", table_name="daily_quota_usage")
    op.drop_index("ix_daily_quota_usage_user_hash", table_name="daily_quota_usage")
    op.drop_index("ix_daily_quota_usage_quota_key", table_name="daily_quota_usage")
    op.drop_table("daily_quota_usage")
