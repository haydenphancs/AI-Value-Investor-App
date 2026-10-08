"""A benchmark sweep that attempted work but WROTE NOTHING must leave its claim UNSETTLED.

Same bug class as `test_benchmark_empty_universe_unsettled.py` (2026-10-01): `_run_claimed_phase`
(main.py) settles any phase that RETURNS. A NON-empty universe whose fetches all fail still
returned a zero summary, because every fetch layer turns an FMP failure into an empty result:

  * fiscal `recompute_all` — `SectorBenchmarkService._fetch_company_data` maps each failed call
    to `[]`, so every sector "completes" with 0 rows (`sectors_done: N, rows_upserted: 0`); a
    sector that RAISES is caught per sector and the run still returned.
  * `recompute_all_ttm` — `_fetch_ttm`'s gather maps both failed calls to all-None values, which
    `_industry_ttm_values` drops; every industry `continue`s and the aggregate writes 0 rows.
  * moat `recompute_all` — `_score_one_ticker` returns None when the profile fetch fails, so
    every pillar is below the sample floor and every industry returns `{}`.

Each now raises its typed `*RecomputeSkipped` (reason "nothing written" or "every sector /
industry failed") after an ERROR log. Still returns: nothing attempted (every sector/industry
fresh), a run whose every attempted sector WROTE, `dry_run`, and — moat only — industries that
legitimately have too few scorable peers: they never write, so they are never fresh, and a
same-day re-run attempts exactly those.

Since 2026-10-07 a fiscal or TTM run in which some sector RAISED, or computed ZERO rows, while
others wrote raises `IndustryBenchmarkRecomputeIncomplete` (it used to settle the quarter / the
week — the old OWNER_TASKS row 7 and 8 policy); the same-day retry skips the sectors that
finished (`test_benchmark_producer_2026_10_07.py`, `test_benchmark_producer_round2_2026_10_07.py`).

The fetch paths are REAL: only the FMP client, Supabase and (moat) the scorer are faked.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Set

import pytest

import app.services.industry_benchmark_service as ibs
import app.services.industry_moat_benchmark_service as imb
import app.services.notification_jobs as nj
from app import main as m
from app.integrations.fmp import FMPUnavailableException
from app.services.industry_benchmark_service import (
    IndustryBenchmarkRecomputeSkipped,
    IndustryBenchmarkService,
)
from app.services.industry_moat_benchmark_service import (
    IndustryMoatBenchmarkRecomputeSkipped,
    IndustryMoatBenchmarkService,
)
from app.services.moat_scoring_service import PILLAR_ORDER
from app.services.sector_benchmark_service import SectorBenchmarkService

# Two sectors, five companies each — enough for MIN_SAMPLE_SIZE (5) when FMP answers.
_BENCH_RAW = [
    {"industry": "Restaurants", "sector": "Consumer Cyclical",
     "market_caps": {f"R{i}": 1.0e10 - i for i in range(5)}},
    {"industry": "Oil & Gas E&P", "sector": "Energy",
     "market_caps": {f"E{i}": 1.0e10 - i for i in range(5)}},
]
_SECTORS = ["Consumer Cyclical", "Energy"]

# Moat: two industries big enough to write, one that never can (3 < MIN_SAMPLE_SIZE peers).
_BIG = {"industry": "Restaurants", "market_caps": {f"R{i}": 1.0e10 - i for i in range(6)}}
_BIG2 = {"industry": "Oil & Gas E&P", "market_caps": {f"E{i}": 1.0e10 - i for i in range(6)}}
_SMALL = {"industry": "Shell Companies", "market_caps": {f"S{i}": 1.0e9 - i for i in range(3)}}


class _FakeFMP:
    """Every `get_*` call answers like FMP. Tickers in `down` (or all, when `down is None`
    and `outage`) raise the integration's transient error, as during an outage."""

    def __init__(self, *, outage: bool = False, down: Optional[Set[str]] = None) -> None:
        self.outage = outage
        self.down = down or set()
        self.calls = 0

    def __getattr__(self, name: str):
        if not name.startswith("get_"):
            raise AttributeError(name)

        async def _call(ticker, *a, **k):
            self.calls += 1
            if self.outage or ticker in self.down:
                raise FMPUnavailableException(f"{name}({ticker}): 503 Service Unavailable")
            return _answer(name, ticker, k.get("period", a[0] if a else None))

        return _call


