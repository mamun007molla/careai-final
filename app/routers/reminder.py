"""Medication Reminder + Prescription Summary endpoints.

Endpoints:
  GET    /reminder/medications/today     today's medication schedule with intake status
  POST   /reminder/medications/take      mark a dose as taken
  GET    /reminder/medications/streak    compliance stats (last 7 days)
  GET    /reminder/medications/history   recent intake log

  GET    /prescriptions                  all prescriptions across all visits
  POST   /prescriptions/summarize        AI extracts structured medications from text
"""
import json
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_user, stamp_audit
from app.core.patient_access import resolve_patient
from app.models.health import MedicalVisit, VisitAttachment
from app.models.medication_intake import MedicationIntakeLog
from app.models.physical import Routine
from app.models.user import User
from app.schemas import (
    MedicationDoseToday, MedicationIntakeCreate, MedicationIntakeOut,
    MedicationStreakStats, PrescriptionAISummary, PrescriptionItem,
)


router = APIRouter(prefix="/reminder", tags=["Medication Reminder & Prescriptions"])


WEEKDAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


# ════════════════════════════════════════════════════════════════════════════
# Today's medication schedule
# ════════════════════════════════════════════════════════════════════════════
@router.get("/medications/today", response_model=list[MedicationDoseToday])
def todays_medications(
    patient_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Return all medication routines scheduled for today, with intake status."""
    patient = resolve_patient(db, cu, patient_id)
    now = datetime.now()
    today = WEEKDAYS[now.weekday()]
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Get all medication-type active routines for this patient
    routines = (db.query(Routine)
                  .filter(Routine.user_id == patient.id,
                          Routine.type == "medication",
                          Routine.is_active == True)
                  .all())

    # Get today's intake logs (one query, then map by routine_id)
    todays_intakes = (db.query(MedicationIntakeLog)
                        .filter(MedicationIntakeLog.user_id == patient.id,
                                MedicationIntakeLog.taken_at >= today_start)
                        .all())
    intake_by_routine = {i.routine_id: i for i in todays_intakes if i.routine_id}

    out: list[MedicationDoseToday] = []
    for r in routines:
        try:
            days = json.loads(r.days) if r.days else []
        except Exception:
            days = []
        if today not in days:
            continue

        intake = intake_by_routine.get(r.id)
        # Calculate minutes_until
        try:
            h, m = r.scheduled_at.split(":")
            scheduled_dt = now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
            minutes_until = int((scheduled_dt - now).total_seconds() / 60)
        except Exception:
            minutes_until = 0
        is_overdue = minutes_until < -15 and intake is None  # 15min grace

        out.append(MedicationDoseToday(
            routine_id=r.id,
            medication_name=r.title,
            scheduled_at=r.scheduled_at,
            days=days,
            notes=r.notes,
            is_taken=intake is not None,
            taken_at=intake.taken_at if intake else None,
            is_overdue=is_overdue,
            minutes_until=minutes_until,
        ))

    out.sort(key=lambda d: d.scheduled_at)
    return out


# ════════════════════════════════════════════════════════════════════════════
# Mark dose as taken
# ════════════════════════════════════════════════════════════════════════════
@router.post("/medications/take", response_model=MedicationIntakeOut, status_code=status.HTTP_201_CREATED)
def take_medication(
    body: MedicationIntakeCreate,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    patient = resolve_patient(db, cu, body.patient_id)

    # Check if a routine_id was provided and validate it exists
    if body.routine_id:
        routine = db.query(Routine).filter(Routine.id == body.routine_id).first()
        if not routine:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Routine not found")
        if routine.user_id != patient.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Routine doesn't belong to this patient")

        # Idempotency: don't double-record same routine on same day
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        already = (db.query(MedicationIntakeLog)
                     .filter(MedicationIntakeLog.routine_id == body.routine_id,
                             MedicationIntakeLog.taken_at >= today_start)
                     .first())
        if already:
            return MedicationIntakeOut.from_orm_with_audit(already)

    # On-time = within ±15 min of scheduled
    now = datetime.now()
    try:
        h, m = body.scheduled_at.split(":")
        scheduled_dt = now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
        on_time = abs((now - scheduled_dt).total_seconds()) <= 15 * 60
    except Exception:
        on_time = True

    intake = MedicationIntakeLog(
        user_id=patient.id,
        routine_id=body.routine_id,
        medication_name=body.medication_name,
        scheduled_at=body.scheduled_at,
        on_time=on_time,
        notes=body.notes,
    )
    stamp_audit(intake, cu)
    db.add(intake)
    db.commit()
    db.refresh(intake)
    return MedicationIntakeOut.from_orm_with_audit(intake)


# ════════════════════════════════════════════════════════════════════════════
# Streak / compliance stats
# ════════════════════════════════════════════════════════════════════════════
@router.get("/medications/streak", response_model=MedicationStreakStats)
def medication_streak(
    patient_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Compute compliance over the last 7 days."""
    patient = resolve_patient(db, cu, patient_id)
    now = datetime.now()
    seven_days_ago = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)

    # Active medication routines
    routines = (db.query(Routine)
                  .filter(Routine.user_id == patient.id,
                          Routine.type == "medication",
                          Routine.is_active == True)
                  .all())

    # For each of the last 7 days, count expected doses
    expected_total = 0
    full_days = 0
    intakes_total = 0

    intakes = (db.query(MedicationIntakeLog)
                 .filter(MedicationIntakeLog.user_id == patient.id,
                         MedicationIntakeLog.taken_at >= seven_days_ago)
                 .all())
    intakes_total = len(intakes)

    # Group intakes by date
    intakes_by_date = {}
    for i in intakes:
        d = i.taken_at.date()
        intakes_by_date.setdefault(d, set()).add(i.routine_id)

    for delta in range(7):
        day = (now - timedelta(days=delta)).date()
        wd_idx = (day.weekday())
        wd = WEEKDAYS[wd_idx]
        expected_today = 0
        for r in routines:
            try:
                days = json.loads(r.days) if r.days else []
            except Exception:
                days = []
            if wd in days:
                expected_today += 1

        if expected_today == 0:
            continue
        expected_total += expected_today

        taken_today = len(intakes_by_date.get(day, set()))
        if taken_today >= expected_today:
            full_days += 1

    pct = round((intakes_total / expected_total * 100) if expected_total else 0, 1)
    return MedicationStreakStats(
        days_with_full_compliance=full_days,
        total_doses_last_7_days=expected_total,
        taken_last_7_days=intakes_total,
        compliance_pct=pct,
    )


