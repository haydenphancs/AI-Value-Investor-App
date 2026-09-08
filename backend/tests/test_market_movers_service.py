"""`market_movers_service` — the Market Performance rebuild.

FMP's Market Performance dataset (all 8 endpoints) is `402 Restricted Endpoint`; none of
the nine purchased packages names it. Movers and sector/industry moves are now derived
from the entitled `company-screener` plus the stored close snapshot.

The tests below concentrate on the three things that fail SILENTLY:

  1. A fabricated `0.0%` where the change is genuinely unknown.
  2. The `avgVolume` → `averageVolume` key rename. The screener uses one spelling and
     `_is_quality_company` reads the other; get it wrong and EVERY row is dropped at the
     quality gate with nothing in the logs, because a missing averageVolume fails it.
  3. A group "average" computed from two members, published as a statistic.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

import app.services.market_movers_service as mm
from app.services.market_movers_service import MarketMoversService, _cache


@pytest.fixture(autouse=True)
def _clear():
    _cache.clear()
    yield
    _cache.clear()


def _screener(symbol: str, price: float, *, sector="Technology", industry="Semiconductors",
              mcap=5e9, vol=1e6, avg=5e5, etf=False, fund=False) -> Dict[str, Any]:
    return {"symbol": symbol, "companyName": f"{symbol} Inc.", "price": price,
            "marketCap": mcap, "volume": vol, "avgVolume": avg, "sector": sector,
            "industry": industry, "exchangeShortName": "NASDAQ",
            "isEtf": etf, "isFund": fund}


def _wire(monkeypatch, rows: List[Dict[str, Any]], closes: Dict[str, Dict[str, Any]]):
    class _PS:
        async def _get_universe(self):
            return {r["symbol"]: r for r in rows}
    monkeypatch.setattr(mm, "price_source", lambda owner=None: _PS())
    monkeypatch.setattr(MarketMoversService, "_select_all_closes", staticmethod(lambda: closes))


# ── the key rename ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_average_volume_is_republished_under_the_profile_spelling(monkeypatch):
    """Screener says `avgVolume`; the quality gate reads `averageVolume`.

    Getting this wrong drops every row silently — `_is_quality_company` treats a missing
    averageVolume as a failure, so the scanner would render empty with no error anywhere.
    """
    _wire(monkeypatch, [_screener("AAPL", 100.0, avg=1234.0)],
          {"AAPL": {"close": 100.0, "previous_close": 90.0}})
    row = (await MarketMoversService().get_universe())["AAPL"]
    assert row["averageVolume"] == 1234.0, "the profile spelling must be present"


# ── no fabricated zeros ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_symbol_with_no_stored_close_has_an_unknown_change(monkeypatch):
    _wire(monkeypatch, [_screener("AAPL", 100.0)], {})
    row = (await MarketMoversService().get_universe())["AAPL"]
    assert row["price"] == 100.0, "the price is known and must still be served"
    assert row["changePercentage"] is None, "0.0 here would be a fabricated flat day"
    assert row["changesPercentage"] is None


@pytest.mark.asyncio
async def test_unknown_changes_are_excluded_from_the_ranking_input(monkeypatch):
    """`change_map` drives the mover ranking — an unknown must be absent, not zero."""
    _wire(monkeypatch,
          [_screener("AAPL", 110.0), _screener("MSFT", 100.0)],
          {"AAPL": {"close": 100.0, "previous_close": 90.0}})
    universe, change_map = await MarketMoversService().get_scanner_inputs()
    assert set(universe) == {"AAPL", "MSFT"}, "both are still priceable"
    assert set(change_map) == {"AAPL"}, "only the one with a real denominator ranks"


@pytest.mark.asyncio
@pytest.mark.parametrize("prev", [0, -1, None, float("nan"), float("inf")])
async def test_an_unusable_previous_close_never_divides_or_zeroes(monkeypatch, prev):
    _wire(monkeypatch, [_screener("AAPL", 110.0)],
          {"AAPL": {"close": None, "previous_close": prev}})
    row = (await MarketMoversService().get_universe())["AAPL"]
    assert row["changePercentage"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("price", [0, -5, None, float("nan"), float("inf")])
async def test_a_row_with_no_real_price_is_dropped(monkeypatch, price):
    _wire(monkeypatch, [_screener("AAPL", price)],
          {"AAPL": {"close": 100.0, "previous_close": 90.0}})
    assert await MarketMoversService().get_universe() == {}


# ── the denominator, same rule as price_service ────────────────────────────────────

@pytest.mark.asyncio
async def test_a_closed_market_measures_the_last_session(monkeypatch):
    """price == close means the market is shut; the denominator is the session before."""
    _wire(monkeypatch, [_screener("AAPL", 100.0)],
          {"AAPL": {"close": 100.0, "previous_close": 80.0}})
    row = (await MarketMoversService().get_universe())["AAPL"]
    assert row["changePercentage"] == pytest.approx(25.0)


@pytest.mark.asyncio
async def test_an_open_market_measures_from_the_latest_close(monkeypatch):
    _wire(monkeypatch, [_screener("AAPL", 110.0)],
          {"AAPL": {"close": 100.0, "previous_close": 80.0}})
    row = (await MarketMoversService().get_universe())["AAPL"]
    assert row["changePercentage"] == pytest.approx(10.0)


# ── sector / industry grouping ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sector_average_is_equal_weighted_and_matches_the_old_shape(monkeypatch):
    """Consumers read `{sector, changesPercentage}` — the shape FMP's snapshot returned."""
    rows = [_screener(f"T{i}", 110.0, sector="Technology") for i in range(5)]
    rows += [_screener(f"E{i}", 90.0, sector="Energy") for i in range(5)]
    closes = {r["symbol"]: {"close": 100.0, "previous_close": 100.0} for r in rows}
    _wire(monkeypatch, rows, closes)

    out = await MarketMoversService().get_sector_performance()
    by = {r["sector"]: r for r in out}
    assert set(by) == {"Technology", "Energy"}
    assert by["Technology"]["changesPercentage"] == pytest.approx(10.0)
    assert by["Energy"]["changesPercentage"] == pytest.approx(-10.0)
    assert out[0]["sector"] == "Technology", "sorted best-first"


