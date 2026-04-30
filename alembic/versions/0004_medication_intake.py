"""medication intake logs

Revision ID: 0004_medication_intake
Revises: 0003_notifications
Create Date: 2026-04-26
"""
from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "0004_medication_intake"
down_revision: Union[str, None] = "0003_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "medication_intake_logs",
        sa.Column("id",                 sa.String(36), primary_key=True),
        sa.Column("user_id",            sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("routine_id",         sa.String(36), sa.ForeignKey("routines.id", ondelete="SET NULL")),
        sa.Column("medication_name",    sa.String(255), nullable=False),
        sa.Column("scheduled_at",       sa.String(10), nullable=False),
        sa.Column("taken_at",           sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("on_time",            sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("notes",              sa.String(500)),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_by_role",    sa.String(20)),
        sa.Column("created_by_name",    sa.String(255)),
    )
    op.create_index("ix_intake_user",    "medication_intake_logs", ["user_id"])
    op.create_index("ix_intake_routine", "medication_intake_logs", ["routine_id"])
    op.create_index("ix_intake_taken",   "medication_intake_logs", ["taken_at"])


def downgrade() -> None:
    op.drop_table("medication_intake_logs")
