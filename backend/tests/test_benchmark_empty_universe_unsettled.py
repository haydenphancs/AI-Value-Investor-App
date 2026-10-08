"""A benchmark run whose universe is empty must leave its scheduled claim UNSETTLED.

Same bug class as `test_industry_dossier_skip_unsettled.py` (found 2026-10-01).
`_run_claimed_phase` (main.py) settles any phase that RETURNS. `load_universe` returns `[]`
when the Supabase Storage download of a universe file fails, and neither
`industry_universe.json` nor `benchmark_universe.json` is in git. So three more chain
phases iterated nothing, returned a zero summary, and consumed the quarter (or the week):

  * `industry_moat_benchmark_service.recompute_all`   — JOB_INDUSTRY_MOAT_QUARTERLY
  * `industry_benchmark_service.recompute_all`        — JOB_INDUSTRY_BENCHMARK_QUARTERLY
  * `industry_benchmark_service.recompute_all_ttm`    — JOB_TTM_BENCHMARK_WEEKLY

Each now logs at ERROR and raises a typed `*RecomputeSkipped` before any write. The
legitimate returns stay returns: sectors/industries skipped as fresh, the industries-only
validation path, and `dry_run`.
"""

from __future__ import annotations

import asyncio
import gc
import logging
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

import app.services.industry_benchmark_service as ibs
import app.services.industry_moat_benchmark_service as imb
import app.services.notification_jobs as nj
from app import main as m
from app.api.v1.endpoints import admin
from app.services.industry_benchmark_service import (
    IndustryBenchmarkRecomputeSkipped,
    IndustryBenchmarkService,
)
from app.services.industry_moat_benchmark_service import (
    IndustryMoatBenchmarkRecomputeSkipped,
    IndustryMoatBenchmarkService,
)

# Raw universe-file entries (what `load_universe` returns), one usable industry each.
_MOAT_RAW = [{"industry": "Restaurants", "market_caps": {"MCD": 2.0e11, "YUM": 4.0e10}}]
_BENCH_RAW = [{
    "industry": "Restaurants", "sector": "Consumer Cyclical",
    "market_caps": {"MCD": 2.0e11, "YUM": 4.0e10},
}]
# Files that LOAD but hold nothing either loader can use — the same failure, no Storage error.
_MOAT_UNUSABLE = [{"industry": "Restaurants", "market_caps": {}}, {"industry": "", "market_caps": {"X": 1}}]
_BENCH_UNUSABLE = [
    {"industry": "Restaurants", "sector": "Unknown", "market_caps": {"MCD": 1.0}},
    {"industry": "Banks", "sector": "Financial Services", "market_caps": {}},
    {"industry": "Oil", "sector": "Energy", "market_caps": {"XOM": "N/A"}},
]


class _RecordingSB:
    """Any table access is recorded; upserts succeed. The empty paths must touch nothing."""

    def __init__(self) -> None:
        self.calls: List[str] = []
        self.upserts: List[Any] = []

    def table(self, name: str):
        self.calls.append(name)
        sb = self

        class _Q:
            def upsert(self, batch, **_k):
                sb.upserts.append(batch)
                return self

            def __getattr__(self, _attr):          # select / eq / order / limit / gte
                return lambda *a, **k: self

            def execute(self):
                return SimpleNamespace(data=[])

        return _Q()


# ── Service wiring ───────────────────────────────────────────────────────


def _moat(monkeypatch, raw) -> SimpleNamespace:
    """A moat service over `raw`, with the per-industry compute stubbed (no FMP)."""
    svc = IndustryMoatBenchmarkService.__new__(IndustryMoatBenchmarkService)
    svc.supabase = _RecordingSB()
    svc.fmp = None
    computed: List[str] = []

    async def _compute(ind, *, run_id=None, skip_if_fresh_hours=None, **_k):   # e.g. stats=
        computed.append(ind)
        return {"brand_power": {"avg": 6.0, "sample_size": 9, "p25": 5.0, "p75": 7.0}}

    # `_load_universe_industries` reads the module-level `load_universe` binding.
    monkeypatch.setattr(imb, "load_universe", lambda _f: list(raw))
    monkeypatch.setattr(svc, "compute_for_industry", _compute)
    monkeypatch.setattr(imb, "_service_singleton", svc)
    return SimpleNamespace(svc=svc, sb=svc.supabase, computed=computed)


