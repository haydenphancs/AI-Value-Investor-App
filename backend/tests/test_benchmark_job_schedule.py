"""Schedule-collision tests for the weekly TTM benchmark job.

Confirmed bug: the weekly TTM job fired at 04:00 UTC Sunday — the SAME instant as the
quarterly fiscal industry recompute (dossier base 02:00 + 120 min) on the first Sunday
of Jan/Apr/Jul/Oct — so the two FMP-heavy jobs raced 4x/year. Fixed by offsetting the
TTM run to 06:00 UTC via the pure helper `_next_weekly_ttm_run`, now unit-testable.
"""

from datetime import datetime, timedelta, timezone

from app.main import _next_weekly_ttm_run, _next_quarterly_dossier_run


def test_ttm_next_run_is_sunday_0600_utc():
    now = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)   # Wednesday
    nxt = _next_weekly_ttm_run(now)
    assert nxt.weekday() == 6                                  # Sunday
    assert (nxt.hour, nxt.minute, nxt.second) == (6, 0, 0)
    assert nxt > now


def test_ttm_sunday_before_0600_runs_same_day():
    now = datetime(2026, 6, 28, 3, 0, tzinfo=timezone.utc)    # Sunday 03:00
    assert now.weekday() == 6
    nxt = _next_weekly_ttm_run(now)
    assert nxt.date() == now.date()
    assert nxt.hour == 6


def test_ttm_sunday_after_0600_rolls_to_next_week():
    now = datetime(2026, 6, 28, 7, 0, tzinfo=timezone.utc)    # Sunday 07:00
    assert now.weekday() == 6
    nxt = _next_weekly_ttm_run(now)
    assert nxt.weekday() == 6
    assert nxt.date() > now.date()
    assert (nxt - now) >= timedelta(days=6)


def test_ttm_handles_month_and_year_boundary():
    now = datetime(2026, 12, 31, 23, 0, tzinfo=timezone.utc)  # Thursday, year-end
    nxt = _next_weekly_ttm_run(now)
    assert nxt.weekday() == 6 and nxt.hour == 6
    assert nxt > now


def test_ttm_never_collides_with_quarterly_fiscal_recompute():
    # On each quarter-start Sunday the fiscal recompute runs at dossier(02:00) + 120min
    # = 04:00 UTC, with the moat-job tail up to ~05:00. The TTM run must clear that.
    for year, month in [(2026, 1), (2026, 4), (2026, 7), (2026, 10)]:
        base = datetime(year, month, 1, 0, 0, tzinfo=timezone.utc)
        dossier = _next_quarterly_dossier_run(base)           # first Sunday 02:00 UTC
        fiscal_recompute = dossier + timedelta(minutes=120)   # 04:00 UTC
        ttm = _next_weekly_ttm_run(dossier.replace(hour=0, minute=0))
        assert ttm.date() == fiscal_recompute.date()          # same Sunday
        assert (ttm - fiscal_recompute) >= timedelta(hours=2)  # clears moat tail too


# ── F24-6: a restart inside a run's day re-enters it; every phase is durably claimed ──
#
# The quarterly chain lived only in one coroutine's stack: a redeploy at 03:00 on the
# first Sunday of a quarter booted into "next run in 2183.0h" and the remaining phases
# were lost for three months. The weekly TTM job had the same shape, one week at a time.

import asyncio
from types import SimpleNamespace

import pytest

from app import main as m


def test_last_quarterly_anchor_mirrors_next():
    now = datetime(2026, 8, 12, 9, 0, tzinfo=timezone.utc)          # mid-quarter
    last = m._last_quarterly_dossier_run(now)
    assert last == datetime(2026, 7, 5, 2, 0, tzinfo=timezone.utc)   # first Sunday of July
    assert m._next_quarterly_dossier_run(last) == datetime(2026, 10, 4, 2, 0, tzinfo=timezone.utc)
    assert m._last_quarterly_dossier_run(last) == last               # at-or-before, inclusive
    # January anchor comes from the PREVIOUS year's table when now is early January.
    early_jan = datetime(2027, 1, 2, 0, 0, tzinfo=timezone.utc)
    assert m._last_quarterly_dossier_run(early_jan) == datetime(2026, 10, 4, 2, 0, tzinfo=timezone.utc)


