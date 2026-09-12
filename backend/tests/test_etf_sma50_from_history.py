"""L2 (2026-09-11): the ETF "50-Day Avg" key statistic was permanently "—".

`_build_etf_detail` read `quote.get("priceAvg50")`, but since `/stable/quote` went
unlicensed the quote row is `price_service._shape`'s fixed key set — no `priceAvg50` ever —
while the full daily series was already cached in the same build. The average is now a pure
function of that history, persisted in the `derived` section (whose `sma_50` key doubles as
the payload version so pre-fix Tier-2 rows are rebuilt, not served blank).
"""

from __future__ import annotations

import ast
import math
from pathlib import Path

import pytest

import app.services.etf_service as etf
from app.services.etf_service import _sma


def _hist(n: int, start: float = 100.0, step: float = 1.0):
    return [{"date": f"2026-01-{i + 1:02d}", "close": start + i * step} for i in range(n)]


def test_sma_is_the_mean_of_the_last_window_closes():
    rows = _hist(60)                     # closes 100..159, oldest first
    assert _sma(rows, 50) == pytest.approx(sum(range(110, 160)) / 50)


def test_sma_is_none_below_the_window_or_on_junk():
    assert _sma(_hist(49), 50) is None, "49 closes must not be averaged under a 50-day label"
    assert _sma([], 50) is None and _sma(None, 50) is None
    rows = _hist(60)
    rows[-1]["close"] = float("nan")     # a NaN close is skipped, not propagated
    assert _sma(rows, 50) is not None and math.isfinite(_sma(rows, 50))
    assert _sma([{"close": 0}] * 60, 50) is None, "zero closes are not prices"
    assert _sma([{"adjClose": 5.0}] * 50, 50) == 5.0


@pytest.mark.asyncio
async def test_derived_section_carries_sma50_and_rebuilds_a_pre_fix_row(monkeypatch):
    etf._cache.clear()
    svc = etf.ETFService.__new__(etf.ETFService)
    calls = {"hist": 0, "put": []}

    async def _history(symbol):
        calls["hist"] += 1
        return _hist(260)

    monkeypatch.setattr(svc, "_get_history", _history, raising=False)
    monkeypatch.setattr(svc, "_get_spy_history", _history, raising=False)
    monkeypatch.setattr(svc, "_build_performance_periods", lambda h, s: [], raising=False)
    monkeypatch.setattr(svc, "_build_benchmark_summary", lambda *a, **k: None, raising=False)
    # A Tier-2 row from before the fix: no `sma_50` key → must be treated as a MISS.
    monkeypatch.setattr(etf.ETFService, "_tier2_get",
                        staticmethod(lambda s, c: {"performance_periods": [], "benchmark_summary": None}))
    monkeypatch.setattr(etf.ETFService, "_tier2_put",
                        staticmethod(lambda s, c, payload: calls["put"].append(payload)))
    derived = await svc._get_derived("SPY")
    assert calls["hist"] == 1, "the stale Tier-2 row was served instead of rebuilt"
    assert derived["sma_50"] == pytest.approx(_sma(_hist(260), 50))
    assert calls["put"] and "sma_50" in calls["put"][0]

    # A row that HAS the key is a hit (no history pull).
    etf._cache.clear()
    monkeypatch.setattr(etf.ETFService, "_tier2_get",
                        staticmethod(lambda s, c: {"performance_periods": [], "benchmark_summary": None, "sma_50": 123.4}))
    derived = await svc._get_derived("SPY")
    assert derived["sma_50"] == 123.4 and calls["hist"] == 1


def test_the_detail_builder_reads_the_average_from_derived():
    src = (Path(etf.__file__)).read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_build_etf_detail")
    body = ast.get_source_segment(src, fn)
    assert 'derived.get("sma_50")' in body
    # ordering: the derived fetch precedes the key-statistics build
    assert body.index("self._get_derived(") < body.index("self._build_key_statistics(")
    assert body.count("self._get_derived(") == 1, "derived must be fetched once per build"
