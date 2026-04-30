"""AI helpers for Module 4 — disease classification + report summarization."""
from app.ai.ai_router import run_text, run_vision


# ──────────────────────────────────────────────────────────────────────────────
# Disease classification — vision AI
# ──────────────────────────────────────────────────────────────────────────────
_CATEGORY_PROMPTS = {
    "skin": """You are assisting a doctor analyzing a dermatology image.
Identify what skin condition(s) might be visible. NEVER claim certainty —
this is decision support only.""",

    "xray": """You are assisting a doctor analyzing an X-ray image.
Identify visible structures and any abnormalities. NEVER claim certainty.
This is decision support, not diagnosis.""",

    "general": """You are assisting a doctor analyzing a medical image.
Describe what you see and suggest possibilities for review.""",
}


async def classify_disease(image_bytes: bytes, category: str,
                            clinical_notes: str = "", mime_type: str = "image/jpeg") -> dict:
    """Vision AI classifies the image; returns ranked candidates + summary."""
    base = _CATEGORY_PROMPTS.get(category, _CATEGORY_PROMPTS["general"])
    notes_section = f"\n\nClinical context from doctor: {clinical_notes}" if clinical_notes else ""

    prompt = f"""{base}{notes_section}

Analyze the image and respond with ONLY this JSON:
{{
  "predictions": [
    {{"name": "Most likely condition", "confidence": 0.7, "description": "Brief description"}},
    {{"name": "Alternative", "confidence": 0.2, "description": "..."}}
  ],
  "summary": "What you observe in 1-2 sentences",
  "recommendations": "Suggested next steps for the doctor"
}}

IMPORTANT: Confidence scores should be conservative. If image is unclear,
return predictions with low confidence and say so in the summary. Always
emphasize this is decision support, not diagnosis."""

    result = await run_vision(image_bytes, prompt, mime_type=mime_type, json_response=True)
    data = result.data

    # Validate predictions list
    raw_preds = data.get("predictions", [])
    if not isinstance(raw_preds, list):
        raw_preds = []
    preds = []
    for p in raw_preds[:5]:
        if not isinstance(p, dict):
            continue
        try:
            conf = float(p.get("confidence", 0))
            conf = max(0.0, min(1.0, conf))
        except (TypeError, ValueError):
            conf = 0.0
        preds.append({
            "name": str(p.get("name", "Unknown"))[:200],
            "confidence": round(conf, 2),
            "description": str(p.get("description", ""))[:500],
        })

    return {
        "predictions": preds,
        "summary": str(data.get("summary", ""))[:1000],
        "recommendations": str(data.get("recommendations", ""))[:1000],
        "ai_provider": result.provider,
        "ai_fallback_used": result.fallback_used,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Report summarization — text AI
# ──────────────────────────────────────────────────────────────────────────────
async def summarize_report(report_text: str) -> dict:
    """Extract structured info from a medical report."""
    prompt = f"""You are a clinical assistant summarizing a medical report
for a doctor's quick review.

REPORT TEXT:
{report_text[:15000]}

Extract a structured summary. Respond with ONLY this JSON:
{{
  "summary_text": "2-4 sentence overview of the report",
  "key_findings": [
    "Finding 1",
    "Finding 2"
  ],
  "abnormal_values": [
    {{"name": "Hemoglobin", "value": "9.2 g/dL", "ref_range": "13.5-17.5 g/dL", "flag": "low"}},
    {{"name": "Blood pressure", "value": "150/95", "ref_range": "<130/80", "flag": "high"}}
  ],
  "recommendations": [
    "Follow up on abnormal X",
    "Consider Y"
  ]
}}

If a section doesn't apply, return empty array. Use only data from the report."""

    result = await run_text(prompt, json_response=True)
    data = result.data

    # Defensive parsing
    def _str_list(field):
        v = data.get(field, [])
        if not isinstance(v, list):
            return []
        return [str(x)[:500] for x in v if x][:10]

    abnormal = data.get("abnormal_values", [])
    if not isinstance(abnormal, list):
        abnormal = []
    parsed_abnormal = []
    for a in abnormal[:20]:
        if isinstance(a, dict):
            parsed_abnormal.append({
                "name": str(a.get("name", ""))[:100],
                "value": str(a.get("value", ""))[:50],
                "ref_range": str(a.get("ref_range", ""))[:100] or None,
                "flag": str(a.get("flag", "abnormal")).lower()[:20],
            })

    return {
        "summary_text": str(data.get("summary_text", ""))[:2000],
        "key_findings": _str_list("key_findings"),
        "abnormal_values": parsed_abnormal,
        "recommendations": _str_list("recommendations"),
        "ai_provider": result.provider,
        "ai_fallback_used": result.fallback_used,
    }
