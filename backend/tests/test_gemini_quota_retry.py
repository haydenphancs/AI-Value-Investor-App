"""
Deterministic tests for the rewritten ``async_retry`` decorator + quota
classification in ``app/integrations/gemini.py``.

The decorator runs TWO independent retry budgets in one ``while True`` loop:

  * Quota / rate-limit (429) errors → up to ``GEMINI_QUOTA_MAX_RETRIES`` extra
    tries, gated by the shared process-wide ``_quota_circuit`` breaker.
  * Generic (non-quota) errors → up to ``max_attempts`` tries.

These tests pin the real behavior and prove the loop ALWAYS terminates (the
whole point of the rewrite was to stop hammering an exhausted quota / looping
forever). They also prove the breaker-open ``GeminiQuotaError`` routes to the
GEMINI_QUOTA_EXCEEDED contract so the caller's sentinel fallback fires.

All backoff sleeps are monkeypatched to async no-ops → no wall-clock waits.
The module-level circuit breaker is a singleton; every test resets it first.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.integrations import gemini
from app.integrations.gemini import (
    GeminiQuotaError,
    async_retry,
    _is_quota_error,
)
from app.api.error_response import classify_exception, ErrorCode


# ── shared helpers ─────────────────────────────────────────────────────────


def _reset_circuit() -> None:
    """Reset the module-level breaker singleton so tests don't bleed state."""
    gemini._quota_circuit._consecutive = 0
    gemini._quota_circuit._opened_at = 0.0


def _no_sleep(monkeypatch) -> None:
    """Replace asyncio.sleep with an async no-op so backoff doesn't wait."""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())


def _set_quota_settings(
    monkeypatch,
    *,
    max_retries: int,
    circuit_threshold: int,
    retry_delay: float = 0.0,
    cooldown: float = 30.0,
) -> None:
    """Pin the gemini.settings knobs the decorator reads, with a fresh circuit."""
    monkeypatch.setattr(gemini.settings, "GEMINI_QUOTA_MAX_RETRIES", max_retries)
    monkeypatch.setattr(
        gemini.settings, "GEMINI_QUOTA_RETRY_DELAY_SECONDS", retry_delay
    )
    monkeypatch.setattr(
        gemini.settings, "GEMINI_QUOTA_CIRCUIT_THRESHOLD", circuit_threshold
    )
    monkeypatch.setattr(
        gemini.settings, "GEMINI_QUOTA_CIRCUIT_COOLDOWN_SECONDS", cooldown
    )


# ── 1. quota error twice then success → returns success, budget respected ──


@pytest.mark.asyncio
async def test_quota_error_twice_then_success(monkeypatch):
    _reset_circuit()
    _no_sleep(monkeypatch)
    # max_retries=2 → tolerates 2 quota retries; keep the breaker well clear of
    # opening (threshold high) so this test isolates the RETRY budget.
    _set_quota_settings(monkeypatch, max_retries=2, circuit_threshold=100)

    calls = {"n": 0}

    @async_retry(max_attempts=5, delay=1.0)
    async def flaky():
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")
        return "ok"

    result = await flaky()

    assert result == "ok"
    # GEMINI_QUOTA_MAX_RETRIES (=2) retries + the original try = 3 total calls.
    assert calls["n"] == 3
    assert calls["n"] == gemini.settings.GEMINI_QUOTA_MAX_RETRIES + 1
    # A success resets the breaker.
    assert gemini._quota_circuit._consecutive == 0


# ── 2. always-quota error → re-raises after the budget (no infinite loop) ──


@pytest.mark.asyncio
async def test_quota_error_always_reraises_bounded(monkeypatch):
    _reset_circuit()
    _no_sleep(monkeypatch)
    # Threshold high so the breaker doesn't short-circuit before the retry
    # budget is spent — we want to prove the RETRY budget itself terminates.
    _set_quota_settings(monkeypatch, max_retries=2, circuit_threshold=100)

    calls = {"n": 0}

    @async_retry(max_attempts=5, delay=1.0)
    async def always_quota():
        calls["n"] += 1
        raise RuntimeError("Error 429: quota exhausted, resource_exhausted")

    with pytest.raises(RuntimeError, match="429"):
        await always_quota()

    # Original try + GEMINI_QUOTA_MAX_RETRIES retries, then give up. The guard is
    # `quota_attempt > GEMINI_QUOTA_MAX_RETRIES`, so attempts 1 & 2 retry and the
    # 3rd raises → exactly max_retries + 1 invocations. It does NOT loop forever.
    assert calls["n"] == gemini.settings.GEMINI_QUOTA_MAX_RETRIES + 1
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_quota_circuit_breaker_short_circuits_within_budget(monkeypatch):
    """If the breaker opens mid-retry it fails fast — also bounded/terminating."""
    _reset_circuit()
    _no_sleep(monkeypatch)
    # Low threshold (1) → first quota error trips the breaker; the decorator's
    # `or _quota_circuit.is_open()` give-up clause re-raises immediately.
    _set_quota_settings(monkeypatch, max_retries=5, circuit_threshold=1)

    calls = {"n": 0}

    @async_retry(max_attempts=5, delay=1.0)
    async def always_quota():
        calls["n"] += 1
        raise RuntimeError("429 quota")

    with pytest.raises(RuntimeError):
        await always_quota()

    # Breaker opened on the very first quota error → no retries → exactly 1 call.
    assert calls["n"] == 1
    assert gemini._quota_circuit.is_open() is True