def _bench(monkeypatch, raw, *, fresh: bool = False) -> SimpleNamespace:
    """A benchmark service over `raw`, fetches stubbed, writes recorded."""
    svc = IndustryBenchmarkService.__new__(IndustryBenchmarkService)
    svc.supabase = _RecordingSB()
    svc._calendar_quarter_blocked = False
    computed: List[str] = []
    ttm_industries: List[str] = []

    async def _compute_sector(sector, inds, al, ql, dry_run=False, **_k):   # tally=
        computed.append(sector)
        return 0 if dry_run else 7

    async def _ttm_values(ticker_caps, sem, **_k):                           # counts=
        ttm_industries.append(ticker_caps[0][0])
        return {"pe_ratio": [10.0, 11.0, 12.0, 13.0, 14.0]}   # >= MIN_SAMPLE_SIZE

    async def _industry_values(ticker_caps, al, ql, **_k):
        return {}

    # `_load_universe` reads the module-level `load_universe` binding — once the per-run
    # Storage fetch (`_fetch_benchmark_universe`, stubbed to "no fresh copy" here, which
    # also keeps the test off the network) has come back empty-handed.
    monkeypatch.setattr(ibs, "load_universe", lambda _f: list(raw))
    monkeypatch.setattr(ibs, "_fetch_benchmark_universe", lambda: None)
    monkeypatch.setattr(ibs, "_last_fetched_universe", None)
    monkeypatch.setattr(svc, "_compute_sector", _compute_sector)
    monkeypatch.setattr(svc, "_industry_value_lists", _industry_values)
    monkeypatch.setattr(svc, "_industry_ttm_values", _ttm_values)
    monkeypatch.setattr(svc, "_sector_is_fresh", lambda _s, _h: fresh)
    monkeypatch.setattr(svc, "_ttm_sector_is_fresh", lambda _s, _h: fresh)
    monkeypatch.setattr(ibs, "_industry_benchmark_service", svc)
    return SimpleNamespace(svc=svc, sb=svc.supabase, computed=computed, ttm=ttm_industries)


def _skip_errors(caplog, logger_name: str) -> List[logging.LogRecord]:
    return [
        r for r in caplog.records
        if r.name == logger_name and r.levelno >= logging.ERROR
        and "recompute SKIPPED" in r.getMessage()
    ]


# ── Moat: raises, logs, touches nothing ──────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [[], _MOAT_UNUSABLE], ids=["download-failed", "no-market-caps"])
@pytest.mark.parametrize("skip_hours", [None, 24])
async def test_moat_empty_universe_raises_logs_and_writes_nothing(monkeypatch, caplog, raw, skip_hours):
    w = _moat(monkeypatch, raw)

    with caplog.at_level(logging.ERROR, logger=imb.__name__):
        with pytest.raises(IndustryMoatBenchmarkRecomputeSkipped) as info:
            await w.svc.recompute_all(skip_if_fresh_hours=skip_hours)

    assert info.value.reason == "empty universe"
    assert "industry_universe.json" in str(info.value)
    assert len(_skip_errors(caplog, imb.__name__)) == 1
    assert w.computed == []
    assert w.sb.calls == [] and w.sb.upserts == []


@pytest.mark.asyncio
async def test_moat_non_empty_universe_returns_its_summary(monkeypatch, caplog):
    w = _moat(monkeypatch, _MOAT_RAW)
    with caplog.at_level(logging.ERROR, logger=imb.__name__):
        summary = await w.svc.recompute_all(skip_if_fresh_hours=24)
    assert summary["industries"] == 1 and summary["pillars_written"] == 1
    assert w.computed == ["Restaurants"]
    assert _skip_errors(caplog, imb.__name__) == []


@pytest.mark.asyncio
async def test_moat_every_industry_fresh_is_a_settled_return(monkeypatch):
    """`skip_if_fresh_hours` skips are legitimate: nothing written, no raise."""
    w = _moat(monkeypatch, _MOAT_RAW)

    async def _fresh(ind, *, run_id=None, skip_if_fresh_hours=None, **_k):
        return {"_skipped": "fresh"}

    monkeypatch.setattr(w.svc, "compute_for_industry", _fresh)
    summary = await w.svc.recompute_all(skip_if_fresh_hours=24)
    assert summary["skipped_fresh"] == 1 and summary["pillars_written"] == 0


# ── Fiscal benchmarks ────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [[], _BENCH_UNUSABLE], ids=["download-failed", "no-usable-sector"])
@pytest.mark.parametrize("sectors", [None, ["Technology"]])
async def test_fiscal_empty_universe_raises_logs_and_writes_nothing(monkeypatch, caplog, raw, sectors):
    w = _bench(monkeypatch, raw)

    with caplog.at_level(logging.ERROR, logger=ibs.__name__):
        with pytest.raises(IndustryBenchmarkRecomputeSkipped) as info:
            await w.svc.recompute_all(skip_if_fresh_hours=24, sectors=sectors)

    assert info.value.reason == "empty universe"
    assert "benchmark_universe.json" in str(info.value) and "fiscal" in str(info.value)
    assert len(_skip_errors(caplog, ibs.__name__)) == 1
    assert w.computed == []
    assert w.sb.calls == [] and w.sb.upserts == []


