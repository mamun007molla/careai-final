"""CareAI — FastAPI app entry."""

import logging
import asyncio
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.ai.ai_router import get_ai_status, reset_ollama_cache, AIServiceError
from app.core.config import settings
from app.core.scheduler import start_scheduler, stop_scheduler

# ✅ IMPORTANT: alias to avoid naming conflict
from app.routers import (
    auth,
    files,
    links,
    physical,
    health as health_router,  # ← FIX
    notifications,
    reminder,
    uploaded_prescriptions,
    mental_health,
    doctor_support,
    family_emergency,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)


# ─────────────────────────────────────────────────────────────
# 🔥 SAFE BACKGROUND SCHEDULER
# ─────────────────────────────────────────────────────────────
async def run_scheduler_safe():
    try:
        logging.info("🚀 Starting scheduler...")
        start_scheduler()
        logging.info("✅ Scheduler started")
    except Exception as e:
        logging.error(f"❌ Scheduler failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.info("🔥 App starting...")

    # run scheduler in background (non-blocking)
    asyncio.create_task(run_scheduler_safe())

    yield

    try:
        stop_scheduler()
        logging.info("🛑 Scheduler stopped")
    except Exception as e:
        logging.error(f"❌ Scheduler stop failed: {e}")


# ─────────────────────────────────────────────────────────────
# 🚀 APP INIT
# ─────────────────────────────────────────────────────────────
app = FastAPI(
    title="CareAI — Elderly Monitoring",
    version="4.9.1",
    description="Production-ready backend",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)


# ─────────────────────────────────────────────────────────────
# 🌐 CORS
# ─────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────
# ❗ AI ERROR HANDLER
# ─────────────────────────────────────────────────────────────
@app.exception_handler(AIServiceError)
async def ai_service_error_handler(request: Request, exc: AIServiceError):
    return JSONResponse(
        status_code=503,
        content={
            "detail": exc.message,
            "ai_error": True,
            "ollama_reason": exc.ollama_reason,
            "groq_reason": exc.groq_reason,
        },
    )


# ─────────────────────────────────────────────────────────────
# 🧠 META ROUTES
# ─────────────────────────────────────────────────────────────
meta = APIRouter(tags=["Meta"])


@meta.get("/")
def root():
    return {
        "name": "CareAI",
        "version": "4.9.1",
        "status": "running",
        "docs": "/docs",
    }


# ✅ Railway / Render healthcheck
@meta.get("/health-check")
def health_check():
    return {"status": "ok"}


# ✅ renamed to avoid conflict
@meta.get("/health")
def health_status():
    return {"status": "ok"}


@meta.get("/api/v1/ai/status")
async def ai_status():
    return await get_ai_status()


@meta.post("/api/v1/ai/reset-cache")
def ai_reset_cache():
    reset_ollama_cache()
    return {"message": "Ollama health cache reset"}


# ─────────────────────────────────────────────────────────────
# 🔗 ROUTERS
# ─────────────────────────────────────────────────────────────
app.include_router(meta)

app.include_router(auth.router, prefix="/api/v1")
app.include_router(links.router, prefix="/api/v1")
app.include_router(files.router, prefix="/api/v1")
app.include_router(physical.router, prefix="/api/v1")

# ✅ FIXED
app.include_router(health_router.router, prefix="/api/v1")

app.include_router(notifications.router, prefix="/api/v1")
app.include_router(reminder.router, prefix="/api/v1")
app.include_router(reminder.prescriptions_router, prefix="/api/v1")
app.include_router(uploaded_prescriptions.router, prefix="/api/v1")
app.include_router(mental_health.router, prefix="/api/v1")
app.include_router(doctor_support.router, prefix="/api/v1")
app.include_router(family_emergency.router, prefix="/api/v1")
