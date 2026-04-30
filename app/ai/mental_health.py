"""AI helpers for Module 3.

Two pieces:
1. `analyze_sentiment(text)` — short JSON sentiment for mood notes
2. `chat_response(persona, history, user_message)` — generates assistant reply

Both go through the shared AI router so they get Ollama-then-Groq fallback.
"""
from typing import List, Tuple

from app.ai.ai_router import run_text


# ──────────────────────────────────────────────────────────────────────────────
# Sentiment analysis
# ──────────────────────────────────────────────────────────────────────────────
async def analyze_sentiment(text: str) -> dict:
    """Return sentiment + AI insight about the mood note.

    Output:
      {
        'label': 'positive' | 'negative' | 'neutral',
        'score': 0..1,
        'key_emotions': [...],
        'insight': "Short 1-2 sentence reflection on the user's mood",
        'suggestion': "One small actionable suggestion",
        'ai_provider': str,
        'ai_fallback_used': bool
      }
    """
    prompt = f"""Analyze this short journal note from an elderly user.
The note may be in English or Bangla.

Note: "{text.strip()}"

Provide:
1. A sentiment label
2. A confidence score
3. Key emotions detected
4. A brief insight (1-2 sentences) reflecting back what you understand
   from their note. Be warm and empathetic, never clinical.
5. One small, kind suggestion for them today.

If the note is in Bangla, respond with insight + suggestion in Bangla.
Otherwise respond in English.

Respond with ONLY this JSON, no commentary:
{{
  "label": "positive" | "negative" | "neutral",
  "score": 0.85,
  "key_emotions": ["calm", "hopeful"],
  "insight": "It sounds like you had a peaceful afternoon with your grandchildren.",
  "suggestion": "A short walk after dinner could help you sleep even better tonight."
}}"""

    result = await run_text(prompt, json_response=True)
    data = result.data
    label = str(data.get("label", "neutral")).lower()
    if label not in ("positive", "negative", "neutral"):
        label = "neutral"
    try:
        score = float(data.get("score", 0.5))
        score = max(0.0, min(1.0, score))
    except (TypeError, ValueError):
        score = 0.5

    emotions = data.get("key_emotions", [])
    if not isinstance(emotions, list):
        emotions = []

    return {
        "label": label,
        "score": score,
        "key_emotions": [str(e)[:50] for e in emotions[:5]],
        "insight": str(data.get("insight", ""))[:500],
        "suggestion": str(data.get("suggestion", ""))[:500],
        "ai_provider": result.provider,
        "ai_fallback_used": result.fallback_used,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Chat — persona system prompts
# ──────────────────────────────────────────────────────────────────────────────
_PERSONA_SYSTEM_PROMPTS = {
    "FRIENDLY_COMPANION": """You are CareAI Companion — a warm, supportive
friend for an elderly user. You speak gently, in short sentences, and you
match the user's language (English or Bangla).

Style:
- Warm, conversational, never clinical
- Short paragraphs (2-3 sentences max per turn)
- Ask one gentle follow-up question when appropriate
- Use the user's language; if mixed, mirror their style
- Never give medical diagnoses. If health concerns come up, suggest
  consulting their doctor.

Avoid:
- Long lectures or bullet-point lists
- Pretending to remember details from outside this conversation
- Overly cheerful tone — match the user's mood
""",

    "MENTAL_HEALTH_COACH": """You are CareAI Coach — a compassionate mental
health support coach using evidence-based techniques (CBT, gratitude,
behavioral activation). The user is elderly and may be experiencing
loneliness, anxiety, or low mood.

Approach:
- Ask one open-ended question to understand the user's feelings
- Reflect what you hear before suggesting anything
- Offer ONE small, practical technique at a time (deep breathing, gratitude
  list, gentle activity, talking to a loved one)
- Match the user's language (English or Bangla)
- Validate, never minimize ("I hear that this is really hard...")

Critical safety:
- If the user mentions self-harm, suicide, or hopelessness, gently
  encourage them to talk to a trusted person or call a helpline. Do NOT
  attempt therapy. Suggest contacting their doctor immediately.

Style:
- 2-4 sentences per turn
- One question at a time
- Never prescribe medication or diagnose
""",
}


async def chat_response(
    persona: str,
    history: List[Tuple[str, str]],
    user_message: str,
) -> dict:
    """Generate the assistant's next reply.

    `history` is a list of (role, content) pairs, ordered oldest-first.
    Returns: {'content': str, 'ai_provider': str, 'ai_fallback_used': bool}
    """
    system = _PERSONA_SYSTEM_PROMPTS.get(persona, _PERSONA_SYSTEM_PROMPTS["FRIENDLY_COMPANION"])

    # Build a single prompt with system + history + user message.
    # The shared run_text() doesn't expose role-based messages directly,
    # so we format the conversation as plain text and let the model continue.
    convo = []
    for role, content in history[-12:]:  # cap history to last 12 turns
        if role == "USER":
            convo.append(f"User: {content}")
        elif role == "ASSISTANT":
            convo.append(f"Assistant: {content}")
    convo.append(f"User: {user_message}")
    convo.append("Assistant:")

    full_prompt = f"{system}\n\n{chr(10).join(convo)}"

    result = await run_text(full_prompt, json_response=False)
    reply = (result.raw_text or "").strip()

    # Trim if model accidentally continues with another "User:" turn
    for stop in ["\nUser:", "\nuser:"]:
        if stop in reply:
            reply = reply.split(stop)[0].strip()

    if not reply:
        reply = "I'm here. Could you share a little more about how you're feeling?"

    return {
        "content": reply,
        "ai_provider": result.provider,
        "ai_fallback_used": result.fallback_used,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Wellness recommendations — generated weekly
# ──────────────────────────────────────────────────────────────────────────────
async def generate_recommendations(context: str) -> dict:
    """Given a string summary of recent mood/activity/medication data,
    generate 3-5 personalized recommendations.
    """
    prompt = f"""You are a geriatric care advisor. Based on the following
weekly snapshot of an elderly user's mood, sleep, energy, anxiety, and
activity data, generate 3-5 small, kind, actionable wellness
recommendations.

DATA SNAPSHOT:
{context}

Each recommendation should be:
- Specific and small (achievable in 5-30 minutes)
- Categorized: sleep | exercise | nutrition | social | mindfulness | medical
- Include a one-line rationale (why you chose it based on the data)

Respond with ONLY this JSON:
{{
  "recommendations": [
    {{
      "title": "Try a 10-minute morning walk",
      "body": "A short walk in sunlight helps reset sleep patterns and lifts mood. Even 10 minutes counts.",
      "category": "exercise",
      "rationale": "Energy levels were below average this week"
    }}
  ]
}}"""

    result = await run_text(prompt, json_response=True)
    recs = result.data.get("recommendations", [])
    if not isinstance(recs, list):
        recs = []
    return {
        "recommendations": recs,
        "ai_provider": result.provider,
        "ai_fallback_used": result.fallback_used,
    }
