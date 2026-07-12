"""Chat question router for the multi-agent chat (Phase 3).

A cheap classification call (flash-lite) maps a user question → the 1-3 most relevant specialist
lenses + whether it's genuinely cross-domain (→ synthesize) or focused (→ a single specialist, the
fast path). NEVER raises: any failure / bad JSON / quota falls back to the general specialist in
single mode, so routing can never break the chat.
"""

import json
import logging
from typing import Any, Dict, List

from app.services.agents.chat_specialists import SPECIALIST_KEYS, get_specialist

logger = logging.getLogger(__name__)

# A small/cheap model keeps the pre-answer routing latency low.
_ROUTER_MODEL = "gemini-2.5-flash-lite"
_ROUTER_SYSTEM = (
    "You classify investing questions for an assistant. Output STRICT JSON only. "
    "Never mention being an AI, a model, or any provider."
)
_VALID = set(SPECIALIST_KEYS)


def _fallback() -> Dict[str, Any]:
    return {"specialists": ["general"], "mode": "single", "labels": ["General"]}


async def route_question(gemini: Any, user_message: str) -> Dict[str, Any]:
    """Classify ``user_message`` into specialist lenses.

    Returns ``{"specialists": [key, ...], "mode": "single"|"synthesize", "labels": [str, ...]}``.
    ``synthesize`` (multiple specialists merged) is chosen ONLY when the model flags the question as
    genuinely cross-domain AND returns >1 lens; otherwise a single focused specialist (fast path).
    Never raises.
    """
    msg = (user_message or "").strip()
    if not msg:
        return _fallback()
    try:
        prompt = (
            "Classify this investing question into the most relevant analyst LENSES.\n"
            "LENSES: valuation, technicals, fundamentals, macro, sentiment, education, general.\n\n"
            f"QUESTION: {msg[:400]}\n\n"
            "Rules:\n"
            "- Pick the SINGLE best lens for a focused question.\n"
            "- Pick 2-3 lenses ONLY if the question genuinely spans multiple domains "
            "(e.g. 'is X a good long-term buy?' → valuation + fundamentals; "
            "'why is the market shaky and should I worry about my tech stocks?' → macro + sentiment).\n"
            "- 'education' for concept explanations; 'general' if nothing else fits.\n"
            'Return ONLY JSON: {"specialists": ["lens", ...], "cross_domain": true|false}'
        )
        res = await gemini.generate_json(prompt, system_instruction=_ROUTER_SYSTEM, model_name=_ROUTER_MODEL)
        data = json.loads((res or {}).get("text") or "{}")
        raw = data.get("specialists") or []
        keys: List[str] = []
        for k in raw:
            if isinstance(k, str):
                kk = k.strip().lower()
                if kk in _VALID and kk not in keys:
                    keys.append(kk)
        keys = keys[:3]
        if not keys:
            keys = ["general"]
        cross_domain = bool(data.get("cross_domain")) and len(keys) > 1
        mode = "synthesize" if cross_domain else "single"
        if mode == "single":
            keys = keys[:1]
        return {
            "specialists": keys,
            "mode": mode,
            "labels": [get_specialist(k).label for k in keys],
        }
    except Exception as e:
        logger.warning("Chat router failed (%s: %s) — defaulting to general", type(e).__name__, e)
        return _fallback()
