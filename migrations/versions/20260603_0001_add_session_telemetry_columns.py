"""add session telemetry columns

Revision ID: 20260603_0001
Revises:
Create Date: 2026-06-03
"""

from alembic import op
import sqlalchemy as sa

revision = "20260603_0001"
down_revision = None
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if not _has_column(table_name, column.name):
        op.add_column(table_name, column)


def upgrade() -> None:
    _add_column_if_missing("test_history", sa.Column("started_at", sa.DateTime(), nullable=True))
    _add_column_if_missing("test_history", sa.Column("completed_at", sa.DateTime(), nullable=True))
    _add_column_if_missing("test_history", sa.Column("response_latency_ms", sa.Integer(), server_default="0", nullable=True))
    _add_column_if_missing("test_history", sa.Column("hint_count", sa.Integer(), server_default="0", nullable=True))
    _add_column_if_missing("test_history", sa.Column("retry_count", sa.Integer(), server_default="0", nullable=True))
    _add_column_if_missing("test_history", sa.Column("confidence_before", sa.Float(), nullable=True))
    _add_column_if_missing("test_history", sa.Column("confidence_after", sa.Float(), nullable=True))


def downgrade() -> None:
    for column_name in (
        "confidence_after",
        "confidence_before",
        "retry_count",
        "hint_count",
        "response_latency_ms",
        "completed_at",
        "started_at",
    ):
        if _has_column("test_history", column_name):
            op.drop_column("test_history", column_name)
