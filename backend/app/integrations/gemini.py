"""
Google Gemini API Integration
Handles all interactions with Google Gemini for AI features.
Requirements: Section 3.3, 4.3.1 - Google Gemini API for deep research

Uses the unified `google-genai` SDK (async-native via `client.aio.*`). The
`GeminiClient` public method signatures are frozen — the 12 services that call
`get_gemini_client()` are unaffected by the SDK swap.
"""

from typing import Optional, List, Dict, Any, Callable, Tuple
import logging
import asyncio
import hashlib
import json
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
    bare `asyncio.TimeoutError` escape, and two outer handlers catch that type
    today (`home_dashboard_service`, `chat_context_resolver`). Keeping
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
    quota errors the breaker OPENS and `is_open()` returns True for
    `GEMINI_QUOTA_CIRCUIT_COOLDOWN_SECONDS`; calls then fail fast (the caller's
    sentinel fallback applies).

    HALF-OPEN, for real (2026-09-11). Once the cooldown elapses the breaker admits
    exactly ONE trial call — `is_open()` returns False once and stamps
    `_trial_started_at`; every other caller keeps failing fast until that trial
    reports. A `record_success()` closes the breaker fully; a `record_quota_error()`
    during the trial re-opens it immediately with the deadline measured from NOW.
    The previous shape cleared all state at the cooldown boundary, which readmitted
    every parallel caller at once — under a sustained 429 that cycled 30 s off /
    ~20 errors on, and the docstring's "half-open" was a promise the code did not
    keep. A trial that ends WITHOUT a verdict (a non-quota failure, a give-up, a
    disconnect before the first chunk) hands the slot back at once via
    `release_trial`; one that is truly lost (no `finally` ever ran) expires after
    another cooldown, so a lost trial cannot wedge the breaker open. For streams the
    verdict is the FIRST chunk, so a long answer does not hold every other caller.

    Single-event-loop process → no lock needed (all access is on one thread).
    """

    def __init__(self) -> None:
        self._consecutive = 0
        self._opened_at = 0.0
        self._trial_started_at = 0.0  # > 0 while the single half-open trial is in flight

    def reset(self) -> None:
        """Pristine closed state (tests, and the one legitimate operator use)."""
        self._consecutive = 0
        self._opened_at = 0.0
        self._trial_started_at = 0.0

    @property
    def half_open(self) -> bool:
        return self._opened_at > 0.0 and self._trial_started_at > 0.0

    @property
    def tripped(self) -> bool:
        """Non-mutating: is the breaker currently refusing calls? `is_open()` ADMITS the
        half-open trial as a side effect, so it must be consulted once per logical call;
        every other check reads this."""
        if self._opened_at <= 0.0:
            return False
        now = time.time()
        cooldown = settings.GEMINI_QUOTA_CIRCUIT_COOLDOWN_SECONDS
        if now - self._opened_at < cooldown:
            return True
        return self._trial_started_at > 0.0 and now - self._trial_started_at < cooldown

    def is_open(self) -> bool:
        if self._opened_at <= 0.0:
            return False
        now = time.time()
        cooldown = settings.GEMINI_QUOTA_CIRCUIT_COOLDOWN_SECONDS
        if now - self._opened_at < cooldown:
            return True
        # Cooldown elapsed → HALF-OPEN. One trial at a time; a trial older than a
        # cooldown is presumed lost and a fresh one is admitted.
        if self._trial_started_at > 0.0 and now - self._trial_started_at < cooldown:
            return True
        self._trial_started_at = now
        logger.warning(
            "Gemini quota circuit HALF-OPEN — admitting one trial call after %.0fs", cooldown,
        )
        return False

    @property
    def trial_stamp(self) -> float:
        """The in-flight trial's start time (0.0 when none) — the token `release_trial`
        needs, captured by the admitted caller right after `is_open()` admitted it."""
        return self._trial_started_at

    def release_trial(self, stamp: float) -> None:
        """Give the half-open slot back when the trial ended WITHOUT a verdict.

        The trial used to be held for its whole life — and then for a full cooldown
        after a lost one — by whichever call happened to arrive first after the outage:
        a chat stream the user stopped after the first token, a synthesis that ran two
        tool rounds, a timeout that gave up. Every other Gemini call in the process failed
        fast for those seconds/minutes on a quota that had already recovered (F04-3).
        Stamp-guarded: after a cooldown a SUCCESSOR trial is admitted concurrently, and an
        unguarded release from the expired one would clear the successor's slot.
        """
        if stamp > 0.0 and self._trial_started_at == stamp:
            self._trial_started_at = 0.0
            logger.info("Gemini quota circuit trial ended without a verdict — slot released")

    def record_quota_error(self) -> None:
        if self.half_open:
            # The trial failed on quota → re-open at once, deadline from now. The
            # consecutive count stays at the threshold so the breaker is still "open
            # for cause", not counting up from zero again.
            self._trial_started_at = 0.0
            self._opened_at = time.time()
            self._consecutive = max(self._consecutive, settings.GEMINI_QUOTA_CIRCUIT_THRESHOLD)
            logger.error(
                "Gemini quota circuit trial FAILED — re-opened, failing fast for %.0fs",
                settings.GEMINI_QUOTA_CIRCUIT_COOLDOWN_SECONDS,
            )
            return
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
        if self._opened_at > 0.0:
            logger.info("Gemini quota circuit CLOSED after a successful call")
        self._consecutive = 0
        self._opened_at = 0.0
        self._trial_started_at = 0.0


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


class _CacheHit:
    """A decorated body's way of saying "this came from the cache, not from upstream".

    `async_retry` unwraps it and does NOT record a breaker success — a hit proves nothing
    about the quota.
    """
    __slots__ = ("value",)

    def __init__(self, value: Any) -> None:
        self.value = value


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
            if kwargs.get("service_tier") == "flex":
                # Google's Flex tier (only the background sentiment backfill asks for it) is
                # ONE attempt that never touches the shared breaker: a busy Flex answers 429,
                # and running that through the ladder below cost 3 calls, 15 s of backoff, an
                # ERROR (a Sentry event) and 3 strikes on the breaker that fails Cay AI chat
                # and reports fast. The caller falls back to the standard tier itself.
                result = await func(*args, **kwargs)
                return result.value if isinstance(result, _CacheHit) else result
            attempt = 0            # generic failures
            quota_attempt = 0      # quota/429 failures
            overload_attempt = 0   # server-overload / 5xx failures
            timeout_attempt = 0    # per-call timeouts (own budget, default 0)
            admitted = False       # the breaker gate is consulted ONCE per logical call
            is_trial = False       # ...and only the admitted half-open TRIAL may ignore a trip
            trial_stamp = 0.0      # the slot token, so a give-up can hand the trial back
            verdict = False        # record_success / record_quota_error ran
            try:
                while True:
                    # Fail fast while the breaker is open — don't add load to an
                    # already-exhausted quota; the caller's sentinel fallback fires.
                    #
                    # Consulted ONCE: `is_open()` admits the single half-open trial as a side
                    # effect, so re-checking it on every retry iteration made the trial call
                    # reject ITSELF — an overload/5xx retry came back to the loop top, saw its
                    # own trial marker, and raised a quota error for a retryable 503.
                    if not admitted:
                        if _quota_circuit.is_open():
                            raise GeminiQuotaError(
                                "Gemini quota circuit open (resource_exhausted) — "
                                "failing fast"
                            )
                        admitted = True
                        is_trial = _quota_circuit.half_open
                        trial_stamp = _quota_circuit.trial_stamp if is_trial else 0.0
                    elif not is_trial:
                        # Admitted while CLOSED, then slept through a backoff while other
                        # callers tripped the breaker: don't wake up and add one more request
                        # to an exhausted quota (whose 429 would be booked as a trial failure).
                        # Through the ADMITTING gate, not the read-only one: if the cooldown
                        # has since elapsed with no trial in flight, this straggler becomes the
                        # trial rather than firing un-admitted with its 429 landing as a plain
                        # increment on a stale deadline. The trial itself never re-checks — it
                        # is the one call allowed to probe.
                        if _quota_circuit.is_open():
                            raise GeminiQuotaError(
                                "Gemini quota circuit opened during backoff — failing fast"
                            )
                        is_trial = _quota_circuit.half_open
                        trial_stamp = _quota_circuit.trial_stamp if is_trial else 0.0
                    try:
                        result = await func(*args, **kwargs)
                        if isinstance(result, _CacheHit):
                            # Served from the response cache: NO upstream call happened, so
                            # it proves nothing about the quota. Booking it as a success used
                            # to close a half-open breaker from a cache hit and readmit every
                            # parallel caller to a still-exhausted quota.
                            return result.value
                        verdict = True
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
                            verdict = True
                            _quota_circuit.record_quota_error()
                            quota_attempt += 1
                            if (
                                quota_attempt > settings.GEMINI_QUOTA_MAX_RETRIES
                                or _quota_circuit.tripped
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
            finally:
                if is_trial and not verdict:
                    _quota_circuit.release_trial(trial_stamp)
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


_CLEAN_FINISH = ("", "STOP", "FINISH_REASON_STOP", "UNSPECIFIED", "FINISH_REASON_UNSPECIFIED")


def _is_clean_finish(reason: Optional[str]) -> bool:
    """Whether a finish reason means the model finished on its own terms.

    Anything else (MAX_TOKENS, SAFETY, RECITATION, OTHER, …) is a CUT — the caller must
    not treat the text as complete: not charge it in full, not cache it, not settle it
    as delivered. One definition, shared by the response cache and the stream markers.
    """
    return str(reason or "").upper() in _CLEAN_FINISH


_LENGTH_FINISH = ("MAX_TOKENS", "FINISH_REASON_MAX_TOKENS")


def is_length_cut(reason: Optional[str]) -> bool:
    """Whether a cut was the OUTPUT CEILING — the one kind a continuation can finish.

    A SAFETY / RECITATION / OTHER stop is a cut too (`_is_clean_finish` is False), but
    asking the model to "continue from the exact point it stops" past a safety or
    recitation block is a wasted capped round with a false premise, and a "Continue
    your answer" chip after it is a dead end. The chat door continues, and offers the
    chip, only for these.
    """
    return str(reason or "").upper() in _LENGTH_FINISH


def _cacheable_answer(result: Dict[str, Any]) -> bool:
    """Only a COMPLETE answer is worth an hour in the response cache.

    An empty text (a safety block, or MAX_TOKENS spent inside the thinking budget) or a
    non-STOP finish is a failure shape, and the cache key is fully deterministic for a chat
    prompt — so a failed turn (not persisted, refunded) was replayed as the same failure on
    every retry for `GEMINI_CACHE_TTL`.
    """
    text = result.get("text")
    if not isinstance(text, str) or not text.strip():
        return False
    return _is_clean_finish(result.get("finish_reason"))


def _has_function_call(response: Any) -> bool:
    """Whether the model's turn contains a function-call part (a request for another tool round)."""
    try:
        candidate = (response.candidates or [None])[0]
        parts = (candidate.content.parts if candidate and candidate.content else None) or []
    except Exception:
        return False
    return any(getattr(p, "function_call", None) and p.function_call.name for p in parts)


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
#
# `thoughts_token_count` was added for the report thinking-budget work
# (SYSTEM_DESIGN_GUIDELINES 9b.7). Thinking bills at the OUTPUT rate, so an
# uncapped reasoning step is a real cost line — and without this field there is
# no way to confirm from production logs that a cap actually took effect. It is
# reported SEPARATELY from `candidates_token_count` — MEASURED, not assumed:
# `total - prompt - candidates - thoughts == 0` on a real uncapped call
# (scripts/eval_report_thinking.py prints the verdict). So `output` is the
# visible answer and `total` is the bill; neither contains the other. Every consumer (_EMPTY_USAGE, _response_usage, _StreamUsage) derives
# from this tuple, so adding a field is additive, and _coerce_token_count
# already degrades a model that does not report it to None.
_USAGE_FIELDS: tuple[tuple[str, str], ...] = (
    ("total", "total_token_count"),
    ("prompt", "prompt_token_count"),
    ("cached", "cached_content_token_count"),
    ("output", "candidates_token_count"),
    ("thoughts", "thoughts_token_count"),
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
    finish: Optional[str] = None,
) -> None:
    """Emit ONE greppable line per Gemini call. Best-effort, never raises.

    `cached_pct` is the number this exists for: the share of input tokens that
    were served from the prefix cache at a 75% discount. A persistent 0 means
    the stable prefix is not being reused (too short, or something volatile —
    a price, a timestamp, a session id — is polluting the front of the request).

    `finish` is the candidate's finish reason (STOP / MAX_TOKENS / SAFETY / …) so a
    cut answer can be found in the logs next to the `thoughts_tok` that caused it —
    the TestFlight "answer cut off" report was diagnosed from exactly that pairing,
    and before this field the reason had to be inferred from the token arithmetic.
    """
    try:
        prompt = usage.get("prompt") or 0
        cached = usage.get("cached") or 0
        cached_pct = round(100.0 * cached / prompt, 1) if prompt > 0 else 0.0
        logger.info(
            "GEMINI_USAGE call_site=%s model=%s tag=%s prompt_tok=%s cached_tok=%s "
            "cached_pct=%s output_tok=%s thoughts_tok=%s total_tok=%s finish=%s",
            call_site, model, tag or "-",
            usage.get("prompt"), usage.get("cached"), cached_pct,
            usage.get("output"), usage.get("thoughts"), usage.get("total"),
            finish or "-",
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


def _thinking_config(budget: Optional[int]) -> Optional[Any]:
    """Encode a thinking budget as a ThinkingConfig, or None to send NONE at all.

    ONE encoder for all three generation helpers so they are provably identical.
    `None` means "attach no thinking_config", which is byte-identical on the wire
    to a pre-cap request — that is what makes it a safe rollback value. `0`
    disables thinking; a positive value is a ceiling in tokens.
    """
    return None if budget is None else types.ThinkingConfig(thinking_budget=budget)


def _stream_thinking_config(budget: Optional[int]) -> Any:
    """The STREAMING twin of `_thinking_config`: always asks for thought summaries.

    The two chat stream methods render `include_thoughts=True` parts as the thinking
    card, so they cannot use `_thinking_config` (which omits the flag and would blank
    the card). `None` keeps today's request byte-identical (thoughts on, no ceiling —
    the rollback value); `0` disables thinking, which also empties the card; a
    positive value is the ceiling. Encoded here once so both methods stay identical.
    """
    if budget is None:
        return types.ThinkingConfig(include_thoughts=True)
    return types.ThinkingConfig(include_thoughts=True, thinking_budget=budget)


#: `generate_with_tools` runs a forced turn's extra tool round only this early: the send door wraps
#: the whole call in `CHAT_SEND_BUDGET_SECONDS` (50 s), and a cold round-2 tool can take 30 s.
#: (Used only when the caller passes no `deadline`; with one, the gate is the time LEFT.)
_FORCED_EXTRA_ROUND_MAX_ELAPSED_SECONDS = 20.0

#: With a turn `deadline` (`generate_with_tools`), every tool round must end this long before it, so
#: the follow-up Gemini call — and a possible tool-less final one — still fit inside the send
#: door's budget (review 2026-10-09: two waves of 30 s ceilings alone passed the 50 s budget and the
#: turn was cancelled with tool data in hand).
_SEND_ANSWER_RESERVE_SECONDS = 12.0


def _forced_names(force_tool: Any, tool_handlers: Dict[str, Any]) -> List[str]:
    """The names round 1 may call: `force_tool` is one name or a sequence of names; only names
    with a handler survive (order kept, duplicates dropped). Anything else → []."""
    if isinstance(force_tool, str):
        candidates: List[Any] = [force_tool]
    elif isinstance(force_tool, (list, tuple, frozenset, set)):
        candidates = sorted(force_tool) if isinstance(force_tool, (set, frozenset)) else list(force_tool)
    else:
        return []
    out: List[str] = []
    for name in candidates:
        if isinstance(name, str) and name and name not in out and tool_handlers.get(name) is not None:
            out.append(name)
    return out


def _forced_tool_config(config: Any, force_tool: Any, tool_handlers: Dict[str, Any]) -> Any:
    """A copy of `config` whose request MUST call one of `force_tool` (function calling mode ANY,
    those names allowed), or None when nothing is forced. `force_tool` is one name or a sequence.
    Used for the FIRST round only — the caller's gate already decided what runs first:
      * an explicit "search the web / verify" ask → the web search (a prompt rule alone did not
        hold: under a routed lens, and whenever the question said "news", the model took the
        headlines tool instead — prod 2026-10-03; a probe: 0/6 under the sentiment lens);
      * a "latest news" ask → Caydex's licensed news tools (owner decision 2026-10-08:
        licensed data first) — the screen company's headline tools, or the market snapshot when
        no company is in view — so the declared web search can only follow in a later round;
      * never the web for a market-data question (the caller passes nothing to force).
    A name with no handler is never forced (the call would end in an error result). The copy keeps
    every other field, thinking and the full tool declarations included."""
    names = _forced_names(force_tool, tool_handlers)
    if not names:
        return None
    return config.model_copy(update={"tool_config": types.ToolConfig(
        function_calling_config=types.FunctionCallingConfig(
            mode=types.FunctionCallingConfigMode.ANY, allowed_function_names=names,
        ),
    )})


def truncate_tool_result(result: Any, budget: Optional[int] = None) -> Any:
    """Shrink a tool result to fit `budget` characters of JSON, STRUCTURALLY.

    Three call sites used to slice `json.dumps(result)[:N]` at three different N
    (8000 / 5000 / none), so an oversized result reached the model as syntactically
    broken JSON with no hint that anything was missing. This prunes instead — list
    tails first, then long strings — and stamps `{"_truncated": true, "_dropped":
    <n>}` so the model can say "I only saw part of this" rather than reading a cut.

    Never raises; a value that cannot be serialised is stringified. One budget
    (`GEMINI_TOOL_RESULT_MAX_CHARS`) for every caller.
    """
    limit = int(budget or getattr(settings, "GEMINI_TOOL_RESULT_MAX_CHARS", 8000) or 8000)

    def _size(v: Any) -> int:
        try:
            return len(json.dumps(v, default=str))
        except Exception:
            return len(str(v))

    if _size(result) <= limit:
        return result

    dropped = 0

    def _prune(v: Any, list_cap: int, str_cap: int) -> Any:
        nonlocal dropped
        if isinstance(v, dict):
            return {k: _prune(x, list_cap, str_cap) for k, x in v.items()}
        if isinstance(v, list):
            if len(v) > list_cap:
                dropped += len(v) - list_cap
                v = v[:list_cap]
            return [_prune(x, list_cap, str_cap) for x in v]
        if isinstance(v, str) and len(v) > str_cap:
            dropped += 1
            return v[: max(str_cap - 1, 1)] + "…"
        return v

    pruned: Any = result
    for list_cap, str_cap in ((25, 1200), (12, 600), (6, 300), (3, 160), (1, 80)):
        dropped = 0
        pruned = _prune(result, list_cap, str_cap)
        # Always marked, whatever the shape: a bare list or string that was cut down
        # must not read as the complete answer.
        if isinstance(pruned, dict):
            pruned = {**pruned, "_truncated": True, "_dropped": dropped}
        else:
            pruned = {"result": pruned, "_truncated": True, "_dropped": dropped}
        if _size(pruned) <= limit:
            return pruned
    # A single enormous scalar (or a non-dict shape that will not prune): keep a valid
    # JSON envelope around a hard cut rather than cutting the JSON itself.
    text = json.dumps(result, default=str)
    return {"_truncated": True, "_dropped": dropped, "partial": text[: max(limit - 64, 1)]}


def _response_finish(response: Any) -> Optional[str]:
    try:
        cand = (response.candidates or [None])[0]
        fr = getattr(cand, "finish_reason", None) if cand else None
        return getattr(fr, "name", fr) if fr is not None else None
    except Exception:
        return None


# Per-tool ceilings, in seconds. The default (`CHAT_TOOL_TIMEOUT_SECONDS`) fits a quote or a
# cached read; the market tools sweep several cached services and may fetch the news feed on
# a miss. (`explain_price_move` had 75 s while it could escalate to a grounded Google search —
# retired 2026-10-02 for the Grounding terms; it is now detectors + the FMP news corpus.)
_TOOL_TIMEOUTS: Dict[str, float] = {
    "explain_price_move": 30.0,
    "get_market_snapshot": 30.0,
    "get_market_overview": 20.0,
    # Ask Cay AI's ownership tool (`chat_ownership_tool`): a warm Holders build is a cache read,
    # a cold one is the Holders tab's full fan-out (~a dozen calls in one gather, plus the 13F
    # quarter history). The handler is shielded, so a timeout still finishes and warms the
    # build for the next question. (Literal name, as above: `chat_tools.OWNERSHIP_TOOL`.)
    "check_ownership_filings": 20.0,
    # Ask Cay AI's financials tool (`chat_financials_tool`, 2026-10-08): up to ten cache-aside
    # reads of the Financials / Overview services, each bounded at ~15 s inside the tool, so a
    # cold ticker answers with what loaded and names the rest. Shielded like every handler: a
    # timeout still finishes the reads and warms their caches. (Literal name, as above:
    # `chat_tools.FINANCIALS_TOOL`.)
    "check_company_financials": 20.0,
    # Ask Cay AI's asset-profile tool (`chat_profile_tool`, 2026-10-08): company facts and
    # peers, fund facts or coin facts, each a cache-aside read. The tool answers "still
    # loading" at its own overall deadline (10.5 s), inside this ceiling, so the model gets
    # the facts that did load rather than a bare timeout. Shielded like every handler.
    # (Literal name, as above: `chat_tools.PROFILE_TOOL`.)
    "check_asset_profile": 12.0,
    # Ask Cay AI's web search (`chat_web_search_service`): up to three budget-claim RPCs off the
    # loop (an automatic search claims its account's bucket, the automatic global bucket and the
    # global cap, in order) plus Brave's hard bound (BRAVE_SEARCH_TIMEOUT_SECONDS + 2 s), with
    # slack. The handler is shielded, and a model re-issue after a `timed_out` joins the same
    # per-turn search, so nothing is paid twice. (The literal name, not an import: this
    # integration must not import the services layer; `test_gemini_tool_result_guards` pins it.)
    "web_search": 15.0,
}

def _tool_ceiling(name: str) -> float:
    """One tool's ceiling in seconds: its `_TOOL_TIMEOUTS` entry, else `CHAT_TOOL_TIMEOUT_SECONDS`."""
    return float(_TOOL_TIMEOUTS.get(name) or getattr(settings, "CHAT_TOOL_TIMEOUT_SECONDS", 8.0) or 8.0)


def _not_run_result(name: str) -> Dict[str, Any]:
    """The result of a job still queued when the round's time ran out (a turn `deadline`): its
    handler never starts. Model-facing, so no vendor name. `upstream`: the time went to OUR slow
    data requests, so a turn on which nothing at all loaded is refunded like any other upstream
    failure (a job that DID load keeps the turn charged — `tool_results` is not empty)."""
    return {
        "error": "not_run",
        "tool": name,
        "upstream": True,
        "detail": "This data request did not run: the answer's time ran out while other requests "
                  "were loading. Answer from the results you have and say this part could not be "
                  "loaded.",
    }


# Tools whose arguments are derived from the USER's own words: their args never reach a log
# line verbatim (a web query is a paraphrase of a user's question). Logged as a shape instead.
_REDACTED_ARG_TOOLS = frozenset({"web_search"})


def _loggable_tool_args(name: str, args: Any) -> Any:
    """`args` for a log line — the argument SHAPE only for a tool in `_REDACTED_ARG_TOOLS`."""
    if name not in _REDACTED_ARG_TOOLS:
        return args
    if not isinstance(args, dict):
        return {"redacted": type(args).__name__}
    return {k: f"<{len(str(v))} chars>" for k, v in args.items()}


async def _run_tool_handler(name: str, handler: Optional[Callable], args: Dict[str, Any],
                            max_wait: Optional[float] = None) -> Any:
    """Run one function-calling handler with the guards every caller needs.

    * Unknown tool → an error result (never an exception): the model gets one
      function_response per call it made, so the counts still match.
    * Exception inside the handler → `{"error": ...}` + a WARNING. In
      `generate_with_tools` the handler used to run inside the `@async_retry` body,
      so an FMP `FMPRateLimitException` ("rate limit" in its message) was classified
      as a Gemini QUOTA error, retried with the quota ladder, and counted toward
      the process-wide circuit breaker. A tool failure is data for the model, not
      a Gemini failure.
    * Per-call timeout (`CHAT_TOOL_TIMEOUT_SECONDS`) → `{"error": "timed_out"}`.
      An index tool that recomputes a cold detail cache used to hold the whole
      stream — the same stall the context resolver caps at 4 s — with no bound.
    * `max_wait` (the round's time left before the turn's deadline) lowers the ceiling,
      never raises it.
    """
    if handler is None:
        logger.warning("Gemini requested unknown tool: %s", name)
        return {"error": f"unknown tool: {name}"}
    timeout = _tool_ceiling(name)
    if max_wait is not None:
        timeout = max(0.01, min(timeout, float(max_wait)))
    try:
        # SHIELDED: the ceiling abandons THIS caller's wait, it must not cancel the work.
        # A market tool is usually the LEADER of a shared in-flight build (`get_index_detail`,
        # `price:universe`), and `wait_for`'s cancellation reached the leader, whose
        # CancelledError arm settled the shared future with an error for every joiner —
        # one chat turn's 8 s ceiling failed the index screen and the widget batch.
        return await asyncio.wait_for(asyncio.shield(handler(args)), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("Gemini tool %s timed out after %.1fs (args=%s)", name, timeout,
                       _loggable_tool_args(name, args))
        # `upstream`: OUR side failed to answer, so the turn's refund gate counts it. A
        # handler that returns `{"error": …}` for the model's own bad argument does not.
        return {"error": "timed_out", "tool": name, "timeout_seconds": timeout, "upstream": True}
    except Exception as e:  # noqa: BLE001 — a tool failure must not become a Gemini failure
        logger.warning("Gemini tool %s failed: %s: %s", name, type(e).__name__, e)
        from app.log_redaction import redact_secrets
        return {"error": redact_secrets(f"{type(e).__name__}: {e}")[:200], "tool": name,
                "upstream": True}


# ── One round's tool calls, run CONCURRENTLY (2026-10-08) ─────────────────────
#
# Both doors used to await each call of a round one after another, so a round's latency was the
# SUM of its tools: two 20 s tools in one round passed the send door's 50 s budget
# (`CHAT_SEND_BUDGET_SECONDS`) and the turn was refunded as GEMINI_UNAVAILABLE. A round is now
# planned (`_plan_tool_round`: the per-turn memo's replays, identical calls folded into one
# job) and its unique jobs run together (`_gather_tool_calls`), so the round costs its SLOWEST
# tool. Each job still goes through `_run_tool_handler`: its own shield, its own
# `_TOOL_TIMEOUTS` ceiling and its own error result, so one slow or failing tool never cancels
# another. Results come back in the MODEL's call order — one function_response (and, on the
# stream door, one `tool` event) per call, exactly as before.
#
# A round is BOUNDED (fix round, 2026-10-08). Unbounded, a cancelled turn (the send door's
# budget, the stream deadline, a client gone) let every job of the round keep going behind the
# shield — one 1-credit turn could start a dozen cold builds at once (`check_ownership_filings`
# is ~a dozen FMP calls per cold ticker) on the shared FMP pool other users' screens wait on;
# the serial loop had started only the first. So:
#   * at most `_TOOL_ROUND_MAX_CONCURRENCY` jobs wait on a handler at once. The permit is taken
#     BEFORE `_run_tool_handler` creates the handler, so a cancelled turn never starts a queued
#     job, and a queued job's ceiling does not burn while it waits. (A handler that outlives its
#     ceiling keeps running shielded and its permit returns — so the hard ceiling on handlers one
#     round can start is the job cap below.)
#   * at most `_TOOL_ROUND_MAX_JOBS` unique jobs WITH a handler per round; every call past it is
#     refused with its own `too_many_tool_calls` result (no `upstream` flag: the model asked for
#     too much, so it never counts toward a refund) and the model may ask again next round.
#     8 jobs / 4 at a time is two waves — and with 30 s ceilings (`explain_price_move`,
#     `get_market_snapshot`) two waves alone can pass the send door's 50 s budget, so the send
#     door also passes a turn `deadline`: each job's wait is capped at the time left and a job
#     still queued when it runs out never starts (`_not_run_result`).
#     Memo replays, in-round duplicates and unknown tools run no handler and use no slot.
#
# The two limits are SETTINGS (`CHAT_TOOL_ROUND_MAX_CONCURRENCY` / `CHAT_TOOL_ROUND_MAX_JOBS`),
# read at CALL time through `_round_max_concurrency()` / `_round_max_jobs()`. The module
# constants below are the code's own defaults, pinned equal to the settings' declared defaults
# (`test_chat_financials_tool`); a setting whose value differs from its declared default wins,
# so the environment rules in production, and a unit test can still patch one constant in
# isolation (the settings then sit at their defaults).
_TOOL_ROUND_MAX_CONCURRENCY = 4
_TOOL_ROUND_MAX_JOBS = 8
_ROUND_LIMIT_BOUNDS = (1, 16)


def _round_limit(setting: str, code_default: Any) -> Any:
    """The effective round limit: `settings.<setting>` when it is set away from its declared
    default (an int in 1..16, never a bool), else `code_default`. An out-of-range value — which
    the Settings field refuses at boot, so only a runtime assignment can produce one — logs a
    WARNING and falls back to the code default. Never raises."""
    raw = getattr(settings, setting, None)
    try:
        declared = type(settings).model_fields[setting].default
    except Exception:  # noqa: BLE001 — a settings double without the field: the code default
        declared = None
    if raw is None or raw == declared:
        return code_default
    low, high = _ROUND_LIMIT_BOUNDS
    if isinstance(raw, int) and not isinstance(raw, bool) and low <= raw <= high:
        return raw
    logger.warning("Gemini round limit %s=%r is outside %d..%d — using %r", setting, raw,
                   low, high, code_default)
    return code_default


def _round_max_concurrency() -> Any:
    return _round_limit("CHAT_TOOL_ROUND_MAX_CONCURRENCY", _TOOL_ROUND_MAX_CONCURRENCY)


def _round_max_jobs() -> Any:
    return _round_limit("CHAT_TOOL_ROUND_MAX_JOBS", _TOOL_ROUND_MAX_JOBS)


def _too_many_calls_result(name: str, limit: Optional[int] = None) -> Dict[str, Any]:
    """The result a call past `_TOOL_ROUND_MAX_JOBS` gets — model-facing, so no vendor name."""
    return {
        "error": "too_many_tool_calls",
        "tool": name,
        "limit": _round_max_jobs() if limit is None else limit,
        "detail": "This step already runs the maximum number of data requests; request this "
                  "one again in your next step if you still need it.",
    }


def _call_args(fc: Any) -> Dict[str, Any]:
    """`fc.args` as a plain dict ({} when absent). A malformed upstream value (not a mapping)
    degrades to {} with a WARNING — the handler then answers its own "missing argument" error —
    instead of raising out of the round and failing a turn that may hold other good calls."""
    raw = getattr(fc, "args", None)
    if not raw:
        return {}
    try:
        return dict(raw)
    except (TypeError, ValueError) as e:
        logger.warning("Gemini tool %s: arguments are not a mapping (%s: %s) — running with {}",
                       getattr(fc, "name", "?"), type(e).__name__, e)
        return {}


def _tool_call_key(name: str, args: Any) -> Optional[Tuple[str, str]]:
    """The (name, canonical-JSON args) identity of one call: the per-turn memo's key and the
    in-round dedup key. None when the args cannot be canonicalised (mixed-type keys, a cycle,
    absurd nesting): that call is never deduped or memoised, and a WARNING says so — it used to
    raise out of `stream_agentic` and fail the turn."""
    try:
        return (name, json.dumps(args, sort_keys=True, default=str))
    except (TypeError, ValueError, RecursionError) as e:
        logger.warning("Gemini tool %s: arguments cannot be canonicalised (%s: %s) — "
                       "run without dedup", name, type(e).__name__, e)
        return None


#: One round's plan. `slots[i]` answers the model's i-th call:
#:   ("memo", result)          — the per-turn memo replays a SUCCESSFUL earlier-round result
#:   ("job", j, is_first)      — served by `jobs[j]`; only the FIRST identical call runs it, every
#:                               later identical call in the same round shares its result
#:   ("refused", result)       — past `_TOOL_ROUND_MAX_JOBS`: runs nothing, answered with its own
#:                               `too_many_tool_calls` result (never memoised)
#: `jobs[j]` is (name, handler-or-None, args, key) — run once each, concurrently.
_RoundSlot = Tuple[Any, ...]
_RoundJob = Tuple[str, Optional[Callable], Dict[str, Any], Optional[Tuple[str, str]]]


def _runner_names(jobs: List[_RoundJob]) -> List[str]:
    """The names of the round's jobs that will RUN a handler, in the model's order — what the
    round observer is told. A memo replay, an in-round duplicate, a call refused past the job cap
    and an unknown tool run nothing, so none of them may defer the automatic web search (review
    2026-10-09: a memo-replayed financials call beside `web_search` deferred it every round)."""
    return [name for name, handler, _args, _key in jobs if handler is not None]


def _notify_tool_round(observer: Optional[Callable[[Tuple[str, ...]], Any]],
                       names: List[str]) -> None:
    """Tell the caller's `on_tool_round` observer which tools the round about to run executes
    (`_runner_names`; an EMPTY tuple on the last round that runs tools, after which a deferred
    search could never run) — BEFORE any of its handlers starts. The chat doors use it so an
    automatic web search called beside one of Caydex's own tools waits for their results
    (`chat_web_search_service.WebSearchTurn.note_tool_round`). Never raises: an observer failure is
    logged and the round runs as if there were no observer."""
    if observer is None:
        return
    try:
        observer(tuple(n for n in names if isinstance(n, str)))
    except Exception as e:  # noqa: BLE001 — an observer must never break a round
        logger.warning("Gemini tool-round observer failed (%s: %s) — round runs unobserved",
                       type(e).__name__, e)


def _memoisable(result: Any) -> bool:
    """A tool result the per-turn memo may replay: never an error (a transient failure is not frozen
    for the turn), and never one marked `deferred` (it asks to be run again — the automatic web
    search answers that while Caydex's own tools run in the same round)."""
    return not (isinstance(result, dict) and (result.get("error") or result.get("deferred") is True))


def _plan_tool_round(
    calls: List[Any],
    tool_handlers: Dict[str, Callable],
    memo: Optional[Dict[Tuple[str, str], Any]] = None,
) -> Tuple[List[Tuple[str, Dict[str, Any]]], List[_RoundSlot], List[_RoundJob]]:
    """Split one round's function calls into (per-call (name, args), per-call slots, unique jobs).

    `memo` is `stream_agentic`'s per-turn memo (successful results only); the send door has none,
    so every call there is a job. A call whose args cannot be keyed is its own job. A NEW call
    with a handler once `_TOOL_ROUND_MAX_JOBS` such jobs are planned is refused (WARNING, names
    only — never arguments)."""
    named: List[Tuple[str, Dict[str, Any]]] = []
    slots: List[_RoundSlot] = []
    jobs: List[_RoundJob] = []
    first_job: Dict[Tuple[str, str], int] = {}
    runnable = 0
    refused: List[str] = []
    max_jobs = _round_max_jobs()   # read once per round, at call time
    for fc in calls:
        name = fc.name
        args = _call_args(fc)
        key = _tool_call_key(name, args)
        handler = tool_handlers.get(name)
        named.append((name, args))
        if key is not None and memo is not None and key in memo:
            slots.append(("memo", memo[key]))
        elif key is not None and key in first_job:
            slots.append(("job", first_job[key], False))
        elif handler is not None and runnable >= max_jobs:
            slots.append(("refused", _too_many_calls_result(name, max_jobs)))
            refused.append(name)
        else:
            if key is not None:
                first_job[key] = len(jobs)
            if handler is not None:
                runnable += 1
            slots.append(("job", len(jobs), True))
            jobs.append((name, handler, args, key))
    if refused:
        logger.warning(
            "Gemini round asked for more than %d unique tool calls — refusing %d: %s",
            max_jobs, len(refused), ",".join(refused)[:300],
        )
    return named, slots, jobs


async def _gather_tool_calls(
    jobs: List[_RoundJob], *, door: str, calls: int, refused: int = 0,
    deadline: Optional[float] = None,
) -> List[Any]:
    """Run one round's UNIQUE jobs concurrently; one result per job, in the order given.

    At most `_TOOL_ROUND_MAX_CONCURRENCY` jobs wait on a handler at once; the permit is taken
    BEFORE `_run_tool_handler` creates the handler, so a job still queued when the turn is
    cancelled never starts.

    `deadline` (a `time.monotonic()` instant, the send door's): each job's wait is capped at the
    time left once it holds its permit, and a job whose permit arrives at or after the deadline
    never starts its handler (`_not_run_result`) — so the round always settles before the turn's
    budget and the follow-up answers from the jobs that finished.

    `_run_tool_handler` converts every handler failure — a raise, a timeout, an unknown tool —
    into an error RESULT, so the gather only ever ends early by cancellation. A cancelled turn
    (client gone, the send budget, the stream budget's deadline) cancels the gather, which cancels
    each running job's WAIT and every queued job's permit wait; the shielded handlers already
    running keep going and warm their caches, exactly as a serial await did. A CancelledError is
    never turned into a result: one that a job raised on its own is re-raised once the round has
    settled (a serial round raised it too). Logs one `GEMINI_TOOL_ROUND` line — names and
    durations only, never arguments — INFO when the round settles, WARNING with
    `outcome=cancelled` (and how many handlers had `started`) when it does not, so the latency
    probes also see the rounds a budget cut."""
    if not jobs:
        return []
    started = time.monotonic()
    gate = asyncio.Semaphore(max(1, int(_round_max_concurrency())))
    handlers_started = 0

    async def _timed(name: str, handler: Optional[Callable], args: Dict[str, Any]) -> Tuple[Any, float]:
        nonlocal handlers_started
        async with gate:
            max_wait: Optional[float] = None
            if deadline is not None and handler is not None:
                max_wait = deadline - time.monotonic()
                if max_wait <= 0:
                    logger.warning("Gemini tool %s not run: the round's time ran out before it "
                                   "started", name)
                    return _not_run_result(name), 0.0
            if handler is not None:
                handlers_started += 1
            t0 = time.monotonic()
            result = await _run_tool_handler(name, handler, args, max_wait=max_wait)
            return result, time.monotonic() - t0

    try:
        outcomes = await asyncio.gather(
            *(_timed(name, handler, args) for name, handler, args, _key in jobs),
            return_exceptions=True,
        )
        results: List[Any] = []
        durations: List[str] = []
        for (name, _handler, _args, _key), outcome in zip(jobs, outcomes):
            if isinstance(outcome, BaseException) and not isinstance(outcome, Exception):
                raise outcome      # CancelledError / KeyboardInterrupt: never swallowed
            if isinstance(outcome, Exception):
                # Unreachable by design (`_run_tool_handler` never raises an Exception); kept so a
                # bug in the runner costs ONE call its result, not the whole round.
                logger.error("Gemini tool %s: the round runner raised %s: %s", name,
                             type(outcome).__name__, outcome, exc_info=outcome)
                from app.log_redaction import redact_secrets
                results.append({"error": redact_secrets(f"{type(outcome).__name__}: {outcome}")[:200],
                                "tool": name, "upstream": True})
                durations.append(f"{name}:error")
                continue
            result, elapsed = outcome
            results.append(result)
            durations.append(f"{name}:{elapsed:.2f}s")
    except BaseException as e:
        # The round did not settle: the turn was cancelled mid-round, or a job raised a
        # CancelledError of its own. Names and elapsed only — never arguments — then re-raise.
        logger.warning(
            "GEMINI_TOOL_ROUND door=%s calls=%d ran=%d refused=%d started=%d elapsed=%.2fs "
            "outcome=%s tools=%s",
            door, calls, len(jobs), refused, handlers_started, time.monotonic() - started,
            "cancelled" if isinstance(e, asyncio.CancelledError) else type(e).__name__,
            ",".join(name for name, _h, _a, _k in jobs)[:500],
        )
        raise
    logger.info(
        "GEMINI_TOOL_ROUND door=%s calls=%d ran=%d refused=%d elapsed=%.2fs tools=%s",
        door, calls, len(jobs), refused, time.monotonic() - started, ",".join(durations),
    )
    return results


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
        service_tier: Optional[str] = None,
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
        if service_tier:
            # "flex": Google's 50%-off tier (slower, may answer 429/503 when busy, never falls
            # back by itself). Only the background sentiment backfill asks for it.
            kwargs["service_tier"] = types.ServiceTier(service_tier)
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
            return _CacheHit(cached)   # unwrapped by `async_retry`; never a breaker success

        try:
            response = await _call_with_timeout(
                self._client.aio.models.generate_content(
                    model=model_name or self.model_name,
                    contents=prompt,
                    config=self._config(
                        system_instruction=system_instruction,
                        max_output_tokens=max_output_tokens,
                        thinking_config=_thinking_config(thinking_budget),
                    ),
                ),
                what="generate_text",
            )
            usage = _response_usage(response)
            _log_gemini_usage(
                usage, call_site="generate_text", model=model_name or self.model_name,
            )
            result = {
                "text": _response_text(response),
                "model": self.model_name,
                "tokens_used": usage["total"],
                "finish_reason": _response_finish(response),
            }
            if _cacheable_answer(result):
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
        thinking_budget: Optional[int] = None,
    ):
        """Yield ``(kind, text)`` chunks as Gemini generates.

        `kind` is "thought" (real reasoning summary → the thinking card) or "answer"
        (→ the message bubble). Thinking is requested via ThinkingConfig(include_thoughts=True);
        each streamed part carries a `.thought` flag we branch on — no more prompt-hack
        separator. Raises immediately if the quota circuit is open; propagates the first SDK
        error so the caller can surface an `error` event; the client HTTP timeout guards a hung read.

        `thinking_budget` caps the private reasoning pass (see `_stream_thinking_config`).
        It matters because `max_output_tokens` bounds THOUGHTS + ANSWER together: with
        no ceiling a 1150-token thought left a 1200-token chat turn 40 tokens for the
        visible answer, which then ended mid-sentence with `finish=MAX_TOKENS`.
        """
        if _quota_circuit.is_open():
            raise GeminiQuotaError(
                "Gemini quota circuit open (resource_exhausted) — failing fast"
            )
        # Half-open TRIAL bookkeeping (F04-3): the slot is a token, not a lease on the
        # whole stream. The first chunk proves the quota accepted the request, so the
        # breaker closes THERE — not minutes later when a long answer finishes — and a
        # stream that ends without a verdict (disconnect before the first chunk, a
        # non-quota error) hands the slot back instead of holding it for a cooldown.
        is_trial = _quota_circuit.half_open
        trial_stamp = _quota_circuit.trial_stamp if is_trial else 0.0
        verdict = False
        config = self._config(
            system_instruction=system_instruction,
            max_output_tokens=max_output_tokens,
            thinking_config=_stream_thinking_config(thinking_budget),
        )
        resolved_model = model_name or self.model_name
        usage = _StreamUsage()
        # Bound BEFORE the try: the `finally` logs it, and a request that raises
        # before the first chunk would otherwise NameError inside the logger.
        finish: Optional[str] = None
        try:
            stream = await self._client.aio.models.generate_content_stream(
                model=resolved_model,
                contents=prompt,
                config=config,
            )
            answered = False
            async for chunk in stream:
                usage.observe(chunk)
                if not verdict:
                    verdict = True
                    _quota_circuit.record_success()
                finish = _response_finish(chunk) or finish
                for part in _iter_parts(chunk):
                    # part.text raises on non-text parts (finish-only) — treat as empty.
                    try:
                        text = part.text or ""
                    except (ValueError, AttributeError):
                        text = ""
                    if not text:
                        continue
                    is_thought = bool(getattr(part, "thought", False))
                    answered = answered or not is_thought
                    yield ("thought" if is_thought else "answer"), text
            if answered and not _is_clean_finish(finish):
                # The answer was CUT after real text streamed. Callers must not settle it
                # as complete (the empty-answer cut still surfaces as "empty stream result").
                yield "finish", str(finish)
            verdict = True
            _quota_circuit.record_success()
        except Exception as e:
            if _is_quota_error(e):
                verdict = True
                _quota_circuit.record_quota_error()
            raise
        finally:
            if is_trial and not verdict:
                _quota_circuit.release_trial(trial_stamp)
            # `finally`, not the happy path: a client disconnect closes this async
            # generator (GeneratorExit) and an error raises past it, and BOTH still
            # spent tokens. Logging only on success would hide exactly the turns
            # that cost money without delivering an answer.
            _log_gemini_usage(
                usage.totals(), call_site="stream_text", model=resolved_model, tag=usage_tag,
                finish=finish,
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
        self,
        prompt: str,
        handle: Dict[str, Any],
        thinking_budget: Optional[int] = None,
    ) -> Dict[str, Any]:
        """generate_text variant that runs against a CachedContent prefix.

        The shared evidence + system instruction live in the cache; `prompt` is
        only the per-field instruction. Same timeout + quota path as generate_text.

        `thinking_budget` mirrors `generate_text`'s: None leaves the model's own
        default alone, 0 disables thinking. Unlike `generate_text` there is NO
        response cache here, so the budget needs no cache-key segment — but the
        caller MUST pass the same value to both this method and the inline
        `generate_text` fallback, or a cache hiccup silently un-caps the call
        (see `narrative_prompts.run_narrative_jobs`).
        """
        cache = handle["cache"]
        response = await _call_with_timeout(
            self._client.aio.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=self._config(
                    cached_content=cache.name,
                    thinking_config=_thinking_config(thinking_budget),
                ),
            ),
            what="generate_text_cached",
        )
        usage = _response_usage(response)
        _log_gemini_usage(
            usage, call_site="generate_text_cached", model=self.model_name,
        )
        return {
            "text": _response_text(response),
            "model": self.model_name,
            "tokens_used": usage["total"],
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
        thinking_budget: Optional[int] = None,
        usage_tag: Optional[str] = None,
        temperature: Optional[float] = None,
        cache: bool = True,
        service_tier: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generate structured JSON using Gemini with response_mime_type.
        Optionally enforce a response_schema for guaranteed output shape.
        Results are cached.

        `thinking_budget` behaves exactly as in `generate_text` (None = the
        model's own default, 0 = no thinking) and is part of the CACHE KEY for
        the same reason: a no-thinking answer and a reasoned one to the same
        prompt are different answers, and the cheap one must not be served to
        the caller that asked for the reasoned one.

        `response_schema` is in the key too. It was not, so two callers issuing
        the same prompt under different schemas would have collided and the
        first one's shape served to the second. No such pair exists today; the
        key is fixed rather than the hazard documented.

        `usage_tag` only labels the `GEMINI_USAGE` line (e.g. `marketing_writer`) so one
        caller's spend can be told apart from every other `generate_json` caller. It is
        deliberately NOT in the cache key: it changes no output.

        `temperature` overrides the client default (`GEMINI_TEMPERATURE`) for this call; a
        grader wants 0 so a borderline verdict does not flip between samples. It joins the
        cache key ONLY when set, so every existing caller's key is byte-identical to before.

        `cache=False` bypasses the shared response cache in BOTH directions. The background
        sentiment backfill sends ~1,000 unique prompts a day that are never asked twice; left
        on, they would evict every chat and Insights entry from the 256-slot cache for nothing.

        `service_tier="flex"` asks for Google's discounted tier (same model and output, slower,
        may be refused when busy). It changes no output, so it is not in the cache key.
        """
        parts = [
            "json", prompt, system_instruction or "", model_name or "",
            "" if response_schema is None else f"schema={response_schema!r}",
            "" if thinking_budget is None else f"tb={thinking_budget}",
        ]
        if temperature is not None:
            parts.append(f"temp={float(temperature)!r}")
        key = _cache_key(*parts)
        cached = self._response_cache.get(key) if cache else None
        if cached is not None:
            logger.debug("Gemini generate_json cache HIT")
            return _CacheHit(cached)

        try:
            response = await _call_with_timeout(
                self._client.aio.models.generate_content(
                    model=model_name or self.model_name,
                    contents=prompt,
                    config=self._config(
                        system_instruction=system_instruction,
                        temperature=temperature,
                        response_mime_type="application/json",
                        response_schema=response_schema,
                        thinking_config=_thinking_config(thinking_budget),
                        service_tier=service_tier,
                    ),
                ),
                what="generate_json",
            )
            usage = _response_usage(response)
            _log_gemini_usage(
                usage, call_site="generate_json", model=model_name or self.model_name,
                tag=usage_tag,
            )
            result = {
                "text": _response_text(response),
                "model": self.model_name,
                "tokens_used": usage["total"],
                "finish_reason": _response_finish(response),
            }
            if cache and _cacheable_answer(result):
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
            return _CacheHit(cached)

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
            # EmbedContentResponse carries no usage_metadata, and
            # `metadata.billable_character_count` is populated only on Vertex — this client
            # is the Developer API (api_key), where it is always None. The embed price is
            # per input character, so log the input length, which IS the billed quantity.
            try:
                billable = getattr(getattr(result, "metadata", None), "billable_character_count", None)
                # `chars` is a PROXY: the Developer API bills embeddings per input token,
                # and the SDK exposes no usage on this response, so input length is the
                # closest observable. `billable_chars` stays only so a Vertex deployment
                # would show it; expect None here.
                logger.info(
                    "GEMINI_EMBED call_site=generate_embedding model=%s chars=%d billable_chars=%s dim=%s",
                    model_name, len(str(text)), billable, len(embedding),
                )
            except Exception:  # pragma: no cover — telemetry must never break a call
                pass
            return embedding
        except Exception as e:
            if not is_transient_gemini_error(e):
                logger.error(f"Embedding generation failed: {e}", exc_info=True)
            raise

    @async_retry(max_attempts=2, delay=2.0)
    async def _generate_content_retried(self, *, model: str, contents: Any, config: Any, what: str):
        """ONE model call under the retry policy.

        `generate_with_tools` used to carry the decorator on the WHOLE method, so a generic
        failure of the follow-up call re-entered at the top: the first call was paid again
        and every tool handler re-ran — `explain_price_move` claiming a second paid web-search
        unit. The orchestration below is undecorated; each model call retries on its own.
        """
        return await _call_with_timeout(
            self._client.aio.models.generate_content(model=model, contents=contents, config=config),
            what=what,
        )

    async def generate_with_tools(
        self,
        prompt: str,
        tools: List[Any],
        tool_handlers: Dict[str, Callable],
        system_instruction: Optional[str] = None,
        model_name: Optional[str] = None,
        max_output_tokens: Optional[int] = None,
        thinking_budget: Optional[int] = None,
        force_first_tool: Any = None,
        extra_round_tools: Optional[Any] = None,
        on_tool_round: Optional[Callable[[Tuple[str, ...]], Any]] = None,
        deadline: Optional[float] = None,
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
            thinking_budget: Optional thinking ceiling (`_thinking_config` semantics;
                None = attach nothing). The chat door passes its own budget because
                `max_output_tokens` bounds thoughts + answer together.
            force_first_tool: Optional tool name, or names, the FIRST request must call
                (`_forced_tool_config`); the follow-up uses the ordinary config. A forced turn gets
                ONE extra executed round for the follow-up's own calls (bounded below).
            extra_round_tools: Optional names whose call in an UNFORCED follow-up earns that same
                one extra round — the automatic web search, which the model may call only after
                Caydex's tools ran (`chat_web_search_service.web_extra_round_tools`). Same bound.
            on_tool_round: Optional observer called with each executed round's tool names
                before its handlers run (`_notify_tool_round`; never raises). The extra round
                is the LAST round that runs tools, so it is observed as an empty tuple: a
                deferred automatic search could never run after it.
            deadline: Optional `time.monotonic()` instant by which the WHOLE call must end (the
                send door's budget). Tool rounds end `_SEND_ANSWER_RESERVE_SECONDS` before it
                (each job's wait capped, a queued job past it never started), and the extra
                round runs only while its slowest call's ceiling still fits; None keeps the
                elapsed-time gate (`_FORCED_EXTRA_ROUND_MAX_ELAPSED_SECONDS`).
        """
        model = model_name or self.model_name
        tool_deadline = (
            deadline - _SEND_ANSWER_RESERVE_SECONDS
            if isinstance(deadline, (int, float)) and not isinstance(deadline, bool) else None
        )
        config = self._config(
            system_instruction=system_instruction, tools=tools,
            max_output_tokens=max_output_tokens,
            thinking_config=_thinking_config(thinking_budget),
        )
        first_config = _forced_tool_config(config, force_first_tool, tool_handlers) or config
        forced = first_config is not config
        started = time.monotonic()
        try:
            response = await self._generate_content_retried(
                model=model, contents=prompt, config=first_config, what="generate_with_tools",
            )

            tool_results: List[Dict[str, Any]] = []
            tool_errors: List[Dict[str, Any]] = []   # {name, error} per call that failed
            candidate = (response.candidates or [None])[0]

            # Collect EVERY function_call in the model's turn — gemini-2.5 can emit several in
            # parallel. Handling only the first (while echoing candidate.content, which carries ALL
            # the calls) sent back a function_response count that mismatched the call count → the API
            # 400s and the whole tool round is lost. Mirror stream_agentic: run each call and append
            # ONE function_response per call (an error response for an unknown handler) so counts match.
            def _calls_in(resp: Any) -> List[Any]:
                cand = (resp.candidates or [None])[0]
                ps = (cand.content.parts if cand and cand.content else None) or []
                return [
                    p.function_call for p in ps
                    if getattr(p, "function_call", None) and p.function_call.name
                ]

            async def _run_calls(calls: List[Any], *, final_round: bool = False) -> List[Any]:
                """Run every call; ONE function_response per call, in the model's call order.

                The round's unique calls run CONCURRENTLY (`_gather_tool_calls`), so the round
                costs its slowest tool inside the send door's 50 s budget; an identical call
                (same name, same canonical args) in the same round runs once and shares the
                result. `tool_results` / `tool_errors` keep one entry per CALL, as before. A call
                past `_TOOL_ROUND_MAX_JOBS` is refused with its own error response (no `upstream`
                flag, so it never counts toward the refund)."""
                named, slots, jobs = _plan_tool_round(calls, tool_handlers)
                # Only the jobs that RUN a handler; nothing on the last executed round (a search
                # deferred there could never run) — see `_runner_names`.
                _notify_tool_round(on_tool_round, [] if final_round else _runner_names(jobs))
                for name, handler, args, _key in jobs:
                    if handler is not None:
                        logger.info("Gemini invoked tool '%s' with args: %s", name,
                                    _loggable_tool_args(name, args))
                job_results = await _gather_tool_calls(
                    jobs, door="send", calls=len(calls),
                    refused=sum(1 for s in slots if s[0] == "refused"),
                    deadline=tool_deadline,
                )
                response_parts: List[Any] = []
                for (name, _args), slot in zip(named, slots):
                    if slot[0] == "refused":
                        handler, handler_result = None, slot[1]
                    else:
                        _kind, job_index, is_first = slot
                        handler = jobs[job_index][1]
                        handler_result = job_results[job_index]
                        if not is_first:
                            logger.info("Gemini repeated tool '%s' with identical args in one "
                                        "round — sharing the round's single run", name)
                    if isinstance(handler_result, dict) and handler_result.get("error"):
                        tool_errors.append({"name": name, "error": handler_result["error"],
                                            "upstream": bool(handler_result.get("upstream"))})
                    elif handler is not None:
                        tool_results.append(handler_result)
                    response_parts.append(types.Part.from_function_response(
                        name=name,
                        response={"result": truncate_tool_result(handler_result)},
                    ))
                return response_parts

            fn_calls = _calls_in(response)
            if fn_calls:
                response_parts = await _run_calls(fn_calls)

                # Feed the results back. Append the model's turn VERBATIM (candidate.content) so any
                # thought_signature is preserved, then ONE user turn with a response per call.
                history = [
                    types.Content(role="user", parts=[types.Part(text=prompt)]),
                    candidate.content,
                    types.Content(role="user", parts=response_parts),
                ]
                follow_up = await self._generate_content_retried(
                    model=model, contents=history, config=config,
                    what="generate_with_tools tool follow-up",
                )
                _log_gemini_usage(
                    _response_usage(response), call_site="generate_with_tools", model=model,
                    finish=_response_finish(response),
                )
                _log_gemini_usage(
                    _response_usage(follow_up), call_site="generate_with_tools:follow_up", model=model,
                    finish=_response_finish(follow_up),
                )
                text = _response_text(follow_up)
                extra_ran = False
                follow_calls = _calls_in(follow_up) if _has_function_call(follow_up) else []
                extra_earned = forced or (
                    bool(extra_round_tools)
                    and any(c.name in extra_round_tools for c in follow_calls)
                )
                if tool_deadline is not None:
                    # The time LEFT decides: the follow-up's slowest call must still fit before the
                    # answer reserve (a capped wait would mostly time out — and the budget never
                    # overruns either way, since every wait is capped at the deadline).
                    _need = max((_tool_ceiling(c.name) for c in follow_calls), default=0.0)
                    extra_fits = tool_deadline - time.monotonic() >= _need
                else:
                    extra_fits = time.monotonic() - started < _FORCED_EXTRA_ROUND_MAX_ELAPSED_SECONDS
                if extra_earned and follow_calls and extra_fits:
                    # The FORCED first request could call only the forced tool, so this door's one
                    # executed round would otherwise be spent on it: the follow-up's own calls
                    # (the headlines, a chart) run once more here, as stream_agentic's round 2
                    # does — or a web turn could never use another tool on this door, and a search
                    # outage would settle `no_tools` here but charged on the stream door. Run even
                    # when the follow-up ALSO carries text: that text is a preamble ("Let me also
                    # pull the headlines"), and returning it alone delivered one sentence as a
                    # charged answer. Skipped late in the send budget: the tool-less round below
                    # still answers from what is in hand. An UNFORCED follow-up earns the same
                    # round when it calls a tool in `extra_round_tools` (the automatic web search
                    # after Caydex's tools could not answer) — every one of its calls runs, through
                    # the same concurrent round runner.
                    extra_ran = True
                    more_parts = await _run_calls(follow_calls, final_round=True)
                    history = history + [
                        follow_up.candidates[0].content,
                        types.Content(role="user", parts=more_parts),
                    ]
                    follow_up = await self._generate_content_retried(
                        model=model, contents=history, config=config,
                        what="generate_with_tools forced-turn follow-up",
                    )
                    _log_gemini_usage(
                        _response_usage(follow_up), call_site="generate_with_tools:follow_up_2",
                        model=model, finish=_response_finish(follow_up),
                    )
                    text = _response_text(follow_up)
                if _has_function_call(follow_up) and (not text or forced or extra_ran):
                    # (A forced turn's follow-up that STILL calls tools here — after its extra
                    # round, or with the round skipped late — answers tool-less too, never with a
                    # bare preamble.)
                    # This door is single-round, but the follow-up was made with the SAME
                    # config that still declares the tools, so the model may answer with a
                    # SECOND function call (chart first, then news for the ticker it found).
                    # `_response_text` skips function-call parts → "" → the endpoint answered
                    # GEMINI_UNAVAILABLE and refunded a turn that had real tool data in hand.
                    # Mirror stream_agentic's final round: ask once more with NO tools.
                    final = await self._generate_content_retried(
                        model=model,
                        contents=history + [follow_up.candidates[0].content, types.Content(
                            role="user", parts=[types.Part(text=(
                                "Answer the user's question now using the tool results you already "
                                "have. Do not call any more tools."
                            ))],
                        )],
                        config=self._config(
                            system_instruction=system_instruction, tools=None,
                            max_output_tokens=max_output_tokens,
                            thinking_config=_thinking_config(thinking_budget),
                        ),
                        what="generate_with_tools final answer",
                    )
                    _log_gemini_usage(
                        _response_usage(final), call_site="generate_with_tools:final", model=model,
                        finish=_response_finish(final),
                    )
                    follow_up = final
                    text = _response_text(follow_up)
                return {
                    "text": text,
                    "model": self.model_name,
                    "tokens_used": _response_tokens(follow_up),
                    "finish_reason": _response_finish(follow_up),
                    "tool_results": tool_results,
                    "tool_errors": tool_errors,
                }

            # No function call — return normal text response.
            _log_gemini_usage(
                _response_usage(response), call_site="generate_with_tools", model=model,
                finish=_response_finish(response),
            )
            return {
                "text": _response_text(response),
                "model": self.model_name,
                "tokens_used": _response_tokens(response),
                "finish_reason": _response_finish(response),
                "tool_results": tool_results,
                "tool_errors": tool_errors,
            }

        except Exception as e:
            # Was an UNCONDITIONAL ERROR — the only handler in this file that never
            # consulted the classifier, so a plain 429 / "high demand" / per-call
            # timeout paged Sentry as if it were a code bug. Mirrors generate_text /
            # generate_json / generate_embedding now. Tool handlers run inside
            # `_run_tool_handler`, which converts their failures to an error RESULT —
            # so nothing an FMP tool raises can reach this classifier any more.
            if not is_transient_gemini_error(e):
                logger.error(f"Gemini tool-calling generation failed: {e}", exc_info=True)
            raise

    def create_tool_chat(
        self,
        system_instruction: Optional[str],
        tools: List[Any],
        temperature: float = 0.7,
        max_output_tokens: int = 8192,
        thinking_budget: Optional[int] = None,
    ):
        """Create a stateful async chat session bound to function-calling tools
        (for the agentic research loop). Returns a google-genai AsyncChat; drive
        it with ``await _call_with_timeout(chat.send_message(...))``. The chats
        module auto-preserves the model's turns (incl. thought_signature) across
        rounds, so the caller only feeds tool responses back.

        `thinking_budget` goes through the same `_thinking_config` encoder as the
        three generation helpers: None attaches nothing (byte-identical to the
        pre-cap request), 0 disables thinking, a positive value is a ceiling. This
        path had NO budget knob at all, so the deep-research loop — up to five
        calls per report — thought at the model default while the rest of the
        report path was capped (SYSTEM_DESIGN_GUIDELINES 9b.7)."""
        kwargs: Dict[str, Any] = {
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
            "system_instruction": system_instruction or None,
            "tools": list(tools),
        }
        thinking = _thinking_config(thinking_budget)
        if thinking is not None:
            kwargs["thinking_config"] = thinking
        return self._client.aio.chats.create(
            model=self.model_name,
            config=types.GenerateContentConfig(**kwargs),
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
        thinking_budget: Optional[int] = None,
        force_first_tool: Any = None,
        on_tool_round: Optional[Callable[[Tuple[str, ...]], Any]] = None,
    ):
        """Stream a MULTI-ROUND agentic answer: the model can call function-calling tools
        mid-stream (manual FC), while reasoning + answer stream throughout.

        `force_first_tool`: round 1 MUST call that tool, or one of those tools (a name or a
        sequence, `_forced_tool_config`); later rounds run on the ordinary config, so the model
        may add other tools — the web search after a news ask's licensed headlines — and then
        answer.

        `on_tool_round`: optional observer called with each round's names of the jobs that RUN
        a handler (`_runner_names` — no memo replay, duplicate, refused or unknown call; an empty
        tuple on the last round that runs tools) before any of its handlers runs
        (`_notify_tool_round`; never raises).

        Yields tagged events:
          * ("thought", str) — a reasoning summary chunk (→ the thinking card)
          * ("answer", str)  — an answer text chunk (→ the message bubble)
          * ("tool_start", {"name"}) — BEFORE a (non-replayed) handler runs, so the caller can
            show live progress ("Searching the web…") during a slow tool. A round's calls run
            CONCURRENTLY (at most `_TOOL_ROUND_MAX_CONCURRENCY` at once), so every `tool_start`
            of the round comes first, before any of its handlers start. A caller that does not
            care must simply ignore the kind.
          * ("tool", {"name","args","result"}) — AFTER the round's tools ran, one per call in the
            model's call order (→ tool_step + widget extraction); a replay of this turn's earlier
            result, or an identical call later in the same round, carries `"memoized": True`; a
            call past `_TOOL_ROUND_MAX_JOBS` carries its own `too_many_tool_calls` error result
          * ("finish", str)  — LAST, only when the answer was CUT (MAX_TOKENS / SAFETY /
            RECITATION) after real answer text streamed; the caller must not settle
            that turn as complete.

        `thinking_budget` caps the per-round reasoning pass (`_stream_thinking_config`).
        `max_output_tokens` bounds thoughts + answer together, so an unbounded pass can
        leave the visible answer no room: the prod turn behind the TestFlight "cut off"
        report spent 1150 of a 1200 ceiling thinking and streamed 40 answer tokens.

        client.aio.chats auto-preserves the model's turns (incl. thought signatures) across rounds;
        we only feed tool responses back. Bounded by max_rounds, with a final answer round if the
        model is still calling tools at the cap (so the user always gets a reply). Honors the quota
        circuit breaker manually (a partial stream can't be safely @async_retry'd)."""
        if _quota_circuit.is_open():
            raise GeminiQuotaError("Gemini quota circuit open (resource_exhausted) — failing fast")
        # See stream_text: the trial's verdict is the FIRST chunk, and a no-verdict exit
        # releases the half-open slot (F04-3).
        is_trial = _quota_circuit.half_open
        trial_stamp = _quota_circuit.trial_stamp if is_trial else 0.0
        verdict = False
        config = self._config(
            system_instruction=system_instruction,
            tools=tools,
            max_output_tokens=max_output_tokens,
            thinking_config=_stream_thinking_config(thinking_budget),
        )
        # Manual function calling — we run handlers ourselves (AFC-while-streaming is buggy upstream).
        config.automatic_function_calling = types.AutomaticFunctionCallingConfig(disable=True)
        resolved_model = model_name or self.model_name
        chat = self._client.aio.chats.create(model=resolved_model, config=config)
        # A per-message config REPLACES the chat's for that request (google-genai), hence a full copy.
        forced_config = _forced_tool_config(config, force_first_tool, tool_handlers)

        message: Any = prompt
        usage = _StreamUsage()
        # Whether any ANSWER text (not thought) has streamed, and the last finish reason
        # seen — a non-STOP finish after answer text is a CUT the caller must not settle
        # as complete (yielded as a `("finish", reason)` event).
        answered = False
        finish: Optional[str] = None
        # Per-turn memo of SUCCESSFUL tool results keyed on (name, canonical args). The
        # model re-issues `get_stock_chart_data("AAPL")` in a later round often enough to
        # matter (it cannot see that the first result was rendered as a card): the repeat
        # used to run the handler again — another quote fetch, another 75 s ceiling — and
        # the second identical card was deduped away. A repeat now replays the stored
        # result: still one tool event and one function_response per CALL, so the
        # call/response invariant holds, and only results without an `error` are kept so
        # a transient round-1 failure is not frozen for the turn.
        memo: Dict[Tuple[str, str], Any] = {}
        try:
            for _round in range(max_rounds):
                fcalls: List[Any] = []
                finish = None
                if _round == 0 and forced_config is not None:
                    stream = await chat.send_message_stream(message, config=forced_config)
                else:
                    stream = await chat.send_message_stream(message)
                async for chunk in stream:
                    usage.observe(chunk)
                    if not verdict:
                        verdict = True
                        _quota_circuit.record_success()
                    finish = _response_finish(chunk) or finish
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
                            is_thought = bool(getattr(part, "thought", False))
                            answered = answered or not is_thought
                            yield ("thought" if is_thought else "answer"), text
                # Round boundary: per-chunk counts are cumulative WITHIN a round but
                # additive ACROSS rounds, so fold before the next send_message_stream.
                usage.commit_round()
                if not fcalls:
                    if answered and not _is_clean_finish(finish):
                        yield "finish", str(finish)
                    verdict = True
                    _quota_circuit.record_success()
                    return
                # Run the requested tools CONCURRENTLY, then emit a "tool" event per call and
                # feed one response per call back next round — both in the model's call order.
                # Every `tool_start` is announced BEFORE any handler of the round runs; a memo
                # replay or an identical call later in the same round announces nothing (it runs
                # no handler) and is marked `memoized`. A call past `_TOOL_ROUND_MAX_JOBS` runs
                # nothing either: no `tool_start`, its own `too_many_tool_calls` result, never
                # memoised (the model may ask again next round).
                named, slots, jobs = _plan_tool_round(fcalls, tool_handlers, memo)
                # Only the jobs that RUN a handler (`_runner_names`); nothing on the last round
                # that runs tools — the final round ignores tool calls, so a search deferred there
                # could never run.
                _notify_tool_round(on_tool_round,
                                   [] if _round == max_rounds - 1 else _runner_names(jobs))
                for name, handler, args, _key in jobs:
                    if handler is not None:
                        logger.info("Gemini invoked tool '%s' with args: %s", name,
                                    _loggable_tool_args(name, args))
                        yield "tool_start", {"name": name}
                job_results = await _gather_tool_calls(
                    jobs, door="stream", calls=len(fcalls),
                    refused=sum(1 for s in slots if s[0] == "refused"),
                )
                for (_name, _handler, _args, key), result in zip(jobs, job_results):
                    # Only SUCCESSFUL results are memoised: a transient failure is not frozen for
                    # the turn, and a later round's retry runs again — and neither is a result
                    # marked `deferred` (`_memoisable`).
                    if key is not None and _memoisable(result):
                        memo[key] = result
                response_parts: List[Any] = []
                for (name, args), slot in zip(named, slots):
                    if slot[0] == "memo":
                        result = slot[1]
                        logger.info("Gemini re-issued tool '%s' with identical args — "
                                    "replaying this turn's result", name)
                        yield "tool", {"name": name, "args": args, "result": result,
                                       "memoized": True}
                    elif slot[0] == "refused":
                        result = slot[1]
                        yield "tool", {"name": name, "args": args, "result": result}
                    else:
                        _kind, job_index, is_first = slot
                        result = job_results[job_index]
                        if is_first:
                            yield "tool", {"name": name, "args": args, "result": result}
                        else:
                            logger.info("Gemini repeated tool '%s' with identical args in one "
                                        "round — sharing the round's single run", name)
                            yield "tool", {"name": name, "args": args, "result": result,
                                           "memoized": True}
                    response_parts.append(types.Part.from_function_response(
                        name=name,
                        response={"result": json.dumps(truncate_tool_result(result), default=str)},
                    ))
                message = response_parts

            # max_rounds exhausted while still calling tools — one final answer round (tools ignored)
            # so the user always gets a reply.
            final_stream = await chat.send_message_stream(message)
            finish = None
            async for chunk in final_stream:
                usage.observe(chunk)
                if not verdict:
                    verdict = True
                    _quota_circuit.record_success()
                finish = _response_finish(chunk) or finish
                for part in _iter_parts(chunk):
                    if getattr(part, "function_call", None):
                        continue
                    try:
                        text = part.text or ""
                    except (ValueError, AttributeError):
                        text = ""
                    if text:
                        is_thought = bool(getattr(part, "thought", False))
                        answered = answered or not is_thought
                        yield ("thought" if is_thought else "answer"), text
            if answered and not _is_clean_finish(finish):
                yield "finish", str(finish)
            verdict = True
            _quota_circuit.record_success()
        except Exception as e:
            if _is_quota_error(e):
                verdict = True
                _quota_circuit.record_quota_error()
            raise
        finally:
            if is_trial and not verdict:
                _quota_circuit.release_trial(trial_stamp)
            # See stream_text: the early `return` above, a client disconnect, and an
            # exception all land here, and all three spent tokens.
            _log_gemini_usage(
                usage.totals(), call_site="stream_agentic", model=resolved_model, tag=usage_tag,
                finish=finish,
            )


# Global client instance
_gemini_client: Optional[GeminiClient] = None


def get_gemini_client() -> GeminiClient:
    """Get or create the global Gemini client instance."""
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = GeminiClient()
    return _gemini_client