def _answer(name: str, ticker: str, period: Optional[str]) -> Any:
    n = int(ticker[1:]) if ticker[1:].isdigit() else 0
    if name == "get_financial_ratios" and period == "annual":
        return [{"date": "2024-12-31", "calendarYear": "2024", "grossProfitMargin": 0.40 + n / 100}]
    if name == "get_ratios_ttm":
        return [{"grossProfitMarginTTM": 0.40 + n / 100}]
    if name == "get_company_profile":
        return {"symbol": ticker, "sector": "Consumer Cyclical", "industry": "Restaurants"}
    if name == "get_earning_call_transcript":
        return None
    return []


class _FakeSB:
    """Selects answer a fresh row for any `.eq(...)` value in `fresh`; upserts are recorded,
    or raise when a row's sector/industry is in `fail` (or always, with `fail_all`)."""

    def __init__(self, *, fresh: Set[str] = frozenset(), fail: Set[str] = frozenset(),
                 fail_all: bool = False) -> None:
        self.fresh, self.fail, self.fail_all = set(fresh), set(fail), fail_all
        self.upserts: List[Dict[str, Any]] = []

    def table(self, _name: str):
        sb = self

        class _Q:
            def __init__(self) -> None:
                self.eqs: List[Any] = []
                self.batch: Optional[List[Dict[str, Any]]] = None

            def upsert(self, batch, **_k):
                self.batch = batch if isinstance(batch, list) else [batch]
                return self

            def eq(self, _col, value):
                self.eqs.append(value)
                return self

            def __getattr__(self, _attr):          # select / order / limit / gte
                return lambda *a, **k: self

            def execute(self):
                if self.batch is not None:
                    keys = {r.get("sector") for r in self.batch} | {r.get("industry") for r in self.batch}
                    if sb.fail_all or keys & sb.fail:
                        raise RuntimeError("Server disconnected")
                    sb.upserts.extend(self.batch)
                    return SimpleNamespace(data=self.batch)
                if sb.fresh & set(self.eqs):
                    now = datetime.now(timezone.utc).isoformat()
                    return SimpleNamespace(data=[{"computed_at": now, "industry": self.eqs[0]}])
                return SimpleNamespace(data=[])

        return _Q()


# ── Wiring ───────────────────────────────────────────────────────────────


def _bench(monkeypatch, fmp: _FakeFMP, sb: _FakeSB) -> IndustryBenchmarkService:
    """The REAL fiscal + TTM fetch paths over `fmp`; only FMP and Supabase are faked."""
    sector_svc = SectorBenchmarkService.__new__(SectorBenchmarkService)
    sector_svc.fmp = fmp
    sector_svc.supabase = sb
    sector_svc._fmp_semaphore = asyncio.Semaphore(10)
    svc = IndustryBenchmarkService.__new__(IndustryBenchmarkService)
    svc.supabase = sb
    svc._sb = sector_svc
    svc._fmp = fmp
    svc._calendar_quarter_blocked = False
    monkeypatch.setattr(ibs, "load_universe", lambda _f: list(_BENCH_RAW))
    # No fresh Storage copy this run (and none from an earlier one): the universe comes from
    # `load_universe` above, and the test never reaches the network.
    monkeypatch.setattr(ibs, "_fetch_benchmark_universe", lambda: None)
    monkeypatch.setattr(ibs, "_last_fetched_universe", None)
    monkeypatch.setattr(ibs, "_industry_benchmark_service", svc)
    return svc


