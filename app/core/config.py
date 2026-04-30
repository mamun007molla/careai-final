"""Application settings loaded from environment / .env file.

Railway injects DATABASE_URL as `postgres://...` but SQLAlchemy 2.x wants
`postgresql+psycopg2://...`. We normalize automatically.
"""
from functools import lru_cache
from typing import List
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+psycopg2://careai:careai@localhost:5432/careai"

    # ── Auth ──────────────────────────────────────────────────────────────────
    SECRET_KEY: str = "change-me-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 10080  # 7 days

    # ── AI: Ollama (primary) ──────────────────────────────────────────────────
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_VISION_MODEL: str = "gemma3:4b"
    OLLAMA_TEXT_MODEL: str = "gemma3:4b"
    OLLAMA_HEALTHCHECK_TIMEOUT: float = 3.0
    OLLAMA_REQUEST_TIMEOUT: float = 120.0

    # ── AI: Groq (fallback) ───────────────────────────────────────────────────
    GROQ_API_KEY: str = ""
    GROQ_VISION_MODEL: str = "meta-llama/llama-4-scout-17b-16e-instruct"
    GROQ_TEXT_MODEL: str = "llama-3.3-70b-versatile"

    # auto | ollama | groq — auto means try ollama then fallback
    AI_PROVIDER: str = "auto"

    # ── Limits ────────────────────────────────────────────────────────────────
    MAX_IMAGE_SIZE_MB: int = 10
    MAX_VIDEO_SIZE_MB: int = 100
    MAX_FILE_SIZE_MB: int = 25     # for general uploads (PDF prescriptions, etc.)

    # ── CORS ──────────────────────────────────────────────────────────────────
    FRONTEND_URL: str = "http://localhost:3000"
    ALLOWED_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

    # ── Optional: Twilio SMS ──────────────────────────────────────────────────
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_PHONE_NUMBER: str = ""

    # ── Optional: Fall Detection Service (HuggingFace Space or self-hosted) ───
    # If set, fall detection will be delegated to this remote ML service.
    # If empty, falls back to local detection (requires requirements-ml.txt installed).
    # Example: "https://yourname-careai-falldetect.hf.space"
    FALL_DETECT_SERVICE_URL: str = ""
    FALL_DETECT_SERVICE_API_KEY: str = ""

    # ── Optional: Email (SMTP) for notifications ──────────────────────────────
    # Gmail SMTP example:
    #   SMTP_HOST=smtp.gmail.com  SMTP_PORT=587
    #   SMTP_USER=your@gmail.com  SMTP_PASSWORD=<app-password>
    # Get an App Password at https://myaccount.google.com/apppasswords
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""

    # ── Notifications scheduler ──────────────────────────────────────────────
    # Set to false to disable the background reminder scheduler
    ENABLE_REMINDER_SCHEDULER: bool = True
    # How often (seconds) to check routines for matching scheduled times
    REMINDER_CHECK_INTERVAL: int = 60

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def normalize_db_url(cls, v: str) -> str:
        # Railway uses `postgres://` (legacy). SQLAlchemy 2.x requires `postgresql+psycopg2://`.
        if not v:
            return v
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+psycopg2://", 1)
        if v.startswith("postgresql://") and "+psycopg" not in v:
            return v.replace("postgresql://", "postgresql+psycopg2://", 1)
        return v

    @property
    def cors_origins(self) -> List[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
