"""The "Date Added" sort key on the two surfaces Holdings sorts: the Tracking feed and the
Updates tabs (plan: Manage Tickers + Sort By pass, 2026-10-31).

"Date Added" used to read `portfolio_items.position` — the stored ORDER, rewritten by every
drag. The honest date is `watchlist_items.added_at`, written once by the add. Both surfaces
now carry it as `added_at` (UTC "YYYY-MM-DDTHH:MM:SS.mmmZ", `utils.iso_dates`), and the
Updates tabs round the never-drawn `price` sort key like the feed. `change_percent` is DRAWN
on the pill at 1 dp, so the server leaves it unrounded (2 dp first would double-round 0.049 to
"+0.1%"); iOS rounds its own sort key to 2 dp.

What must hold:
  * the value reaches the wire on every row — the enriched one AND the degraded `except`
    row of the feed, the group path AND the fallback path of the tabs;
  * a missing or malformed value is None and costs NOTHING else (the row, its price, the
    response);
  * price: 2 dp; change: unrounded; neither ever a signed zero (iOS reads `-0.0 >= 0` as up).

No network / Supabase: every read is an inline fake.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict

import pytest

import app.api.v1.endpoints.updates as up
import app.services.active_group_service as ags
import app.services.tracking_service as ts
import app.utils.iso_dates as iso_dates
from app.services.active_group_service import ActiveGroup
from app.services.tracking_service import TrackingService
from _price_fakes import PriceFromFMPFake

_USER = "u-added-at"


# ── Tracking feed ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_feed_caches():
    ts._feed_cache.clear()
    ts._feed_inflight.clear()
    yield
    ts._feed_cache.clear()
    ts._feed_inflight.clear()


class _FakeTable:
    def __init__(self, rows): self._rows = rows
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def order(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def range(self, *a, **k): return self
    def execute(self):
        return type("R", (), {"data": self._rows})()


class _FakeSupabase:
    def __init__(self, watchlist): self._w = watchlist
    def table(self, name):
        return _FakeTable(self._w if name == "watchlist_items" else [])


async def _feed(monkeypatch, watchlist, quotes) -> Dict[str, Any]:
    monkeypatch.setattr(ts, "get_supabase", lambda: _FakeSupabase(watchlist))

    async def _quotes(self, tickers): return quotes
    async def _nothing(self, *a, **k): return {}
    async def _no_list(self, *a, **k): return []
    async def _backfill(self, user_id, wl): return None
    for name, fn in [("_get_batch_quotes", _quotes), ("_get_all_sparklines", _nothing),
                     ("_get_earnings_alerts", _no_list), ("_get_whale_trade_alerts", _no_list),
                     ("_get_analyst_rating_alerts", _no_list),
                     ("_get_insider_transaction_alerts", _no_list),
                     ("_backfill_classification", _backfill),
                     ("_get_extended_prints", _nothing)]:
        monkeypatch.setattr(TrackingService, name, fn)
    feed = await TrackingService().get_tracking_feed(_USER)
    return feed


def _row(i, ticker, **extra):
    return {"id": i, "ticker": ticker, "company_name": ticker, "asset_type": "stock",
            "sector": "Technology", **extra}


@pytest.mark.asyncio
async def test_the_feed_row_carries_the_star_date_from_the_watchlist_row(monkeypatch, caplog):
    watchlist = [
        _row(1, "AAPL", added_at="2026-10-30T14:03:21.123456+00:00"),
        _row(2, "MSFT", added_at="2026-10-29T09:00:00-05:00"),
        _row(3, "NVDA"),                                   # key missing entirely
        _row(4, "PLUG", added_at=None),                    # present NULL
        _row(5, "ORCL", added_at="garbage"),               # malformed
        _row(6, "AMD", added_at=1_700_000_000),            # wrong type
    ]
    quotes = {t: {"price": 100.0, "changePercentage": 1.0}
              for t in ("AAPL", "MSFT", "NVDA", "PLUG", "ORCL", "AMD")}
    with caplog.at_level(logging.WARNING, logger=iso_dates.logger.name):
        feed = await _feed(monkeypatch, watchlist, quotes)
    rows = {a.ticker: a for a in feed.assets}

    assert rows["AAPL"].added_at == "2026-10-30T14:03:21.123Z"
    assert rows["MSFT"].added_at == "2026-10-29T14:00:00.000Z", "an offset is converted to UTC"
    for ticker in ("NVDA", "PLUG", "ORCL", "AMD"):
        assert rows[ticker].added_at is None, ticker
    # A bad date costs the sort key only — never the row or its price.
    assert set(rows) == {"AAPL", "MSFT", "NVDA", "PLUG", "ORCL", "AMD"}
    for ticker in ("ORCL", "AMD"):
        assert rows[ticker].price == 100.0 and rows[ticker].price_known is True, ticker
    warned = " ".join(r.getMessage() for r in caplog.records if r.name == iso_dates.logger.name)
    assert "ORCL" in warned and "AMD" in warned
    assert "NVDA" not in warned and "PLUG" not in warned, "an absent date is not a warning"
    # On the wire, as the key iOS decodes.
    dumped = {a["ticker"]: a for a in feed.model_dump()["assets"]}
    assert dumped["AAPL"]["added_at"] == "2026-10-30T14:03:21.123Z"
    assert "added_at" in dumped["NVDA"] and dumped["NVDA"]["added_at"] is None


@pytest.mark.asyncio
async def test_the_degraded_except_row_carries_the_star_date_too(monkeypatch, caplog):
    """A row whose enrichment raised still goes out (price unknown); it must keep its date,
    or a 'Date Added' sort would move it the moment one quote misbehaves."""
    real = ts.display_name_for_row

    def _boom_for_aapl(ticker, name):
        if ticker == "AAPL":
            raise RuntimeError("name table exploded")
        return real(ticker, name)

    monkeypatch.setattr(ts, "display_name_for_row", _boom_for_aapl)
    watchlist = [
        _row(1, "AAPL", added_at="2026-10-30T14:03:21.5+00:00"),
        _row(2, "MSFT", added_at="2026-10-29T10:00:00Z"),
    ]
    quotes = {"AAPL": {"price": 1.0, "changePercentage": 1.0},
              "MSFT": {"price": 2.0, "changePercentage": 2.0}}
    feed = await _feed(monkeypatch, watchlist, quotes)
    rows = {a.ticker: a for a in feed.assets}

    assert rows["AAPL"].price_known is False, "anti-vacuity: AAPL took the except path"
    assert rows["AAPL"].added_at == "2026-10-30T14:03:21.500Z"
    assert rows["MSFT"].added_at == "2026-10-29T10:00:00.000Z"


@pytest.mark.asyncio
async def test_a_malformed_date_on_the_degraded_row_still_cannot_raise(monkeypatch):
    """The except arm has no second safety net: a raise there would fail the whole feed."""
    monkeypatch.setattr(ts, "display_name_for_row",
                        lambda *_a: (_ for _ in ()).throw(RuntimeError("boom")))
    watchlist = [_row(1, "AAPL", added_at={"not": "a date"}), _row(2, "MSFT", added_at=True)]
    feed = await _feed(monkeypatch, watchlist, {"AAPL": {"price": 1.0, "changePercentage": 0.0}})
    rows = {a.ticker: a for a in feed.assets}
    assert set(rows) == {"AAPL", "MSFT"}
    assert rows["AAPL"].added_at is None and rows["MSFT"].added_at is None
    assert rows["AAPL"].price_known is False


# ── Updates tabs ─────────────────────────────────────────────────────────────


def _user(tier="max"):
    return {"id": _USER, "email": "a@b.c", "tier": tier}


def _patch_tabs(monkeypatch, *, group=None, meta=None, watchlist=(), quotes=()):
    async def _fake_group(_uid):
        return group

    async def _fake_meta(_uid, tickers):
        return {t: v for t, v in (meta or {}).items() if t in tickers}

    class _Tbl:
        def select(self, *a): return self
        def eq(self, *a): return self
        def order(self, *a, **kw): return self
        def limit(self, n): return self
        def execute(self): return type("R", (), {"data": list(watchlist)})()

    class _FMP:
        async def get_batch_quotes_bulk(self, symbols):
            return [q for q in quotes if q.get("symbol") in symbols]

    async def _no_etfs(tickers):
        return set()

    monkeypatch.setattr(up, "get_active_group", _fake_group)
    monkeypatch.setattr(up, "fetch_ticker_metadata", _fake_meta)
    monkeypatch.setattr(up, "fetch_etf_tickers", _no_etfs)
    monkeypatch.setattr(up, "get_supabase",
                        lambda: type("S", (), {"table": lambda _s, n: _Tbl()})())
    monkeypatch.setattr(up, "get_fmp_client", lambda: _FMP())
    monkeypatch.setattr(up, "price_source", lambda owner=None: PriceFromFMPFake(_FMP()))


def _pills(resp):
    return {t.scope: t for t in resp.tabs if not t.is_market_tab}


@pytest.mark.asyncio
async def test_group_path_pills_carry_the_star_date_from_the_metadata_read(monkeypatch):
    _patch_tabs(
        monkeypatch,
        group=ActiveGroup(id="g1", name="Tech", tickers=["ORCL", "PLUG", "XYZ", "AMD"]),
        meta={
            "ORCL": {"company_name": "Oracle", "added_at": "2026-10-30T14:03:21.123456+00:00"},
            "PLUG": {"company_name": "Plug"},                       # no date
            "XYZ": {"company_name": "Xyz", "added_at": "not a date"},
            # AMD: no metadata row at all (a failed repair path) — bare symbol, no date
        },
    )
    resp = await up.get_updates_tabs(user=_user())
    pills = _pills(resp)
    assert list(pills) == ["ORCL", "PLUG", "XYZ", "AMD"], "group order is untouched"
    assert pills["ORCL"].added_at == "2026-10-30T14:03:21.123Z"
    assert pills["PLUG"].added_at is None
    assert pills["XYZ"].added_at is None and pills["XYZ"].company_name == "Xyz"
    assert pills["AMD"].added_at is None
    market = resp.tabs[0]
    assert market.is_market_tab and market.added_at is None
    assert market.change_percent is None and market.price is None


@pytest.mark.asyncio
async def test_fallback_path_pills_carry_the_star_date_from_the_watchlist_rows(monkeypatch):
    _patch_tabs(
        monkeypatch,
        group=None,
        watchlist=[
            {"ticker": "AAPL", "added_at": "2026-10-31T12:00:00Z"},
            {"ticker": "MSFT", "added_at": "2026-10-30T20:00:00.25-04:00"},
            {"ticker": "NVDA"},
        ],
    )
    resp = await up.get_updates_tabs(user=_user())
    pills = _pills(resp)
    assert pills["AAPL"].added_at == "2026-10-31T12:00:00.000Z"
    assert pills["MSFT"].added_at == "2026-10-31T00:00:00.250Z"
    assert pills["NVDA"].added_at is None
    assert resp.model_dump()["tabs"][1]["added_at"] == "2026-10-31T12:00:00.000Z"


@pytest.mark.asyncio
async def test_pill_price_is_rounded_like_the_feed_and_change_is_left_for_the_pill(monkeypatch):
    _patch_tabs(
        monkeypatch,
        group=ActiveGroup(id="g1", name="T", tickers=["AAA", "BBB", "CCC", "DDD", "EEE"]),
        quotes=[
            {"symbol": "AAA", "price": 123.4567, "changePercentage": 1.23456,
             "marketCap": 1234567890.123},
            # A tiny negative move rounds to a NEGATIVE zero unless collapsed.
            {"symbol": "BBB", "price": 10.0, "changePercentage": -0.004},
            {"symbol": "CCC", "price": -0.001, "changePercentage": -0.0},
            {"symbol": "DDD", "price": float("nan"), "changePercentage": float("inf")},
            # EEE: no quote at all
        ],
    )
    resp = await up.get_updates_tabs(user=_user())
    pills = _pills(resp)

    assert pills["AAA"].price == 123.46
    # The pill draws change at 1 dp: rounding it to 2 dp here first would double-round.
    assert pills["AAA"].change_percent == 1.23456
    assert pills["AAA"].market_cap == 1234567890.123, "market cap is not a 2-dp field"

    assert pills["BBB"].change_percent == -0.004, "a real (tiny) negative move must survive"
    for value in (pills["CCC"].price, pills["CCC"].change_percent):
        assert value == 0.0 and math.copysign(1.0, value) == 1.0, "signed zero leaked"

    # Unknown stays unknown — never a rounded 0.0.
    assert pills["DDD"].price is None and pills["DDD"].change_percent is None
    assert pills["EEE"].price is None and pills["EEE"].change_percent is None
    # And the response still serializes under allow_nan=False rules (no NaN slipped in).
    import json
    json.dumps(resp.model_dump(), allow_nan=False)


# ── the metadata read selects the column ─────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_ticker_metadata_selects_added_at(monkeypatch):
    """The group path's only source for the date. Without the column in the select, every
    group pill silently ships `added_at: null`."""
    selected = []

    class _Q:
        def __init__(self, rows): self.rows = rows
        def select(self, cols, *a, **k): selected.append(cols); return self
        def eq(self, *a): return self
        def in_(self, *a): return self
        def execute(self): return type("R", (), {"data": self.rows})()

    rows = [{"ticker": "orcl", "company_name": "Oracle",
             "added_at": "2026-10-30T14:03:21.123456+00:00"}]
    monkeypatch.setattr(ags, "get_supabase",
                        lambda: type("S", (), {"table": lambda _s, n: _Q(rows)})())
    meta = await ags.fetch_ticker_metadata(_USER, ["ORCL"])
    assert selected and "added_at" in [c.strip() for c in selected[0].split(",")]
    assert meta["ORCL"]["added_at"] == "2026-10-30T14:03:21.123456+00:00"
