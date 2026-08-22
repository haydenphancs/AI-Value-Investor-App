"""Chat question router for the multi-agent chat (Phase 3).

A cheap classification call (flash-lite) maps a user question → the most relevant specialist
lenses + whether it's genuinely cross-domain (→ synthesize) or focused (→ a single specialist, the
fast path). NEVER raises: any failure / bad JSON / quota falls back to the general specialist in
single mode, so routing can never break the chat.
"""

import json
import logging
from typing import Any, Dict, List

from app.config import settings
from app.services.agents.chat_specialists import SPECIALIST_KEYS, get_specialist

logger = logging.getLogger(__name__)

# A small/cheap model keeps the pre-answer routing latency low.
_ROUTER_MODEL = "gemini-2.5-flash-lite"
_ROUTER_SYSTEM = (
    "You classify investing questions for an assistant. Output STRICT JSON only. "
    "Never mention being an AI, a model, or any provider."
)
_VALID = set(SPECIALIST_KEYS)

# Lenses that answer a CONCEPT rather than interrogate a specific security. These are
# the only classifications eligible for the cheap model, and only when the turn also
# carries no ticker and no on-screen data (see `select_model`).
_CONCEPTUAL_SPECIALISTS = frozenset({"education", "general"})


def _fallback() -> Dict[str, Any]:
    # `degraded` marks "we did not actually classify this" — distinct from a genuine
    # `general` classification. Without it the two are indistinguishable downstream,
    # and a Gemini outage (every turn falling back to `general`) would silently
    # downgrade the WHOLE product to the cheap model. See `select_model`.
    return {"specialists": ["general"], "mode": "single", "labels": ["General"], "degraded": True}


def _max_specialists() -> int:
    """How many lenses a synthesize turn may run. Read per call, never captured at import,
    so the Railway env var takes effect on restart without a redeploy.

    Floored at 1: a 0 or negative value would leave `keys` empty and send every turn down
    the `_fallback()` general path, which is a silent product outage rather than a cost cap.
    """
    return max(1, int(getattr(settings, "CHAT_MAX_SPECIALISTS", 2) or 1))


def _multi_lens_phrase() -> str:
    """The prompt's cross-domain instruction, matched to the cap.

    Asking for "2-3" while truncating to 2 would make the model rank three lenses and let
    us silently discard the one it may have weighted highest; asking for the cap directly
    means the lenses we keep are the ones it actually chose.
    """
    cap = _max_specialists()
    if cap <= 1:
        return "a single lens"
    if cap == 2:
        return "exactly 2 lenses"
    return f"2-{cap} lenses"


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
            f"- Pick {_multi_lens_phrase()} ONLY if the question genuinely spans multiple domains "
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
        keys = keys[: _max_specialists()]
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
            "degraded": False,
        }
    except Exception as e:
        logger.warning("Chat router failed (%s: %s) — defaulting to general", type(e).__name__, e)
        return _fallback()


def select_model(
    route: Dict[str, Any],
    *,
    has_ticker: bool,
    has_client_context: bool,
) -> str:
    """Choose the generation model for this turn. PURE — no I/O, no extra LLM call.

    `route_question` already classified the turn on the critical path, so reading its
    output costs nothing; this is the cheapest cost lever available.

    Downgrades to ``settings.CHAT_CHEAP_MODEL`` only when ALL of these hold:
      * routing is enabled,
      * the classification actually succeeded (never a degraded fallback),
      * exactly one lens was chosen (``single`` — a synthesized cross-domain answer
        is the hard case, so it keeps the flagship model),
      * that lens is conceptual (education / general),
      * the turn carries neither a ticker nor an on-screen data snapshot.

    The last condition is load-bearing and easy to miss: an ``education``
    classification on a STOCK screen ("what does this P/E mean?") still has to reason
    over a live grounding block and call tools, which is exactly the work the cheap
    model is worse at. Ticker-less conceptual questions are the safe set.

    Every unknown falls back to the EXPENSIVE model on purpose: the downside of a
    wrong cheap answer (a user reads it) outweighs the downside of a wrong expensive
    one (it costs a fraction of a cent). Fail closed here means fail to *better*.
    """
    if not settings.CHAT_MODEL_ROUTING_ENABLED:
        return settings.GEMINI_MODEL
    # Absent `degraded` is treated as degraded: any caller constructing a route dict
    # by hand has not proven a classification happened.
    if route.get("degraded", True):
        return settings.GEMINI_MODEL
    if has_ticker or has_client_context:
        return settings.GEMINI_MODEL
    if route.get("mode") != "single":
        return settings.GEMINI_MODEL
    specialists = route.get("specialists") or []
    if len(specialists) != 1 or specialists[0] not in _CONCEPTUAL_SPECIALISTS:
        return settings.GEMINI_MODEL
    return settings.CHAT_CHEAP_MODEL
