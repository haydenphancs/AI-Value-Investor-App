"""
Deterministic unit tests for `_QuotaCircuitBreaker` in app/integrations/gemini.py.

The breaker stops hammering Gemini during a sustained quota outage:
  - it opens ONLY after `GEMINI_QUOTA_CIRCUIT_THRESHOLD` *consecutive*
    `record_quota_error()` calls,
  - once open it stays open for exactly `GEMINI_QUOTA_CIRCUIT_COOLDOWN_SECONDS`
    measured from the FIRST open — straggler 429s that land after the open
    transition must NOT push the deadline forward (the regression this file
    guards against),
  - after the cooldown elapses `is_open()` half-opens: it returns False AND
    clears `_consecutive` / `_opened_at`,
  - any `record_success()` resets the consecutive counter so intermittent
    successes prevent the breaker from ever opening.

Time is controlled by monkeypatching `app.integrations.gemini.time.time` to a
mutable fake clock; the module-level singleton `_quota_circuit` is reset at the
start of every test. No network, no sleeps, fully deterministic.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.integrations import gemini


class _FakeClock:
    """A mutable monotonic-ish clock standing in for time.time()."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, delta: float) -> None:
        self.now += delta


def _reset_breaker() -> None:
    """Restore the module singleton to its pristine closed state."""
    gemini._quota_circuit._consecutive = 0
    gemini._quota_circuit._opened_at = 0.0


def _install_clock(monkeypatch, start: float = 1000.0) -> _FakeClock:
    """Patch app.integrations.gemini.time.time with a controllable fake clock."""
    clock = _FakeClock(start)
    monkeypatch.setattr(gemini.time, "time", clock)
    return clock


# ── 1. Opens after exactly THRESHOLD consecutive quota errors ──────────────


def test_opens_after_exactly_threshold_consecutive_errors(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_QUOTA_CIRCUIT_THRESHOLD", 3)
    monkeypatch.setattr(settings, "GEMINI_QUOTA_CIRCUIT_COOLDOWN_SECONDS", 30.0)
    _install_clock(monkeypatch)
    _reset_breaker()
    cb = gemini._quota_circuit

    # threshold - 1 errors → still closed
    for _ in range(2):
        cb.record_quota_error()
    assert cb.is_open() is False, "breaker must stay closed below the threshold"

    # the threshold-th error → opens
    cb.record_quota_error()
    assert cb.is_open() is True, "breaker must open AT the threshold"
    assert cb._consecutive == 3


# ── 2. REGRESSION: stragglers must not push the open deadline forward ───────


def test_straggler_errors_do_not_rearm_open_deadline(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_QUOTA_CIRCUIT_THRESHOLD", 3)
    monkeypatch.setattr(settings, "GEMINI_QUOTA_CIRCUIT_COOLDOWN_SECONDS", 30.0)
    clock = _install_clock(monkeypatch, start=1000.0)
    _reset_breaker()
    cb = gemini._quota_circuit

    # Open the breaker at T0 = 1000.0.
    for _ in range(3):
        cb.record_quota_error()
    assert cb.is_open() is True
    t0 = cb._opened_at
    assert t0 == 1000.0, "open time must be stamped at the first open transition"

    # Straggler 429s land AFTER the open transition (the ~15 parallel calls that
    # were already past the is_open() check). They must NOT move the deadline.
    clock.advance(5.0)  # T0 + 5
    cb.record_quota_error()
    clock.advance(5.0)  # T0 + 10
    cb.record_quota_error()
    clock.advance(10.0)  # T0 + 20
    cb.record_quota_error()
    assert cb._opened_at == t0, "stragglers must not re-stamp _opened_at"
    assert cb.is_open() is True, "still within cooldown → still open"

    # Just before the cooldown boundary measured from T0 → still open.
    clock.now = t0 + 29.999
    assert cb.is_open() is True

    # At exactly T0 + cooldown → flips back to False, independent of the
    # stragglers that landed at T0+5/+10/+20.
    clock.now = t0 + settings.GEMINI_QUOTA_CIRCUIT_COOLDOWN_SECONDS
    assert cb.is_open() is False, (
        "deadline must be measured from the FIRST open, not the last straggler"
    )


# ── 3. Half-open reset clears _consecutive and _opened_at ──────────────────


def test_half_open_after_cooldown_resets_state(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_QUOTA_CIRCUIT_THRESHOLD", 3)
    monkeypatch.setattr(settings, "GEMINI_QUOTA_CIRCUIT_COOLDOWN_SECONDS", 30.0)
    clock = _install_clock(monkeypatch, start=1000.0)
    _reset_breaker()
    cb = gemini._quota_circuit

    for _ in range(3):
        cb.record_quota_error()
    assert cb.is_open() is True
    assert cb._consecutive == 3
    assert cb._opened_at == 1000.0

    # Advance past the cooldown → half-open trial allowed.
    clock.advance(settings.GEMINI_QUOTA_CIRCUIT_COOLDOWN_SECONDS)
    assert cb.is_open() is False
    # The half-open transition must clear BOTH counters so the next trial starts
    # from a clean slate.
    assert cb._consecutive == 0
    assert cb._opened_at == 0.0


# ── 4. record_success() resets state — intermittent successes prevent open ──


def test_record_success_resets_consecutive_and_opened_at(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_QUOTA_CIRCUIT_THRESHOLD", 3)
    monkeypatch.setattr(settings, "GEMINI_QUOTA_CIRCUIT_COOLDOWN_SECONDS", 30.0)
    _install_clock(monkeypatch)
    _reset_breaker()
    cb = gemini._quota_circuit

    # Two errors, then a success → counter back to zero.
    cb.record_quota_error()
    cb.record_quota_error()
    assert cb._consecutive == 2
    cb.record_success()
    assert cb._consecutive == 0
    assert cb._opened_at == 0.0
    assert cb.is_open() is False

    # Because the success reset the run, the breaker never opens under a
    # success-interleaved error pattern (error, error, success, error, ...).
    for _ in range(10):
        cb.record_quota_error()
        cb.record_quota_error()
        cb.record_success()
    assert cb.is_open() is False, (
        "interleaved successes must keep consecutive below the threshold"
    )

    # And a success while already open fully closes the breaker.
    cb.record_quota_error()
    cb.record_quota_error()
    cb.record_quota_error()
    assert cb.is_open() is True
    cb.record_success()
    assert cb._consecutive == 0
    assert cb._opened_at == 0.0
    assert cb.is_open() is False
