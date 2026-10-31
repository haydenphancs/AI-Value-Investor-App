"""
Claude as a SECOND provider, Claude first with Gemini as the automatic fallback — for four roles
and nothing else (owner, 2026-10-30: "it will not replace anything ... a second option like a
fall back option").

The roles are picked by the `usage_tag` a caller already sends to `generate_json`:

    marketing_writer      → MARKETING_WRITER_PROVIDER   (writer model)
    marketing_judge       → MARKETING_JUDGE_PROVIDER    (judge model)
    learn_weekly_writer   → LEARN_WRITER_PROVIDER       (writer model)
    learn_weekly_judge    → LEARN_JUDGE_PROVIDER        (judge model)
    learn_weekly_support  → LEARN_JUDGE_PROVIDER        (judge model)

`ProviderRouter.generate_json(prompt, **kwargs)` takes EXACTLY the Gemini call. When a role is
"gemini" (the default), Claude is not configured, the breaker is open, or the tag is any other,
it forwards the call to `GeminiClient.generate_json` untouched and returns its result as is —
byte-identical to before. When a role is "claude" it asks Claude with the same prompt, system
instruction and schema; on ANY Claude failure (refusal, timeout, overload, used-up credits, a
truncated or empty answer) it logs `CLAUDE_FALLBACK` and makes the original Gemini call.

Every result the router produced on a Claude-routed role carries `provider` ("claude" | "gemini")
and, after a fallback, `fallback_reason`; `tokens_used` adds the failed Claude call's tokens so a
caller's token total still counts every billed token.

Settings are read at CALL time, so flipping a role needs no code — but `script_service` sizes the
marketing lease at import, so a marketing switch takes effect with the deploy that sets it.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, Optional, Tuple

from app.config import settings
from app.integrations import anthropic_client as ac
from app.services.llm_schema import gemini_schema_to_json_schema

logger = logging.getLogger(__name__)

PROVIDER_GEMINI = "gemini"
PROVIDER_CLAUDE = "claude"
PROVIDERS = (PROVIDER_GEMINI, PROVIDER_CLAUDE)

KIND_WRITER = "writer"
KIND_JUDGE = "judge"

#: usage_tag → (the setting naming its provider, writer or judge).
ROLES: Dict[str, Tuple[str, str]] = {
    "marketing_writer": ("MARKETING_WRITER_PROVIDER", KIND_WRITER),
    "marketing_judge": ("MARKETING_JUDGE_PROVIDER", KIND_JUDGE),
    "learn_weekly_writer": ("LEARN_WRITER_PROVIDER", KIND_WRITER),
    "learn_weekly_judge": ("LEARN_JUDGE_PROVIDER", KIND_JUDGE),
    "learn_weekly_support": ("LEARN_JUDGE_PROVIDER", KIND_JUDGE),
}
#: The marketing roles — the lease arithmetic in script_service asks whether any is on Claude.
MARKETING_SETTINGS = ("MARKETING_WRITER_PROVIDER", "MARKETING_JUDGE_PROVIDER")

#: Effort levels the API accepts; anything else is sent as "low" (the configured floor).
EFFORTS = ("low", "medium", "high", "xhigh", "max")
#: Models that take `thinking: {"type": "between_tools"}` — Sonnet 5.5's way to turn thinking off,
#: accepted only at effort high or below. Every other current model thinks adaptively (Opus 5.5
#: cannot turn it off); a caller's thinking_budget=0 then just means "low effort".
_BETWEEN_TOOLS_MODELS = ("claude-sonnet-5-5",)
_BETWEEN_TOOLS_EFFORTS = ("low", "medium", "high")

#: The attribute the router sets on an exception raised by the Gemini FALLBACK call: the tokens the
#: failed Claude attempt before it billed (a truncated answer can bill ~16k). Kept apart from every
#: caller's own token attribute (`tokens_used`, `marketing_tokens_used`), which callers overwrite
#: with their own running totals; the four callers add this to theirs (`claude_tokens_of`).
CLAUDE_TOKENS_ATTR = "claude_tokens_used"

_breaker_until: float = 0.0
_breaker_reason: str = ""
_logged_once: set = set()


def _error_once(key: str, msg: str, *args: Any) -> None:
    if key not in _logged_once:
        _logged_once.add(key)
        logger.error(msg, *args)


_SHOWABLE_RE = re.compile(r"[A-Za-z0-9._ -]{0,24}")


def _shown(raw: Any) -> str:
    """A bad setting value as it may be logged. These settings have a closed vocabulary (a provider,
    an effort, a model id), so only a short word-like value is echoed; anything else — a key pasted
    into the wrong variable (Gemini's `AIza…` matches no redaction pattern) — is shown by length."""
    s = "" if raw is None else str(raw)
    return repr(s) if _SHOWABLE_RE.fullmatch(s) else f"<{len(s)} chars, not shown>"


def claude_tokens_of(e: BaseException) -> int:
    """The failed Claude attempt's tokens the router put on `e` (CLAUDE_TOKENS_ATTR); 0 when none.
    Never NaN, negative or a bool."""
    v = getattr(e, CLAUDE_TOKENS_ATTR, 0)
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0


def provider_setting(name: str) -> str:
    """A role switch's value, normalised. Unknown → gemini, with ONE ERROR per bad value."""
    raw = getattr(settings, name, PROVIDER_GEMINI)
    value = str(raw or "").strip().lower() or PROVIDER_GEMINI
    if value not in PROVIDERS:
        _error_once(f"bad:{name}:{value}",
                    "llm_provider: %s=%s is not one of %s — using gemini", name, _shown(raw), PROVIDERS)
        return PROVIDER_GEMINI
    return value


def any_marketing_role_on_claude() -> bool:
    """True when a marketing role would ask Claude first (switch on AND a key set). Sizes the
    script lease: each such call can take Claude's deadline AND then Gemini's worst case."""
    return ac.is_configured() and any(provider_setting(s) == PROVIDER_CLAUDE
                                      for s in MARKETING_SETTINGS)


def claude_model_for(kind: str) -> Optional[str]:
    """The Claude model of a role kind, or None when the judge would be the writer's model (a
    grader that shares the writer's blind spots is refused: that judge role stays on Gemini)."""
    writer = str(settings.CAYDEX_CLAUDE_WRITER_MODEL or "").strip()
    judge = str(settings.CAYDEX_CLAUDE_JUDGE_MODEL or "").strip()
    if kind == KIND_WRITER:
        return writer or None
    if not judge or judge == writer:
        _error_once(f"judge_eq_writer:{judge}",
                    "llm_provider: CAYDEX_CLAUDE_JUDGE_MODEL=%s equals CAYDEX_CLAUDE_WRITER_MODEL — the judge "
                    "roles stay on Gemini (a grader must not share the writer's model)", _shown(judge))
        return None
    return judge


def breaker_open(now: Optional[float] = None) -> bool:
    return (time.monotonic() if now is None else now) < _breaker_until


def _open_breaker(reason: str) -> None:
    global _breaker_until, _breaker_reason
    seconds = max(float(settings.CAYDEX_CLAUDE_BREAKER_SECONDS), 0.0)
    _breaker_until = time.monotonic() + seconds
    _breaker_reason = reason
    logger.error("CLAUDE_BREAKER open for %.0fs (%s) — every Claude role uses Gemini until then",
                 seconds, reason)


def reset_breaker() -> None:
    """Tests only."""
    global _breaker_until, _breaker_reason
    _breaker_until = 0.0
    _breaker_reason = ""


def effective_effort() -> str:
    """The effort actually sent to Claude (an unknown CAYDEX_CLAUDE_EFFORT is sent as "low") — what
    a measurement script must record."""
    return _effort()


def _effort() -> str:
    raw = str(settings.CAYDEX_CLAUDE_EFFORT or "").strip().lower()
    if raw not in EFFORTS:
        _error_once(f"effort:{raw}", "llm_provider: CAYDEX_CLAUDE_EFFORT=%s is not one of %s — using low",
                    _shown(settings.CAYDEX_CLAUDE_EFFORT), EFFORTS)
        return "low"
    return raw


def _thinking(model: str, thinking_budget: Optional[int], effort: str) -> Optional[Dict[str, Any]]:
    if thinking_budget == 0 and model in _BETWEEN_TOOLS_MODELS and effort in _BETWEEN_TOOLS_EFFORTS:
        return {"type": "between_tools"}
    return None


def route_for(usage_tag: Optional[str]) -> Optional[Tuple[str, str]]:
    """(role setting, Claude model) when this call should ask Claude first, else None."""
    role = ROLES.get(str(usage_tag or ""))
    if role is None:
        return None
    setting, kind = role
    if provider_setting(setting) != PROVIDER_CLAUDE:
        return None
    if not ac.is_configured():
        _error_once(f"nokey:{setting}",
                    "llm_provider: %s=claude but CAYDEX_ANTHROPIC_API_KEY is not set — using gemini", setting)
        return None
    model = claude_model_for(kind)
    if model is None:
        return None
    return setting, model


async def _ask_claude(claude: Any, model: str, prompt: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """The Gemini call's arguments → the Claude call: the same prompt, system instruction and
    schema (translated); temperature, cache and service_tier have no Claude equivalent and are
    dropped; thinking_budget=0 becomes Sonnet's between_tools where the model takes it."""
    effort = _effort()
    schema = kwargs.get("response_schema")
    return await claude.generate_json(
        prompt,
        json_schema=None if schema is None else gemini_schema_to_json_schema(schema),
        model_name=model,
        system_instruction=kwargs.get("system_instruction"),
        thinking=_thinking(model, kwargs.get("thinking_budget"), effort),
        effort=effort,
        usage_tag=kwargs.get("usage_tag"),
    )


#: Measurement retries (ClaudeOnly): only a rate limit or an outage is retried — never a refusal,
#: a truncation or a bad answer, which are what a calibration measures.
_MEASURE_RETRYABLE = (ac.ClaudeRateLimitException, ac.ClaudeUnavailableException)


class ClaudeOnly:
    """Claude with NO fallback, for measurement scripts (judge calibration, the writer preview): a
    fallback would mix Gemini answers into a Claude measurement. Raises ClaudeException.

    `retries` (default 0) re-asks after a 429 / 5xx / overload / timeout with a 2-4-8 s back-off, so
    one transport blip among hundreds of calibration calls is not scored as an unusable answer. The
    tokens of a failed attempt ride on the final exception's `tokens_used`."""

    def __init__(self, model: str, claude: Any = None, *, retries: int = 0,
                 sleep: Any = None):
        self.model = model
        self._claude = claude
        self.retries = max(int(retries), 0)
        self._sleep = sleep

    async def generate_json(self, prompt: str, **kwargs: Any) -> Dict[str, Any]:
        import asyncio

        claude = self._claude or ac.get_claude_client()
        sleep = self._sleep or asyncio.sleep
        spent = 0
        for attempt in range(self.retries + 1):
            try:
                result = await _ask_claude(claude, self.model, prompt, kwargs)
            except _MEASURE_RETRYABLE as e:
                spent += int(getattr(e, "tokens_used", 0) or 0)
                if attempt >= self.retries:
                    e.tokens_used = spent
                    raise
                logger.warning("ClaudeOnly %s: %s (%s) — retry %d of %d", self.model,
                               type(e).__name__, e, attempt + 1, self.retries)
                await sleep(2.0 * (2 ** attempt))
                continue
            except ac.ClaudeException as e:
                e.tokens_used = int(getattr(e, "tokens_used", 0) or 0) + spent
                raise
            return {**result, "provider": PROVIDER_CLAUDE,
                    "tokens_used": int(result.get("tokens_used") or 0) + spent}
        raise AssertionError("unreachable")  # pragma: no cover


class ProviderRouter:
    """Drop-in for `GeminiClient` in the four roles' `client` argument: `generate_json` only."""

    def __init__(self, gemini: Any = None, claude: Any = None):
        self._gemini = gemini
        self._claude = claude

    def _gemini_client(self) -> Any:
        if self._gemini is None:
            from app.integrations.gemini import get_gemini_client

            self._gemini = get_gemini_client()
        return self._gemini

    def _claude_client(self) -> Any:
        if self._claude is None:
            self._claude = ac.get_claude_client()
        return self._claude

    async def generate_json(self, prompt: str, **kwargs: Any) -> Dict[str, Any]:
        usage_tag = kwargs.get("usage_tag")
        route = route_for(usage_tag)
        if route is None:
            return await self._gemini_client().generate_json(prompt, **kwargs)
        setting, model = route
        if breaker_open():
            logger.warning("CLAUDE_FALLBACK tag=%s reason=breaker_open(%s) — Gemini",
                           usage_tag, _breaker_reason)
            result = await self._gemini_client().generate_json(prompt, **kwargs)
            return {**result, "provider": PROVIDER_GEMINI, "fallback_reason": "breaker_open"}

        try:
            result = await _ask_claude(self._claude_client(), model, prompt, kwargs)
        except Exception as e:
            # Exception only: a CancelledError (BaseException) propagates untouched. A typed Claude
            # failure is expected (WARNING); anything else is a bug on the Claude path — still the
            # Gemini call (the owner's "fall back" promise), but with the stack (ERROR).
            reason = type(e).__name__
            if isinstance(e, ac.BREAKER_EXCEPTIONS):
                _open_breaker(reason)
            if isinstance(e, ac.ClaudeException):
                logger.warning("CLAUDE_FALLBACK tag=%s model=%s reason=%s (%s) — the same call on "
                               "Gemini", usage_tag, model, reason, e)
            else:
                logger.error("CLAUDE_FALLBACK tag=%s model=%s reason=%s (%s: %s) — unexpected; the "
                             "same call on Gemini", usage_tag, model, reason, reason, e, exc_info=True)
            spent = int(getattr(e, "tokens_used", 0) or 0) if isinstance(e, ac.ClaudeException) else 0
            try:
                result = await self._gemini_client().generate_json(prompt, **kwargs)
            except Exception as ge:
                # Both providers failed: the Gemini error propagates unchanged in type, carrying the
                # Claude attempt's billed tokens so the caller's total still counts them.
                try:
                    setattr(ge, CLAUDE_TOKENS_ATTR, claude_tokens_of(ge) + spent)
                except (AttributeError, TypeError):   # a __slots__ / C-level exception type
                    logger.warning("CLAUDE_FALLBACK tag=%s: could not attach %d Claude tokens to %s — "
                                   "that spend will be missing from the caller's total", usage_tag,
                                   spent, type(ge).__name__)
                raise
            return {**result, "provider": PROVIDER_GEMINI, "fallback_reason": reason,
                    "tokens_used": int(result.get("tokens_used") or 0) + spent}
        return {**result, "provider": PROVIDER_CLAUDE}


_router: Optional[ProviderRouter] = None


def get_provider_router() -> ProviderRouter:
    global _router
    if _router is None:
        _router = ProviderRouter()
    return _router


def served_by(result: Any) -> str:
    """'claude:<model>' / 'gemini:<model>' / 'gemini' for one generate_json result — for the
    package record and the Telegram review line. A plain Gemini result (no router, or a role on
    gemini) reads 'gemini'."""
    if not isinstance(result, dict):
        return PROVIDER_GEMINI
    provider = result.get("provider") or PROVIDER_GEMINI
    if provider == PROVIDER_CLAUDE:
        return f"claude:{result.get('model') or '?'}"
    return f"gemini (Claude fallback: {result['fallback_reason']})" if result.get("fallback_reason") \
        else PROVIDER_GEMINI