# ── 3. generic (non-quota) error → retries to max_attempts then raises ─────


@pytest.mark.asyncio
async def test_generic_error_retries_then_raises(monkeypatch):
    _reset_circuit()
    _no_sleep(monkeypatch)
    _set_quota_settings(monkeypatch, max_retries=2, circuit_threshold=100)

    calls = {"n": 0}

    @async_retry(max_attempts=3, delay=1.0)
    async def always_boom():
        calls["n"] += 1
        raise ValueError("something unrelated to quota broke")

    with pytest.raises(ValueError, match="unrelated"):
        await always_boom()

    # Generic budget: `attempt >= max_attempts` raises → exactly max_attempts
    # invocations (3). Bounded, terminates — never an infinite loop.
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_generic_error_then_success_within_budget(monkeypatch):
    _reset_circuit()
    _no_sleep(monkeypatch)
    _set_quota_settings(monkeypatch, max_retries=2, circuit_threshold=100)

    calls = {"n": 0}

    @async_retry(max_attempts=3, delay=1.0)
    async def boom_once():
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("transient non-quota glitch")
        return "recovered"

    result = await boom_once()
    assert result == "recovered"
    assert calls["n"] == 2


# ── 4. a successful call resets the circuit (record_success → _consecutive 0) ─


@pytest.mark.asyncio
async def test_success_resets_circuit(monkeypatch):
    _reset_circuit()
    _no_sleep(monkeypatch)
    _set_quota_settings(monkeypatch, max_retries=5, circuit_threshold=100)

    # Simulate prior quota errors having accumulated on the breaker.
    gemini._quota_circuit._consecutive = 7
    assert gemini._quota_circuit._consecutive == 7

    @async_retry(max_attempts=2, delay=1.0)
    async def succeeds():
        return "fresh"

    result = await succeeds()

    assert result == "fresh"
    # record_success() ran → consecutive counter back to 0, breaker not open.
    assert gemini._quota_circuit._consecutive == 0
    assert gemini._quota_circuit._opened_at == 0.0
    assert gemini._quota_circuit.is_open() is False


# ── 5. GeminiQuotaError recognized by _is_quota_error AND classify_exception ─


def test_gemini_quota_error_recognized_by_is_quota_error():
    # The breaker-open exception's message carries quota/resource_exhausted
    # markers on purpose so the retry loop treats it as a quota error.
    exc = GeminiQuotaError(
        "Gemini quota circuit open (resource_exhausted) — failing fast"
    )
    assert _is_quota_error(exc) is True


def test_gemini_quota_error_routes_to_quota_contract():
    # Proves the breaker-open exception maps to the right iOS error contract —
    # so the caller's sentinel fallback fires instead of a generic 500.
    exc = GeminiQuotaError(
        "Gemini quota circuit open (resource_exhausted, quota) — failing fast"
    )
    code, status = classify_exception(exc)
    assert code == ErrorCode.GEMINI_QUOTA_EXCEEDED
    assert isinstance(status, int)


@pytest.mark.asyncio
async def test_breaker_open_raises_quota_error_that_classifies(monkeypatch):
    """End-to-end: an OPEN breaker makes the decorator fail fast with a
    GeminiQuotaError that classifies to GEMINI_QUOTA_EXCEEDED."""
    _reset_circuit()
    _no_sleep(monkeypatch)
    _set_quota_settings(monkeypatch, max_retries=2, circuit_threshold=1)

    # Force the breaker OPEN before the call (deterministic time control).
    fixed_now = 1000.0
    monkeypatch.setattr(gemini.time, "time", lambda: fixed_now)
    gemini._quota_circuit._consecutive = 1
    gemini._quota_circuit._opened_at = fixed_now  # within cooldown → open

    assert gemini._quota_circuit.is_open() is True

    calls = {"n": 0}

    @async_retry(max_attempts=5, delay=1.0)
    async def never_runs():
        calls["n"] += 1
        return "should-not-reach"

    with pytest.raises(GeminiQuotaError) as ei:
        await never_runs()

    # Failed fast: the wrapped body never executed.
    assert calls["n"] == 0
    code, _status = classify_exception(ei.value)
    assert code == ErrorCode.GEMINI_QUOTA_EXCEEDED