def test_last_weekly_ttm_anchor_mirrors_next():
    now = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)          # Wednesday
    last = m._last_weekly_ttm_run(now)
    assert last == datetime(2026, 6, 21, 6, 0, tzinfo=timezone.utc)
    assert m._next_weekly_ttm_run(last) == last + timedelta(days=7)
    sunday_0700 = datetime(2026, 6, 28, 7, 0, tzinfo=timezone.utc)
    assert m._last_weekly_ttm_run(sunday_0700) == datetime(2026, 6, 28, 6, 0, tzinfo=timezone.utc)


def test_catchup_anchor_is_inside_the_window_only():
    anchor = datetime(2026, 7, 5, 2, 0, tzinfo=timezone.utc)
    last = lambda now: anchor  # noqa: E731
    assert m._catchup_anchor(anchor + timedelta(hours=1), last) == anchor       # restart at 03:00
    assert m._catchup_anchor(anchor + timedelta(hours=19, minutes=59), last) == anchor
    assert m._catchup_anchor(anchor + timedelta(hours=20), last) is None        # window closed
    assert m._catchup_anchor(anchor - timedelta(seconds=1), last) is None       # not yet
    assert m._catchup_anchor(anchor, last) == anchor


@pytest.mark.asyncio
async def test_a_claimed_phase_runs_once_and_records_success(monkeypatch):
    calls = []
    outcomes = []

    class _Run:
        def __init__(self):
            self.success = False
            self.items = 0

    import contextlib

    @contextlib.asynccontextmanager
    async def _claimed(job, *, timezone_name="UTC", stale_seconds=None):
        calls.append((job, stale_seconds))
        run = _Run()
        try:
            yield run
        finally:
            outcomes.append((job, run.success))

    monkeypatch.setattr("app.services.notification_jobs.claimed_scheduled_job", _claimed)
    ran = []

    async def _body():
        ran.append(1)
        return {"ok": 1}

    assert await m._run_claimed_phase("job_x", "X", _body) is True
    assert ran == [1]
    assert calls == [("job_x", m._CHAIN_PHASE_STALE_SECONDS)]
    assert outcomes == [("job_x", True)]
    assert m._CHAIN_PHASE_STALE_SECONDS >= 2 * 3600, "must outlast the 60-90 min moat phase"


def _today_utc():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).date().isoformat()


def _refusing(monkeypatch, states, granted_after=None):
    """A claim that is refused until `granted_after` refusals, plus a job-state reader that
    answers `states` in order (last one repeats). Sleeps are instant and counted."""
    import contextlib
    calls = {"claims": 0, "sleeps": []}

    @contextlib.asynccontextmanager
    async def _claim(job, *, timezone_name="UTC", stale_seconds=None):
        calls["claims"] += 1
        if granted_after is not None and calls["claims"] > granted_after:
            yield SimpleNamespace(success=False, items=0)
        else:
            yield None

    def _state(job):
        i = min(len(states) - 1, calls["claims"] - 1)
        return states[i]

    async def _sleep(seconds):
        calls["sleeps"].append(seconds)

    monkeypatch.setattr("app.services.notification_jobs.claimed_scheduled_job", _claim)
    monkeypatch.setattr("app.services.notification_jobs.scheduled_job_state", _state)
    monkeypatch.setattr(m.asyncio, "sleep", _sleep)
    return calls


@pytest.mark.asyncio
async def test_a_phase_that_already_ran_today_is_skipped(monkeypatch):
    calls = _refusing(monkeypatch, [{"run_day": _today_utc(), "claim_at": None, "enabled": True}])
    ran = []

    async def _body():
        ran.append(1)

    assert await m._run_claimed_phase("job_x", "X", _body) is True, "already ran = settled"
    assert ran == [] and calls["claims"] == 1 and calls["sleeps"] == []


