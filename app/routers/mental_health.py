"""Module 3 — Mental Health Support.

Privacy enforcement:
- Mood logs: patient + linked DOCTORS (resolve_patient_doctor_only)
- Chat: PATIENT ONLY — even doctors can't read someone else's chats.
  Each chat endpoint checks current_user.role == "ELDERLY".
- Wellness recommendations: patient + linked DOCTORS

Endpoints:
  POST   /mental/mood                    create mood log (with AI sentiment)
  GET    /mental/mood                    list logs
  GET    /mental/mood/summary            7/30/90 day trend + averages
  DELETE /mental/mood/{id}               delete

  POST   /mental/chat/sessions           start a new chat session (patient only)
  GET    /mental/chat/sessions           list patient's chat sessions
  GET    /mental/chat/sessions/{id}      get session + messages
  POST   /mental/chat/sessions/{id}/messages   send message + get AI reply
  DELETE /mental/chat/sessions/{id}      delete session + all messages

  GET    /mental/recommendations         list active recs
  POST   /mental/recommendations/generate   trigger fresh generation
  POST   /mental/recommendations/{id}/dismiss
  POST   /mental/recommendations/{id}/save
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.ai.mental_health import (
    analyze_sentiment, chat_response, generate_recommendations,
)
from app.core.database import get_db
from app.core.dependencies import get_current_user, stamp_audit
from app.core.patient_access import resolve_patient_doctor_only
from app.models.mental_health import (
    ChatMessage, ChatSession, MoodLog, WellnessRecommendation,
)
from app.models.medication_intake import MedicationIntakeLog
from app.models.physical import ActivityLog
from app.models.user import User
from app.schemas import (
    ChatMessageCreate, ChatMessageOut, ChatPersona, ChatSessionCreate,
    ChatSessionOut, ChatTurnOut, MessageRole, MoodLogCreate, MoodLogOut,
    MoodSummary, MoodTrendPoint, RecommendationStatus,
    WellnessGenerateRequest, WellnessRecommendationOut,
)


log = logging.getLogger("careai.mental")
router = APIRouter(prefix="/mental", tags=["Module 3 — Mental Health Support"])


# ════════════════════════════════════════════════════════════════════════════
# Mood logs
# ════════════════════════════════════════════════════════════════════════════
@router.post("/mood", response_model=MoodLogOut, status_code=status.HTTP_201_CREATED)
async def create_mood(
    body: MoodLogCreate,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    patient = resolve_patient_doctor_only(db, cu, body.patient_id)

    # Run sentiment on note if present (best-effort — don't fail the log
    # save if the AI is down)
    sentiment = {}
    if body.note and body.note.strip():
        try:
            sentiment = await analyze_sentiment(body.note)
        except Exception as e:
            log.warning("Sentiment analysis failed: %s", e)
            sentiment = {}

    m = MoodLog(
        user_id=patient.id,
        mood=body.mood, sleep=body.sleep, energy=body.energy, anxiety=body.anxiety,
        note=body.note,
        sentiment_label=sentiment.get("label"),
        sentiment_score=sentiment.get("score"),
        ai_insight=sentiment.get("insight"),
        ai_suggestion=sentiment.get("suggestion"),
        ai_provider=sentiment.get("ai_provider"),
        ai_fallback_used=sentiment.get("ai_fallback_used", False),
        logged_at=body.logged_at or datetime.utcnow(),
    )
    stamp_audit(m, cu)
    db.add(m)
    db.commit()
    db.refresh(m)
    return MoodLogOut.from_orm_with_audit(m)


@router.get("/mood", response_model=list[MoodLogOut])
def list_moods(
    patient_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    patient = resolve_patient_doctor_only(db, cu, patient_id)
    rows = (db.query(MoodLog)
              .filter(MoodLog.user_id == patient.id)
              .order_by(desc(MoodLog.logged_at))
              .limit(limit).all())
    return [MoodLogOut.from_orm_with_audit(r) for r in rows]


@router.get("/mood/summary", response_model=MoodSummary)
def mood_summary(
    patient_id: Optional[str] = Query(None),
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    patient = resolve_patient_doctor_only(db, cu, patient_id)
    since = datetime.utcnow() - timedelta(days=days)

    rows = (db.query(MoodLog)
              .filter(MoodLog.user_id == patient.id,
                      MoodLog.logged_at >= since)
              .order_by(MoodLog.logged_at.asc())
              .all())

    if not rows:
        return MoodSummary(
            days=days, avg_mood=0, avg_sleep=0, avg_energy=0, avg_anxiety=0,
            entry_count=0, trend=[],
        )

    # Group by date
    by_date = {}
    for r in rows:
        key = r.logged_at.strftime("%Y-%m-%d")
        if key not in by_date:
            by_date[key] = {"mood": [], "sleep": [], "energy": [], "anxiety": []}
        by_date[key]["mood"].append(r.mood)
        by_date[key]["sleep"].append(r.sleep)
        by_date[key]["energy"].append(r.energy)
        by_date[key]["anxiety"].append(r.anxiety)

    trend = []
    for date, vals in sorted(by_date.items()):
        n = len(vals["mood"])
        trend.append(MoodTrendPoint(
            date=date,
            mood=round(sum(vals["mood"]) / n, 2),
            sleep=round(sum(vals["sleep"]) / n, 2),
            energy=round(sum(vals["energy"]) / n, 2),
            anxiety=round(sum(vals["anxiety"]) / n, 2),
            entry_count=n,
        ))

    avg = lambda field: round(sum(r.__dict__[field] for r in rows) / len(rows), 2)
    return MoodSummary(
        days=days,
        avg_mood=avg("mood"), avg_sleep=avg("sleep"),
        avg_energy=avg("energy"), avg_anxiety=avg("anxiety"),
        entry_count=len(rows), trend=trend,
    )


@router.delete("/mood/{mood_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_mood(
    mood_id: str,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    m = db.query(MoodLog).filter(MoodLog.id == mood_id).first()
    if not m:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mood log not found")
    # Only the patient (owner) or the logger can delete
    if m.user_id != cu.id and m.created_by_user_id != cu.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot delete this entry")
    db.delete(m)
    db.commit()


# ════════════════════════════════════════════════════════════════════════════
# Chat — PATIENT ONLY (private)
# ════════════════════════════════════════════════════════════════════════════
def _require_patient(cu: User) -> None:
    """Chat endpoints reject non-patient users entirely. No exceptions."""
    if cu.role != "ELDERLY":
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Chat is private to the patient. Caregivers cannot view.")


def _serialize_session(db: Session, s: ChatSession) -> ChatSessionOut:
    count = db.query(func.count(ChatMessage.id)).filter(
        ChatMessage.session_id == s.id
    ).scalar() or 0
    return ChatSessionOut(
        id=s.id, user_id=s.user_id, persona=s.persona, title=s.title,
        created_at=s.created_at, last_message_at=s.last_message_at,
        message_count=count,
    )


@router.post("/chat/sessions", response_model=ChatSessionOut, status_code=status.HTTP_201_CREATED)
def create_chat_session(
    body: ChatSessionCreate,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    _require_patient(cu)
    s = ChatSession(user_id=cu.id, persona=body.persona.value)
    db.add(s)
    db.commit()
    db.refresh(s)
    return _serialize_session(db, s)


@router.get("/chat/sessions", response_model=list[ChatSessionOut])
def list_chat_sessions(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    _require_patient(cu)
    rows = (db.query(ChatSession)
              .filter(ChatSession.user_id == cu.id)
              .order_by(desc(ChatSession.last_message_at))
              .limit(limit).all())
    return [_serialize_session(db, s) for s in rows]


@router.get("/chat/sessions/{session_id}/messages", response_model=list[ChatMessageOut])
def list_messages(
    session_id: str,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    _require_patient(cu)
    s = db.query(ChatSession).filter(
        ChatSession.id == session_id, ChatSession.user_id == cu.id
    ).first()
    if not s:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    rows = (db.query(ChatMessage)
              .filter(ChatMessage.session_id == session_id)
              .order_by(ChatMessage.created_at.asc())
              .all())
    return rows


@router.post("/chat/sessions/{session_id}/messages", response_model=ChatTurnOut)
async def send_message(
    session_id: str,
    body: ChatMessageCreate,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Send a user message; AI generates assistant reply. Both stored."""
    _require_patient(cu)
    s = db.query(ChatSession).filter(
        ChatSession.id == session_id, ChatSession.user_id == cu.id
    ).first()
    if not s:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")

    # 1. Save user message
    user_msg = ChatMessage(
        session_id=s.id, role="USER", content=body.content.strip(),
    )
    db.add(user_msg)
    db.flush()

    # 2. Build history for AI context
    prior = (db.query(ChatMessage)
               .filter(ChatMessage.session_id == s.id,
                       ChatMessage.id != user_msg.id)
               .order_by(ChatMessage.created_at.asc())
               .all())
    history = [(m.role, m.content) for m in prior]

    # 3. Call AI
    try:
        ai_reply = await chat_response(s.persona, history, body.content)
    except Exception as e:
        log.warning("Chat AI call failed: %s", e)
        # Graceful fallback message
        ai_reply = {
            "content": "I'm having trouble responding right now. Could you try again in a moment? In the meantime, take a slow, gentle breath.",
            "ai_provider": "fallback",
            "ai_fallback_used": True,
        }

    # 4. Save assistant message
    assistant_msg = ChatMessage(
        session_id=s.id, role="ASSISTANT",
        content=ai_reply["content"],
        ai_provider=ai_reply.get("ai_provider"),
        ai_fallback_used=ai_reply.get("ai_fallback_used", False),
    )
    db.add(assistant_msg)

    # 5. Update session metadata
    s.last_message_at = datetime.utcnow()
    if not s.title:
        # Auto-title from first user message (first 60 chars)
        s.title = body.content.strip()[:60]

    db.commit()
    db.refresh(user_msg)
    db.refresh(assistant_msg)

    return ChatTurnOut(
        user_message=ChatMessageOut.model_validate(user_msg),
        assistant_message=ChatMessageOut.model_validate(assistant_msg),
    )


