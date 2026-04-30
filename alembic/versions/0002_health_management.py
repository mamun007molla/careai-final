"""module 2 — health management tables

Revision ID: 0002_health_management
Revises: 0001_initial
Create Date: 2026-04-26
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM


revision: str = "0002_health_management"
down_revision: Union[str, None] = "0001_initial"
branch_labels = None
depends_on = None


def _create_enum_if_not_exists(name: str, values: list[str]) -> None:
    """Create an enum type only if it doesn't already exist (idempotent)."""
    values_sql = ", ".join(f"'{v}'" for v in values)
    op.execute(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = '{name}') THEN
                CREATE TYPE {name} AS ENUM ({values_sql});
            END IF;
        END
        $$;
    """)


def upgrade() -> None:
    # ── Create enums idempotently ────────────────────────────────────────────
    _create_enum_if_not_exists("visit_type", [
        "CONSULTATION", "FOLLOWUP", "DIAGNOSTIC", "EMERGENCY", "OTHER",
    ])
    _create_enum_if_not_exists("attachment_kind", [
        "PRESCRIPTION", "LAB_REPORT", "IMAGING", "DISCHARGE_SUMMARY", "OTHER",
    ])

    # ── Reference enums via postgresql.ENUM with create_type=False ───────────
    visit_type_col = ENUM(
        "CONSULTATION", "FOLLOWUP", "DIAGNOSTIC", "EMERGENCY", "OTHER",
        name="visit_type", create_type=False,
    )
    attachment_kind_col = ENUM(
        "PRESCRIPTION", "LAB_REPORT", "IMAGING", "DISCHARGE_SUMMARY", "OTHER",
        name="attachment_kind", create_type=False,
    )

    # ── medical_visits ───────────────────────────────────────────────────────
    op.create_table(
        "medical_visits",
        sa.Column("id",                 sa.String(36), primary_key=True),
        sa.Column("patient_id",         sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("doctor_id",          sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("visit_type",         visit_type_col, nullable=False, server_default="CONSULTATION"),
        sa.Column("visit_date",         sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("title",              sa.String(255), nullable=False),
        sa.Column("diagnosis",          sa.Text),
        sa.Column("prescription_text",  sa.Text),
        sa.Column("notes",              sa.Text),
        sa.Column("follow_up_at",       sa.DateTime),
        sa.Column("created_at",         sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_by_role",    sa.String(20)),
        sa.Column("created_by_name",    sa.String(255)),
    )
    op.create_index("ix_visits_patient",  "medical_visits", ["patient_id"])
    op.create_index("ix_visits_doctor",   "medical_visits", ["doctor_id"])
    op.create_index("ix_visits_date",     "medical_visits", ["visit_date"])
    op.create_index("ix_visits_audit",    "medical_visits", ["created_by_user_id"])

    # ── visit_attachments ────────────────────────────────────────────────────
    op.create_table(
        "visit_attachments",
        sa.Column("id",                 sa.String(36), primary_key=True),
        sa.Column("visit_id",           sa.String(36), sa.ForeignKey("medical_visits.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_id",            sa.String(36), sa.ForeignKey("files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind",               attachment_kind_col, nullable=False, server_default="OTHER"),
        sa.Column("description",        sa.String(500)),
        sa.Column("uploaded_at",        sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_by_role",    sa.String(20)),
        sa.Column("created_by_name",    sa.String(255)),
    )
    op.create_index("ix_attach_visit", "visit_attachments", ["visit_id"])

    # ── meal_logs ────────────────────────────────────────────────────────────
    op.create_table(
        "meal_logs",
        sa.Column("id",                 sa.String(36), primary_key=True),
        sa.Column("user_id",            sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("meal_type",          sa.String(30), nullable=False),
        sa.Column("description",        sa.String(500), nullable=False),
        sa.Column("eaten_at",           sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("calories",           sa.Float),
        sa.Column("protein_g",          sa.Float),
        sa.Column("carbs_g",            sa.Float),
        sa.Column("fat_g",              sa.Float),
        sa.Column("ai_estimated",       sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("ai_provider",        sa.String(20)),
        sa.Column("ai_fallback_used",   sa.Boolean, server_default=sa.false()),
        sa.Column("image_file_id",      sa.String(36), sa.ForeignKey("files.id", ondelete="SET NULL")),
        sa.Column("notes",              sa.Text),
        sa.Column("created_at",         sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_by_role",    sa.String(20)),
        sa.Column("created_by_name",    sa.String(255)),
    )
    op.create_index("ix_meals_user",  "meal_logs", ["user_id"])
    op.create_index("ix_meals_eaten", "meal_logs", ["eaten_at"])


def downgrade() -> None:
    op.drop_table("meal_logs")
    op.drop_table("visit_attachments")
    op.drop_table("medical_visits")
    op.execute("DROP TYPE IF EXISTS attachment_kind")
    op.execute("DROP TYPE IF EXISTS visit_type")