@pytest.mark.asyncio
async def test_a_held_claim_is_waited_on_and_taken_over_when_released(monkeypatch):
    """W2 B-1: a deploy overlap — the old instance holds the phase's claim when the new one
    re-enters the chain. "Refused" used to mean "done": the phase was skipped for the
    quarter. Now the new instance re-asks until the holder finishes (→ run_day today →
    skip) or releases without success / its claim goes stale (→ take over and run)."""
    held = {"run_day": None, "claim_at": "2026-10-04T03:30:00+00:00", "enabled": True}
    calls = _refusing(monkeypatch, [held, held, held], granted_after=3)
    ran = []

    async def _body():
        ran.append(1)
        return {"ok": 1}

    assert await m._run_claimed_phase("job_x", "X", _body) is True
    assert ran == [1], "taken over once the holder released it"
    assert calls["claims"] == 4
    assert calls["sleeps"] == [m._CLAIM_RETRY_SECONDS] * 3


@pytest.mark.asyncio
async def test_a_held_claim_whose_holder_finishes_is_then_skipped(monkeypatch):
    held = {"run_day": None, "claim_at": "2026-10-04T03:30:00+00:00", "enabled": True}
    done = {"run_day": _today_utc(), "claim_at": None, "enabled": True}
    calls = _refusing(monkeypatch, [held, done])
    ran = []

    async def _body():
        ran.append(1)

    assert await m._run_claimed_phase("job_x", "X", _body) is True
    assert ran == [] and calls["claims"] == 2 and len(calls["sleeps"]) == 1


@pytest.mark.asyncio
async def test_a_held_claim_is_abandoned_after_the_stale_window(monkeypatch):
    """Bounded: a claim that is never released (a SIGKILLed holder whose stale window has
    not yet elapsed on the RPC side) does not park the chain forever."""
    held = {"run_day": None, "claim_at": "2026-10-04T03:30:00+00:00", "enabled": True}
    calls = _refusing(monkeypatch, [held])
    clock = {"t": 1000.0}
    monkeypatch.setattr(m.time, "monotonic", lambda: clock["t"])

    async def _sleep(seconds):
        calls["sleeps"].append(seconds)
        clock["t"] += seconds
    monkeypatch.setattr(m.asyncio, "sleep", _sleep)
    ran = []

    async def _body():
        ran.append(1)

    assert await m._run_claimed_phase("job_x", "X", _body) is False, "unsettled → retried"
    assert ran == []
    assert calls["sleeps"], "it waited before giving up"
    assert sum(calls["sleeps"]) >= m._CHAIN_PHASE_STALE_SECONDS


@pytest.mark.asyncio
async def test_an_unreadable_ledger_or_a_disabled_job_fails_closed(monkeypatch):
    ran = []

    async def _body():
        ran.append(1)

    _refusing(monkeypatch, [None])
    assert await m._run_claimed_phase("job_x", "X", _body) is False, "unreadable → retried"
    calls = _refusing(monkeypatch, [{"run_day": None, "claim_at": None, "enabled": False}])
    assert await m._run_claimed_phase("job_x", "X", _body) is True, "operator-disabled = settled"
    assert ran == [] and calls["sleeps"] == []


def test_the_job_state_reader_reads_the_ledger_row(monkeypatch):
    from app.services import notification_jobs as nj
    seen = {}

    class _Q:
        def select(self, cols): seen["cols"] = cols; return self
        def eq(self, col, val): seen["eq"] = (col, val); return self
        def limit(self, n): return self
        def execute(self): return SimpleNamespace(data=[{"job": "j", "run_day": "2026-10-04", "claim_at": None, "enabled": True}])

    class _SB:
        def table(self, name): seen["table"] = name; return _Q()

    monkeypatch.setattr(nj, "_sb", lambda: _SB())
    assert nj.scheduled_job_state("j")["run_day"] == "2026-10-04"
    assert seen["table"] == "notification_job_state" and seen["eq"] == ("job", "j")

    class _Boom:
        def table(self, name): raise RuntimeError("520")
    monkeypatch.setattr(nj, "_sb", lambda: _Boom())
    assert nj.scheduled_job_state("j") is None


