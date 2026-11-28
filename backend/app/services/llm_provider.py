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
caller's token total still counts every billed token. Whenever Claude billed, the result also
carries `claude_usage` (CLAUDE_USAGE_KEY): those Claude tokens by model and kind, so the marketing
cost line can price them at Claude's rates instead of Gemini's.

Settings are read at CALL time, so flipping a role needs no code — but `script_service` sizes the
marketing lease at import, so a marketing switch takes effect with the deploy that sets it.

The one other way to Claude is `ClaudeOnly`: Claude with NO Gemini fallback. It serves the
measurement scripts and — owner decision 2026-11-15, "Google draws, Claude checks" — the marketing
per-post ART brief and check (`services/marketing/post_art.py`), which pass `images` (vision: the
drawn picture), `max_tokens` (Opus 5.5 always thinks, and its thinking counts against it), a client
`deadline_seconds` that fits the art step's budget, an explicit `effort`, and the opt-in breaker
policy BREAKER_ACCOUNT. The art is NOT a role: ROLES, MARKETING_SETTINGS and
`any_marketing_role_on_claude()` — and so the script lease — are untouched by it.

`ClaudeOnly.cited_web_search` (2026-11-27) is the ONE app-feature use of Claude: the Updates
Insights jump search's fallback lookup (`services/jump_claude_lookup_service.py`, owner: "use Claude
API (200 credits as fallback)", "Claude Sonnet 5.5"; ships OFF). It finds news pages with the
server-side web search tool; Claude's prose is discarded. One attempt, the BREAKER_ACCOUNT read (an
open breaker = no call), and a NARROWER opening rule than the art's: only used-up credits or a 401
(`search_opens_account_breaker`) — a 403/400 refusal of the web tool (web search not enabled for the
organisation, a refused parameter) is that feature's problem and must not push the marketing roles
to Gemini for an hour. It is not a role either: ROLES and the lease never see it.

Claude's list prices (`CLAUDE_MICROS_PER_MILLION_TOKENS`, `claude_rates`) live here since
2026-11-27, so an app feature's spend cap never imports a marketing module;
`marketing/digest_service.py` re-exports both under their old names.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, Optional, Sequence, Tuple

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
#: The key of a router result, and the attribute of an exception, carrying the Claude tokens in it BY
#: MODEL AND KIND: {served model: {"input", "output", "cache_read", "cache_write"}}. A breakdown only —
#: the same tokens are already in `tokens_used` / CLAUDE_TOKENS_ATTR, so it is never added to a token
#: total. The marketing writer sums it into `marketing_scripts.claude_usage`, which the weekly cost
#: line prices per model (Claude's writer and judge are different models with different prices).
#: Absent on a plain Gemini result and whenever Claude billed nothing.
CLAUDE_USAGE_KEY = "claude_usage"

#: ClaudeOnly's opt-in breaker policy (2026-11-16). The default, None, is the measurement scripts'
#: behaviour: the shared breaker is neither read nor opened (a calibration must measure Claude, not
#: skip it). BREAKER_ACCOUNT — the marketing art brief and check — reads it (an open breaker raises
#: ClaudeBreakerOpenException with no call) and opens it ONLY on a failure of the account itself
#: (`anthropic_client.ACCOUNT_BREAKER_EXCEPTIONS`: used-up credits, a revoked key), never on a 429,
#: an overload or a timeout — those only cost that call. The four roles (ProviderRouter) keep the
#: wider `anthropic_client.BREAKER_EXCEPTIONS`.
BREAKER_ACCOUNT = "account"
BREAKER_POLICIES = (None, BREAKER_ACCOUNT)

