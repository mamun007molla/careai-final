"""Module 4 — Doctor Support endpoints.

ALL endpoints DOCTOR-only via require_roles("DOCTOR").

Endpoints:
  POST   /doctor/classify            upload image + clinical notes → AI predictions
  GET    /doctor/classify            list past classifications
  GET    /doctor/classify/{id}       get single classification
  PATCH  /doctor/classify/{id}       update doctor_diagnosis (override AI)
  DELETE /doctor/classify/{id}       delete

  POST   /doctor/summarize           text → AI summary
  GET    /doctor/summarize           list past summaries
  GET    /doctor/summarize/{id}      get single
  DELETE /doctor/summarize/{id}      delete
"""
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.ai.doctor_support import classify_disease, summarize_report
from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user, require_roles, stamp_audit
from app.core.patient_access import resolve_patient
from app.models.doctor_support import DiseaseClassification, ReportSummary
from app.models.user import User
from app.routers.files import save_upload_to_db
from app.schemas import (
    AuditOut, DiseaseClassificationOut, DiseasePrediction,
    DoctorDiagnosisUpdate, ImageCategory, ReportSummaryCreate, ReportSummaryOut,
    AbnormalValue,
)


log = logging.getLogger("careai.doctor")
router = APIRouter(prefix="/doctor", tags=["Module 4 — Doctor Support"])


def _ensure_record_access(db, cu, record_owner_user_id, patient_id):
    """User can access a doctor-support record if any of:
    - They created it (owner)
    - They are the patient it's about
    - They are linked to that patient (any role)
    """
    if cu.id == record_owner_user_id:
        return True
    if patient_id and cu.id == patient_id:
        return True
    if patient_id:
        from app.models.user import PatientLink
        link = db.query(PatientLink).filter(
            PatientLink.patient_id == patient_id,
            PatientLink.linked_id == cu.id,
        ).first()
        if link:
            return True
    raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this record")


# ════════════════════════════════════════════════════════════════════════════
# Helpers — serialize JSON-stored fields
# ════════════════════════════════════════════════════════════════════════════
def _serialize_classification(c: DiseaseClassification) -> DiseaseClassificationOut:
    preds = []
    if c.ai_predictions:
        try:
            raw = json.loads(c.ai_predictions)
            if isinstance(raw, list):
                preds = [DiseasePrediction(**p) for p in raw if isinstance(p, dict)]
        except Exception:
            preds = []
    return DiseaseClassificationOut(
        id=c.id, patient_id=c.patient_id, image_file_id=c.image_file_id,
        image_category=c.image_category,
        clinical_notes=c.clinical_notes,
        ai_predictions=preds,
        ai_summary=c.ai_summary, ai_recommendations=c.ai_recommendations,
        ai_provider=c.ai_provider, ai_fallback_used=c.ai_fallback_used or False,
        doctor_diagnosis=c.doctor_diagnosis,
        classified_at=c.classified_at,
        created_by=AuditOut(
            user_id=c.created_by_user_id, role=c.created_by_role,
            name=c.created_by_name,
        ),
    )


def _serialize_report(r: ReportSummary) -> ReportSummaryOut:
    def _parse_list(json_str):
        if not json_str:
            return []
        try:
            v = json.loads(json_str)
            return v if isinstance(v, list) else []
        except Exception:
            return []

    abnormals = []
    for a in _parse_list(r.abnormal_values):
        if isinstance(a, dict):
            try:
                abnormals.append(AbnormalValue(
                    name=a.get("name", ""),
                    value=str(a.get("value", "")),
                    ref_range=a.get("ref_range"),
                    flag=a.get("flag", "abnormal"),
                ))
            except Exception:
                continue

    return ReportSummaryOut(
        id=r.id, patient_id=r.patient_id, source_file_id=r.source_file_id,
        title=r.title, raw_text=r.raw_text,
        summary_text=r.summary_text,
        key_findings=[s for s in _parse_list(r.key_findings) if isinstance(s, str)],
        abnormal_values=abnormals,
        recommendations=[s for s in _parse_list(r.recommendations) if isinstance(s, str)],
        ai_provider=r.ai_provider, ai_fallback_used=r.ai_fallback_used or False,
        summarized_at=r.summarized_at,
        created_by=AuditOut(
            user_id=r.created_by_user_id, role=r.created_by_role,
            name=r.created_by_name,
        ),
    )


