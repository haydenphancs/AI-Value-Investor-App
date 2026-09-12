"""
Deterministic unit tests for `_QuotaCircuitBreaker` in app/integrations/gemini.py.

The breaker stops hammering Gemini during a sustained quota outage:
  - it opens ONLY after `GEMINI_QUOTA_CIRCUIT_THRESHOLD` *consecutive*
    `record_quota_error()` calls,
  - once open it stays open for exactly `GEMINI_QUOTA_CIRCUIT_COOLDOWN_SECONDS`
    measured from the FIRST open — straggler 429s that land after the open
    transition must NOT push the deadline forward (the regression this file
    guards against),
  - after the cooldown elapses it HALF-OPENS: `is_open()` returns False exactly
    once (one trial call is admitted) and True for everyone else until that
    trial reports; a success closes the breaker fully, a quota error during
    the trial re-opens it immediately with the deadline measured from NOW,
  - a trial that never reports (a non-quota failure, a disconnect) expires after
    another cooldown so a lost trial cannot wedge the breaker open,
  - any `record_success()` resets the consecutive counter so intermittent
    successes prevent the breaker from ever opening.

The previous implementation cleared ALL state at the cooldown boundary — every
parallel caller was readmitted at once, and under a sustained 429 the process
cycled "30 s off / ~20 errors on". Its docstring promised a half-open trial the
code did not perform; this file now pins the real one.

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
    gemini._quota_circuit.reset()


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


# ── 3. Half-open: ONE trial, everyone else keeps failing fast ─────────────


def test_half_open_admits_exactly_one_trial(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_QUOTA_CIRCUIT_THRESHOLD", 3)
    monkeypatch.setattr(settings, "GEMINI_QUOTA_CIRCUIT_COOLDOWN_SECONDS", 30.0)
    clock = _install_clock(monkeypatch, start=1000.0)
    _reset_breaker()
    cb = gemini._quota_circuit

    for _ in range(3):
        cb.record_quota_error()
    assert cb.is_open() is True

    clock.advance(settings.GEMINI_QUOTA_CIRCUIT_COOLDOWN_SECONDS)
    # The first caller after the cooldown is the trial.
    assert cb.is_open() is False
    assert cb.half_open is True
    # The ~15 parallel Stage-B jobs behind it are NOT readmitted on the trial's coat-tails.
    assert cb.is_open() is True
    assert cb.is_open() is True
    # State is NOT cleared by the trial itself — the breaker is still open for cause.
    assert cb._consecutive == 3
    assert cb._opened_at == 1000.0


def test_a_successful_trial_closes_the_breaker(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_QUOTA_CIRCUIT_THRESHOLD", 3)
    monkeypatch.setattr(settings, "GEMINI_QUOTA_CIRCUIT_COOLDOWN_SECONDS", 30.0)
    clock = _install_clock(monkeypatch, start=1000.0)
    _reset_breaker()
    cb = gemini._quota_circuit

    for _ in range(3):
        cb.record_quota_error()
    clock.advance(30.0)
    assert cb.is_open() is False  # trial admitted
    cb.record_success()
    assert cb.half_open is False
    assert cb._consecutive == 0 and cb._opened_at == 0.0
    # Fully closed: everyone is admitted again.
    assert cb.is_open() is False and cb.is_open() is False


def test_a_failed_trial_reopens_with_the_deadline_from_now(monkeypatch):
    """The regression the old shape had: after the cooldown it forgot everything, so a
    sustained 429 needed another THRESHOLD consecutive errors to re-open."""
    monkeypatch.setattr(settings, "GEMINI_QUOTA_CIRCUIT_THRESHOLD", 3)
    monkeypatch.setattr(settings, "GEMINI_QUOTA_CIRCUIT_COOLDOWN_SECONDS", 30.0)
    clock = _install_clock(monkeypatch, start=1000.0)
    _reset_breaker()
    cb = gemini._quota_circuit

    for _ in range(3):
        cb.record_quota_error()
    clock.advance(30.0)  # 1030
    assert cb.is_open() is False  # trial
    clock.advance(2.0)  # 1032: the trial 429s
    cb.record_quota_error()
    assert cb.half_open is False
    assert cb._opened_at == 1032.0, "re-open must stamp the deadline from the trial's failure"
    assert cb.is_open() is True
    # ONE error re-opened it — not another threshold's worth.
    clock.advance(29.0)  # 1061 < 1062
    assert cb.is_open() is True
    clock.advance(1.0)  # 1062: next trial
    assert cb.is_open() is False


def test_a_lost_trial_expires_after_another_cooldown(monkeypatch):
    """A trial that raised a NON-quota error (timeout, disconnect) never reports to the
    breaker. It must not hold the half-open slot forever."""
    monkeypatch.setattr(settings, "GEMINI_QUOTA_CIRCUIT_THRESHOLD", 3)
    monkeypatch.setattr(settings, "GEMINI_QUOTA_CIRCUIT_COOLDOWN_SECONDS", 30.0)
    clock = _install_clock(monkeypatch, start=1000.0)
    _reset_breaker()
    cb = gemini._quota_circuit

    for _ in range(3):
        cb.record_quota_error()
    clock.advance(30.0)
    assert cb.is_open() is False  # trial admitted, then vanishes
    clock.advance(29.9)
    assert cb.is_open() is True   # still waiting on it
    clock.advance(0.2)
    assert cb.is_open() is False  # presumed lost → a fresh trial


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


def test_tripped_is_a_non_mutating_view_of_the_gate(monkeypatch):
    """`is_open()` ADMITS the trial as a side effect; `tripped` only reads."""
    clock = _install_clock(monkeypatch)
    _reset_breaker()
    monkeypatch.setattr(settings, "GEMINI_QUOTA_CIRCUIT_THRESHOLD", 3)
    monkeypatch.setattr(settings, "GEMINI_QUOTA_CIRCUIT_COOLDOWN_SECONDS", 30)
    cb = gemini._quota_circuit
    for _ in range(3):
        cb.record_quota_error()
    assert cb.tripped is True
    clock.advance(31)
    assert cb.tripped is False and cb.tripped is False   # reading twice consumes nothing
    assert cb.half_open is False                          # ...and starts no trial
    assert cb.is_open() is False                          # the ONE admission
    assert cb.half_open is True
    assert cb.tripped is True                             # others wait on the trial
    assert cb.half_open is True                           # still the same trial


@pytest.mark.asyncio
async def test_the_retry_loop_consults_the_gate_once_per_logical_call(monkeypatch):
    """The half-open TRIAL call must be allowed to retry a transient failure. Re-checking
    `is_open()` at the top of every retry iteration made the trial reject ITSELF: its
    own marker read as "someone else's trial in flight" and a retryable error became a
    quota fail-fast."""
    clock = _install_clock(monkeypatch)
    _reset_breaker()
    monkeypatch.setattr(settings, "GEMINI_QUOTA_CIRCUIT_THRESHOLD", 2)
    monkeypatch.setattr(settings, "GEMINI_QUOTA_CIRCUIT_COOLDOWN_SECONDS", 30)

    async def _no_sleep(_):  # the generic branch backs off `delay*n`; keep it instant
        return None
    monkeypatch.setattr(gemini.asyncio, "sleep", _no_sleep)

    cb = gemini._quota_circuit
    cb.record_quota_error(); cb.record_quota_error()
    assert cb.is_open() is True
    clock.advance(31)                                     # cooldown over, trial available

    calls = {"n": 0}

    @gemini.async_retry(max_attempts=3, delay=0.0)
    async def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient: connection reset")   # generic, retryable
        return "ok"

    assert await flaky() == "ok"
    assert calls["n"] == 2
    assert cb.half_open is False and cb.tripped is False   # the successful trial closed it


@pytest.mark.asyncio
async def test_a_retrier_admitted_while_closed_is_refused_after_others_trip_the_breaker(monkeypatch):
    """Admit-once must not become admit-forever: a call that was admitted with the breaker
    CLOSED and is sleeping in a backoff when OTHER callers trip it must not wake up and fire
    one more request at the exhausted quota (whose 429 would be booked as the trial's)."""
    _install_clock(monkeypatch)
    _reset_breaker()
    monkeypatch.setattr(settings, "GEMINI_QUOTA_CIRCUIT_THRESHOLD", 2)
    monkeypatch.setattr(settings, "GEMINI_QUOTA_CIRCUIT_COOLDOWN_SECONDS", 30)
    cb = gemini._quota_circuit

    async def _trip_during_backoff(_):
        cb.record_quota_error(); cb.record_quota_error()   # other callers exhaust the quota
    monkeypatch.setattr(gemini.asyncio, "sleep", _trip_during_backoff)

    calls = {"n": 0}

    @gemini.async_retry(max_attempts=3, delay=0.0)
    async def flaky():
        calls["n"] += 1
        raise RuntimeError("transient: connection reset")   # generic → backoff → retry

    with pytest.raises(gemini.GeminiQuotaError):
        await flaky()
    assert calls["n"] == 1, "the second attempt must be refused, not fired"
