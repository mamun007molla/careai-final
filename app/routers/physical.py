"""Module 1 — Physical Monitoring router.

Endpoints (per requirements doc):
1. Activity Tracker + Fall Detection  → POST /physical/detect-fall
2. Medication Verification             → POST /physical/verify-medication
3. Activity Logging                    → /physical/activities (CRUD + stats)
4. Routine Schedule                    → /physical/routines (CRUD)

Design notes:
- All write endpoints support `patient_id` query/body param so a doctor or
  family member can record on behalf of a patient (subject to link check).
- Audit fields populated via stamp_audit() — no router cares "who" did it
  except to enforce access; the audit just tells the read side.
"""
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user, stamp_audit
from app.core.notifications import create_notification
from app.core.patient_access import resolve_patient
from app.models.physical import (
    ActivityLog, FallDetectionLog, MedicationVerifyLog, Routine,
)
from app.models.user import User
from app.routers.files import save_upload_to_db, save_bytes_to_db
from app.schemas import (
    ActivityLogCreate, ActivityLogOut, ActivityStats,
    FallDetectionLogOut, FallDetectionResult,
    MedicationVerifyLogOut, MedicationVerifyResult,
    RoutineCreate, RoutineOut,
)


router = APIRouter(prefix="/physical", tags=["Module 1 — Physical Monitoring"])


