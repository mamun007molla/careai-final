"""Import all models so Alembic / Base.metadata sees them."""
from app.models.user import User, PatientLink                            # noqa: F401
from app.models.file import File                                         # noqa: F401
from app.models.physical import (                                        # noqa: F401
    ActivityLog, Routine, MedicationVerifyLog, FallDetectionLog,
)
from app.models.health import (                                          # noqa: F401
    MedicalVisit, VisitAttachment, MealLog, Prescription,
)
from app.models.notification import Notification                         # noqa: F401
from app.models.medication_intake import MedicationIntakeLog              # noqa: F401
from app.models.mental_health import (                                    # noqa: F401
    MoodLog, ChatSession, ChatMessage, WellnessRecommendation,
)
from app.models.doctor_support import (                                   # noqa: F401
    DiseaseClassification, ReportSummary,
)
from app.models.family_emergency import (                                 # noqa: F401
    FamilyDigest, SOSAlert, CaregiverThread, CaregiverMessage,
)

__all__ = [
    "User", "PatientLink",
    "File",
    # M1
    "ActivityLog", "Routine", "MedicationVerifyLog", "FallDetectionLog",
    # M2
    "MedicalVisit", "VisitAttachment", "MealLog",
    # Notifications
    "Notification",
    # Medication intake
    "MedicationIntakeLog",
    # M3 - Mental Health
    "MoodLog", "ChatSession", "ChatMessage", "WellnessRecommendation",
    # M4 - Doctor Support
    "DiseaseClassification", "ReportSummary",
    # M5 - Family & Emergency
    "FamilyDigest", "SOSAlert", "CaregiverThread", "CaregiverMessage",
]
