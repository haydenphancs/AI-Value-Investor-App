"""`fetch_chart_data` is the SHARED chart fetcher — it must serve crypto, not raise.

FMP 402s every crypto pair, so before this every call here raised
FMPNotEntitledException for one. That broke callers well beyond the crypto screen; the
most visible was `/api/v1/stocks/{ticker}/chart`, which `CryptoDetailViewModel` polls
every 30 seconds for the live intraday chart. Each tick raised, the client's `catch` only
`print`ed, and the chart silently never updated — a dead feature with no error anywhere.

Routing at this choke point (rather than per call site) is the same argument as
`price_service.get_quotes`, and it means the fix ships with the BACKEND — no iOS release
required, which matters because the shipped app is an older build.
"""

from __future__ import annotations

import inspect
import re

import pytest

from app.services import chart_helper
from app.services.chart_helper import (
    DEFAULT_INTERVALS,
    daily_range_days,
    fetch_chart_data,
    resolve_interval,
)


def _source() -> str:
    """Comment- AND DOCSTRING-stripped source of the routing function.

    Stripping the docstring is not optional here: this function's own docstring explains
    the trap by NAMING it (`resolved_interval != "daily"`), so a scan that keeps
    docstrings would find that token in the prose and pass even after the code regressed
    to exactly the thing the prose warns about. That is the documented vacuity mode in
    .claude/rules/testing.md §3, and it bit this very test when it was written.
    """
    import ast as _ast
    import textwrap

    src = textwrap.dedent(inspect.getsource(chart_helper._fetch_crypto_chart_data))
    tree = _ast.parse(src)
    fn = tree.body[0]
    # Drop a leading docstring expression, then unparse only the executable body.
    body = fn.body[1:] if (fn.body and isinstance(fn.body[0], _ast.Expr)
                           and isinstance(getattr(fn.body[0], "value", None), _ast.Constant)
                           and isinstance(fn.body[0].value.value, str)) else fn.body
    code = "\n".join(_ast.unparse(n) for n in body)
    return "\n".join(
        "" if l.strip().startswith("#") else re.sub(r"\s#.*$", "", l)
        for l in code.splitlines()
    )


def test_the_crypto_branch_exists_and_is_reached_from_the_shared_fetcher():
    body = inspect.getsource(fetch_chart_data)
    assert "_fetch_crypto_chart_data" in body, (
        "fetch_chart_data must route crypto away from FMP — every FMP call 402s for a pair"
    )
    assert "uses_coingecko_price" in body, (
        "routing must use the conjunction predicate, not `detect_asset_class == crypto`: "
        "a bare BTC is the Grayscale ETF and belongs on FMP"
    )


def test_the_fmp_path_is_preserved_behind_the_kill_switch():
    """'Hide, don't remove' — CRYPTO_PRICE_SOURCE=fmp must restore the original path."""
    body = inspect.getsource(fetch_chart_data)
    assert "CRYPTO_PRICE_SOURCE" in body


# ── the misrouting trap: branch on RANGE, never on the resolved interval ─────

def test_only_one_day_and_one_week_take_the_intraday_series():
    """`DEFAULT_INTERVALS` maps 5Y->weekly and ALL->monthly.

    An `interval != "daily"` test therefore sweeps those two into the intraday branch and
    serves SEVEN DAYS of hourly bars under a five-year label — plausible, confidently
    wrong, and worse than the exception it replaced.
    """
    src = _source()
    normalised = src.replace("'", '"')
    assert '{"1D": 1, "1W": 7}' in normalised, "intraday selection must be a RANGE map"
    assert 'resolved_interval != "daily"' not in normalised, (
        "branching on the interval misroutes 5Y and ALL into the intraday path"
    )


@pytest.mark.parametrize("code", ["5Y", "ALL"])
def test_the_long_ranges_still_resolve_non_daily(code):
    """Pins the trap itself: if this changes, the comment above needs rereading."""
    assert resolve_interval(code, None) != "daily"
    assert DEFAULT_INTERVALS[code] in {"weekly", "monthly"}


# ── the window must never exceed what the source can serve ──────────────────