# ════════════════════════════════════════════════════════════════════════════
# Disease classification
# ════════════════════════════════════════════════════════════════════════════
@router.post("/classify", response_model=DiseaseClassificationOut, status_code=status.HTTP_201_CREATED)
async def classify(
    image: UploadFile = File(...),
    image_category: ImageCategory = Form(ImageCategory.GENERAL),
    patient_id: Optional[str] = Form(None),
    clinical_notes: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Doctor uploads image; AI returns predictions. Patient_id optional —
    doctor can run ad-hoc classification without linking to a specific patient."""
    # If patient_id provided, ensure linked
    if patient_id:
        resolve_patient(db, cu, patient_id)

    # Save image
    image_file = await save_upload_to_db(
        db, image, owner_id=patient_id or cu.id,
        uploaded_by=cu.id, purpose="OTHER",
        max_mb=settings.MAX_IMAGE_SIZE_MB,
    )
    db.flush()

    # Run AI classification
    try:
        result = await classify_disease(
            image_file.content, image_category.value,
            clinical_notes or "", image_file.mime_type,
        )
    except AIServiceError:
        raise   # Let global handler return clean 503
    except Exception as e:
        log.warning("Classification AI failed: %s", e)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"AI classification failed: {str(e)}")

    c = DiseaseClassification(
        patient_id=patient_id,
        image_file_id=image_file.id,
        image_category=image_category.value,
        clinical_notes=clinical_notes,
        ai_predictions=json.dumps(result["predictions"]),
        ai_summary=result["summary"],
        ai_recommendations=result["recommendations"],
        ai_provider=result["ai_provider"],
        ai_fallback_used=result["ai_fallback_used"],
    )
    stamp_audit(c, cu)
    db.add(c)
    db.commit()
    db.refresh(c)
    return _serialize_classification(c)


@router.get("/classify", response_model=list[DiseaseClassificationOut])
def list_classifications(
    patient_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    from app.models.user import PatientLink
    # Get patient_ids the current user is linked to (or is)
    accessible_patient_ids = [cu.id] if cu.role == "ELDERLY" else []
    accessible_patient_ids += [r[0] for r in db.query(PatientLink.patient_id)
                                              .filter(PatientLink.linked_id == cu.id).all()]

    q = db.query(DiseaseClassification).filter(
        # Owner OR record about a patient I have access to
        (DiseaseClassification.created_by_user_id == cu.id)
        | (DiseaseClassification.patient_id.in_(accessible_patient_ids) if accessible_patient_ids else False)
    )
    if patient_id:
        q = q.filter(DiseaseClassification.patient_id == patient_id)
    rows = q.order_by(desc(DiseaseClassification.classified_at)).limit(limit).all()
    return [_serialize_classification(c) for c in rows]


@router.get("/classify/{cid}", response_model=DiseaseClassificationOut)
def get_classification(
    cid: str,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    c = db.query(DiseaseClassification).filter(DiseaseClassification.id == cid).first()
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Classification not found")
    _ensure_record_access(db, cu, c.created_by_user_id, c.patient_id)
    return _serialize_classification(c)


@router.patch("/classify/{cid}", response_model=DiseaseClassificationOut)
def update_diagnosis(
    cid: str,
    body: DoctorDiagnosisUpdate,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    c = db.query(DiseaseClassification).filter(DiseaseClassification.id == cid).first()
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Classification not found")
    # Only DOCTOR can override AI diagnosis (clinical decision)
    if cu.role != "DOCTOR":
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Only doctors can record a clinical diagnosis")
    _ensure_record_access(db, cu, c.created_by_user_id, c.patient_id)
    c.doctor_diagnosis = body.doctor_diagnosis
    db.commit()
    db.refresh(c)
    return _serialize_classification(c)


@router.delete("/classify/{cid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_classification(
    cid: str,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    c = db.query(DiseaseClassification).filter(DiseaseClassification.id == cid).first()
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Classification not found")
    _ensure_record_access(db, cu, c.created_by_user_id, c.patient_id)
    db.delete(c)
    db.commit()


# ════════════════════════════════════════════════════════════════════════════
# Report summarization
# ════════════════════════════════════════════════════════════════════════════
@router.post("/summarize", response_model=ReportSummaryOut, status_code=status.HTTP_201_CREATED)
async def summarize(
    body: ReportSummaryCreate,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    if body.patient_id:
        resolve_patient(db, cu, body.patient_id)

    try:
        result = await summarize_report(body.text)
    except Exception as e:
        log.warning("Summarize AI failed: %s", e)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"AI summarization failed: {str(e)}")

    r = ReportSummary(
        patient_id=body.patient_id,
        source_file_id=body.source_file_id,
        title=body.title,
        raw_text=body.text,
        summary_text=result["summary_text"],
        key_findings=json.dumps(result["key_findings"]),
        abnormal_values=json.dumps(result["abnormal_values"]),
        recommendations=json.dumps(result["recommendations"]),
        ai_provider=result["ai_provider"],
        ai_fallback_used=result["ai_fallback_used"],
    )
    stamp_audit(r, cu)
    db.add(r)
    db.commit()
    db.refresh(r)
    return _serialize_report(r)


@router.get("/summarize", response_model=list[ReportSummaryOut])
def list_summaries(
    patient_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    from app.models.user import PatientLink
    accessible_patient_ids = [cu.id] if cu.role == "ELDERLY" else []
    accessible_patient_ids += [r[0] for r in db.query(PatientLink.patient_id)
                                              .filter(PatientLink.linked_id == cu.id).all()]

    q = db.query(ReportSummary).filter(
        (ReportSummary.created_by_user_id == cu.id)
        | (ReportSummary.patient_id.in_(accessible_patient_ids) if accessible_patient_ids else False)
    )
    if patient_id:
        q = q.filter(ReportSummary.patient_id == patient_id)
    rows = q.order_by(desc(ReportSummary.summarized_at)).limit(limit).all()
    return [_serialize_report(r) for r in rows]


@router.get("/summarize/{rid}", response_model=ReportSummaryOut)
def get_summary(
    rid: str,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    r = db.query(ReportSummary).filter(ReportSummary.id == rid).first()
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Summary not found")
    _ensure_record_access(db, cu, r.created_by_user_id, r.patient_id)
    return _serialize_report(r)


@router.delete("/summarize/{rid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_summary(
    rid: str,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    r = db.query(ReportSummary).filter(ReportSummary.id == rid).first()
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Summary not found")
    _ensure_record_access(db, cu, r.created_by_user_id, r.patient_id)
    db.delete(r)
    db.commit()


# ════════════════════════════════════════════════════════════════════════════
# Medical Visual Question Answering (VQA)
# ════════════════════════════════════════════════════════════════════════════
@router.post("/vqa", response_model=dict)
async def medical_vqa(
    image: UploadFile = File(...),
    question: str = Form(...),
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Medical visual Q&A — user uploads a medical image and asks a question.
    Open to all roles (patient, family, doctor).
    """
    if len(question.strip()) < 5:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Question must be at least 5 characters")

    image_bytes = await image.read()
    if len(image_bytes) > settings.MAX_IMAGE_SIZE_MB * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            "Image too large")

    from app.ai.ai_router import AIServiceError, run_vision

    role_label = {
        "ELDERLY": "elderly patient",
        "FAMILY":  "family caregiver",
        "DOCTOR":  "doctor",
    }.get(cu.role, "user")

    prompt = f"""You are assisting a {role_label} who is asking a question
about the medical image they have uploaded.

Their question: "{question.strip()}"

Provide a clear, factual answer based ONLY on what is visible in the image.

Important guidelines:
- NEVER claim diagnostic certainty — this is decision support only
- If the user is a patient or family member, suggest they consult a doctor
- If you can't see something clearly, say so honestly
- Use simple language for patients/family; clinical language is OK for doctors
- Respond in the same language as the question (English or Bangla)

Respond with ONLY this JSON:
{{
  "answer": "Your detailed answer here, 2-4 sentences",
  "observations": ["What you see in the image, observation 1", "observation 2"],
  "recommendations": ["What the user should do next", "..."],
  "confidence": "high" | "medium" | "low",
  "disclaimer": "Reminder text about consulting a doctor"
}}"""

    try:
        result = await run_vision(
            image_bytes, prompt,
            mime_type=image.content_type or "image/jpeg",
            json_response=True,
        )
    except AIServiceError:
        raise
    except Exception as e:
        log.warning("VQA AI failed: %s", e)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"AI vision service error: {str(e)}")

    data = result.data
    return {
        "answer": str(data.get("answer", ""))[:2000],
        "observations": [str(o)[:300] for o in (data.get("observations") or [])[:8]],
        "recommendations": [str(r)[:300] for r in (data.get("recommendations") or [])[:5]],
        "confidence": str(data.get("confidence", "medium")).lower(),
        "disclaimer": str(data.get("disclaimer", "This AI response is for informational purposes only. Always consult a qualified doctor.")),
        "ai_provider": result.provider,
        "ai_fallback_used": result.fallback_used,
    }
