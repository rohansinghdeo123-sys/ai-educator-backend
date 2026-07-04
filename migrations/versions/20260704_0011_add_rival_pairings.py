"""add rival_pairings table for the weekly rival challenge

Revision ID: 20260704_0011
Revises: 20260620_0010
Create Date: 2026-07-04
"""

from alembic import op
import sqlalchemy as sa


revision = "20260704_0011"
down_revision = "20260620_0010"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table("rival_pairings"):
        return
    op.create_table(
        "rival_pairings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("rival_user_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), server_default="active", nullable=True),
        sa.Column("match_basis", sa.String(), server_default="monthly_exam", nullable=True),
        sa.Column("user_seed_score", sa.Float(), server_default="0", nullable=True),
        sa.Column("rival_seed_score", sa.Float(), server_default="0", nullable=True),
        sa.Column("user_seed_rank", sa.Integer(), nullable=True),
        sa.Column("rival_seed_rank", sa.Integer(), nullable=True),
        sa.Column("resolved", sa.Boolean(), server_default=sa.text("false"), nullable=True),
        sa.Column("outcome", sa.String(), server_default="", nullable=True),
        sa.Column("my_week_xp", sa.Integer(), server_default="0", nullable=True),
        sa.Column("rival_week_xp", sa.Integer(), server_default="0", nullable=True),
        sa.Column("reward_xp", sa.Integer(), server_default="0", nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("week_start", "user_id", name="uq_rival_week_user"),
    )
    op.create_index("ix_rival_pairings_week_start", "rival_pairings", ["week_start"])
    op.create_index("ix_rival_pairings_user_id", "rival_pairings", ["user_id"])
    op.create_index("ix_rival_pairings_rival_user_id", "rival_pairings", ["rival_user_id"])
    op.create_index("ix_rival_pairings_status", "rival_pairings", ["status"])
    op.create_index("ix_rival_pairings_resolved", "rival_pairings", ["resolved"])


def downgrade() -> None:
    if _has_table("rival_pairings"):
        op.drop_table("rival_pairings")