@pytest.mark.asyncio
async def test_a_thinly_populated_group_is_dropped_not_published(monkeypatch):
    """Two members is not an average. A one-line card cannot show 'n=2'."""
    rows = [_screener(f"T{i}", 110.0, industry="Semiconductors") for i in range(5)]
    rows += [_screener("LONE", 200.0, industry="Uranium"),
             _screener("LONE2", 200.0, industry="Uranium")]
    closes = {r["symbol"]: {"close": 100.0, "previous_close": 100.0} for r in rows}
    _wire(monkeypatch, rows, closes)

    out = await MarketMoversService().get_industry_performance()
    names = {r["industry"] for r in out}
    assert "Semiconductors" in names
    assert "Uranium" not in names, "a 2-member 'average' must not be published"


@pytest.mark.asyncio
async def test_etfs_and_funds_do_not_pollute_a_sector_average(monkeypatch):
    """A 3x leveraged ETF's move would swamp the sector it nominally tracks."""
    rows = [_screener(f"T{i}", 101.0, sector="Technology") for i in range(5)]
    rows.append(_screener("SOXL", 200.0, sector="Technology", etf=True))
    closes = {r["symbol"]: {"close": 100.0, "previous_close": 100.0} for r in rows}
    _wire(monkeypatch, rows, closes)

    out = await MarketMoversService().get_sector_performance()
    tech = next(r for r in out if r["sector"] == "Technology")
    assert tech["changesPercentage"] == pytest.approx(1.0), (
        "the 100% ETF move must be excluded, not averaged in"
    )
    assert tech["constituents"] == 5


@pytest.mark.asyncio
async def test_a_group_with_no_computable_changes_is_absent(monkeypatch):
    rows = [_screener(f"T{i}", 100.0) for i in range(8)]
    _wire(monkeypatch, rows, {})          # no closes at all
    assert await MarketMoversService().get_sector_performance() == []


# ── degradation ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_close_map_outage_still_yields_a_priced_universe(monkeypatch):
    class _PS:
        async def _get_universe(self):
            return {"AAPL": _screener("AAPL", 100.0)}
    monkeypatch.setattr(mm, "price_source", lambda owner=None: _PS())
    def _boom():
        raise RuntimeError("supabase down")
    monkeypatch.setattr(MarketMoversService, "_select_all_closes", staticmethod(_boom))

    universe = await MarketMoversService().get_universe()
    assert universe["AAPL"]["price"] == 100.0
    assert universe["AAPL"]["changePercentage"] is None