def _moat(monkeypatch, fmp: _FakeFMP, sb: _FakeSB, raw: List[Dict[str, Any]]) -> IndustryMoatBenchmarkService:
    """The REAL `compute_for_industry` / `_score_one_ticker` over `fmp`."""
    svc = IndustryMoatBenchmarkService.__new__(IndustryMoatBenchmarkService)
    svc.supabase = sb
    svc.fmp = fmp

    async def _no_tam(_industry, _sample):
        # The real one reaches the dossier service (Supabase) whenever a profile answers.
        return None

    def _score(**_k):
        return {p: SimpleNamespace(score=6.0) for p in PILLAR_ORDER}

    monkeypatch.setattr(svc, "_fetch_industry_tam", _no_tam)
    monkeypatch.setattr(imb, "score_moat_dimensions", _score)
    monkeypatch.setattr(imb, "load_universe", lambda _f: [dict(e) for e in raw])
    monkeypatch.setattr(imb, "_service_singleton", svc)
    return svc


def _records(caplog, logger_name: str, level: int, needle: str) -> List[logging.LogRecord]:
    return [
        r for r in caplog.records
        if r.name == logger_name and r.levelno == level and needle in r.getMessage()
    ]


# ── Fiscal `recompute_all` ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fiscal_fmp_outage_raises_nothing_written(monkeypatch, caplog):
    fmp, sb = _FakeFMP(outage=True), _FakeSB()
    svc = _bench(monkeypatch, fmp, sb)

    with caplog.at_level(logging.INFO, logger=ibs.__name__):
        with pytest.raises(IndustryBenchmarkRecomputeSkipped) as info:
            await svc.recompute_all(skip_if_fresh_hours=24)

    assert info.value.reason == "nothing written"
    assert "2 sector(s) attempted (0 raised, 2 computed zero rows)" in str(info.value)
    assert "fiscal" in str(info.value)
    assert fmp.calls > 0, "the real fetch path must have been exercised"
    assert sb.upserts == []
    assert len(_records(caplog, ibs.__name__, logging.ERROR, "recompute SKIPPED (nothing written)")) == 1
    # Each sector names its own zero-row degradation (one WARNING per sector).
    assert len(_records(caplog, ibs.__name__, logging.WARNING, "computed ZERO rows")) == 2
    assert _records(caplog, ibs.__name__, logging.INFO, "industry_benchmark complete") == []


@pytest.mark.asyncio
async def test_fiscal_every_sector_failing_raises_every_sector_failed(monkeypatch, caplog):
    svc = _bench(monkeypatch, _FakeFMP(), _FakeSB(fail_all=True))

    with caplog.at_level(logging.ERROR, logger=ibs.__name__):
        with pytest.raises(IndustryBenchmarkRecomputeSkipped) as info:
            await svc.recompute_all(skip_if_fresh_hours=24)

    assert info.value.reason == "every sector failed"
    assert "2 sector(s) attempted (2 raised, 0 computed zero rows)" in str(info.value)
    # Each sector's own failure line carries the exception type.
    assert len(_records(caplog, ibs.__name__, logging.ERROR, "failed: RuntimeError: Server disconnected")) == 2


@pytest.mark.asyncio
async def test_fiscal_one_raised_one_empty_is_nothing_written(monkeypatch):
    # Energy's FMP calls fail (0 rows); Consumer Cyclical's write fails (raises).
    fmp = _FakeFMP(down={f"E{i}" for i in range(5)})
    svc = _bench(monkeypatch, fmp, _FakeSB(fail={"Consumer Cyclical"}))

    with pytest.raises(IndustryBenchmarkRecomputeSkipped) as info:
        await svc.recompute_all(skip_if_fresh_hours=24)

    assert info.value.reason == "nothing written"
    assert "(1 raised, 1 computed zero rows)" in str(info.value)


