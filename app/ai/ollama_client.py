"""Ollama HTTP client. Returns (parsed_dict_or_text_dict, raw_response)."""
import base64
import json
import re
from typing import Optional

import httpx

from app.core.config import settings


_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")
_FENCE_RE = re.compile(r"```(?:json)?\s*")


def _strip_thoughts(text: str) -> str:
    # Some Ollama models emit <unused94>thought<unused95>; strip them.
    return re.sub(r"<unused\d+>.*?<unused\d+>", "", text, flags=re.DOTALL).strip()


def extract_json(text: str) -> Optional[dict]:
    """Best-effort JSON extraction from a model response."""
    if not text:
        return None
    cleaned = _FENCE_RE.sub("", text).replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    m = _JSON_BLOCK_RE.search(cleaned)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


async def ollama_vision(
    image_bytes: bytes,
    prompt: str,
    json_response: bool = True,
) -> tuple[dict, str]:
    """Call Ollama vision model. Raises on transport / parse failure."""
    b64 = base64.b64encode(image_bytes).decode()
    payload = {
        "model": settings.OLLAMA_VISION_MODEL,
        "prompt": prompt,
        "images": [b64],
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 1024},
    }
    url = f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/generate"

    async with httpx.AsyncClient(timeout=settings.OLLAMA_REQUEST_TIMEOUT) as client:
        r = await client.post(url, json=payload)
        if r.status_code != 200:
            raise RuntimeError(f"Ollama HTTP {r.status_code}: {r.text[:200]}")
        raw = _strip_thoughts(r.json().get("response", ""))

    if json_response:
        data = extract_json(raw)
        if data is None:
            raise ValueError("Ollama did not return parseable JSON")
        return data, raw

    return {"text": raw}, raw


async def ollama_text(
    prompt: str,
    system: Optional[str] = None,
    json_response: bool = False,
) -> tuple[dict, str]:
    """Call Ollama text model via /api/chat for cleaner system prompt support."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": settings.OLLAMA_TEXT_MODEL,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.2},
    }
    if json_response:
        payload["format"] = "json"

    url = f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/chat"

    async with httpx.AsyncClient(timeout=settings.OLLAMA_REQUEST_TIMEOUT) as client:
        r = await client.post(url, json=payload)
        if r.status_code != 200:
            raise RuntimeError(f"Ollama HTTP {r.status_code}: {r.text[:200]}")
        raw = _strip_thoughts(r.json().get("message", {}).get("content", ""))

    if json_response:
        data = extract_json(raw)
        if data is None:
            raise ValueError("Ollama did not return parseable JSON")
        return data, raw

    return {"text": raw}, raw
