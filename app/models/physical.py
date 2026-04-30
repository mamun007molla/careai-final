"""Module 1 — Physical Monitoring models.

Every record carries audit fields (decision #5):
  - created_by_user_id : who created this record
  - created_by_role    : their role at creation time (denormalized for fast queries)
  - created_by_name    : their name at creation (denormalized for display)

This means a doctor adding a record on behalf of a patient is fully traceable.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Enum, Float, ForeignKey, Integer, String, Text,
)

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


# ─── Mixin: shared audit columns ──────────────────────────────────────────────
def _audit_columns():
    return (
        Column("created_by_user_id", String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
        Column("created_by_role",    String(20), nullable=True),
        Column("created_by_name",    String(255), nullable=True),
    )


# ─── Activity Log (manual logging, by anyone linked to the patient) ──────────
class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id         = Column(String(36), primary_key=True, default=_uuid)
    # patient this log is for (always the elderly user)
    user_id    = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    type       = Column(String(100), nullable=False)            # "walking" | "eating" | "sleeping" | "exercise" | custom
    duration   = Column(Integer, nullable=True)                 # minutes
    notes      = Column(Text, nullable=True)
    logged_at  = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # audit
    created_by_user_id, created_by_role, created_by_name = _audit_columns()


# ─── Routine schedule ─────────────────────────────────────────────────────────
class Routine(Base):
    __tablename__ = "routines"

    id           = Column(String(36), primary_key=True, default=_uuid)
    user_id      = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title        = Column(String(255), nullable=False)
    type         = Column(String(50), nullable=False)           # "medication" | "meal" | "exercise"
    scheduled_at = Column(String(10), nullable=False)           # "HH:MM" 24h
    days         = Column(Text, nullable=False)                 # JSON array as string
    is_active    = Column(Boolean, default=True, nullable=False)
    notes        = Column(Text, nullable=True)
    created_at   = Column(DateTime, default=datetime.utcnow, nullable=False)

    created_by_user_id, created_by_role, created_by_name = _audit_columns()


# ─── Medication verify history ────────────────────────────────────────────────
class MedicationVerifyLog(Base):
    __tablename__ = "medication_verify_logs"

    id                    = Column(String(36), primary_key=True, default=_uuid)
    user_id               = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    prescribed_medication = Column(String(255), nullable=False)
    detected_medication   = Column(Text, nullable=True)
    matched               = Column(Boolean, default=False, nullable=False)
    confidence            = Column(Float, nullable=True)
    ai_provider           = Column(String(20), nullable=True)   # "ollama" | "groq"
    ai_fallback_used      = Column(Boolean, default=False)
    ai_fallback_reason    = Column(String(500), nullable=True)
    image_file_id         = Column(String(36), ForeignKey("files.id", ondelete="SET NULL"), nullable=True)
    raw_response          = Column(Text, nullable=True)
    verified_at           = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    created_by_user_id, created_by_role, created_by_name = _audit_columns()


# ─── Fall detection log (video stored in DB) ──────────────────────────────────
class FallDetectionLog(Base):
    __tablename__ = "fall_detection_logs"

    id                  = Column(String(36), primary_key=True, default=_uuid)
    user_id             = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    fall_detected       = Column(Boolean, default=False, nullable=False)
    confidence          = Column(Float, nullable=True)
    mode                = Column(String(50), nullable=True)     # "multimodal" | "vision-only"
    has_audio           = Column(Boolean, default=False)
    segments_json       = Column(Text, nullable=True)           # detection log per window
    input_video_file_id  = Column(String(36), ForeignKey("files.id", ondelete="SET NULL"), nullable=True)
    output_video_file_id = Column(String(36), ForeignKey("files.id", ondelete="SET NULL"), nullable=True)
    alert_sent          = Column(Boolean, default=False)
    detected_at         = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    created_by_user_id, created_by_role, created_by_name = _audit_columns()