@pytest.mark.asyncio
async def test_fiscal_legitimate_returns_stay_returns(monkeypatch, caplog):
    with caplog.at_level(logging.ERROR, logger=ibs.__name__):
        # A healthy run.
        w = _bench(monkeypatch, _BENCH_RAW)
        summary = await w.svc.recompute_all(skip_if_fresh_hours=24)
        assert summary["sectors_done"] == 1 and summary["rows_upserted"] == 7

        # Every sector fresh — nothing written, still settled.
        w = _bench(monkeypatch, _BENCH_RAW, fresh=True)
        summary = await w.svc.recompute_all(skip_if_fresh_hours=24)
        assert summary["sectors_skipped_fresh"] == 1 and summary["sectors_done"] == 0

        # An operator's sector filter matching nothing is their input, not a failed file.
        w = _bench(monkeypatch, _BENCH_RAW)
        summary = await w.svc.recompute_all(sectors=["Technology"])
        assert summary["sectors_done"] == 0 and w.computed == []

        # dry_run and the industries-only validation path never raise on an empty universe.
        w = _bench(monkeypatch, [])
        summary = await w.svc.recompute_all(dry_run=True)
        assert summary["sectors_done"] == 0 and summary["dry_run"] is True
        summary = await w.svc.recompute_all(industries=["Restaurants"])
        assert summary["industries"] == 0
        assert w.sb.calls == []

    assert _skip_errors(caplog, ibs.__name__) == []


# ── TTM benchmarks ───────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [[], _BENCH_UNUSABLE], ids=["download-failed", "no-usable-sector"])
@pytest.mark.parametrize("sectors", [None, ["Technology"]])
async def test_ttm_empty_universe_raises_logs_and_writes_nothing(monkeypatch, caplog, raw, sectors):
    w = _bench(monkeypatch, raw)

    with caplog.at_level(logging.ERROR, logger=ibs.__name__):
        with pytest.raises(IndustryBenchmarkRecomputeSkipped) as info:
            await w.svc.recompute_all_ttm(skip_if_fresh_hours=24, sectors=sectors)

    assert info.value.reason == "empty universe"
    assert "TTM" in str(info.value)
    assert len(_skip_errors(caplog, ibs.__name__)) == 1
    assert w.ttm == []
    assert w.sb.calls == [] and w.sb.upserts == []


@pytest.mark.asyncio
async def test_ttm_legitimate_returns_stay_returns(monkeypatch, caplog):
    with caplog.at_level(logging.ERROR, logger=ibs.__name__):
        w = _bench(monkeypatch, _BENCH_RAW)
        summary = await w.svc.recompute_all_ttm(skip_if_fresh_hours=24)
        assert summary["sectors_done"] == 1 and summary["rows_upserted"] == 2   # industry + aggregate
        assert w.sb.calls == ["sector_benchmarks", "sector_benchmarks"]

        w = _bench(monkeypatch, _BENCH_RAW, fresh=True)
        summary = await w.svc.recompute_all_ttm(skip_if_fresh_hours=24)
        assert summary["sectors_skipped_fresh"] == 1 and summary["rows_upserted"] == 0

        w = _bench(monkeypatch, [])
        summary = await w.svc.recompute_all_ttm(dry_run=True)
        assert summary["mode"] == "ttm" and summary["sectors_done"] == 0
        summary = await w.svc.recompute_all_ttm(industries=["Restaurants"])
        assert summary["mode"] == "ttm-industries" and summary["industries_done"] == 0
        assert w.sb.calls == []

    assert _skip_errors(caplog, ibs.__name__) == []


def test_the_skip_exceptions_are_typed_runtime_errors_carrying_their_reason():
    for cls, prefix in (
        (IndustryMoatBenchmarkRecomputeSkipped, "industry_moat_benchmark recompute SKIPPED"),
        (IndustryBenchmarkRecomputeSkipped, "industry_benchmark recompute SKIPPED"),
    ):
        exc = cls("empty universe", "detail")
        assert isinstance(exc, RuntimeError)
        assert exc.reason == "empty universe"
        assert str(exc).startswith(f"{prefix} (empty universe)")


# ── Through the REAL claim helper: the run is not consumed ───────────────


def _real_claim_ledger(monkeypatch) -> Dict[str, Any]:
    """Keep `claimed_scheduled_job` real; stub only its two ledger RPCs."""
    ledger: Dict[str, Any] = {}

    def _claim(job, *, timezone_name="UTC", now=None, stale_seconds=None):
        ledger["claimed"] = (job, stale_seconds)
        return True

    def _finish(job, *, success, items=0, error=None, timezone_name="UTC", now=None):
        ledger["finished"] = {"job": job, "success": success, "error": error}

    monkeypatch.setattr(nj, "claim_scheduled", _claim)
    monkeypatch.setattr(nj, "finish_scheduled", _finish)
    return ledger


