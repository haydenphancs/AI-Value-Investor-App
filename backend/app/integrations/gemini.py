"""
Google Gemini API Integration
Handles all interactions with Google Gemini for AI features.
Requirements: Section 3.3, 4.3.1 - Google Gemini API for deep research

Uses the unified `google-genai` SDK (async-native via `client.aio.*`). The
`GeminiClient` public method signatures are frozen — the 12 services that call
`get_gemini_client()` are unaffected by the SDK swap.
"""

from typing import Optional, List, Dict, Any, Callable
import logging
import asyncio
import hashlib
import json
import re
import time
from functools import wraps

from google import genai
from google.genai import types
from google.genai import errors as genai_errors

from app.config import settings

logger = logging.getLogger(__name__)

# ── Transient-error detection ──────────────────────────────────────
# Two flavours of transient upstream condition, both retry-later + sentinel-
# fallback (NOT a code bug worth an ERROR-level Sentry page):
#   * QUOTA / rate-limit (429) — governed by the circuit breaker below.
#   * SERVER OVERLOAD / 5xx ("This model is currently experiencing high demand")
#     — the SDK's ServerError; retry with backoff (Google's own guidance).
_QUOTA_ERROR_STRINGS = ("429", "resource_exhausted", "quota", "rate limit")
_OVERLOAD_ERROR_STRINGS = ("high demand", "overloaded", "try again later", "unavailable", "503")


def _is_quota_error(exc: Exception) -> bool:
    """Return True if the exception looks like a quota/rate-limit error."""
    msg = str(exc).lower()
    return any(s in msg for s in _QUOTA_ERROR_STRINGS)


def _is_overload_error(exc: Exception) -> bool:
    """Gemini server-side overload / 5xx — transient and retryable, NOT a code
    bug. Matches the SDK's ``ServerError`` type (any 5xx) plus the "high demand"
    503 message so a wrapped/stringified error is still caught."""
    if isinstance(exc, genai_errors.ServerError):
        return True
    msg = str(exc).lower()
    return any(s in msg for s in _OVERLOAD_ERROR_STRINGS)


class GeminiQuotaError(Exception):
    """Raised by the circuit breaker when it's open (fail-fast).

    The message intentionally contains "quota"/"resource_exhausted" so both
    `_is_quota_error` and the API error classifier
    (`app.api.error_response.classify_exception`) recognize it and route to the
    GEMINI_QUOTA_EXCEEDED contract — and so the caller's existing sentinel
    fallback (e.g. narrative jobs) fires instead of propagating a raw error.
    """


class GeminiTimeoutError(TimeoutError):
    """One Gemini SDK call exceeded settings.GEMINI_REQUEST_TIMEOUT_SECONDS.

    Subclasses **TimeoutError deliberately**. `_call_with_timeout` used to let the
    bare `asyncio.TimeoutError` escape, and three outer handlers catch that type
    today (`home_dashboard_service`, `chat_context_resolver`, `live_price`). Keeping
    the inheritance guarantees this change CANNOT alter what any of them catch — it
    only adds a name and a message.

    Why a named type at all: `str(TimeoutError()) == ""`, so the bare form defeated
    every string-matching classifier in this module (it logged ERROR and opened the
    Sentry issue `TimeoutError` / "No error message"), and it defeated
    `classify_exception`, whose `type(exc).__module__` test saw `builtins` and fell
    through to **FMP_UNAVAILABLE** — telling the user their market-data provider was
    down when it was the AI engine.

    ⚠️ MESSAGE CONSTRAINT — the text must never contain "429", "quota", "rate limit",
    "resource_exhausted", "unavailable", "503", "try again later" or "high demand".
    `_is_quota_error` / `_is_overload_error` substring-match `str(exc)`, so any of
    those words routes a timeout into the wrong retry branch — and a quota word would
    additionally trip the process-wide `_quota_circuit`, fail-fasting every OTHER
    Gemini call in the process off the back of one slow read. Pinned by
    `tests/test_gemini_timeout.py::test_timeout_message_cannot_be_misrouted`.
    """


def is_transient_gemini_error(exc: Exception) -> bool:
    """Quota/rate-limit, server-overload, OR per-call timeout — an upstream capacity
    condition the caller should treat as retry-later + sentinel fallback and log at
    WARNING, never an ERROR-level Sentry page. The single classifier every caller
    should use (so the three failure modes stay in sync).

    The timeout arm is `isinstance`-based on purpose: a 90s stall carries no message
    for a substring rule to match, which is exactly why it used to page."""
    return (
        isinstance(exc, (GeminiQuotaError, GeminiTimeoutError))
        or _is_quota_error(exc)
        or _is_overload_error(exc)
    )


class _QuotaCircuitBreaker:
    """Process-wide breaker that stops hammering Gemini during a sustained
    quota outage.

    Without it, under load every one of the ~15 parallel narrative calls (per
    report, across every concurrent report) would each burn its full backoff
    ladder against an API that is already returning 429 — adding load and
    latency for nothing. After `GEMINI_QUOTA_CIRCUIT_THRESHOLD` *consecutive*
    quota errors the breaker opens and `is_open()` returns True for
    `GEMINI_QUOTA_CIRCUIT_COOLDOWN_SECONDS`; calls then fail fast (the caller's
    sentinel fallback applies). Any success resets it. A single half-open trial
    is allowed once the cooldown elapses.

    Single-event-loop process → no lock needed (all access is on one thread).
    """

    def __init__(self) -> None:
        self._consecutive = 0
        self._opened_at = 0.0

    def is_open(self) -> bool:
        if self._opened_at <= 0.0:
            return False
        if time.time() - self._opened_at >= settings.GEMINI_QUOTA_CIRCUIT_COOLDOWN_SECONDS:
            # Cooldown elapsed → half-open: clear state and allow one trial.
            self._opened_at = 0.0
            self._consecutive = 0
            return False
        return True

    def record_quota_error(self) -> None:
        self._consecutive += 1
        if self._consecutive >= settings.GEMINI_QUOTA_CIRCUIT_THRESHOLD:
            # Stamp the open time ONLY on the closed→open transition. Setting it
            # unconditionally would let every straggler 429 (the ~15 parallel
            # calls already past the is_open() check) push the deadline forward,
            # holding the breaker open well beyond the configured cooldown.
            if self._opened_at <= 0.0:
                logger.error(
                    "Gemini quota circuit OPEN after %d consecutive quota "
                    "errors — failing fast for %.0fs",
                    self._consecutive,
                    settings.GEMINI_QUOTA_CIRCUIT_COOLDOWN_SECONDS,
                )
                self._opened_at = time.time()

    def record_success(self) -> None:
        self._consecutive = 0
        self._opened_at = 0.0


