"""F16-7 — the Related rails must not fabricate "+0.0%" (or "$0.00") for an UNKNOWN change.

`RelatedTickerResponse.change_percent` is a non-Optional Double on iOS, coloured off
`>= 0`, so a coerced 0.0 renders as a green flat day. The batch quote path
(`price_service._from_screener`) answers `changePercentage: None` for exactly a stale or
missing close snapshot — 46 stale symbols per batch in the 2026-09-17 log — and the whole
`closes` map degrades to `{}` on a Supabase error. Both builders now mirror the crypto
rail (`crypto_service._build_related_cryptos`): omit the row, keep a genuine 0.0.

Covers `etf_service._build_related_etfs` and `stock_overview_service._build_related_tickers`.
(`commodity_service` step 8 has the same defect and is out of this lane.)
"""
from __future__ import annotations

import math

import pytest

from app.services import etf_service as E
from app.services import stock_overview_service as S


class _Price:
    def __init__(self, rows):
        self.rows = rows

    async def get_quotes_list(self, symbols):
        return self.rows


def _etf_svc(rows):
    svc = E.ETFService.__new__(E.ETFService)
    svc.price = _Price(rows)
    return svc


def _stock_svc(rows, peers=("VOO", "IVV", "QQQ")):
    svc = S.StockOverviewService.__new__(S.StockOverviewService)
    svc.price = _Price(rows)

    class _FMP:
        async def get_stock_peers(self, ticker):
            return list(peers)

    svc.fmp = _FMP()
    return svc


# SPY's curated siblings: VOO, IVV, QQQ, DIA, IWM, VTI.
def _rows():
    return [
        {"symbol": "VOO", "name": "Vanguard S&P 500", "price": 500.1, "changePercentage": None},
        {"symbol": "IVV", "name": "iShares Core S&P", "price": 501.2, "changePercentage": 0.0},
        {"symbol": "QQQ", "name": "Invesco QQQ", "price": 480.3, "changePercentage": -1.234},
        {"symbol": "DIA", "name": "SPDR Dow", "price": None, "changePercentage": 0.5},
        {"symbol": "IWM", "name": "iShares Russell", "price": 0, "changePercentage": 0.5},
        {"symbol": "VTI", "name": "Vanguard Total", "price": float("nan"), "changePercentage": 0.5},
    ]


@pytest.mark.asyncio
async def test_etf_a_related_row_with_no_change_is_omitted_and_a_real_zero_is_kept():
    out = await _etf_svc(_rows())._build_related_etfs("SPY")
    by = {r.symbol: r for r in out}
    assert "VOO" not in by, "changePercentage: None rendered as a green +0.0%"
    assert by["IVV"].change_percent == 0.0, "a genuine flat day must survive"
    assert by["QQQ"].change_percent == -1.23
    assert by["QQQ"].price == 480.3


@pytest.mark.asyncio
async def test_etf_a_related_row_with_no_price_is_omitted():
    out = await _etf_svc(_rows())._build_related_etfs("SPY")
    syms = {r.symbol for r in out}
    assert not ({"DIA", "IWM", "VTI"} & syms), "a $0.00 / NaN sibling was rendered"
    assert all(r.price > 0 and math.isfinite(r.change_percent) for r in out)


@pytest.mark.asyncio
async def test_etf_the_plural_fallback_key_is_read_only_when_the_singular_is_absent():
    rows = [
        {"symbol": "VOO", "price": 500.0, "changePercentage": 0.0, "changesPercentage": 9.9},
        {"symbol": "IVV", "price": 500.0, "changesPercentage": 2.5},
        {"symbol": "QQQ", "price": 500.0, "changePercentage": None, "changesPercentage": None},
    ]
    by = {r.symbol: r for r in await _etf_svc(rows)._build_related_etfs("SPY")}
    assert by["VOO"].change_percent == 0.0, "`a or b` folded a real 0.0 into the fallback"
    assert by["IVV"].change_percent == 2.5
    assert "QQQ" not in by


@pytest.mark.asyncio
async def test_etf_a_closes_outage_yields_an_empty_rail_not_six_flat_days():
    """Every sibling with `changePercentage: None` — what a Supabase error on the closes
    map produces — is omitted, so the rail is empty rather than uniformly green."""
    rows = [{"symbol": s, "price": 100.0, "changePercentage": None}
            for s in ("VOO", "IVV", "QQQ", "DIA", "IWM", "VTI")]
    assert await _etf_svc(rows)._build_related_etfs("SPY") == []