@pytest.mark.asyncio
async def test_a_screener_outage_yields_an_empty_universe_not_an_error(monkeypatch):
    class _PS:
        async def _get_universe(self):
            raise RuntimeError("fmp down")
    monkeypatch.setattr(mm, "price_source", lambda owner=None: _PS())
    monkeypatch.setattr(MarketMoversService, "_select_all_closes", staticmethod(lambda: {}))
    assert await MarketMoversService().get_universe() == {}
    assert await MarketMoversService().get_sector_performance() == []


# ── Group performance: the fields downstream surfaces actually read ──────────────────
#
# `sector-performance-snapshot` / `industry-performance-snapshot` are outside the signed
# FMP Order Form (verified 402), so `_group_performance` — screener rows grouped and
# equal-weighted — is the substitute every consumer now reads. These pin the parts of that
# row shape whose ABSENCE is silent: a missing key does not raise, it just makes a feature
# quietly stop working.


def _universe_row(symbol, *, sector, industry, change, is_etf=False):
    return {
        "symbol": symbol, "companyName": symbol, "price": 10.0,
        "marketCap": 1e10, "volume": 1e6, "averageVolume": 1e6,
        "changePercentage": change, "changesPercentage": change,
        "sector": sector, "industry": industry, "exchange": "NASDAQ",
        "isEtf": is_etf, "isFund": False,
    }


def _fake_universe(rows):
    async def _get():
        return {r["symbol"]: r for r in rows}
    return _get


@pytest.mark.asyncio
async def test_industry_rows_carry_their_sector(monkeypatch):
    """Without `sector`, Overview's "Industry Rank" is permanently "--".

    `stock_overview_service._build_sector_industry` ranks a company's industry within its
    SECTOR by filtering `industry_perf` on `ip.get("sector")`. FMP's retired snapshot
    carried that key; the first substitute did not, so the filter matched nothing on every
    request and the row rendered "--" for every ticker, with no error anywhere.
    """
    svc = MarketMoversService()
    rows = [
        _universe_row(f"T{i}", sector="Technology", industry="Semiconductors", change=1.0)
        for i in range(mm._MIN_GROUP_MEMBERS)
    ]
    monkeypatch.setattr(svc, "get_universe", _fake_universe(rows))

    out = await svc._group_performance("industry")

    assert out, "expected one industry group"
    assert out[0]["industry"] == "Semiconductors"
    assert out[0]["sector"] == "Technology", (
        "industry rows must carry their sector — Overview's Industry Rank filters on it"
    )


@pytest.mark.asyncio
async def test_a_mislabelled_constituent_does_not_reassign_the_industry(monkeypatch):
    """The sector is the MODE, not last-write-wins.

    FMP's taxonomy is not guaranteed 1:1 and a single row can carry a stray sector. Taking
    whichever arrived last would move an entire industry out of the sector its members are
    ranked in.
    """
    svc = MarketMoversService()
    rows = [
        _universe_row(f"T{i}", sector="Technology", industry="Semiconductors", change=1.0)
        for i in range(mm._MIN_GROUP_MEMBERS)
    ]
    rows.append(_universe_row("ODD", sector="Industrials",
                              industry="Semiconductors", change=1.0))
    monkeypatch.setattr(svc, "get_universe", _fake_universe(rows))

    out = await svc._group_performance("industry")

    assert out[0]["sector"] == "Technology"


@pytest.mark.asyncio
async def test_sector_rows_do_not_carry_a_redundant_sector_key(monkeypatch):
    """`_group_performance("sector")` already keys on `sector`; no self-referential extra."""
    svc = MarketMoversService()
    rows = [
        _universe_row(f"T{i}", sector="Technology", industry="Semiconductors", change=1.0)
        for i in range(mm._MIN_GROUP_MEMBERS)
    ]
    monkeypatch.setattr(svc, "get_universe", _fake_universe(rows))

    out = await svc._group_performance("sector")

    assert out[0]["sector"] == "Technology"
    assert set(out[0]) == {"sector", "changesPercentage", "constituents"}


