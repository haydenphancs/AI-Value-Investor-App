"""Per-call Gemini timeouts: typed, transient, WARNING — and attributed correctly.

Guards the production Sentry issue `TimeoutError` / "(No error message)" at
`app.integrations.gemini in _call_with_timeout`.

Root cause: `asyncio.wait_for` raised a BARE `TimeoutError`. In py3.11
`asyncio.TimeoutError is TimeoutError` and `str(TimeoutError()) == ""`, so:

  * `is_transient_gemini_error` — three substring rules over `str(exc)` — matched
    nothing, so the four guarded handlers fired `logger.error(..., exc_info=True)`
    and `LoggingIntegration(event_level=ERROR)` opened an issue per attempt;
  * `classify_exception` saw `type(exc).__module__ == "builtins"`, missed the
    google/genai branch, and fell through to `"timeout" in cls` →
    **FMP_UNAVAILABLE**, telling the user their market-data provider was down when
    the AI engine had stalled.

The fix is a typed `GeminiTimeoutError(TimeoutError)`. This file pins the four
things that make it safe: the inheritance (so no existing handler changes), the
message (so it cannot be misrouted into the quota branch and trip the shared
circuit breaker), the level, and the error attribution.
"""

from __future__ import annotations

import asyncio
import inspect
import logging

import pytest

