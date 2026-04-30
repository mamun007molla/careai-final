"""AI Provider Router — Groq primary with Ollama fallback.

Design:
- Try Groq first (fast cloud, ~1-3s response, free tier 14k req/day).
- If Groq fails (no key, rate limit, network), fall back to local Ollama.
- AI_PROVIDER env can force "groq" or "ollama" only (useful for testing offline).
- Default AI_PROVIDER=auto means: try Groq, fall back to Ollama.

Both providers raise a clean AIServiceError when fully unavailable so the
global FastAPI handler returns a 503 with a helpful message instead of a 500.
"""
import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from app.core.config import settings


log = logging.getLogger("careai.ai_router")


# ─── Custom exception for clean error mapping by routers ─────────────────────
class AIServiceError(Exception):
    """Raised when both Groq and Ollama are unavailable/failed."""
    def __init__(self, message: str, groq_reason: Optional[str] = None,
                 ollama_reason: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.groq_reason = groq_reason
        self.ollama_reason = ollama_reason

    def user_message(self) -> str:
        return self.message


@dataclass
class AIResult:
    """Wraps a provider response with metadata about which path was taken."""
    data: dict
    provider: str                          # "groq" | "ollama"
    fallback_used: bool = False
    fallback_reason: Optional[str] = None
    latency_ms: int = 0
    raw_text: str = ""

    def dict(self) -> dict:
        return {
            **self.data,
            "_meta": {
                "provider": self.provider,
                "fallback_used": self.fallback_used,
                "fallback_reason": self.fallback_reason,
                "latency_ms": self.latency_ms,
            },
        }


# ─── Ollama health-check (cached for short TTL) ───────────────────────────────
_ollama_status: dict = {"available": None, "checked_at": 0.0, "ttl": 30.0}


async def _is_ollama_available() -> tuple[bool, Optional[str]]:
    """Return (available, reason_if_not). Cached for 30s to avoid spam."""
    now = time.time()
    if _ollama_status["available"] is not None and (now - _ollama_status["checked_at"]) < _ollama_status["ttl"]:
        return _ollama_status["available"], _ollama_status.get("reason")

    try:
        async with httpx.AsyncClient(timeout=settings.OLLAMA_HEALTHCHECK_TIMEOUT) as client:
            r = await client.get(f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/tags")
            if r.status_code != 200:
                _ollama_status.update(available=False, checked_at=now,
                                      reason=f"Ollama returned HTTP {r.status_code}")
                return False, _ollama_status["reason"]

            tags = r.json().get("models", [])
            names = {m.get("name", "").split(":")[0] for m in tags}
            wanted = settings.OLLAMA_VISION_MODEL.split(":")[0]
            if wanted and names and wanted not in names:
                _ollama_status.update(available=False, checked_at=now,
                                      reason=f"Model '{settings.OLLAMA_VISION_MODEL}' not pulled")
                return False, _ollama_status["reason"]

            _ollama_status.update(available=True, checked_at=now, reason=None)
            return True, None
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as e:
        _ollama_status.update(available=False, checked_at=now,
                              reason=f"Ollama not reachable: {type(e).__name__}")
        return False, _ollama_status["reason"]


def reset_ollama_cache() -> None:
    """Manually reset the cache — useful after pulling a new model."""
    _ollama_status.update(available=None, checked_at=0.0, reason=None)


async def get_ai_status() -> dict:
    """Public status report for the /ai/status endpoint."""
    ollama_ok, ollama_reason = await _is_ollama_available()
    return {
        "primary": "groq",
        "fallback": "ollama",
        "groq": {
            "configured": bool(settings.GROQ_API_KEY),
            "vision_model": settings.GROQ_VISION_MODEL,
            "text_model":   settings.GROQ_TEXT_MODEL,
        },
        "ollama": {
            "configured_url": settings.OLLAMA_BASE_URL,
            "vision_model":   settings.OLLAMA_VISION_MODEL,
            "available":      ollama_ok,
            "reason":         ollama_reason,
        },
        "forced_provider": settings.AI_PROVIDER,
    }


# ════════════════════════════════════════════════════════════════════════════
# VISION
# ════════════════════════════════════════════════════════════════════════════
async def run_vision(
    image_bytes: bytes,
    prompt: str,
    mime_type: str = "image/jpeg",
    json_response: bool = True,
) -> AIResult:
    """Run a vision prompt with Groq primary, Ollama fallback.

    Flow:
      1. Try Groq (if key configured)
      2. If Groq fails → try Ollama (if available)
      3. If both fail → AIServiceError with helpful message
    """
    forced  = settings.AI_PROVIDER.lower()
    started = time.time()

    # ── Forced ollama (for offline/testing) ──
    if forced == "ollama":
        return await _try_ollama_vision(image_bytes, prompt, json_response, started,
                                        is_fallback=False, force=True)

    # ── Default flow: Groq primary ──
    groq_error = None
    if settings.GROQ_API_KEY:
        try:
            return await _try_groq_vision(image_bytes, prompt, mime_type,
                                          json_response, started, is_fallback=False)
        except Exception as e:
            groq_error = f"{type(e).__name__}: {e}"
            log.warning("Groq vision failed, will try Ollama fallback: %s", groq_error)

    # ── Forced groq + no key/failed → error out, don't fall back ──
    if forced == "groq":
        raise AIServiceError(
            "AI_PROVIDER is set to 'groq' but the request failed. "
            f"Reason: {groq_error or 'GROQ_API_KEY not configured'}. "
            "Switch AI_PROVIDER=auto in .env to allow Ollama fallback.",
            groq_reason=groq_error or "GROQ_API_KEY not configured",
        )

    # ── Fallback to Ollama ──
    return await _try_ollama_vision(image_bytes, prompt, json_response, started,
                                    is_fallback=True, fallback_reason=groq_error)


async def _try_groq_vision(image_bytes, prompt, mime_type, json_response, started,
                           is_fallback=False) -> AIResult:
    from app.ai.groq_client import groq_vision

    if not settings.GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not configured")

    data, raw = await groq_vision(image_bytes, prompt, mime_type, json_response=json_response)
    return AIResult(
        data=data, raw_text=raw, provider="groq",
        fallback_used=is_fallback,
        latency_ms=int((time.time() - started) * 1000),
    )


async def _try_ollama_vision(image_bytes, prompt, json_response, started,
                             is_fallback=False, fallback_reason=None,
                             force=False) -> AIResult:
    from app.ai.ollama_client import ollama_vision

    # Health check (skip if forced — assume caller knows it's running)
    if not force:
        ollama_ok, reason = await _is_ollama_available()
        if not ollama_ok:
            if is_fallback:
                raise AIServiceError(
                    "AI service is currently unavailable. "
                    f"Groq failed: {fallback_reason}. "
                    f"Ollama also unavailable: {reason}. "
                    "Please check your GROQ_API_KEY in .env, or start Ollama locally.",
                    groq_reason=fallback_reason,
                    ollama_reason=reason,
                )
            raise AIServiceError(
                f"Ollama is not available: {reason}. "
                "Install from https://ollama.com and run: ollama pull gemma3:4b",
                ollama_reason=reason,
            )

    try:
        data, raw = await ollama_vision(image_bytes, prompt, json_response=json_response)
        return AIResult(
            data=data, raw_text=raw, provider="ollama",
            fallback_used=is_fallback,
            fallback_reason=fallback_reason,
            latency_ms=int((time.time() - started) * 1000),
        )
    except Exception as e:
        err = f"Ollama vision failed: {type(e).__name__}: {e}"
        log.warning(err)
        _ollama_status.update(available=False, checked_at=time.time(), reason=err)

        if is_fallback:
            raise AIServiceError(
                "AI service is currently unavailable. Both Groq and Ollama failed. "
                "Please try again in a moment.",
                groq_reason=fallback_reason,
                ollama_reason=err,
            )
        raise AIServiceError(
            f"Ollama vision failed: {e}",
            ollama_reason=err,
        )


# ════════════════════════════════════════════════════════════════════════════
# TEXT
# ════════════════════════════════════════════════════════════════════════════
async def run_text(prompt: str, json_response: bool = False,
                   system: Optional[str] = None) -> AIResult:
    """Run a text prompt with Groq primary, Ollama fallback."""
    forced  = settings.AI_PROVIDER.lower()
    started = time.time()

    if forced == "ollama":
        return await _try_ollama_text(prompt, system, json_response, started,
                                      is_fallback=False, force=True)

    groq_error = None
    if settings.GROQ_API_KEY:
        try:
            return await _try_groq_text(prompt, system, json_response, started,
                                        is_fallback=False)
        except Exception as e:
            groq_error = f"{type(e).__name__}: {e}"
            log.warning("Groq text failed, will try Ollama fallback: %s", groq_error)

    if forced == "groq":
        raise AIServiceError(
            "AI_PROVIDER is set to 'groq' but the request failed. "
            f"Reason: {groq_error or 'GROQ_API_KEY not configured'}.",
            groq_reason=groq_error or "GROQ_API_KEY not configured",
        )

    return await _try_ollama_text(prompt, system, json_response, started,
                                  is_fallback=True, fallback_reason=groq_error)


async def _try_groq_text(prompt, system, json_response, started,
                         is_fallback=False) -> AIResult:
    from app.ai.groq_client import groq_text

    if not settings.GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not configured")

    data, raw = await groq_text(prompt, system, json_response)
    return AIResult(
        data=data, raw_text=raw, provider="groq",
        fallback_used=is_fallback,
        latency_ms=int((time.time() - started) * 1000),
    )


async def _try_ollama_text(prompt, system, json_response, started,
                           is_fallback=False, fallback_reason=None,
                           force=False) -> AIResult:
    from app.ai.ollama_client import ollama_text

    if not force:
        ollama_ok, reason = await _is_ollama_available()
        if not ollama_ok:
            if is_fallback:
                raise AIServiceError(
                    "AI service is currently unavailable. "
                    f"Groq failed: {fallback_reason}. "
                    f"Ollama also unavailable: {reason}.",
                    groq_reason=fallback_reason,
                    ollama_reason=reason,
                )
            raise AIServiceError(
                f"Ollama is not available: {reason}.",
                ollama_reason=reason,
            )

    try:
        data, raw = await ollama_text(prompt, system, json_response)
        return AIResult(
            data=data, raw_text=raw, provider="ollama",
            fallback_used=is_fallback,
            fallback_reason=fallback_reason,
            latency_ms=int((time.time() - started) * 1000),
        )
    except Exception as e:
        err = f"Ollama text failed: {type(e).__name__}: {e}"
        log.warning(err)
        _ollama_status.update(available=False, checked_at=time.time(), reason=err)

        if is_fallback:
            raise AIServiceError(
                "AI service is currently unavailable. Both Groq and Ollama failed.",
                groq_reason=fallback_reason,
                ollama_reason=err,
            )
        raise AIServiceError(f"Ollama text failed: {e}", ollama_reason=err)
