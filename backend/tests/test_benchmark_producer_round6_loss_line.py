"""Benchmark PRODUCER, review round 5 (2026-10-08) — the INCOMPLETE ("loss") line of
`industry_benchmark_service` and the failure kinds `sector_benchmark_service` feeds it.

  P4-1  An industry that is ITSELF over the loss line (>= MIN_SAMPLE_SIZE tickers, more than
        INDUSTRY_FAILURE_INCOMPLETE_SHARE of them lost) was still upserted — its full-sample
        rows replaced by a median over the companies fetched before the outage (the largest:
        tickers are fetched by cap) — and pooled into its sector. It is now neither written
        nor pooled; its previous rows stay, a WARNING names it, the summary lists it. Fiscal
        and TTM, full sweeps and the operator's industries-only paths.
  P4-2  Only TRANSIENT failures count toward the line: a 429 that outlived the retries, a 5xx
        (a raw `httpx.HTTPStatusError` >= 500 is now 'unavailable'), a network failure. A
        refusal (4xx, 401, 402, a 200 whose body is not a list) is the new 'refused' kind and
        an unexplained one stays 'error': both are counted and named in the WARNING, and
        neither can hold a run unsettled (the retry gets the same answer). In the fiscal sweep
        a ticker is lost only when a CORE call (income statement or ratios, annual or
        quarterly) failed transiently — not when one of its six side calls did.
  DOC4-1 The INCOMPLETE ERROR line (and so the ledger's `error`) carries `lossy_sectors`,
        `fetch_failures`, `fetch_failures_by_kind` and `industries_with_failures`: a run that
        raises never logs its `complete:` summary.

Hermetic: an FMP-shaped fake, an in-memory `sector_benchmarks` table, no network, no sleeps
(no 429 is simulated end to end; the 429 kind is covered by the unit tests below).
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx
import pytest

import app.services.industry_benchmark_service as ibs
import app.services.sector_benchmark_service as sbs
from app.integrations.fmp import (
    FMPAuthException,
    FMPNotEntitledException,
    FMPPartialPageException,
    FMPRateLimitException,
    FMPUnavailableException,
)


# ── Fakes ───────────────────────────────────────────────────────────────────────────────


def _http(code: int) -> httpx.HTTPStatusError:
    """What `FMPClient._make_request_impl` re-raises for a status it does not map itself."""
    req = httpx.Request("GET", "https://financialmodelingprep.com/stable/ratios")
    return httpx.HTTPStatusError(f"HTTP {code}", request=req, response=httpx.Response(code, request=req))


class _Table:
    """`sector_benchmarks` in memory: upserts merge on the conflict key and are recorded in
    `upserted`; the freshness probe answers the newest matching row."""

    def __init__(self) -> None:
        self.rows: Dict[tuple, Dict[str, Any]] = {}
        self.upserted: List[Dict[str, Any]] = []

    def table(self, _name: str):
        db = self

        class _Q:
            def __init__(self) -> None:
                self.batch: Optional[List[Dict[str, Any]]] = None
                self.eqs: Dict[str, Any] = {}

            def upsert(self, batch, on_conflict=None):
                self.batch = list(batch)
                return self

            def eq(self, col, val):
                self.eqs[col] = val
                return self

            def __getattr__(self, _attr):            # select / order / limit
                return lambda *a, **k: self

            def execute(self):
                if self.batch is not None:
                    for r in self.batch:
                        db.upserted.append(dict(r))
                        key = (r["sector"], r["industry"], r["metric_name"],
                               r["period_type"], r["period_label"])
                        db.rows[key] = dict(r)
                    return SimpleNamespace(data=self.batch)
                hits = [r for r in db.rows.values()
                        if all(r.get(c) == v for c, v in self.eqs.items())]
                hits.sort(key=lambda r: r["computed_at"], reverse=True)
                return SimpleNamespace(data=hits[:1])

        return _Q()

    def seed(self, sector: str, industry: str, period_type: str, label: str,
             sample_size: int, median: float) -> Tuple[tuple, Dict[str, Any]]:
        key = (sector, industry, "gross_margin", period_type, label)
        row = {"sector": sector, "industry": industry, "metric_name": "gross_margin",
               "period_type": period_type, "period_label": label, "median_value": median,
               "sample_size": sample_size, "computed_at": "2026-07-05T05:00:00+00:00"}
        self.rows[key] = dict(row)
        return key, row

    def upserted_for(self, industry: str) -> List[Dict[str, Any]]:
        return [r for r in self.upserted if r["industry"] == industry]


# A failure rule: (fmp method name, period) -> an exception to raise, a body to answer, or
# None to answer normally. `fail` maps a ticker to its rule.
_Rule = Callable[[str, Optional[str]], Any]


def _always(outcome: Any) -> _Rule:
    return lambda _name, _period: outcome


def _only(method: str, outcome: Any, period: Optional[str] = None) -> _Rule:
    def rule(name: str, p: Optional[str]) -> Any:
        if name == method and (period is None or p == period):
            return outcome
        return None
    return rule


class _FMP:
    """Answers like FMP: one complete annual ratios year (2024) and a TTM ratios row per
    company, [] elsewhere; tickers in `fail` follow their rule."""

    def __init__(self, fail: Optional[Dict[str, _Rule]] = None) -> None:
        self.fail = dict(fail or {})

    def __getattr__(self, name: str):
        if not name.startswith("get_"):
            raise AttributeError(name)

        async def call(ticker, *a, **k):
            period = k.get("period")
            rule = self.fail.get(ticker)
            outcome = rule(name, period) if rule is not None else None
            if isinstance(outcome, BaseException):
                raise outcome
            if outcome is not None:
                return outcome
            n = int(ticker[1:])
            if name == "get_financial_ratios" and period == "annual":
                return [{"date": "2024-12-31", "grossProfitMargin": 0.40 + n / 100}]
            if name == "get_ratios_ttm":
                return [{"grossProfitMarginTTM": 0.40 + n / 100}]
            return []

        return call


def _universe(groups: Dict[str, Dict[str, List[str]]]) -> List[Dict[str, Any]]:
    """{sector: {industry: [tickers]}} → universe entries (listed order = cap order)."""
    return [
        {"industry": ind, "sector": sector,
         "market_caps": {t: 1.0e10 - i for i, t in enumerate(tickers)}}
        for sector, inds in groups.items() for ind, tickers in inds.items()
    ]


def _svc(monkeypatch, fmp: _FMP, groups: Dict[str, Dict[str, List[str]]],
         db: Optional[_Table] = None):
    """The REAL fiscal + TTM fetch, accounting and write paths over `fmp` and `db`."""
    db = db or _Table()
    sector_svc = sbs.SectorBenchmarkService.__new__(sbs.SectorBenchmarkService)
    sector_svc.fmp = fmp
    sector_svc.supabase = db
    sector_svc._fmp_semaphore = asyncio.Semaphore(10)
    svc = ibs.IndustryBenchmarkService.__new__(ibs.IndustryBenchmarkService)
    svc.supabase = db
    svc._sb = sector_svc
    svc._fmp = fmp
    svc._calendar_quarter_blocked = False
    universe = _universe(groups)
    monkeypatch.setattr(ibs, "_fetch_benchmark_universe", lambda: [dict(e) for e in universe])
    monkeypatch.setattr(ibs, "_last_fetched_universe", None)
    monkeypatch.setattr(ibs, "BATCH_DELAY_SECONDS", 0)
    monkeypatch.setattr(sbs, "_exhausted_in_a_row", 0)
    return svc, db


def _sweep(svc, mode: str):
    return svc.recompute_all if mode == "fiscal" else svc.recompute_all_ttm


_MARKER = {"fiscal": ("annual", "2024"), "ttm": ("ttm", "TTM")}


def _tickers(prefix: str, n: int) -> List[str]:
    return [f"{prefix}{i}" for i in range(n)]


# ═══ P4-2: the failure kinds ═════════════════════════════════════════════════════════════


class _NoStatus(httpx.HTTPStatusError):
    """An HTTPStatusError whose response carries no readable status."""

    def __init__(self) -> None:
        Exception.__init__(self, "no status")
        self.response = SimpleNamespace(status_code=None)


@pytest.mark.parametrize("exc, kind", [
    (FMPRateLimitException("429"), "rate_limited"),
    (FMPUnavailableException("503 after retries"), "unavailable"),
    (FMPPartialPageException("lost a page", endpoint="x", pages_total=4, pages_failed=1),
     "unavailable"),
    (_http(500), "unavailable"),
    (_http(501), "unavailable"),        # not in the client's retry set: reaches here raw
    (_http(520), "unavailable"),        # Cloudflare 52x: raw, and transient
    (_http(599), "unavailable"),
    (_http(400), "refused"),
    (_http(403), "refused"),
    (_http(404), "refused"),
    (_http(422), "refused"),
    (_http(499), "refused"),
    (FMPAuthException("401 Unauthorized"), "refused"),
    (FMPNotEntitledException("402 Restricted Endpoint"), "refused"),
    (_http(302), "error"),              # neither a refusal nor transient
    (_NoStatus(), "error"),
    (ValueError("Expecting value: line 1 column 1"), "error"),
    (httpx.DecodingError("bad gzip"), "error"),
    (TypeError("a bug"), "error"),
])
def test_classify_fetch_failure(exc, kind):
    # Mutation: without the >= 500 branch the raw 5xx rows read "error"; without the refusal
    # branches the 4xx / 401 / 402 rows read "error".
    assert sbs.classify_fetch_failure(exc) == kind
    assert kind in sbs.FETCH_FAILURE_KINDS
    assert (kind in sbs.TRANSIENT_FETCH_FAILURES) == (kind in ("rate_limited", "unavailable"))


def test_the_transient_kinds_are_exactly_429_and_5xx():
    assert sbs.TRANSIENT_FETCH_FAILURES == {"rate_limited", "unavailable"}
    assert set(sbs.FETCH_FAILURE_KINDS) == {"rate_limited", "unavailable", "error", "refused"}


@pytest.mark.asyncio
async def test_fetch_company_data_names_each_failed_call_and_its_kind():
    fail = {"A1": lambda name, period: {
        ("get_cash_flow_statement", "annual"): FMPUnavailableException("503"),
        ("get_financial_ratios", "quarter"): {"Error Message": "nope"},     # 200, not a list
        ("get_key_metrics", "annual"): _http(404),
        ("get_balance_sheet", "quarter"): None,                             # answers normally
    }.get((name, period))}
    svc = sbs.SectorBenchmarkService.__new__(sbs.SectorBenchmarkService)
    svc.fmp = _FMP(fail)
    svc._fmp_semaphore = asyncio.Semaphore(4)
    data = await svc._fetch_company_data("A1", 3, 4)
    assert data[sbs.FETCH_FAILED_CALLS_KEY] == {
        "cashflow_annual": "unavailable", "ratios_quarterly": "refused",
        "key_metrics_annual": "refused",
    }
    assert sorted(data[sbs.FETCH_ERRORS_KEY]) == ["refused", "refused", "unavailable"]
    assert data["ratios_annual"] == [{"date": "2024-12-31", "grossProfitMargin": pytest.approx(0.41)}]
    # Every core key of the fiscal line is a key this fetch produces (a rename would make the
    # line blind: no ticker could ever be lost).
    assert set(ibs._FISCAL_LINE_CALLS) <= set(data)


def test_record_fetch_outcome_separates_the_kind_from_the_line():
    c = ibs._new_counts()
    ibs._record_fetch_outcome(c, ["unavailable"], True, line_failures=[])          # side call
    ibs._record_fetch_outcome(c, ["refused", "unavailable"], True, line_failures=["refused"])
    ibs._record_fetch_outcome(c, ["refused"], False)                               # refused only
    ibs._record_fetch_outcome(c, ["error"], True)                                  # unexplained
    ibs._record_fetch_outcome(c, ["unavailable", "rate_limited"], True)            # TTM default
    ibs._record_fetch_outcome(c, ["refused", "unavailable"], True,
                              line_failures=["unavailable"])                       # core 5xx
    assert c == {"tickers": 6, "rate_limited": 1, "unavailable": 3, "error": 1, "refused": 1,
                 "empty": 0, "lost_rate_limited": 1, "lost_unavailable": 1}
    assert ibs._lost(c) == 2


def test_fiscal_line_failures_reads_only_the_core_calls():
    assert ibs._fiscal_line_failures(None) is None            # unknown: every failure counts
    assert ibs._fiscal_line_failures(["unavailable"]) is None
    assert ibs._fiscal_line_failures({}) == []
    assert ibs._fiscal_line_failures({
        "cashflow_annual": "unavailable", "balance_quarterly": "rate_limited",
        "key_metrics_annual": "unavailable", "income_quarterly": "refused",
        "ratios_annual": "unavailable",
    }) == ["refused", "unavailable"]


def test_sector_loss_counts_transient_only_and_names_the_rest():
    tally = ibs._FetchTally("fiscal")
    a = tally.counts_for("Technology", "A")
    for _ in range(3):
        ibs._record_fetch_outcome(a, [], True)
    for _ in range(2):
        ibs._record_fetch_outcome(a, ["unavailable"], False)
    b = tally.counts_for("Technology", "B")
    for _ in range(2):
        ibs._record_fetch_outcome(b, [], True)
    for _ in range(3):
        ibs._record_fetch_outcome(b, ["refused"], False)
    assert tally.sector_loss("Technology") == (
        "2 of 10 tickers lost to transient fetch failures (20%: 0 rate-limited, 2 unavailable); "
        "not counted toward the line: 3 refused, 0 other; industries over the line: A 2/5 (40%)"
    )
    # Refused alone (B 3/5 = 60%) never makes a sector lossy.
    only_refused = ibs._FetchTally("ttm")
    c = only_refused.counts_for("Energy", "B")
    for kind in ("refused", "refused", "refused", "error", None):
        ibs._record_fetch_outcome(c, [kind] if kind else [], kind is None)
    assert only_refused.sector_loss("Energy") is None
    assert only_refused.withhold("Energy", "B") is None


def test_withhold_is_the_industry_line_and_records_it():
    tally = ibs._FetchTally("fiscal")
    for industry, tickers, lost in (("Over", 10, 3), ("AtLine", 8, 2), ("Small", 4, 4)):
        c = tally.counts_for("Technology", industry)
        for i in range(tickers):
            ibs._record_fetch_outcome(c, ["unavailable"] if i < lost else [], True)
    assert tally.withhold("Technology", "Over") == "Over 3/10 (30%: 0 rate-limited, 3 unavailable)"
    assert tally.withhold("Technology", "AtLine") is None       # 25% is not MORE than 25%
    assert tally.withhold("Technology", "Small") is None        # below MIN_SAMPLE_SIZE
    assert tally.withhold("Technology", "Never fetched") is None
    assert tally.summary()["industries_withheld"] == ["Technology / Over"]


# ═══ P4-2 end to end: a refusal settles; a transient failure does not ════════════════════


_SEMIS = _tickers("M", 6)
_SOFTWARE = _tickers("S", 6)
_TECH_ENERGY = {
    "Technology": {"Semiconductors": _SEMIS, "Software - Application": _SOFTWARE},
    "Energy": {"Oil & Gas E&P": _tickers("E", 5)},
}
# The review's repro: 2 of a 6-ticker industry (33% > the 25% industry line) keep failing on
# the ratios call every week.
_RATIOS = {"fiscal": "get_financial_ratios", "ttm": "get_ratios_ttm"}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fiscal", "ttm"])
@pytest.mark.parametrize("outcome, kind", [
    (_http(404), "refused"),
    (_http(403), "refused"),
    (FMPAuthException("401"), "refused"),
    (FMPNotEntitledException("402 Restricted Endpoint"), "refused"),
    ({"Error Message": "Invalid symbol"}, "refused"),        # a 200 whose body is not a list
    (ValueError("Expecting value"), "error"),
])
async def test_a_persistent_refusal_settles_the_run(monkeypatch, caplog, mode, outcome, kind):
    # Mutation: count 'refused' / 'error' toward the line (e.g. line = every failure kind) and
    # Technology goes lossy — this raises Incomplete instead of returning.
    fail = {t: _only(_RATIOS[mode], outcome) for t in _SEMIS[:2]}
    svc, db = _svc(monkeypatch, _FMP(fail), _TECH_ENERGY)
    with caplog.at_level(logging.WARNING, logger=ibs.logger.name):
        summary = await _sweep(svc, mode)(skip_if_fresh_hours=24)
    assert summary["sectors_lossy"] == 0 and summary["lossy_sectors"] == []
    assert summary["fetch_failures"] == 2 and summary["fetch_failures_by_kind"][kind] == 2
    assert summary["fetch_lost_transient"] == 0 and summary["industries_withheld"] == []
    period_type, label = _MARKER[mode]
    aggregate = db.rows[("Technology", "", "gross_margin", period_type, label)]
    assert aggregate["sample_size"] == 10                  # the 10 companies that answered
    probe = svc._sector_is_fresh if mode == "fiscal" else svc._ttm_sector_is_fresh
    assert probe("Technology", 24) is True
    # Counted and named: the industry crossed the 25% WARNING line (2/6 = 33%).
    (warn,) = [r.getMessage() for r in caplog.records if "above the warning line" in r.getMessage()]
    other = "2 refused, 0 other" if kind == "refused" else "0 refused, 2 other"
    assert f"Technology / Semiconductors 2/6 (33%: 0 rate-limited, 0 unavailable, {other}" in warn
    assert "The 2 refused / other never hold a run open" in warn
    assert not [r for r in caplog.records if "NOT written" in r.getMessage()]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fiscal", "ttm"])
@pytest.mark.parametrize("outcome", [
    FMPUnavailableException("503 after 4 attempts"),
    _http(520),                                              # raw 5xx → unavailable
    _http(500),
])
async def test_the_transient_twin_holds_the_sector_open(monkeypatch, caplog, mode, outcome):
    # Mutation: drop the >= 500 reclassification and the raw 520/500 rows read "error" — the
    # run settles and this test fails.
    fail = {t: _only(_RATIOS[mode], outcome) for t in _SEMIS[:2]}
    svc, db = _svc(monkeypatch, _FMP(fail), _TECH_ENERGY)
    with caplog.at_level(logging.WARNING, logger=ibs.logger.name):
        with pytest.raises(ibs.IndustryBenchmarkRecomputeIncomplete) as info:
            await _sweep(svc, mode)(skip_if_fresh_hours=24)
    exc = info.value
    assert exc.lossy_sectors == ["Technology"]
    assert exc.summary["fetch_failures_by_kind"]["unavailable"] == 2
    assert exc.summary["fetch_lost_transient"] == 2
    assert exc.summary["industries_withheld"] == ["Technology / Semiconductors"]
    period_type, label = _MARKER[mode]
    assert ("Technology", "", "gross_margin", period_type, label) not in db.rows


# Fiscal: (statement key, FMP method, period) for all ten calls of `_fetch_company_data`.
_FISCAL_CALLS = [
    ("income_annual", "get_income_statement", "annual"),
    ("income_quarterly", "get_income_statement", "quarter"),
    ("ratios_annual", "get_financial_ratios", "annual"),
    ("ratios_quarterly", "get_financial_ratios", "quarter"),
    ("cashflow_annual", "get_cash_flow_statement", "annual"),
    ("cashflow_quarterly", "get_cash_flow_statement", "quarter"),
    ("key_metrics_annual", "get_key_metrics", "annual"),
    ("key_metrics_quarterly", "get_key_metrics", "quarter"),
    ("balance_annual", "get_balance_sheet", "annual"),
    ("balance_quarterly", "get_balance_sheet", "quarter"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("key, method, period", _FISCAL_CALLS)
async def test_fiscal_a_ticker_is_lost_only_to_a_core_call(monkeypatch, key, method, period):
    """2 of Semiconductors' 6 companies 503 on ONE call. A core call (income or ratios)
    loses them to the line; a side call (cash flow, key metrics, balance sheet) is counted
    but loses nothing — the medians from their nine answered calls are in."""
    # Mutation: pass no `line_failures` (every failed call counts, the old rule) and the six
    # side-call rows go lossy; drop a key from `_FISCAL_LINE_CALLS` and its row settles.
    fail = {t: _only(method, FMPUnavailableException("503"), period) for t in _SEMIS[:2]}
    svc, db = _svc(monkeypatch, _FMP(fail), _TECH_ENERGY)
    core = key in ibs._FISCAL_LINE_CALLS
    assert core == (key.startswith("income_") or key.startswith("ratios_"))
    if core:
        with pytest.raises(ibs.IndustryBenchmarkRecomputeIncomplete) as info:
            await svc.recompute_all(skip_if_fresh_hours=24)
        summary = info.value.summary
        assert info.value.lossy_sectors == ["Technology"]
        assert summary["fetch_lost_transient"] == 2
    else:
        summary = await svc.recompute_all(skip_if_fresh_hours=24)
        assert summary["sectors_lossy"] == 0 and summary["fetch_lost_transient"] == 0
        assert db.rows[("Technology", "", "gross_margin", "annual", "2024")]["sample_size"] == 12
        assert db.rows[("Technology", "Semiconductors", "gross_margin", "annual", "2024")][
            "sample_size"] == 6
    # Counted by kind either way.
    assert summary["fetch_failures"] == 2
    assert summary["fetch_failures_by_kind"]["unavailable"] == 2


@pytest.mark.asyncio
async def test_a_run_that_was_refused_everything_still_refuses_to_settle(monkeypatch):
    """Refusals never make a sector LOSSY, but a sweep that wrote nothing (a revoked key: 401
    on every call) is still the nothing-written refusal, never a settled run."""
    everyone = [t for inds in _TECH_ENERGY.values() for ts in inds.values() for t in ts]
    fail = {t: _always(FMPAuthException("401")) for t in everyone}
    svc, _ = _svc(monkeypatch, _FMP(fail), _TECH_ENERGY)
    with pytest.raises(ibs.IndustryBenchmarkRecomputeSkipped) as info:
        await svc.recompute_all(skip_if_fresh_hours=24)
    assert info.value.reason == "nothing written"


# ═══ P4-1: an industry over the line keeps its previous rows ═════════════════════════════


_CHIPS = _tickers("M", 10)
_TECH_WIDE = {
    "Technology": {"Semiconductors": _CHIPS, "Software - Application": _SOFTWARE},
    "Energy": {"Oil & Gas E&P": _tickers("E", 5)},
}


def _spy_pool(monkeypatch) -> List[str]:
    pooled: List[str] = []
    real = ibs.IndustryBenchmarkService._pool_into_sector

    def spy(sector_acc, values, industry, left_out):
        pooled.append(industry)
        return real(sector_acc, values, industry, left_out)

    monkeypatch.setattr(ibs.IndustryBenchmarkService, "_pool_into_sector", staticmethod(spy))
    return pooled


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fiscal", "ttm"])
async def test_an_industry_over_the_line_is_neither_written_nor_pooled(monkeypatch, caplog, mode):
    """The review's repro, scaled down: an outage takes the 4 smallest of Semiconductors' 10
    companies (40% > 25%). Pre-fix the 6 largest replaced last run's n=120 rows."""
    # Mutation: delete the `withhold` check in the sweep and Semiconductors is upserted with
    # n=6 over the seeded n=120 row, and pooled.
    period_type, label = _MARKER[mode]
    db = _Table()
    key, before = db.seed("Technology", "Semiconductors", period_type, label, 120, 0.55)
    fmp = _FMP({t: _always(FMPUnavailableException("503")) for t in _CHIPS[6:]})
    svc, _ = _svc(monkeypatch, fmp, _TECH_WIDE, db)
    pooled = _spy_pool(monkeypatch)

    with caplog.at_level(logging.WARNING, logger=ibs.logger.name):
        with pytest.raises(ibs.IndustryBenchmarkRecomputeIncomplete) as info:
            await _sweep(svc, mode)(skip_if_fresh_hours=24)

    exc = info.value
    assert exc.lossy_sectors == ["Technology"]
    assert exc.summary["industries_withheld"] == ["Technology / Semiconductors"]
    assert db.upserted_for("Semiconductors") == []           # not one row of it
    assert db.rows[key] == before                             # the full-sample row stands
    assert "Semiconductors" not in pooled and "Software - Application" in pooled
    assert db.rows[("Technology", "Software - Application", "gross_margin", period_type, label)][
        "sample_size"] == 6
    assert ("Technology", "", "gross_margin", period_type, label) not in db.rows
    (warn,) = [r.getMessage() for r in caplog.records
               if "NOT written and left out of the sector pool" in r.getMessage()]
    assert "Technology — 1 industry NOT written" in warn
    assert "Semiconductors 4/10 (40%: 0 rate-limited, 4 unavailable)" in warn
    assert "its previous rows stay" in warn

    # The same-day retry, FMP back: the industry is rewritten from all ten, the sector settles.
    fmp.fail.clear()
    summary = await _sweep(svc, mode)(skip_if_fresh_hours=24)
    assert summary["industries_withheld"] == [] and summary["sectors_lossy"] == 0
    assert db.rows[key]["sample_size"] == 10
    assert db.rows[("Technology", "", "gross_margin", period_type, label)]["sample_size"] == 16


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fiscal", "ttm"])
async def test_an_industry_that_fetched_nothing_is_listed_not_warned_twice(monkeypatch, caplog, mode):
    """Every Semiconductors company failed: nothing could have been written, so the per-sector
    "NOT written" WARNING (for a PARTIAL fetch the line kept out) stays quiet — the
    unwritten-aggregate WARNING already names the industry — while the summary lists it."""
    # Mutation: log every withheld industry and a whole-run outage repeats each industry in a
    # second WARNING per sector — the first assertion below fails.
    fmp = _FMP({t: _always(FMPUnavailableException("503")) for t in _SEMIS})
    svc, _ = _svc(monkeypatch, fmp, _TECH_ENERGY)
    with caplog.at_level(logging.WARNING, logger=ibs.logger.name):
        with pytest.raises(ibs.IndustryBenchmarkRecomputeIncomplete) as info:
            await _sweep(svc, mode)(skip_if_fresh_hours=24)
    messages = [r.getMessage() for r in caplog.records]
    assert not [m for m in messages if "left out of the sector pool" in m]
    (unwritten,) = [m for m in messages if "the sector aggregate is NOT written" in m]
    assert "industries over the line: Semiconductors 6/6 (100%)" in unwritten
    assert info.value.summary["industries_withheld"] == ["Technology / Semiconductors"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fiscal", "ttm"])
@pytest.mark.parametrize("lost, written", [(2, True), (3, False)])
async def test_the_industry_line_is_more_than_a_quarter(monkeypatch, mode, lost, written):
    """8 companies: 2 lost (25%, not MORE) is written although the SECTOR is lossy (2 of 14 >
    10% — that only withholds the aggregate); 3 lost (37.5%) is withheld."""
    period_type, label = _MARKER[mode]
    eight = _tickers("M", 8)
    fmp = _FMP({t: _always(_http(502)) for t in eight[8 - lost:]})
    svc, db = _svc(monkeypatch, fmp, {
        "Technology": {"Semiconductors": eight, "Software - Application": _SOFTWARE},
        "Energy": {"Oil & Gas E&P": _tickers("E", 5)},
    })
    with pytest.raises(ibs.IndustryBenchmarkRecomputeIncomplete) as info:
        await _sweep(svc, mode)(skip_if_fresh_hours=24)
    assert info.value.lossy_sectors == ["Technology"]
    row = db.rows.get(("Technology", "Semiconductors", "gross_margin", period_type, label))
    if written:
        assert row is not None and row["sample_size"] == 6
        assert info.value.summary["industries_withheld"] == []
    else:
        assert row is None
        assert info.value.summary["industries_withheld"] == ["Technology / Semiconductors"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fiscal", "ttm"])
async def test_the_industries_only_path_withholds_too(monkeypatch, caplog, mode):
    period_type, label = _MARKER[mode]
    db = _Table()
    key, before = db.seed("Technology", "Semiconductors", period_type, label, 120, 0.55)
    fmp = _FMP({t: _always(FMPUnavailableException("503")) for t in _CHIPS[6:]})
    svc, _ = _svc(monkeypatch, fmp, _TECH_WIDE, db)
    with caplog.at_level(logging.WARNING, logger=ibs.logger.name):
        summary = await _sweep(svc, mode)(industries=["Semiconductors"])
    assert summary["rows_upserted"] == 0
    assert summary["industries_withheld"] == ["Technology / Semiconductors"]
    assert db.upserted == [] and db.rows[key] == before
    assert any("Semiconductors 4/10 (40%" in r.getMessage() for r in caplog.records
               if "NOT written and left out of the sector pool" in r.getMessage())


# ═══ DOC4-1: the ERROR line carries the counters ═════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fiscal", "ttm"])
async def test_the_incomplete_error_line_carries_the_lossy_sectors_and_counters(monkeypatch, caplog, mode):
    # Mutation: drop any of the three keys from the brief and its assertion fails.
    fail = {t: _always(FMPUnavailableException("503")) for t in _SEMIS[:2]}
    fail[_SOFTWARE[0]] = _only(_RATIOS[mode], _http(404))
    svc, _ = _svc(monkeypatch, _FMP(fail), _TECH_ENERGY)
    with caplog.at_level(logging.ERROR, logger=ibs.logger.name):
        with pytest.raises(ibs.IndustryBenchmarkRecomputeIncomplete) as info:
            await _sweep(svc, mode)(skip_if_fresh_hours=24)
    (line,) = [r.getMessage() for r in caplog.records
               if r.levelno == logging.ERROR and "recompute INCOMPLETE" in r.getMessage()]
    for text in (line, str(info.value)):           # the log line and the ledger's `error`
        assert "'lossy_sectors': ['Technology']" in text
        assert "'fetch_failures': 3" in text
        assert ("'fetch_failures_by_kind': {'rate_limited': 0, 'unavailable': 2, 'error': 0, "
                "'refused': 1}") in text
        assert "'industries_with_failures': 2" in text
        assert "lost too many companies to FMP fetch failures: Technology" in text


def test_the_brief_tolerates_a_summary_without_the_counters():
    exc = ibs.IndustryBenchmarkRecomputeIncomplete("fiscal", ["Energy"], {"rows_upserted": 9})
    assert "'fetch_failures': None" in str(exc) and "'lossy_sectors': None" in str(exc)
    assert "'industries_with_failures': None" in str(exc)
