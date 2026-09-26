"""
The script lease must outlive the slowest single model call (script_service.LEASE_SECONDS).

The lease is refreshed before each `generate_json` call. If one call can outlast it, a second
container (a Railway deploy overlap) takes the expired lease and pays for a parallel generation
while the first is still running. The old figure — "one call is bounded at ~200 s" — counted
only the timeout and generic retry budgets; `async_retry` keeps FOUR independent budgets, so the
real worst case is 572 s at the default settings.

These tests do not trust `worst_case_model_call_seconds`: they read `generate_json`'s decorator
arguments out of gemini.py and drive the REAL `async_retry` through every budget, then compare.
Hermetic: the breaker is replaced, `asyncio.sleep` is recorded instead of slept.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.config import settings
from app.integrations import gemini
from app.services.marketing import script_service as ss

_GEMINI_SRC = Path(__file__).resolve().parents[1] / "app" / "integrations" / "gemini.py"


def _generate_json_decorator_args():
    src = _GEMINI_SRC.read_text(encoding="utf-8")
    m = re.search(
        r"@async_retry\(\s*max_attempts\s*=\s*(\d+)\s*,\s*delay\s*=\s*([0-9.]+)\s*\)\s*\n\s*"
        r"async\s+def\s+generate_json\b", src,
    )
    assert m, "GeminiClient.generate_json is no longer decorated with @async_retry(max_attempts=…, delay=…)"
    return int(m.group(1)), float(m.group(2))


def test_the_mirrored_decorator_arguments_match_gemini_py():
    attempts, delay = _generate_json_decorator_args()
    assert (attempts, delay) == (ss._GENERATE_JSON_MAX_ATTEMPTS, ss._GENERATE_JSON_DELAY_SECONDS)


class _NoBreaker:
    half_open = False
    tripped = False
    trial_stamp = 0.0

    def is_open(self):
        return False

    def record_success(self):
        pass

    def record_quota_error(self):
        pass

    def release_trial(self, _stamp):
        pass


@pytest.mark.parametrize("order", ["grouped", "interleaved"])
@pytest.mark.asyncio
async def test_the_real_async_retry_never_exceeds_the_computed_worst_case(monkeypatch, order):
    max_attempts, delay = _generate_json_decorator_args()
    q = int(settings.GEMINI_QUOTA_MAX_RETRIES)
    t = int(settings.GEMINI_TIMEOUT_MAX_RETRIES)
    monkeypatch.setattr(gemini, "_quota_circuit", _NoBreaker())
    sleeps = []

    async def record_sleep(seconds, *a, **k):
        sleeps.append(float(seconds))

    monkeypatch.setattr(gemini.asyncio, "sleep", record_sleep)

    overload = [RuntimeError("503 UNAVAILABLE: the model is overloaded")] * (q + 1)
    quota = [RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")] * (q + 1)
    timeouts = [gemini.GeminiTimeoutError("generate_json exceeded its timeout")] * (t + 1)
    generic = [RuntimeError("Server disconnected without sending a response")] * max_attempts
    # Every budget one short of exhaustion, then one final failure: the maximum number of tries.
    if order == "grouped":
        seq = overload[:q] + quota[:q] + timeouts[:t] + generic[: max_attempts - 1] + [generic[-1]]
    else:
        pools = [overload[:q], quota[:q], timeouts[:t], generic[: max_attempts - 1]]
        seq = []
        while any(pools):
            for p in pools:
                if p:
                    seq.append(p.pop())
        seq.append(generic[-1])
    calls = []

    @gemini.async_retry(max_attempts=max_attempts, delay=delay)
    async def call():
        calls.append(1)
        raise seq[len(calls) - 1]

    with pytest.raises(Exception):
        await call()
    observed = len(calls) * float(settings.GEMINI_REQUEST_TIMEOUT_SECONDS) + sum(sleeps)
    assert len(calls) == len(seq)
    assert observed <= ss.worst_case_model_call_seconds() + 1e-9, (len(calls), sleeps)
    assert ss.LEASE_SECONDS > observed


def test_the_lease_outlives_one_call_and_still_fits_one_worker_poll_session():
    worst = ss.worst_case_model_call_seconds()
    # at the shipped defaults: 6 attempts x 90 s + 32 s of backoff
    if (settings.GEMINI_REQUEST_TIMEOUT_SECONDS, settings.GEMINI_QUOTA_MAX_RETRIES,
            settings.GEMINI_TIMEOUT_MAX_RETRIES, settings.GEMINI_QUOTA_RETRY_DELAY_SECONDS) == (90, 2, 0, 5.0):
        assert worst == 572.0
    assert ss.LEASE_SECONDS >= worst + ss.LEASE_MARGIN_SECONDS
    # A crashed owner must free the run within one worker poll session (15 min), or the next kick
    # could never take over before the tick gives up.
    assert ss.LEASE_SECONDS < 15 * 60


def _independent_worst_case_generation_seconds() -> float:
    """A whole generation, recomputed HERE from the retry constants the loops use and from
    postgrest's own client timeout (not via `generation_budget`): acquire (read + conditional
    UPDATE) + the run-date read + per model call (one lease refresh + the call) + one terminal
    write with its re-read. `test_marketing_residuals_lease.py` drives the real loops to prove
    these counts are what the code does."""
    from postgrest.constants import DEFAULT_POSTGREST_CLIENT_TIMEOUT
    from app.services.marketing.generation_budget import MODEL_CALLS_PER_GENERATION

    stmt = float(DEFAULT_POSTGREST_CLIENT_TIMEOUT)
    refresh = (ss._REFRESH_ATTEMPTS * stmt
               + sum(ss._REFRESH_BACKOFF_SECONDS * k for k in range(1, ss._REFRESH_ATTEMPTS)))
    terminal = (ss._FINISH_ATTEMPTS * stmt
                + sum(ss._FINISH_BACKOFF_SECONDS * k for k in range(1, ss._FINISH_ATTEMPTS))
                + stmt)  # the `_landed` re-read after a retry matched nothing
    per_call = ss.worst_case_model_call_seconds() + refresh
    return 2 * stmt + stmt + MODEL_CALLS_PER_GENERATION * per_call + terminal


def test_an_in_process_owner_counts_as_alive_for_a_whole_generation_and_no_longer():
    """`_advance` leaves a lapsed lease alone while its owner is a live task in the same process
    (OWNER_ALIVE_SECONDS). It must cover a whole generation — every model call behind its own
    lease refresh, every ledger statement to the PostgREST timeout, the terminal write with its
    retries and re-read — or a slow LIVE owner at the cap is closed out from under and its paid
    package is fenced out. It used to be 3 × LEASE_SECONDS (1896 s) against a real worst case of
    ~2710 s with two calls, 4577 s with four. It must also be finite and tight (the bound plus
    the margin, rounded up), or one wedged task wedges the day in that process."""
    generation = _independent_worst_case_generation_seconds()
    assert ss.worst_case_generation_seconds() == pytest.approx(generation)
    assert ss.OWNER_ALIVE_SECONDS >= generation + ss.LEASE_MARGIN_SECONDS
    assert ss.OWNER_ALIVE_SECONDS < generation + ss.LEASE_MARGIN_SECONDS + 1
    if (settings.GEMINI_REQUEST_TIMEOUT_SECONDS, settings.GEMINI_QUOTA_MAX_RETRIES,
            settings.GEMINI_TIMEOUT_MAX_RETRIES, settings.GEMINI_QUOTA_RETRY_DELAY_SECONDS) == (90, 2, 0, 5.0):
        # 3 × 120 + 4 × (572 + 361.5) + 483, at the shipped defaults (PostgREST 120 s, 4 calls)
        assert generation == 4577.0 and ss.OWNER_ALIVE_SECONDS == 4637
    # The lease stays PER CALL: it is the cross-process takeover window, refreshed before each.
    assert ss.LEASE_SECONDS < ss.OWNER_ALIVE_SECONDS


def test_the_per_run_model_call_bound():
    """Two caps × (draft, judge, repair, judge): (4 + 4 - 1) generations × 4 calls = 28."""
    from app.services.marketing.generation_budget import MODEL_CALLS_PER_GENERATION

    assert MODEL_CALLS_PER_GENERATION == 4
    assert ss.MAX_MODEL_CALLS_PER_RUN == (
        (ss.MAX_GENERATIONS + ss.MAX_WRITER_FAILURES - 1) * MODEL_CALLS_PER_GENERATION
    ) == 28
