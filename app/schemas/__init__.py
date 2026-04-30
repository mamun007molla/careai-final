"""Pydantic schemas — request/response shapes for the API.

Kept in one module on purpose: with ~25 schemas, splitting hurts more than it
helps because routers need to import from many groups. Sections are clearly
delimited so it stays readable.
"""
import json
from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


# ════════════════════════════════════════════════════════════════════════════
# Enums
# ════════════════════════════════════════════════════════════════════════════
class UserRole(str, Enum):
    ELDERLY = "ELDERLY"
    FAMILY  = "FAMILY"
    DOCTOR  = "DOCTOR"


class LinkRole(str, Enum):
    FAMILY = "FAMILY"
    DOCTOR = "DOCTOR"


# ════════════════════════════════════════════════════════════════════════════
# Auth
# ════════════════════════════════════════════════════════════════════════════
class RegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    # Bangladesh phone: 11 digits starting with 01[3-9]
    phone: Optional[str] = Field(
        None, pattern=r"^01[3-9]\d{8}$",
        description="Bangladesh format: 11 digits, 01XXXXXXXXX (e.g. 01712345678)",
    )
    password: str = Field(..., min_length=6, max_length=128)
    role: UserRole = UserRole.ELDERLY
    # doctor-only optional fields
    specialty: Optional[str] = None
    license_no: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    email: str
    phone: Optional[str] = None
    role: UserRole
    specialty: Optional[str] = None
    license_no: Optional[str] = None
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ════════════════════════════════════════════════════════════════════════════
# Patient Links (multi-doctor)
# ════════════════════════════════════════════════════════════════════════════
class LinkCreateRequest(BaseModel):
    patient_email: EmailStr
    relation: Optional[str] = "family"
    is_primary: bool = False
    notes: Optional[str] = None


class LinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    patient_id: str
    linked_id: str
    role: LinkRole
    relation: Optional[str]
    is_primary: bool
    notes: Optional[str]
    created_at: datetime


class LinkedPersonOut(BaseModel):
    """Convenience shape for `/links/my-doctors` and `/links/my-patients`."""
    link_id: str
    user_id: str
    name: str
    email: str
    phone: Optional[str] = None
    role: str
    relation: Optional[str] = None
    is_primary: bool = False
    specialty: Optional[str] = None
    notes: Optional[str] = None


# ════════════════════════════════════════════════════════════════════════════
# Files
# ════════════════════════════════════════════════════════════════════════════
class FileMetaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    filename: str
    mime_type: str
    size_bytes: int
    purpose: str
    uploaded_at: datetime


# ════════════════════════════════════════════════════════════════════════════
# Module 1 — Audit fields shared shape
# ════════════════════════════════════════════════════════════════════════════
class AuditOut(BaseModel):
    """Embedded into every M1 record so the UI can show "added by Dr. X"."""
    user_id: Optional[str] = None
    role: Optional[str] = None
    name: Optional[str] = None


# ════════════════════════════════════════════════════════════════════════════
# Module 1 — Activity Log
# ════════════════════════════════════════════════════════════════════════════
class ActivityLogCreate(BaseModel):
    type: str = Field(..., min_length=1, max_length=100)
    duration: Optional[int] = Field(None, ge=0, le=24 * 60)
    notes: Optional[str] = None
    logged_at: Optional[datetime] = None
    # When a doctor / family logs on behalf of a patient, they pass patient_id.
    # Otherwise the current user logs for themselves.
    patient_id: Optional[str] = None


class ActivityLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    type: str
    duration: Optional[int]
    notes: Optional[str]
    logged_at: datetime
    created_at: datetime
    created_by: AuditOut

    @classmethod
    def from_orm_with_audit(cls, obj):
        return cls(
            id=obj.id, user_id=obj.user_id, type=obj.type,
            duration=obj.duration, notes=obj.notes,
            logged_at=obj.logged_at, created_at=obj.created_at,
            created_by=AuditOut(
                user_id=obj.created_by_user_id,
                role=obj.created_by_role,
                name=obj.created_by_name,
            ),
        )


class ActivityStats(BaseModel):
    total: int
    today: int
    by_type: dict
    total_duration_min: int