# Module-level breaker shared by every decorated Gemini call.
_quota_circuit = _QuotaCircuitBreaker()


class _TimeoutStreak:
    """One ERROR per timeout OUTAGE, not one per call.

    Demoting per-call timeouts to WARNING is what closes the `TimeoutError` Sentry
    issue, but on its own it would make a sustained Gemini stall invisible — every
    caller has a sentinel fallback, so nothing else would shout. This escalates on
    the STREAK instead: once GEMINI_TIMEOUT_ALERT_STREAK consecutive calls have timed
    out with no success in between, emit exactly one ERROR. Any success resets it, so
    the ERROR means "sustained upstream problem", not "one slow prompt".

    Same closed→open idiom as `_QuotaCircuitBreaker.record_quota_error` above: the
    `_alerted` latch is what keeps ~15 parallel narrative jobs from each filing a
    duplicate.

    Single-event-loop process → no lock needed.
    """

    def __init__(self) -> None:
        self._consecutive = 0
        self._alerted = False

    def record(self) -> None:
        self._consecutive += 1
        if self._consecutive >= settings.GEMINI_TIMEOUT_ALERT_STREAK and not self._alerted:
            self._alerted = True
            logger.error(
                "Gemini per-call timeouts sustained: %d consecutive calls hit the "
                "%ss ceiling — likely an upstream outage, not a slow prompt",
                self._consecutive,
                settings.GEMINI_REQUEST_TIMEOUT_SECONDS,
            )

    def record_success(self) -> None:
        self._consecutive = 0
        self._alerted = False


_timeout_streak = _TimeoutStreak()


