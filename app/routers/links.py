"""Patient ↔ Caregiver/Doctor linking.

A patient can be linked to MANY family members AND MANY doctors.
Either side can initiate the link by entering the other's email.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.notifications import create_notification
from app.models.user import PatientLink, User
from app.schemas import LinkCreateRequest, LinkedPersonOut, LinkOut


router = APIRouter(prefix="/links", tags=["Patient Links"])


# ─── Caregiver / Doctor → links to a patient by email ─────────────────────────
@router.post("/to-patient", response_model=LinkOut, status_code=status.HTTP_201_CREATED)
def link_to_patient(
    body: LinkCreateRequest,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    if cu.role not in ("FAMILY", "DOCTOR"):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Only FAMILY or DOCTOR users can link to a patient.")

    patient = db.query(User).filter(
        User.email == body.patient_email.lower().strip(),
        User.role == "ELDERLY",
    ).first()
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No elderly patient with this email")

    existing = db.query(PatientLink).filter(
        PatientLink.patient_id == patient.id,
        PatientLink.linked_id == cu.id,
    ).first()
    if existing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Already linked to {patient.name}")

    # If is_primary is set and the user is a DOCTOR, demote any other primary
    if body.is_primary and cu.role == "DOCTOR":
        db.query(PatientLink).filter(
            PatientLink.patient_id == patient.id,
            PatientLink.role == "DOCTOR",
            PatientLink.is_primary == True,
        ).update({"is_primary": False})

    link = PatientLink(
        patient_id=patient.id,
        linked_id=cu.id,
        role=cu.role,
        relation=body.relation,
        is_primary=body.is_primary,
        notes=body.notes,
    )
    db.add(link)
    db.flush()

    # Notify the patient that someone connected to them
    role_label = "Doctor" if cu.role == "DOCTOR" else "Family member"
    detail = cu.specialty if cu.role == "DOCTOR" and cu.specialty else (body.relation or "")
    create_notification(
        db, user_id=patient.id, type_="CONNECTION_ADDED",
        title=f"🔗 {cu.name} connected to you",
        body=f"{role_label}{' (' + detail + ')' if detail else ''} is now linked to your CareAI account.",
        link="/settings",
        source_user=cu,
    )

    db.commit()
    db.refresh(link)
    return link


# ─── Patient → list everyone linked to them ───────────────────────────────────
@router.get("/my-doctors", response_model=list[LinkedPersonOut])
def my_doctors(db: Session = Depends(get_db), cu: User = Depends(get_current_user)):
    return _list_linked(db, patient_id=cu.id, role_filter="DOCTOR")


@router.get("/my-family", response_model=list[LinkedPersonOut])
def my_family(db: Session = Depends(get_db), cu: User = Depends(get_current_user)):
    return _list_linked(db, patient_id=cu.id, role_filter="FAMILY")


# ─── Doctor / Family → list their patients ────────────────────────────────────
@router.get("/my-patients", response_model=list[LinkedPersonOut])
def my_patients(db: Session = Depends(get_db), cu: User = Depends(get_current_user)):
    if cu.role == "ELDERLY":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Patients use /my-doctors and /my-family")
    rows = db.query(PatientLink).filter(PatientLink.linked_id == cu.id).all()
    out: list[LinkedPersonOut] = []
    for r in rows:
        p = db.query(User).filter(User.id == r.patient_id).first()
        if p:
            out.append(LinkedPersonOut(
                link_id=r.id, user_id=p.id, name=p.name, email=p.email,
                phone=p.phone, role=p.role, relation=r.relation,
                is_primary=r.is_primary, specialty=p.specialty, notes=r.notes,
            ))
    return out


def _list_linked(db: Session, *, patient_id: str, role_filter: str) -> list[LinkedPersonOut]:
    rows = db.query(PatientLink).filter(
        PatientLink.patient_id == patient_id,
        PatientLink.role == role_filter,
    ).all()
    out: list[LinkedPersonOut] = []
    for r in rows:
        u = db.query(User).filter(User.id == r.linked_id).first()
        if u:
            out.append(LinkedPersonOut(
                link_id=r.id, user_id=u.id, name=u.name, email=u.email,
                phone=u.phone, role=u.role, relation=r.relation,
                is_primary=r.is_primary, specialty=u.specialty, notes=r.notes,
            ))
    return out


@router.patch("/{link_id}/set-primary", response_model=LinkOut)
def set_primary(
    link_id: str,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    link = db.query(PatientLink).filter(PatientLink.id == link_id).first()
    if not link:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link not found")
    # Only the patient themselves can change their primary doctor
    if cu.id != link.patient_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Only the patient can designate their primary doctor")
    if link.role != "DOCTOR":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only doctor links can be primary")

    db.query(PatientLink).filter(
        PatientLink.patient_id == link.patient_id,
        PatientLink.role == "DOCTOR",
    ).update({"is_primary": False})

    link.is_primary = True
    db.commit()
    db.refresh(link)
    return link


@router.delete("/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
def unlink(
    link_id: str,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    link = db.query(PatientLink).filter(PatientLink.id == link_id).first()
    if not link:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link not found")
    # Either side of the link can dissolve it
    if cu.id not in (link.patient_id, link.linked_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your link")
    db.delete(link)
    db.commit()
