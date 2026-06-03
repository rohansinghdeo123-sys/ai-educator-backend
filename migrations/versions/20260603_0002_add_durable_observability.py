"""add durable observability tables

Revision ID: 20260603_0002
Revises: 20260603_0001
Create Date: 2026-06-03
"""

from alembic import op
import sqlalchemy as sa

revision = "20260603_0002"
down_revision = "20260603_0001"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if not _has_table("observability_events"):
        op.create_table(
            "observability_events",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("event_version", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("agent_id", sa.String(), nullable=True),
            sa.Column("event_type", sa.String(), nullable=True),
            sa.Column("severity", sa.String(), nullable=True),
            sa.Column("session_id", sa.String(), nullable=True),
            sa.Column("source", sa.String(), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
            sa.Column("data_json", sa.JSON(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        for column in ("event_version", "created_at", "agent_id", "event_type", "severity", "session_id"):
            op.create_index(f"ix_observability_events_{column}", "observability_events", [column])

    if not _has_table("model_tool_traces"):
        op.create_table(
            "model_tool_traces",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("user_id", sa.String(), nullable=True),
            sa.Column("session_id", sa.String(), nullable=True),
            sa.Column("turn_id", sa.String(), nullable=True),
            sa.Column("trace_type", sa.String(), nullable=True),
            sa.Column("name", sa.String(), nullable=True),
            sa.Column("provider", sa.String(), nullable=True),
            sa.Column("model", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("estimated_input_tokens", sa.Integer(), nullable=True),
            sa.Column("estimated_output_tokens", sa.Integer(), nullable=True),
            sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        for column in ("created_at", "user_id", "session_id", "turn_id", "trace_type", "name", "status"):
            op.create_index(f"ix_model_tool_traces_{column}", "model_tool_traces", [column])


def downgrade() -> None:
    if _has_table("model_tool_traces"):
        op.drop_table("model_tool_traces")
    if _has_table("observability_events"):
        op.drop_table("observability_events")