# ════════════════════════════════════════════════════════════════════════════
# Module 1 — Routine
# ════════════════════════════════════════════════════════════════════════════
class RoutineCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    type: str = Field(..., min_length=1, max_length=50)
    scheduled_at: str = Field(..., pattern=r"^\d{2}:\d{2}$")  # "HH:MM"
    days: List[str]
    is_active: bool = True
    notes: Optional[str] = None
    patient_id: Optional[str] = None


class RoutineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    title: str
    type: str
    scheduled_at: str
    days: List[str]
    is_active: bool
    notes: Optional[str]
    created_at: datetime
    created_by: AuditOut

    @field_validator("days", mode="before")
    @classmethod
    def parse_days(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return []
        return v

    @classmethod
    def from_orm_with_audit(cls, obj):
        return cls(
            id=obj.id, user_id=obj.user_id, title=obj.title, type=obj.type,
            scheduled_at=obj.scheduled_at, days=obj.days,
            is_active=obj.is_active, notes=obj.notes, created_at=obj.created_at,
            created_by=AuditOut(
                user_id=obj.created_by_user_id,
                role=obj.created_by_role,
                name=obj.created_by_name,
            ),
        )


# ════════════════════════════════════════════════════════════════════════════
# Module 1 — Medication Verify
# ════════════════════════════════════════════════════════════════════════════
class MedicationVerifyResult(BaseModel):
    matched: bool
    confidence: float
    detected_medication: Optional[str] = None
    prescribed_medication: str
    warnings: List[str] = []
    image_file_id: Optional[str] = None
    log_id: Optional[str] = None
    ai_provider: str
    ai_fallback_used: bool = False
    ai_fallback_reason: Optional[str] = None
    latency_ms: int = 0


class MedicationVerifyLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    prescribed_medication: str
    detected_medication: Optional[str]
    matched: bool
    confidence: Optional[float]
    ai_provider: Optional[str]
    ai_fallback_used: bool
    image_file_id: Optional[str]
    verified_at: datetime
    created_by: AuditOut

    @classmethod
    def from_orm_with_audit(cls, obj):
        return cls(
            id=obj.id, user_id=obj.user_id,
            prescribed_medication=obj.prescribed_medication,
            detected_medication=obj.detected_medication,
            matched=obj.matched, confidence=obj.confidence,
            ai_provider=obj.ai_provider, ai_fallback_used=obj.ai_fallback_used,
            image_file_id=obj.image_file_id, verified_at=obj.verified_at,
            created_by=AuditOut(
                user_id=obj.created_by_user_id,
                role=obj.created_by_role,
                name=obj.created_by_name,
            ),
        )


# ════════════════════════════════════════════════════════════════════════════
# Module 1 — Fall Detection
# ════════════════════════════════════════════════════════════════════════════
class FallDetectionResult(BaseModel):
    log_id: str
    fall_detected: bool
    confidence: float
    mode: str
    has_audio: bool
    segments: List[dict]
    input_video_file_id: Optional[str] = None
    output_video_file_id: Optional[str] = None
    message: str
    alert_sent: bool


class FallDetectionLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    fall_detected: bool
    confidence: Optional[float]
    mode: Optional[str]
    has_audio: bool
    input_video_file_id: Optional[str]
    output_video_file_id: Optional[str]
    alert_sent: bool
    detected_at: datetime
    created_by: AuditOut

    @classmethod
    def from_orm_with_audit(cls, obj):
        return cls(
            id=obj.id,
            user_id=obj.user_id,
            fall_detected=obj.fall_detected,
            confidence=obj.confidence,
            mode=obj.mode,
            has_audio=obj.has_audio,
            input_video_file_id=obj.input_video_file_id,
            output_video_file_id=obj.output_video_file_id,
            alert_sent=obj.alert_sent,
            detected_at=obj.detected_at,
            created_by=AuditOut(
                user_id=obj.created_by_user_id,
                role=obj.created_by_role,
                name=obj.created_by_name,
            ),
        )

# ════════════════════════════════════════════════════════════════════════════
# Module 2 — Health Management
# ════════════════════════════════════════════════════════════════════════════
class VisitType(str, Enum):
    CONSULTATION      = "CONSULTATION"
    FOLLOWUP          = "FOLLOWUP"
    DIAGNOSTIC        = "DIAGNOSTIC"
    EMERGENCY         = "EMERGENCY"
    OTHER             = "OTHER"


class AttachmentKind(str, Enum):
    PRESCRIPTION      = "PRESCRIPTION"
    LAB_REPORT        = "LAB_REPORT"
    IMAGING           = "IMAGING"
    DISCHARGE_SUMMARY = "DISCHARGE_SUMMARY"
    OTHER             = "OTHER"


# ── Medical Visit ────────────────────────────────────────────────────────────
class MedicalVisitCreate(BaseModel):
    patient_id: str
    visit_type: VisitType = VisitType.CONSULTATION
    visit_date: Optional[datetime] = None
    title: str = Field(..., min_length=1, max_length=255)
    diagnosis: Optional[str] = None
    prescription_text: Optional[str] = None
    notes: Optional[str] = None
    follow_up_at: Optional[datetime] = None


class MedicalVisitUpdate(BaseModel):
    visit_type: Optional[VisitType] = None
    visit_date: Optional[datetime] = None
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    diagnosis: Optional[str] = None
    prescription_text: Optional[str] = None
    notes: Optional[str] = None
    follow_up_at: Optional[datetime] = None


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    visit_id: str
    file_id: str
    kind: AttachmentKind
    description: Optional[str]
    uploaded_at: datetime
    # Embedded file meta for convenience
    filename: Optional[str] = None
    mime_type: Optional[str] = None
    size_bytes: Optional[int] = None
    created_by: AuditOut


class MedicalVisitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    patient_id: str
    doctor_id: Optional[str]
    doctor_name: Optional[str] = None
    doctor_specialty: Optional[str] = None
    visit_type: VisitType
    visit_date: datetime
    title: str
    diagnosis: Optional[str]
    prescription_text: Optional[str]
    notes: Optional[str]
    follow_up_at: Optional[datetime]
    created_at: datetime
    created_by: AuditOut
    attachments: List[AttachmentOut] = []


# ── Meal Log ─────────────────────────────────────────────────────────────────
class MealLogCreate(BaseModel):
    patient_id: Optional[str] = None
    meal_type: str = Field(..., min_length=1, max_length=30)
    description: str = Field(..., min_length=1, max_length=500)
    eaten_at: Optional[datetime] = None
    calories: Optional[float] = Field(None, ge=0, le=10000)
    protein_g: Optional[float] = Field(None, ge=0, le=1000)
    carbs_g: Optional[float] = Field(None, ge=0, le=1000)
    fat_g: Optional[float] = Field(None, ge=0, le=1000)
    image_file_id: Optional[str] = None
    notes: Optional[str] = None


class MealLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    meal_type: str
    description: str
    eaten_at: datetime
    calories: Optional[float]
    protein_g: Optional[float]
    carbs_g: Optional[float]
    fat_g: Optional[float]
    ai_estimated: bool
    ai_provider: Optional[str]
    ai_fallback_used: bool
    image_file_id: Optional[str]
    notes: Optional[str]
    created_at: datetime
    created_by: AuditOut

    @classmethod
    def from_orm_with_audit(cls, obj):
        return cls(
            id=obj.id, user_id=obj.user_id, meal_type=obj.meal_type,
            description=obj.description, eaten_at=obj.eaten_at,
            calories=obj.calories, protein_g=obj.protein_g,
            carbs_g=obj.carbs_g, fat_g=obj.fat_g,
            ai_estimated=obj.ai_estimated, ai_provider=obj.ai_provider,
            ai_fallback_used=obj.ai_fallback_used,
            image_file_id=obj.image_file_id, notes=obj.notes,
            created_at=obj.created_at,
            created_by=AuditOut(
                user_id=obj.created_by_user_id,
                role=obj.created_by_role,
                name=obj.created_by_name,
            ),
        )


class MealNutritionEstimate(BaseModel):
    """Result of AI nutrition estimation — user can edit before saving."""
    description: str
    calories: Optional[float] = None
    protein_g: Optional[float] = None
    carbs_g: Optional[float] = None
    fat_g: Optional[float] = None
    ai_provider: str
    ai_fallback_used: bool = False
    ai_fallback_reason: Optional[str] = None
    image_file_id: Optional[str] = None
    confidence: Optional[float] = None
    raw_response: Optional[str] = None


class NutritionDailySummary(BaseModel):
    date: str               # ISO date
    total_calories: float
    total_protein_g: float
    total_carbs_g: float
    total_fat_g: float
    meal_count: int


# ════════════════════════════════════════════════════════════════════════════
# Notifications
# ════════════════════════════════════════════════════════════════════════════
class NotificationType(str, Enum):
    MEDICATION_REMINDER = "MEDICATION_REMINDER"
    MEAL_REMINDER       = "MEAL_REMINDER"
    EXERCISE_REMINDER   = "EXERCISE_REMINDER"
    GENERIC_REMINDER    = "GENERIC_REMINDER"
    VISIT_ADDED         = "VISIT_ADDED"
    PRESCRIPTION_ADDED  = "PRESCRIPTION_ADDED"
    FALL_DETECTED       = "FALL_DETECTED"
    CONNECTION_ADDED    = "CONNECTION_ADDED"
    INFO                = "INFO"


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    type: NotificationType
    title: str
    body: Optional[str]
    link: Optional[str]
    is_read: bool
    created_at: datetime
    source_user_name: Optional[str] = None
    source_user_role: Optional[str] = None


class NotificationStats(BaseModel):
    unread: int
    total: int

# ════════════════════════════════════════════════════════════════════════════
# Medication Intake (separate from VerifyLog)
# ════════════════════════════════════════════════════════════════════════════
class MedicationIntakeCreate(BaseModel):
    patient_id: Optional[str] = None
    routine_id: Optional[str] = None
    medication_name: str = Field(..., min_length=1, max_length=255)
    scheduled_at: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    notes: Optional[str] = None


class MedicationIntakeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    routine_id: Optional[str]
    medication_name: str
    scheduled_at: str
    taken_at: datetime
    on_time: bool
    notes: Optional[str]
    created_by: AuditOut

    @classmethod
    def from_orm_with_audit(cls, obj):
        return cls(
            id=obj.id, user_id=obj.user_id, routine_id=obj.routine_id,
            medication_name=obj.medication_name, scheduled_at=obj.scheduled_at,
            taken_at=obj.taken_at, on_time=obj.on_time, notes=obj.notes,
            created_by=AuditOut(
                user_id=obj.created_by_user_id,
                role=obj.created_by_role,
                name=obj.created_by_name,
            ),
        )


class MedicationDoseToday(BaseModel):
    """One scheduled dose for today — combines routine + intake status."""
    routine_id: str
    medication_name: str
    scheduled_at: str
    days: List[str]
    notes: Optional[str] = None
    is_taken: bool = False
    taken_at: Optional[datetime] = None
    is_overdue: bool = False
    minutes_until: int = 0   # negative if overdue


class MedicationStreakStats(BaseModel):
    days_with_full_compliance: int   # last 7 days
    total_doses_last_7_days: int
    taken_last_7_days: int
    compliance_pct: float


# ════════════════════════════════════════════════════════════════════════════
# Prescription Summary
# ════════════════════════════════════════════════════════════════════════════
class PrescriptionItem(BaseModel):
    """One prescription — could be from prescription_text or attachment."""
    visit_id: str
    visit_title: str
    visit_date: datetime
    doctor_name: Optional[str] = None
    doctor_specialty: Optional[str] = None
    prescription_text: Optional[str] = None
    attachment_id: Optional[str] = None
    attachment_filename: Optional[str] = None


# ─── Standalone uploaded prescriptions (patient/family-uploaded) ────────────
class StandalonePrescriptionCreate(BaseModel):
    """Create a standalone prescription (patient/family upload)."""
    doctor_name:       Optional[str] = Field(None, max_length=150)
    doctor_specialty:  Optional[str] = Field(None, max_length=100)
    clinic_name:       Optional[str] = Field(None, max_length=150)
    prescription_text: Optional[str] = None
    issued_at:         Optional[datetime] = None
    notes:             Optional[str] = None


class StandalonePrescriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id:                str
    patient_id:        str
    doctor_name:       Optional[str]
    doctor_specialty:  Optional[str]
    clinic_name:       Optional[str]
    prescription_text: Optional[str]
    file_id:           Optional[str]
    file_filename:     Optional[str] = None
    file_mime_type:    Optional[str] = None
    issued_at:         Optional[datetime]
    notes:             Optional[str]
    created_at:        datetime
    created_by:        AuditOut

    @classmethod
    def from_orm_with_audit(cls, obj, file_obj=None):
        return cls(
            id=obj.id,
            patient_id=obj.patient_id,
            doctor_name=obj.doctor_name,
            doctor_specialty=obj.doctor_specialty,
            clinic_name=obj.clinic_name,
            prescription_text=obj.prescription_text,
            file_id=obj.file_id,
            file_filename=file_obj.filename if file_obj else None,
            file_mime_type=file_obj.mime_type if file_obj else None,
            issued_at=obj.issued_at,
            notes=obj.notes,
            created_at=obj.created_at,
            created_by=AuditOut(
                user_id=obj.created_by_user_id,
                role=obj.created_by_role,
                name=obj.created_by_name,
            ),
        )


class PrescriptionAISummary(BaseModel):
    """AI extraction of medications from a prescription."""
    medications: List[dict]    # [{"name": "Amlodipine", "dose": "5mg", "frequency": "once daily", "duration": "30 days"}]
    summary_text: str
    warnings: List[str] = []
    ai_provider: str
    ai_fallback_used: bool = False

# ════════════════════════════════════════════════════════════════════════════
# Module 3 — Mental Health Support
# ════════════════════════════════════════════════════════════════════════════
class ChatPersona(str, Enum):
    FRIENDLY_COMPANION  = "FRIENDLY_COMPANION"
    MENTAL_HEALTH_COACH = "MENTAL_HEALTH_COACH"


class MessageRole(str, Enum):
    USER      = "USER"
    ASSISTANT = "ASSISTANT"
    SYSTEM    = "SYSTEM"


class RecommendationStatus(str, Enum):
    ACTIVE    = "ACTIVE"
    DISMISSED = "DISMISSED"
    SAVED     = "SAVED"


# ── Mood Log ─────────────────────────────────────────────────────────────────
class MoodLogCreate(BaseModel):
    patient_id: Optional[str] = None
    mood:    int = Field(..., ge=1, le=5)
    sleep:   int = Field(..., ge=1, le=5)
    energy:  int = Field(..., ge=1, le=5)
    anxiety: int = Field(..., ge=1, le=5)
    note: Optional[str] = Field(None, max_length=2000)
    logged_at: Optional[datetime] = None


class MoodLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    mood: int
    sleep: int
    energy: int
    anxiety: int
    note: Optional[str]
    sentiment_label: Optional[str]
    sentiment_score: Optional[float]
    ai_insight: Optional[str] = None
    ai_suggestion: Optional[str] = None
    ai_provider: Optional[str]
    logged_at: datetime
    created_at: datetime
    created_by: AuditOut

    @classmethod
    def from_orm_with_audit(cls, obj):
        return cls(
            id=obj.id, user_id=obj.user_id,
            mood=obj.mood, sleep=obj.sleep, energy=obj.energy, anxiety=obj.anxiety,
            note=obj.note, sentiment_label=obj.sentiment_label,
            sentiment_score=obj.sentiment_score,
            ai_insight=obj.ai_insight, ai_suggestion=obj.ai_suggestion,
            ai_provider=obj.ai_provider,
            logged_at=obj.logged_at, created_at=obj.created_at,
            created_by=AuditOut(
                user_id=obj.created_by_user_id,
                role=obj.created_by_role,
                name=obj.created_by_name,
            ),
        )


class MoodTrendPoint(BaseModel):
    date: str   # YYYY-MM-DD
    mood: float
    sleep: float
    energy: float
    anxiety: float
    entry_count: int


class MoodSummary(BaseModel):
    days: int
    avg_mood: float
    avg_sleep: float
    avg_energy: float
    avg_anxiety: float
    entry_count: int
    trend: List[MoodTrendPoint]


# ── Chat ─────────────────────────────────────────────────────────────────────
class ChatSessionCreate(BaseModel):
    persona: ChatPersona = ChatPersona.FRIENDLY_COMPANION


class ChatSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    persona: ChatPersona
    title: Optional[str]
    created_at: datetime
    last_message_at: datetime
    message_count: int = 0


class ChatMessageCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    session_id: str
    role: MessageRole
    content: str
    ai_provider: Optional[str] = None
    ai_fallback_used: bool = False
    created_at: datetime


class ChatTurnOut(BaseModel):
    """The result of POSTing a user message — includes both messages."""
    user_message: ChatMessageOut
    assistant_message: ChatMessageOut


# ── Wellness Recommendations ─────────────────────────────────────────────────
class WellnessRecommendationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    title: str
    body: str
    category: str
    rationale: Optional[str]
    status: RecommendationStatus
    ai_provider: Optional[str]
    generated_at: datetime
    dismissed_at: Optional[datetime]
    saved_at: Optional[datetime]


class WellnessGenerateRequest(BaseModel):
    """Optional override — by default uses last 7 days of mood data."""
    days_lookback: int = Field(7, ge=1, le=30)

# ════════════════════════════════════════════════════════════════════════════
# Module 4 — Doctor Support
# ════════════════════════════════════════════════════════════════════════════
class ImageCategory(str, Enum):
    SKIN     = "skin"
    XRAY     = "xray"
    GENERAL  = "general"


class DiseasePrediction(BaseModel):
    name: str
    confidence: float = Field(..., ge=0, le=1)
    description: Optional[str] = None


class DiseaseClassificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    patient_id: Optional[str]
    image_file_id: str
    image_category: ImageCategory
    clinical_notes: Optional[str]
    ai_predictions: List[DiseasePrediction] = []
    ai_summary: Optional[str]
    ai_recommendations: Optional[str]
    ai_provider: Optional[str]
    ai_fallback_used: bool = False
    doctor_diagnosis: Optional[str]
    classified_at: datetime
    created_by: AuditOut


class DoctorDiagnosisUpdate(BaseModel):
    doctor_diagnosis: str = Field(..., min_length=1, max_length=2000)


class ReportSummaryCreate(BaseModel):
    patient_id: Optional[str] = None
    title: str = Field(..., min_length=1, max_length=255)
    text: str = Field(..., min_length=10, max_length=20000)
    source_file_id: Optional[str] = None


class AbnormalValue(BaseModel):
    name: str
    value: str
    ref_range: Optional[str] = None
    flag: str = "high"   # high | low | abnormal


class ReportSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    patient_id: Optional[str]
    source_file_id: Optional[str]
    title: str
    raw_text: str
    summary_text: Optional[str]
    key_findings: List[str] = []
    abnormal_values: List[AbnormalValue] = []
    recommendations: List[str] = []
    ai_provider: Optional[str]
    ai_fallback_used: bool = False
    summarized_at: datetime
    created_by: AuditOut


# ════════════════════════════════════════════════════════════════════════════
# Module 5 — Family Engagement & Emergency
# ════════════════════════════════════════════════════════════════════════════
class SOSStatus(str, Enum):
    ACTIVE       = "ACTIVE"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED     = "RESOLVED"
    FALSE_ALARM  = "FALSE_ALARM"


class SOSCreate(BaseModel):
    message: Optional[str] = Field(None, max_length=500)
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location_text: Optional[str] = Field(None, max_length=500)


class SOSAlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    patient_id: str
    patient_name: Optional[str] = None
    message: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    location_text: Optional[str]
    status: SOSStatus
    triggered_at: datetime
    acknowledged_by_name: Optional[str]
    acknowledged_at: Optional[datetime]
    resolved_at: Optional[datetime]
    resolution_note: Optional[str]
    notified_count: int = 0
    sms_sent: bool = False


class SOSResolve(BaseModel):
    note: Optional[str] = Field(None, max_length=2000)
    false_alarm: bool = False


# ── Family Digests ───────────────────────────────────────────────────────────
class FamilyDigestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    patient_id: str
    period_date: str
    activities_count: int
    medications_taken: int
    medications_total: int
    avg_mood: Optional[float]
    fall_alerts: int
    body_text: str
    status: str
    sent_at: Optional[datetime]
    created_at: datetime


# ── Caregiver Messaging ──────────────────────────────────────────────────────
class CaregiverMessageCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)


class CaregiverMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    thread_id: str
    sender_id: Optional[str]
    sender_name: str
    sender_role: str
    content: str
    created_at: datetime


class CaregiverThreadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    patient_id: str
    patient_name: Optional[str] = None
    title: Optional[str]
    created_at: datetime
    last_message_at: datetime
    member_count: int = 0
    unread_count: int = 0
