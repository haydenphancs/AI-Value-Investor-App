"""The widget describes the SESSION its numbers belong to — not the wall clock.

At 07:30 ET on a Monday the screener still reports Friday's close, so every batch change
is FRIDAY's move. The payload used to stamp `session_date = session_trading_date()`
(Monday), print "Down 4.8% today" under "Pre-market 7:30 AM ET", gate earnings and news
on Monday's rows — so a Monday BMO print became the CAUSE of Friday's move — and serve
the hourly context cache's Friday sector averages as "3 of 11 sectors up" beside a live
SPY line. The industry line alone was age-gated; the headline, the sector band and the
attribution were the "fixed 1 of N consumers" siblings.

`price_service` now stamps `changeSession` on batch quotes (one derivation shared with
the movers universe), `RankedMover` carries it, `_session_of` makes the head's stamp the
payload's session, and `for_tickers` drops sector rows stamped for another session.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.services import widget_movers_service as wm
from app.services.widget_movers_service import (
    RankedMover,
    WidgetMoversService,
    _MarketContext,
    deterministic_reason,
    rank_movers,
)


def _mover(ticker="NVDA", pct=-4.76, stamp=None, z=None):
    return RankedMover(ticker=ticker, change_percent=pct, price=100.0, company_name="NVIDIA",
                       sigma_daily=None, z=z, tier="notable", change_session=stamp)


# ── the stamp rides through ranking ─────────────────────────────────────────

def test_rank_movers_carries_the_change_session():
    ranked = rank_movers([{"ticker": "NVDA", "change_percent": -4.76, "price": 100.0,
                           "change_session": "2026-09-11"}])
    assert ranked[0].change_session == "2026-09-11"


def test_rank_movers_tolerates_a_missing_stamp():
    ranked = rank_movers([{"ticker": "NVDA", "change_percent": -4.76, "price": 100.0}])
    assert ranked[0].change_session is None


# ── _session_of ──────────────────────────────────────────────────────────────

LIVE = date(2026, 9, 14)   # a Monday


def test_a_head_stamped_for_a_prior_session_moves_the_payload_to_that_session():
    d, iso, word = WidgetMoversService._session_of([_mover(stamp="2026-09-11")], LIVE)
    assert (d, iso, word) == (date(2026, 9, 11), "2026-09-11", "on Fri")


def test_a_head_stamped_for_the_live_session_says_today():
    assert WidgetMoversService._session_of([_mover(stamp="2026-09-14")], LIVE) == (LIVE, "2026-09-14", "today")


@pytest.mark.parametrize("stamp", [None, "", "garbage", "2026-09-15"])
def test_no_or_unusable_or_future_stamp_keeps_the_live_session(stamp):
    d, iso, word = WidgetMoversService._session_of([_mover(stamp=stamp)], LIVE)
    assert (d, word) == (LIVE, "today")


def test_no_movers_keeps_the_live_session():
    assert WidgetMoversService._session_of([], LIVE)[2] == "today"


# ── the wording follows the session ─────────────────────────────────────────

def test_deterministic_reason_names_the_prior_session():
    assert deterministic_reason(-4.8, None, "on Fri") == "Down 4.8% on Fri."
    assert deterministic_reason(-4.8, 2.0, "on Fri") == "Down 4.8% on Fri — about 2.0× its normal daily range."
    assert deterministic_reason(-4.8, None) == "Down 4.8% today."


# ── the sector band is gated on the payload's session ───────────────────────

def _ctx(sectors, dates):
    return _MarketContext(sector_changes=sectors, sector_dates=dates, sector_available=True)


def test_sector_rows_from_another_session_are_dropped():
    ctx = _ctx([("Energy", 1.4), ("Technology", -0.3)],
               {"energy": "2026-09-11", "technology": "2026-09-11"})
    view = ctx.for_tickers({}, "2026-09-14", {})
    assert view.sector_changes == []
    assert view.sector_available is False, "Friday's breadth must not render under Monday"


def test_sector_rows_from_the_payloads_session_survive():
    ctx = _ctx([("Energy", 1.4), ("Technology", -0.3)],
               {"energy": "2026-09-11", "technology": "2026-09-11"})
    view = ctx.for_tickers({}, "2026-09-11", {})
    assert view.sector_changes == [("Energy", 1.4), ("Technology", -0.3)]
    assert view.sector_available is True


def test_a_sector_row_without_a_stamp_fails_closed():
    ctx = _ctx([("Energy", 1.4)], {})
    assert ctx.for_tickers({}, "2026-09-14", {}).sector_available is False


def test_a_mixed_band_keeps_only_the_current_rows():
    ctx = _ctx([("Energy", 1.4), ("Utilities", 0.2)],
               {"energy": "2026-09-14", "utilities": "2026-09-11"})
    view = ctx.for_tickers({}, "2026-09-14", {})
    assert view.sector_changes == [("Energy", 1.4)]


# ── the payload, end to end ─────────────────────────────────────────────────

def test_a_premarket_payload_is_dated_and_worded_for_friday(monkeypatch):
    monkeypatch.setattr(wm, "session_trading_date", lambda now=None: LIVE)
    monkeypatch.setattr(wm, "session_label", lambda now=None: "Pre-market 7:30 AM ET")
    svc = WidgetMoversService()
    out = svc._payload(mode="market", ranked=[_mover(stamp="2026-09-11")], cards={},
                       ctx=_MarketContext(), basket=None)
    assert out.session_date == "2026-09-11"
    assert out.session_label == "Fri close"
    assert out.headline_mover is not None
    detail = out.headline_mover.cause.detail
    assert "Friday" in detail or "on Fri" in detail, detail
    assert "today" not in detail.lower(), detail


def test_a_live_payload_is_unchanged(monkeypatch):
    monkeypatch.setattr(wm, "session_trading_date", lambda now=None: LIVE)
    monkeypatch.setattr(wm, "session_label", lambda now=None: "Live 10:15 AM ET")
    out = WidgetMoversService()._payload(mode="market", ranked=[_mover(stamp="2026-09-14")],
                                         cards={}, ctx=_MarketContext(), basket=None)
    assert out.session_date == "2026-09-14"
    assert out.session_label == "Live 10:15 AM ET"
    assert "today" in out.headline_mover.cause.detail.lower()


def test_attribution_sentences_name_the_prior_session():
    from app.services.daily_move_attribution import attribute, session_words
    assert session_words("on Fri") == ("on Fri", "Friday's", "Friday's")
    a = attribute(ticker="NVDA", change_percent=-4.76, today=date(2026, 9, 11),
                  industry_name="Semiconductors", industry_change_percent=-4.5,
                  session_word="on Fri")
    assert a is not None and "on Fri" in a.detail and "today" not in a.detail.lower()
    b = attribute(ticker="NVDA", change_percent=-4.76, today=date(2026, 9, 11),
                  had_news=False, news_checked=True, session_word="on Fri")
    assert b.detail == "No company news on Fri."


# ── a MIXED batch: the stale row is dropped, the tile keeps the live session ──
#
# `price_service._change_session` stamps a row with the stored close's date whenever
# the quote still equals that close (halted / not yet printed) and with the live
# session otherwise, so one batch can carry both Friday and Monday. Ranking is by z
# alone, so a Friday −10% halted name used to head Monday's tile and — because the
# payload carries ONE session — relabel every live runner "on Fri".

from app.services.widget_movers_service import drop_prior_session_movers, newest_session


def test_a_prior_session_row_is_dropped_from_a_live_batch():
    ranked = [_mover("HALT", pct=-10.0, stamp="2026-09-11", z=5.0),
              _mover("NVDA", pct=-4.76, stamp="2026-09-14", z=3.0),
              _mover("AMD", pct=-3.1, stamp="2026-09-14", z=2.0)]
    current, stale = drop_prior_session_movers(ranked)
    assert [m.ticker for m in current] == ["NVDA", "AMD"]
    assert [m.ticker for m in stale] == ["HALT"]
    assert WidgetMoversService._session_of(current, LIVE) == (LIVE, "2026-09-14", "today")


def test_an_all_stale_batch_is_kept_and_labelled_with_its_session():
    """Pre-market Monday: every stamp is Friday's. Nothing is dropped, the tile says Fri."""
    ranked = [_mover("NVDA", stamp="2026-09-11", z=3.0), _mover("AMD", stamp="2026-09-11", z=2.0)]
    current, stale = drop_prior_session_movers(ranked)
    assert len(current) == 2 and stale == []
    assert WidgetMoversService._session_of(current, LIVE)[2] == "on Fri"