@pytest.mark.asyncio
async def test_a_failing_phase_releases_its_claim_as_a_failure(monkeypatch):
    import contextlib
    outcomes = []

    @contextlib.asynccontextmanager
    async def _claimed(job, *, timezone_name="UTC", stale_seconds=None):
        run = SimpleNamespace(success=False, items=0)
        try:
            yield run
        finally:
            outcomes.append(run.success)

    monkeypatch.setattr("app.services.notification_jobs.claimed_scheduled_job", _claimed)

    async def _body():
        raise RuntimeError("FMP down")

    # swallowed + logged, loop survives — and reported UNSETTLED so the loop retries it
    assert await m._run_claimed_phase("job_x", "X", _body) is False
    assert outcomes == [False]


@pytest.mark.asyncio
async def test_a_cancelled_phase_propagates_cancellation(monkeypatch):
    import contextlib

    @contextlib.asynccontextmanager
    async def _claimed(job, *, timezone_name="UTC", stale_seconds=None):
        yield SimpleNamespace(success=False, items=0)

    monkeypatch.setattr("app.services.notification_jobs.claimed_scheduled_job", _claimed)

    async def _body():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await m._run_claimed_phase("job_x", "X", _body)


def test_every_chain_phase_is_claimed_under_its_own_job_name():
    """Source-scan, bound to the two job bodies: five distinct quarterly claims + one
    weekly, each through `_run_claimed_phase`; no phase runs bare."""
    import inspect
    import re
    src = inspect.getsource(m._run_industry_dossier_job)
    src = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    jobs = re.findall(r"_run_claimed_phase\(\s*(JOB_[A-Z_]+)", src)
    assert jobs == [
        "JOB_INDUSTRY_DOSSIER_QUARTERLY", "JOB_COMPETITOR_INTEL_QUARTERLY",
        "JOB_IP_INTEL_QUARTERLY", "JOB_INDUSTRY_MOAT_QUARTERLY",
        "JOB_INDUSTRY_BENCHMARK_QUARTERLY",
    ], jobs
    # No phase awaits its service bare, outside a claimed body (docstring stripped too).
    body = src.split('"""', 2)[-1] if src.count('"""') >= 2 else src
    bare = [ln for ln in body.splitlines()
            if "await get_" in ln and "_service()" in ln and "return await" not in ln]
    assert bare == [], bare
    assert "_catchup_anchor(now, _last_quarterly_dossier_run)" in src
    ttm = inspect.getsource(m._run_ttm_benchmark_job)
    assert "_run_claimed_phase(JOB_TTM_BENCHMARK_WEEKLY" in ttm
    assert "_catchup_anchor(now, _last_weekly_ttm_run)" in ttm


def test_the_claim_helper_honours_a_per_job_stale_window(monkeypatch):
    from app.services import notification_jobs as nj
    seen = {}

    class _Q:
        def execute(self):
            return SimpleNamespace(data=True)

    class _SB:
        def rpc(self, name, params):
            seen["params"] = params
            return _Q()

    monkeypatch.setattr(nj, "_sb", lambda: _SB())
    assert nj.claim_scheduled("job_x", stale_seconds=10_800) is True
    assert seen["params"]["p_stale_seconds"] == 10_800
    assert nj.claim_scheduled("job_x") is True
    assert seen["params"]["p_stale_seconds"] == nj.settings.NOTIFICATION_JOB_STALE_SECONDS


# ── A failed phase is retried inside the same run's catch-up window ─────────────────────
# `_run_claimed_phase` used to return None on a failure and both loops then slept to the
# NEXT anchor: one FRED/FMP blip at 02:00 on a quarter-start Sunday left that phase's data
# a quarter stale (the TTM job: a week), although the docstring promised a retry.


class _StopLoop(Exception):
    pass