#: Anthropic's list prices per 1M tokens, in micro-dollars: (input, output, cache read, cache write) —
#: as published 2026-10-06, checked 2026-10-31 (Sonnet 5.5 $2 / $10, Opus 5.5 $4 / $20, Fable 5.1 $10 / $50;
#: cache reads $0.20 / $0.20 / $0.25). Cache writes at 1.25× input, the 5-minute TTL's rate (these callers
#: send no cache_control, so both cache kinds stay 0 in practice). Haiku 5.5 at its rate for prompts up to
#: 100K tokens (every prompt of these roles is far below; $0.50 / $2.50 above), with no published cache-read
#: rate (None). A served model is matched by its id or its id plus a dated snapshot suffix (-YYYYMMDD). A
#: model or token kind with no rate here makes the marketing cost line's Claude part "unreadable" and the
#: jump lookup off (`jump_claude_lookup_service`) — add its row, never a guess. Revisit with any Anthropic
#: price change. Moved here from `marketing/digest_service.py` (2026-11-27), which re-exports it.
CLAUDE_MICROS_PER_MILLION_TOKENS: Dict[str, Tuple[Optional[int], Optional[int], Optional[int], Optional[int]]] = {
    "claude-sonnet-5-5": (2_000_000, 10_000_000, 200_000, 2_500_000),
    "claude-opus-5-5": (4_000_000, 20_000_000, 200_000, 5_000_000),
    "claude-fable-5-1": (10_000_000, 50_000_000, 250_000, 12_500_000),
    "claude-haiku-5-5": (100_000, 500_000, None, 125_000),
}


def claude_rates(model: str) -> Optional[Tuple[Optional[int], ...]]:
    """A served Claude model's price row: by its id, or by its id with a dated snapshot suffix
    ("claude-sonnet-5-5-20261001"); None for a model without one."""
    rates = CLAUDE_MICROS_PER_MILLION_TOKENS.get(model)
    if rates is None:
        base, _, suffix = model.rpartition("-")
        if len(suffix) == 8 and suffix.isascii() and suffix.isdigit():
            rates = CLAUDE_MICROS_PER_MILLION_TOKENS.get(base)
    return rates


def search_opens_account_breaker(e: BaseException) -> bool:
    """Whether a failed `cited_web_search` opens the shared breaker under BREAKER_ACCOUNT: only an
    ACCOUNT failure — used-up credits, or a 401 (a revoked or wrong key). A 403 is
    ClaudeAuthException too, but on this path it is the web tool refused for the organisation, which
    only the jump lookup should stop over (review 2026-11-27)."""
    if isinstance(e, ac.ClaudeCreditsExhaustedException):
        return True
    return isinstance(e, ac.ClaudeAuthException) and getattr(e, "status_code", None) == 401

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


