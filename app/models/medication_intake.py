"""Medication intake log — separate from MedicationVerifyLog.

VerifyLog = "I took photo of pill, AI verified it matches prescription"
IntakeLog = "I marked this scheduled medication as taken"

Both can exist for the same dose. The intake log enables streak tracking
and missed-dose detection on the Medication Reminder page.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class MedicationIntakeLog(Base):
    __tablename__ = "medication_intake_logs"

    id              = Column(String(36), primary_key=True, default=_uuid)
    user_id         = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    # Reference to the routine that scheduled this — null for ad-hoc taken events
    routine_id      = Column(String(36), ForeignKey("routines.id", ondelete="SET NULL"),
                             nullable=True, index=True)
    # Title at time of taking — denormalized so deleting the routine doesn't
    # break history
    medication_name = Column(String(255), nullable=False)
    # The scheduled HH:MM (e.g. "08:00") — same source as routine.scheduled_at.
    # For ad-hoc dose, this is current HH:MM.
    scheduled_at    = Column(String(10), nullable=False)
    taken_at        = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    # Was this on time, or marked retroactively?
    on_time         = Column(Boolean, default=True, nullable=False)
    notes           = Column(String(500), nullable=True)

    # Audit
    created_by_user_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_by_role    = Column(String(20), nullable=True)
    created_by_name    = Column(String(255), nullable=True)