def test_the_history_cap_is_applied_to_long_ranges():
    """CoinGecko Basic stops at CRYPTO_HISTORY_YEARS; 5Y/ALL clamp rather than over-ask."""
    from app.config import settings

    src = _source()
    assert "CRYPTO_HISTORY_YEARS" in src, "the long-range fetch must be capped"
    cap_days = int(settings.CRYPTO_HISTORY_YEARS) * 365
    # `daily_range_days("5Y")` is far past the cap — that is exactly why the clamp exists.
    assert daily_range_days("5Y") > cap_days


def test_a_coingecko_failure_degrades_to_an_empty_series():
    """Every other failure in this function yields []; this must match, not raise."""
    src = _source()
    assert "return []" in src
    assert "logger.warning" in src, "a degraded chart must be logged, never silent"


@pytest.mark.asyncio
async def test_an_upstream_error_returns_empty_rather_than_propagating(monkeypatch):
    class _Boom:
        async def get_market_chart(self, *a, **kw):
            raise RuntimeError("coingecko down")

    from app.integrations import coingecko as _cg
    from app.services import crypto_service as _cs

    async def _boom(_self, *a, **k):
        raise RuntimeError("coingecko down")

    monkeypatch.setattr(_cg.CoinGeckoClient, "get_market_chart", _boom)
    _cs._cache.clear()
    out = await chart_helper._fetch_crypto_chart_data("BTCUSD", "3M", "daily")
    assert out == [], "a CoinGecko failure must degrade to an empty chart, not raise"


@pytest.mark.asyncio
async def test_the_equity_path_never_touches_coingecko(monkeypatch):
    """Anti-vacuity: AAPL must still go to FMP."""
    from app.integrations import coingecko as _cg

    async def _explode(_self, *a, **k):
        raise AssertionError("an equity symbol reached the CoinGecko branch")

    monkeypatch.setattr(_cg.CoinGeckoClient, "get_market_chart", _explode)

    calls = []

    class _FMP:
        async def get_historical_prices(self, *a, **kw):
            calls.append(a)
            return []
        async def get_intraday_prices(self, *a, **kw):
            calls.append(a)
            return []

    await fetch_chart_data(_FMP(), "AAPL", "3M", None)
    assert calls, "the equity path must still reach FMP"


# ── BEHAVIOURAL: the branch must actually be TAKEN, not merely present ───────
#
# Verified by hand: a source scan alone passes when the branch is disabled
# (`if False and uses_coingecko_price(...)`), because the call is still mentioned.
# These drive the real function instead.

class _RecordingCoinGecko:
    """Records the upstream call.

    ⚠️ Patched onto `CoinGeckoClient.get_market_chart` at the CLASS level, not onto
    `get_coingecko_client`. The crypto chart path now goes through
    `crypto_service._cg_history` — the CACHED reader — which holds its own client
    instance captured in `CryptoService.__init__`, so replacing the factory function
    would not intercept it and the call would escape to the network.
    """

    def __init__(self, rows=None):
        self.calls = []
        self._rows = rows if rows is not None else [
            [1_757_000_000_000, 79_000.0], [1_757_000_300_000, 79_100.0],
        ]

    def install(self, monkeypatch):
        from app.integrations import coingecko as _cg

        recorder = self

        async def _spy(_self, base, days, interval=None):
            recorder.calls.append((base, days, interval))
            return {"prices": recorder._rows,
                    "total_volumes": [[ts, 1.0] for ts, _ in recorder._rows]}

        monkeypatch.setattr(_cg.CoinGeckoClient, "get_market_chart", _spy)
        # The cached reader would otherwise serve a previous test's rows and record zero
        # calls, making every assertion below vacuous.
        from app.services import crypto_service as _cs
        _cs._cache.clear()
        return self


class _ExplodingFMP:
    async def get_historical_prices(self, *a, **kw):
        raise AssertionError("a crypto pair reached FMP's historical endpoint")

    async def get_intraday_prices(self, *a, **kw):
        raise AssertionError("a crypto pair reached FMP's intraday endpoint")


@pytest.mark.asyncio
@pytest.mark.parametrize("range_code", ["1D", "1W", "3M", "1Y", "5Y", "ALL"])
async def test_a_crypto_pair_never_reaches_fmp_on_any_range(monkeypatch, range_code):
    """The regression: every one of these raised FMPNotEntitledException."""
    cg = _RecordingCoinGecko().install(monkeypatch)

    rows = await fetch_chart_data(_ExplodingFMP(), "BTCUSD", range_code, None,
                                  extended_hours=True)

    assert cg.calls, f"{range_code} did not reach CoinGecko"
    assert isinstance(rows, list)


