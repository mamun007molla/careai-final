"""Module 2 — Health Management router.

Access rules (per user decision):
- Visits / prescriptions / attachments: only DOCTOR can create or edit.
  ELDERLY and FAMILY can VIEW everything for patients they're linked to.
- Meal logs: anyone linked to the patient (incl. the patient themselves) can
  create or edit. Same as M1 logs.

Endpoints:
  GET    /health/visits                 list visits (filter by patient_id)
  POST   /health/visits                 create  (DOCTOR only)
  GET    /health/visits/{id}            single visit with attachments
  PATCH  /health/visits/{id}            update  (DOCTOR only, must be the creator's doctor)
  DELETE /health/visits/{id}            delete  (DOCTOR only, creator only)
  POST   /health/visits/{id}/attach     add file attachment (DOCTOR only)
  DELETE /health/attachments/{id}       remove attachment (DOCTOR only)

  GET    /health/meals                  list meal logs
  POST   /health/meals                  create
  PATCH  /health/meals/{id}             update
  DELETE /health/meals/{id}             delete
  POST   /health/meals/estimate         AI estimate from image (no save)
  GET    /health/meals/summary          daily nutrition rollup
"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user, require_roles, stamp_audit
from app.core.notifications import create_notification
from app.core.patient_access import resolve_patient
from app.models.file import File as FileModel
from app.models.health import MealLog, MedicalVisit, VisitAttachment
from app.models.user import PatientLink, User
from app.routers.files import save_upload_to_db
from app.schemas import (
    AttachmentKind, AttachmentOut, AuditOut,
    MealLogCreate, MealLogOut, MealNutritionEstimate, MedicalVisitCreate,
    MedicalVisitOut, MedicalVisitUpdate, NutritionDailySummary,
)


router = APIRouter(prefix="/health", tags=["Module 2 — Health Management"])


@router.get("/health")
def health():
    return {"status": "ok"}


# ════════════════════════════════════════════════════════════════════════════
# Helper — assemble a MedicalVisitOut with embedded attachments + doctor info
# ════════════════════════════════════════════════════════════════════════════
def _serialize_visit(db: Session, v: MedicalVisit) -> MedicalVisitOut:
    """Single visit → schema, with attachments and doctor info eager-loaded."""
    doctor = db.query(User).filter(User.id == v.doctor_id).first() if v.doctor_id else None

    # Attachments + their file metadata in one pass
    rows = (
        db.query(VisitAttachment, FileModel)
          .join(FileModel, FileModel.id == VisitAttachment.file_id)
          .filter(VisitAttachment.visit_id == v.id)
          .all()
    )
    atts = [
        AttachmentOut(
            id=a.id, visit_id=a.visit_id, file_id=a.file_id, kind=a.kind,
            description=a.description, uploaded_at=a.uploaded_at,
            filename=f.filename, mime_type=f.mime_type, size_bytes=f.size_bytes,
            created_by=AuditOut(
                user_id=a.created_by_user_id,
                role=a.created_by_role,
                name=a.created_by_name,
            ),
        )
        for a, f in rows
    ]

    return MedicalVisitOut(
        id=v.id, patient_id=v.patient_id,
        doctor_id=v.doctor_id,
        doctor_name=doctor.name if doctor else None,
        doctor_specialty=doctor.specialty if doctor else None,
        visit_type=v.visit_type, visit_date=v.visit_date, title=v.title,
        diagnosis=v.diagnosis, prescription_text=v.prescription_text,
        notes=v.notes, follow_up_at=v.follow_up_at,
        created_at=v.created_at,
        created_by=AuditOut(
            user_id=v.created_by_user_id,
            role=v.created_by_role,
            name=v.created_by_name,
        ),
        attachments=atts,
    )


# ════════════════════════════════════════════════════════════════════════════
# Visits — read endpoints (accessible to anyone linked to the patient)
# ════════════════════════════════════════════════════════════════════════════
@router.get("/visits", response_model=list[MedicalVisitOut])
def list_visits(
    patient_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    patient = resolve_patient(db, cu, patient_id)
    rows = (db.query(MedicalVisit)
              .filter(MedicalVisit.patient_id == patient.id)
              .order_by(desc(MedicalVisit.visit_date))
              .limit(limit).all())
    return [_serialize_visit(db, v) for v in rows]


@router.get("/visits/{visit_id}", response_model=MedicalVisitOut)
def get_visit(
    visit_id: str,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    v = db.query(MedicalVisit).filter(MedicalVisit.id == visit_id).first()
    if not v:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Visit not found")
    # Same access rule — caller must be linked to or be the patient
    resolve_patient(db, cu, v.patient_id)
    return _serialize_visit(db, v)


# ════════════════════════════════════════════════════════════════════════════
# Visits — write endpoints (DOCTOR only)
# ════════════════════════════════════════════════════════════════════════════
@router.post("/visits", response_model=MedicalVisitOut, status_code=status.HTTP_201_CREATED)
def create_visit(
    body: MedicalVisitCreate,
    db: Session = Depends(get_db),
    cu: User = Depends(require_roles("DOCTOR")),
):
    # Ensure patient exists and the doctor is linked to them
    resolve_patient(db, cu, body.patient_id)

    v = MedicalVisit(
        patient_id=body.patient_id,
        doctor_id=cu.id,
        visit_type=body.visit_type.value,
        visit_date=body.visit_date or datetime.utcnow(),
        title=body.title,
        diagnosis=body.diagnosis,
        prescription_text=body.prescription_text,
        notes=body.notes,
        follow_up_at=body.follow_up_at,
    )
    stamp_audit(v, cu)
    db.add(v)
    db.flush()

    # Notify the patient about the new visit
    create_notification(
        db, user_id=body.patient_id, type_="VISIT_ADDED",
        title=f"New visit: {body.title}",
        body=(f"{cu.name} ({cu.specialty or 'Doctor'}) added a visit. "
              + (body.diagnosis or "Open to view details.")[:200]),
        link="/health/visits",
        source_user=cu,
    )

    db.commit()
    db.refresh(v)
    return _serialize_visit(db, v)


@router.patch("/visits/{visit_id}", response_model=MedicalVisitOut)
def update_visit(
    visit_id: str,
    body: MedicalVisitUpdate,
    db: Session = Depends(get_db),
    cu: User = Depends(require_roles("DOCTOR")),
):
    v = db.query(MedicalVisit).filter(MedicalVisit.id == visit_id).first()
    if not v:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Visit not found")
    # Doctors can only edit their OWN visit records
    if v.created_by_user_id != cu.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "You can only edit visits you created")

    for field, val in body.model_dump(exclude_unset=True).items():
        setattr(v, field, val.value if hasattr(val, "value") else val)
    db.commit()
    db.refresh(v)
    return _serialize_visit(db, v)


@router.delete("/visits/{visit_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_visit(
    visit_id: str,
    db: Session = Depends(get_db),
    cu: User = Depends(require_roles("DOCTOR")),
):
    v = db.query(MedicalVisit).filter(MedicalVisit.id == visit_id).first()
    if not v:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Visit not found")
    if v.created_by_user_id != cu.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only delete visits you created")
    db.delete(v)
    db.commit()


# ════════════════════════════════════════════════════════════════════════════
# Visit attachments — DOCTOR only for create/delete
# ════════════════════════════════════════════════════════════════════════════
@router.post("/visits/{visit_id}/attach", response_model=AttachmentOut, status_code=status.HTTP_201_CREATED)
async def attach_to_visit(
    visit_id: str,
    file: UploadFile = File(...),
    kind: AttachmentKind = Form(AttachmentKind.PRESCRIPTION),
    description: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    cu: User = Depends(require_roles("DOCTOR")),
):
    v = db.query(MedicalVisit).filter(MedicalVisit.id == visit_id).first()
    if not v:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Visit not found")
    if v.created_by_user_id != cu.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "You can only attach to visits you created")

    # Map attachment kind → file purpose enum
    purpose_map = {
        "PRESCRIPTION":      "PRESCRIPTION",
        "LAB_REPORT":        "HEALTH_RECORD",
        "IMAGING":           "HEALTH_RECORD",
        "DISCHARGE_SUMMARY": "HEALTH_RECORD",
        "OTHER":             "HEALTH_RECORD",
    }
    fp = purpose_map[kind.value]

    # Save the file (allow up to MAX_VIDEO size since reports can be big PDFs)
    f = await save_upload_to_db(
        db, file,
        owner_id=v.patient_id,
        uploaded_by=cu.id,
        purpose=fp,
        max_mb=settings.MAX_VIDEO_SIZE_MB,
    )

    att = VisitAttachment(
        visit_id=v.id, file_id=f.id, kind=kind.value,
        description=description,
    )
    stamp_audit(att, cu)
    db.add(att)
    db.flush()

    # Notify the patient (especially loud for prescriptions)
    if kind.value == "PRESCRIPTION":
        create_notification(
            db, user_id=v.patient_id, type_="PRESCRIPTION_ADDED",
            title=f"📋 New prescription added",
            body=(f"{cu.name} attached a prescription to your visit '{v.title}'."
                  + (f" Note: {description}" if description else "")),
            link=f"/health/visits",
            source_user=cu,
        )
    else:
        create_notification(
            db, user_id=v.patient_id, type_="VISIT_ADDED",
            title=f"📎 Document added: {kind.value.replace('_', ' ').title()}",
            body=f"{cu.name} attached a {kind.value.lower().replace('_', ' ')} to '{v.title}'.",
            link=f"/health/visits",
            source_user=cu,
            send_email=False,  # less urgent
        )

    db.commit()
    db.refresh(att)

    return AttachmentOut(
        id=att.id, visit_id=att.visit_id, file_id=att.file_id, kind=att.kind,
        description=att.description, uploaded_at=att.uploaded_at,
        filename=f.filename, mime_type=f.mime_type, size_bytes=f.size_bytes,
        created_by=AuditOut(
            user_id=att.created_by_user_id,
            role=att.created_by_role,
            name=att.created_by_name,
        ),
    )


@router.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_attachment(
    attachment_id: str,
    db: Session = Depends(get_db),
    cu: User = Depends(require_roles("DOCTOR")),
):
    att = db.query(VisitAttachment).filter(VisitAttachment.id == attachment_id).first()
    if not att:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    if att.created_by_user_id != cu.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only delete your own attachments")

    # Cascade-delete the file too — orphan files take up space
    f = db.query(FileModel).filter(FileModel.id == att.file_id).first()
    db.delete(att)
    if f:
        db.delete(f)
    db.commit()


# ════════════════════════════════════════════════════════════════════════════
# Meal logs — anyone linked can write
# ════════════════════════════════════════════════════════════════════════════
@router.get("/meals", response_model=list[MealLogOut])
def list_meals(
    patient_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    patient = resolve_patient(db, cu, patient_id)
    rows = (db.query(MealLog)
              .filter(MealLog.user_id == patient.id)
              .order_by(desc(MealLog.eaten_at))
              .limit(limit).all())
    return [MealLogOut.from_orm_with_audit(r) for r in rows]


@router.post("/meals", response_model=MealLogOut, status_code=status.HTTP_201_CREATED)
def create_meal(
    body: MealLogCreate,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    patient = resolve_patient(db, cu, body.patient_id)
    m = MealLog(
        user_id=patient.id,
        meal_type=body.meal_type,
        description=body.description,
        eaten_at=body.eaten_at or datetime.utcnow(),
        calories=body.calories, protein_g=body.protein_g,
        carbs_g=body.carbs_g, fat_g=body.fat_g,
        image_file_id=body.image_file_id,
        notes=body.notes,
        # If image was AI-estimated upstream, the frontend already populated
        # the macros — we infer ai_estimated by whether image_file_id is set.
        ai_estimated=bool(body.image_file_id),
    )
    stamp_audit(m, cu)
    db.add(m)
    db.commit()
    db.refresh(m)
    return MealLogOut.from_orm_with_audit(m)


@router.patch("/meals/{meal_id}", response_model=MealLogOut)
def update_meal(
    meal_id: str,
    body: MealLogCreate,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    m = db.query(MealLog).filter(MealLog.id == meal_id).first()
    if not m:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Meal not found")
    if cu.id != m.user_id:
        resolve_patient(db, cu, m.user_id)

    # Update editable fields (skip patient_id — meal owner doesn't change)
    for field in ("meal_type", "description", "calories", "protein_g",
                  "carbs_g", "fat_g", "notes"):
        v = getattr(body, field)
        if v is not None:
            setattr(m, field, v)
    if body.eaten_at:
        m.eaten_at = body.eaten_at
    # User edited the AI estimate → no longer "pure AI"
    m.ai_estimated = False
    db.commit()
    db.refresh(m)
    return MealLogOut.from_orm_with_audit(m)


@router.delete("/meals/{meal_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_meal(
    meal_id: str,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    m = db.query(MealLog).filter(MealLog.id == meal_id).first()
    if not m:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Meal not found")
    if cu.id != m.user_id and cu.id != m.created_by_user_id:
        resolve_patient(db, cu, m.user_id)
    db.delete(m)
    db.commit()


@router.post("/meals/estimate", response_model=MealNutritionEstimate)
async def estimate_meal_nutrition(
    image: UploadFile = File(..., description="Photo of the food"),
    patient_id: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Run AI vision to estimate nutrition. Saves the image, returns suggestion.

    Frontend flow: user uploads → calls this → edits values → calls POST /meals
    with image_file_id and the (possibly edited) macros.
    """
    from app.ai.nutrition import estimate_nutrition

    patient = resolve_patient(db, cu, patient_id)

    # Persist image first so even if AI fails, we keep the photo
    image_file = await save_upload_to_db(
        db, image,
        owner_id=patient.id,
        uploaded_by=cu.id,
        purpose="MEAL_IMAGE",
        max_mb=settings.MAX_IMAGE_SIZE_MB,
    )
    db.commit()

    try:
        result = await estimate_nutrition(image_file.content, image_file.mime_type)
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"Nutrition estimation failed: {str(e)}")

    return MealNutritionEstimate(
        description=result["description"],
        calories=result["calories"],
        protein_g=result["protein_g"],
        carbs_g=result["carbs_g"],
        fat_g=result["fat_g"],
        confidence=result["confidence"],
        ai_provider=result["ai_provider"],
        ai_fallback_used=result["ai_fallback_used"],
        ai_fallback_reason=result["ai_fallback_reason"],
        image_file_id=image_file.id,
        raw_response=result.get("raw_response"),
    )


@router.get("/meals/summary", response_model=NutritionDailySummary)
def daily_summary(
    patient_id: Optional[str] = Query(None),
    date: Optional[str] = Query(None, description="YYYY-MM-DD; defaults to today"),
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    patient = resolve_patient(db, cu, patient_id)
    if date:
        try:
            day_start = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid date format")
    else:
        day_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    rows = (db.query(MealLog)
              .filter(MealLog.user_id == patient.id,
                      MealLog.eaten_at >= day_start,
                      MealLog.eaten_at < day_end)
              .all())
    return NutritionDailySummary(
        date=day_start.strftime("%Y-%m-%d"),
        total_calories=sum(r.calories or 0 for r in rows),
        total_protein_g=sum(r.protein_g or 0 for r in rows),
        total_carbs_g=sum(r.carbs_g or 0 for r in rows),
        total_fat_g=sum(r.fat_g or 0 for r in rows),
        meal_count=len(rows),
    )
