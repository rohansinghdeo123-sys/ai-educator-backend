"""add durable agent runtime tables

Revision ID: 20260606_0003
Revises: 20260603_0002
Create Date: 2026-06-06
"""

from alembic import op
import sqlalchemy as sa

revision = "20260606_0003"
down_revision = "20260603_0002"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _create_indexes(table_name: str, columns: tuple[str, ...]) -> None:
    for column in columns:
        op.create_index(f"ix_{table_name}_{column}", table_name, [column])


def upgrade() -> None:
    if not _has_table("agent_runtime_runs"):
        op.create_table(
            "agent_runtime_runs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=True),
            sa.Column("turn_id", sa.String(), nullable=True),
            sa.Column("user_id", sa.String(), nullable=True),
            sa.Column("session_id", sa.String(), nullable=True),
            sa.Column("workflow_name", sa.String(), nullable=True),
            sa.Column("lead_agent", sa.String(), nullable=True),
            sa.Column("mode", sa.String(), nullable=True),
            sa.Column("intent", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("confidence_score", sa.Float(), nullable=True),
            sa.Column("grounding_status", sa.String(), nullable=True),
            sa.Column("final_answer_excerpt", sa.Text(), nullable=True),
            sa.Column("state_json", sa.JSON(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("run_id"),
        )
        _create_indexes(
            "agent_runtime_runs",
            (
                "run_id",
                "turn_id",
                "user_id",
                "session_id",
                "workflow_name",
                "lead_agent",
                "intent",
                "status",
                "started_at",
                "grounding_status",
            ),
        )

    if not _has_table("agent_runtime_steps"):
        op.create_table(
            "agent_runtime_steps",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=True),
            sa.Column("step_name", sa.String(), nullable=True),
            sa.Column("agent_name", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=True),
            sa.Column("step_order", sa.Integer(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("input_json", sa.JSON(), nullable=True),
            sa.Column("output_json", sa.JSON(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        _create_indexes(
            "agent_runtime_steps",
            ("run_id", "step_name", "agent_name", "status", "started_at"),
        )

    if not _has_table("agent_runtime_messages"):
        op.create_table(
            "agent_runtime_messages",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("sender_agent", sa.String(), nullable=True),
            sa.Column("receiver_agent", sa.String(), nullable=True),
            sa.Column("message_type", sa.String(), nullable=True),
            sa.Column("task", sa.Text(), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("required_action", sa.Text(), nullable=True),
            sa.Column("evidence_json", sa.JSON(), nullable=True),
            sa.Column("result_json", sa.JSON(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        _create_indexes(
            "agent_runtime_messages",
            ("run_id", "created_at", "sender_agent", "receiver_agent", "message_type"),
        )

    if not _has_table("agent_runtime_tool_calls"):
        op.create_table(
            "agent_runtime_tool_calls",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=True),
            sa.Column("tool_name", sa.String(), nullable=True),
            sa.Column("agent_name", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("input_json", sa.JSON(), nullable=True),
            sa.Column("output_json", sa.JSON(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        _create_indexes(
            "agent_runtime_tool_calls",
            ("run_id", "tool_name", "agent_name", "status", "started_at"),
        )

    if not _has_table("agent_runtime_handoffs"):
        op.create_table(
            "agent_runtime_handoffs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("from_agent", sa.String(), nullable=True),
            sa.Column("to_agent", sa.String(), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("status", sa.String(), nullable=True),
            sa.Column("input_json", sa.JSON(), nullable=True),
            sa.Column("result_json", sa.JSON(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        _create_indexes(
            "agent_runtime_handoffs",
            ("run_id", "created_at", "from_agent", "to_agent", "status"),
        )


def downgrade() -> None:
    for table_name in (
        "agent_runtime_handoffs",
        "agent_runtime_tool_calls",
        "agent_runtime_messages",
        "agent_runtime_steps",
        "agent_runtime_runs",
    ):
        if _has_table(table_name):
            op.drop_table(table_name)