# ════════════════════════════════════════════════════════════════════════════
# Feature 3 — Activity Logging
# ════════════════════════════════════════════════════════════════════════════
@router.get("/activities", response_model=list[ActivityLogOut])
def list_activities(
    patient_id: Optional[str] = Query(None, description="Required if caller is FAMILY/DOCTOR"),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    patient = resolve_patient(db, cu, patient_id)
    rows = (db.query(ActivityLog)
              .filter(ActivityLog.user_id == patient.id)
              .order_by(desc(ActivityLog.logged_at))
              .limit(limit).all())
    return [ActivityLogOut.from_orm_with_audit(r) for r in rows]


@router.post("/activities", response_model=ActivityLogOut, status_code=status.HTTP_201_CREATED)
def create_activity(
    body: ActivityLogCreate,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    patient = resolve_patient(db, cu, body.patient_id)
    log = ActivityLog(
        user_id=patient.id,
        type=body.type,
        duration=body.duration,
        notes=body.notes,
        logged_at=body.logged_at or datetime.utcnow(),
    )
    stamp_audit(log, cu)
    db.add(log)
    db.commit()
    db.refresh(log)
    return ActivityLogOut.from_orm_with_audit(log)


@router.delete("/activities/{activity_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_activity(
    activity_id: str,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    log = db.query(ActivityLog).filter(ActivityLog.id == activity_id).first()
    if not log:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Activity not found")
    # Patient who owns it OR the user who created it can delete
    if cu.id not in (log.user_id, log.created_by_user_id):
        # Else, must be linked to the patient
        resolve_patient(db, cu, log.user_id)
    db.delete(log)
    db.commit()


@router.get("/activities/stats", response_model=ActivityStats)
def activity_stats(
    patient_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    patient = resolve_patient(db, cu, patient_id)
    logs = db.query(ActivityLog).filter(ActivityLog.user_id == patient.id).all()
    today = datetime.utcnow().date()
    by_type: dict = {}
    for l in logs:
        by_type[l.type] = by_type.get(l.type, 0) + 1
    return ActivityStats(
        total=len(logs),
        today=sum(1 for l in logs if l.logged_at.date() == today),
        by_type=by_type,
        total_duration_min=sum(l.duration or 0 for l in logs),
    )


# ════════════════════════════════════════════════════════════════════════════
# Feature 4 — Routine Schedule
# ════════════════════════════════════════════════════════════════════════════
@router.get("/routines", response_model=list[RoutineOut])
def list_routines(
    patient_id: Optional[str] = Query(None),
    only_active: bool = Query(True),
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    patient = resolve_patient(db, cu, patient_id)
    q = db.query(Routine).filter(Routine.user_id == patient.id)
    if only_active:
        q = q.filter(Routine.is_active == True)  # noqa: E712
    return [RoutineOut.from_orm_with_audit(r) for r in q.order_by(Routine.scheduled_at).all()]


@router.post("/routines", response_model=RoutineOut, status_code=status.HTTP_201_CREATED)
def create_routine(
    body: RoutineCreate,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    patient = resolve_patient(db, cu, body.patient_id)
    routine = Routine(
        user_id=patient.id,
        title=body.title,
        type=body.type,
        scheduled_at=body.scheduled_at,
        days=json.dumps(body.days),
        is_active=body.is_active,
        notes=body.notes,
    )
    stamp_audit(routine, cu)
    db.add(routine)
    db.commit()
    db.refresh(routine)
    return RoutineOut.from_orm_with_audit(routine)


@router.put("/routines/{routine_id}", response_model=RoutineOut)
def update_routine(
    routine_id: str,
    body: RoutineCreate,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    routine = db.query(Routine).filter(Routine.id == routine_id).first()
    if not routine:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Routine not found")
    if cu.id != routine.user_id:
        resolve_patient(db, cu, routine.user_id)

    routine.title = body.title
    routine.type = body.type
    routine.scheduled_at = body.scheduled_at
    routine.days = json.dumps(body.days)
    routine.is_active = body.is_active
    routine.notes = body.notes
    db.commit()
    db.refresh(routine)
    return RoutineOut.from_orm_with_audit(routine)


@router.delete("/routines/{routine_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_routine(
    routine_id: str,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    routine = db.query(Routine).filter(Routine.id == routine_id).first()
    if not routine:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Routine not found")
    if cu.id != routine.user_id:
        resolve_patient(db, cu, routine.user_id)
    db.delete(routine)
    db.commit()


# ════════════════════════════════════════════════════════════════════════════
# Feature 2 — Medication Verification (AI)
# ════════════════════════════════════════════════════════════════════════════
@router.post("/verify-medication", response_model=MedicationVerifyResult)
async def verify_medication(
    image: UploadFile = File(..., description="Photo of the pill / box / strip"),
    prescribed_medication: str = Form(..., min_length=1),
    patient_id: Optional[str] = Form(None),
    save_log: bool = Form(True),
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    from app.ai.medication_verify import verify_medication_image

    patient = resolve_patient(db, cu, patient_id)

    # Save the image to DB first (so we have a persistent file_id even if AI fails)
    image_file = await save_upload_to_db(
        db, image,
        owner_id=patient.id,
        uploaded_by=cu.id,
        purpose="MEDICATION_VERIFY",
        max_mb=settings.MAX_IMAGE_SIZE_MB,
    )

    # Run AI (Ollama → Groq fallback)
    try:
        result = await verify_medication_image(
            image_file.content,
            prescribed_medication,
            mime_type=image_file.mime_type,
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"AI verification failed: {str(e)}")

    log_id = None
    if save_log:
        log = MedicationVerifyLog(
            user_id=patient.id,
            prescribed_medication=prescribed_medication,
            detected_medication=result["detected_medication"],
            matched=result["matched"],
            confidence=result["confidence"],
            ai_provider=result["ai_provider"],
            ai_fallback_used=result["ai_fallback_used"],
            ai_fallback_reason=result.get("ai_fallback_reason"),
            image_file_id=image_file.id,
            raw_response=result.get("raw_response"),
        )
        stamp_audit(log, cu)
        db.add(log)
        db.commit()
        db.refresh(log)
        log_id = log.id
    else:
        db.commit()

    return MedicationVerifyResult(
        matched=result["matched"],
        confidence=result["confidence"],
        detected_medication=result["detected_medication"],
        prescribed_medication=prescribed_medication,
        warnings=result.get("warnings", []),
        image_file_id=image_file.id,
        log_id=log_id,
        ai_provider=result["ai_provider"],
        ai_fallback_used=result["ai_fallback_used"],
        ai_fallback_reason=result.get("ai_fallback_reason"),
        latency_ms=result.get("latency_ms", 0),
    )


@router.get("/verify-medication/history", response_model=list[MedicationVerifyLogOut])
def verify_history(
    patient_id: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    patient = resolve_patient(db, cu, patient_id)
    rows = (db.query(MedicationVerifyLog)
              .filter(MedicationVerifyLog.user_id == patient.id)
              .order_by(desc(MedicationVerifyLog.verified_at))
              .limit(limit).all())
    return [MedicationVerifyLogOut.from_orm_with_audit(r) for r in rows]


# ════════════════════════════════════════════════════════════════════════════
# Feature 1 — Fall Detection (video → AI → DB)
# ════════════════════════════════════════════════════════════════════════════
@router.post("/detect-fall", response_model=FallDetectionResult)
async def detect_fall(
    video: UploadFile = File(..., description="Video clip (mp4, mov, webm)"),
    patient_id: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    from app.ai.fall_detection_client import run_fall_detection

    patient = resolve_patient(db, cu, patient_id)

    # 1. Persist input video to DB
    input_file = await save_upload_to_db(
        db, video,
        owner_id=patient.id,
        uploaded_by=cu.id,
        purpose="FALL_DETECTION_INPUT",
        max_mb=settings.MAX_VIDEO_SIZE_MB,
    )
    db.commit()  # commit so the file_id is durable even if detection fails

    # 2. Run detection — returns dict with results + optional output_video_bytes
    try:
        result = await run_fall_detection(input_file.content, input_file.filename)
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"Fall detection failed: {str(e)}")

    # 3. Save annotated output video to DB if produced
    output_file_id = None
    if result.get("output_video_bytes"):
        out = save_bytes_to_db(
            db, result["output_video_bytes"],
            filename=f"fall_output_{input_file.id}.mp4",
            mime_type="video/mp4",
            owner_id=patient.id,
            uploaded_by=cu.id,
            purpose="FALL_DETECTION_OUTPUT",
        )
        output_file_id = out.id

    # 4. Persist detection log
    log = FallDetectionLog(
        user_id=patient.id,
        fall_detected=result["fall_detected"],
        confidence=result["confidence"],
        mode=result["mode"],
        has_audio=result["has_audio"],
        segments_json=json.dumps(result["segments"]),
        input_video_file_id=input_file.id,
        output_video_file_id=output_file_id,
        alert_sent=False,
    )
    stamp_audit(log, cu)
    db.add(log)
    db.commit()
    db.refresh(log)

    # 5. Dispatch notifications on fall detection (patient + linked family/doctors)
    alert_sent = False
    if result["fall_detected"]:
        from app.models.user import PatientLink

        body_msg = (f"A fall was detected in the uploaded video "
                    f"(confidence {round(result['confidence']*100)}%). "
                    f"Please check on the patient.")

        # Notify the patient
        create_notification(
            db, user_id=patient.id, type_="FALL_DETECTED",
            title="🚨 Fall detected",
            body=body_msg, link="/physical/activity-tracker",
            source_user=cu,
        )
        # Notify all linked contacts (family + doctors)
        contacts = (db.query(PatientLink)
                      .filter(PatientLink.patient_id == patient.id)
                      .all())
        for link in contacts:
            create_notification(
                db, user_id=link.linked_id, type_="FALL_DETECTED",
                title=f"🚨 Fall detected: {patient.name}",
                body=body_msg, link="/physical/activity-tracker",
                source_user=cu,
            )
        alert_sent = True
        log.alert_sent = True
        db.commit()

    return FallDetectionResult(
        log_id=log.id,
        fall_detected=result["fall_detected"],
        confidence=result["confidence"],
        mode=result["mode"],
        has_audio=result["has_audio"],
        segments=result["segments"],
        input_video_file_id=input_file.id,
        output_video_file_id=output_file_id,
        message="⚠️ FALL DETECTED — alert recipient(s)" if result["fall_detected"] else "✅ No fall detected",
        alert_sent=alert_sent,
    )


@router.get("/fall-detection/history", response_model=list[FallDetectionLogOut])
def fall_history(
    patient_id: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    patient = resolve_patient(db, cu, patient_id)
    rows = (db.query(FallDetectionLog)
              .filter(FallDetectionLog.user_id == patient.id)
              .order_by(desc(FallDetectionLog.detected_at))
              .limit(limit).all())
    return [FallDetectionLogOut.from_orm_with_audit(r) for r in rows]
