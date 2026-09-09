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

    monkeypatch.setattr("app.integrations.coingecko.get_coingecko_client", lambda: _Boom())
    out = await chart_helper._fetch_crypto_chart_data("BTCUSD", "3M", "daily")
    assert out == [], "a CoinGecko failure must degrade to an empty chart, not raise"


@pytest.mark.asyncio
async def test_the_equity_path_never_touches_coingecko(monkeypatch):
    """Anti-vacuity: AAPL must still go to FMP."""
    def _explode():
        raise AssertionError("an equity symbol reached the CoinGecko branch")

    monkeypatch.setattr("app.integrations.coingecko.get_coingecko_client", _explode)

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
    def __init__(self, rows=None):
        self.calls = []
        self._rows = rows if rows is not None else [
            [1_757_000_000_000, 79_000.0], [1_757_000_300_000, 79_100.0],
        ]

    async def get_market_chart(self, base, days, interval=None):
        self.calls.append((base, days, interval))
        return {"prices": self._rows,
                "total_volumes": [[ts, 1.0] for ts, _ in self._rows]}


class _ExplodingFMP:
    async def get_historical_prices(self, *a, **kw):
        raise AssertionError("a crypto pair reached FMP's historical endpoint")

    async def get_intraday_prices(self, *a, **kw):
        raise AssertionError("a crypto pair reached FMP's intraday endpoint")


@pytest.mark.asyncio
@pytest.mark.parametrize("range_code", ["1D", "1W", "3M", "1Y", "5Y", "ALL"])
async def test_a_crypto_pair_never_reaches_fmp_on_any_range(monkeypatch, range_code):
    """The regression: every one of these raised FMPNotEntitledException."""
    cg = _RecordingCoinGecko()
    monkeypatch.setattr("app.integrations.coingecko.get_coingecko_client", lambda: cg)

    rows = await fetch_chart_data(_ExplodingFMP(), "BTCUSD", range_code, None,
                                  extended_hours=True)

    assert cg.calls, f"{range_code} did not reach CoinGecko"
    assert isinstance(rows, list)


@pytest.mark.asyncio
@pytest.mark.parametrize("range_code,expected_days", [("1D", 1), ("1W", 7)])
async def test_intraday_ranges_ask_for_their_own_window(monkeypatch, range_code, expected_days):
    cg = _RecordingCoinGecko()
    monkeypatch.setattr("app.integrations.coingecko.get_coingecko_client", lambda: cg)
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

    cg = _RecordingCoinGecko()
    monkeypatch.setattr("app.integrations.coingecko.get_coingecko_client", lambda: cg)
    await fetch_chart_data(_ExplodingFMP(), "BTCUSD", range_code, None)
    _base, days, interval = cg.calls[-1]
    assert interval == "daily", f"{range_code} must ask for DAILY bars, got {interval!r}"
    assert days > 7, f"{range_code} asked for {days} days — that is the 1W window"
    assert days <= int(settings.CRYPTO_HISTORY_YEARS) * 365, "asked past the plan cap"


@pytest.mark.asyncio
async def test_a_bare_ticker_that_is_a_real_security_still_goes_to_fmp(monkeypatch):
    """`BTC` is the Grayscale ETF — it must NOT be routed to CoinGecko."""
    def _explode():
        raise AssertionError("bare BTC (the Grayscale ETF) reached CoinGecko")

    monkeypatch.setattr("app.integrations.coingecko.get_coingecko_client", _explode)

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
