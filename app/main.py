"""CareAI — FastAPI app entry."""
import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.ai.ai_router import get_ai_status, reset_ollama_cache
from app.core.config import settings
from app.core.scheduler import start_scheduler, stop_scheduler
from app.routers import (
    auth, files, links, physical, health, notifications, reminder,
    uploaded_prescriptions,
    mental_health, doctor_support, family_emergency,
)


logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown — manages the reminder scheduler."""
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(
    title="CareAI — Elderly Monitoring",
    version="4.9.1",
    description="v4.9.1: Groq primary AI routing + critical bug fixes (Cannot reach server)",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Global exception handler for AI service errors ──────────────────────────
from fastapi import Request
from fastapi.responses import JSONResponse
from app.ai.ai_router import AIServiceError


@app.exception_handler(AIServiceError)
async def ai_service_error_handler(request: Request, exc: AIServiceError):
    """Convert AI service errors into clean 503s with helpful messages."""
    return JSONResponse(
        status_code=503,
        content={
            "detail": exc.message,
            "ai_error": True,
            "ollama_reason": exc.ollama_reason,
            "groq_reason":   exc.groq_reason,
        },
    )


# ─── Health & meta ────────────────────────────────────────────────────────────
meta = APIRouter(tags=["Meta"])


@meta.get("/")
def root():
    return {
        "name": "CareAI",
        "version": "4.5.0",
        "modules": [
            "1 — Physical Monitoring",
            "2 — Health Management",
            "3 — Mental Health Support",
            "4 — Doctor Support",
            "5 — Family Engagement & Emergency",
            "Notifications & Reminders",
        ],
        "docs": "/docs",
    }


@meta.get("/health-check")
def health_check():
    """Lightweight check used by Railway."""
    return {"status": "ok"}


@meta.get("/api/v1/ai/status")
async def ai_status():
    """Frontend uses this to show 'Ollama ready / using Groq fallback / no AI configured'."""
    return await get_ai_status()


@meta.post("/api/v1/ai/reset-cache")
def ai_reset_cache():
    """Reset the Ollama health-check cache (use after pulling a new model)."""
    reset_ollama_cache()
    return {"message": "Ollama health cache reset"}


app.include_router(meta)
app.include_router(auth.router, prefix="/api/v1")
app.include_router(links.router, prefix="/api/v1")
app.include_router(files.router, prefix="/api/v1")
app.include_router(physical.router, prefix="/api/v1")
app.include_router(health.router, prefix="/api/v1")
app.include_router(notifications.router, prefix="/api/v1")
app.include_router(reminder.router, prefix="/api/v1")
app.include_router(reminder.prescriptions_router, prefix="/api/v1")
app.include_router(uploaded_prescriptions.router, prefix="/api/v1")
app.include_router(mental_health.router, prefix="/api/v1")
app.include_router(doctor_support.router, prefix="/api/v1")
app.include_router(family_emergency.router, prefix="/api/v1")
