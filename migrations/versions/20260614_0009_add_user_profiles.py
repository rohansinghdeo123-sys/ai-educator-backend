"""add canonical user profiles

Revision ID: 20260614_0009
Revises: 20260612_0008
Create Date: 2026-06-14
"""

from alembic import op
import sqlalchemy as sa


revision = "20260614_0009"
down_revision = "20260612_0008"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table("user_profiles"):
        return

    op.create_table(
        "user_profiles",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False, server_default=""),
        sa.Column("display_name", sa.String(), nullable=False, server_default=""),
        sa.Column("class_level", sa.String(), nullable=False, server_default=""),
        sa.Column("onboarding_completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index("ix_user_profiles_email", "user_profiles", ["email"])
    op.create_index("ix_user_profiles_class_level", "user_profiles", ["class_level"])
    op.create_index(
        "ix_user_profiles_onboarding_completed",
        "user_profiles",
        ["onboarding_completed"],
    )


def downgrade() -> None:
    if _has_table("user_profiles"):
        op.drop_table("user_profiles")
