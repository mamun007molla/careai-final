"""Module 2 — Health Management models.

Tables:
- medical_visits      : a clinical encounter (date, doctor, diagnosis, notes)
- visit_attachments   : N files attached to a visit (prescription, lab report, image)
- meal_logs           : food log with optional AI nutrition estimate

All tables carry audit fields (created_by_*) like M1 does.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Enum, Float, ForeignKey, Integer, String, Text,
)

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _audit_columns():
    return (
        Column("created_by_user_id", String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
        Column("created_by_role",    String(20), nullable=True),
        Column("created_by_name",    String(255), nullable=True),
    )


# Visit type — what kind of clinical encounter this was
VISIT_TYPES = ("CONSULTATION", "FOLLOWUP", "DIAGNOSTIC", "EMERGENCY", "OTHER")

# Attachment kind — drives icon + grouping in UI
ATTACHMENT_KINDS = ("PRESCRIPTION", "LAB_REPORT", "IMAGING", "DISCHARGE_SUMMARY", "OTHER")


# ─── Medical Visit ────────────────────────────────────────────────────────────
class MedicalVisit(Base):
    """A clinical encounter — anchors prescriptions/reports/notes together.

    Convention:
    - patient_id  : the elderly user the visit is FOR
    - doctor_id   : the doctor who saw the patient (NULL if patient self-reports
                    the visit before linking, or if visit is logged from outside
                    the system)
    - created_by  : whoever entered the record into the system (audit columns).
                    Per access rules: only DOCTOR can create.
    """
    __tablename__ = "medical_visits"

    id            = Column(String(36), primary_key=True, default=_uuid)
    patient_id    = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    doctor_id     = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    visit_type    = Column(Enum(*VISIT_TYPES, name="visit_type"), nullable=False, default="CONSULTATION")
    visit_date    = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    title         = Column(String(255), nullable=False)
    diagnosis     = Column(Text, nullable=True)
    prescription_text = Column(Text, nullable=True)   # free-text rx (the typed kind)
    notes         = Column(Text, nullable=True)
    follow_up_at  = Column(DateTime, nullable=True)

    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by_user_id, created_by_role, created_by_name = _audit_columns()


# ─── Visit Attachment ─────────────────────────────────────────────────────────
class VisitAttachment(Base):
    """Glue table linking a File to a MedicalVisit with a typed `kind`."""
    __tablename__ = "visit_attachments"

    id          = Column(String(36), primary_key=True, default=_uuid)
    visit_id    = Column(String(36), ForeignKey("medical_visits.id", ondelete="CASCADE"), nullable=False, index=True)
    file_id     = Column(String(36), ForeignKey("files.id", ondelete="CASCADE"), nullable=False)
    kind        = Column(Enum(*ATTACHMENT_KINDS, name="attachment_kind"), nullable=False, default="OTHER")
    description = Column(String(500), nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    created_by_user_id, created_by_role, created_by_name = _audit_columns()


# ─── Meal Log ─────────────────────────────────────────────────────────────────
class MealLog(Base):
    """One meal entry with optional AI-estimated nutrition (user-editable)."""
    __tablename__ = "meal_logs"

    id            = Column(String(36), primary_key=True, default=_uuid)
    user_id       = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    meal_type     = Column(String(30), nullable=False)   # breakfast | lunch | dinner | snack
    description   = Column(String(500), nullable=False)  # "rice, daal, fish curry"
    eaten_at      = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Nutrition (nullable — fill manually or via AI)
    calories      = Column(Float, nullable=True)
    protein_g     = Column(Float, nullable=True)
    carbs_g       = Column(Float, nullable=True)
    fat_g         = Column(Float, nullable=True)

    # Provenance: did AI fill this in, or did the user?
    ai_estimated  = Column(Boolean, default=False, nullable=False)
    ai_provider   = Column(String(20), nullable=True)
    ai_fallback_used = Column(Boolean, default=False)
    image_file_id = Column(String(36), ForeignKey("files.id", ondelete="SET NULL"), nullable=True)
    notes         = Column(Text, nullable=True)

    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by_user_id, created_by_role, created_by_name = _audit_columns()


# ════════════════════════════════════════════════════════════════════════════
# Standalone prescriptions — uploaded by patient or family
# (separate from visit prescriptions — those are doctor-generated)
# ════════════════════════════════════════════════════════════════════════════
class Prescription(Base):
    """A prescription uploaded directly by the patient or family member.

    Use case: patient visits an external doctor not on the platform and
    wants to keep the prescription in their CareAI records.

    Distinct from VisitAttachment(kind=PRESCRIPTION), which is created by
    a CareAI doctor during a visit.
    """
    __tablename__ = "prescriptions"

    id              = Column(String(36), primary_key=True, default=_uuid)
    patient_id      = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"),
                             nullable=False, index=True)

    # Doctor info (free text since external doctor not in the system)
    doctor_name     = Column(String(150), nullable=True)
    doctor_specialty = Column(String(100), nullable=True)
    clinic_name     = Column(String(150), nullable=True)

    # Either text or attachment (or both)
    prescription_text = Column(Text, nullable=True)
    file_id         = Column(String(36), ForeignKey("files.id", ondelete="SET NULL"),
                             nullable=True)

    issued_at       = Column(DateTime, nullable=True)   # date on prescription
    notes           = Column(Text, nullable=True)

    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by_user_id, created_by_role, created_by_name = _audit_columns()