@pytest.mark.asyncio
async def test_fiscal_partial_run_raises_incomplete(monkeypatch):
    """Policy since 2026-10-07: a partial run (one sector RAISED) no longer settles the
    quarter — it raises `IndustryBenchmarkRecomputeIncomplete`, carrying the summary, and
    the rows the healthy sector wrote stay written."""
    sb = _FakeSB(fail={"Energy"})
    svc = _bench(monkeypatch, _FakeFMP(), sb)

    with pytest.raises(ibs.IndustryBenchmarkRecomputeIncomplete) as info:
        await svc.recompute_all(skip_if_fresh_hours=24)

    assert info.value.failed_sectors == ["Energy"]
    summary = info.value.summary
    assert summary["sectors_done"] == 1 and summary["sectors_failed"] == 1
    assert summary["rows_upserted"] == 2            # Restaurants + its sector aggregate
    assert {r["sector"] for r in sb.upserts} == {"Consumer Cyclical"}


@pytest.mark.asyncio
async def test_fiscal_nothing_attempted_or_dry_run_still_returns(monkeypatch, caplog):
    with caplog.at_level(logging.ERROR, logger=ibs.__name__):
        # Every sector fresh: nothing attempted, nothing fetched — a settled no-op.
        fmp = _FakeFMP(outage=True)
        svc = _bench(monkeypatch, fmp, _FakeSB(fresh=set(_SECTORS)))
        summary = await svc.recompute_all(skip_if_fresh_hours=24)
        assert summary["sectors_skipped_fresh"] == 2 and summary["rows_upserted"] == 0
        assert fmp.calls == 0

        # dry_run writes nothing by design, even when it computed rows.
        for outage in (True, False):
            sb = _FakeSB()
            svc = _bench(monkeypatch, _FakeFMP(outage=outage), sb)
            summary = await svc.recompute_all(dry_run=True)
            assert summary["dry_run"] is True and summary["rows_upserted"] == 0
            assert sb.upserts == []

    assert _records(caplog, ibs.__name__, logging.ERROR, "recompute SKIPPED") == []


@pytest.mark.asyncio
async def test_fiscal_healthy_run_returns(monkeypatch):
    sb = _FakeSB()
    svc = _bench(monkeypatch, _FakeFMP(), sb)
    summary = await svc.recompute_all(skip_if_fresh_hours=24)
    assert summary["sectors_done"] == 2 and summary["sectors_failed"] == 0
    assert summary["rows_upserted"] == 4 == len(sb.upserts)


# ── `recompute_all_ttm` ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ttm_fmp_outage_raises_nothing_written(monkeypatch, caplog):
    fmp, sb = _FakeFMP(outage=True), _FakeSB()
    svc = _bench(monkeypatch, fmp, sb)

    with caplog.at_level(logging.WARNING, logger=ibs.__name__):
        with pytest.raises(IndustryBenchmarkRecomputeSkipped) as info:
            await svc.recompute_all_ttm(skip_if_fresh_hours=24)

    assert info.value.reason == "nothing written"
    assert "2 sector(s) attempted (0 raised, 2 computed zero rows)" in str(info.value)
    assert "TTM" in str(info.value)
    assert fmp.calls == 20                         # ratios-ttm + key-metrics-ttm × 10 tickers
    assert sb.upserts == []
    assert len(_records(caplog, ibs.__name__, logging.ERROR, "recompute SKIPPED (nothing written)")) == 1
    assert len(_records(caplog, ibs.__name__, logging.WARNING, "ttm: ")) == 2


@pytest.mark.asyncio
async def test_ttm_every_sector_failing_raises_every_sector_failed(monkeypatch, caplog):
    svc = _bench(monkeypatch, _FakeFMP(), _FakeSB(fail_all=True))
    with caplog.at_level(logging.ERROR, logger=ibs.__name__):
        with pytest.raises(IndustryBenchmarkRecomputeSkipped) as info:
            await svc.recompute_all_ttm(skip_if_fresh_hours=24)
    assert info.value.reason == "every sector failed"
    assert len(_records(caplog, ibs.__name__, logging.ERROR, "failed: RuntimeError: Server disconnected")) == 2


