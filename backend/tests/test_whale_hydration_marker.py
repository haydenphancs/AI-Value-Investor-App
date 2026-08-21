"""Durable hydration marker + the FMP-outage write guard.

Two defects, one root cause: the hydration job could not tell the difference between
"it ran and legitimately had nothing to do" and "it never ran / it failed".

1. DURABLE MARKER (migration 147). "Has today's full sweep run?" used to be INFERRED
   from the boot clock — `last_full_run_date = _boot.date() if _boot.hour >= 2`. That is
   wrong in exactly the case that hurts: a redeploy or OOM at 02:07, mid-run, boots a
   process that skips the REST OF THE DAY, leaving un-swept whales on yesterday's data
   with nothing downstream able to compensate. It is now a claim in
   `notification_job_state`, which also records `items_written` — the count of whales that
   actually took the write path.

   ⚠️ The day boundary must be UTC. `claim_notification_job` hardcodes
   `AT TIME ZONE 'America/New_York'`; a 02:00 UTC job judged on an ET calendar is 21:00
   the PREVIOUS day, so the marker would misdate every run, and a schedule shifted past
   04:00 UTC would put two consecutive daily runs on one ET day and silently suppress
   one. `claim_scheduled_job` takes the timezone as a parameter; these tests pin that the
   whale job passes UTC.

   ⚠️ `max(whales.last_hydrated_at)` must NEVER become the marker: the 6-hourly
   politician branch re-stamps it, so it would read "already ran today" every day and
   suppress the full sweep forever.

2. FMP-OUTAGE WRITE GUARD. Every FMP method swallows its exception and returns [], so an
   empty result is ambiguous. `_hydrate_one`'s ticker-only fallback issued a blocking
   `whales` UPDATE on that ambiguous empty — meaning a 429 storm wrote a return computed
   from degraded data for every ticker-backed whale, on every sweep, indefinitely.

Pure logic — no network, no real Supabase. Run via `python -m pytest` from backend/.
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.integrations.fmp import FMPClient, FMPRateLimitException
from app.services import notification_jobs as nj


# ─────────────────────────────────────────────────────────────────────────────
# Fakes
# ─────────────────────────────────────────────────────────────────────────────
class _FakeQuery:
    """Chainable postgrest stub that records whether execute() was reached."""

    def __init__(self, sink, table):
        self._sink = sink
        self._table = table

    def update(self, payload):
        self._sink.append(("update", self._table, payload))
        return self

    def eq(self, *_a, **_k):
        return self

    def execute(self):
        self._sink.append(("execute", self._table, None))
        return SimpleNamespace(data=[])


class _FakeSupabase:
    def __init__(self):
        self.ops = []

    def table(self, name):
        return _FakeQuery(self.ops, name)

    @property
    def writes(self):
        return [o for o in self.ops if o[0] == "update"]


def _make_hydrator(*, fmp, sb):
    """Build a WhaleHydrator without touching get_supabase() or any real client."""
    from scripts.hydrate_whales import WhaleHydrator

    h = object.__new__(WhaleHydrator)
    h.fmp = fmp
    h.gemini = MagicMock()
    h.force = False
    h.dry_run = False
    h.sb = sb
    h.stats = {
        "processed": 0, "skipped": 0, "errors": 0, "no_data": 0, "upstream_failed": 0,
    }
    h._profile_cache = {}
    return h


_WHALE = {
    "id": "11111111-1111-4111-8111-111111111111",
    "name": "Test Fund",
    "data_source": "13f",
    "cik": "0001234567",
    "associated_ticker": "ARKK",   # ticker-backed ⇒ eligible for the fallback WRITE
}


# ─────────────────────────────────────────────────────────────────────────────
# 1. FMP-outage write guard
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_empty_without_failure_still_takes_the_ticker_only_write():
    """A genuinely empty filer must keep its existing behaviour — this is the control.

    Without it the guard below could pass by simply never writing at all.
    """
    fmp = SimpleNamespace(request_failures=0)
    sb = _FakeSupabase()
    h = _make_hydrator(fmp=fmp, sb=sb)

    h._process_13f = AsyncMock(return_value=None)          # empty, no failure
    h._compute_ytd_return = AsyncMock(return_value=SimpleNamespace(
        is_ok=True, value=12.5, source="ticker", window_years=15, status="ok",
    ))

    await h._hydrate_one(dict(_WHALE))

    assert h.stats["no_data"] == 1, "a real empty must still count as no_data"
    assert h.stats["upstream_failed"] == 0
    assert h.stats["processed"] == 1, "ticker-only fallback should have run"
    assert sb.writes, "the control case is supposed to WRITE"


@pytest.mark.asyncio
async def test_empty_caused_by_fmp_failure_writes_nothing():
    """An outage must not be written as data, and must not be counted as no_data."""
    fmp = SimpleNamespace(request_failures=0)
    sb = _FakeSupabase()
    h = _make_hydrator(fmp=fmp, sb=sb)

    async def _fail(*_a, **_k):
        # What a swallowed 429 looks like from here: counter moves, result is empty.
        fmp.request_failures += 3
        return None

    h._process_13f = _fail
    h._compute_ytd_return = AsyncMock(return_value=SimpleNamespace(
        is_ok=True, value=12.5, source="ticker", window_years=15, status="ok",
    ))

    await h._hydrate_one(dict(_WHALE))

    assert sb.writes == [], "an FMP outage must take NO write path"
    assert h.stats["upstream_failed"] == 1
    assert h.stats["no_data"] == 0, "an outage must never be fed to dormancy review"
    assert h.stats["processed"] == 0


@pytest.mark.asyncio
async def test_failure_counter_moves_only_on_failure():
    """The guard is only as good as the counter it reads."""
    c = FMPClient()
    c._make_request_impl = AsyncMock(side_effect=FMPRateLimitException("429"))
    for _ in range(2):
        with pytest.raises(FMPRateLimitException):
            await c._make_request("x")
    assert c.request_failures == 2

    c._make_request_impl = AsyncMock(return_value=[{"ok": 1}])
    for _ in range(3):
        await c._make_request("x")
    assert c.request_failures == 2, "success must not increment"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Durable marker semantics
# ─────────────────────────────────────────────────────────────────────────────
def _rpc_capture(return_data):
    """Fake supabase whose .rpc() records the call and returns `return_data`."""
    calls = []

    class _RPC:
        def __init__(self, name, params):
            calls.append((name, params))

        def execute(self):
            return SimpleNamespace(data=return_data)

    sb = SimpleNamespace(rpc=lambda name, params: _RPC(name, params))
    return sb, calls


def test_claim_uses_utc_not_eastern(monkeypatch):
    """The whole reason claim_scheduled_job exists. Pin it."""
    sb, calls = _rpc_capture(True)
    monkeypatch.setattr(nj, "_sb", lambda: sb)

    assert nj.claim_scheduled(nj.JOB_WHALE_HYDRATION_FULL) is True
    name, params = calls[0]
    assert name == "claim_scheduled_job", "must not fall back to the ET-hardcoded RPC"
    assert params["p_timezone"] == "UTC"
    assert params["p_job"] == "whale_hydration_full"


def test_claim_fails_closed_on_error(monkeypatch):
    """Fail-closed: a skipped wake is cheap, a duplicated 56-whale FMP sweep is not."""
    def _boom():
        raise RuntimeError("supabase down")
    monkeypatch.setattr(nj, "_sb", _boom)
    assert nj.claim_scheduled(nj.JOB_WHALE_HYDRATION_FULL) is False


def test_claim_denied_when_rpc_returns_false(monkeypatch):
    sb, _ = _rpc_capture(False)
    monkeypatch.setattr(nj, "_sb", lambda: sb)
    assert nj.claim_scheduled(nj.JOB_WHALE_HYDRATION_FULL) is False


def test_finish_records_items_written(monkeypatch):
    """`items_written` is what separates 'ran, wrote nothing' from 'never ran'."""
    sb, calls = _rpc_capture(None)
    monkeypatch.setattr(nj, "_sb", lambda: sb)

    nj.finish_scheduled(nj.JOB_WHALE_HYDRATION_FULL, success=True, items=44)
    name, params = calls[0]
    assert name == "finish_scheduled_job"
    assert params["p_items"] == 44
    assert params["p_success"] is True
    assert params["p_timezone"] == "UTC"


@pytest.mark.asyncio
async def test_context_manager_yields_none_when_not_granted(monkeypatch):
    monkeypatch.setattr(nj, "claim_scheduled", lambda *a, **k: False)
    finished = []
    monkeypatch.setattr(nj, "finish_scheduled", lambda *a, **k: finished.append(k))

    async with nj.claimed_scheduled_job(nj.JOB_WHALE_HYDRATION_FULL) as run:
        assert run is None

    assert finished == [], "a claim that was never granted must not be released"


@pytest.mark.asyncio
async def test_success_path_records_items(monkeypatch):
    monkeypatch.setattr(nj, "claim_scheduled", lambda *a, **k: True)
    finished = []
    monkeypatch.setattr(nj, "finish_scheduled",
                        lambda job, **k: finished.append(k))

    async with nj.claimed_scheduled_job(nj.JOB_WHALE_HYDRATION_FULL) as run:
        run.items = 44
        run.success = True

    assert finished[0]["success"] is True
    assert finished[0]["items"] == 44


@pytest.mark.asyncio
async def test_ran_but_wrote_nothing_is_a_success_with_zero_items(monkeypatch):
    """The 13F off-season case. It RAN — run_day must advance — but wrote nothing.

    This is the exact state that made the latency measurement unreadable: identical to a
    job that never fired, unless items is recorded alongside success.
    """
    monkeypatch.setattr(nj, "claim_scheduled", lambda *a, **k: True)
    finished = []
    monkeypatch.setattr(nj, "finish_scheduled", lambda job, **k: finished.append(k))

    async with nj.claimed_scheduled_job(nj.JOB_WHALE_HYDRATION_FULL) as run:
        run.items = 0
        run.success = True

    assert finished[0]["success"] is True, "it ran; the day must not be retried"
    assert finished[0]["items"] == 0, "and it must be visible that nothing was written"


@pytest.mark.asyncio
async def test_body_exception_records_failure_so_the_day_retries(monkeypatch):
    monkeypatch.setattr(nj, "claim_scheduled", lambda *a, **k: True)
    finished = []
    monkeypatch.setattr(nj, "finish_scheduled", lambda job, **k: finished.append(k))

    with pytest.raises(ValueError):
        async with nj.claimed_scheduled_job(nj.JOB_WHALE_HYDRATION_FULL) as run:
            run.items = 7
            raise ValueError("fmp exploded")

    assert finished[0]["success"] is False, "run_day must not advance on failure"
    assert finished[0]["items"] == 7, "partial progress must still be recorded"
    assert "ValueError" in finished[0]["error"]


@pytest.mark.asyncio
async def test_cancellation_records_failure_and_reraises(monkeypatch):
    """The mid-run redeploy — the exact scenario the old clock seed got wrong.

    `CancelledError` is a BaseException, so a plain `except Exception` would miss it and
    the claim would sit parked for the full stale window.
    """
    monkeypatch.setattr(nj, "claim_scheduled", lambda *a, **k: True)
    finished = []
    monkeypatch.setattr(nj, "finish_scheduled", lambda job, **k: finished.append(k))

    with pytest.raises(asyncio.CancelledError):
        async with nj.claimed_scheduled_job(nj.JOB_WHALE_HYDRATION_FULL) as run:
            run.items = 12
            raise asyncio.CancelledError()

    assert finished[0]["success"] is False
    assert finished[0]["items"] == 12
    assert "cancelled" in (finished[0]["error"] or "")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Source guards — brace-bounded, comment-stripped (source scans go vacuous)
# ─────────────────────────────────────────────────────────────────────────────
def _strip_py_comments(src: str) -> str:
    out = []
    for line in src.splitlines():
        s = line.split("#", 1)[0]
        if s.strip():
            out.append(s)
    return "\n".join(out)


def _hydration_job_body() -> str:
    """Just `_run_whale_hydration_job`, comments removed.

    Bounded to the function: asserting against the whole of main.py would pass on a
    token that lives in an unrelated job.
    """
    src = Path(__file__).resolve().parents[1] / "app" / "main.py"
    text = src.read_text()
    start = text.index("async def _run_whale_hydration_job(")
    nxt = text.find("\nasync def ", start + 1)
    other = text.find("\ndef ", start + 1)
    ends = [e for e in (nxt, other) if e != -1]
    return _strip_py_comments(text[start:min(ends) if ends else len(text)])


def test_full_hydration_uses_the_durable_claim():
    body = _hydration_job_body()
    assert "claimed_scheduled_job(JOB_WHALE_HYDRATION_FULL)" in body
    assert "run.success = True" in body


def test_clock_inferred_seed_is_gone():
    """The regression this whole change exists to prevent."""
    body = _hydration_job_body()
    assert "last_full_run_date" not in body, (
        "the clock-inferred 'today already ran' seed is back — a mid-run restart will "
        "again silently skip the rest of the day"
    )


def test_last_hydrated_at_is_not_used_as_the_marker():
    """It is re-stamped by the 6-hourly politician branch; as a marker it would suppress
    the daily full sweep forever."""
    body = _hydration_job_body()
    assert "max(" not in body or "last_hydrated_at" not in body, (
        "last_hydrated_at must never gate the daily full run"
    )
