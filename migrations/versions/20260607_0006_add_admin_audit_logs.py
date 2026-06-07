"""add admin audit logs

Revision ID: 20260607_0006
Revises: 20260607_0005
Create Date: 2026-06-07
"""

from alembic import op
import sqlalchemy as sa


revision = "20260607_0006"
down_revision = "20260607_0005"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _create_index(name: str, table: str, columns: list[str], unique: bool = False) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {index["name"] for index in inspector.get_indexes(table)}
    if name not in existing:
        op.create_index(name, table, columns, unique=unique)


def upgrade() -> None:
    if not _has_table("admin_audit_logs"):
        op.create_table(
            "admin_audit_logs",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("actor_uid", sa.String(), nullable=True),
            sa.Column("actor_email", sa.String(), nullable=True),
            sa.Column("action", sa.String(), nullable=True),
            sa.Column("target_type", sa.String(), nullable=True),
            sa.Column("target_id", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=True),
            sa.Column("ip_address", sa.String(), nullable=True),
            sa.Column("user_agent", sa.Text(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
        )
        _create_index("ix_admin_audit_logs_created_at", "admin_audit_logs", ["created_at"])
        _create_index("ix_admin_audit_logs_actor_uid", "admin_audit_logs", ["actor_uid"])
        _create_index("ix_admin_audit_logs_actor_email", "admin_audit_logs", ["actor_email"])
        _create_index("ix_admin_audit_logs_action", "admin_audit_logs", ["action"])
        _create_index("ix_admin_audit_logs_target_type", "admin_audit_logs", ["target_type"])
        _create_index("ix_admin_audit_logs_target_id", "admin_audit_logs", ["target_id"])
        _create_index("ix_admin_audit_logs_status", "admin_audit_logs", ["status"])


def downgrade() -> None:
    if _has_table("admin_audit_logs"):
        op.drop_table("admin_audit_logs")
