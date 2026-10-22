"""The industry-moat recompute survives FMP rate limits instead of silently dropping peers.

On 2026-10-21 a production moat recompute (run from a laptop, 3 companies in flight) hit FMP's
per-minute cap 201 times in 3 minutes. The moat path had no 429 handling: the first 429 on a
peer's profile dropped the peer, a 429 on one of its statements scored it on the statements
that did arrive, and a failed profile of the industry's largest company (the dossier sample)
left the whole industry's Network Effects pillar unscored — none of it counted or named.

Since then every moat FMP request goes through the fiscal medians' helper
(`sector_benchmark_service.call_with_rate_limit_retry`: burst retries, then one shared
per-minute window) inside one `rate_limit_run("moat")`; a peer whose call still fails is
counted (`fetch_failures_by_kind`, `companies_fetch_lost`) and its industry named at WARNING;
the dossier sample falls back to the next-largest company.

Time is VIRTUAL (`_VirtualTime` from the round-4 window test), so the 2-4-8 s burst retries
and 60 s windows cost nothing. Hermetic: a scripted FMP fake, a stubbed scorer, an in-memory
Supabase, a stubbed dossier service.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest

import app.services.industry_dossier_service as ids
import app.services.industry_moat_benchmark_service as imb
import app.services.sector_benchmark_service as sbs
from app.integrations.fmp import (
    FMPNotEntitledException,
    FMPRateLimitException,
    FMPUnavailableException,
)
from app.services.moat_scoring_service import PILLAR_ORDER, MoatScores, PillarResult
from tests.test_benchmark_producer_round4_rate_limit_window import _VirtualTime
from tests.test_industry_moat_lookup_failed_2026_10_07 import _SB

_INDUSTRY = "Semiconductors"
_PEERS = {f"T{i}": 1.0e10 - i for i in range(6)}           # T0 is the largest
_UNIVERSE = [{"industry": _INDUSTRY, "market_caps": _PEERS}]
_RATIOS = [{"date": "2025-12-31", "grossProfitMargin": 0.5}]
_SCORED_PILLAR = PILLAR_ORDER[0]


@pytest.fixture
def vt(monkeypatch) -> _VirtualTime:
    """Virtual time, and the process-wide 429 state reset (restored after the test)."""
    clock = _VirtualTime()
    monkeypatch.setattr(sbs.asyncio, "sleep", clock.sleep)      # asyncio.sleep, everywhere
    monkeypatch.setattr(sbs, "_rate_limit_clock", clock.clock)
    monkeypatch.setattr(sbs, "_exhausted_in_a_row", 0)
    monkeypatch.setattr(sbs, "_window", sbs._SharedWindow())
    monkeypatch.setattr(sbs.random, "uniform", lambda a, b: 0.0)   # no jitter
    return clock


def _answer(name: str, ticker: str) -> Any:
    if name == "get_company_profile":
        return {"symbol": ticker, "sector": "Technology", "industry": _INDUSTRY}
    if name == "get_income_statement":
        return [{"date": "2025-12-31", "revenue": 100.0}]
    if name == "get_balance_sheet":
        return [{"date": "2025-12-31", "totalAssets": 50.0}]
    if name == "get_financial_ratios":
        return list(_RATIOS)
    if name == "get_earning_call_transcript":
        return ""
    raise AssertionError(f"the moat path called an unexpected FMP method {name}")


class _ScriptFMP:
    """Answers like FMP. `script[(method, ticker)]` is consumed one item per call (an
    exception is raised, anything else answered), then the normal answer; `always[(method,
    ticker)]` is raised on every call. Records every call as (method, ticker)."""

    def __init__(self, script: Optional[Dict[Tuple[str, str], List[Any]]] = None,
                 always: Optional[Dict[Tuple[str, str], BaseException]] = None) -> None:
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.always = dict(always or {})
        self.calls: List[Tuple[str, str]] = []

    def __getattr__(self, name: str):
        if not name.startswith("get_"):
            raise AttributeError(name)

        async def call(ticker, *_a, **_k):
            self.calls.append((name, ticker))
            key = (name, ticker)
            if key in self.always:
                raise self.always[key]
            queue = self.script.get(key)
            if queue:
                item = queue.pop(0)
                if isinstance(item, BaseException):
                    raise item
                return item
            return _answer(name, ticker)

        call.__name__ = name
        return call

    def count(self, name: str, ticker: str) -> int:
        return self.calls.count((name, ticker))


class _Dossier:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: List[Tuple[str, str]] = []

    async def get_or_compute_dossier(self, *, industry, sector):
        self.calls.append((industry, sector))
        if self.fail:
            raise RuntimeError("dossier read failed")
        return SimpleNamespace(hhi=1500.0, lifecycle_phase="growth")


def _429() -> FMPRateLimitException:
    return FMPRateLimitException("FMP rate limit hit on profile")


def _wire(monkeypatch, fmp: _ScriptFMP, *, dossier_fails: bool = False):
    """The REAL recompute_all / compute_for_industry / _score_one_ticker /
    _fetch_industry_tam over `fmp`; the scorer records what it was given per symbol."""
    svc = imb.IndustryMoatBenchmarkService.__new__(imb.IndustryMoatBenchmarkService)
    svc.fmp = fmp
    svc.supabase = _SB()
    seen: Dict[str, Dict[str, Any]] = {}
    runs: List[Optional[str]] = []

    def _score(**kw):
        seen[kw["profile"]["symbol"]] = kw
        run = sbs.current_rate_limit_run()
        runs.append(run.label if run is not None else None)
        return MoatScores({_SCORED_PILLAR: PillarResult(name=_SCORED_PILLAR, score=7.0)})

    dossier = _Dossier(fail=dossier_fails)
    monkeypatch.setattr(imb, "score_moat_dimensions", _score)
    monkeypatch.setattr(imb, "load_universe", lambda _f: [dict(e) for e in _UNIVERSE])
    # `_fetch_industry_tam` imports the getter function-scoped: patch the SOURCE module.
    monkeypatch.setattr(ids, "get_industry_dossier_service", lambda: dossier)
    return SimpleNamespace(svc=svc, seen=seen, runs=runs, dossier=dossier)


def _warnings(caplog, needle: str) -> List[str]:
    return [r.getMessage() for r in caplog.records
            if r.name == imb.logger.name and r.levelno == logging.WARNING
            and needle in r.getMessage()]


def _sample_sizes(w) -> set:
    return {row["sample_size"] for row in w.svc.supabase.upserts}


# ═══ A 429 is retried and the peer counts ════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_429_on_a_peers_profile_is_retried_and_the_peer_counts(monkeypatch, vt):
    fmp = _ScriptFMP(script={("get_company_profile", "T3"): [_429(), _429()]})
    w = _wire(monkeypatch, fmp)
    summary = await vt.run(w.svc.recompute_all(skip_if_fresh_hours=24))
    assert "T3" in w.seen, "the peer was dropped on its first 429"
    assert fmp.count("get_company_profile", "T3") == 3
    assert 2.0 in vt.slept and 4.0 in vt.slept           # the burst back-off ran
    assert _sample_sizes(w) == {6}
    assert summary["companies_fetch_lost"] == 0 and summary["fetch_failures"] == 0
    assert summary["industries_fetch_lost"] == 0


@pytest.mark.asyncio
async def test_a_429_on_a_statement_is_retried_and_scored_on_the_real_rows(monkeypatch, vt):
    fmp = _ScriptFMP(script={("get_financial_ratios", "T2"): [_429()]})
    w = _wire(monkeypatch, fmp)
    summary = await vt.run(w.svc.recompute_all(skip_if_fresh_hours=24))
    assert w.seen["T2"]["ratios"] == _RATIOS, "scored on what arrived, not the real rows"
    assert fmp.count("get_financial_ratios", "T2") == 2
    assert _sample_sizes(w) == {6} and summary["companies_fetch_lost"] == 0


@pytest.mark.asyncio
async def test_the_dossier_sample_profile_is_retried_too(monkeypatch, vt):
    fmp = _ScriptFMP(script={("get_company_profile", "T0"): [_429()]})
    w = _wire(monkeypatch, fmp)
    drops: Dict[str, int] = {}
    tam = await vt.run(w.svc._fetch_industry_tam(_INDUSTRY, list(_PEERS), drops=drops))
    assert tam is not None and drops == {}
    assert [c for c in fmp.calls if c[0] == "get_company_profile"] == [
        ("get_company_profile", "T0"), ("get_company_profile", "T0"),
    ], "the largest company's sector should be used once its 429 was retried"
    assert w.dossier.calls == [(_INDUSTRY, "Technology")]


# ═══ What still fails is counted, named, and never half-scored ═══════════════════════════


@pytest.mark.asyncio
async def test_a_persistent_429_is_waited_out_then_counted_and_named(monkeypatch, vt, caplog):
    fmp = _ScriptFMP(always={("get_company_profile", "T4"): _429()})
    w = _wire(monkeypatch, fmp)
    with caplog.at_level(logging.WARNING, logger=imb.logger.name):
        summary = await vt.run(w.svc.recompute_all(skip_if_fresh_hours=24))
    assert "T4" not in w.seen
    # 4 burst attempts, then a retry after each of its RATE_LIMIT_LOCKOUT_WINDOWS windows.
    assert fmp.count("get_company_profile", "T4") == (
        sbs.RATE_LIMIT_MAX_RETRIES + 1 + sbs.RATE_LIMIT_LOCKOUT_WINDOWS
    )
    assert summary["rate_limit_windows"] >= 1
    assert summary["fetch_failures"] == 1
    assert summary["fetch_failures_by_kind"] == {
        "rate_limited": 1, "unavailable": 0, "error": 0, "refused": 0,
    }
    assert summary["companies_fetch_lost"] == 1 and summary["industries_fetch_lost"] == 1
    assert summary["industries_failed"] == 0
    assert _sample_sizes(w) == {5}                         # written from the 5 scored
    run_lines = _warnings(caplog, "1 peer(s) in 1 industry left out of the peer averages")
    assert run_lines and f"{_INDUSTRY} 1/6 (1 rate-limited, 0 unavailable)" in run_lines[0]
    assert _warnings(caplog, f"'{_INDUSTRY}' — 1 of 6 peer(s) left out because an FMP call")


@pytest.mark.asyncio
async def test_a_persistent_5xx_on_a_statement_leaves_the_peer_out_not_half_scored(
    monkeypatch, vt, caplog,
):
    fmp = _ScriptFMP(always={("get_income_statement", "T1"): FMPUnavailableException("503")})
    w = _wire(monkeypatch, fmp)
    with caplog.at_level(logging.WARNING, logger=imb.logger.name):
        summary = await vt.run(w.svc.recompute_all(skip_if_fresh_hours=24))
    assert "T1" not in w.seen, "scored without its income statement"
    assert fmp.count("get_income_statement", "T1") == 1   # the client already retried a 5xx
    assert summary["fetch_failures_by_kind"]["unavailable"] == 1
    assert summary["companies_fetch_lost"] == 1
    assert _sample_sizes(w) == {5}
    assert _warnings(caplog, f"{_INDUSTRY} 1/6 (0 rate-limited, 1 unavailable)")


@pytest.mark.asyncio
async def test_a_refused_statement_keeps_todays_behaviour_scored_and_counted(monkeypatch, vt):
    fmp = _ScriptFMP(
        always={("get_financial_ratios", "T1"): FMPNotEntitledException("not licensed")},
        script={("get_balance_sheet", "T2"): [{"Error Message": "Invalid symbol"}]},
    )
    w = _wire(monkeypatch, fmp)
    summary = await vt.run(w.svc.recompute_all(skip_if_fresh_hours=24))
    # A refusal comes back the same on every run: scored on what arrived (as before), counted.
    assert w.seen["T1"]["ratios"] == [] and w.seen["T2"]["balance"] == []
    assert fmp.count("get_financial_ratios", "T1") == 1
    assert summary["fetch_failures_by_kind"]["refused"] == 2
    assert summary["companies_fetch_lost"] == 0 and summary["industries_fetch_lost"] == 0
    assert _sample_sizes(w) == {6}


# ═══ The dossier sample falls back to the next-largest company ═══════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("first_answer", [
    FMPUnavailableException("503"),
    {"symbol": "T0"},                              # answered, but with no sector
    {"symbol": "T0", "sector": "   "},
])
async def test_a_failed_dossier_sample_falls_back_to_the_next_ticker(
    monkeypatch, vt, caplog, first_answer,
):
    fmp = _ScriptFMP(script={("get_company_profile", "T0"): [first_answer]})
    w = _wire(monkeypatch, fmp)
    drops: Dict[str, int] = {}
    with caplog.at_level(logging.INFO, logger=imb.logger.name):
        tam = await vt.run(w.svc._fetch_industry_tam(_INDUSTRY, list(_PEERS), drops=drops))
    assert tam is not None and tam.hhi == 1500.0
    assert drops == {}
    assert [t for name, t in fmp.calls if name == "get_company_profile"] == ["T0", "T1"]
    assert w.dossier.calls == [(_INDUSTRY, "Technology")]
    assert any("read with T1's sector after T0" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_every_sample_failing_is_bounded_counted_and_named(monkeypatch, vt, caplog):
    fmp = _ScriptFMP(always={
        ("get_company_profile", f"T{i}"): FMPUnavailableException("503") for i in range(6)
    })
    w = _wire(monkeypatch, fmp)
    drops: Dict[str, int] = {}
    with caplog.at_level(logging.WARNING, logger=imb.logger.name):
        tam = await vt.run(w.svc._fetch_industry_tam(_INDUSTRY, list(_PEERS), drops=drops))
    assert tam is None and drops == {"tam_unavailable": 1}
    assert len(fmp.calls) == imb.TAM_SAMPLE_TRIES
    assert w.dossier.calls == []
    line = _warnings(caplog, "no sample profile gave a sector")
    assert line and "T0: unavailable (FMPUnavailableException)" in line[0]
    assert "T4: unavailable" in line[0] and "T5" not in line[0]


@pytest.mark.asyncio
async def test_a_failed_dossier_read_is_counted_in_the_summary(monkeypatch, vt, caplog):
    w = _wire(monkeypatch, _ScriptFMP(), dossier_fails=True)
    with caplog.at_level(logging.WARNING, logger=imb.logger.name):
        summary = await vt.run(w.svc.recompute_all(skip_if_fresh_hours=24))
    assert summary["industries_tam_unavailable"] == 1
    assert summary["pillars_written"] == 1                 # the peers still score
    assert _warnings(caplog, "1 industry without its Network Effects context")
    assert _warnings(caplog, "RuntimeError: dossier read failed")


@pytest.mark.asyncio
async def test_an_industry_fmp_emptied_raises_and_names_fmp(monkeypatch, vt, caplog):
    """Five of six peers lost to a 5xx: one scored, below the floor — the industry FAILED
    (the run raises, the chain retries), it was not merely small."""
    fmp = _ScriptFMP(always={
        ("get_company_profile", f"T{i}"): FMPUnavailableException("503") for i in range(5)
    })
    w = _wire(monkeypatch, fmp)
    with caplog.at_level(logging.WARNING, logger=imb.logger.name):
        with pytest.raises(imb.IndustryMoatBenchmarkRecomputeSkipped) as info:
            await vt.run(w.svc.recompute_all(skip_if_fresh_hours=24))
    assert info.value.reason == "every industry failed"
    assert "1 failed (peers lost to FMP failures: 1)" in str(info.value)
    assert "429 past the retries and the shared window" in str(info.value)
    assert _warnings(caplog, f"{_INDUSTRY} 5/6 (0 rate-limited, 5 unavailable)")
    assert _warnings(caplog, "no pillar written: 5 of its 6 peers were left out")
    assert _warnings(caplog, "no sample profile gave a sector")   # T0..T4 tried, T5 not


@pytest.mark.parametrize("stats, expected", [
    ({"eligible": 0, "scored": 2, "lost_rate_limited": 3}, "peers lost to FMP failures"),
    ({"eligible": 0, "scored": 1, "lost_unavailable": 2, "lost_rate_limited": 2},
     "peers lost to FMP failures"),
    ({"eligible": 0, "scored": 3, "benchmark_failed": 1, "lost_unavailable": 1},
     "peers lost to FMP failures"),
    ({"eligible": 0, "scored": 2, "lost_unavailable": 1}, None),       # small anyway
    ({"eligible": 0, "scored": 2, "lost_refused": 5, "lost_error": 3}, None),  # not transient
    ({"eligible": 0, "scored": 0, "lost_rate_limited": 6}, "no peer could be scored"),
    ({"eligible": 0, "scored": 1, "benchmark_failed": 4, "lost_unavailable": 4},
     "sector-median read failed"),
])
def test_zero_write_failure_names_peers_lost_to_fmp(stats, expected):
    assert imb._zero_write_failure(0, stats) == expected
    assert imb._zero_write_failure(1, stats) is None


@pytest.mark.parametrize("results, expected", [
    ({"profile": {"sector": "X"}, "income": [], "balance": [], "ratios": []}, (None, None)),
    ({"profile": {}, "income": [], "balance": None, "ratios": []}, (None, None)),
    ({"profile": _429(), "income": [], "balance": [], "ratios": []},
     ("rate_limited", "rate_limited")),
    ({"profile": FMPNotEntitledException("x"), "income": [], "balance": [], "ratios": []},
     ("refused", "refused")),                    # never scored, but not a transient loss
    ({"profile": {"sector": "X"}, "income": FMPUnavailableException("503"),
      "balance": FMPNotEntitledException("x"), "ratios": []},
     ("unavailable", "unavailable")),
    ({"profile": {"sector": "X"}, "income": [], "balance": {"Error Message": "x"},
      "ratios": ValueError("bad body")}, ("error", None)),
    ({"profile": FMPNotEntitledException("x"), "income": _429(), "balance": [],
      "ratios": []}, ("rate_limited", "refused")),   # the profile decides why it is out
])
def test_peer_fetch_outcome(results, expected):
    assert imb._peer_fetch_outcome(results) == expected


# ═══ Every moat FMP request goes through the helper — except the transcript ══════════════


@pytest.mark.asyncio
async def test_every_moat_fmp_request_is_retried_except_the_transcript(monkeypatch, vt):
    """The transcript read is refused before any HTTP call (its package is outside the Order
    Form) and the client turns every failure into "": through the helper its 'success' would
    tell the shared 429 window that FMP answers again and close it."""
    wrapped: List[str] = []
    real = imb.call_with_rate_limit_retry

    async def _recording(fn, *args, **kwargs):
        wrapped.append(fn.__name__)
        return await real(fn, *args, **kwargs)

    monkeypatch.setattr(imb, "call_with_rate_limit_retry", _recording)
    fmp = _ScriptFMP()
    w = _wire(monkeypatch, fmp)
    await vt.run(w.svc.recompute_all(skip_if_fresh_hours=24))
    made = {name for name, _t in fmp.calls}
    assert "get_earning_call_transcript" in made
    assert set(wrapped) == made - {"get_earning_call_transcript"} == {
        "get_company_profile", "get_income_statement", "get_balance_sheet",
        "get_financial_ratios",
    }
    # One wrapped call per request: 4 per peer + the dossier sample's profile.
    assert len(wrapped) == len(fmp.calls) - len(_PEERS) == 4 * len(_PEERS) + 1


class _InFlightFMP(_ScriptFMP):
    """Records how many DIFFERENT companies had an FMP call outstanding at once, and the most
    calls one company had outstanding."""

    def __init__(self) -> None:
        super().__init__()
        self.active: Dict[str, int] = {}
        self.max_companies = 0
        self.max_per_company = 0

    def __getattr__(self, name: str):
        inner = super().__getattr__(name)

        async def call(ticker, *a, **k):
            self.active[ticker] = self.active.get(ticker, 0) + 1
            self.max_companies = max(
                self.max_companies, sum(1 for n in self.active.values() if n),
            )
            self.max_per_company = max(self.max_per_company, self.active[ticker])
            try:
                await asyncio.sleep(0)            # let any other company's calls start
                return await inner(ticker, *a, **k)
            finally:
                self.active[ticker] -= 1

        call.__name__ = name
        return call


@pytest.mark.asyncio
async def test_the_moat_phase_runs_one_company_in_flight(monkeypatch, vt):
    """Owner decision 2026-10-21 ("Lower to 1"): measured that day, 3 companies in flight hit
    FMP's per-minute cap 201 times in 3 min, 1 recomputed the whole universe in 10 min with no
    429 — and the chain runs beside production's own FMP traffic, which has no 429 retry.
    Mutation: back to 3 and three companies overlap."""
    assert imb.PER_TICKER_FMP_CONCURRENCY == 1
    fmp = _InFlightFMP()
    w = _wire(monkeypatch, fmp)
    summary = await vt.run(w.svc.recompute_all(skip_if_fresh_hours=24))
    assert summary["pillars_written"] == 1 and len(w.seen) == len(_PEERS)
    assert fmp.max_companies == 1
    # …while one company's own requests still go out together (4 + the refused transcript).
    assert fmp.max_per_company >= 4


@pytest.mark.asyncio
async def test_the_run_is_one_moat_rate_limit_run_and_a_clean_one_reports_zero(
    monkeypatch, vt,
):
    w = _wire(monkeypatch, _ScriptFMP())
    summary = await vt.run(w.svc.recompute_all(skip_if_fresh_hours=24))
    assert w.runs and set(w.runs) == {"moat"}
    assert sbs.current_rate_limit_run() is None             # scoped to the run
    assert {k: summary[k] for k in (
        "rate_limit_windows", "rate_limit_wait_seconds", "rate_limit_lockouts",
        "fetch_failures", "companies_fetch_lost", "industries_fetch_lost",
        "industries_tam_unavailable",
    )} == {
        "rate_limit_windows": 0, "rate_limit_wait_seconds": 0.0, "rate_limit_lockouts": 0,
        "fetch_failures": 0, "companies_fetch_lost": 0, "industries_fetch_lost": 0,
        "industries_tam_unavailable": 0,
    }
    assert summary["fetch_failures_by_kind"] == {
        "rate_limited": 0, "unavailable": 0, "error": 0, "refused": 0,
    }
    # The scoring is unchanged: same vintage stamp on every row.
    assert {r["model_version"] for r in w.svc.supabase.upserts} == {imb.MODEL_VERSION}
    assert imb.MODEL_VERSION == "moat_v1.2026-10-11"