def test_unstamped_rows_ride_along_with_the_newest_session():
    ranked = [_mover("OLD", stamp="2026-09-11", z=4.0), _mover("NEW", stamp="2026-09-14", z=3.0),
              _mover("NOSTAMP", stamp=None, z=2.0)]
    current, stale = drop_prior_session_movers(ranked)
    assert [m.ticker for m in current] == ["NEW", "NOSTAMP"]
    assert [m.ticker for m in stale] == ["OLD"]


def test_a_batch_without_any_stamp_is_untouched():
    ranked = [_mover("A", z=2.0), _mover("B", z=1.0)]
    assert drop_prior_session_movers(ranked) == (ranked, [])
    assert newest_session(ranked) is None


def test_session_of_reads_the_newest_stamp_not_the_heads():
    """Even if a stale head slipped past the filter, the session is the batch's newest."""
    ranked = [_mover("HALT", stamp="2026-09-11", z=5.0), _mover("NVDA", stamp="2026-09-14", z=3.0)]
    assert WidgetMoversService._session_of(ranked, LIVE) == (LIVE, "2026-09-14", "today")


# ── the quote→row plumbing (the one line every test above assumed) ───────────
#
# Every other test here hands `rank_movers` a row that ALREADY carries "change_session".
# Deleting the single `"change_session": q.get("changeSession")` line in `_rank_and_read`
# kept all of them green while the shipped tile fell back to the live session — so this
# one drives the real method with a stubbed quote source.

