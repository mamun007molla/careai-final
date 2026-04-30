"""Background reminder scheduler.

Every REMINDER_CHECK_INTERVAL seconds, this loop:
1. Reads current local time as HH:MM and weekday (MON, TUE, ...)
2. Finds all active routines whose scheduled_at == current HH:MM and
   `days` array contains today's weekday
3. For each match: creates a notification for the patient (and optionally
   for any linked family) — but ONLY ONCE per minute per routine (idempotent).

Also runs a DAILY job at 8 PM that generates family digest reports for
every patient with linked caregivers.

The "fire once" guard works by checking if a notification of the same type
+ same routine title was already created in the past 90 seconds. This is
imperfect (no dedicated "sent_for_routines" table) but pragmatic for v1.

Runs in-process via APScheduler. For production multi-worker deploys,
move to a distributed scheduler (Celery beat, GitHub Actions cron, etc.).
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.notifications import create_notification
from app.models.family_emergency import FamilyDigest
from app.models.health import MealLog
from app.models.medication_intake import MedicationIntakeLog
from app.models.mental_health import MoodLog
from app.models.notification import Notification
from app.models.physical import ActivityLog, FallDetectionLog, Routine
from app.models.user import PatientLink, User


log = logging.getLogger("careai.scheduler")

_scheduler: Optional[AsyncIOScheduler] = None

# Maps routine.type → notification type
_TYPE_TO_NOTIF = {
    "medication": "MEDICATION_REMINDER",
    "meal":       "MEAL_REMINDER",
    "exercise":   "EXERCISE_REMINDER",
    "other":      "GENERIC_REMINDER",
}

WEEKDAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def _check_reminders():
    """Scheduler tick — find matching routines and fire notifications."""
    db = SessionLocal()
    try:
        now = datetime.now()
        current_hhmm = now.strftime("%H:%M")
        today = WEEKDAYS[now.weekday()]

        # Routines that match exactly on time + day, active only
        candidates = (db.query(Routine)
                        .filter(Routine.scheduled_at == current_hhmm,
                                Routine.is_active == True)
                        .all())

        if not candidates:
            return

        # Dedup window: don't fire the same reminder twice within 90s
        dedup_since = now - timedelta(seconds=90)

        for r in candidates:
            try:
                days = json.loads(r.days) if r.days else []
            except Exception:
                days = []
            if today not in days:
                continue

            notif_type = _TYPE_TO_NOTIF.get(r.type, "GENERIC_REMINDER")

            # Already fired in dedup window?
            already = (db.query(Notification)
                         .filter(and_(
                             Notification.user_id == r.user_id,
                             Notification.type == notif_type,
                             Notification.title.like(f"%{r.title}%"),
                             Notification.created_at >= dedup_since,
                         ))
                         .first())
            if already:
                continue

            # Fire to the patient
            title = f"⏰ Time for: {r.title}"
            body = (f"Scheduled at {r.scheduled_at}. "
                    + (r.notes or "Don't forget!"))

            create_notification(
                db, user_id=r.user_id, type_=notif_type,
                title=title, body=body, link="/physical/routine-schedule",
                send_email=True,
            )

            # Also notify all linked family members (caregivers)
            family_links = (db.query(PatientLink)
                              .filter(PatientLink.patient_id == r.user_id,
                                      PatientLink.role == "FAMILY")
                              .all())
            for link in family_links:
                family_title = f"⏰ {_patient_name(db, r.user_id)}: {r.title}"
                create_notification(
                    db, user_id=link.linked_id, type_=notif_type,
                    title=family_title, body=body,
                    link="/physical/routine-schedule",
                    send_email=False,  # don't double-spam — patient already gets email
                )

            log.info("Fired reminder: %s for user=%s", r.title, r.user_id)

        db.commit()
    except Exception as e:
        log.exception("Reminder check failed: %s", e)
        db.rollback()
    finally:
        db.close()


def _patient_name(db, patient_id: str) -> str:
    u = db.query(User).filter(User.id == patient_id).first()
    return u.name if u else "Patient"


def start_scheduler():
    """Called once on app startup."""
    global _scheduler
    if not settings.ENABLE_REMINDER_SCHEDULER:
        log.info("Reminder scheduler disabled via settings")
        return
    if _scheduler:
        return  # already running

    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        _check_reminders, "interval",
        seconds=settings.REMINDER_CHECK_INTERVAL,
        id="reminder_check", max_instances=1, coalesce=True,
    )
    # Daily family digest at 8 PM
    _scheduler.add_job(
        _generate_daily_digests, "cron",
        hour=20, minute=0,
        id="family_digest", max_instances=1, coalesce=True,
    )
    _scheduler.start()
    log.info("Reminder scheduler started (interval=%ds, digest at 8 PM)",
             settings.REMINDER_CHECK_INTERVAL)


def _generate_daily_digests():
    """Run every patient through digest generation."""
    db = SessionLocal()
    try:
        # Patients who have at least one linked contact
        patient_ids = [r[0] for r in db.query(PatientLink.patient_id).distinct().all()]
        log.info("Generating digests for %d patients", len(patient_ids))
        for pid in patient_ids:
            try:
                generate_digest_for_patient(db, pid, send_email=True)
            except Exception as e:
                log.exception("Digest failed for %s: %s", pid, e)
        db.commit()
    finally:
        db.close()


def generate_digest_for_patient(db: Session, patient_id: str,
                                 send_email: bool = True) -> FamilyDigest:
    """Build a digest for the patient's day, persist + notify family.

    Returns the created FamilyDigest row (already added to db, not committed).
    """
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today + timedelta(days=1)

    patient = db.query(User).filter(User.id == patient_id).first()
    if not patient:
        raise ValueError(f"Patient {patient_id} not found")

    # Aggregate
    activities = (db.query(ActivityLog)
                    .filter(ActivityLog.user_id == patient_id,
                            ActivityLog.logged_at >= today,
                            ActivityLog.logged_at < tomorrow)
                    .all())

    intakes_today = (db.query(MedicationIntakeLog)
                       .filter(MedicationIntakeLog.user_id == patient_id,
                               MedicationIntakeLog.taken_at >= today,
                               MedicationIntakeLog.taken_at < tomorrow)
                       .all())

    # Total expected medications today
    today_weekday = ["MON","TUE","WED","THU","FRI","SAT","SUN"][datetime.utcnow().weekday()]
    routines = (db.query(Routine)
                  .filter(Routine.user_id == patient_id,
                          Routine.type == "medication",
                          Routine.is_active == True)
                  .all())
    expected_meds = 0
    for r in routines:
        try:
            days = json.loads(r.days) if r.days else []
            if today_weekday in days:
                expected_meds += 1
        except Exception:
            pass

    moods = (db.query(MoodLog)
               .filter(MoodLog.user_id == patient_id,
                       MoodLog.logged_at >= today,
                       MoodLog.logged_at < tomorrow)
               .all())
    avg_mood = sum(m.mood for m in moods) / len(moods) if moods else None

    falls_today = (db.query(FallDetectionLog)
                     .filter(FallDetectionLog.user_id == patient_id,
                             FallDetectionLog.detected_at >= today,
                             FallDetectionLog.fall_detected == True)
                     .count())

    meals = (db.query(MealLog)
               .filter(MealLog.user_id == patient_id,
                       MealLog.eaten_at >= today,
                       MealLog.eaten_at < tomorrow)
               .all())

    # Build text body
    lines = [f"# Daily update for {patient.name}",
             f"## {today.strftime('%A, %B %d')}", ""]

    if activities:
        types = {}
        for a in activities:
            types[a.type] = types.get(a.type, 0) + 1
        types_str = ", ".join(f"{v} {k}" for k, v in types.items())
        lines.append(f"**Activities:** {len(activities)} logged ({types_str})")
    else:
        lines.append("**Activities:** None logged today")

    if expected_meds:
        pct = round(len(intakes_today) / expected_meds * 100) if expected_meds else 0
        lines.append(f"**Medications:** {len(intakes_today)} of {expected_meds} taken ({pct}%)")
    elif intakes_today:
        lines.append(f"**Medications:** {len(intakes_today)} taken (no scheduled routine)")
    else:
        lines.append("**Medications:** No scheduled medications today")

    if meals:
        lines.append(f"**Meals:** {len(meals)} logged")

    if avg_mood is not None:
        emoji = "😊" if avg_mood >= 4 else "😐" if avg_mood >= 3 else "🙁"
        lines.append(f"**Mood:** Average {avg_mood:.1f}/5 {emoji}")

    if falls_today:
        lines.append(f"**⚠️ Fall alerts:** {falls_today} today — please check in")

    lines.append("")
    lines.append("_Generated automatically by CareAI._")

    body_text = "\n".join(lines)

    # Find existing digest for today (idempotent)
    period_str = today.strftime("%Y-%m-%d")
    existing = (db.query(FamilyDigest)
                  .filter(FamilyDigest.patient_id == patient_id,
                          FamilyDigest.period_date == period_str)
                  .first())
    if existing:
        # Update in place
        digest = existing
        digest.activities_count = len(activities)
        digest.medications_taken = len(intakes_today)
        digest.medications_total = expected_meds
        digest.avg_mood = avg_mood
        digest.fall_alerts = falls_today
        digest.body_text = body_text
        digest.status = "PENDING"
    else:
        digest = FamilyDigest(
            patient_id=patient_id,
            period_date=period_str,
            activities_count=len(activities),
            medications_taken=len(intakes_today),
            medications_total=expected_meds,
            avg_mood=avg_mood,
            fall_alerts=falls_today,
            body_text=body_text,
            status="PENDING",
        )
        db.add(digest)
    db.flush()

    # Notify all linked family members (not doctors — too noisy)
    family = (db.query(PatientLink)
                .filter(PatientLink.patient_id == patient_id,
                        PatientLink.role == "FAMILY")
                .all())
    for link in family:
        create_notification(
            db, user_id=link.linked_id, type_="INFO",
            title=f"📰 Daily update: {patient.name}",
            body=f"{len(activities)} activities · {len(intakes_today)}/{expected_meds} meds · " +
                 (f"mood {avg_mood:.1f}/5" if avg_mood else "no mood log"),
            link="/family/digests",
            send_email=send_email,
        )

    digest.status = "SENT" if family else "PENDING"
    digest.recipients_count = len(family)
    digest.sent_at = datetime.utcnow() if family else None
    return digest


def stop_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("Reminder scheduler stopped")
