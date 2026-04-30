"""m4 doctor support + m5 family/emergency tables

Revision ID: 0006_doctor_family
Revises: 0005_mental_health
Create Date: 2026-04-28
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM


revision: str = "0006_doctor_family"
down_revision: Union[str, None] = "0005_mental_health"
branch_labels = None
depends_on = None


def _create_enum_if_not_exists(name: str, values: list[str]) -> None:
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
    _create_enum_if_not_exists("sos_status",
        ["ACTIVE", "ACKNOWLEDGED", "RESOLVED", "FALSE_ALARM"])
    _create_enum_if_not_exists("digest_status",
        ["PENDING", "SENT", "FAILED"])

    sos_status_col = ENUM(
        "ACTIVE", "ACKNOWLEDGED", "RESOLVED", "FALSE_ALARM",
        name="sos_status", create_type=False,
    )
    digest_status_col = ENUM(
        "PENDING", "SENT", "FAILED",
        name="digest_status", create_type=False,
    )

    # ── M4: disease_classifications ──────────────────────────────────────────
    op.create_table(
        "disease_classifications",
        sa.Column("id",                  sa.String(36), primary_key=True),
        sa.Column("patient_id",          sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("image_file_id",       sa.String(36), sa.ForeignKey("files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("image_category",      sa.String(50), nullable=False),
        sa.Column("clinical_notes",      sa.Text),
        sa.Column("ai_predictions",      sa.Text),
        sa.Column("ai_summary",          sa.Text),
        sa.Column("ai_recommendations",  sa.Text),
        sa.Column("ai_provider",         sa.String(20)),
        sa.Column("ai_fallback_used",    sa.Boolean, server_default=sa.false()),
        sa.Column("doctor_diagnosis",    sa.Text),
        sa.Column("classified_at",       sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("created_by_user_id",  sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_by_role",     sa.String(20)),
        sa.Column("created_by_name",     sa.String(255)),
    )
    op.create_index("ix_dc_patient",  "disease_classifications", ["patient_id"])
    op.create_index("ix_dc_at",       "disease_classifications", ["classified_at"])

    # ── M4: report_summaries ─────────────────────────────────────────────────
    op.create_table(
        "report_summaries",
        sa.Column("id",                  sa.String(36), primary_key=True),
        sa.Column("patient_id",          sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("source_file_id",      sa.String(36), sa.ForeignKey("files.id", ondelete="SET NULL")),
        sa.Column("title",               sa.String(255), nullable=False),
        sa.Column("raw_text",            sa.Text, nullable=False),
        sa.Column("summary_text",        sa.Text),
        sa.Column("key_findings",        sa.Text),
        sa.Column("abnormal_values",     sa.Text),
        sa.Column("recommendations",     sa.Text),
        sa.Column("ai_provider",         sa.String(20)),
        sa.Column("ai_fallback_used",    sa.Boolean, server_default=sa.false()),
        sa.Column("summarized_at",       sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("created_by_user_id",  sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_by_role",     sa.String(20)),
        sa.Column("created_by_name",     sa.String(255)),
    )
    op.create_index("ix_rs_patient",  "report_summaries", ["patient_id"])
    op.create_index("ix_rs_at",       "report_summaries", ["summarized_at"])

    # ── M5: family_digests ───────────────────────────────────────────────────
    op.create_table(
        "family_digests",
        sa.Column("id",                  sa.String(36), primary_key=True),
        sa.Column("patient_id",          sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("period_date",         sa.String(10), nullable=False),
        sa.Column("activities_count",    sa.Integer, server_default="0"),
        sa.Column("medications_taken",   sa.Integer, server_default="0"),
        sa.Column("medications_total",   sa.Integer, server_default="0"),
        sa.Column("avg_mood",            sa.Float),
        sa.Column("fall_alerts",         sa.Integer, server_default="0"),
        sa.Column("body_text",           sa.Text, nullable=False),
        sa.Column("body_html",           sa.Text),
        sa.Column("status",              digest_status_col, nullable=False, server_default="PENDING"),
        sa.Column("recipients_count",    sa.Integer, server_default="0"),
        sa.Column("sent_at",             sa.DateTime),
        sa.Column("error_msg",           sa.String(500)),
        sa.Column("created_at",          sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_digest_patient", "family_digests", ["patient_id"])
    op.create_index("ix_digest_status",  "family_digests", ["status"])
    op.create_index("ix_digest_created", "family_digests", ["created_at"])

    # ── M5: sos_alerts ───────────────────────────────────────────────────────
    op.create_table(
        "sos_alerts",
        sa.Column("id",                            sa.String(36), primary_key=True),
        sa.Column("patient_id",                    sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message",                       sa.String(500)),
        sa.Column("latitude",                      sa.Float),
        sa.Column("longitude",                     sa.Float),
        sa.Column("location_text",                 sa.String(500)),
        sa.Column("status",                        sos_status_col, nullable=False, server_default="ACTIVE"),
        sa.Column("triggered_at",                  sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("acknowledged_by_user_id",       sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("acknowledged_by_name",          sa.String(255)),
        sa.Column("acknowledged_at",               sa.DateTime),
        sa.Column("resolved_at",                   sa.DateTime),
        sa.Column("resolution_note",               sa.Text),
        sa.Column("notified_count",                sa.Integer, server_default="0"),
        sa.Column("sms_sent",                      sa.Boolean, server_default=sa.false()),
        sa.Column("sms_failed_reason",             sa.String(500)),
    )
    op.create_index("ix_sos_patient",   "sos_alerts", ["patient_id"])
    op.create_index("ix_sos_status",    "sos_alerts", ["status"])
    op.create_index("ix_sos_triggered", "sos_alerts", ["triggered_at"])

    # ── M5: caregiver_threads ────────────────────────────────────────────────
    op.create_table(
        "caregiver_threads",
        sa.Column("id",                  sa.String(36), primary_key=True),
        sa.Column("patient_id",          sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("title",               sa.String(255)),
        sa.Column("created_at",          sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("last_message_at",     sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_thread_last", "caregiver_threads", ["last_message_at"])

    # ── M5: caregiver_messages ───────────────────────────────────────────────
    op.create_table(
        "caregiver_messages",
        sa.Column("id",                  sa.String(36), primary_key=True),
        sa.Column("thread_id",           sa.String(36), sa.ForeignKey("caregiver_threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sender_id",           sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("sender_name",         sa.String(255), nullable=False),
        sa.Column("sender_role",         sa.String(20), nullable=False),
        sa.Column("content",             sa.Text, nullable=False),
        sa.Column("created_at",          sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_msg_thread",  "caregiver_messages", ["thread_id"])
    op.create_index("ix_msg_created", "caregiver_messages", ["created_at"])


def downgrade() -> None:
    op.drop_table("caregiver_messages")
    op.drop_table("caregiver_threads")
    op.drop_table("sos_alerts")
    op.drop_table("family_digests")
    op.drop_table("report_summaries")
    op.drop_table("disease_classifications")
    op.execute("DROP TYPE IF EXISTS digest_status")
    op.execute("DROP TYPE IF EXISTS sos_status")
