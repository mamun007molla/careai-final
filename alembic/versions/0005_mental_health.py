"""mental health tables — mood, chat, recommendations

Revision ID: 0005_mental_health
Revises: 0004_medication_intake
Create Date: 2026-04-28
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM


revision: str = "0005_mental_health"
down_revision: Union[str, None] = "0004_medication_intake"
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
    _create_enum_if_not_exists("chat_persona", ["FRIENDLY_COMPANION", "MENTAL_HEALTH_COACH"])
    _create_enum_if_not_exists("message_role", ["USER", "ASSISTANT", "SYSTEM"])
    _create_enum_if_not_exists("recommendation_status", ["ACTIVE", "DISMISSED", "SAVED"])

    chat_persona_col = ENUM(
        "FRIENDLY_COMPANION", "MENTAL_HEALTH_COACH",
        name="chat_persona", create_type=False,
    )
    message_role_col = ENUM(
        "USER", "ASSISTANT", "SYSTEM",
        name="message_role", create_type=False,
    )
    rec_status_col = ENUM(
        "ACTIVE", "DISMISSED", "SAVED",
        name="recommendation_status", create_type=False,
    )

    # ── mood_logs ────────────────────────────────────────────────────────────
    op.create_table(
        "mood_logs",
        sa.Column("id",                  sa.String(36), primary_key=True),
        sa.Column("user_id",             sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mood",                sa.Integer, nullable=False),
        sa.Column("sleep",               sa.Integer, nullable=False),
        sa.Column("energy",              sa.Integer, nullable=False),
        sa.Column("anxiety",             sa.Integer, nullable=False),
        sa.Column("note",                sa.Text),
        sa.Column("sentiment_label",     sa.String(20)),
        sa.Column("sentiment_score",     sa.Float),
        sa.Column("ai_provider",         sa.String(20)),
        sa.Column("ai_fallback_used",    sa.Boolean, server_default=sa.false()),
        sa.Column("logged_at",           sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("created_at",          sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("created_by_user_id",  sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_by_role",     sa.String(20)),
        sa.Column("created_by_name",     sa.String(255)),
    )
    op.create_index("ix_mood_user",   "mood_logs", ["user_id"])
    op.create_index("ix_mood_logged", "mood_logs", ["logged_at"])

    # ── chat_sessions ────────────────────────────────────────────────────────
    op.create_table(
        "chat_sessions",
        sa.Column("id",              sa.String(36), primary_key=True),
        sa.Column("user_id",         sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("persona",         chat_persona_col, nullable=False, server_default="FRIENDLY_COMPANION"),
        sa.Column("title",           sa.String(255)),
        sa.Column("created_at",      sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("last_message_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_chat_user",    "chat_sessions", ["user_id"])
    op.create_index("ix_chat_created", "chat_sessions", ["created_at"])

    # ── chat_messages ────────────────────────────────────────────────────────
    op.create_table(
        "chat_messages",
        sa.Column("id",               sa.String(36), primary_key=True),
        sa.Column("session_id",       sa.String(36), sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role",             message_role_col, nullable=False),
        sa.Column("content",          sa.Text, nullable=False),
        sa.Column("ai_provider",      sa.String(20)),
        sa.Column("ai_fallback_used", sa.Boolean, server_default=sa.false()),
        sa.Column("created_at",       sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_msg_session", "chat_messages", ["session_id"])

    # ── wellness_recommendations ─────────────────────────────────────────────
    op.create_table(
        "wellness_recommendations",
        sa.Column("id",               sa.String(36), primary_key=True),
        sa.Column("user_id",          sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title",            sa.String(255), nullable=False),
        sa.Column("body",             sa.Text, nullable=False),
        sa.Column("category",         sa.String(50), nullable=False),
        sa.Column("rationale",        sa.Text),
        sa.Column("status",           rec_status_col, nullable=False, server_default="ACTIVE"),
        sa.Column("ai_provider",      sa.String(20)),
        sa.Column("ai_fallback_used", sa.Boolean, server_default=sa.false()),
        sa.Column("generated_at",     sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("dismissed_at",     sa.DateTime),
        sa.Column("saved_at",         sa.DateTime),
    )
    op.create_index("ix_rec_user",      "wellness_recommendations", ["user_id"])
    op.create_index("ix_rec_status",    "wellness_recommendations", ["status"])
    op.create_index("ix_rec_generated", "wellness_recommendations", ["generated_at"])


def downgrade() -> None:
    op.drop_table("wellness_recommendations")
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
    op.drop_table("mood_logs")
    op.execute("DROP TYPE IF EXISTS recommendation_status")
    op.execute("DROP TYPE IF EXISTS message_role")
    op.execute("DROP TYPE IF EXISTS chat_persona")