# Same shapes as main.py's `_moat`, `_benchmarks` and `_ttm` closures.
async def _moat_body():
    return await imb.get_industry_moat_benchmark_service().recompute_all(skip_if_fresh_hours=24)


async def _benchmarks_body():
    return await ibs.get_industry_benchmark_service().recompute_all(skip_if_fresh_hours=24)


async def _ttm_body():
    return await ibs.get_industry_benchmark_service().recompute_all_ttm(skip_if_fresh_hours=24)


_PHASES = [
    pytest.param(
        m.JOB_INDUSTRY_MOAT_QUARTERLY, _moat_body, _moat, _MOAT_RAW,
        "IndustryMoatBenchmarkRecomputeSkipped", id="moat-quarterly",
    ),
    pytest.param(
        m.JOB_INDUSTRY_BENCHMARK_QUARTERLY, _benchmarks_body, _bench, _BENCH_RAW,
        "IndustryBenchmarkRecomputeSkipped", id="benchmark-quarterly",
    ),
    pytest.param(
        m.JOB_TTM_BENCHMARK_WEEKLY, _ttm_body, _bench, _BENCH_RAW,
        "IndustryBenchmarkRecomputeSkipped", id="ttm-weekly",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("job,body,wire,_raw,exc_name", _PHASES)
async def test_an_empty_universe_leaves_the_scheduled_claim_unsettled(
    monkeypatch, job, body, wire, _raw, exc_name,
):
    w = wire(monkeypatch, [])
    ledger = _real_claim_ledger(monkeypatch)

    settled = await m._run_claimed_phase(job, f"{job} phase", body)

    assert settled is False, "an empty universe must be retried, not settle the run"
    assert ledger["claimed"] == (job, m._CHAIN_PHASE_STALE_SECONDS)
    finished = ledger["finished"]
    assert finished["job"] == job
    assert finished["success"] is False             # run_day stays unset → the day is retried
    assert finished["error"].startswith(f"{exc_name}:")
    assert "(empty universe)" in finished["error"]  # greppable in the ledger row
    assert w.sb.calls == [] and w.sb.upserts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("job,body,wire,raw,_exc_name", _PHASES)
async def test_a_non_empty_universe_still_settles_the_claim(
    monkeypatch, job, body, wire, raw, _exc_name,
):
    """Mutation twin: the guard must not make a healthy run look unsettled."""
    wire(monkeypatch, raw)
    ledger = _real_claim_ledger(monkeypatch)

    settled = await m._run_claimed_phase(job, f"{job} phase", body)

    assert settled is True
    assert ledger["finished"]["success"] is True
    assert ledger["finished"]["error"] is None


# ── The admin triggers: a raise in the background task is retrieved and logged ──


# The two sector/industry BENCHMARK triggers left this table on 2026-10-07: they now run
# under the quarterly phase's claim, and a `RecomputeSkipped` there is caught, logged at
# WARNING and released as an unsettled claim instead of failing the task. Their twin of this
# test (an empty universe through the route, no unretrieved task) is in
# `test_benchmark_producer_2026_10_07_admin.py`.
_ADMIN_ROUTES = [
    pytest.param(
        admin.refresh_industry_moat_benchmarks, {"skip_recent_hours": 24}, _moat,
        "admin_refresh_industry_moat_benchmarks", "IndustryMoatBenchmarkRecomputeSkipped",
        id="moat",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("route,kwargs,wire,task_name,exc_name", _ADMIN_ROUTES)
async def test_admin_trigger_logs_the_skip_and_never_leaks_an_unretrieved_task(
    monkeypatch, caplog, route, kwargs, wire, task_name, exc_name,
):
    wire(monkeypatch, [])
    monkeypatch.setattr(admin, "_authorize_admin", lambda _user, _token: None)
    loop = asyncio.get_running_loop()
    unhandled: List[Dict[str, Any]] = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, ctx: unhandled.append(ctx))
    try:
        with caplog.at_level(logging.ERROR, logger=admin.__name__):
            resp = await route(x_admin_token=None, user={}, **kwargs)
            assert resp["status"] == "started"
            (task,) = [t for t in admin._admin_tasks if t.get_name() == task_name]
            # `asyncio.wait` does NOT retrieve the exception — only the done-callback may.
            await asyncio.wait({task})
            await asyncio.sleep(0)                   # let the done-callback run
            assert task not in admin._admin_tasks
            del task
            gc.collect()
    finally:
        loop.set_exception_handler(previous)

    failed = [
        r for r in caplog.records
        if r.name == admin.__name__ and r.levelno >= logging.ERROR
        and f"{task_name!r} FAILED ({exc_name}:" in r.getMessage()
    ]
    assert len(failed) == 1
    assert not [c for c in unhandled if "never retrieved" in str(c.get("message", ""))]
