"""Standalone prescriptions — uploaded directly by patient/family.

These are prescriptions from external doctors not in the CareAI system.
The patient or a linked family member uploads the prescription as a PDF
or text; doctors linked to the patient can also view them.

Distinct from `VisitAttachment(kind=PRESCRIPTION)` which is created by an
on-platform doctor during a visit.
"""
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user, stamp_audit
from app.core.patient_access import resolve_patient
from app.routers.files import save_upload_to_db
from app.models.file import File as FileModel
from app.models.health import Prescription
from app.models.user import User
from app.schemas import (
    StandalonePrescriptionCreate, StandalonePrescriptionOut,
)


router = APIRouter(prefix="/uploaded-prescriptions",
                   tags=["Standalone Prescriptions"])


def _serialize(db: Session, p: Prescription) -> StandalonePrescriptionOut:
    file_obj = None
    if p.file_id:
        file_obj = db.query(FileModel).filter(FileModel.id == p.file_id).first()
    return StandalonePrescriptionOut.from_orm_with_audit(p, file_obj)


# ────────────────────────────────────────────────────────────────────────────
# Create — multipart with optional file
# ────────────────────────────────────────────────────────────────────────────
@router.post("", response_model=StandalonePrescriptionOut, status_code=status.HTTP_201_CREATED)
async def upload_prescription(
    # Form fields
    doctor_name:       Optional[str] = Form(None),
    doctor_specialty:  Optional[str] = Form(None),
    clinic_name:       Optional[str] = Form(None),
    prescription_text: Optional[str] = Form(None),
    issued_at:         Optional[str] = Form(None),  # ISO datetime string
    notes:             Optional[str] = Form(None),
    patient_id:        Optional[str] = Form(None),
    # Optional file (PDF or image)
    file:              Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Upload a prescription with text and/or attached file.

    Permissions:
    - Patient: uploads to own record (no patient_id needed)
    - Family: uploads on behalf of a linked patient (patient_id required)
    - Doctor: uploads on behalf of a linked patient (patient_id required)
    """
    patient = resolve_patient(db, cu, patient_id)

    # Must have either text or file
    has_text = prescription_text and prescription_text.strip()
    if not has_text and not file:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Provide prescription text, an attached file, or both",
        )

    # Save file if present
    file_id = None
    if file and file.filename:
        saved = await save_upload_to_db(
            db, file,
            owner_id=patient.id,
            uploaded_by=cu.id,
            purpose="PRESCRIPTION",
            max_mb=settings.MAX_FILE_SIZE_MB,
        )
        db.flush()
        file_id = saved.id

    # Parse issued_at
    issued_at_dt = None
    if issued_at:
        try:
            from datetime import datetime
            issued_at_dt = datetime.fromisoformat(issued_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "Invalid issued_at — use ISO format")

    p = Prescription(
        patient_id=patient.id,
        doctor_name=(doctor_name or "").strip() or None,
        doctor_specialty=(doctor_specialty or "").strip() or None,
        clinic_name=(clinic_name or "").strip() or None,
        prescription_text=(prescription_text or "").strip() or None,
        file_id=file_id,
        issued_at=issued_at_dt,
        notes=(notes or "").strip() or None,
    )
    stamp_audit(p, cu)
    db.add(p)
    db.commit()
    db.refresh(p)
    return _serialize(db, p)


# ────────────────────────────────────────────────────────────────────────────
# List — patient + linked users see all prescriptions for that patient
# ────────────────────────────────────────────────────────────────────────────
@router.get("", response_model=list[StandalonePrescriptionOut])
def list_prescriptions(
    patient_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    patient = resolve_patient(db, cu, patient_id)
    rows = (db.query(Prescription)
              .filter(Prescription.patient_id == patient.id)
              .order_by(desc(Prescription.issued_at), desc(Prescription.created_at))
              .limit(limit).all())
    return [_serialize(db, p) for p in rows]


# ────────────────────────────────────────────────────────────────────────────
# Get one
# ────────────────────────────────────────────────────────────────────────────
@router.get("/{pid}", response_model=StandalonePrescriptionOut)
def get_prescription(
    pid: str,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    p = db.query(Prescription).filter(Prescription.id == pid).first()
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Prescription not found")
    resolve_patient(db, cu, p.patient_id)  # access check
    return _serialize(db, p)


# ────────────────────────────────────────────────────────────────────────────
# Delete (creator or patient only)
# ────────────────────────────────────────────────────────────────────────────
@router.delete("/{pid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_prescription(
    pid: str,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    p = db.query(Prescription).filter(Prescription.id == pid).first()
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Prescription not found")

    # Only creator or patient themselves can delete
    if cu.id != p.created_by_user_id and cu.id != p.patient_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only the uploader or the patient can delete a prescription",
        )

    db.delete(p)
    db.commit()