@pytest.mark.asyncio
async def test_ttm_partial_is_incomplete_while_nothing_attempted_dry_run_and_healthy_return(
    monkeypatch, caplog,
):
    with caplog.at_level(logging.ERROR, logger=ibs.__name__):
        # Partial: Energy's TTM fetches fail (zero rows), Consumer Cyclical writes. Since
        # 2026-10-07 that leaves the week's claim open so the same-day retry recomputes Energy;
        # it used to settle with Energy stale for a week.
        svc = _bench(monkeypatch, _FakeFMP(down={f"E{i}" for i in range(5)}), _FakeSB())
        with pytest.raises(ibs.IndustryBenchmarkRecomputeIncomplete) as info:
            await svc.recompute_all_ttm(skip_if_fresh_hours=24)
        assert info.value.empty_sectors == ["Energy"]
        assert info.value.summary["rows_upserted"] == 2

        fmp = _FakeFMP(outage=True)
        svc = _bench(monkeypatch, fmp, _FakeSB(fresh=set(_SECTORS)))
        summary = await svc.recompute_all_ttm(skip_if_fresh_hours=24)
        assert summary["sectors_skipped_fresh"] == 2 and fmp.calls == 0

        svc = _bench(monkeypatch, _FakeFMP(outage=True), _FakeSB())
        summary = await svc.recompute_all_ttm(dry_run=True)
        assert summary["dry_run"] is True and summary["rows_upserted"] == 0

        svc = _bench(monkeypatch, _FakeFMP(), _FakeSB())
        summary = await svc.recompute_all_ttm(skip_if_fresh_hours=24)
        assert summary["sectors_done"] == 2 and summary["sectors_failed"] == 0
        assert summary["rows_upserted"] == 4

    assert _records(caplog, ibs.__name__, logging.ERROR, "recompute SKIPPED") == []


# ── Moat `recompute_all` ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_moat_fmp_outage_raises_every_industry_failed(monkeypatch, caplog):
    fmp, sb = _FakeFMP(outage=True), _FakeSB()
    svc = _moat(monkeypatch, fmp, sb, [_BIG, _BIG2])

    with caplog.at_level(logging.WARNING, logger=imb.__name__):
        with pytest.raises(IndustryMoatBenchmarkRecomputeSkipped) as info:
            await svc.recompute_all(skip_if_fresh_hours=24)

    assert info.value.reason == "every industry failed"
    assert "2 industries attempted, 0 pillar rows written" in str(info.value)
    assert "no peer could be scored: 2" in str(info.value)
    assert fmp.calls > 0 and sb.upserts == []
    assert len(_records(caplog, imb.__name__, logging.ERROR, "recompute SKIPPED (every industry failed)")) == 1
    assert len(_records(caplog, imb.__name__, logging.WARNING, "peers could be scored")) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("raw,reason,counts", [
    ([_BIG], "every industry failed", "1 failed (every upsert failed: 1), 0 short of"),
    ([_BIG, _SMALL], "nothing written", "1 failed (every upsert failed: 1), 1 short of"),
], ids=["upsert-down", "upsert-down-plus-small-industry"])
async def test_moat_every_upsert_failing_raises(monkeypatch, raw, reason, counts):
    svc = _moat(monkeypatch, _FakeFMP(), _FakeSB(fail_all=True), raw)
    with pytest.raises(IndustryMoatBenchmarkRecomputeSkipped) as info:
        await svc.recompute_all(skip_if_fresh_hours=24)
    assert info.value.reason == reason
    assert counts in str(info.value)


@pytest.mark.asyncio
async def test_moat_fresh_industries_are_not_counted_as_attempted(monkeypatch):
    # Restaurants is fresh; the only industry attempted fails, so EVERY attempt failed.
    fmp = _FakeFMP(outage=True)
    svc = _moat(monkeypatch, fmp, _FakeSB(fresh={"Restaurants"}), [_BIG, _BIG2])
    with pytest.raises(IndustryMoatBenchmarkRecomputeSkipped) as info:
        await svc.recompute_all(skip_if_fresh_hours=24)
    assert info.value.reason == "every industry failed"
    assert "1 industry attempted, 0 pillar rows written" in str(info.value)