@pytest.mark.asyncio
@pytest.mark.parametrize("range_code,expected_days", [("1D", 1), ("1W", 7)])
async def test_intraday_ranges_ask_for_their_own_window(monkeypatch, range_code, expected_days):
    cg = _RecordingCoinGecko().install(monkeypatch)
    await fetch_chart_data(_ExplodingFMP(), "BTCUSD", range_code, None)
    base, days, interval = cg.calls[-1]
    assert base == "BTC", f"CoinGecko takes the BARE base, got {base!r}"
    assert days == expected_days
    assert interval is None, "intraday must not force interval=daily"


@pytest.mark.asyncio
@pytest.mark.parametrize("range_code", ["5Y", "ALL"])
async def test_long_ranges_take_the_daily_series_not_seven_days_of_hourly(monkeypatch, range_code):
    """The misrouting bug, asserted behaviourally rather than by source text."""
    from app.config import settings

    cg = _RecordingCoinGecko().install(monkeypatch)
    await fetch_chart_data(_ExplodingFMP(), "BTCUSD", range_code, None)
    _base, days, interval = cg.calls[-1]
    assert interval == "daily", f"{range_code} must ask for DAILY bars, got {interval!r}"
    assert days > 7, f"{range_code} asked for {days} days — that is the 1W window"
    assert days <= int(settings.CRYPTO_HISTORY_YEARS) * 365, "asked past the plan cap"


@pytest.mark.asyncio
async def test_a_bare_ticker_that_is_a_real_security_still_goes_to_fmp(monkeypatch):
    """`BTC` is the Grayscale ETF — it must NOT be routed to CoinGecko."""
    from app.integrations import coingecko as _cg

    async def _explode(_self, *a, **k):
        raise AssertionError("bare BTC (the Grayscale ETF) reached CoinGecko")

    monkeypatch.setattr(_cg.CoinGeckoClient, "get_market_chart", _explode)

    hit = []

    class _FMP:
        async def get_historical_prices(self, *a, **kw):
            hit.append(a)
            return []
        async def get_intraday_prices(self, *a, **kw):
            hit.append(a)
            return []

    await fetch_chart_data(_FMP(), "BTC", "3M", None)
    assert hit, "bare BTC must still be served by FMP"


# ── the CACHE is the whole point on this path ───────────────────────────────

@pytest.mark.asyncio
async def test_repeated_polls_cost_one_upstream_call(monkeypatch):
    """`CryptoDetailViewModel` polls `/stocks/{t}/chart` every 30 SECONDS while open.

    Calling the CoinGecko client directly here bypassed the cached reader entirely, so a
    single continuously-open crypto screen was ~2 calls/minute — about 86,000/month
    against a plan the rest of this codebase sizes as 2.3 calls/minute sustained
    (100,000/month). A handful of concurrent viewers would have saturated the limiter and
    starved the price-alert sweeper along with it.
    """
    cg = _RecordingCoinGecko().install(monkeypatch)
    fmp = _ExplodingFMP()

    for _ in range(3):
        rows = await fetch_chart_data(fmp, "BTCUSD", "1D", None, extended_hours=True)
        assert rows, "the cached reader must still return the series"

    assert len(cg.calls) == 1, (
        f"3 polls cost {len(cg.calls)} upstream calls — the cache is being bypassed"
    )


@pytest.mark.asyncio
async def test_the_crypto_chart_goes_through_the_cached_reader(monkeypatch):
    """Structural: the call must be `_cg_history`, not the raw client.

    `_cg_history` is what owns the TTLs (120s intraday / 1h daily) and the `_inflight`
    dedup; the raw client has only in-flight dedup, which cannot help requests 30s apart.
    """
    seen = []

    async def _spy(_self, symbol, days, *, intraday=False):
        seen.append((symbol, days, intraday))
        return [{"date": "2026-09-09 10:00:00", "close": 1.0, "volume": 1.0}]

    from app.services.crypto_service import CryptoService

    monkeypatch.setattr(CryptoService, "_cg_history", _spy)
    await fetch_chart_data(_ExplodingFMP(), "BTCUSD", "1D", None)
    assert seen == [("BTC", 1, True)], f"did not reach the cached reader: {seen}"
