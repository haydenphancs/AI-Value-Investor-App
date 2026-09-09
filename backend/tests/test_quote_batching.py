"""Multi-symbol quote fetches must use `batch-quote`, not a per-symbol loop.

WHY THIS EXISTS — measured 2026-08-21.

A cold `GET /home/dashboard` issued **~203 individual `/quote` HTTP requests**:

    themes    131   (8 active themes, 131 distinct tickers — counted in the live DB)
    earnings   40
    shorts     25
    pulse       6
    watchlist   1   <- the ONE section already using the real batch endpoint

The cause was `FMPClient.get_batch_quotes`, which despite its name fanned out to N
individual `get_stock_price_quote` calls behind a semaphore. The real batch method,
`get_batch_quotes_bulk` (`/stable/batch-quote`, comma-separated symbols), already existed
and was already used by six callers.

The reason everyone avoided it was a WRONG DOCSTRING claiming `batch-quote` omitted
eps / pe / sharesOutstanding / beta / avgVolume / dividendYield. Verified against live FMP:
the two endpoints return an IDENTICAL 17-key field set with identical values across AAPL,
SPY, ^GSPC, BTCUSD, GCUSD and BRK-B — and those six fields are absent from BOTH (they are
not on `/stable` at all). So there was never a field-set reason to loop.

`get_batch_quotes` has been removed outright so the trap cannot be picked up again.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from _price_fakes import PriceFromFMPFake

_APP = Path(__file__).resolve().parents[1] / "app"


def _quote(symbol: str) -> dict:
    """A quote carrying every field a pulse tile requires."""
    return {
        "symbol": symbol,
        "name": symbol,
        "price": 100.0,
        "change": 1.0,
        "changesPercentage": 1.0,
        "changePercentage": 1.0,
        "previousClose": 99.0,
    }


def test_the_fan_out_helper_is_gone():
    """A method named `get_batch_quotes` that is not a batch must not come back."""
    from app.integrations.fmp import FMPClient

    assert not hasattr(FMPClient, "get_batch_quotes"), (
        "get_batch_quotes is back. It fans out to N individual /quote calls despite its "
        "name — use get_batch_quotes_bulk, which is a genuine single-request batch."
    )
    assert hasattr(FMPClient, "get_batch_quotes_bulk")


def test_no_service_calls_the_retired_fan_out():
    """Anti-vacuity companion: proves nothing still references it by name."""
    # Match the CALL form (`.get_batch_quotes(`) only. The comments and docstrings that
    # explain WHY the method was retired legitimately name it — scanning raw prose is the
    # comment-vacuity trap from .claude/rules/testing.md, in reverse.
    offenders = []
    for path in sorted(_APP.rglob("*.py")):
        src = path.read_text()
        if re.search(r"\.get_batch_quotes\s*\(", src):
            offenders.append(str(path.relative_to(_APP)))
    assert not offenders, f"still CALLING the retired fan-out: {offenders}"


def test_the_call_form_scan_would_actually_catch_a_regression():
    """Anti-vacuity control for the scan above."""
    assert re.search(r"\.get_batch_quotes\s*\(", "await self.fmp.get_batch_quotes(syms)")
    assert not re.search(
        r"\.get_batch_quotes\s*\(", "await self.fmp.get_batch_quotes_bulk(syms)"
    )


def test_every_real_pulse_symbol_is_now_licensed():
    """The tripwire that used to sit here, flipped — Phase 4 landed.

    This asserted the OPPOSITE: that every `_PULSE_SYMBOLS` entry (`^GSPC`, `^IXIC`,
    `^DJI`, `BTCUSD`, `GCUSD`, `CLUSD`) was blocked at the symbol level, so Home's Market
    Pulse rendered ZERO tiles while the two batching tests below ran against entitled
    stand-ins. It was written to fail the moment the real strip became servable, as the
    reminder to re-point those tests at it. Both now drive `_PULSE_SYMBOLS` directly, and
    the stand-in list is gone.

    Kept, inverted, because the property is permanent: a blocked symbol on this strip
    means a blank tile on the first screen of the app, and nothing else would catch it —
    `_build_pulse` drops a failed tile silently by design.
    """
    import app.services.home_dashboard_service as hd
    from app.integrations.fmp_entitlements import is_blocked_symbol

    symbols = [c["symbol"] for c in hd._PULSE_SYMBOLS]
    assert symbols, "the pulse strip is empty"
    blocked = [s for s in symbols if is_blocked_symbol(s)]
    assert not blocked, (
        f"pulse symbols outside the FMP licence render as MISSING TILES: {blocked}"
    )


@pytest.mark.asyncio
async def test_market_pulse_issues_one_quote_request_for_every_tile(monkeypatch):
    """Six tiles, one `batch-quote` request — not six `/quote` calls."""
    import app.services.home_dashboard_service as hd

    calls = {"single": 0, "bulk": 0, "bulk_symbols": 0}

    class _FMP:
        async def get_stock_price_quote(self, symbol):
            calls["single"] += 1
            return _quote(symbol)

        async def get_batch_quotes_bulk(self, symbols):
            calls["bulk"] += 1
            calls["bulk_symbols"] += len(symbols)
            return [_quote(s) for s in symbols]

    svc = hd.HomeDashboardService.__new__(hd.HomeDashboardService)
    svc.fmp = _FMP()
    svc.price = PriceFromFMPFake(svc.fmp)

    async def _no_spark(symbol, extended_hours=False):
        # (series, spark_from, spark_to). The two bounds are non-optional floats on
        # MarketPulseItemResponse — returning None fails validation and drops the tile,
        # which would make this test measure the wrong thing.
        return ([], 0.0, 1.0)

    svc._fetch_sparkline = _no_spark

    # ⚠️ Driven with LICENSED symbols. This test measures BATCHING, and it used to assert
    # `len(tiles) == len(_PULSE_SYMBOLS)` against the real strip — every one of which
    # (`^GSPC`, `^IXIC`, `^DJI`, `BTCUSD`, `GCUSD`, `CLUSD`) is blocked at the symbol level.
    # `PriceService` returns `{}` for all six, so production renders ZERO tiles while this
    # asserted six, because the fake was more permissive than the service it stands in for.
    # The dead strip is Phase 4's job (ETF proxies); the test lying about it was not.

    tiles = await svc._build_pulse()

    assert len(tiles) == len(hd._PULSE_SYMBOLS), "a tile was dropped"
    assert calls["bulk"] == 1, f"expected ONE batch request, got {calls['bulk']}"
    assert calls["bulk_symbols"] == len(hd._PULSE_SYMBOLS)
    assert calls["single"] == 0, (
        f"{calls['single']} per-symbol /quote calls survived — the pulse is still "
        "fanning out"
    )


@pytest.mark.asyncio
async def test_pulse_falls_back_per_tile_when_the_batch_fails(monkeypatch):
    """Degrade a tile, never the strip.

    Anti-vacuity control for the test above: if the fallback were removed, a batch
    outage would blank the whole Market Pulse instead of costing 6 individual calls.
    """
    import app.services.home_dashboard_service as hd

    calls = {"single": 0}

    class _FMP:
        async def get_batch_quotes_bulk(self, symbols):
            raise RuntimeError("batch endpoint down")

        async def get_stock_price_quote(self, symbol):
            calls["single"] += 1
            return _quote(symbol)

    svc = hd.HomeDashboardService.__new__(hd.HomeDashboardService)
    svc.fmp = _FMP()
    svc.price = PriceFromFMPFake(svc.fmp)

    async def _no_spark(symbol, extended_hours=False):
        # (series, spark_from, spark_to). The two bounds are non-optional floats on
        # MarketPulseItemResponse — returning None fails validation and drops the tile,
        # which would make this test measure the wrong thing.
        return ([], 0.0, 1.0)

    svc._fetch_sparkline = _no_spark

    tiles = await svc._build_pulse()
    assert len(tiles) == len(hd._PULSE_SYMBOLS), "a batch failure blanked the strip"
    assert calls["single"] == len(hd._PULSE_SYMBOLS), "the per-tile fallback did not fire"


@pytest.mark.asyncio
async def test_portfolio_prices_are_one_request_and_drop_unpriceable_rows():
    """This one is a MONEY path — the prices become `market_value = shares x price`.

    It was an UNBOUNDED `asyncio.gather` with no semaphore, so a 100-holding portfolio
    opened 100 concurrent requests against a 20-connection pool.
    """
    import app.services.portfolio_insights_service as pi

    calls = {"bulk": 0, "single": 0}

    class _FMP:
        async def get_batch_quotes_bulk(self, symbols):
            calls["bulk"] += 1
            # ZZZ deliberately absent, and NVDA carries a non-finite price.
            return [
                {"symbol": "ORCL", "price": 180.0},
                {"symbol": "CRM", "price": 250.0},
                {"symbol": "NVDA", "price": float("nan")},
            ]

        async def get_stock_price_quote(self, ticker):
            calls["single"] += 1
            return {"price": 1.0}

    svc = pi.PortfolioInsightsService.__new__(pi.PortfolioInsightsService)
    original = pi.get_fmp_client
    original_price = pi.price_source
    pi.get_fmp_client = lambda: _FMP()
    # Prices no longer come from the FMP client — `price_source` is the seam now.
    pi.price_source = lambda owner=None: PriceFromFMPFake(_FMP())
    try:
        prices = await svc._fetch_prices(["ORCL", "CRM", "ZZZ", "NVDA"])
    finally:
        pi.get_fmp_client = original
        pi.price_source = original_price

    assert calls["bulk"] == 1
    assert calls["single"] == 0, "still fanning out per holding"
    assert prices == {"ORCL": 180.0, "CRM": 250.0}, (
        "an unpriceable symbol must be ABSENT so the caller falls back to the stored "
        f"market_value; got {prices}"
    )
