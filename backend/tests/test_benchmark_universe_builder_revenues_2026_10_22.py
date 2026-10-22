"""The universe build records each kept ticker's TTM revenue beside its market cap
(2026-10-22, owner decision "Revenue-share HHI").

Revenue = screener cap ÷ `priceToSalesRatioTTM`, read from the `ratios-ttm` answer the
statement-twin pass already fetches — the build makes NO extra FMP call. These tests pin the
`revenues` map, what is left out of it, and that call count.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

import pytest

import scripts.build_benchmark_universe as bu


def _ratios(ps: Any, revps: Any, seed: float) -> List[Dict[str, Any]]:
    return [{"grossProfitMarginTTM": 0.3 + seed, "operatingProfitMarginTTM": 0.1 + seed,
             "netProfitMarginTTM": 0.05 + seed, "currentRatioTTM": 1.5, "debtToEquityRatioTTM": 0.8,
             "priceToSalesRatioTTM": ps, "revenuePerShareTTM": revps}]


class _FakeFMP:
    def __init__(self, answers: Dict[str, Any]) -> None:
        self.answers = answers
        self.calls: List[str] = []

    async def _make_request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
        assert endpoint == "ratios-ttm", endpoint
        sym = (params or {})["symbol"]
        self.calls.append(sym)
        answer = self.answers[sym]
        if isinstance(answer, BaseException):
            raise answer
        return answer


async def _no_sleep(_s: float) -> None:
    return None


def _scan(answers: Dict[str, Any]) -> bu._FingerprintScan:
    fmp = _FakeFMP(answers)
    scan = asyncio.run(bu._fetch_statement_fingerprints(fmp, list(answers), sleep=_no_sleep,
                                                        clock=lambda: 0.0))
    assert sorted(fmp.calls) == sorted(answers), "one ratios-ttm call per row, no more"
    return scan


def _rows(*caps: tuple) -> List[Dict[str, Any]]:
    return [{"symbol": s, "marketCap": c, "sector": "Industrials"} for s, c in caps]


def test_revenues_are_cap_over_ps_for_each_kept_ticker():
    scan = _scan({"RTX": _ratios(2.6, 70.0, 0.001), "BA": _ratios(1.5, 120.0, 0.002),
                  "SPCX": _ratios(61.2, 2.6, 0.003)})
    agg = bu._aggregate({"Aerospace & Defense": _rows(("RTX", 249e9), ("BA", 141e9), ("SPCX", 1906.8e9))},
                        scan.sales)
    revenues = agg[0]["revenues"]
    assert revenues["RTX"] == pytest.approx(249e9 / 2.6)
    assert revenues["BA"] == pytest.approx(141e9 / 1.5)
    assert revenues["SPCX"] == pytest.approx(1906.8e9 / 61.2)     # ~$31B — not its $1.9T cap
    assert set(agg[0]) == {"industry", "sector", "tickers", "market_caps", "revenues"}


def test_a_pre_revenue_row_is_zero_and_unusable_rows_are_left_out():
    scan = _scan({
        "ACHR": _ratios(0.0, 0.0, 0.001),             # pre-revenue → explicit 0.0
        "EMPTY": [],                                  # FMP holds no statements → no answer
        "BAD": _ratios(-3.0, 1.0, 0.002),             # negative P/S → left out
        "STR": _ratios("4.1", 1.0, 0.003),            # a string → never coerced
        "OK": _ratios(2.0, 5.0, 0.004),
    })
    agg = bu._aggregate({"Aerospace & Defense": _rows(("ACHR", 3.6e9), ("EMPTY", 1e9), ("BAD", 1e9),
                                                       ("STR", 1e9), ("OK", 10e9))}, scan.sales)
    assert agg[0]["revenues"] == {"ACHR": 0.0, "OK": pytest.approx(5e9)}


def test_a_failed_call_leaves_the_ticker_out():
    import httpx
    # 120 rows, so one failure stays inside the scan's 1% failure budget (more would stop it).
    answers: Dict[str, Any] = {f"OK{i:03d}": _ratios(2.0, 5.0, i / 1e4) for i in range(119)}
    answers["DOWN"] = httpx.ConnectError("refused")
    scan = _scan(answers)
    assert "DOWN" in scan.failed and "DOWN" not in scan.sales
    agg = bu._aggregate({"X": _rows(("OK000", 10e9), ("DOWN", 5e9))}, scan.sales)
    assert agg[0]["revenues"] == {"OK000": pytest.approx(5e9)}
    assert "DOWN" in agg[0]["market_caps"]            # the cap row is kept, as before


def test_without_the_scan_there_is_no_revenues_key():
    agg = bu._aggregate({"X": _rows(("OK", 10e9))})
    assert "revenues" not in agg[0]                   # --skip-twin-scan / the pre-scan guard pass


def test_a_dropped_statement_twin_has_no_revenue():
    """Only KEPT rows carry a revenue: a twin the scan dropped is not in by_industry, so its
    revenue cannot count twice."""
    scan = _scan({"GOOG": _ratios(8.0, 30.0, 0.001), "GOOGL": _ratios(8.0, 30.0, 0.001)})
    agg = bu._aggregate({"Internet Content & Information": _rows(("GOOG", 2.1e12))}, scan.sales)
    assert set(agg[0]["revenues"]) == {"GOOG"}


def test_symbols_are_matched_case_and_space_insensitively():
    scan = bu._FingerprintScan({}, [], [], {}, [], 1, 0.0, {" rtx ": (2.0, 1.0)})
    agg = bu._aggregate({"X": [{"symbol": "RTX", "marketCap": 10e9, "sector": "Industrials"}]}, scan.sales)
    assert agg[0]["revenues"] == {"RTX": pytest.approx(5e9)}


def test_the_leader_log_names_the_top_three_with_their_ps(caplog):
    scan = _scan({"A": _ratios(1.0, 1.0, 0.001), "B": _ratios(2.0, 1.0, 0.002),
                  "C": _ratios(4.0, 1.0, 0.003), "D": _ratios(8.0, 1.0, 0.004),
                  "N": _ratios(-1.0, 1.0, 0.005)})
    agg = bu._aggregate({"X": _rows(("A", 10e9), ("B", 10e9), ("C", 10e9), ("D", 10e9), ("N", 10e9))},
                        scan.sales)
    with caplog.at_level(logging.INFO, logger=bu.logger.name):
        bu._log_revenue_leaders(agg, scan.sales)
    leaders = [r.getMessage() for r in caplog.records if "revenue leaders" in r.getMessage()]
    assert len(leaders) == 1 and "A $10.0B (P/S 1.00)" in leaders[0] and "C $2.5B" in leaders[0]
    assert "D $" not in leaders[0]
    assert any("without a usable revenue" in r.getMessage() and "N" in r.getMessage()
               for r in caplog.records if r.levelno == logging.WARNING)


def test_the_positional_scan_tuple_still_builds_without_sales():
    """test_benchmark_universe_builder_twin_sanity builds `_FingerprintScan` positionally
    with seven fields; `sales` is last with a default."""
    scan = bu._FingerprintScan({}, [], [], {}, [], 0, 0.0)
    assert scan.sales is None


def test_a_build_whose_revenues_cover_too_few_tickers_is_a_problem():
    """FMP renaming / emptying priceToSalesRatioTTM leaves every map {} while the
    fingerprint check passes — the build must refuse rather than upload a universe that
    reads every concentration as unmeasured."""
    caps = {f"T{i}": 1e9 for i in range(60)}
    broken = [{"industry": "X", "sector": "S", "tickers": sorted(caps), "market_caps": caps, "revenues": {}}]
    assert "priceToSalesRatioTTM" in bu._revenue_coverage_problem(broken)
    healthy = [{**broken[0], "revenues": {s: 1e8 for s in list(caps)[:55]}}]
    assert bu._revenue_coverage_problem(healthy) is None
    tiny = [{**broken[0], "market_caps": {"A": 1e9}, "tickers": ["A"]}]
    assert bu._revenue_coverage_problem(tiny) is None      # too few rows to judge
