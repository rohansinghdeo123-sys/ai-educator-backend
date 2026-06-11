"""normalize admin audit log columns

Revision ID: 20260608_0007
Revises: 20260607_0006
Create Date: 2026-06-08
"""

from alembic import op
import sqlalchemy as sa


revision = "20260608_0007"
down_revision = "20260607_0006"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return set(inspector.get_table_names())


def _column_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {column["name"] for column in inspector.get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _column_names(table_name):
        op.add_column(table_name, column)


def _create_index_if_missing(name: str, table_name: str, columns: list[str]) -> None:
    if name not in _index_names(table_name):
        op.create_index(name, table_name, columns)


def upgrade() -> None:
    if "admin_audit_logs" not in _table_names():
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
    else:
        _add_column_if_missing("admin_audit_logs", sa.Column("actor_uid", sa.String(), nullable=True))
        _add_column_if_missing("admin_audit_logs", sa.Column("actor_email", sa.String(), nullable=True))
        _add_column_if_missing("admin_audit_logs", sa.Column("ip_address", sa.String(), nullable=True))
        _add_column_if_missing("admin_audit_logs", sa.Column("user_agent", sa.Text(), nullable=True))

    columns = _column_names("admin_audit_logs")
    if "admin_uid" in columns and "actor_uid" in columns:
        op.execute("UPDATE admin_audit_logs SET actor_uid = COALESCE(actor_uid, admin_uid)")
    if "admin_email" in columns and "actor_email" in columns:
        op.execute("UPDATE admin_audit_logs SET actor_email = COALESCE(actor_email, admin_email)")

    _create_index_if_missing("ix_admin_audit_logs_created_at", "admin_audit_logs", ["created_at"])
    _create_index_if_missing("ix_admin_audit_logs_actor_uid", "admin_audit_logs", ["actor_uid"])
    _create_index_if_missing("ix_admin_audit_logs_actor_email", "admin_audit_logs", ["actor_email"])
    _create_index_if_missing("ix_admin_audit_logs_action", "admin_audit_logs", ["action"])
    _create_index_if_missing("ix_admin_audit_logs_target_type", "admin_audit_logs", ["target_type"])
    _create_index_if_missing("ix_admin_audit_logs_target_id", "admin_audit_logs", ["target_id"])
    _create_index_if_missing("ix_admin_audit_logs_status", "admin_audit_logs", ["status"])


def downgrade() -> None:
    # Keep normalized audit columns on downgrade; dropping audit data is too risky.
    pass
