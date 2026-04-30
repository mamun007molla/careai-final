"""Fall detection client.

If FALL_DETECT_SERVICE_URL is set, calls remote ML service over HTTP.
Otherwise falls back to local detector (development mode, requires ML deps
installed locally).

Public API matches the original `run_fall_detection(video_bytes, filename)`
so the rest of the backend doesn't need to change.
"""
import asyncio
import base64
import logging
from typing import Optional

import httpx

from app.core.config import settings


log = logging.getLogger("careai.fall_client")


async def run_fall_detection(video_bytes: bytes, filename: str = "input.mp4") -> dict:
    """Run fall detection — remote if configured, else local.

    Returns the same shape as the legacy local detector:
      {
        "fall_detected": bool,
        "confidence": float,
        "mode": str,
        "has_audio": bool,
        "segments": list,
        "output_video_bytes": bytes | None,
      }
    """
    service_url = (settings.FALL_DETECT_SERVICE_URL or "").strip()

    # ── Remote mode ──────────────────────────────────────────────────────────
    if service_url:
        return await _run_remote(video_bytes, filename, service_url)

    # ── Local mode (dev only) ────────────────────────────────────────────────
    try:
        from app.ai.fall_detection.detector import run_fall_detection as local_run
    except ImportError as e:
        log.warning("Local detector unavailable: %s", e)
        return _stub_result(f"Local ML not available: {e}")

    return await local_run(video_bytes, filename)


async def _run_remote(video_bytes: bytes, filename: str, base_url: str) -> dict:
    """Send video to remote service, decode response."""
    url = base_url.rstrip("/") + "/detect"
    headers = {}
    if settings.FALL_DETECT_SERVICE_API_KEY:
        headers["X-API-Key"] = settings.FALL_DETECT_SERVICE_API_KEY

    log.info("Calling remote fall detection: %s (size=%d KB)",
             url, len(video_bytes) // 1024)

    timeout = httpx.Timeout(connect=10.0, read=180.0, write=60.0, pool=10.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            files = {"video": (filename, video_bytes, "video/mp4")}
            r = await client.post(url, files=files, headers=headers)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as e:
        log.error("Remote service returned %d: %s", e.response.status_code,
                  e.response.text[:200])
        return _stub_result(f"Remote service error: HTTP {e.response.status_code}")
    except httpx.TimeoutException:
        log.error("Remote service timeout (%.0fs)", timeout.read)
        return _stub_result("Remote service timeout — video may be too long")
    except Exception as e:
        log.exception("Remote detection failed")
        return _stub_result(f"Remote service unreachable: {str(e)}")

    # Decode annotated video back from base64
    output_bytes = None
    out_b64 = data.get("output_video_b64")
    if out_b64:
        try:
            output_bytes = base64.b64decode(out_b64)
        except Exception as e:
            log.warning("Failed to decode annotated video: %s", e)

    return {
        "fall_detected": bool(data.get("fall_detected", False)),
        "confidence":   float(data.get("confidence", 0.0)),
        "mode":         str(data.get("mode", "remote")),
        "has_audio":    bool(data.get("has_audio", False)),
        "segments":     list(data.get("segments", [])),
        "output_video_bytes": output_bytes,
    }


def _stub_result(reason: str) -> dict:
    """Graceful degradation — never crash the request."""
    return {
        "fall_detected": False,
        "confidence": 0.0,
        "mode": "disabled",
        "has_audio": False,
        "segments": [{"info": reason}],
        "output_video_bytes": None,
    }
