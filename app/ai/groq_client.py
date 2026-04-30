"""Groq client — used as fallback when Ollama is unavailable."""
import base64
from typing import Optional

from groq import AsyncGroq

from app.ai.ollama_client import extract_json
from app.core.config import settings


def _client() -> AsyncGroq:
    if not settings.GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not configured")
    return AsyncGroq(api_key=settings.GROQ_API_KEY)


async def groq_vision(
    image_bytes: bytes,
    prompt: str,
    mime_type: str = "image/jpeg",
    json_response: bool = True,
) -> tuple[dict, str]:
    b64 = base64.b64encode(image_bytes).decode()
    client = _client()
    resp = await client.chat.completions.create(
        model=settings.GROQ_VISION_MODEL,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
            ],
        }],
        max_tokens=1024,
        temperature=0.1,
    )
    raw = (resp.choices[0].message.content or "").strip()

    if json_response:
        data = extract_json(raw)
        if data is None:
            raise ValueError("Groq did not return parseable JSON")
        return data, raw

    return {"text": raw}, raw


async def groq_text(
    prompt: str,
    system: Optional[str] = None,
    json_response: bool = False,
) -> tuple[dict, str]:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    kwargs = {
        "model": settings.GROQ_TEXT_MODEL,
        "messages": messages,
        "max_tokens": 1024,
        "temperature": 0.2,
    }
    if json_response:
        kwargs["response_format"] = {"type": "json_object"}

    client = _client()
    resp = await client.chat.completions.create(**kwargs)
    raw = (resp.choices[0].message.content or "").strip()

    if json_response:
        data = extract_json(raw)
        if data is None:
            raise ValueError("Groq did not return parseable JSON")
        return data, raw

    return {"text": raw}, raw