@router.get("/medications/history", response_model=list[MedicationIntakeOut])
def intake_history(
    patient_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    patient = resolve_patient(db, cu, patient_id)
    rows = (db.query(MedicationIntakeLog)
              .filter(MedicationIntakeLog.user_id == patient.id)
              .order_by(desc(MedicationIntakeLog.taken_at))
              .limit(limit).all())
    return [MedicationIntakeOut.from_orm_with_audit(r) for r in rows]


# ════════════════════════════════════════════════════════════════════════════
# Prescription summary — across all visits
# ════════════════════════════════════════════════════════════════════════════
prescriptions_router = APIRouter(prefix="/prescriptions", tags=["Prescriptions"])


@prescriptions_router.get("", response_model=list[PrescriptionItem])
def list_prescriptions(
    patient_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """List all prescriptions across all visits — text + attachments combined."""
    patient = resolve_patient(db, cu, patient_id)

    visits = (db.query(MedicalVisit)
                .filter(MedicalVisit.patient_id == patient.id)
                .order_by(desc(MedicalVisit.visit_date))
                .all())

    out: list[PrescriptionItem] = []
    for v in visits:
        doctor = db.query(User).filter(User.id == v.doctor_id).first() if v.doctor_id else None
        # If visit has prescription_text, surface it
        if v.prescription_text:
            out.append(PrescriptionItem(
                visit_id=v.id,
                visit_title=v.title,
                visit_date=v.visit_date,
                doctor_name=doctor.name if doctor else None,
                doctor_specialty=doctor.specialty if doctor else None,
                prescription_text=v.prescription_text,
            ))

        # Plus any PRESCRIPTION-kind attachments
        rx_atts = (db.query(VisitAttachment)
                     .filter(VisitAttachment.visit_id == v.id,
                             VisitAttachment.kind == "PRESCRIPTION")
                     .all())
        for a in rx_atts:
            from app.models.file import File as FileModel
            f = db.query(FileModel).filter(FileModel.id == a.file_id).first()
            out.append(PrescriptionItem(
                visit_id=v.id,
                visit_title=v.title,
                visit_date=v.visit_date,
                doctor_name=doctor.name if doctor else None,
                doctor_specialty=doctor.specialty if doctor else None,
                attachment_id=a.id,
                attachment_filename=f.filename if f else "prescription.pdf",
            ))

    return out[:limit]


@prescriptions_router.post("/summarize", response_model=PrescriptionAISummary)
async def summarize_prescription(
    text: str = Body(..., embed=True, min_length=10),
    cu: User = Depends(get_current_user),
):
    """AI extracts structured medication list from prescription text."""
    from app.ai.ai_router import run_text, AIServiceError

    prompt = f"""You are a clinical assistant extracting structured information
from a doctor's prescription.

PRESCRIPTION TEXT:
{text}

Extract each medication with name, dose, frequency, and duration. Also write a
short patient-friendly summary and any warnings (interactions, side effects).

Respond with ONLY this JSON:
{{
  "medications": [
    {{"name": "Amlodipine", "dose": "5mg", "frequency": "once daily", "duration": "30 days"}}
  ],
  "summary_text": "You have been prescribed... Take with water...",
  "warnings": ["Avoid grapefruit juice", "May cause drowsiness"]
}}"""

    try:
        result = await run_text(prompt, json_response=True)
    except AIServiceError:
        raise
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"AI summarization failed: {str(e)}")

    data = result.data
    return PrescriptionAISummary(
        medications=list(data.get("medications", [])),
        summary_text=str(data.get("summary_text", "")),
        warnings=list(data.get("warnings", [])),
        ai_provider=result.provider,
        ai_fallback_used=result.fallback_used,
    )


@prescriptions_router.post("/summarize-attachment/{file_id}", response_model=PrescriptionAISummary)
async def summarize_prescription_pdf(
    file_id: str,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Extract text from a prescription PDF/image attachment and summarize it.

    Visible to patient + linked doctors + linked family.
    """
    from app.models.file import File as FileModel
    from app.models.health import VisitAttachment, MedicalVisit
    from app.ai.ai_router import run_text, AIServiceError

    f = db.query(FileModel).filter(FileModel.id == file_id).first()
    if not f:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")

    # Verify the user has access to this file via visit attachment
    att = db.query(VisitAttachment).filter(VisitAttachment.file_id == file_id).first()
    if not att:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not a prescription attachment")
    visit = db.query(MedicalVisit).filter(MedicalVisit.id == att.visit_id).first()
    if not visit:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Visit not found")
    # Use the existing access helper — patient or any linked contact can view
    resolve_patient(db, cu, visit.patient_id)

    # Extract text from PDF or use vision AI for image
    extracted_text = ""
    mime = (f.mime_type or "").lower()

    if "pdf" in mime:
        try:
            import pypdf
            import io
            reader = pypdf.PdfReader(io.BytesIO(f.content))
            extracted_text = "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                                f"PDF extraction failed: {str(e)}")
    elif mime.startswith("image/"):
        # Use vision AI to read the image
        from app.ai.ai_router import run_vision
        ocr_prompt = ("Extract all text content from this prescription image. "
                      "Output only the extracted text exactly as written.")
        try:
            ocr_result = await run_vision(f.content, ocr_prompt,
                                          mime_type=f.mime_type, json_response=False)
            extracted_text = ocr_result.raw_text or ""
        except AIServiceError:
            raise
        except Exception as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                                f"Image OCR failed: {str(e)}")
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Unsupported file type — only PDF or image")

    if not extracted_text or len(extracted_text.strip()) < 10:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Could not extract sufficient text from this file")

    # Now summarize the extracted text
    prompt = f"""You are a clinical assistant extracting structured information
from a doctor's prescription.

PRESCRIPTION TEXT:
{extracted_text[:10000]}

Extract each medication with name, dose, frequency, and duration. Also write a
short patient-friendly summary and any warnings (interactions, side effects).

Respond with ONLY this JSON:
{{
  "medications": [
    {{"name": "Amlodipine", "dose": "5mg", "frequency": "once daily", "duration": "30 days"}}
  ],
  "summary_text": "You have been prescribed... Take with water...",
  "warnings": ["Avoid grapefruit juice", "May cause drowsiness"]
}}"""
    try:
        result = await run_text(prompt, json_response=True)
    except AIServiceError:
        raise
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"AI summarization failed: {str(e)}")

    data = result.data
    return PrescriptionAISummary(
        medications=list(data.get("medications", [])),
        summary_text=str(data.get("summary_text", "")),
        warnings=list(data.get("warnings", [])),
        ai_provider=result.provider,
        ai_fallback_used=result.fallback_used,
    )
