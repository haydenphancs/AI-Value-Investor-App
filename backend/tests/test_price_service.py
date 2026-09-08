"""`price_service` — the quote-family replacement.

Covers the two invariants that matter more than the happy path:

  1. An unknown day change is ``None``, never ``0.0``. This repo has shipped the
     fabricated-zero bug more than once (the ETF "Well Diversified" badge on no data;
     `index_service` painting ``$0.00`` under a live market-status badge), and here the
     trigger is ordinary — migration 157 not yet applied, or a symbol with no stored
     close. A 0.00% day change on a user's own holdings is a wrong number, not a blank.
  2. Unlicensed symbols never reach FMP, and never reach the close snapshot. `batch-eod`
     serves ^GSPC / GCUSD / BTCUSD / EURUSD even though the per-symbol endpoint 402s them,
     so the filter is the only thing standing between us and ingesting data we did not buy.

Hermetic: FMP and Supabase are both stubbed.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, Dict, List

import pytest

import app.services.price_service as ps_module
from app.services.price_service import PriceService, _cache, _finite


@pytest.fixture(autouse=True)
def _clear_cache():
    _cache.clear()
    yield
    _cache.clear()


def _screener_row(symbol: str, price: float, **over: Any) -> Dict[str, Any]:
    row = {"symbol": symbol, "companyName": f"{symbol} Inc.", "price": price,
           "volume": 1_000_000, "avgVolume": 2_000_000, "marketCap": 5_000_000_000,
           "exchangeShortName": "NASDAQ", "isEtf": False, "isFund": False}
    row.update(over)
    return row


class _FakeFMP:
    def __init__(self, screener=None, profiles=None, eod=None):
        self.screener_rows = screener or []
        self.profiles = profiles or {}
        self.eod_rows = eod or []
        self.profile_calls: List[str] = []

    async def get_company_screener(self, **kw):
        return self.screener_rows if kw.get("page", 0) == 0 else []

    async def get_company_profile(self, ticker):
        self.profile_calls.append(ticker)
        return self.profiles.get(ticker.upper(), {})

    async def get_batch_eod(self, trade_date):
        return self.eod_rows


def _install(monkeypatch, fake: _FakeFMP):
    monkeypatch.setattr(ps_module, "get_fmp_client", lambda: fake)
    return fake


# ── shaping ────────────────────────────────────────────────────────────────────────

def test_output_is_quote_shaped_with_both_percentage_spellings():
    """39 call sites read these keys directly; 32 use one spelling and 25 the other."""
    row = PriceService._shape(
        symbol="AAPL", name="Apple Inc.", price=100.0, previous_close=95.0,
        change=5.0, change_pct=5.263, volume=1.0, avg_volume=2.0,
        market_cap=3.0, exchange="NASDAQ",
    )
    for key in ("symbol", "name", "price", "change", "changePercentage",
                "changesPercentage", "previousClose", "volume", "avgVolume",
                "marketCap", "exchange"):
        assert key in row, f"consumers read {key!r}"
    assert row["changePercentage"] == row["changesPercentage"]


@pytest.mark.parametrize("value,expected", [
    (1.5, 1.5), ("2.5", 2.5), (0, 0.0), (-3, -3.0),
    (None, None), ("", None), ("abc", None), ([], None), ({}, None),
    (float("nan"), None), (float("inf"), None), (float("-inf"), None),
    (True, None), (False, None),
])
def test_finite_rejects_every_non_number(value, expected):
    """NaN defeats both `<= 0` guards and `except (TypeError, ValueError)`."""
    assert _finite(value) == expected


# ── single symbol ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_single_quote_carries_change_and_derives_previous_close(monkeypatch):
    _install(monkeypatch, _FakeFMP(profiles={"AAPL": {
        "symbol": "AAPL", "companyName": "Apple Inc.", "price": 319.97,
        "change": -8.24, "changePercentage": -2.51059, "volume": 39_606_884,
        "averageVolume": 52_997_011, "marketCap": 4_699_513_299_320, "exchange": "NASDAQ",
    }}))
    q = await PriceService().get_quote("aapl")
    assert q["symbol"] == "AAPL" and q["price"] == 319.97
    assert q["changePercentage"] == -2.51059
    # profile has no previousClose field; price - change reconstructs it. Verified
    # against the real batch-eod close for the prior session: 328.21.
    assert q["previousClose"] == pytest.approx(328.21, abs=0.01)


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol", ["^GSPC", "GCUSD", "BTCUSD", "EURUSD"])
async def test_blocked_symbols_return_none_without_calling_fmp(monkeypatch, symbol):
    fake = _install(monkeypatch, _FakeFMP())
    assert await PriceService().get_quote(symbol) == {}
    assert fake.profile_calls == [], "an unlicensed symbol must not reach FMP at all"


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, [], None])
async def test_empty_profile_yields_an_empty_dict_not_a_zero_quote(monkeypatch, payload):
    fake = _FakeFMP()
    fake.profiles = {"AAPL": payload}
    _install(monkeypatch, fake)
    assert await PriceService().get_quote("AAPL") == {}


@pytest.mark.asyncio
async def test_upstream_failure_degrades_to_an_empty_quote(monkeypatch):
    class _Boom(_FakeFMP):
        async def get_company_profile(self, ticker):
            raise RuntimeError("upstream down")
    _install(monkeypatch, _Boom())
    assert await PriceService().get_quote("AAPL") == {}


@pytest.mark.asyncio
async def test_blank_symbol_is_not_a_request(monkeypatch):
    fake = _install(monkeypatch, _FakeFMP())
    for bad in ["", "   ", None]:
        assert await PriceService().get_quote(bad) == {}
    assert fake.profile_calls == []


# ── batch ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_batch_without_stored_closes_reports_unknown_change_not_zero(monkeypatch):
    """THE INVARIANT. Before migration 157 lands, every batch row hits this path."""
    _install(monkeypatch, _FakeFMP(screener=[_screener_row("AAPL", 319.97)]))
    monkeypatch.setattr(PriceService, "_select_closes", staticmethod(lambda syms: []))

    out = await PriceService().get_quotes(["AAPL"])
    q = out["AAPL"]
    assert q["price"] == 319.97, "the price is known and must still be served"
    assert q["change"] is None, "0.0 here would be a fabricated day change"
    assert q["changePercentage"] is None
    assert q["changesPercentage"] is None
    assert q["previousClose"] is None


@pytest.mark.asyncio
async def test_batch_computes_change_against_the_stored_close(monkeypatch):
    _install(monkeypatch, _FakeFMP(screener=[_screener_row("AAPL", 110.0)]))
    monkeypatch.setattr(PriceService, "_select_closes",
                        staticmethod(lambda syms: [{"symbol": "AAPL", "close": 100.0}]))
    q = (await PriceService().get_quotes(["AAPL"]))["AAPL"]
    assert q["previousClose"] == 100.0
    assert q["change"] == pytest.approx(10.0)
    assert q["changePercentage"] == pytest.approx(10.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_close", [0, -5, float("nan"), float("inf"), None, "x"])
async def test_a_bad_previous_close_never_produces_a_division_or_a_zero(monkeypatch, bad_close):
    """A zero close would be a ZeroDivisionError; a NaN would poison the percentage."""
    _install(monkeypatch, _FakeFMP(screener=[_screener_row("AAPL", 110.0)]))
    monkeypatch.setattr(PriceService, "_select_closes",
                        staticmethod(lambda syms: [{"symbol": "AAPL", "close": bad_close}]))
    q = (await PriceService().get_quotes(["AAPL"]))["AAPL"]
    assert q["price"] == 110.0
    assert q["change"] is None and q["changePercentage"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_price", [float("nan"), float("inf"), None, "x"])
async def test_a_row_with_no_real_price_is_dropped_entirely(monkeypatch, bad_price):
    """Better absent than present-at-zero — the caller drops the tile."""
    _install(monkeypatch, _FakeFMP(screener=[_screener_row("AAPL", bad_price)]))
    monkeypatch.setattr(PriceService, "_select_closes", staticmethod(lambda syms: []))
    assert "AAPL" not in await PriceService().get_quotes(["AAPL"])


@pytest.mark.asyncio
async def test_batch_excludes_unlicensed_symbols(monkeypatch):
    _install(monkeypatch, _FakeFMP(screener=[_screener_row("AAPL", 100.0)]))
    monkeypatch.setattr(PriceService, "_select_closes", staticmethod(lambda syms: []))
    out = await PriceService().get_quotes(["AAPL", "^GSPC", "BTCUSD", "GCUSD"])
    assert set(out) == {"AAPL"}


@pytest.mark.asyncio
async def test_symbol_missing_from_the_universe_falls_back_to_the_single_path(monkeypatch):
    """The screener covers actively-traded US listings above the cap — not everything."""
    fake = _install(monkeypatch, _FakeFMP(
        screener=[_screener_row("AAPL", 100.0)],
        profiles={"SHOP.TO": {"symbol": "SHOP.TO", "companyName": "Shopify",
                              "price": 180.0, "change": 1.0, "changePercentage": 0.56,
                              "exchange": "TSX"}},
    ))
    monkeypatch.setattr(PriceService, "_select_closes", staticmethod(lambda syms: []))
    out = await PriceService().get_quotes(["AAPL", "SHOP.TO"])
    assert set(out) == {"AAPL", "SHOP.TO"}
    assert fake.profile_calls == ["SHOP.TO"], "only the miss falls through"


@pytest.mark.asyncio
async def test_empty_input_makes_no_upstream_call(monkeypatch):
    fake = _install(monkeypatch, _FakeFMP())
    assert await PriceService().get_quotes([]) == {}
    assert await PriceService().get_quotes(["", "  ", None]) == {}
    assert fake.profile_calls == []


@pytest.mark.asyncio
async def test_supabase_outage_still_serves_prices(monkeypatch):
    """A missing table (pre-migration) or a DB blip must not blank the whole screen."""
    _install(monkeypatch, _FakeFMP(screener=[_screener_row("AAPL", 100.0)]))
    def _boom(symbols): raise RuntimeError("PGRST205 relation missing")
    monkeypatch.setattr(PriceService, "_select_closes", staticmethod(_boom))
    q = (await PriceService().get_quotes(["AAPL"]))["AAPL"]
    assert q["price"] == 100.0 and q["changePercentage"] is None


# ── daily ingest ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ingest_drops_every_unlicensed_symbol(monkeypatch):
    """🔴 The compliance filter. batch-eod serves these; no purchased package covers them."""
    captured: List[Dict[str, Any]] = []
    _install(monkeypatch, _FakeFMP(eod=[
        # A real US session (bellwethers present) that also carries the unlicensed
        # symbols batch-eod ships even though the per-symbol endpoint 402s them.
        {"symbol": "AAPL", "date": "2026-09-04", "close": 319.97, "volume": 1},
        {"symbol": "MSFT", "date": "2026-09-04", "close": 499.70, "volume": 1},
        {"symbol": "^GSPC", "date": "2026-09-04", "close": 7718.6, "volume": 1},
        {"symbol": "GCUSD", "date": "2026-09-04", "close": 4476.6, "volume": 1},
        {"symbol": "BTCUSD", "date": "2026-09-04", "close": 79675.12, "volume": 1},
        {"symbol": "EURUSD", "date": "2026-09-04", "close": 1.1, "volume": 1},
    ]))
    monkeypatch.setattr(PriceService, "_upsert_closes",
                        staticmethod(lambda p: captured.extend(p) or len(p)))
    written = await PriceService().refresh_close_snapshot("2026-09-04")
    assert written == 2
    assert [r["symbol"] for r in captured] == ["AAPL", "MSFT"], (
        "an unlicensed close must never be persisted — this filter is the only thing "
        "preventing ingest through FMP's own enforcement gap"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("close", [0, -1, float("nan"), float("inf"), None, "junk"])
async def test_ingest_rejects_unusable_closes(monkeypatch, close):
    """A row with an unusable close is DROPPED while the rest of the session is written.

    ⚠️ This used to feed a lone `{"symbol": "AAPL", "close": <bad>}` row, which fails the
    bellwether quorum — so the ingest bailed at the "no session" branch and the
    `close is None or close <= 0` filter this test names was never reached. All six
    parametrised values were equivalent no-ops asserting the same unrelated early return.
    """
    captured: List[Dict[str, Any]] = []
    bad = {"symbol": "BADX", "date": "2026-09-04", "close": close, "volume": 1000}
    _install(monkeypatch, _SessionFMP({
        "2026-09-04": _session("2026-09-04", bad),
        "2026-09-03": _session("2026-09-03", {**bad, "date": "2026-09-03"}),
    }))
    monkeypatch.setattr(PriceService, "_upsert_closes",
                        staticmethod(lambda p: captured.extend(p) or len(p)))
    written = await PriceService().refresh_close_snapshot("2026-09-04")

    symbols = [r["symbol"] for r in captured]
    assert "BADX" not in symbols, f"a close of {close!r} was persisted"
    # ...and the good rows still land, which is what proves the filter is per-row rather
    # than an early return that happens to write nothing.
    assert symbols == ["AAPL", "MSFT"] and written == 2


@pytest.mark.asyncio
async def test_ingest_survives_an_upstream_failure(monkeypatch):
    class _Boom(_FakeFMP):
        async def get_batch_eod(self, trade_date):
            raise RuntimeError("upstream down")
    _install(monkeypatch, _Boom())
    assert await PriceService().refresh_close_snapshot("2026-09-04") == 0


@pytest.mark.asyncio
async def test_ingest_of_an_empty_session_writes_nothing(monkeypatch):
    """A market holiday returns no rows; the previous snapshot must survive untouched."""
    _install(monkeypatch, _FakeFMP(eod=[]))
    monkeypatch.setattr(PriceService, "_upsert_closes",
                        staticmethod(lambda p: pytest.fail("must not write")))
    assert await PriceService().refresh_close_snapshot("2026-09-07") == 0


@pytest.mark.parametrize("today,expected", [
    (date(2026, 9, 8), "2026-09-07"),   # Tue -> Mon
    (date(2026, 9, 7), "2026-09-04"),   # Mon -> Fri (skips the weekend)
    (date(2026, 9, 6), "2026-09-04"),   # Sun -> Fri
    (date(2026, 9, 5), "2026-09-04"),   # Sat -> Fri
])
def test_last_trading_day_skips_weekends(today, expected):
    assert PriceService._last_trading_day(today) == expected


# ── the denominator bug (migration 158) ────────────────────────────────────────────
#
# Found by cross-checking the batch path against `profile`: the batch path called AAPL
# flat (+0.00%) at the same instant `profile` reported -2.51%. Both were reading correct
# data — the batch path was just dividing by the wrong session.

@pytest.mark.parametrize("price,close,prev,expected,why", [
    # Market OPEN: the live price has moved off the stored close, so that close is the
    # session boundary and the right denominator.
    (110.0, 100.0, 90.0, 100.0, "open market uses the latest close"),
    # Market CLOSED: the live price IS the latest close. Using it would give exactly
    # 0.00% — the bug. The session before it is the right denominator.
    (100.0, 100.0, 90.0, 90.0, "closed market must step back one session"),
    # Float round-tripping through JSON and Postgres NUMERIC must not read as "moved".
    (100.0000000001, 100.0, 90.0, 90.0, "epsilon tolerance, still 'equal'"),
    # Degraded rows: an unknown denominator is None, never a silent substitute.
    (100.0, 100.0, None, None, "no prior session -> unknown, not 0%"),
    (110.0, None, 90.0, 90.0, "no latest close -> fall back to the prior one"),
    (None, 100.0, 90.0, None, "no price -> nothing to compare"),
    (100.0, None, None, None, "nothing stored at all"),
])
def test_denominator_picks_the_session_before_the_price(price, close, prev, expected, why):
    snap = {"close": close, "previous_close": prev}
    assert PriceService._pick_denominator(price, snap) == expected, why


def test_denominator_with_no_snapshot_row_is_unknown():
    assert PriceService._pick_denominator(100.0, None) is None


@pytest.mark.asyncio
async def test_closed_market_reports_the_last_sessions_move_not_zero(monkeypatch):
    """THE REGRESSION. Overnight, at weekends and on holidays, price == close.

    Before 158 this produced +0.00% on every tile — a fabricated flat market, and the
    exact failure class `price_service` exists to prevent.
    """
    _install(monkeypatch, _FakeFMP(screener=[_screener_row("AAPL", 319.97)]))
    monkeypatch.setattr(PriceService, "_select_closes", staticmethod(lambda syms: [
        {"symbol": "AAPL", "close": 319.97, "previous_close": 328.21},
    ]))
    q = (await PriceService().get_quotes(["AAPL"]))["AAPL"]
    assert q["change"] == pytest.approx(-8.24, abs=0.01)
    assert q["changePercentage"] == pytest.approx(-2.51, abs=0.01), (
        "must match what FMP profile reports for the same instant"
    )


@pytest.mark.asyncio
async def test_open_market_measures_from_the_previous_close(monkeypatch):
    _install(monkeypatch, _FakeFMP(screener=[_screener_row("AAPL", 330.0)]))
    monkeypatch.setattr(PriceService, "_select_closes", staticmethod(lambda syms: [
        {"symbol": "AAPL", "close": 319.97, "previous_close": 328.21},
    ]))
    q = (await PriceService().get_quotes(["AAPL"]))["AAPL"]
    assert q["previousClose"] == 319.97, "an in-progress session measures from yesterday"
    assert q["changePercentage"] == pytest.approx((330.0 / 319.97 - 1) * 100, abs=0.01)


# ── two-session ingest ─────────────────────────────────────────────────────────────

def _eod(symbol: str, d: str, close: float):
    return {"symbol": symbol, "date": d, "close": close, "volume": 1000}


def _session(d: str, *extra: dict) -> list:
    """One US trading session's rows.

    Always carries the bellwethers `_is_us_session` probes for. `batch-eod` is a GLOBAL
    feed, so "there are rows" does not mean the US traded — on Labor Day 2026-09-07 it
    returned 40,159 international rows with AAPL absent. A fixture without them is not a
    US session and the ingest is right to reject it.
    """
    return [_eod("AAPL", d, 100.0), _eod("MSFT", d, 200.0), *extra]


class _SessionFMP(_FakeFMP):
    """batch-eod keyed by date, so holiday gaps can be simulated."""

    def __init__(self, sessions):
        super().__init__()
        self.sessions = sessions
        self.requested: List[str] = []

    async def get_batch_eod(self, trade_date):
        self.requested.append(trade_date)
        return self.sessions.get(trade_date, [])


@pytest.mark.asyncio
async def test_ingest_pairs_the_two_most_recent_sessions(monkeypatch):
    captured: List[Dict[str, Any]] = []
    _install(monkeypatch, _SessionFMP({
        "2026-09-04": _session("2026-09-04", _eod("ZZZ", "2026-09-04", 319.97)),
        "2026-09-03": _session("2026-09-03", _eod("ZZZ", "2026-09-03", 328.21)),
    }))
    monkeypatch.setattr(PriceService, "_upsert_closes",
                        staticmethod(lambda p: captured.extend(p) or len(p)))
    assert await PriceService().refresh_close_snapshot("2026-09-04") == 3
    row = next(r for r in captured if r["symbol"] == "ZZZ")
    assert row["close"] == 319.97
    assert row["previous_close"] == 328.21
    assert row["previous_trade_date"] == "2026-09-03"


@pytest.mark.asyncio
async def test_ingest_walks_back_over_a_market_holiday(monkeypatch):
    """A holiday is a weekday with no session — Labor Day is how this was found."""
    fake = _install(monkeypatch, _SessionFMP({
        "2026-09-04": _session("2026-09-04"),
        "2026-09-03": _session("2026-09-03"),
        # Labor Day: a WEEKDAY with rows — international exchanges traded — but no US
        # session, so no bellwethers. This is the exact shape that fooled the old check.
        "2026-09-07": [_eod("SHOP.TO", "2026-09-07", 90.0),
                       _eod("2205.HK", "2026-09-07", 3.0)],
    }))
    captured: List[Dict[str, Any]] = []
    monkeypatch.setattr(PriceService, "_upsert_closes",
                        staticmethod(lambda p: captured.extend(p) or len(p)))
    await PriceService().refresh_close_snapshot("2026-09-07")

    assert "2026-09-07" in fake.requested, "it must try the holiday first"
    assert "2026-09-04" in fake.requested, "then step back to the real session"

    # ⚠️ Assert on SYMBOLS, not the row count. Mutation-testing caught this: with the
    # broken "any rows means a session" check the job accepts Labor Day and writes the two
    # international rows instead — also a count of 2, so a count assertion passed happily
    # while the entire US universe was being skipped.
    written = {r["symbol"] for r in captured}
    assert "AAPL" in written and "MSFT" in written, (
        f"must ingest the US session, got {sorted(written)}"
    )
    assert "SHOP.TO" not in written, (
        "the Labor Day international rows must not be mistaken for a US session"
    )
    assert all(r["trade_date"] == "2026-09-04" for r in captured)


@pytest.mark.asyncio
async def test_ingest_without_a_prior_session_writes_nothing(monkeypatch):
    """⚠️ THIS TEST'S EXPECTATION WAS INVERTED on 2026-09-07, by evidence.

    It used to assert the opposite — "half the data is better than none: price still
    renders, change % reads unknown" — and that reasoning was simply wrong. The upsert
    replaces the whole row, so writing a close with no prior session sets
    `previous_close = NULL` on EVERY symbol, destroying a denominator that was still
    perfectly good.

    Observed in production hours later: the hourly loop hit FMP's burst limiter on the
    second batch-eod call and nulled `previous_close` across all 63,394 rows, turning the
    day change into "unknown" app-wide long after a successful ingest had populated it.

    Skipping is strictly safer. The stored (close, previous_close) pair stays internally
    consistent, yesterday's close is still yesterday's close, and the loop retries within
    the hour. A first-ever ingest into an empty table needs both calls to succeed, which
    the rate-limit backoff makes the normal case.
    """
    _install(monkeypatch, _SessionFMP({"2026-09-04": _session("2026-09-04")}))
    monkeypatch.setattr(PriceService, "_upsert_closes",
                        staticmethod(lambda p: pytest.fail(
                            "must not write — this is the destructive partial write"
                        )))
    assert await PriceService().refresh_close_snapshot("2026-09-04") == 0


@pytest.mark.asyncio
async def test_session_lookback_is_bounded(monkeypatch):
    """An upstream returning nothing forever must terminate, not spin."""
    fake = _install(monkeypatch, _SessionFMP({}))
    monkeypatch.setattr(PriceService, "_upsert_closes",
                        staticmethod(lambda p: pytest.fail("must not write")))
    assert await PriceService().refresh_close_snapshot("2026-09-04") == 0
    assert len(fake.requested) <= 12, f"unbounded lookback: {len(fake.requested)} calls"


@pytest.mark.parametrize("start,expected", [
    ("2026-09-07", "2026-09-04"),   # Mon -> Fri
    ("2026-09-04", "2026-09-03"),   # Fri -> Thu
    ("2026-09-08", "2026-09-07"),   # Tue -> Mon (holiday handled by the data, not here)
])
def test_step_back_skips_weekends(start, expected):
    assert PriceService._step_back(start) == expected


# ── rate-limit backoff on the bulk ingest ──────────────────────────────────────────
#
# Found in production: a full ingest makes TWO ~12 MB batch-eod calls back to back, and
# FMP's burst limiter rejected the second almost every time. The failure was silent in the
# worst way — 62,893 closes written correctly, every `previous_close` NULL, and the day
# change therefore unknown across the whole app.

@pytest.mark.asyncio
async def test_a_rate_limited_batch_eod_is_retried(monkeypatch):
    from app.integrations.fmp import FMPRateLimitException

    attempts = {"n": 0}

    class _Limited(_SessionFMP):
        async def get_batch_eod(self, trade_date):
            self.requested.append(trade_date)
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise FMPRateLimitException("burst limit")
            return self.sessions.get(trade_date, [])

    _install(monkeypatch, _Limited({
        "2026-09-04": _session("2026-09-04"),
        "2026-09-03": _session("2026-09-03"),
    }))
    monkeypatch.setattr(ps_module, "_RATE_LIMIT_BACKOFF_SECONDS", 0)
    captured: List[Dict[str, Any]] = []
    monkeypatch.setattr(PriceService, "_upsert_closes",
                        staticmethod(lambda p: captured.extend(p) or len(p)))

    assert await PriceService().refresh_close_snapshot("2026-09-04") == 2
    assert attempts["n"] >= 2, "the rate-limited call must be retried, not abandoned"
    assert next(r for r in captured if r["symbol"] == "AAPL")["previous_close"] == 100.0, (
        "the whole point of the retry: without it the prior session is lost and every "
        "day change silently reads as unknown"
    )


@pytest.mark.asyncio
async def test_a_persistent_rate_limit_gives_up_rather_than_spinning(monkeypatch):
    from app.integrations.fmp import FMPRateLimitException

    calls = {"n": 0}

    class _AlwaysLimited(_SessionFMP):
        async def get_batch_eod(self, trade_date):
            calls["n"] += 1
            raise FMPRateLimitException("still limited")

    _install(monkeypatch, _AlwaysLimited({}))
    monkeypatch.setattr(ps_module, "_RATE_LIMIT_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(PriceService, "_upsert_closes",
                        staticmethod(lambda p: pytest.fail("must not write")))
    assert await PriceService().refresh_close_snapshot("2026-09-04") == 0
    assert calls["n"] <= 8, f"unbounded retry: {calls['n']} calls"


@pytest.mark.asyncio
async def test_a_non_rate_limit_error_is_not_retried(monkeypatch):
    """Backoff is for a transient burst limit; a real error must fail fast."""
    calls = {"n": 0}

    class _Broken(_SessionFMP):
        async def get_batch_eod(self, trade_date):
            calls["n"] += 1
            raise RuntimeError("upstream down")

    _install(monkeypatch, _Broken({}))
    monkeypatch.setattr(PriceService, "_upsert_closes",
                        staticmethod(lambda p: pytest.fail("must not write")))
    assert await PriceService().refresh_close_snapshot("2026-09-04") == 0
    assert calls["n"] == 1, "a hard error must not be retried"


@pytest.mark.asyncio
async def test_a_missing_prior_session_never_nulls_a_good_previous_close(monkeypatch):
    """🔴 Observed in production 2026-09-07 — a destructive partial write.

    The upsert replaces the whole row, so writing a batch with no prior-session data sets
    `previous_close = NULL` on every symbol. The hourly loop did exactly that: the second
    batch-eod call was rate-limited, and the job nulled the denominator across all 63,394
    rows hours after a successful ingest had populated it, turning the day change into
    "unknown" app-wide.

    Skipping is always safe — the stored (close, previous_close) pair stays internally
    consistent and the loop retries within the hour.
    """
    # ⚠️ THE FIXTURE MUST CARRY THE BELLWETHERS. It used to be a lone AAPL row, which
    # fails `_is_us_session` (needs 2 of AAPL/MSFT/SPY) — so `refresh_close_snapshot`
    # returned 0 from the "no session" branch and NEVER REACHED the `prev_by_symbol`
    # guard this test is named for. It passed for the wrong reason, and the guard
    # protecting a real production incident was untested. Verified:
    #   _is_us_session([AAPL only])   -> False
    #   _is_us_session([AAPL + MSFT]) -> True
    _install(monkeypatch, _SessionFMP({
        "2026-09-04": _session("2026-09-04"),
        # nothing for any earlier session — so the PRIOR fetch comes back empty and the
        # abort guard is what has to stop the write.
    }))
    monkeypatch.setattr(PriceService, "_upsert_closes",
                        staticmethod(lambda p: pytest.fail(
                            "must not write — this would null previous_close on every row"
                        )))
    assert await PriceService().refresh_close_snapshot("2026-09-04") == 0


@pytest.mark.asyncio
async def test_a_truncated_prior_session_never_nulls_previous_close(monkeypatch):
    """The abort must key on COVERAGE, not merely on emptiness.

    The original guard fired only when the prior session was completely empty. A truncated
    response — FMP returning a few hundred of ~65,000 rows — sailed past it, and every
    symbol missing from that short list was written with `previous_close = NULL`. Same
    destructive partial write as the 2026-09-07 incident, just quieter: it degrades a
    fraction of the market instead of all of it, so nothing looks obviously broken.

    Here the latest session has four symbols and the prior one covers a single symbol.
    """
    latest = _session(
        "2026-09-04",
        *[_eod(sym, "2026-09-04", 300.0) for sym in ("NVDA", "TSLA", "AMD", "GOOG")],
    )                                                   # 6 symbols
    # ⚠️ The truncated session must still carry the BELLWETHERS. A fixture without them
    # fails `_is_us_session`, so `_fetch_latest_session` returns nothing and the ORIGINAL
    # `if not prev_by_symbol` guard fires — the test would then pass without ever
    # exercising the coverage floor it is named for. Caught by mutation-testing.
    truncated = _session("2026-09-03")                  # 2 of 6 -> 33% coverage
    _install(monkeypatch, _SessionFMP({
        "2026-09-04": latest,
        "2026-09-03": truncated,
    }))
    monkeypatch.setattr(PriceService, "_upsert_closes",
                        staticmethod(lambda p: pytest.fail(
                            "wrote a truncated prior session — this nulls previous_close "
                            "on every symbol it omits"
                        )))
    assert await PriceService().refresh_close_snapshot("2026-09-04") == 0


@pytest.mark.asyncio
async def test_a_healthy_prior_session_still_writes(monkeypatch):
    """Anti-over-correction: the coverage floor must not block an ordinary ingest.

    Real sessions differ a little at the edges (a halt, a new listing), so the floor is set
    well below parity rather than at it.
    """
    captured: List[Dict[str, Any]] = []
    _install(monkeypatch, _SessionFMP({
        "2026-09-04": _session("2026-09-04", _eod("NVDA", "2026-09-04", 300.0)),
        "2026-09-03": _session("2026-09-03"),          # 2 of 3 — a normal edge difference
    }))
    monkeypatch.setattr(PriceService, "_upsert_closes",
                        staticmethod(lambda p: captured.extend(p) or len(p)))
    assert await PriceService().refresh_close_snapshot("2026-09-04") == 3
    with_prev = [r for r in captured if r["previous_close"] is not None]
    assert {r["symbol"] for r in with_prev} == {"AAPL", "MSFT"}
