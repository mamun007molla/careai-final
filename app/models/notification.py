"""Notification model.

A notification is delivered to ONE user. It can come from:
- Reminder trigger (medication, meal, exercise routine matched current time)
- Doctor adding a visit/prescription on a patient
- Family adding a meal/activity for a patient
- Fall detection alert
- New connection request

Each row tracks read state. Background scheduler creates them; the bell icon
in the frontend polls for unread count.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, String, Text

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


# Categories — drives icon + click target on the frontend
NOTIFICATION_TYPES = (
    "MEDICATION_REMINDER",   # routine.type = "medication" matched
    "MEAL_REMINDER",         # routine.type = "meal" matched
    "EXERCISE_REMINDER",     # routine.type = "exercise" matched
    "GENERIC_REMINDER",      # any other routine type matched
    "VISIT_ADDED",           # doctor created a visit
    "PRESCRIPTION_ADDED",    # attachment uploaded with kind=PRESCRIPTION
    "FALL_DETECTED",         # M1 fall detection result
    "CONNECTION_ADDED",      # new caregiver/doctor connected to patient
    "INFO",
)


class Notification(Base):
    __tablename__ = "notifications"

    id          = Column(String(36), primary_key=True, default=_uuid)
    # The user who SHOULD see this notification (the "to")
    user_id     = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    type        = Column(Enum(*NOTIFICATION_TYPES, name="notification_type"),
                         nullable=False, default="INFO")
    title       = Column(String(255), nullable=False)
    body        = Column(Text, nullable=True)
    # Optional deep link — frontend routes here on click (e.g. /health/visits/abc)
    link        = Column(String(500), nullable=True)
    is_read     = Column(Boolean, default=False, nullable=False, index=True)
    # When the notification was sent via email — null if email not enabled
    emailed_at  = Column(DateTime, nullable=True)
    created_at  = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Optional source — who triggered it (e.g. the doctor who added a visit).
    # Null for system events like reminder triggers.
    source_user_id   = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    source_user_name = Column(String(255), nullable=True)
    source_user_role = Column(String(20), nullable=True)
