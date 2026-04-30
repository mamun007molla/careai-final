"""Helper used by Module 1 endpoints to resolve which patient a record is for.

Rules:
- ELDERLY user logging for themselves → patient = self.
- FAMILY/DOCTOR providing patient_id → must be linked to that patient, else 403.
- FAMILY/DOCTOR omitting patient_id → 400 "patient_id is required".
"""
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.user import PatientLink, User


def resolve_patient(
    db: Session,
    current_user: User,
    patient_id: Optional[str],
) -> User:
    """Return the User whose record is being created/queried."""
    if current_user.role == "ELDERLY":
        if patient_id and patient_id != current_user.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "Patients can only manage their own records")
        return current_user

    # FAMILY or DOCTOR
    if not patient_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "patient_id is required when acting on behalf of a patient")

    patient = db.query(User).filter(User.id == patient_id, User.role == "ELDERLY").first()
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")

    link = db.query(PatientLink).filter(
        PatientLink.patient_id == patient.id,
        PatientLink.linked_id == current_user.id,
    ).first()
    if not link:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "You are not linked to this patient")
    return patient


def resolve_patient_doctor_only(
    db: Session,
    current_user: User,
    patient_id: Optional[str],
) -> User:
    """Like resolve_patient, but blocks FAMILY entirely.

    Used for mental health data (mood logs, recommendations) where family
    visibility was explicitly excluded by user decision. Patient sees their
    own data; only linked DOCTORS can act on behalf of the patient.
    """
    if current_user.role == "ELDERLY":
        if patient_id and patient_id != current_user.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "Patients can only manage their own records")
        return current_user

    if current_user.role == "FAMILY":
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Family members cannot view mental health data")

    # DOCTOR
    if not patient_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "patient_id is required when acting on behalf of a patient")

    patient = db.query(User).filter(User.id == patient_id, User.role == "ELDERLY").first()
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")

    link = db.query(PatientLink).filter(
        PatientLink.patient_id == patient.id,
        PatientLink.linked_id == current_user.id,
    ).first()
    if not link:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "You are not linked to this patient")
    return patient