@pytest.mark.asyncio
async def test_an_industry_with_no_sector_on_any_row_is_still_published(monkeypatch):
    """A missing sector degrades the RANK, never the performance row itself."""
    svc = MarketMoversService()
    rows = [
        _universe_row(f"T{i}", sector=None, industry="Semiconductors", change=1.0)
        for i in range(mm._MIN_GROUP_MEMBERS)
    ]
    monkeypatch.setattr(svc, "get_universe", _fake_universe(rows))

    out = await svc._group_performance("industry")

    assert out and out[0]["industry"] == "Semiconductors"
    assert "sector" not in out[0]


# ── Which session does a group's change describe? ───────────────────────────────────


def _closed_universe(rows, closes):
    """Rows straight from the screener, plus the stored close snapshot they price against."""
    async def _get():
        return {r["symbol"]: r for r in rows}
    return _get, closes


@pytest.mark.asyncio
async def test_a_group_row_names_the_session_its_change_describes(monkeypatch):
    """`widget_movers_service.industry_for` now FAILS CLOSED on a missing date.

    FMP's `industry-performance-snapshot` carried a `date` and is outside the licence
    (402). The substitute did not, so the widget's cross-session age gate short-circuited
    and printed a previous session's move as today's cause. The gate is fail-closed now,
    which makes this stamp load-bearing in the other direction too: drop it and industry
    attribution disappears from every widget card.
    """
    svc = MarketMoversService()
    rows = [
        _universe_row(f"T{i}", sector="Technology", industry="Semiconductors", change=1.0)
        for i in range(mm._MIN_GROUP_MEMBERS)
    ]
    for r in rows:
        r["changeSession"] = "2026-09-05"
    monkeypatch.setattr(svc, "get_universe", _fake_universe(rows))

    out = await svc._group_performance("industry")

    assert out[0]["date"] == "2026-09-05"


@pytest.mark.asyncio
async def test_the_session_stamp_is_the_mode_not_the_first_row(monkeypatch):
    """A handful of thinly-traded members can lag the rest of the group."""
    svc = MarketMoversService()
    rows = [
        _universe_row(f"T{i}", sector="Technology", industry="Semiconductors", change=1.0)
        for i in range(mm._MIN_GROUP_MEMBERS + 2)
    ]
    rows[0]["changeSession"] = "2026-09-04"          # one laggard, listed first
    for r in rows[1:]:
        r["changeSession"] = "2026-09-05"
    monkeypatch.setattr(svc, "get_universe", _fake_universe(rows))

    out = await svc._group_performance("industry")

    assert out[0]["date"] == "2026-09-05"


@pytest.mark.asyncio
async def test_no_stamp_at_all_when_no_member_can_name_its_session(monkeypatch):
    """Absent, not guessed. The consumer treats an unknown date as "do not print"."""
    svc = MarketMoversService()
    rows = [
        _universe_row(f"T{i}", sector="Technology", industry="Semiconductors", change=1.0)
        for i in range(mm._MIN_GROUP_MEMBERS)
    ]
    monkeypatch.setattr(svc, "get_universe", _fake_universe(rows))

    out = await svc._group_performance("industry")

    assert "date" not in out[0]


@pytest.mark.parametrize("price, close, prev, expect_live, why", [
    (100.0, 100.0, 98.0, False,
     "premarket: the price still equals Friday's close, so the change IS Friday's move"),
    (102.0, 100.0, 98.0, True,
     "intraday: the price has moved off the stored close, so the change is the live one"),
])
def test_the_session_follows_the_denominator_the_picker_chose(price, close, prev,
                                                              expect_live, why):
    """The stamp is derived, not asserted.

    `_pick_denominator` already decides this: a price that has moved off the stored close
    belongs to a LATER session (so `close` is the denominator and the change is live),
    while a price still equal to it means the change is the one that close ENDED. Neither
    `session_trading_date()` nor the snapshot's `trade_date` alone is right — each is
    wrong for half the day, which is why the finding's first suggested fix would not have
    closed the hole.
    """
    from app.services.price_service import PriceService

    picked = PriceService._pick_denominator(price, {"close": close, "previous_close": prev})

    assert (picked == close) is expect_live, why
