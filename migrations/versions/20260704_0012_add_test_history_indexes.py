"""add test_history date indexes for rival rankings and weekly stats

Revision ID: 20260704_0012
Revises: 20260704_0011
Create Date: 2026-07-04
"""

from alembic import op
import sqlalchemy as sa


revision = "20260704_0012"
down_revision = "20260704_0011"
branch_labels = None
depends_on = None


def _existing_indexes(table: str) -> set:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {index["name"] for index in inspector.get_indexes(table)}


def upgrade() -> None:
    existing = _existing_indexes("test_history")
    if "ix_test_history_date" not in existing:
        op.create_index("ix_test_history_date", "test_history", ["date"])
    if "ix_test_history_user_date" not in existing:
        op.create_index("ix_test_history_user_date", "test_history", ["user_id", "date"])


def downgrade() -> None:
    existing = _existing_indexes("test_history")
    if "ix_test_history_user_date" in existing:
        op.drop_index("ix_test_history_user_date", "test_history")
    if "ix_test_history_date" in existing:
        op.drop_index("ix_test_history_date", "test_history")
