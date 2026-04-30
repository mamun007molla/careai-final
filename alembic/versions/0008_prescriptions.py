"""standalone prescriptions table

Revision ID: 0008_prescriptions
Revises: 0007_mood_ai_insight
Create Date: 2026-04-29
"""
from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "0008_prescriptions"
down_revision: Union[str, None] = "0007_mood_ai_insight"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prescriptions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("patient_id", sa.String(36),
                  sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("doctor_name",      sa.String(150), nullable=True),
        sa.Column("doctor_specialty", sa.String(100), nullable=True),
        sa.Column("clinic_name",      sa.String(150), nullable=True),
        sa.Column("prescription_text", sa.Text, nullable=True),
        sa.Column("file_id", sa.String(36),
                  sa.ForeignKey("files.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("issued_at",  sa.DateTime, nullable=True),
        sa.Column("notes",      sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("created_by_user_id", sa.String(36), nullable=True),
        sa.Column("created_by_role",    sa.String(20), nullable=True),
        sa.Column("created_by_name",    sa.String(150), nullable=True),
    )
    op.create_index("ix_prescriptions_patient_id", "prescriptions", ["patient_id"])


def downgrade() -> None:
    op.drop_index("ix_prescriptions_patient_id", table_name="prescriptions")
    op.drop_table("prescriptions")
