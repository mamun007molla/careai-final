"""initial schema — users, links, files, physical (M1)

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-26

IDEMPOTENT enums:
    Uses sqlalchemy.dialects.postgresql.ENUM with create_type=False so the
    column references won't try to auto-create the type. Enums are created
    explicitly via raw SQL with `CREATE TYPE IF NOT EXISTS` (Postgres 9.x+
    needs DO-block; Postgres 13+ accepts IF NOT EXISTS for many types but
    NOT for CREATE TYPE — so we use the DO-block approach for safety).
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM


revision: str = "0001_initial"
down_revision: Union[str, None] = None
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
    # ── Create enums idempotently with raw SQL ───────────────────────────────
    _create_enum_if_not_exists("user_role",    ["ELDERLY", "FAMILY", "DOCTOR"])
    _create_enum_if_not_exists("link_role",    ["FAMILY", "DOCTOR"])
    _create_enum_if_not_exists("file_purpose", [
        "MEDICATION_VERIFY", "FALL_DETECTION_INPUT", "FALL_DETECTION_OUTPUT",
        "ACTIVITY_IMAGE", "PRESCRIPTION", "HEALTH_RECORD", "MEAL_IMAGE", "OTHER",
    ])

    # ── Reference enums in columns with create_type=False ───────────────────
    # IMPORTANT: postgresql.ENUM (not sa.Enum) supports create_type=False
    user_role_col = ENUM(
        "ELDERLY", "FAMILY", "DOCTOR",
        name="user_role", create_type=False,
    )
    link_role_col = ENUM(
        "FAMILY", "DOCTOR",
        name="link_role", create_type=False,
    )
    file_purpose_col = ENUM(
        "MEDICATION_VERIFY", "FALL_DETECTION_INPUT", "FALL_DETECTION_OUTPUT",
        "ACTIVITY_IMAGE", "PRESCRIPTION", "HEALTH_RECORD", "MEAL_IMAGE", "OTHER",
        name="file_purpose", create_type=False,
    )

    # ── users ────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id",            sa.String(36), primary_key=True),
        sa.Column("name",          sa.String(255), nullable=False),
        sa.Column("email",         sa.String(255), nullable=False, unique=True),
        sa.Column("phone",         sa.String(50)),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role",          user_role_col, nullable=False, server_default="ELDERLY"),
        sa.Column("is_active",     sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at",    sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("specialty",     sa.String(255)),
        sa.Column("license_no",    sa.String(100)),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # ── patient_links ────────────────────────────────────────────────────────
    op.create_table(
        "patient_links",
        sa.Column("id",          sa.String(36), primary_key=True),
        sa.Column("patient_id",  sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("linked_id",   sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role",        link_role_col, nullable=False),
        sa.Column("relation",    sa.String(100)),
        sa.Column("is_primary",  sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("notes",       sa.Text),
        sa.Column("created_at",  sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("patient_id", "linked_id", name="uq_patient_linked"),
    )
    op.create_index("ix_links_patient", "patient_links", ["patient_id"])
    op.create_index("ix_links_linked",  "patient_links", ["linked_id"])

    # ── files ────────────────────────────────────────────────────────────────
    op.create_table(
        "files",
        sa.Column("id",          sa.String(36), primary_key=True),
        sa.Column("filename",    sa.String(255), nullable=False),
        sa.Column("mime_type",   sa.String(100), nullable=False, server_default="application/octet-stream"),
        sa.Column("size_bytes",  sa.Integer, nullable=False),
        sa.Column("content",     sa.LargeBinary, nullable=False),
        sa.Column("purpose",     file_purpose_col, nullable=False, server_default="OTHER"),
        sa.Column("owner_id",    sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("uploaded_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("uploaded_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_files_owner", "files", ["owner_id"])

    # ── activity_logs ────────────────────────────────────────────────────────
    op.create_table(
        "activity_logs",
        sa.Column("id",                 sa.String(36), primary_key=True),
        sa.Column("user_id",            sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type",               sa.String(100), nullable=False),
        sa.Column("duration",           sa.Integer),
        sa.Column("notes",              sa.Text),
        sa.Column("logged_at",          sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("created_at",         sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_by_role",    sa.String(20)),
        sa.Column("created_by_name",    sa.String(255)),
    )
    op.create_index("ix_activity_user",   "activity_logs", ["user_id"])
    op.create_index("ix_activity_logged", "activity_logs", ["logged_at"])
    op.create_index("ix_activity_audit",  "activity_logs", ["created_by_user_id"])

    # ── routines ─────────────────────────────────────────────────────────────
    op.create_table(
        "routines",
        sa.Column("id",                 sa.String(36), primary_key=True),
        sa.Column("user_id",            sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title",              sa.String(255), nullable=False),
        sa.Column("type",               sa.String(50), nullable=False),
        sa.Column("scheduled_at",       sa.String(10), nullable=False),
        sa.Column("days",               sa.Text, nullable=False),
        sa.Column("is_active",          sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("notes",              sa.Text),
        sa.Column("created_at",         sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_by_role",    sa.String(20)),
        sa.Column("created_by_name",    sa.String(255)),
    )
    op.create_index("ix_routines_user",  "routines", ["user_id"])
    op.create_index("ix_routines_audit", "routines", ["created_by_user_id"])

    # ── medication_verify_logs ──────────────────────────────────────────────
    op.create_table(
        "medication_verify_logs",
        sa.Column("id",                    sa.String(36), primary_key=True),
        sa.Column("user_id",               sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("prescribed_medication", sa.String(255), nullable=False),
        sa.Column("detected_medication",   sa.Text),
        sa.Column("matched",               sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("confidence",            sa.Float),
        sa.Column("ai_provider",           sa.String(20)),
        sa.Column("ai_fallback_used",      sa.Boolean, server_default=sa.false()),
        sa.Column("ai_fallback_reason",    sa.String(500)),
        sa.Column("image_file_id",         sa.String(36), sa.ForeignKey("files.id", ondelete="SET NULL")),
        sa.Column("raw_response",          sa.Text),
        sa.Column("verified_at",           sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("created_by_user_id",    sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_by_role",       sa.String(20)),
        sa.Column("created_by_name",       sa.String(255)),
    )
    op.create_index("ix_medverify_user",  "medication_verify_logs", ["user_id"])
    op.create_index("ix_medverify_at",    "medication_verify_logs", ["verified_at"])
    op.create_index("ix_medverify_audit", "medication_verify_logs", ["created_by_user_id"])

    # ── fall_detection_logs ──────────────────────────────────────────────────
    op.create_table(
        "fall_detection_logs",
        sa.Column("id",                    sa.String(36), primary_key=True),
        sa.Column("user_id",               sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fall_detected",         sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("confidence",            sa.Float),
        sa.Column("mode",                  sa.String(50)),
        sa.Column("has_audio",             sa.Boolean, server_default=sa.false()),
        sa.Column("segments_json",         sa.Text),
        sa.Column("input_video_file_id",   sa.String(36), sa.ForeignKey("files.id", ondelete="SET NULL")),
        sa.Column("output_video_file_id",  sa.String(36), sa.ForeignKey("files.id", ondelete="SET NULL")),
        sa.Column("alert_sent",            sa.Boolean, server_default=sa.false()),
        sa.Column("detected_at",           sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("created_by_user_id",    sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_by_role",       sa.String(20)),
        sa.Column("created_by_name",       sa.String(255)),
    )
    op.create_index("ix_fall_user",  "fall_detection_logs", ["user_id"])
    op.create_index("ix_fall_at",    "fall_detection_logs", ["detected_at"])
    op.create_index("ix_fall_audit", "fall_detection_logs", ["created_by_user_id"])


def downgrade() -> None:
    op.drop_table("fall_detection_logs")
    op.drop_table("medication_verify_logs")
    op.drop_table("routines")
    op.drop_table("activity_logs")
    op.drop_table("files")
    op.drop_table("patient_links")
    op.drop_table("users")
    op.execute("DROP TYPE IF EXISTS file_purpose")
    op.execute("DROP TYPE IF EXISTS link_role")
    op.execute("DROP TYPE IF EXISTS user_role")