def _drive_loop(monkeypatch, *, anchor_fn_name, phase_results):
    """Run a scheduled loop against a fixed anchor 3 h in the past (inside its catch-up
    window, every chain offset already due). `_run_claimed_phase` answers from
    `phase_results[job]` in order (last value repeats). Sleeps are instant; the long sleep
    to the NEXT anchor ends the test."""
    anchor = datetime.now(timezone.utc) - timedelta(hours=3)
    monkeypatch.setattr(m, anchor_fn_name, lambda now: anchor)
    runs, sleeps = [], []

    async def _phase(job, label, body):
        runs.append(job)
        seq = phase_results.get(job, [True])
        return seq[min(runs.count(job) - 1, len(seq) - 1)]

    async def _sleep(seconds):
        sleeps.append(seconds)
        if seconds not in (120, 180, m._PHASE_RETRY_SECONDS):
            raise _StopLoop()

    monkeypatch.setattr(m, "_run_claimed_phase", _phase)
    monkeypatch.setattr(m.asyncio, "sleep", _sleep)
    return runs, sleeps


@pytest.mark.asyncio
async def test_a_failed_ttm_run_is_retried_in_the_same_window(monkeypatch):
    runs, sleeps = _drive_loop(
        monkeypatch, anchor_fn_name="_last_weekly_ttm_run",
        phase_results={m.JOB_TTM_BENCHMARK_WEEKLY: [False, True]},
    )
    with pytest.raises(_StopLoop):
        await m._run_ttm_benchmark_job()
    assert runs == [m.JOB_TTM_BENCHMARK_WEEKLY] * 2, "failed once, retried once, then settled"
    assert sleeps.count(m._PHASE_RETRY_SECONDS) == 1


@pytest.mark.asyncio
async def test_retries_are_bounded_per_anchor(monkeypatch):
    runs, sleeps = _drive_loop(
        monkeypatch, anchor_fn_name="_last_weekly_ttm_run",
        phase_results={m.JOB_TTM_BENCHMARK_WEEKLY: [False]},
    )
    with pytest.raises(_StopLoop):
        await m._run_ttm_benchmark_job()
    assert runs == [m.JOB_TTM_BENCHMARK_WEEKLY] * (1 + m._MAX_PHASE_RETRIES_PER_ANCHOR)
    assert sleeps.count(m._PHASE_RETRY_SECONDS) == m._MAX_PHASE_RETRIES_PER_ANCHOR


@pytest.mark.asyncio
async def test_a_settled_ttm_run_is_not_retried(monkeypatch):
    runs, sleeps = _drive_loop(
        monkeypatch, anchor_fn_name="_last_weekly_ttm_run",
        phase_results={m.JOB_TTM_BENCHMARK_WEEKLY: [True]},
    )
    with pytest.raises(_StopLoop):
        await m._run_ttm_benchmark_job()
    assert runs == [m.JOB_TTM_BENCHMARK_WEEKLY]
    assert m._PHASE_RETRY_SECONDS not in sleeps


@pytest.mark.asyncio
async def test_one_failed_quarterly_phase_re_enters_the_chain(monkeypatch):
    """The whole chain is re-entered; in production the phases that already ran are
    skipped by their own day-keyed claims, so only the failed one does work again."""
    runs, sleeps = _drive_loop(
        monkeypatch, anchor_fn_name="_last_quarterly_dossier_run",
        phase_results={m.JOB_COMPETITOR_INTEL_QUARTERLY: [False, True]},
    )
    with pytest.raises(_StopLoop):
        await m._run_industry_dossier_job()
    assert runs.count(m.JOB_COMPETITOR_INTEL_QUARTERLY) == 2
    assert runs.count(m.JOB_INDUSTRY_BENCHMARK_QUARTERLY) == 2, "the chain re-entered once"
    assert sleeps.count(m._PHASE_RETRY_SECONDS) == 1


def test_the_retry_budget_is_per_anchor():
    r = m._AnchorRetries()
    a1 = datetime(2026, 10, 4, 2, tzinfo=timezone.utc)
    r.entered(a1)
    r.used = 2
    r.entered(a1)
    assert r.used == 2, "re-entering the same anchor must not refill its budget"
    r.entered(a1 + timedelta(days=91))
    assert r.used == 0
