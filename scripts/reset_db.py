"""Recovery utility — drops orphaned enums and tables from a broken migration.

Use this if `alembic upgrade head` fails with errors like:
    type "user_role" already exists
    type "visit_type" already exists

after a previous failed run.

Usage:
    cd backend
    python scripts/reset_db.py        # interactive — asks before dropping

This drops EVERYTHING (tables, enums, alembic version). After running it,
do `alembic upgrade head` for a fresh schema.
"""
import sys
from pathlib import Path

# Allow running as `python scripts/reset_db.py` from the backend folder
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, text                          # noqa: E402
from app.core.config import settings                                 # noqa: E402


ENUMS = ["user_role", "link_role", "file_purpose",
         "visit_type", "attachment_kind"]

TABLES = [
    # Drop in dependency order — children first
    "fall_detection_logs", "medication_verify_logs", "routines",
    "activity_logs", "visit_attachments", "medical_visits", "meal_logs",
    "files", "patient_links", "users", "alembic_version",
]


def main():
    print(f"Connecting to: {settings.DATABASE_URL.split('@')[-1] if '@' in settings.DATABASE_URL else settings.DATABASE_URL}")
    confirm = input("This will DROP ALL CareAI tables and enums. Type 'yes' to confirm: ")
    if confirm.lower() != "yes":
        print("Aborted.")
        return

    engine = create_engine(settings.DATABASE_URL, future=True)
    with engine.begin() as conn:
        # Drop tables first (CASCADE removes dependent FKs)
        for t in TABLES:
            try:
                conn.execute(text(f'DROP TABLE IF EXISTS "{t}" CASCADE'))
                print(f"  dropped table {t}")
            except Exception as e:
                print(f"  ⚠ could not drop {t}: {e}")

        # Then drop enums
        for e in ENUMS:
            try:
                conn.execute(text(f'DROP TYPE IF EXISTS "{e}" CASCADE'))
                print(f"  dropped enum {e}")
            except Exception as ex:
                print(f"  ⚠ could not drop {e}: {ex}")

    print("\n✅ Done. Now run:  alembic upgrade head")


if __name__ == "__main__":
    main()
