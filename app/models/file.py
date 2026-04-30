"""Files-in-database model (decision #3).

Single `files` table stores binary content via BYTEA. Other tables reference
files by `file_id`. The /files/{id} endpoint streams content with the right
mime type and supports HTTP range requests for video playback.

Trade-off: bloats the DB. Cheap for a class project / demo. For production,
swap the storage layer behind `STORAGE_BACKEND` (see ai_router.py pattern).
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, LargeBinary, String

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


# Purpose tells us what feature uploaded the file — useful for cleanup, quotas,
# and access-control rules later.
FILE_PURPOSES = (
    "MEDICATION_VERIFY",     # M1 — meds image
    "FALL_DETECTION_INPUT",  # M1 — input video
    "FALL_DETECTION_OUTPUT", # M1 — annotated output video
    "ACTIVITY_IMAGE",        # M1 — posture image
    "PRESCRIPTION",          # M2 — prescription doc
    "HEALTH_RECORD",         # M2 — medical record attachment
    "MEAL_IMAGE",            # M2 — food image
    "OTHER",
)


class File(Base):
    __tablename__ = "files"

    id          = Column(String(36), primary_key=True, default=_uuid)
    filename    = Column(String(255), nullable=False)
    mime_type   = Column(String(100), nullable=False, default="application/octet-stream")
    size_bytes  = Column(Integer, nullable=False)
    content     = Column(LargeBinary, nullable=False)  # PostgreSQL BYTEA
    purpose     = Column(Enum(*FILE_PURPOSES, name="file_purpose"), nullable=False, default="OTHER")
    owner_id    = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    uploaded_by = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow, nullable=False)
