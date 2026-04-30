"""Module 4 — Doctor Support models.

Both features are DOCTOR-only — only doctors can request classifications
or summaries (enforced in router with require_roles).

Tables:
- disease_classifications: AI image-based diagnosis suggestions
- report_summaries:        AI text summarization of medical reports
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, String, Text,
)

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


# ─── Disease Classification ──────────────────────────────────────────────────
class DiseaseClassification(Base):
    """Result of AI image-based disease classification.

    The doctor uploads an image (skin lesion, x-ray, etc.), AI returns
    candidate conditions ranked by confidence. The doctor reviews and
    optionally adds their own diagnosis.
    """
    __tablename__ = "disease_classifications"

    id              = Column(String(36), primary_key=True, default=_uuid)

    # Patient the image is for (optional — doctor may classify ad-hoc)
    patient_id      = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"),
                             nullable=True, index=True)
    image_file_id   = Column(String(36), ForeignKey("files.id", ondelete="CASCADE"),
                             nullable=False)

    image_category  = Column(String(50), nullable=False)   # "skin" | "xray" | "general"
    clinical_notes  = Column(Text, nullable=True)          # what the doctor describes

    # AI output — top conditions ranked, JSON array of {name, confidence, description}
    ai_predictions      = Column(Text, nullable=True)      # JSON
    ai_summary          = Column(Text, nullable=True)
    ai_recommendations  = Column(Text, nullable=True)
    ai_provider         = Column(String(20), nullable=True)
    ai_fallback_used    = Column(Boolean, default=False)

    # Doctor's own assessment (saved if they confirm or override)
    doctor_diagnosis    = Column(Text, nullable=True)

    classified_at   = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Audit (always doctor)
    created_by_user_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_by_role    = Column(String(20), nullable=True)
    created_by_name    = Column(String(255), nullable=True)


# ─── Report Summary ──────────────────────────────────────────────────────────
class ReportSummary(Base):
    """AI summarization of a medical report — text or attached file.

    Inputs: free-form text from the doctor, OR reference to a file (PDF
    extracted text). Output: structured summary + key findings + flags.
    """
    __tablename__ = "report_summaries"

    id              = Column(String(36), primary_key=True, default=_uuid)
    patient_id      = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"),
                             nullable=True, index=True)
    source_file_id  = Column(String(36), ForeignKey("files.id", ondelete="SET NULL"),
                             nullable=True)

    title           = Column(String(255), nullable=False)
    raw_text        = Column(Text, nullable=False)         # what was sent for analysis

    # AI output (JSON)
    summary_text    = Column(Text, nullable=True)
    key_findings    = Column(Text, nullable=True)          # JSON array of strings
    abnormal_values = Column(Text, nullable=True)          # JSON array of {name, value, ref_range, flag}
    recommendations = Column(Text, nullable=True)          # JSON array
    ai_provider     = Column(String(20), nullable=True)
    ai_fallback_used = Column(Boolean, default=False)

    summarized_at   = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    created_by_user_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_by_role    = Column(String(20), nullable=True)
    created_by_name    = Column(String(255), nullable=True)