@pytest.mark.asyncio
async def test_moat_every_industry_raising_raises(monkeypatch):
    svc = _moat(monkeypatch, _FakeFMP(), _FakeSB(), [_BIG, _SMALL])

    async def _boom(ind, **_k):
        raise RuntimeError(f"boom {ind}")

    monkeypatch.setattr(svc, "compute_for_industry", _boom)
    with pytest.raises(IndustryMoatBenchmarkRecomputeSkipped) as info:
        await svc.recompute_all(skip_if_fresh_hours=24)
    assert info.value.reason == "every industry failed"
    assert "raised: 2" in str(info.value)


@pytest.mark.asyncio
async def test_moat_outage_plus_small_industry_is_nothing_written(monkeypatch):
    # FMP is down for the big industry; the small one answers but is short of peers.
    fmp = _FakeFMP(down={f"R{i}" for i in range(6)})
    svc = _moat(monkeypatch, fmp, _FakeSB(), [_BIG, _SMALL])
    with pytest.raises(IndustryMoatBenchmarkRecomputeSkipped) as info:
        await svc.recompute_all(skip_if_fresh_hours=24)
    assert info.value.reason == "nothing written"
    assert "1 failed (no peer could be scored: 1), 1 short of 5 scorable peers" in str(info.value)


@pytest.mark.asyncio
async def test_moat_same_day_rerun_over_only_small_industries_returns(monkeypatch, caplog):
    """The false positive a bare `pillars_written == 0` check would raise: after a full run
    the big industries are fresh, and the ones that can never reach the floor are the only
    ones attempted. Nothing failed, so nothing is retried."""
    sb = _FakeSB(fresh={"Restaurants"})
    svc = _moat(monkeypatch, _FakeFMP(), sb, [_BIG, _SMALL])

    with caplog.at_level(logging.WARNING, logger=imb.__name__):
        summary = await svc.recompute_all(skip_if_fresh_hours=24)

    assert summary["skipped_fresh"] == 1
    assert summary["pillars_written"] == 0 and summary["industries_failed"] == 0
    assert sb.upserts == []
    assert _records(caplog, imb.__name__, logging.ERROR, "recompute SKIPPED") == []
    assert _records(caplog, imb.__name__, logging.WARNING, "peers could be scored") == []


@pytest.mark.asyncio
async def test_moat_partial_fresh_and_healthy_runs_return(monkeypatch):
    # Partial: Oil & Gas's FMP calls fail; Restaurants writes its five pillars.
    sb = _FakeSB()
    svc = _moat(monkeypatch, _FakeFMP(down={f"E{i}" for i in range(6)}), sb, [_BIG, _BIG2])
    summary = await svc.recompute_all(skip_if_fresh_hours=24)
    assert summary["pillars_written"] == len(PILLAR_ORDER) and summary["industries_failed"] == 1
    assert {r["industry"] for r in sb.upserts} == {"Restaurants"}

    # Every industry fresh: nothing attempted, nothing fetched.
    fmp = _FakeFMP(outage=True)
    svc = _moat(monkeypatch, fmp, _FakeSB(fresh={"Restaurants", "Oil & Gas E&P"}), [_BIG, _BIG2])
    summary = await svc.recompute_all(skip_if_fresh_hours=24)
    assert summary["skipped_fresh"] == 2 and fmp.calls == 0

    sb = _FakeSB()
    svc = _moat(monkeypatch, _FakeFMP(), sb, [_BIG, _BIG2, _SMALL])
    summary = await svc.recompute_all(skip_if_fresh_hours=24)
    assert summary["pillars_written"] == 2 * len(PILLAR_ORDER) == len(sb.upserts)
    assert summary["industries_failed"] == 0


