"""The quarterly industry-moat batch leaves out a company scored without its sector medians.

`score_moat_dimensions` returns `MoatScores` with `lookup_failed = True` when the sector
median read failed (a Supabase error that outlasted the lookup's retry). Its benchmark-driven
pillars are then unscored, and averaging that company into its industry's peer moat values
would drag them toward the pillars that needed no benchmark — with nothing in the logs.
Since 2026-10-07 `_score_one_ticker` drops such a company (WARNING) and keeps a clean one.
Hermetic: fake FMP, stubbed scorer.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from app.services import industry_moat_benchmark_service as imb
from app.services.moat_scoring_service import PILLAR_ORDER, MoatScores, PillarResult


class _FakeFMP:
    async def get_company_profile(self, ticker):
        return {"symbol": ticker, "sector": "Technology", "industry": "Semiconductors"}

    async def get_income_statement(self, *_a, **_k):
        return [{"date": "2025-12-31", "revenue": 100.0}]

    async def get_balance_sheet(self, *_a, **_k):
        return []

    async def get_financial_ratios(self, *_a, **_k):
        return []

    async def get_earning_call_transcript(self, *_a, **_k):
        return None


def _service() -> imb.IndustryMoatBenchmarkService:
    svc = imb.IndustryMoatBenchmarkService.__new__(imb.IndustryMoatBenchmarkService)
    svc.fmp = _FakeFMP()
    return svc


def _scores(failed: bool) -> MoatScores:
    first = PILLAR_ORDER[0]
    result = MoatScores({first: PillarResult(name=first, score=7.0)})
    result.lookup_failed = failed
    return result


@pytest.mark.asyncio
async def test_a_company_scored_without_sector_medians_is_left_out(monkeypatch, caplog):
    monkeypatch.setattr(imb, "score_moat_dimensions", lambda **_k: _scores(failed=True))
    with caplog.at_level(logging.WARNING, logger=imb.logger.name):
        out = await _service()._score_one_ticker("NVDA", asyncio.Semaphore(1))
    assert out is None
    assert any("sector-median read FAILED" in r.getMessage() and "NVDA" in r.getMessage()
               for r in caplog.records)


@pytest.mark.asyncio
async def test_a_clean_company_is_still_scored(monkeypatch):
    monkeypatch.setattr(imb, "score_moat_dimensions", lambda **_k: _scores(failed=False))
    out = await _service()._score_one_ticker("NVDA", asyncio.Semaphore(1))
    assert out is not None
    assert set(out) == set(PILLAR_ORDER)
    assert out[PILLAR_ORDER[0]] == 7.0


# ═══════════════════════════════════════════════════════════════════════════
# Review 2026-10-07 round 2 (F4): such a drop is COUNTED, and when it is why an industry
# or the whole run wrote nothing, the logs and the raise name the sector-median read —
# not FMP, which is what an operator debugged before.
# ═══════════════════════════════════════════════════════════════════════════

import math  # noqa: E402
import uuid  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from typing import Any, Dict, List, Set  # noqa: E402

import httpx  # noqa: E402

from app.services.sector_benchmark_lookup import BenchmarkLookupFailed, lookup_failed  # noqa: E402
from app.utils import supabase_errors  # noqa: E402

_PEERS = {f"T{i}": 1.0e10 - i for i in range(6)}


class _SB:
    """No industry is fresh; every upsert is recorded."""

    def __init__(self) -> None:
        self.upserts: List[Dict[str, Any]] = []

    def table(self, _name):
        sb = self

        class _Q:
            batch = None

            def upsert(self, row, **_k):
                self.batch = row
                return self

            def __getattr__(self, _attr):          # select / eq / gte / limit
                return lambda *a, **k: self

            def execute(self):
                if self.batch is not None:
                    sb.upserts.append(self.batch)
                    return SimpleNamespace(data=[self.batch])
                return SimpleNamespace(data=[])

        return _Q()


def _batch(monkeypatch, *, failed: Set[str], universe: List[Dict[str, Any]]):
    """The REAL compute_for_industry / recompute_all / _score_one_ticker; the scorer says
    `lookup_failed` for the tickers in `failed`."""
    svc = _service()
    svc.supabase = _SB()

    async def _no_tam(_industry, _sample):
        return None

    def _score(**kw):
        return _scores(failed=kw["profile"]["symbol"] in failed)

    # `_FakeFMP.get_company_profile` names the ticker; every profile answers.
    monkeypatch.setattr(svc, "_fetch_industry_tam", _no_tam)
    monkeypatch.setattr(imb, "score_moat_dimensions", _score)
    monkeypatch.setattr(imb, "load_universe", lambda _f: [dict(e) for e in universe])
    return svc


def _warnings(caplog, needle: str) -> List[str]:
    return [r.getMessage() for r in caplog.records
            if r.name == imb.logger.name and r.levelno == logging.WARNING
            and needle in r.getMessage()]


@pytest.mark.asyncio
async def test_every_peer_dropped_names_the_sector_median_read(monkeypatch, caplog):
    svc = _batch(monkeypatch, failed=set(_PEERS),
                 universe=[{"industry": "Semiconductors", "market_caps": _PEERS}])
    stats: Dict[str, int] = {}
    with caplog.at_level(logging.WARNING, logger=imb.logger.name):
        written = await svc.compute_for_industry("Semiconductors", stats=stats)
    assert written == {}
    assert stats == {"peers": 6, "scored": 0, "eligible": 0, "benchmark_failed": 6}
    assert imb._zero_write_failure(0, stats) == "sector-median read failed"
    assert _warnings(caplog, "6 of 6 peer(s) left out because their sector-median read failed")


@pytest.mark.asyncio
async def test_a_run_that_wrote_nothing_for_a_benchmark_outage_says_so(monkeypatch, caplog):
    universe = [{"industry": "Semiconductors", "market_caps": _PEERS},
                {"industry": "Software", "market_caps": {f"S{i}": 1.0 for i in range(6)}}]
    svc = _batch(monkeypatch, failed=set(_PEERS) | {f"S{i}" for i in range(6)},
                 universe=universe)
    with caplog.at_level(logging.WARNING, logger=imb.logger.name):
        with pytest.raises(imb.IndustryMoatBenchmarkRecomputeSkipped) as info:
            await svc.recompute_all(skip_if_fresh_hours=24)
    text = str(info.value)
    assert info.value.reason == "every industry failed"
    assert "2 failed (sector-median read failed: 2)" in text
    assert "sector-median read failed (Supabase sector_benchmarks unreadable)" in text
    assert "FMP" not in text, "the operator must not be sent to debug FMP"
    lines = _warnings(caplog, "no pillar written: the sector-median read failed")
    assert len(lines) == 2 and not any("FMP unreachable" in line for line in lines)
    assert not _warnings(caplog, "peers could be scored")


@pytest.mark.asyncio
async def test_an_fmp_outage_still_names_fmp(monkeypatch):
    """The other cause keeps its own words (the two must not merge)."""
    universe = [{"industry": "Semiconductors", "market_caps": _PEERS}]
    svc = _batch(monkeypatch, failed=set(), universe=universe)

    async def _no_profile(_ticker):
        return None

    monkeypatch.setattr(svc.fmp, "get_company_profile", _no_profile)
    with pytest.raises(imb.IndustryMoatBenchmarkRecomputeSkipped) as info:
        await svc.recompute_all(skip_if_fresh_hours=24)
    assert "no peer could be scored: 1" in str(info.value)
    assert "FMP was unreachable or refusing" in str(info.value)
    assert "sector-median" not in str(info.value)


@pytest.mark.asyncio
async def test_a_partial_drop_writes_and_is_counted_in_the_summary(monkeypatch, caplog):
    svc = _batch(monkeypatch, failed={"T0"},
                 universe=[{"industry": "Semiconductors", "market_caps": _PEERS}])
    with caplog.at_level(logging.WARNING, logger=imb.logger.name):
        summary = await svc.recompute_all(skip_if_fresh_hours=24)
    assert summary["pillars_written"] == 1          # the one pillar the stub scores
    assert summary["industries_failed"] == 0
    assert summary["companies_benchmark_failed"] == 1
    assert summary["industries_benchmark_failed"] == 1
    assert {r["sample_size"] for r in svc.supabase.upserts} == {5}
    assert _warnings(caplog, "1 peer(s) in 1 industry left out because their sector-median")


@pytest.mark.asyncio
async def test_a_clean_run_reports_zero_and_keeps_three_stats_keys(monkeypatch):
    svc = _batch(monkeypatch, failed=set(),
                 universe=[{"industry": "Semiconductors", "market_caps": _PEERS}])
    stats: Dict[str, int] = {"benchmark_failed": 9}       # left over from a reused dict
    await svc.compute_for_industry("Semiconductors", stats=stats)
    assert stats == {"peers": 6, "scored": 6, "eligible": 1}
    summary = await svc.recompute_all(skip_if_fresh_hours=24)
    assert summary["companies_benchmark_failed"] == 0
    assert summary["industries_benchmark_failed"] == 0


@pytest.mark.asyncio
async def test_the_counter_counts_only_a_benchmark_drop(monkeypatch):
    monkeypatch.setattr(imb, "score_moat_dimensions", lambda **_k: _scores(failed=True))
    drops: Dict[str, int] = {}
    assert await _service()._score_one_ticker("NVDA", asyncio.Semaphore(1), drops=drops) is None
    assert drops == {"benchmark_failed": 1}
    monkeypatch.setattr(imb, "score_moat_dimensions", lambda **_k: _scores(failed=False))
    assert await _service()._score_one_ticker("NVDA", asyncio.Semaphore(1), drops=drops)
    assert drops == {"benchmark_failed": 1}, "a clean company is not counted"


@pytest.mark.parametrize("stats, expected", [
    ({"eligible": 0, "scored": 0, "benchmark_failed": 6}, "sector-median read failed"),
    ({"eligible": 0, "scored": 0, "benchmark_failed": 1}, "sector-median read failed"),
    # scored + dropped would have reached the sample floor → the drop is the cause
    ({"eligible": 0, "scored": imb.MIN_SAMPLE_SIZE - 1, "benchmark_failed": 1},
     "sector-median read failed"),
    # …but short of the floor even with every dropped peer: a small industry, not a failure
    ({"eligible": 0, "scored": 2, "benchmark_failed": 1}, None),
    ({"eligible": 2, "scored": 0, "benchmark_failed": 6}, "every upsert failed"),
    ({"eligible": 0, "scored": 0}, "no peer could be scored"),
    ({"eligible": 0, "scored": 0, "benchmark_failed": 0}, "no peer could be scored"),
    ({"eligible": 0, "scored": 3}, None),
])
def test_zero_write_failure_names_the_benchmark_read(stats, expected):
    assert imb._zero_write_failure(0, stats) == expected
    assert imb._zero_write_failure(1, stats) is None


# ═══════════════════════════════════════════════════════════════════════════
# F3: the industry pillar peer-average READ flags a failure (never the same {} as "no
# rows"), is never cached, and serves one scorer vintage per industry (F2)
# ═══════════════════════════════════════════════════════════════════════════


class _ReadSB:
    def __init__(self, answers):
        self.answers = list(answers)      # each: a list of rows, or an exception to raise
        self.selected: List[str] = []

    def table(self, _name):
        sb = self

        class _Q:
            def select(self, cols, *a, **k):
                sb.selected.append(cols)
                return self

            def eq(self, *_a):
                return self

            def execute(self):
                answer = sb.answers.pop(0)
                if isinstance(answer, BaseException):
                    raise answer
                return SimpleNamespace(data=answer)

        return _Q()


def _lookup(monkeypatch, answers):
    monkeypatch.setattr(supabase_errors.time, "sleep", lambda _s: None)
    lk = imb.IndustryMoatBenchmarkLookup.__new__(imb.IndustryMoatBenchmarkLookup)
    lk.supabase = _ReadSB(answers)
    return lk


@pytest.fixture
def industry():
    name = f"Test Industry {uuid.uuid4().hex[:8]}"
    yield name
    imb._lookup_cache.pop(name, None)


_V = imb.MODEL_VERSION
_OLD = "moat_v1.2026-05"


def _rows(version, scores):
    return [{"pillar_name": p, "peer_average_score": s, "model_version": version}
            for p, s in scores.items()]


def test_a_failed_read_is_flagged_logged_and_not_cached(monkeypatch, caplog, industry):
    lk = _lookup(monkeypatch, [RuntimeError("permission denied"), _rows(_V, {"Brand": 6.0})])
    with caplog.at_level(logging.WARNING, logger=imb.logger.name):
        out = lk.get_pillar_benchmarks(industry)
    assert out == {} and lookup_failed(out) and isinstance(out, BenchmarkLookupFailed)
    assert industry not in imb._lookup_cache
    errs = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errs and "RuntimeError: permission denied" in errs[0].getMessage()
    assert industry in errs[0].getMessage() and errs[0].exc_info
    # The next request re-reads instead of being served the failure.
    again = lk.get_pillar_benchmarks(industry)
    assert again == {"Brand": 6.0} and not lookup_failed(again)


def test_a_transient_blip_is_retried(monkeypatch, industry):
    blip = httpx.RemoteProtocolError("Server disconnected without sending a response.")
    assert supabase_errors.is_transient_supabase_error(blip)
    lk = _lookup(monkeypatch, [blip, _rows(_V, {"Brand": 6.0})])
    out = lk.get_pillar_benchmarks(industry)
    assert out == {"Brand": 6.0} and not lookup_failed(out)


def test_a_transient_outage_is_flagged_at_warning(monkeypatch, caplog, industry):
    blip = httpx.RemoteProtocolError("Server disconnected")
    lk = _lookup(monkeypatch, [blip, blip, blip])
    with caplog.at_level(logging.WARNING, logger=imb.logger.name):
        out = lk.get_pillar_benchmarks(industry)
    assert lookup_failed(out)
    final = [r for r in caplog.records if "lookup FAILED" in r.getMessage()]
    assert len(final) == 1 and final[0].levelno == logging.WARNING


def test_no_rows_is_an_answer_and_is_cached(monkeypatch, industry):
    lk = _lookup(monkeypatch, [[]])
    out = lk.get_pillar_benchmarks(industry)
    assert out == {} and not lookup_failed(out)
    assert lk.get_pillar_benchmarks(industry) == {}       # served from the cache (no 2nd answer)
    assert lk.supabase.selected == ["pillar_name,peer_average_score,model_version"]


def test_an_empty_industry_name_reads_nothing(monkeypatch):
    lk = _lookup(monkeypatch, [])
    assert lk.get_pillar_benchmarks("") == {} and lk.supabase.selected == []


def test_each_pillar_keeps_its_own_newest_vintage(monkeypatch, industry):
    # Round-3 review (RPT3-5, 2026-10-08) superseded "never mix two vintages": a pillar a
    # partial recompute did not rewrite keeps its old-vintage peer average instead of
    # falling to the flat 5.0 placeholder; a rewritten pillar uses its new row.
    rows = (_rows(_V, {"Brand": 6.0, "Cost": 4.0})
            + _rows(_OLD, {"Network": 9.0, "Brand": 1.0}))
    out = _lookup(monkeypatch, [rows]).get_pillar_benchmarks(industry)
    assert out == {"Brand": 6.0, "Cost": 4.0, "Network": 9.0}


def test_an_industry_holding_only_the_old_vintage_keeps_it_whole(monkeypatch, caplog, industry):
    rows = _rows(_OLD, {"Brand": 6.0, "Cost": 4.0})
    with caplog.at_level(logging.INFO, logger=imb.logger.name):
        out = _lookup(monkeypatch, [rows]).get_pillar_benchmarks(industry)
    assert out == {"Brand": 6.0, "Cost": 4.0}
    assert any("recompute pending" in r.getMessage() and industry in r.getMessage()
               for r in caplog.records)


def test_an_unknown_stamp_ranks_below_every_known_vintage(monkeypatch, industry):
    rows = (_rows(None, {"Brand": 1.0}) + _rows("moat_v0", {"Cost": 1.0})
            + _rows(_OLD, {"Network": 7.0}))
    # Per pillar (RPT3-5): an unknown stamp ranks below every known vintage, so it only
    # serves a pillar no known vintage covers (production holds none: 474 rows, all
    # moat_v1.2026-05, checked 2026-10-08).
    assert _lookup(monkeypatch, [rows]).get_pillar_benchmarks(industry) == {
        "Brand": 1.0, "Cost": 1.0, "Network": 7.0,
    }
    twin = _rows(None, {"Network": 1.0}) + _rows(_OLD, {"Network": 7.0})
    # (another industry name: the lookup caches per industry)
    assert _lookup(monkeypatch, [twin]).get_pillar_benchmarks(f"{industry} twin") == {"Network": 7.0}


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), True, "abc", [], {}])
def test_a_malformed_score_is_skipped(monkeypatch, industry, bad):
    rows = _rows(_V, {"Brand": bad, "Cost": "4.5"}) + ["junk", None] + [
        {"pillar_name": ["Network"], "peer_average_score": 3.0, "model_version": _V},
        {"pillar_name": "", "peer_average_score": 3.0, "model_version": _V},
    ]
    out = _lookup(monkeypatch, [rows]).get_pillar_benchmarks(industry)
    assert out == {"Cost": 4.5}
    assert all(math.isfinite(v) for v in out.values())


def test_the_model_version_was_bumped_for_the_same_year_rule():
    """Peer averages scored under the old year rule must be distinguishable from the new."""
    assert imb.MODEL_VERSION != _OLD
    assert imb._MODEL_VERSION_ORDER[-1] == imb.MODEL_VERSION
    assert _OLD in imb._MODEL_VERSION_ORDER
