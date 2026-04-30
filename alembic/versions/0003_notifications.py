"""notifications table

Revision ID: 0003_notifications
Revises: 0002_health_management
Create Date: 2026-04-26
"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM


revision: str = "0003_notifications"
down_revision: Union[str, None] = "0002_health_management"
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
    _create_enum_if_not_exists("notification_type", [
        "MEDICATION_REMINDER", "MEAL_REMINDER", "EXERCISE_REMINDER",
        "GENERIC_REMINDER", "VISIT_ADDED", "PRESCRIPTION_ADDED",
        "FALL_DETECTED", "CONNECTION_ADDED", "INFO",
    ])

    notification_type_col = ENUM(
        "MEDICATION_REMINDER", "MEAL_REMINDER", "EXERCISE_REMINDER",
        "GENERIC_REMINDER", "VISIT_ADDED", "PRESCRIPTION_ADDED",
        "FALL_DETECTED", "CONNECTION_ADDED", "INFO",
        name="notification_type", create_type=False,
    )

    op.create_table(
        "notifications",
        sa.Column("id",                 sa.String(36), primary_key=True),
        sa.Column("user_id",            sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type",               notification_type_col, nullable=False, server_default="INFO"),
        sa.Column("title",              sa.String(255), nullable=False),
        sa.Column("body",               sa.Text),
        sa.Column("link",               sa.String(500)),
        sa.Column("is_read",            sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("emailed_at",         sa.DateTime),
        sa.Column("created_at",         sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("source_user_id",     sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("source_user_name",   sa.String(255)),
        sa.Column("source_user_role",   sa.String(20)),
    )
    op.create_index("ix_notifications_user",    "notifications", ["user_id"])
    op.create_index("ix_notifications_unread",  "notifications", ["is_read"])
    op.create_index("ix_notifications_created", "notifications", ["created_at"])


def downgrade() -> None:
    op.drop_table("notifications")
    op.execute("DROP TYPE IF EXISTS notification_type")