@router.delete("/chat/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(
    session_id: str,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    _require_patient(cu)
    s = db.query(ChatSession).filter(
        ChatSession.id == session_id, ChatSession.user_id == cu.id
    ).first()
    if not s:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    db.delete(s)  # cascades to messages
    db.commit()


# ════════════════════════════════════════════════════════════════════════════
# Wellness Recommendations
# ════════════════════════════════════════════════════════════════════════════
@router.get("/recommendations", response_model=list[WellnessRecommendationOut])
def list_recommendations(
    patient_id: Optional[str] = Query(None),
    status_filter: Optional[RecommendationStatus] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    patient = resolve_patient_doctor_only(db, cu, patient_id)
    q = db.query(WellnessRecommendation).filter(
        WellnessRecommendation.user_id == patient.id
    )
    if status_filter:
        q = q.filter(WellnessRecommendation.status == status_filter.value)
    return q.order_by(desc(WellnessRecommendation.generated_at)).limit(limit).all()


@router.post("/recommendations/generate", response_model=list[WellnessRecommendationOut])
async def generate_recs(
    body: WellnessGenerateRequest = WellnessGenerateRequest(),
    patient_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Generate fresh recommendations from recent activity + mood."""
    patient = resolve_patient_doctor_only(db, cu, patient_id)
    since = datetime.utcnow() - timedelta(days=body.days_lookback)

    # Build context summary
    moods = (db.query(MoodLog)
               .filter(MoodLog.user_id == patient.id, MoodLog.logged_at >= since)
               .all())
    activities = (db.query(ActivityLog)
                    .filter(ActivityLog.user_id == patient.id, ActivityLog.logged_at >= since)
                    .all())
    intakes = (db.query(MedicationIntakeLog)
                 .filter(MedicationIntakeLog.user_id == patient.id,
                         MedicationIntakeLog.taken_at >= since)
                 .all())

    if not moods and not activities and not intakes:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Not enough data to generate recommendations. Log mood and activity for a few days first."
        )

    # Compose a textual summary
    lines = [f"Time period: last {body.days_lookback} days"]
    if moods:
        n = len(moods)
        avg = lambda f: round(sum(getattr(m, f) for m in moods) / n, 1)
        lines.append(f"Mood entries: {n}")
        lines.append(f"  Average mood (1-5, higher=better): {avg('mood')}")
        lines.append(f"  Average sleep quality: {avg('sleep')}")
        lines.append(f"  Average energy: {avg('energy')}")
        lines.append(f"  Average calmness (5=calmest): {avg('anxiety')}")

        notes = [m.note for m in moods if m.note][-3:]
        if notes:
            lines.append("  Recent notes (most recent first):")
            for note in reversed(notes):
                lines.append(f"   - \"{note[:200]}\"")
    else:
        lines.append("No mood entries in this period.")

    if activities:
        types = {}
        for a in activities:
            types[a.type] = types.get(a.type, 0) + 1
        lines.append(f"Activities logged: {len(activities)} ({', '.join(f'{k}: {v}' for k, v in types.items())})")
    else:
        lines.append("No activities logged in this period.")

    if intakes:
        lines.append(f"Medication doses taken: {len(intakes)}")

    context = "\n".join(lines)

    # Call AI
    try:
        result = await generate_recommendations(context)
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"Recommendation generation failed: {str(e)}")

    out = []
    for rec in result["recommendations"][:5]:
        if not isinstance(rec, dict):
            continue
        title = str(rec.get("title", "")).strip()[:255]
        body_text = str(rec.get("body", "")).strip()
        category = str(rec.get("category", "mindfulness")).lower().strip()[:50]
        rationale = str(rec.get("rationale", ""))[:1000] or None
        if not title or not body_text:
            continue

        r = WellnessRecommendation(
            user_id=patient.id,
            title=title, body=body_text, category=category,
            rationale=rationale,
            ai_provider=result["ai_provider"],
            ai_fallback_used=result["ai_fallback_used"],
        )
        db.add(r)
        out.append(r)
    db.commit()
    for r in out:
        db.refresh(r)
    return out


@router.post("/recommendations/{rec_id}/dismiss", response_model=WellnessRecommendationOut)
def dismiss_rec(
    rec_id: str,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    r = _get_owned_rec(db, rec_id, cu)
    r.status = "DISMISSED"
    r.dismissed_at = datetime.utcnow()
    db.commit()
    db.refresh(r)
    return r


@router.post("/recommendations/{rec_id}/save", response_model=WellnessRecommendationOut)
def save_rec(
    rec_id: str,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    r = _get_owned_rec(db, rec_id, cu)
    r.status = "SAVED"
    r.saved_at = datetime.utcnow()
    db.commit()
    db.refresh(r)
    return r


def _get_owned_rec(db: Session, rec_id: str, cu: User) -> WellnessRecommendation:
    r = db.query(WellnessRecommendation).filter(
        WellnessRecommendation.id == rec_id
    ).first()
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recommendation not found")
    # Patient owns; doctor can also act on behalf if linked
    resolve_patient_doctor_only(db, cu, r.user_id)
    return r
