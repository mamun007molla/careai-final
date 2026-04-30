"""User and PatientLink models.

Roles:
- ELDERLY        — the patient
- FAMILY         — family member / caregiver (rename of "CAREGIVER" to match the requirements doc)
- DOCTOR         — physician

PatientLink supports MANY caregivers / doctors per patient (decision #4).
Composite uniqueness enforced via UniqueConstraint, not a single PK.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Enum, ForeignKey, Index, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


# Keep backward-compat synonyms in the enum so old data still loads
USER_ROLES = ("ELDERLY", "FAMILY", "DOCTOR")
LINK_ROLES = ("FAMILY", "DOCTOR")


class User(Base):
    __tablename__ = "users"

    id            = Column(String(36), primary_key=True, default=_uuid)
    name          = Column(String(255), nullable=False)
    email         = Column(String(255), unique=True, nullable=False, index=True)
    phone         = Column(String(50), nullable=True)
    password_hash = Column(String(255), nullable=False)
    role          = Column(Enum(*USER_ROLES, name="user_role"), nullable=False, default="ELDERLY")
    is_active     = Column(Boolean, default=True, nullable=False)
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Doctor-only profile fields (NULL for other roles)
    specialty     = Column(String(255), nullable=True)
    license_no    = Column(String(100), nullable=True)


class PatientLink(Base):
    """Connects a patient (ELDERLY) to a caregiver (FAMILY) or doctor (DOCTOR).

    A patient can have many family members AND many doctors simultaneously.
    `is_primary` lets the patient designate one primary doctor for SOS routing.
    """
    __tablename__ = "patient_links"

    id           = Column(String(36), primary_key=True, default=_uuid)
    patient_id   = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    linked_id    = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role         = Column(Enum(*LINK_ROLES, name="link_role"), nullable=False)
    relation     = Column(String(100), nullable=True)         # "son", "spouse", "GP", etc.
    is_primary   = Column(Boolean, default=False, nullable=False)
    notes        = Column(Text, nullable=True)
    created_at   = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("patient_id", "linked_id", name="uq_patient_linked"),
        Index("ix_links_patient", "patient_id"),
        Index("ix_links_linked", "linked_id"),
    )
