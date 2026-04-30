"""Medication verification service.

Strategy:
1. Vision model describes what it sees in the image (uses ai_router → Ollama or Groq).
2. We then run a name-match heuristic on the AI's textual description against the
   prescribed medication, with brand/generic alias support.
3. Confidence is derived from match strength, not the model's self-reported number
   (models tend to be overconfident).
"""
import re
from typing import Tuple

from app.ai.ai_router import run_vision


# Brand ↔ generic aliases — extend as needed for your region
ALIASES = {
    "napa":         ["paracetamol", "acetaminophen", "panadol", "napa extra", "ace"],
    "napa extra":   ["paracetamol", "caffeine", "napa"],
    "paracetamol":  ["napa", "ace", "tamen", "panadol", "calpol", "acetaminophen"],
    "metformin":    ["glucophage", "glycomet"],
    "amlodipine":   ["norvasc", "stamlo"],
    "aspirin":      ["ecotrin", "disprin"],
    "omeprazole":   ["losec", "prilosec"],
    "atorvastatin": ["lipitor", "atocor"],
    "losartan":     ["losan", "cozaar"],
}


def names_match(prescribed: str, detected_text: str) -> Tuple[bool, float]:
    """Return (matched, confidence) for a prescribed name vs. detected text."""
    if not detected_text:
        return False, 0.0

    p = prescribed.lower().strip()
    d = detected_text.lower()

    # Direct substring match
    if p in d:
        return True, 0.95

    # Alias match (brand ↔ generic)
    for alias in ALIASES.get(p, []):
        if alias in d:
            return True, 0.80

    # Reverse: prescribed is generic, detected has brand → check inverted aliases
    for canonical, aliases in ALIASES.items():
        if p == canonical:
            continue
        if p in aliases and canonical in d:
            return True, 0.80

    # Token overlap
    words = [w for w in re.split(r"\W+", p) if len(w) > 2]
    if words:
        hit_ratio = sum(1 for w in words if w in d) / len(words)
        if hit_ratio >= 0.5:
            return True, 0.65

    # Prefix / suffix similarity for typos / OCR errors
    if len(p) >= 4 and (p[:4] in d or p[-4:] in d):
        return True, 0.55

    return False, 0.0


_VERIFY_PROMPT = """You are verifying medication for an elderly patient.

PRESCRIBED MEDICATION: {prescribed}

Look at the image carefully and report:
1. What you see (pill colour, shape, blister/box label text, anything readable)
2. Whether the label or the pills match "{prescribed}" or a known equivalent

Respond with ONLY this JSON:
{{
  "matched": true,
  "confidence": 0.85,
  "detected": "describe what you see — pill colour, packaging, name visible",
  "match_reason": "why you decided matched or not",
  "warnings": []
}}"""


async def verify_medication_image(
    image_bytes: bytes,
    prescribed: str,
    mime_type: str = "image/jpeg",
) -> dict:
    """Run AI vision and apply heuristic matching. Returns a dict ready for the schema."""
    prompt = _VERIFY_PROMPT.format(prescribed=prescribed)

    ai_result = await run_vision(image_bytes, prompt, mime_type=mime_type, json_response=True)
    payload = ai_result.data
    detected = str(payload.get("detected", "")).strip()

    # Trust the heuristic over the model's self-reported `matched`.
    if detected and len(detected) >= 5:
        matched, confidence = names_match(prescribed, detected)
    else:
        matched = bool(payload.get("matched", False))
        confidence = float(payload.get("confidence", 0.3))

    warnings = list(payload.get("warnings", []))
    if not matched:
        warnings.append(
            f"Could not confirm '{prescribed}' from the image. "
            f"Detected: {detected[:120] or '(nothing readable)'}"
        )

    return {
        "matched": matched,
        "confidence": round(confidence, 2),
        "detected_medication": detected,
        "warnings": warnings,
        "raw_response": ai_result.raw_text,
        "ai_provider": ai_result.provider,
        "ai_fallback_used": ai_result.fallback_used,
        "ai_fallback_reason": ai_result.fallback_reason,
        "latency_ms": ai_result.latency_ms,
    }