@pytest.mark.asyncio
async def test_etf_malformed_batch_rows_never_raise():
    rows = ["junk", None, {"symbol": None}, {"symbol": "VOO", "price": "abc", "changePercentage": "x"},
            {"symbol": "IVV", "price": True, "changePercentage": 1.0},
            {"symbol": "QQQ", "price": "480.5", "changePercentage": "1.5"}]
    out = await _etf_svc(rows)._build_related_etfs("SPY")
    assert [r.symbol for r in out] == ["QQQ"]
    assert out[0].price == 480.5 and out[0].change_percent == 1.5


@pytest.mark.asyncio
async def test_etf_a_failed_batch_quote_is_an_empty_rail():
    class _Dead:
        async def get_quotes_list(self, symbols):
            raise RuntimeError("FMP 429")

    svc = E.ETFService.__new__(E.ETFService)
    svc.price = _Dead()
    assert await svc._build_related_etfs("SPY") == []


# ── stock_overview_service twin ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stock_a_related_row_with_no_change_is_omitted_and_a_real_zero_is_kept():
    rows = [
        {"symbol": "VOO", "name": "A", "price": 10.0, "changePercentage": None},
        {"symbol": "IVV", "name": "B", "price": 10.0, "changePercentage": 0.0},
        {"symbol": "QQQ", "name": "C", "price": 10.0, "changePercentage": 3.456},
    ]
    by = {r.symbol: r for r in await _stock_svc(rows)._build_related_tickers("AAPL")}
    assert "VOO" not in by
    assert by["IVV"].change_percent == 0.0
    assert by["QQQ"].change_percent == 3.46


@pytest.mark.asyncio
async def test_stock_a_related_row_with_no_price_is_omitted():
    rows = [
        {"symbol": "VOO", "price": None, "changePercentage": 1.0},
        {"symbol": "IVV", "price": 0.0, "changePercentage": 1.0},
        {"symbol": "QQQ", "price": float("inf"), "changePercentage": 1.0},
        {"symbol": "DIA", "price": -5.0, "changePercentage": 1.0},
    ]
    assert await _stock_svc(rows)._build_related_tickers("AAPL") == []


@pytest.mark.asyncio
async def test_stock_the_plural_fallback_does_not_swallow_a_real_zero():
    rows = [{"symbol": "VOO", "price": 10.0, "changePercentage": 0.0, "changesPercentage": 7.0},
            {"symbol": "IVV", "price": 10.0, "changesPercentage": 7.0}]
    by = {r.symbol: r for r in await _stock_svc(rows)._build_related_tickers("AAPL")}
    assert by["VOO"].change_percent == 0.0
    assert by["IVV"].change_percent == 7.0


@pytest.mark.asyncio
async def test_stock_malformed_rows_and_nan_change_never_raise():
    rows = [None, 3, {"symbol": ""}, {"symbol": "VOO", "price": 10.0, "changePercentage": float("nan")},
            {"symbol": "IVV", "price": 10.0, "changePercentage": "2.0"}]
    out = await _stock_svc(rows)._build_related_tickers("AAPL")
    assert [r.symbol for r in out] == ["IVV"]
    assert out[0].change_percent == 2.0


@pytest.mark.asyncio
async def test_stock_no_peers_is_an_empty_rail():
    assert await _stock_svc([], peers=())._build_related_tickers("AAPL") == []


# ── commodity: the same three-state rule ─────────────────────────────────────


def _commodity_rail(pairs):
    from app.services import commodity_service as M
    return M.CommodityService._build_related_commodities(pairs)


def test_commodity_a_related_row_with_no_change_is_omitted_and_a_real_zero_is_kept():
    rows = _commodity_rail([
        ("SIUSD", {"price": 31.2, "changePercentage": None}),      # no change → omitted
        ("PLUSD", {"price": 990.0, "changePercentage": 0.0}),      # a real flat day → kept
        ("PAUSD", {"price": 1100.0, "changesPercentage": -1.25}),  # plural fallback
    ])
    assert [r.symbol for r in rows] == ["PLUSD", "PAUSD"]
    assert rows[0].change_percent == 0.0 and rows[1].change_percent == -1.25


def test_commodity_a_related_row_with_no_price_or_a_nan_never_renders_zero():
    rows = _commodity_rail([
        ("SIUSD", {"price": None, "changePercentage": 1.0}),
        ("PLUSD", {"price": 0, "changePercentage": 1.0}),
        ("PAUSD", {"price": float("nan"), "changePercentage": 1.0}),
        ("GCUSD", {"price": 2400.0, "changePercentage": float("inf")}),
        ("CLUSD", "not a dict"),
        "not a pair",
    ])
    assert rows == []


def test_commodity_the_singular_key_wins_and_a_real_zero_is_not_swallowed_by_the_plural():
    rows = _commodity_rail([("SIUSD", {"price": 31.2, "changePercentage": 0.0, "changesPercentage": 4.0})])
    assert rows[0].change_percent == 0.0
