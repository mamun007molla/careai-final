"""Nutrition estimation from a food image.

Uses the shared AI router (Ollama → Groq fallback). Output is treated as a
SUGGESTION — the frontend lets the user edit every field before saving.

Model output is bounded to plausible ranges. We never trust the model
blindly: nonsensical values get nulled out so the UI shows blanks rather
than "5000 cal pizza".
"""
from typing import Optional

from app.ai.ai_router import run_vision


_PROMPT = """You are a clinical nutritionist analyzing a food photo for an elderly patient.

Identify the food in the image and estimate the nutritional content per the visible serving size.

Be CONSERVATIVE with calorie estimates — better to underestimate than overestimate for elderly users.
If the image is unclear or contains no food, set "calories" to null.

Respond with ONLY this JSON, no commentary:
{
  "description": "short food description, e.g. 'rice with daal and grilled chicken (1 plate)'",
  "calories": 450,
  "protein_g": 25,
  "carbs_g": 60,
  "fat_g": 12,
  "confidence": 0.7
}"""


def _bound(value, low: float, high: float) -> Optional[float]:
    """Coerce to float and bound; return None if invalid."""
    try:
        v = float(value)
        if v < low or v > high:
            return None
        return round(v, 1)
    except (TypeError, ValueError):
        return None


async def estimate_nutrition(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
    ai_result = await run_vision(image_bytes, _PROMPT, mime_type=mime_type, json_response=True)
    p = ai_result.data

    return {
        "description": str(p.get("description", "Unknown food")).strip()[:500],
        "calories":    _bound(p.get("calories"),  0, 5000),
        "protein_g":   _bound(p.get("protein_g"), 0, 500),
        "carbs_g":     _bound(p.get("carbs_g"),   0, 800),
        "fat_g":       _bound(p.get("fat_g"),     0, 500),
        "confidence":  _bound(p.get("confidence"), 0, 1) or 0.5,
        "ai_provider": ai_result.provider,
        "ai_fallback_used": ai_result.fallback_used,
        "ai_fallback_reason": ai_result.fallback_reason,
        "raw_response": ai_result.raw_text,
    }
