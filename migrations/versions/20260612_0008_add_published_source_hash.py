"""add published_source_hash to content_chapters

Revision ID: 20260612_0008
Revises: 20260608_0007
Create Date: 2026-06-12
"""

from alembic import op
import sqlalchemy as sa


revision = "20260612_0008"
down_revision = "20260608_0007"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _has_column("content_chapters", "published_source_hash"):
        op.add_column(
            "content_chapters",
            sa.Column("published_source_hash", sa.String(), nullable=True, server_default=""),
        )
        # Existing approved/published chapters were approved against their
        # current source; backfill so the first re-approval after a PDF swap
        # bumps the version correctly.
        op.execute(
            "UPDATE content_chapters SET published_source_hash = source_hash "
            "WHERE status IN ('approved', 'published')"
        )


def downgrade() -> None:
    if _has_column("content_chapters", "published_source_hash"):
        op.drop_column("content_chapters", "published_source_hash")
