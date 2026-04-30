"""Module 5 — Family Engagement & Emergency endpoints.

Three areas:
1. SOS — patient triggers emergency, all linked contacts notified
2. Caregiver messaging — group chat per patient
3. Family digests — auto-generated daily, viewable by patient + linked

Privacy:
- SOS: patient creates; everyone linked + patient can view & resolve
- Messages: patient + everyone linked (group chat)
- Digests: patient + linked
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user, require_roles
from app.core.notifications import create_notification
from app.core.patient_access import resolve_patient
from app.models.family_emergency import (
    CaregiverMessage, CaregiverThread, FamilyDigest, SOSAlert,
)
from app.models.user import PatientLink, User
from app.schemas import (
    CaregiverMessageCreate, CaregiverMessageOut, CaregiverThreadOut,
    FamilyDigestOut, SOSAlertOut, SOSCreate, SOSResolve, SOSStatus,
)


log = logging.getLogger("careai.family")
router = APIRouter(prefix="/family", tags=["Module 5 — Family & Emergency"])


# ════════════════════════════════════════════════════════════════════════════
# SOS Alerts
# ════════════════════════════════════════════════════════════════════════════
@router.post("/sos", response_model=SOSAlertOut, status_code=status.HTTP_201_CREATED)
def trigger_sos(
    body: SOSCreate,
    db: Session = Depends(get_db),
    cu: User = Depends(require_roles("ELDERLY")),
):
    """Patient triggers SOS — notifies ALL linked contacts immediately."""
    sos = SOSAlert(
        patient_id=cu.id,
        message=body.message,
        latitude=body.latitude,
        longitude=body.longitude,
        location_text=body.location_text,
        status="ACTIVE",
    )
    db.add(sos)
    db.flush()

    # Notify ALL linked contacts
    contacts = (db.query(PatientLink)
                  .filter(PatientLink.patient_id == cu.id)
                  .all())

    body_text = f"{cu.name} has triggered an emergency alert."
    if body.message:
        body_text += f"\n\nMessage: {body.message}"
    if body.location_text:
        body_text += f"\n\nLocation: {body.location_text}"
    if body.latitude and body.longitude:
        body_text += f"\n\nMap: https://www.google.com/maps?q={body.latitude},{body.longitude}"

    for link in contacts:
        create_notification(
            db, user_id=link.linked_id, type_="FALL_DETECTED",  # reuse type for high urgency
            title=f"🚨 EMERGENCY: {cu.name}",
            body=body_text, link=f"/family/sos/{sos.id}",
            source_user=cu,
        )
    sos.notified_count = len(contacts)

    # SMS via Twilio (best-effort, async-ish via fire-and-forget)
    sms_sent = False
    sms_error = None
    if settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN and settings.TWILIO_PHONE_NUMBER:
        try:
            from twilio.rest import Client as TwilioClient
            client = TwilioClient(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
            sms_body = f"CareAI EMERGENCY: {cu.name} triggered SOS. Open the app for details."
            recipient_users = (db.query(User)
                                 .join(PatientLink, PatientLink.linked_id == User.id)
                                 .filter(PatientLink.patient_id == cu.id,
                                         User.phone.isnot(None))
                                 .all())
            sent_any = False
            for u in recipient_users:
                phone = u.phone
                # Bangladesh format → +880 prefix
                if phone.startswith("01"):
                    phone = "+880" + phone[1:]
                try:
                    client.messages.create(
                        body=sms_body, from_=settings.TWILIO_PHONE_NUMBER, to=phone,
                    )
                    sent_any = True
                except Exception as e:
                    log.warning("SMS to %s failed: %s", phone, e)
            sms_sent = sent_any
        except Exception as e:
            sms_error = str(e)[:500]
            log.warning("Twilio init failed: %s", e)
    sos.sms_sent = sms_sent
    sos.sms_failed_reason = sms_error

    db.commit()
    db.refresh(sos)
    return _serialize_sos(db, sos)


@router.get("/sos", response_model=list[SOSAlertOut])
def list_sos(
    patient_id: Optional[str] = Query(None),
    status_filter: Optional[SOSStatus] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """List SOS alerts visible to current user.

    - Patient: their own alerts
    - Doctor/Family: alerts from any linked patient
    """
    q = db.query(SOSAlert)
    if cu.role == "ELDERLY":
        q = q.filter(SOSAlert.patient_id == cu.id)
    else:
        if patient_id:
            resolve_patient(db, cu, patient_id)
            q = q.filter(SOSAlert.patient_id == patient_id)
        else:
            # All patients caregiver is linked to
            linked_ids = [r[0] for r in db.query(PatientLink.patient_id)
                                          .filter(PatientLink.linked_id == cu.id).all()]
            q = q.filter(SOSAlert.patient_id.in_(linked_ids))
    if status_filter:
        q = q.filter(SOSAlert.status == status_filter.value)
    rows = q.order_by(desc(SOSAlert.triggered_at)).limit(limit).all()
    return [_serialize_sos(db, s) for s in rows]


@router.get("/sos/{sos_id}", response_model=SOSAlertOut)
def get_sos(
    sos_id: str,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    s = db.query(SOSAlert).filter(SOSAlert.id == sos_id).first()
    if not s:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "SOS alert not found")
    _ensure_sos_access(db, s, cu)
    return _serialize_sos(db, s)


@router.post("/sos/{sos_id}/acknowledge", response_model=SOSAlertOut)
def acknowledge_sos(
    sos_id: str,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """A linked contact (or patient themselves) acknowledges they're handling it."""
    s = db.query(SOSAlert).filter(SOSAlert.id == sos_id).first()
    if not s:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "SOS alert not found")
    _ensure_sos_access(db, s, cu)

    if s.status == "ACTIVE":
        s.status = "ACKNOWLEDGED"
        s.acknowledged_by_user_id = cu.id
        s.acknowledged_by_name = cu.name
        s.acknowledged_at = datetime.utcnow()

        # Notify the patient
        if cu.id != s.patient_id:
            create_notification(
                db, user_id=s.patient_id, type_="INFO",
                title=f"✅ {cu.name} is responding",
                body=f"Your SOS alert was acknowledged. Help is on the way.",
                link=f"/family/sos/{s.id}",
                source_user=cu, send_email=False,
            )
    db.commit()
    db.refresh(s)
    return _serialize_sos(db, s)