from app.api.error_response import ErrorCode, classify_exception
from app.config import settings
from app.integrations import gemini
from app.integrations.gemini import (
    GeminiTimeoutError,
    _call_with_timeout,
    _is_overload_error,
    _is_quota_error,
    async_retry,
    is_transient_gemini_error,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _reset(monkeypatch, *, timeout_retries=0, alert_streak=5):
    gemini._quota_circuit._consecutive = 0
    gemini._quota_circuit._opened_at = 0.0
    gemini._timeout_streak._consecutive = 0
    gemini._timeout_streak._alerted = False
    monkeypatch.setattr(settings, "GEMINI_TIMEOUT_MAX_RETRIES", timeout_retries)
    monkeypatch.setattr(settings, "GEMINI_TIMEOUT_ALERT_STREAK", alert_streak)


def _no_sleep(monkeypatch):
    async def _instant(*_a, **_k):
        return None

    monkeypatch.setattr(gemini.asyncio, "sleep", _instant)


async def _hang():
    await asyncio.sleep(10.0)


async def _timed_out(what="unit test call") -> GeminiTimeoutError:
    """Produce a REAL GeminiTimeoutError by actually tripping the timeout."""
    with pytest.raises(GeminiTimeoutError) as excinfo:
        await _call_with_timeout(_hang(), what=what)
    return excinfo.value


# ── the typed error ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_call_with_timeout_raises_the_typed_error(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(settings, "GEMINI_REQUEST_TIMEOUT_SECONDS", 0.02)

    err = await _timed_out("generate_grounded_research")

    assert isinstance(err, GeminiTimeoutError)
    assert str(err), "a bare TimeoutError's empty str() is what caused the Sentry issue"
    assert "generate_grounded_research" in str(err)


@pytest.mark.asyncio
async def test_it_is_still_a_timeout_error(monkeypatch):
    """THE inheritance guard.

    `home_dashboard_service`, `chat_context_resolver` and `live_price` all
    `except asyncio.TimeoutError`. Subclassing TimeoutError is what guarantees this
    change cannot alter what any of them catch — dropping to `Exception` would
    silently break three degradation paths.
    """
    _reset(monkeypatch)
    monkeypatch.setattr(settings, "GEMINI_REQUEST_TIMEOUT_SECONDS", 0.02)

    err = await _timed_out()

    assert isinstance(err, TimeoutError)
    assert isinstance(err, asyncio.TimeoutError)  # same object in py3.11
    assert isinstance(err.__cause__, asyncio.TimeoutError)  # original preserved


@pytest.mark.asyncio
async def test_timeout_message_cannot_be_misrouted(monkeypatch):
    """The message must not contain a word that steers it into another branch.

    `_is_quota_error`/`_is_overload_error` substring-match `str(exc)`. A quota word
    here would not just pick the wrong branch — it would feed `_quota_circuit`,
    which after GEMINI_QUOTA_CIRCUIT_THRESHOLD hits fails EVERY other Gemini call in
    the process fast. One slow read must never be able to do that.
    """
    _reset(monkeypatch)
    monkeypatch.setattr(settings, "GEMINI_REQUEST_TIMEOUT_SECONDS", 0.02)

    err = await _timed_out()
    text = str(err).lower()

    for forbidden in (
        "429", "quota", "rate limit", "resource_exhausted",
        "unavailable", "503", "try again later", "high demand",
    ):
        assert forbidden not in text, f"{forbidden!r} would misroute the retry branch"
    assert _is_quota_error(err) is False
    assert _is_overload_error(err) is False


@pytest.mark.asyncio
async def test_timeout_is_classified_transient(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(settings, "GEMINI_REQUEST_TIMEOUT_SECONDS", 0.02)

    assert is_transient_gemini_error(await _timed_out()) is True


def test_a_bare_timeout_error_is_still_not_transient():
    """Negative control on the isinstance arm.

    Only OUR timeout is known to be an upstream-capacity condition; a bare
    TimeoutError from somewhere else carries no such provenance.
    """
    assert is_transient_gemini_error(TimeoutError()) is False


# ── retry budget + log level ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_timeout_is_not_retried_and_logs_warning_not_error(monkeypatch, caplog):
    """The core of the fix, mirroring test_gemini_quota_retry's overload analogue.

    Default budget is 0, so exactly one attempt — and no ERROR record, which is what
    stops the Sentry issue (LoggingIntegration fires at ERROR).
    """
    _reset(monkeypatch, timeout_retries=0)
    _no_sleep(monkeypatch)
    calls = {"n": 0}

    @async_retry(max_attempts=5, delay=1.0)
    async def always_timeout():
        calls["n"] += 1
        raise GeminiTimeoutError("call exceeded its 90s per-request ceiling")

    with caplog.at_level(logging.DEBUG, logger=gemini.logger.name):
        with pytest.raises(GeminiTimeoutError):
            await always_timeout()

    assert calls["n"] == 1, "a 90s stall is a stuck connection, not a blip"
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert [r for r in caplog.records if r.levelno == logging.WARNING]


@pytest.mark.asyncio
async def test_the_timeout_budget_is_honoured_when_raised(monkeypatch):
    """Own budget, not the generic one — proves the branch is really separate."""
    _reset(monkeypatch, timeout_retries=2)
    _no_sleep(monkeypatch)
    calls = {"n": 0}

    @async_retry(max_attempts=5, delay=1.0)
    async def always_timeout():
        calls["n"] += 1
        raise GeminiTimeoutError("call exceeded its 90s per-request ceiling")

    with pytest.raises(GeminiTimeoutError):
        await always_timeout()

    assert calls["n"] == 3  # 2 retries + the original


@pytest.mark.asyncio
async def test_timeout_then_success_recovers_when_retries_are_enabled(monkeypatch):
    _reset(monkeypatch, timeout_retries=2)
    _no_sleep(monkeypatch)
    calls = {"n": 0}

    @async_retry(max_attempts=5, delay=1.0)
    async def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise GeminiTimeoutError("call exceeded its 90s per-request ceiling")
        return "ok"

    assert await flaky() == "ok"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_timeout_does_not_open_the_quota_circuit(monkeypatch):
    """A stall must not be able to fail-fast every other Gemini call in the process."""
    _reset(monkeypatch, timeout_retries=0)
    _no_sleep(monkeypatch)
    monkeypatch.setattr(settings, "GEMINI_QUOTA_CIRCUIT_THRESHOLD", 2)

    @async_retry(max_attempts=5, delay=1.0)
    async def always_timeout():
        raise GeminiTimeoutError("call exceeded its 90s per-request ceiling")

    for _ in range(5):
        with pytest.raises(GeminiTimeoutError):
            await always_timeout()

    assert gemini._quota_circuit.is_open() is False
    assert gemini._quota_circuit._consecutive == 0


# ── the anti-blindness escalation ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sustained_timeouts_escalate_to_exactly_one_error_then_reset(monkeypatch, caplog):
    """WARNING-per-call must not make a real outage invisible.

    Escalates on the STREAK: one ERROR per outage, latched so ~15 parallel narrative
    jobs cannot each file a duplicate, and cleared by any success.
    """
    _reset(monkeypatch, alert_streak=3)
    monkeypatch.setattr(settings, "GEMINI_REQUEST_TIMEOUT_SECONDS", 0.01)

    with caplog.at_level(logging.DEBUG, logger=gemini.logger.name):
        for _ in range(5):
            await _timed_out()

        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(errors) == 1, "latched: one ERROR per outage, not per call"
        assert "sustained" in errors[0].getMessage()

        # A success clears the streak...
        monkeypatch.setattr(settings, "GEMINI_REQUEST_TIMEOUT_SECONDS", 5.0)

        async def _fast():
            return "ok"

        assert await _call_with_timeout(_fast(), what="probe") == "ok"
        assert gemini._timeout_streak._consecutive == 0

        # ...so a NEW outage can escalate again.
        monkeypatch.setattr(settings, "GEMINI_REQUEST_TIMEOUT_SECONDS", 0.01)
        for _ in range(3):
            await _timed_out()

    assert len([r for r in caplog.records if r.levelno >= logging.ERROR]) == 2


@pytest.mark.asyncio
async def test_a_single_timeout_does_not_escalate(monkeypatch, caplog):
    """Negative control: below the streak, nothing pages."""
    _reset(monkeypatch, alert_streak=5)
    monkeypatch.setattr(settings, "GEMINI_REQUEST_TIMEOUT_SECONDS", 0.01)

    with caplog.at_level(logging.DEBUG, logger=gemini.logger.name):
        await _timed_out()

    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


# ── error attribution ────────────────────────────────────────────────────────

def test_classify_exception_maps_the_timeout_to_gemini_not_fmp():
    code, status = classify_exception(
        GeminiTimeoutError("call exceeded its 90s per-request ceiling")
    )
    assert code is ErrorCode.GEMINI_UNAVAILABLE
    assert status == 502


def test_a_bare_timeout_error_still_maps_to_fmp_unavailable():
    """Regression control: the new guard must not disturb the existing rule.

    `classify_exception`'s `"timeout" in cls` arm at the bottom of the chain is
    load-bearing for genuine FMP read timeouts.
    """
    code, _ = classify_exception(TimeoutError())
    assert code is ErrorCode.FMP_UNAVAILABLE


def test_the_timeout_guard_precedes_the_generic_heuristic():
    """Non-vacuity for the PLACEMENT, not just the mapping.

    If the block were inserted after the generic heuristics, `"timeout" in cls`
    would win first and silently answer FMP_UNAVAILABLE again. `GeminiTimeoutError`
    contains "timeout" in its lowercased class name, so this asserts ordering.
    """
    assert "timeout" in type(GeminiTimeoutError("x")).__name__.lower()
    code, _ = classify_exception(GeminiTimeoutError("x"))
    assert code is ErrorCode.GEMINI_UNAVAILABLE


# ── generate_with_tools: the previously-unguarded handler ────────────────────

@pytest.mark.asyncio
async def test_generate_with_tools_does_not_log_error_for_a_transient(monkeypatch, caplog):
    """It was the ONLY handler that logged ERROR unconditionally.

    So a plain 429 / "high demand" / timeout paged Sentry from this path even after
    the other four were guarded.
    """
    _reset(monkeypatch)
    client = gemini.GeminiClient()

    async def _boom(*_a, **_k):
        raise GeminiTimeoutError("call exceeded its 90s per-request ceiling")

    monkeypatch.setattr(client._client.aio.models, "generate_content", _boom)

    with caplog.at_level(logging.DEBUG, logger=gemini.logger.name):
        with pytest.raises(GeminiTimeoutError):
            await client.generate_with_tools("prompt", tools=[], tool_handlers={})

    tool_errors = [
        r for r in caplog.records
        if "tool-calling generation failed" in r.getMessage()
    ]
    assert tool_errors == []


@pytest.mark.asyncio
async def test_generate_with_tools_still_logs_error_for_a_real_bug(monkeypatch, caplog):
    """Negative control — the demotion must stay scoped to transients."""
    _reset(monkeypatch)
    client = gemini.GeminiClient()

    async def _boom(*_a, **_k):
        raise KeyError("candidates")

    monkeypatch.setattr(client._client.aio.models, "generate_content", _boom)

    with caplog.at_level(logging.DEBUG, logger=gemini.logger.name):
        with pytest.raises(KeyError):
            await client.generate_with_tools("prompt", tools=[], tool_handlers={})

    tool_errors = [
        r for r in caplog.records
        if "tool-calling generation failed" in r.getMessage()
    ]
    assert tool_errors and tool_errors[0].levelno == logging.ERROR


# ── the stale docstrings ─────────────────────────────────────────────────────

def test_docstrings_no_longer_claim_async_retry_skips_the_timeout():
    """Both `gemini._call_with_timeout` and the config comment asserted the
    decorator "skips it (not a quota error)". It did not — the generic branch
    retried it, doubling worst-case latency to ~182s per call. The code now matches
    the claim (budget 0), and the docs must say what actually happens.
    """
    doc = inspect.getdoc(_call_with_timeout) or ""
    assert "skips it" not in doc
    assert "GeminiTimeoutError" in doc
    assert "GEMINI_TIMEOUT_MAX_RETRIES" in doc

    config_src = inspect.getsource(type(settings))
    assert "the @async_retry decorator skips it" not in config_src