def parse_claude_usage(raw: Any) -> Optional[Dict[str, Dict[str, int]]]:
    """A carried or stored Claude usage, read strictly: {} for None or {}; the usage (every kind
    listed, every zero-token model dropped) when it is {model: {kind: count}} with non-blank model ids,
    known kinds (`anthropic_client.USAGE_KINDS`) and plain non-negative int counts; else None."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        return None
    out: Dict[str, Dict[str, int]] = {}
    for model, counts in raw.items():
        if not isinstance(model, str) or not model.strip() or not isinstance(counts, dict):
            return None
        if any(k not in ac.USAGE_KINDS or type(n) is not int or n < 0 for k, n in counts.items()):
            return None
        if any(counts.values()):
            out[model] = {k: counts.get(k, 0) for k in ac.USAGE_KINDS}
    return out


def claude_usage_of(obj: Any) -> Dict[str, Dict[str, int]]:
    """The Claude usage a router result (key CLAUDE_USAGE_KEY) or an exception (the attribute of that
    name) carries; {} when it carries none, or an unreadable one (logged: its tokens are still in the
    caller's total, only their Claude price is lost)."""
    raw = obj.get(CLAUDE_USAGE_KEY) if isinstance(obj, dict) else getattr(obj, CLAUDE_USAGE_KEY, None)
    parsed = parse_claude_usage(raw)
    if parsed is None:
        logger.warning("llm_provider: an unreadable %s on a %s — its Claude tokens stay in the token "
                       "total but cannot be priced apart", CLAUDE_USAGE_KEY, type(obj).__name__)
        return {}
    return parsed


def merge_claude_usage(*usages: Any) -> Dict[str, Dict[str, int]]:
    """The sum of Claude usages, model by model and kind by kind (each read by `parse_claude_usage`;
    an unreadable one adds nothing)."""
    out: Dict[str, Dict[str, int]] = {}
    for usage in usages:
        for model, counts in (parse_claude_usage(usage) or {}).items():
            entry = out.setdefault(model, {k: 0 for k in ac.USAGE_KINDS})
            for k in ac.USAGE_KINDS:
                entry[k] += counts[k]
    return out


def claude_usage_tokens(usage: Any) -> int:
    """Every token of a Claude usage (0 for an unreadable one)."""
    return sum(sum(counts.values()) for counts in (parse_claude_usage(usage) or {}).values())


def _usage_entry(model: Any, usage: Any) -> Dict[str, Dict[str, Dict[str, int]]]:
    """{CLAUDE_USAGE_KEY: {model: usage}} for one Claude call that billed, else {} — merged into a
    result dict as is."""
    parsed = parse_claude_usage({model: usage}) if isinstance(model, str) and isinstance(usage, dict) else None
    return {CLAUDE_USAGE_KEY: parsed} if parsed else {}


def _exception_usage(e: BaseException) -> Dict[str, Dict[str, int]]:
    """The usage a failed Claude call billed (ClaudeException.usage under its .model); {} when none."""
    return _usage_entry(getattr(e, "model", None), getattr(e, "usage", None)).get(CLAUDE_USAGE_KEY, {})


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


def _effort(override: Optional[str] = None) -> str:
    """CAYDEX_CLAUDE_EFFORT, or a ClaudeOnly caller's explicit `effort` (the art: "low" whatever the
    roles use). Unknown → "low", with ONE ERROR per bad value."""
    if override is not None:
        raw = str(override).strip().lower()
        if raw not in EFFORTS:
            _error_once(f"effort_arg:{raw}", "llm_provider: effort=%s is not one of %s — using low",
                        _shown(override), EFFORTS)
            return "low"
        return raw
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


async def _ask_claude(claude: Any, model: str, prompt: str, kwargs: Dict[str, Any], *,
                      effort: Optional[str] = None,
                      claude_kwargs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The Gemini call's arguments → the Claude call: the same prompt, system instruction and
    schema (translated); temperature, cache and service_tier have no Claude equivalent and are
    dropped; thinking_budget=0 becomes Sonnet's between_tools where the model takes it.

    `effort` (ClaudeOnly only) replaces CAYDEX_CLAUDE_EFFORT; `claude_kwargs` (ClaudeOnly only:
    images, max_tokens, deadline_seconds — each present only when its caller set it) are Claude-only
    keywords added to the call as they are. The router passes neither, so its call is unchanged."""
    effort = _effort(effort)
    schema = kwargs.get("response_schema")
    return await claude.generate_json(
        prompt,
        json_schema=None if schema is None else gemini_schema_to_json_schema(schema),
        model_name=model,
        system_instruction=kwargs.get("system_instruction"),
        thinking=_thinking(model, kwargs.get("thinking_budget"), effort),
        effort=effort,
        usage_tag=kwargs.get("usage_tag"),
        **(claude_kwargs or {}),
    )


#: Measurement retries (ClaudeOnly): only a rate limit or an outage is retried — never a refusal,
#: a truncation or a bad answer, which are what a calibration measures.
_MEASURE_RETRYABLE = (ac.ClaudeRateLimitException, ac.ClaudeUnavailableException)


class ClaudeOnly:
    """Claude with NO fallback. Raises ClaudeException. Three users:

      * measurement scripts (judge calibration, the writer preview): a fallback would mix Gemini
        answers into a Claude measurement;
      * the marketing per-post art brief and check (owner decision 2026-11-15, "Google draws, Claude
        checks": no Gemini fallback by decision — a Gemini check would be Google grading Google's own
        picture, and art is optional), with `breaker=BREAKER_ACCOUNT` and, per call, `images`,
        `max_tokens`, `deadline_seconds` and `effort`;
      * the Updates jump search's fallback lookup, through `cited_web_search` only (2026-11-27).

    `retries` (default 0) re-asks after a 429 / 5xx / overload / timeout with a 2-4-8 s back-off, so
    one transport blip among hundreds of calibration calls is not scored as an unusable answer. Every
    attempt's billed tokens are kept, on success AND on failure: a failed call's ride on the final
    exception's `tokens_used` and, by model and kind, its CLAUDE_USAGE_KEY attribute; a success
    carries CLAUDE_USAGE_KEY in its result. The final exception's `billing_unknown` is True when ANY
    attempt may have billed without an answer (a timeout, a dropped connection); a success does not
    report an earlier such attempt, so a caller that caps its spend (the art) uses retries=0.

    `breaker` (BREAKER_POLICIES): None (the default) neither reads nor opens the shared breaker;
    BREAKER_ACCOUNT raises ClaudeBreakerOpenException with no call while it is open, and opens it
    only on ACCOUNT_BREAKER_EXCEPTIONS. An unknown policy is a ValueError at construction."""

    def __init__(self, model: str, claude: Any = None, *, retries: int = 0,
                 sleep: Any = None, breaker: Optional[str] = None):
        if breaker not in BREAKER_POLICIES:
            raise ValueError(f"ClaudeOnly: unknown breaker policy {breaker!r} — one of {BREAKER_POLICIES}")
        self.model = model
        self._claude = claude
        self.retries = max(int(retries), 0)
        self._sleep = sleep
        self.breaker = breaker

    async def generate_json(self, prompt: str, *, images: Sequence[Tuple[bytes, str]] = (),
                            max_tokens: Optional[int] = None, deadline_seconds: Optional[float] = None,
                            effort: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
        """The Gemini-shaped call (`kwargs`: system_instruction, response_schema, usage_tag, …), plus
        the Claude-only keywords. Each is sent only when set, so a call without them is exactly
        today's: `images` — (bytes, media type) pairs, sent before the prompt (the client refuses a
        bad one, typed, before any I/O); `max_tokens` — the answer's cap, thinking included (else
        CAYDEX_CLAUDE_MAX_TOKENS); `deadline_seconds` — a SHORTER total deadline for EACH attempt,
        ending in ClaudeTimeoutException; `effort` — replaces CAYDEX_CLAUDE_EFFORT."""
        import asyncio

        claude = self._claude or ac.get_claude_client()
        sleep = self._sleep or asyncio.sleep
        claude_kwargs: Dict[str, Any] = {}
        no_images = images is None or (isinstance(images, (list, tuple)) and len(images) == 0)
        if not no_images:
            claude_kwargs["images"] = images
        if max_tokens is not None:
            claude_kwargs["max_tokens"] = max_tokens
        if deadline_seconds is not None:
            claude_kwargs["deadline_seconds"] = deadline_seconds
        tag = kwargs.get("usage_tag")
        spent = 0
        spent_usage: Dict[str, Dict[str, int]] = {}
        billing_unknown = False   # an earlier attempt may have billed without an answer
        for attempt in range(self.retries + 1):
            if self.breaker == BREAKER_ACCOUNT and breaker_open():
                skipped = ac.ClaudeBreakerOpenException(
                    f"the shared Claude breaker is open ({_breaker_reason}) — not called "
                    f"(model={self.model}, tag={tag})", tokens_used=spent)
                skipped.billing_unknown = billing_unknown
                if spent_usage:
                    setattr(skipped, CLAUDE_USAGE_KEY, spent_usage)
                logger.warning("CLAUDE_SKIP tag=%s model=%s reason=breaker_open(%s) — no call, no fallback",
                               tag, self.model, _breaker_reason)
                raise skipped
            try:
                result = await _ask_claude(claude, self.model, prompt, kwargs, effort=effort,
                                           claude_kwargs=claude_kwargs)
            except _MEASURE_RETRYABLE as e:
                spent += int(getattr(e, "tokens_used", 0) or 0)
                spent_usage = merge_claude_usage(spent_usage, _exception_usage(e))
                billing_unknown = billing_unknown or bool(getattr(e, "billing_unknown", False))
                if attempt >= self.retries:
                    e.tokens_used = spent
                    e.billing_unknown = billing_unknown
                    if spent_usage:
                        setattr(e, CLAUDE_USAGE_KEY, spent_usage)
                    raise
                logger.warning("ClaudeOnly %s: %s (%s) — retry %d of %d", self.model,
                               type(e).__name__, e, attempt + 1, self.retries)
                await sleep(2.0 * (2 ** attempt))
                continue
            except ac.ClaudeException as e:
                if self.breaker == BREAKER_ACCOUNT and isinstance(e, ac.ACCOUNT_BREAKER_EXCEPTIONS):
                    _open_breaker(f"{type(e).__name__} (tag={tag})")
                e.tokens_used = int(getattr(e, "tokens_used", 0) or 0) + spent
                e.billing_unknown = billing_unknown or bool(getattr(e, "billing_unknown", False))
                usage = merge_claude_usage(spent_usage, _exception_usage(e))
                if usage:
                    setattr(e, CLAUDE_USAGE_KEY, usage)
                raise
            usage = merge_claude_usage(spent_usage, _usage_entry(result.get("model") or self.model,
                                                                 result.get("usage")).get(CLAUDE_USAGE_KEY))
            return {**result, "provider": PROVIDER_CLAUDE,
                    "tokens_used": int(result.get("tokens_used") or 0) + spent,
                    **({CLAUDE_USAGE_KEY: usage} if usage else {})}
        raise AssertionError("unreachable")  # pragma: no cover

    async def cited_web_search(self, prompt: str, *, system_instruction: Optional[str], max_uses: int,
                               max_tokens: int, deadline_seconds: Optional[float], effort: str = "low",
                               blocked_domains: Sequence[str] = (),
                               user_location: Optional[Dict[str, str]] = None,
                               usage_tag: str) -> Dict[str, Any]:
        """ONE `ClaudeClient.cited_web_search` call (the jump search's fallback lookup, 2026-11-27).

        Under BREAKER_ACCOUNT an open shared breaker raises ClaudeBreakerOpenException with no call;
        a failure opens it ONLY on `search_opens_account_breaker` (used-up credits, a 401) — never on
        a 403/400 refusal of the web tool, a 429, an overload or a timeout. `effort` is normalised
        like the art's (unknown → "low"); thinking is Sonnet 5.5's `between_tools` where the model
        and effort take it (Opus 5.5 always thinks). Never retried: a ClaudeOnly built with retries
        > 0 is refused before any I/O (a second attempt would spend past the caller's pre-charge).
        A success carries CLAUDE_USAGE_KEY; a billed failure carries it as an attribute."""
        tag = usage_tag
        if self.retries:
            raise ac.ClaudeRequestInvalidException(
                f"cited_web_search makes ONE attempt — build ClaudeOnly with retries=0 (tag={tag})")
        if self.breaker == BREAKER_ACCOUNT and breaker_open():
            logger.warning("CLAUDE_SKIP tag=%s model=%s reason=breaker_open(%s) — no web search call",
                           tag, self.model, _breaker_reason)
            raise ac.ClaudeBreakerOpenException(
                f"the shared Claude breaker is open ({_breaker_reason}) — not called "
                f"(model={self.model}, tag={tag})")
        claude = self._claude or ac.get_claude_client()
        effort = _effort(effort)
        try:
            result = await claude.cited_web_search(
                prompt, model_name=self.model, system_instruction=system_instruction, max_uses=max_uses,
                max_tokens=max_tokens, effort=effort, thinking=_thinking(self.model, 0, effort),
                blocked_domains=blocked_domains, user_location=user_location, usage_tag=tag,
                deadline_seconds=deadline_seconds)
        except ac.ClaudeException as e:
            if self.breaker == BREAKER_ACCOUNT and search_opens_account_breaker(e):
                _open_breaker(f"{type(e).__name__} (tag={tag})")
            usage = _exception_usage(e)
            if usage:
                setattr(e, CLAUDE_USAGE_KEY, usage)
            raise
        usage = _usage_entry(result.get("model") or self.model, result.get("usage")).get(CLAUDE_USAGE_KEY)
        return {**result, "provider": PROVIDER_CLAUDE, **({CLAUDE_USAGE_KEY: usage} if usage else {})}


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
            spent_usage = _exception_usage(e) if isinstance(e, ac.ClaudeException) else {}
            try:
                result = await self._gemini_client().generate_json(prompt, **kwargs)
            except Exception as ge:
                # Both providers failed: the Gemini error propagates unchanged in type, carrying the
                # Claude attempt's billed tokens so the caller's total still counts them (and their
                # breakdown, so the cost line still prices them as Claude's).
                try:
                    setattr(ge, CLAUDE_TOKENS_ATTR, claude_tokens_of(ge) + spent)
                    if spent_usage:
                        setattr(ge, CLAUDE_USAGE_KEY, merge_claude_usage(claude_usage_of(ge), spent_usage))
                except (AttributeError, TypeError):   # a __slots__ / C-level exception type
                    logger.warning("CLAUDE_FALLBACK tag=%s: could not attach %d Claude tokens to %s — "
                                   "that spend will be missing from the caller's total", usage_tag,
                                   spent, type(ge).__name__)
                raise
            return {**result, "provider": PROVIDER_GEMINI, "fallback_reason": reason,
                    "tokens_used": int(result.get("tokens_used") or 0) + spent,
                    **({CLAUDE_USAGE_KEY: spent_usage} if spent_usage else {})}
        return {**result, "provider": PROVIDER_CLAUDE, **_usage_entry(result.get("model") or model,
                                                                       result.get("usage"))}


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