# ── Per-call timeout guard ─────────────────────────────────────────
async def _call_with_timeout(coro, *, what: str = "Gemini call"):
    """Await a Gemini coroutine with a hard timeout.

    The unified SDK is async-native (`client.aio.*` returns coroutines), so this
    just wraps the coroutine in `asyncio.wait_for` — no more thread offload. A
    hung network read would otherwise park the whole report-generation task
    forever (seen as a report card stuck at "synthesizing..." at 55%).

    On timeout, raises **GeminiTimeoutError** (a `TimeoutError` subclass, so any
    existing `except asyncio.TimeoutError` handler is unaffected). `@async_retry`
    gives it its OWN budget — `GEMINI_TIMEOUT_MAX_RETRIES`, default 0, i.e. no
    retry — and logs the give-up at WARNING, because the caller's sentinel fallback
    covers the user. A sustained run of timeouts still escalates to exactly one
    ERROR via `_timeout_streak`, so demoting the individual call does not make an
    outage invisible.

    (This previously raised a BARE asyncio.TimeoutError, whose empty `str()` slipped
    past every string-based classifier here → an ERROR log per attempt → the Sentry
    issue `TimeoutError` with "No error message".)

    `what` names the calling method so the log and the Sentry title say which one
    stalled; it is keyword-only with a default so existing call sites are unaffected.

    Timeout sourced from settings.GEMINI_REQUEST_TIMEOUT_SECONDS.
    """
    try:
        result = await asyncio.wait_for(
            coro, timeout=settings.GEMINI_REQUEST_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError as exc:
        # NB: an EXTERNAL cancellation (e.g. the 600s RESEARCH_PIPELINE_TIMEOUT_SECONDS
        # ceiling in research_service) surfaces as CancelledError, not TimeoutError,
        # so it is not misreported as a per-call stall.
        _timeout_streak.record()
        raise GeminiTimeoutError(
            f"{what} exceeded its {settings.GEMINI_REQUEST_TIMEOUT_SECONDS}s "
            f"per-request ceiling"
        ) from exc
    # Reset lives HERE rather than in async_retry so undecorated callers
    # (create_narrative_cache, delete_cache, the tool-chat drive loop) clear the
    # streak too.
    _timeout_streak.record_success()
    return result


def async_retry(max_attempts: int = 3, delay: float = 1.0):
    """
    Decorator for retrying async functions on failure.

    Two independent retry budgets:
      * Generic errors → up to `max_attempts` tries, linear backoff `delay*n`.
      * Quota/rate-limit (429) errors → up to GEMINI_QUOTA_MAX_RETRIES tries
        with GEMINI_QUOTA_RETRY_DELAY_SECONDS*n backoff. Previously these were
        NOT retried (immediate raise → sentinel narrative); under the
        agent-run semaphore a short backoff recovers transient 429s so the
        report keeps its real prose. The shared `_quota_circuit` short-circuits
        once quota errors are sustained, so retries never pile onto an outage.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            attempt = 0            # generic failures
            quota_attempt = 0      # quota/429 failures
            overload_attempt = 0   # server-overload / 5xx failures
            timeout_attempt = 0    # per-call timeouts (own budget, default 0)
            while True:
                # Fail fast while the breaker is open — don't add load to an
                # already-exhausted quota; the caller's sentinel fallback fires.
                if _quota_circuit.is_open():
                    raise GeminiQuotaError(
                        "Gemini quota circuit open (resource_exhausted) — "
                        "failing fast"
                    )
                try:
                    result = await func(*args, **kwargs)
                    _quota_circuit.record_success()
                    return result
                except Exception as e:
                    # PER-CALL TIMEOUT — checked FIRST and by isinstance ONLY.
                    # A string match would be one wording change away from landing in
                    # the quota branch, which would trip the shared circuit breaker
                    # and fail-fast every other Gemini call in the process.
                    #
                    # Budget defaults to 0 (no retry), which is what the docstrings
                    # always claimed and what the latency arithmetic wants: the
                    # generic branch used to retry these, so one hung call cost
                    # 90s + backoff + 90s ≈ 182s against a 600s pipeline ceiling with
                    # ~15 parallel narratives. A read that stalled a full 90s is a
                    # stuck connection, not a blip. Kept as its own SETTING rather
                    # than deleted so it is one env var away if that judgement changes.
                    if isinstance(e, GeminiTimeoutError):
                        timeout_attempt += 1
                        if timeout_attempt > settings.GEMINI_TIMEOUT_MAX_RETRIES:
                            # WARNING, not ERROR: the caller's sentinel fallback
                            # covers the user, and _timeout_streak escalates a
                            # SUSTAINED run to a single ERROR.
                            logger.warning(
                                "Gemini call timed out — giving up after %d "
                                "attempt(s); the caller's sentinel fallback "
                                "applies: %s",
                                timeout_attempt, e,
                            )
                            raise
                        backoff = (
                            settings.GEMINI_QUOTA_RETRY_DELAY_SECONDS * timeout_attempt
                        )
                        logger.warning(
                            "Gemini timeout (attempt %d/%d) — backing off %.1fs: %s",
                            timeout_attempt,
                            settings.GEMINI_TIMEOUT_MAX_RETRIES,
                            backoff, e,
                        )
                        await asyncio.sleep(backoff)
                        continue
                    if _is_quota_error(e):
                        _quota_circuit.record_quota_error()
                        quota_attempt += 1
                        if (
                            quota_attempt > settings.GEMINI_QUOTA_MAX_RETRIES
                            or _quota_circuit.is_open()
                        ):
                            logger.error(
                                f"Quota/rate-limit error — giving up after "
                                f"{quota_attempt} attempt(s): {e}"
                            )
                            raise
                        backoff = (
                            settings.GEMINI_QUOTA_RETRY_DELAY_SECONDS
                            * quota_attempt
                        )
                        logger.warning(
                            f"Quota/rate-limit (attempt {quota_attempt}/"
                            f"{settings.GEMINI_QUOTA_MAX_RETRIES}) — backing "
                            f"off {backoff:.1f}s: {e}"
                        )
                        await asyncio.sleep(backoff)
                        continue
                    # Server overload / 5xx ("high demand"): transient upstream
                    # capacity, NOT a code bug. Retry with backoff (Google's own
                    # guidance) on its OWN budget, log at WARNING (the caller's
                    # sentinel fallback covers the user), and DON'T touch the quota
                    # circuit — an overload is not a quota exhaustion.
                    if _is_overload_error(e):
                        overload_attempt += 1
                        if overload_attempt > settings.GEMINI_QUOTA_MAX_RETRIES:
                            logger.warning(
                                f"Gemini overloaded — giving up after "
                                f"{overload_attempt} attempt(s): {e}"
                            )
                            raise
                        backoff = (
                            settings.GEMINI_QUOTA_RETRY_DELAY_SECONDS
                            * overload_attempt
                        )
                        logger.warning(
                            f"Gemini overloaded (attempt {overload_attempt}/"
                            f"{settings.GEMINI_QUOTA_MAX_RETRIES}) — backing off "
                            f"{backoff:.1f}s: {e}"
                        )
                        await asyncio.sleep(backoff)
                        continue
                    attempt += 1
                    if attempt >= max_attempts:
                        raise
                    logger.warning(
                        f"Attempt {attempt} failed: {e}. Retrying..."
                    )
                    await asyncio.sleep(delay * attempt)
        return wrapper
    return decorator


# ── In-memory LRU cache with TTL ──────────────────────────────────

class _TTLCache:
    """Simple in-memory cache with max-size eviction and TTL expiry."""

    def __init__(self, max_size: int = 128, ttl_seconds: int = 3600):
        self._store: Dict[str, Any] = {}
        self._timestamps: Dict[str, float] = {}
        self._max_size = max_size
        self._ttl = ttl_seconds

    def get(self, key: str) -> Any:
        if key in self._store:
            if time.time() - self._timestamps[key] < self._ttl:
                return self._store[key]
            # Expired
            del self._store[key]
            del self._timestamps[key]
        return None

    def set(self, key: str, value: Any):
        # Evict oldest if full
        if len(self._store) >= self._max_size and key not in self._store:
            oldest = min(self._timestamps, key=self._timestamps.get)
            del self._store[oldest]
            del self._timestamps[oldest]
        self._store[key] = value
        self._timestamps[key] = time.time()

    @property
    def size(self) -> int:
        return len(self._store)


def _cache_key(*parts: str) -> str:
    """Build a deterministic cache key from string parts."""
    raw = "|".join(str(p) for p in parts if p)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ── Response accessors (defensive; the SDK's .text raises on no-text parts) ──

def _iter_parts(response: Any) -> List[Any]:
    """Parts of the first candidate — works for a full response OR a streaming chunk.
    The unified SDK has no top-level `.parts`; they live under candidates[0].content.parts."""
    try:
        cand = (response.candidates or [None])[0]
        if cand and cand.content and cand.content.parts:
            return list(cand.content.parts)
    except (AttributeError, TypeError, IndexError):
        pass
    return []


def _response_text(response: Any) -> str:
    """Safe `.text` — the SDK property raises ValueError when the candidate has
    no text Part (function-call-only / finish-only). Falls back to walking parts.
    Skips thought parts so real reasoning never leaks into the answer text."""
    try:
        return response.text or ""
    except (ValueError, AttributeError):
        pass
    chunks: List[str] = []
    for p in _iter_parts(response):
        if getattr(p, "thought", False):
            continue
        try:
            t = p.text
        except (ValueError, AttributeError):
            continue
        if t:
            chunks.append(t)
    return "\n".join(chunks)


# ── Token accounting ──────────────────────────────────────────────
# Only `total_token_count` used to be read, which made prompt-prefix caching
# invisible: Gemini 2.5 discounts a repeated request PREFIX by 75% once it
# clears the model's floor, and `cached_content_token_count` is the ONLY signal
# that it happened. Without it, "is our system instruction being cached?" is
# unanswerable and every prompt-cost decision is guesswork.
_USAGE_FIELDS: tuple[tuple[str, str], ...] = (
    ("total", "total_token_count"),
    ("prompt", "prompt_token_count"),
    ("cached", "cached_content_token_count"),
    ("output", "candidates_token_count"),
)
_EMPTY_USAGE: Dict[str, Optional[int]] = {key: None for key, _ in _USAGE_FIELDS}


def _coerce_token_count(value: Any) -> Optional[int]:
    """Coerce one usage field to an int, or None. NEVER raises.

    The SDK types these as `int | None`, but this is telemetry sitting on the
    response path of every user-facing call — a proto default, a float, or a
    non-finite sentinel must degrade to None rather than take down the answer.
    `bool` is rejected explicitly (it is an `int` subclass, so `True` would
    otherwise be reported as 1 token), and OverflowError is caught because
    `int(float("inf"))` raises it and it is NOT a ValueError.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _response_usage(response: Any) -> Dict[str, Optional[int]]:
    """Extract `{total, prompt, cached, output}` token counts. NEVER raises."""
    um = getattr(response, "usage_metadata", None)
    if um is None:
        return dict(_EMPTY_USAGE)
    return {key: _coerce_token_count(getattr(um, attr, None)) for key, attr in _USAGE_FIELDS}


def _response_tokens(response: Any) -> Optional[int]:
    """Total tokens for a response. Thin wrapper — six call sites depend on it."""
    return _response_usage(response)["total"]


def _log_gemini_usage(
    usage: Dict[str, Optional[int]],
    *,
    call_site: str,
    model: str,
    tag: Optional[str] = None,
) -> None:
    """Emit ONE greppable line per Gemini call. Best-effort, never raises.

    `cached_pct` is the number this exists for: the share of input tokens that
    were served from the prefix cache at a 75% discount. A persistent 0 means
    the stable prefix is not being reused (too short, or something volatile —
    a price, a timestamp, a session id — is polluting the front of the request).
    """
    try:
        prompt = usage.get("prompt") or 0
        cached = usage.get("cached") or 0
        cached_pct = round(100.0 * cached / prompt, 1) if prompt > 0 else 0.0
        logger.info(
            "GEMINI_USAGE call_site=%s model=%s tag=%s prompt_tok=%s cached_tok=%s "
            "cached_pct=%s output_tok=%s total_tok=%s",
            call_site, model, tag or "-",
            usage.get("prompt"), usage.get("cached"), cached_pct,
            usage.get("output"), usage.get("total"),
        )
    except Exception as e:  # pragma: no cover — telemetry must never break a call
        logger.warning("GEMINI_USAGE log failed (%s: %s)", type(e).__name__, e)


class _StreamUsage:
    """Accumulate token usage across a stream, and across agentic ROUNDS.

    Within one streamed response the SDK reports CUMULATIVE counts, so the last
    non-empty reading of a round wins (not a sum, which would multiply-count).
    Across rounds those per-round totals ARE additive, hence the explicit
    `commit_round()` boundary — a 4-round agentic turn that summed every chunk
    would over-report by roughly the chunk count.
    """

    __slots__ = ("_committed", "_round")

    def __init__(self) -> None:
        self._committed: Dict[str, int] = {key: 0 for key, _ in _USAGE_FIELDS}
        self._round: Dict[str, Optional[int]] = dict(_EMPTY_USAGE)

    def observe(self, chunk: Any) -> None:
        """Record a chunk's usage if it carries any. Never raises."""
        usage = _response_usage(chunk)
        if any(value is not None for value in usage.values()):
            self._round = usage

    def commit_round(self) -> None:
        """Fold the current round's last reading into the running total."""
        for key, _ in _USAGE_FIELDS:
            value = self._round.get(key)
            if value:
                self._committed[key] += value
        self._round = dict(_EMPTY_USAGE)

    def totals(self) -> Dict[str, Optional[int]]:
        """Commit any open round and return the accumulated counts."""
        self.commit_round()
        return dict(self._committed)


def _response_finish(response: Any) -> Optional[str]:
    try:
        cand = (response.candidates or [None])[0]
        fr = getattr(cand, "finish_reason", None) if cand else None
        return getattr(fr, "name", fr) if fr is not None else None
    except Exception:
        return None


class GeminiClient:
    """Client for Google Gemini API (unified google-genai SDK)."""

    def __init__(self):
        """Initialize Gemini client with API key from settings."""
        # An HTTP-level timeout bounds every call (including streams — a stalled
        # read can't park forever); the async _call_with_timeout adds an app-level
        # bound on non-streaming calls. HttpOptions.timeout is in milliseconds.
        self._client = genai.Client(
            api_key=settings.GEMINI_API_KEY,
            http_options=types.HttpOptions(
                timeout=int(settings.GEMINI_REQUEST_TIMEOUT_SECONDS * 1000)
            ),
        )
        self.model_name = settings.GEMINI_MODEL
        self._temperature = settings.GEMINI_TEMPERATURE
        self._max_tokens = settings.GEMINI_MAX_TOKENS
        cache_ttl = getattr(settings, "GEMINI_CACHE_TTL", 3600)
        self._response_cache = _TTLCache(max_size=256, ttl_seconds=cache_ttl)
        self._embedding_cache = _TTLCache(max_size=512, ttl_seconds=cache_ttl)

    def _config(
        self,
        *,
        system_instruction: Optional[str] = None,
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        tools: Optional[List[Any]] = None,
        response_mime_type: Optional[str] = None,
        response_schema: Optional[Any] = None,
        cached_content: Optional[str] = None,
        thinking_config: Optional[Any] = None,
    ) -> types.GenerateContentConfig:
        """Assemble a GenerateContentConfig from the knobs that used to live in
        the legacy generation_config dict + per-call GenerativeModel kwargs."""
        kwargs: Dict[str, Any] = {
            "temperature": self._temperature if temperature is None else temperature,
            "max_output_tokens": self._max_tokens if max_output_tokens is None else max_output_tokens,
        }
        if system_instruction:
            kwargs["system_instruction"] = system_instruction
        if tools:
            kwargs["tools"] = list(tools)
        if response_mime_type:
            kwargs["response_mime_type"] = response_mime_type
        if response_schema is not None:
            kwargs["response_schema"] = response_schema
        if cached_content:
            kwargs["cached_content"] = cached_content
        if thinking_config is not None:
            kwargs["thinking_config"] = thinking_config
        return types.GenerateContentConfig(**kwargs)

    @async_retry(max_attempts=2, delay=2.0)
    async def generate_text(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        model_name: Optional[str] = None,
        max_output_tokens: Optional[int] = None,
        thinking_budget: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Generate text using Gemini.  Results are cached by (prompt, system_instruction,
        model, output cap, thinking budget) for GEMINI_CACHE_TTL seconds to avoid
        duplicate API calls.

        `max_output_tokens` defaults to the global `GEMINI_MAX_TOKENS`. Chat passes
        `CHAT_MAX_OUTPUT_TOKENS`; report generation deliberately does not.

        `thinking_budget` defaults to None = leave the model's own default alone, so every
        pre-existing caller is byte-identical. Pass **0** to disable thinking for a call
        whose output is a short, highly-constrained string — a template fill-in or a
        one-line hook — where the reasoning tokens buy nothing. Measured on the index
        story prompt against the live API: 3.91s / 4.26s with default (dynamic) thinking
        and 689 / 779 thought tokens, versus 1.21s / 1.47s and zero thought tokens at 0.
        Those thought tokens bill at the OUTPUT rate (see SYSTEM_DESIGN_GUIDELINES §9b.7).

        ⚠️ Both caps are part of the CACHE KEY. Without them a capped chat call and an
        uncapped one for the same prompt collide, and whichever ran first serves the other
        — so a report could be handed a 1,200-token-truncated answer, or a chat turn could
        return a full-length one straight past its own ceiling. `thinking_budget` is in the
        key for the same reason: a no-thinking answer and a reasoned one to the same prompt
        are different answers, and the cheap one must not be served to the caller that
        asked for the reasoned one.
        """
        key = _cache_key(
            prompt, system_instruction or "", model_name or "", str(max_output_tokens or ""),
            "" if thinking_budget is None else f"tb={thinking_budget}",
        )
        cached = self._response_cache.get(key)
        if cached is not None:
            logger.debug("Gemini generate_text cache HIT")
            return cached

        try:
            response = await _call_with_timeout(
                self._client.aio.models.generate_content(
                    model=model_name or self.model_name,
                    contents=prompt,
                    config=self._config(
                        system_instruction=system_instruction,
                        max_output_tokens=max_output_tokens,
                        thinking_config=(
                            None if thinking_budget is None
                            else types.ThinkingConfig(thinking_budget=thinking_budget)
                        ),
                    ),
                ),
                what="generate_text",
            )
            result = {
                "text": _response_text(response),
                "model": self.model_name,
                "tokens_used": _response_tokens(response),
                "finish_reason": _response_finish(response),
            }
            self._response_cache.set(key, result)
            return result
        except Exception as e:
            # A transient overload/quota is retried + WARNING-logged by
            # @async_retry and covered by the caller's sentinel — only a genuine
            # failure warrants an ERROR-level Sentry page.
            if not is_transient_gemini_error(e):
                logger.error(f"Gemini text generation failed: {e}", exc_info=True)
            raise

    # ── Streaming text (SSE chat) ─────────────────────────────────────
    # NOT decorated with @async_retry — retrying a partial stream would replay
    # already-emitted tokens. We honor the quota circuit breaker manually
    # (fail-fast if open; record quota errors/success) and let the caller's SSE
    # endpoint emit an `error` event + fall back. The unified SDK streams
    # natively (`aio.models.generate_content_stream`) — no thread bridge.
    async def stream_text(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        model_name: Optional[str] = None,
        usage_tag: Optional[str] = None,
        max_output_tokens: Optional[int] = None,
    ):
        """Yield ``(kind, text)`` chunks as Gemini generates.

        `kind` is "thought" (real reasoning summary → the thinking card) or "answer"
        (→ the message bubble). Thinking is requested via ThinkingConfig(include_thoughts=True);
        each streamed part carries a `.thought` flag we branch on — no more prompt-hack
        separator. Raises immediately if the quota circuit is open; propagates the first SDK
        error so the caller can surface an `error` event; the client HTTP timeout guards a hung read.
        """
        if _quota_circuit.is_open():
            raise GeminiQuotaError(
                "Gemini quota circuit open (resource_exhausted) — failing fast"
            )
        config = self._config(
            system_instruction=system_instruction,
            max_output_tokens=max_output_tokens,
            thinking_config=types.ThinkingConfig(include_thoughts=True),
        )
        resolved_model = model_name or self.model_name
        usage = _StreamUsage()
        try:
            stream = await self._client.aio.models.generate_content_stream(
                model=resolved_model,
                contents=prompt,
                config=config,
            )
            async for chunk in stream:
                usage.observe(chunk)
                for part in _iter_parts(chunk):
                    # part.text raises on non-text parts (finish-only) — treat as empty.
                    try:
                        text = part.text or ""
                    except (ValueError, AttributeError):
                        text = ""
                    if not text:
                        continue
                    yield ("thought" if getattr(part, "thought", False) else "answer"), text
            _quota_circuit.record_success()
        except Exception as e:
            if _is_quota_error(e):
                _quota_circuit.record_quota_error()
            raise
        finally:
            # `finally`, not the happy path: a client disconnect closes this async
            # generator (GeneratorExit) and an error raises past it, and BOTH still
            # spent tokens. Logging only on success would hide exactly the turns
            # that cost money without delivering an answer.
            _log_gemini_usage(
                usage.totals(), call_site="stream_text", model=resolved_model, tag=usage_tag,
            )

    # ── Context caching (Stage-B narratives) ──────────────────────────
    # The N parallel narrative calls per report share one large evidence blob +
    # persona system prompt. Uploading that shared prefix to a CachedContent
    # once and pointing every call at it (config.cached_content) bills the prefix
    # ~1x (write) + N×25% (cache reads) instead of N×100%. All three methods are
    # FAIL-SAFE: a below-min-size / quota / hung-SDK condition degrades to the
    # inline path (create_* returns None) so report quality is never sacrificed.

    async def create_narrative_cache(
        self,
        system_instruction: Optional[str],
        evidence: str,
        ttl_minutes: Optional[int] = None,
    ) -> Optional[Any]:
        """Create a Gemini CachedContent for the shared (system prompt +
        evidence) prefix. Returns an opaque handle ``{"cache": <CachedContent>}``
        or None on ANY failure (caller falls back to inline prompts). Never raises.

        Unlike the legacy SDK there is no cache-bound model object — callers pass
        ``config.cached_content = cache.name`` per request (see generate_text_cached).
        """
        if not evidence:
            return None
        try:
            ttl = ttl_minutes if ttl_minutes is not None else getattr(
                settings, "GEMINI_CONTEXT_CACHE_TTL_MINUTES", 10
            )
            model_name = (
                self.model_name
                if self.model_name.startswith("models/")
                else f"models/{self.model_name}"
            )
            # Through _call_with_timeout so a hung SDK create can't park the agent
            # run for the full 600s pipeline ceiling — TimeoutError → None → inline.
            cache = await _call_with_timeout(
                self._client.aio.caches.create(
                    model=model_name,
                    config=types.CreateCachedContentConfig(
                        system_instruction=system_instruction or None,
                        contents=[f"FINANCIAL EVIDENCE:\n{evidence}"],
                        ttl=f"{int(ttl) * 60}s",
                    ),
                ),
                what="create_narrative_cache",
            )
            logger.info("Gemini context cache created (ttl=%dm)", ttl)
            return {"cache": cache}
        except Exception as e:
            # Below-min-token (2.5 Flash min ~1024), quota, or hung → inline.
            logger.info(
                "Gemini context cache unavailable (%s: %s) — using inline prompts",
                type(e).__name__, e,
            )
            return None

    @async_retry(max_attempts=2, delay=2.0)
    async def generate_text_cached(
        self, prompt: str, handle: Dict[str, Any]
    ) -> Dict[str, Any]:
        """generate_text variant that runs against a CachedContent prefix.

        The shared evidence + system instruction live in the cache; `prompt` is
        only the per-field instruction. Same timeout + quota path as generate_text.
        """
        cache = handle["cache"]
        response = await _call_with_timeout(
            self._client.aio.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=self._config(cached_content=cache.name),
            ),
            what="generate_text_cached",
        )
        return {
            "text": _response_text(response),
            "model": self.model_name,
            "tokens_used": _response_tokens(response),
            "finish_reason": _response_finish(response),
        }

    async def delete_cache(self, handle: Optional[Dict[str, Any]]) -> None:
        """Best-effort delete of a CachedContent so cache storage is freed
        before its TTL. Never raises (a failed delete just expires via TTL)."""
        if not handle:
            return
        try:
            cache = handle.get("cache")
            if cache is None:
                return
            await _call_with_timeout(
                self._client.aio.caches.delete(name=cache.name),
                what="delete_cache",
            )
        except Exception as e:
            logger.debug("Context cache delete failed (expires via TTL): %s", e)

    @async_retry(max_attempts=2, delay=2.0)
    async def generate_json(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        model_name: Optional[str] = None,
        response_schema: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Generate structured JSON using Gemini with response_mime_type.
        Optionally enforce a response_schema for guaranteed output shape.
        Results are cached.
        """
        key = _cache_key("json", prompt, system_instruction or "", model_name or "")
        cached = self._response_cache.get(key)
        if cached is not None:
            logger.debug("Gemini generate_json cache HIT")
            return cached

        try:
            response = await _call_with_timeout(
                self._client.aio.models.generate_content(
                    model=model_name or self.model_name,
                    contents=prompt,
                    config=self._config(
                        system_instruction=system_instruction,
                        response_mime_type="application/json",
                        response_schema=response_schema,
                    ),
                ),
                what="generate_json",
            )
            result = {
                "text": _response_text(response),
                "model": self.model_name,
                "tokens_used": _response_tokens(response),
                "finish_reason": _response_finish(response),
            }
            self._response_cache.set(key, result)
            return result
        except Exception as e:
            if not is_transient_gemini_error(e):
                logger.error(f"Gemini JSON generation failed: {e}", exc_info=True)
            raise

    @async_retry(max_attempts=2, delay=2.0)
    async def generate_embedding(
        self,
        text: str,
        model_name: str = "models/gemini-embedding-001",
        task_type: str = "RETRIEVAL_DOCUMENT",
    ) -> List[float]:
        """
        Generate an embedding vector for `text`.

        `task_type` defaults to RETRIEVAL_DOCUMENT (matches the stored corpus).
        Pass "RETRIEVAL_QUERY" for user-query embeddings (Phase 4 query rewrite).
        Embeddings are cached — identical (text, model, task_type) won't hit the API twice.
        """
        key = _cache_key("emb", text, model_name, task_type)
        cached = self._embedding_cache.get(key)
        if cached is not None:
            logger.debug("Embedding cache HIT")
            return cached

        try:
            result = await _call_with_timeout(
                self._client.aio.models.embed_content(
                    model=model_name,
                    contents=text,
                    config=types.EmbedContentConfig(
                        task_type=task_type,
                        output_dimensionality=settings.EMBEDDING_DIMENSION,
                    ),
                ),
                what="generate_embedding",
            )
            embedding = list(result.embeddings[0].values)
            self._embedding_cache.set(key, embedding)
            return embedding
        except Exception as e:
            if not is_transient_gemini_error(e):
                logger.error(f"Embedding generation failed: {e}", exc_info=True)
            raise

    @async_retry(max_attempts=2, delay=2.0)
    async def generate_grounded_research(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        model_name: Optional[str] = None,
        temperature: float = 0.3,
        max_output_tokens: int = 8192,
    ) -> Dict[str, Any]:
        """
        Generate text with **Google Search grounding** enabled (first-class Tool
        in the unified SDK — no more raw REST). The response's grounding metadata
        carries the actual web URLs Gemini consulted (more trustworthy than
        asking the model to self-report sources).

        Returns dict with: text, tokens_used, grounding_sources (list of
        {title, uri, publisher} deduped by uri), search_queries, finish_reason, model.
        """
        model = model_name or self.model_name
        try:
            response = await _call_with_timeout(
                self._client.aio.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction or None,
                        temperature=temperature,
                        max_output_tokens=max_output_tokens,
                        tools=[types.Tool(google_search=types.GoogleSearch())],
                    ),
                ),
                what="generate_grounded_research",
            )
        except Exception as exc:
            if not is_transient_gemini_error(exc):
                logger.error("Gemini grounded research failed: %s", exc, exc_info=True)
            raise

        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return {"text": "", "tokens_used": None, "grounding_sources": [], "search_queries": []}
        cand = candidates[0]
        text = _response_text(response)

        # Extract grounding sources — deduped by uri.
        sources: List[Dict[str, str]] = []
        seen_uris: set = set()
        grounding = getattr(cand, "grounding_metadata", None)
        for chunk in (getattr(grounding, "grounding_chunks", None) or []) if grounding else []:
            web = getattr(chunk, "web", None)
            uri = (getattr(web, "uri", "") or "") if web else ""
            title = (getattr(web, "title", "") or "") if web else ""
            if uri and uri not in seen_uris:
                seen_uris.add(uri)
                # Grounded search returns Vertex AI Search redirect URIs, so the
                # URL host is always "vertexaisearch" — useless as a publisher.
                # The real publisher domain comes through in `title` as a bare
                # host like "infosys.com". Prefer that; fall back to the URI host.
                publisher = ""
                title_clean = title.strip().lower()
                if re.match(r"^[\w.-]+\.[a-z]{2,}$", title_clean):
                    publisher = title_clean.replace("www.", "").split(".")[0]
                else:
                    try:
                        from urllib.parse import urlparse
                        host = urlparse(uri).hostname or ""
                        if host and "vertexaisearch" not in host:
                            publisher = host.replace("www.", "").split(".")[0]
                    except Exception:
                        pass
                sources.append({"title": title[:200], "uri": uri, "publisher": publisher})

        search_queries = list(getattr(grounding, "web_search_queries", None) or []) if grounding else []
        finish_reason = _response_finish(response)
        if finish_reason and finish_reason != "STOP":
            logger.warning(
                "Gemini grounded research finished with reason=%s — response may be truncated",
                finish_reason,
            )

        return {
            "text": text,
            "tokens_used": _response_tokens(response),
            "grounding_sources": sources,
            "search_queries": search_queries,
            "finish_reason": finish_reason,
            "model": model,
        }

    @async_retry(max_attempts=2, delay=2.0)
    async def generate_with_tools(
        self,
        prompt: str,
        tools: List[Any],
        tool_handlers: Dict[str, Callable],
        system_instruction: Optional[str] = None,
        model_name: Optional[str] = None,
        max_output_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Generate a response using Gemini Function Calling (single-round).

        Gemini may call one of the declared tools. When it does, this method
        executes the matching handler, feeds the result back, and returns the
        final text + any structured data the handler produced (``tool_results``).

        Args:
            prompt: User prompt.
            tools: List of ``types.Tool`` objects (function declarations).
            tool_handlers: ``{function_name: async_callable}`` map; each callable
                receives the function-call args dict and returns a dict.
            system_instruction: Optional system instruction.
            model_name: Optional model override.
        """
        model = model_name or self.model_name
        config = self._config(
            system_instruction=system_instruction, tools=tools,
            max_output_tokens=max_output_tokens,
        )
        try:
            response = await _call_with_timeout(
                self._client.aio.models.generate_content(
                    model=model, contents=prompt, config=config,
                ),
                what="generate_with_tools",
            )

            tool_results: List[Dict[str, Any]] = []
            candidate = (response.candidates or [None])[0]
            parts = (candidate.content.parts if candidate and candidate.content else None) or []

            # Collect EVERY function_call in the model's turn — gemini-2.5 can emit several in
            # parallel. Handling only the first (while echoing candidate.content, which carries ALL
            # the calls) sent back a function_response count that mismatched the call count → the API
            # 400s and the whole tool round is lost. Mirror stream_agentic: run each call and append
            # ONE function_response per call (an error response for an unknown handler) so counts match.
            fn_calls = [
                p.function_call for p in parts
                if getattr(p, "function_call", None) and p.function_call.name
            ]
            if fn_calls:
                response_parts: List[Any] = []
                for fc in fn_calls:
                    args = dict(fc.args) if fc.args else {}
                    handler = tool_handlers.get(fc.name)
                    if handler is None:
                        logger.warning(f"Gemini called unknown tool: {fc.name}")
                        handler_result = {"error": f"unknown tool: {fc.name}"}
                    else:
                        logger.info(f"Gemini invoked tool '{fc.name}' with args: {args}")
                        handler_result = await handler(args)
                        tool_results.append(handler_result)
                    response_parts.append(types.Part.from_function_response(
                        name=fc.name,
                        response={"result": handler_result},
                    ))

                # Feed the results back. Append the model's turn VERBATIM (candidate.content) so any
                # thought_signature is preserved, then ONE user turn with a response per call.
                follow_up = await _call_with_timeout(
                    self._client.aio.models.generate_content(
                        model=model,
                        contents=[
                            types.Content(role="user", parts=[types.Part(text=prompt)]),
                            candidate.content,
                            types.Content(role="user", parts=response_parts),
                        ],
                        config=config,
                    ),
                    what="generate_with_tools tool follow-up",
                )
                return {
                    "text": _response_text(follow_up),
                    "model": self.model_name,
                    "tokens_used": _response_tokens(follow_up),
                    "finish_reason": _response_finish(follow_up),
                    "tool_results": tool_results,
                }

            # No function call — return normal text response.
            return {
                "text": _response_text(response),
                "model": self.model_name,
                "tokens_used": _response_tokens(response),
                "finish_reason": _response_finish(response),
                "tool_results": tool_results,
            }

        except Exception as e:
            # Was an UNCONDITIONAL ERROR — the only one of the five handlers in this
            # file that never consulted the classifier, so a plain 429 / "high
            # demand" / per-call timeout paged Sentry as if it were a code bug.
            # Mirrors generate_text / generate_json / generate_embedding /
            # generate_grounded_research now. The try still spans the tool handlers,
            # so a genuine bug inside an FMP tool keeps its ERROR + stack.
            if not is_transient_gemini_error(e):
                logger.error(f"Gemini tool-calling generation failed: {e}", exc_info=True)
            raise

    def create_tool_chat(
        self,
        system_instruction: Optional[str],
        tools: List[Any],
        temperature: float = 0.7,
        max_output_tokens: int = 8192,
    ):
        """Create a stateful async chat session bound to function-calling tools
        (for the agentic research loop). Returns a google-genai AsyncChat; drive
        it with ``await _call_with_timeout(chat.send_message(...))``. The chats
        module auto-preserves the model's turns (incl. thought_signature) across
        rounds, so the caller only feeds tool responses back."""
        return self._client.aio.chats.create(
            model=self.model_name,
            config=types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                system_instruction=system_instruction or None,
                tools=list(tools),
            ),
        )

    async def stream_agentic(
        self,
        prompt: str,
        tools: List[Any],
        tool_handlers: Dict[str, Callable],
        system_instruction: Optional[str] = None,
        max_rounds: int = 4,
        model_name: Optional[str] = None,
        usage_tag: Optional[str] = None,
        max_output_tokens: Optional[int] = None,
    ):
        """Stream a MULTI-ROUND agentic answer: the model can call function-calling tools
        mid-stream (manual FC), while reasoning + answer stream throughout.

        Yields tagged events:
          * ("thought", str) — a reasoning summary chunk (→ the thinking card)
          * ("answer", str)  — an answer text chunk (→ the message bubble)
          * ("tool", {"name","args","result"}) — AFTER a tool ran (→ tool_step + widget extraction)

        client.aio.chats auto-preserves the model's turns (incl. thought signatures) across rounds;
        we only feed tool responses back. Bounded by max_rounds, with a final answer round if the
        model is still calling tools at the cap (so the user always gets a reply). Honors the quota
        circuit breaker manually (a partial stream can't be safely @async_retry'd)."""
        if _quota_circuit.is_open():
            raise GeminiQuotaError("Gemini quota circuit open (resource_exhausted) — failing fast")
        config = self._config(
            system_instruction=system_instruction,
            tools=tools,
            max_output_tokens=max_output_tokens,
            thinking_config=types.ThinkingConfig(include_thoughts=True),
        )
        # Manual function calling — we run handlers ourselves (AFC-while-streaming is buggy upstream).
        config.automatic_function_calling = types.AutomaticFunctionCallingConfig(disable=True)
        resolved_model = model_name or self.model_name
        chat = self._client.aio.chats.create(model=resolved_model, config=config)

        message: Any = prompt
        usage = _StreamUsage()
        try:
            for _round in range(max_rounds):
                fcalls: List[Any] = []
                stream = await chat.send_message_stream(message)
                async for chunk in stream:
                    usage.observe(chunk)
                    for part in _iter_parts(chunk):
                        fc = getattr(part, "function_call", None)
                        if fc and fc.name:
                            fcalls.append(fc)
                            continue
                        try:
                            text = part.text or ""
                        except (ValueError, AttributeError):
                            text = ""
                        if text:
                            yield ("thought" if getattr(part, "thought", False) else "answer"), text
                # Round boundary: per-chunk counts are cumulative WITHIN a round but
                # additive ACROSS rounds, so fold before the next send_message_stream.
                usage.commit_round()
                if not fcalls:
                    _quota_circuit.record_success()
                    return
                # Run the requested tools, emit a "tool" event each, feed responses back next round.
                response_parts: List[Any] = []
                for fc in fcalls:
                    args = dict(fc.args) if fc.args else {}
                    handler = tool_handlers.get(fc.name)
                    if handler is None:
                        logger.warning("Agentic chat requested unknown tool: %s", fc.name)
                        result = {"error": f"unknown tool: {fc.name}"}
                    else:
                        try:
                            result = await handler(args)
                        except Exception as e:
                            logger.warning("Agentic tool %s failed: %s: %s", fc.name, type(e).__name__, e)
                            result = {"error": str(e)}
                    yield "tool", {"name": fc.name, "args": args, "result": result}
                    response_parts.append(types.Part.from_function_response(
                        name=fc.name,
                        response={"result": json.dumps(result, default=str)[:8000]},
                    ))
                message = response_parts

            # max_rounds exhausted while still calling tools — one final answer round (tools ignored)
            # so the user always gets a reply.
            final_stream = await chat.send_message_stream(message)
            async for chunk in final_stream:
                usage.observe(chunk)
                for part in _iter_parts(chunk):
                    if getattr(part, "function_call", None):
                        continue
                    try:
                        text = part.text or ""
                    except (ValueError, AttributeError):
                        text = ""
                    if text:
                        yield ("thought" if getattr(part, "thought", False) else "answer"), text
            _quota_circuit.record_success()
        except Exception as e:
            if _is_quota_error(e):
                _quota_circuit.record_quota_error()
            raise
        finally:
            # See stream_text: the early `return` above, a client disconnect, and an
            # exception all land here, and all three spent tokens.
            _log_gemini_usage(
                usage.totals(), call_site="stream_agentic", model=resolved_model, tag=usage_tag,
            )


# Global client instance
_gemini_client: Optional[GeminiClient] = None


def get_gemini_client() -> GeminiClient:
    """Get or create the global Gemini client instance."""
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = GeminiClient()
    return _gemini_client