@pytest.mark.asyncio
async def test_compute_for_industry_reports_its_stats(monkeypatch):
    svc = _moat(monkeypatch, _FakeFMP(down={"R0"}), _FakeSB(), [_BIG])
    stats: Dict[str, int] = {"stale": 1}
    written = await svc.compute_for_industry("Restaurants", stats=stats)
    assert len(written) == len(PILLAR_ORDER)
    assert stats == {"stale": 1, "peers": 6, "scored": 5, "eligible": len(PILLAR_ORDER)}

    stats = {}
    assert await svc.compute_for_industry("Not In Universe", stats=stats) == {}
    assert stats == {"peers": 0, "scored": 0, "eligible": 0}


def test_zero_write_failure_classification():
    f = imb._zero_write_failure
    assert f(3, {"eligible": 0, "scored": 0}) is None                 # wrote something
    assert f(0, {"eligible": 2, "scored": 9}) == "every upsert failed"
    assert f(0, {"eligible": 0, "scored": 0}) == "no peer could be scored"
    assert f(0, {}) == "no peer could be scored"                     # an empty stats dict
    assert f(0, {"eligible": 0, "scored": 3}) is None                 # short of peers: legit


# ── Through the REAL claim helper ────────────────────────────────────────


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


def _wire_moat(monkeypatch, fmp, sb):
    return _moat(monkeypatch, fmp, sb, [_BIG, _BIG2])


_PHASES = [
    pytest.param(
        m.JOB_INDUSTRY_MOAT_QUARTERLY, _moat_body, _wire_moat,
        "IndustryMoatBenchmarkRecomputeSkipped", "(every industry failed)", id="moat-quarterly",
    ),
    pytest.param(
        m.JOB_INDUSTRY_BENCHMARK_QUARTERLY, _benchmarks_body, _bench,
        "IndustryBenchmarkRecomputeSkipped", "(nothing written)", id="benchmark-quarterly",
    ),
    pytest.param(
        m.JOB_TTM_BENCHMARK_WEEKLY, _ttm_body, _bench,
        "IndustryBenchmarkRecomputeSkipped", "(nothing written)", id="ttm-weekly",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("job,body,wire,exc_name,reason", _PHASES)
async def test_an_fmp_outage_leaves_the_scheduled_claim_unsettled(
    monkeypatch, job, body, wire, exc_name, reason,
):
    sb = _FakeSB()
    wire(monkeypatch, _FakeFMP(outage=True), sb)
    ledger = _real_claim_ledger(monkeypatch)

    settled = await m._run_claimed_phase(job, f"{job} phase", body)

    assert settled is False, "a run that wrote nothing must be retried, not settle the claim"
    assert ledger["claimed"] == (job, m._CHAIN_PHASE_STALE_SECONDS)
    finished = ledger["finished"]
    assert finished["job"] == job
    assert finished["success"] is False             # run_day stays unset → the day is retried
    assert finished["error"].startswith(f"{exc_name}:")
    assert reason in finished["error"]               # greppable in the ledger row
    assert sb.upserts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("job,body,wire,_exc_name,_reason", _PHASES)
async def test_a_healthy_run_still_settles_the_claim(
    monkeypatch, job, body, wire, _exc_name, _reason,
):
    """Mutation twin: the guard must not make a healthy run look unsettled."""
    sb = _FakeSB()
    wire(monkeypatch, _FakeFMP(), sb)
    ledger = _real_claim_ledger(monkeypatch)

    settled = await m._run_claimed_phase(job, f"{job} phase", body)

    assert settled is True
    assert ledger["finished"]["success"] is True and ledger["finished"]["error"] is None
    assert sb.upserts


@pytest.mark.asyncio
async def test_a_small_industries_only_moat_rerun_settles_the_claim(monkeypatch):
    sb = _FakeSB(fresh={"Restaurants"})
    _moat(monkeypatch, _FakeFMP(), sb, [_BIG, _SMALL])
    ledger = _real_claim_ledger(monkeypatch)

    settled = await m._run_claimed_phase(m.JOB_INDUSTRY_MOAT_QUARTERLY, "moat phase", _moat_body)

    assert settled is True
    assert ledger["finished"]["success"] is True