class _PS:
    def __init__(self, rows):
        self.rows = rows

    async def get_quotes_list(self, symbols):
        return list(self.rows)


class _Sigmas:
    async def get_sigmas_bulk(self, symbols):
        return {}


class _News:
    async def get_cards(self, tickers):
        return {}


@pytest.mark.asyncio
async def test_rank_and_read_stamps_each_row_from_the_quote(monkeypatch):
    monkeypatch.setattr(wm, "price_source", lambda owner=None: _PS([
        {"symbol": "NVDA", "changePercentage": -4.76, "price": 100.0, "changeSession": "2026-09-11"},
        {"symbol": "AMD", "changePercentage": -3.1, "price": 50.0, "changeSession": "2026-09-11"},
    ]))
    monkeypatch.setattr(wm, "get_volatility_cache_service", lambda: _Sigmas())
    monkeypatch.setattr(wm, "get_news_insight_service", lambda: _News())
    ranked, _cards, _ok, _idx = await WidgetMoversService()._rank_and_read(["NVDA", "AMD"])
    assert [m.ticker for m in ranked] == ["NVDA", "AMD"]
    assert [m.change_session for m in ranked] == ["2026-09-11", "2026-09-11"]


@pytest.mark.asyncio
async def test_rank_and_read_drops_the_halted_prior_session_row(monkeypatch):
    monkeypatch.setattr(wm, "price_source", lambda owner=None: _PS([
        {"symbol": "HALT", "changePercentage": -10.0, "price": 10.0, "changeSession": "2026-09-11"},
        {"symbol": "NVDA", "changePercentage": -4.76, "price": 100.0, "changeSession": "2026-09-14"},
    ]))
    monkeypatch.setattr(wm, "get_volatility_cache_service", lambda: _Sigmas())
    monkeypatch.setattr(wm, "get_news_insight_service", lambda: _News())
    ranked, *_ = await WidgetMoversService()._rank_and_read(["HALT", "NVDA"])
    assert [m.ticker for m in ranked] == ["NVDA"]
