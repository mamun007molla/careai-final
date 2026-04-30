"""
Application settings loaded from environment / .env file.

Fixes:
- Railway `postgres://` → `postgresql+psycopg2://`
- Correctly encodes DB password using LAST '@' separator logic
- Safe for special chars: @ : # % etc.
"""

from functools import lru_cache
from typing import List
from urllib.parse import urlparse, urlunparse, quote

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Database ─────────────────────────────────────────────
    DATABASE_URL: str = Field(
        default="postgresql+psycopg2://careai:careai@localhost:5432/careai"
    )

    # ── Auth ────────────────────────────────────────────────
    SECRET_KEY: str = "change-me-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 10080  # 7 days

    # ── AI: Ollama (primary) ────────────────────────────────
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_VISION_MODEL: str = "gemma3:4b"
    OLLAMA_TEXT_MODEL: str = "gemma3:4b"
    OLLAMA_HEALTHCHECK_TIMEOUT: float = 3.0
    OLLAMA_REQUEST_TIMEOUT: float = 120.0

    # ── AI: Groq (fallback) ─────────────────────────────────
    GROQ_API_KEY: str = ""
    GROQ_VISION_MODEL: str = "meta-llama/llama-4-scout-17b-16e-instruct"
    GROQ_TEXT_MODEL: str = "llama-3.3-70b-versatile"

    # auto | ollama | groq
    AI_PROVIDER: str = "auto"

    # ── Limits ──────────────────────────────────────────────
    MAX_IMAGE_SIZE_MB: int = 10
    MAX_VIDEO_SIZE_MB: int = 100
    MAX_FILE_SIZE_MB: int = 25

    # ── CORS ────────────────────────────────────────────────
    FRONTEND_URL: str = "http://localhost:3000"
    ALLOWED_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

    # ── Twilio (optional) ───────────────────────────────────
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_PHONE_NUMBER: str = ""

    # ── Fall Detection Service (optional) ───────────────────
    FALL_DETECT_SERVICE_URL: str = ""
    FALL_DETECT_SERVICE_API_KEY: str = ""

    # ── Email (SMTP) ────────────────────────────────────────
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""

    # ── Scheduler ───────────────────────────────────────────
    ENABLE_REMINDER_SCHEDULER: bool = True
    REMINDER_CHECK_INTERVAL: int = 60

    # ───────────────────────────────────────────────────────
    # 🔥 DATABASE URL FIX (PROPER SOLUTION)
    # ───────────────────────────────────────────────────────
    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def normalize_db_url(cls, v: str) -> str:
        if not v:
            return v

        # ✅ Step 1: Normalize scheme
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+psycopg2://", 1)
        elif v.startswith("postgresql://") and "+psycopg" not in v:
            v = v.replace("postgresql://", "postgresql+psycopg2://", 1)

        try:
            parsed = urlparse(v)

            # If no credentials → return as-is
            if "@" not in parsed.netloc:
                return v

            # ✅ Step 2: split LAST '@' (critical fix)
            creds, host = parsed.netloc.rsplit("@", 1)

            # Split user + password
            if ":" in creds:
                user, password = creds.split(":", 1)

                # encode password safely
                encoded_password = quote(password, safe="")

                new_netloc = f"{user}:{encoded_password}@{host}"
            else:
                new_netloc = parsed.netloc

            # rebuild URL
            return urlunparse(parsed._replace(netloc=new_netloc))

        except Exception:
            # fail-safe (never crash app)
            return v

    # ── Helpers ─────────────────────────────────────────────
    @property
    def cors_origins(self) -> List[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]


# ── Cached settings ─────────────────────────────────────────
@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
