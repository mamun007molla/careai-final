"""Module 3 — Mental Health Support models.

Tables:
- mood_logs               : 4-slider daily check-in + optional note + AI sentiment
- chat_sessions           : a "conversation" with the AI companion
- chat_messages           : individual messages in a session
- wellness_recommendations: AI-generated weekly tips, patient can dismiss/save

Visibility rules (enforced in routers):
- mood_logs: patient + linked DOCTORS only — family blocked
- chat_sessions/messages: patient ONLY — completely private
- wellness_recommendations: patient + linked DOCTORS
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Enum, Float, ForeignKey, Integer, String, Text,
)

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


CHAT_PERSONAS = ("FRIENDLY_COMPANION", "MENTAL_HEALTH_COACH")
MESSAGE_ROLES = ("USER", "ASSISTANT", "SYSTEM")
RECOMMENDATION_STATUSES = ("ACTIVE", "DISMISSED", "SAVED")


# ─── Mood Log ────────────────────────────────────────────────────────────────
class MoodLog(Base):
    """One daily mental-health check-in.

    All four sliders are 1-5 (where 5 = best). The note is optional; if
    present, the AI router runs sentiment analysis and stores the result.
    """
    __tablename__ = "mood_logs"

    id            = Column(String(36), primary_key=True, default=_uuid)
    user_id       = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"),
                           nullable=False, index=True)

    # Sliders 1-5 (5 = best/most/highest)
    mood          = Column(Integer, nullable=False)   # sad → happy
    sleep         = Column(Integer, nullable=False)   # poor → great rest
    energy        = Column(Integer, nullable=False)   # exhausted → energetic
    anxiety       = Column(Integer, nullable=False)   # very anxious → calm
                                                      # NB: 5 here = LEAST anxious

    note          = Column(Text, nullable=True)

    # AI sentiment of the note (only filled if note is non-empty)
    sentiment_label  = Column(String(20), nullable=True)   # positive | negative | neutral
    sentiment_score  = Column(Float, nullable=True)        # 0.0 - 1.0
    ai_insight       = Column(Text, nullable=True)         # AI reflection
    ai_suggestion    = Column(Text, nullable=True)         # AI tip
    ai_provider      = Column(String(20), nullable=True)
    ai_fallback_used = Column(Boolean, default=False)

    logged_at     = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Audit (we don't expect family to log moods, but track who did anyway)
    created_by_user_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_by_role    = Column(String(20), nullable=True)
    created_by_name    = Column(String(255), nullable=True)


# ─── Chat Session ────────────────────────────────────────────────────────────
class ChatSession(Base):
    """A single conversation with the AI companion.

    Each session has ONE persona — chosen at start, can't switch mid-chat.
    Patients can have many sessions; usually they start a new one each day.
    """
    __tablename__ = "chat_sessions"

    id            = Column(String(36), primary_key=True, default=_uuid)
    user_id       = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    persona       = Column(Enum(*CHAT_PERSONAS, name="chat_persona"),
                           nullable=False, default="FRIENDLY_COMPANION")
    title         = Column(String(255), nullable=True)   # auto-generated from first message
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    last_message_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ─── Chat Message ────────────────────────────────────────────────────────────
class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id            = Column(String(36), primary_key=True, default=_uuid)
    session_id    = Column(String(36), ForeignKey("chat_sessions.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    role          = Column(Enum(*MESSAGE_ROLES, name="message_role"), nullable=False)
    content       = Column(Text, nullable=False)
    # AI provenance (only on ASSISTANT messages)
    ai_provider   = Column(String(20), nullable=True)
    ai_fallback_used = Column(Boolean, default=False)
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)


# ─── Wellness Recommendation ─────────────────────────────────────────────────
class WellnessRecommendation(Base):
    """AI-generated personalized wellness tips.

    Generated periodically based on mood trends + activity + medication
    compliance. Patient can dismiss or save (save = mark as helpful).
    """
    __tablename__ = "wellness_recommendations"

    id            = Column(String(36), primary_key=True, default=_uuid)
    user_id       = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"),
                           nullable=False, index=True)

    title         = Column(String(255), nullable=False)
    body          = Column(Text, nullable=False)
    category      = Column(String(50), nullable=False)   # sleep | exercise | nutrition | social | mindfulness | medical

    # Why was this generated? (e.g. "anxiety scores trending up over 7 days")
    rationale     = Column(Text, nullable=True)

    status        = Column(Enum(*RECOMMENDATION_STATUSES, name="recommendation_status"),
                           nullable=False, default="ACTIVE", index=True)

    ai_provider      = Column(String(20), nullable=True)
    ai_fallback_used = Column(Boolean, default=False)

    generated_at  = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    dismissed_at  = Column(DateTime, nullable=True)
    saved_at      = Column(DateTime, nullable=True)