@router.post("/sos/{sos_id}/resolve", response_model=SOSAlertOut)
def resolve_sos(
    sos_id: str,
    body: SOSResolve,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Mark the alert resolved. Anyone with access can resolve."""
    s = db.query(SOSAlert).filter(SOSAlert.id == sos_id).first()
    if not s:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "SOS alert not found")
    _ensure_sos_access(db, s, cu)
    s.status = "FALSE_ALARM" if body.false_alarm else "RESOLVED"
    s.resolved_at = datetime.utcnow()
    s.resolution_note = body.note
    db.commit()
    db.refresh(s)
    return _serialize_sos(db, s)


# ── SOS helpers ──────────────────────────────────────────────────────────────
def _ensure_sos_access(db: Session, sos: SOSAlert, cu: User):
    if cu.id == sos.patient_id:
        return
    link = db.query(PatientLink).filter(
        PatientLink.patient_id == sos.patient_id,
        PatientLink.linked_id == cu.id,
    ).first()
    if not link:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "You are not linked to this patient")


def _serialize_sos(db: Session, s: SOSAlert) -> SOSAlertOut:
    pat = db.query(User).filter(User.id == s.patient_id).first()
    return SOSAlertOut(
        id=s.id, patient_id=s.patient_id,
        patient_name=pat.name if pat else None,
        message=s.message,
        latitude=s.latitude, longitude=s.longitude, location_text=s.location_text,
        status=s.status, triggered_at=s.triggered_at,
        acknowledged_by_name=s.acknowledged_by_name,
        acknowledged_at=s.acknowledged_at,
        resolved_at=s.resolved_at, resolution_note=s.resolution_note,
        notified_count=s.notified_count or 0,
        sms_sent=s.sms_sent or False,
    )


# ════════════════════════════════════════════════════════════════════════════
# Caregiver Messaging — group chat per patient
# ════════════════════════════════════════════════════════════════════════════
def _get_or_create_thread(db: Session, patient_id: str) -> CaregiverThread:
    t = db.query(CaregiverThread).filter(
        CaregiverThread.patient_id == patient_id
    ).first()
    if not t:
        t = CaregiverThread(patient_id=patient_id)
        db.add(t)
        db.flush()
    return t


def _ensure_thread_access(db: Session, patient_id: str, cu: User):
    """Patient or any linked contact can post/read."""
    if cu.id == patient_id:
        return
    link = db.query(PatientLink).filter(
        PatientLink.patient_id == patient_id,
        PatientLink.linked_id == cu.id,
    ).first()
    if not link:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "You are not in this caregiver thread")


@router.get("/threads", response_model=list[CaregiverThreadOut])
def list_threads(
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """List all threads the current user has access to.

    - Patient: their own thread (auto-created)
    - Caregiver: one thread per linked patient (auto-created)
    """
    # Find all patient_ids the user has access to
    if cu.role == "ELDERLY":
        accessible_pids = [cu.id]
    else:
        accessible_pids = [r[0] for r in db.query(PatientLink.patient_id)
                                          .filter(PatientLink.linked_id == cu.id).all()]

    # Auto-create thread for each accessible patient that doesn't have one yet
    for pid in accessible_pids:
        existing = db.query(CaregiverThread).filter(
            CaregiverThread.patient_id == pid).first()
        if not existing:
            db.add(CaregiverThread(patient_id=pid))
    if accessible_pids:
        db.commit()

    # Now fetch all threads
    if not accessible_pids:
        return []
    threads = (db.query(CaregiverThread)
                 .filter(CaregiverThread.patient_id.in_(accessible_pids))
                 .all())

    out = []
    for t in threads:
        pat = db.query(User).filter(User.id == t.patient_id).first()
        member_count = 1 + (db.query(func.count(PatientLink.id))
                              .filter(PatientLink.patient_id == t.patient_id)
                              .scalar() or 0)
        out.append(CaregiverThreadOut(
            id=t.id, patient_id=t.patient_id,
            patient_name=pat.name if pat else None,
            title=t.title or (f"{pat.name}'s family chat" if pat else "Care thread"),
            created_at=t.created_at, last_message_at=t.last_message_at,
            member_count=member_count,
        ))
    out.sort(key=lambda x: x.last_message_at or x.created_at, reverse=True)
    return out


@router.get("/threads/{patient_id}/messages", response_model=list[CaregiverMessageOut])
def list_messages(
    patient_id: str,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    _ensure_thread_access(db, patient_id, cu)
    t = _get_or_create_thread(db, patient_id)
    db.commit()
    rows = (db.query(CaregiverMessage)
              .filter(CaregiverMessage.thread_id == t.id)
              .order_by(CaregiverMessage.created_at.asc())
              .limit(limit).all())
    return rows


@router.post("/threads/{patient_id}/messages", response_model=CaregiverMessageOut, status_code=status.HTTP_201_CREATED)
def send_message(
    patient_id: str,
    body: CaregiverMessageCreate,
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    _ensure_thread_access(db, patient_id, cu)
    t = _get_or_create_thread(db, patient_id)
    m = CaregiverMessage(
        thread_id=t.id, sender_id=cu.id,
        sender_name=cu.name, sender_role=cu.role,
        content=body.content.strip(),
    )
    db.add(m)
    t.last_message_at = datetime.utcnow()

    # Notify all OTHER members of the thread
    if cu.id != patient_id:
        create_notification(
            db, user_id=patient_id, type_="INFO",
            title=f"💬 {cu.name} sent a message",
            body=body.content[:200],
            link=f"/family/messages",
            source_user=cu, send_email=False,
        )
    # Also notify other linked contacts
    others = (db.query(PatientLink)
                .filter(PatientLink.patient_id == patient_id,
                        PatientLink.linked_id != cu.id)
                .all())
    for link in others:
        create_notification(
            db, user_id=link.linked_id, type_="INFO",
            title=f"💬 {cu.name} sent a message",
            body=body.content[:200],
            link=f"/family/messages",
            source_user=cu, send_email=False,
        )

    db.commit()
    db.refresh(m)
    return m


# ════════════════════════════════════════════════════════════════════════════
# Family Digests
# ════════════════════════════════════════════════════════════════════════════
@router.get("/digests", response_model=list[FamilyDigestOut])
def list_digests(
    patient_id: Optional[str] = Query(None),
    limit: int = Query(30, ge=1, le=90),
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    if cu.role == "ELDERLY":
        # Patient sees their own
        q = db.query(FamilyDigest).filter(FamilyDigest.patient_id == cu.id)
    elif patient_id:
        # Caregiver requested specific patient
        resolve_patient(db, cu, patient_id)
        q = db.query(FamilyDigest).filter(FamilyDigest.patient_id == patient_id)
    else:
        # Caregiver, no patient specified — show across all linked patients
        linked_ids = [r[0] for r in db.query(PatientLink.patient_id)
                                      .filter(PatientLink.linked_id == cu.id).all()]
        if not linked_ids:
            return []
        q = db.query(FamilyDigest).filter(FamilyDigest.patient_id.in_(linked_ids))

    return (q.order_by(desc(FamilyDigest.created_at))
             .limit(limit).all())


@router.post("/digests/generate", response_model=FamilyDigestOut)
def generate_digest_now(
    patient_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """On-demand digest generation (also runs automatically nightly)."""
    from app.core.scheduler import generate_digest_for_patient
    if cu.role == "ELDERLY":
        target_id = cu.id
    else:
        if not patient_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "patient_id required")
        resolve_patient(db, cu, patient_id)
        target_id = patient_id

    digest = generate_digest_for_patient(db, target_id, send_email=False)
    db.commit()
    db.refresh(digest)
    return digest
