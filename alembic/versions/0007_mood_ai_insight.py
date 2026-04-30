"""mood ai insight + suggestion columns

Revision ID: 0007_mood_ai_insight
Revises: 0006_doctor_family
Create Date: 2026-04-29
"""
from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "0007_mood_ai_insight"
down_revision: Union[str, None] = "0006_doctor_family"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mood_logs", sa.Column("ai_insight",    sa.Text, nullable=True))
    op.add_column("mood_logs", sa.Column("ai_suggestion", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("mood_logs", "ai_suggestion")
    op.drop_column("mood_logs", "ai_insight")
