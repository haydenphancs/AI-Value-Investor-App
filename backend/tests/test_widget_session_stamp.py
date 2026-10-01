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
    # "today" is worded only when the live session IS the ET calendar day: at 07:00 ET on
    # a Monday the screener still reports Friday and `session_trading_date` says Friday, so
    # a session-only comparison called Friday's move "today". LIVE is a date literal, so
    # the calendar must be pinned with it or this test rots the day after it was written.
    monkeypatch.setattr(wm, "_et_calendar_day", lambda: LIVE)
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


# ── the PRODUCTION JOIN, which every test above stops one step short of ─────────────


def test_the_session_gate_cannot_fail_open_to_the_wall_clock():
    """`_market_context(session_date=...)` must be REQUIRED.

    Every session assertion in this file is a leaf: they hand `for_tickers` the session
    themselves. The one line that joins `_session_of` to the gate in production was
    untested, and the parameter was declared `Optional[str] = None`, resolved as
    `session_date or session_trading_date().isoformat()` — i.e. it failed OPEN to the wall
    clock. One of the three call sites relied on that default, so a pre-market Monday
    attribution compared Friday's change against Monday's sector rows. A required parameter
    turns that into a signature error.
    """
    import inspect
    import re

    from app.services.widget_movers_service import WidgetMoversService

    sig = inspect.signature(WidgetMoversService._market_context)
    param = sig.parameters["session_date"]
    assert param.default is inspect.Parameter.empty, (
        "session_date has a default again — the sector/industry gate silently falls back "
        "to the wall clock whenever a caller forgets it"
    )

    src = inspect.getsource(WidgetMoversService._market_context)
    code = "\n".join(re.sub(r"#.*$", "", line) for line in src.splitlines())
    assert "session_date or session_trading_date" not in code, (
        "the fail-open fallback is back inside the function"
    )


def test_every_market_context_call_site_passes_a_session():
    """A required parameter is only worth as much as the values handed to it: each caller
    must pass a session it DERIVED, not one it invented."""
    import ast
    import inspect
    import re

    from app.services import widget_movers_service as wms

    src = inspect.getsource(wms)
    tree = ast.parse(src)
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "_market_context"
    ]
    assert len(calls) >= 3, f"only {len(calls)} call sites found — the scan has rotted"
    for call in calls:
        positional = len(call.args)
        named = {k.arg for k in call.keywords}
        assert positional >= 3 or "session_date" in named, (
            f"widget_movers_service.py:{call.lineno} calls _market_context without a "
            "session — the sector and industry gates then compare against the wrong day"
        )


@pytest.mark.asyncio
async def test_the_attribution_path_gates_on_the_rows_session_not_the_clock(monkeypatch):
    """End-to-end over the production join, for the ONE path that used the wall clock.

    `attribute_ticker_move` is what Ask Cay AI calls, and it must give the same answer the
    Home Screen widget gives for the same market day.
    """
    from datetime import date

    from app.services import widget_movers_service as wms

    svc = wms.WidgetMoversService.__new__(wms.WidgetMoversService)
    friday = date(2026, 9, 11)
    monday = date(2026, 9, 14)
    seen = {}

    ranked = [wms.RankedMover(
        ticker="NVDA", change_percent=-4.8, price=130.0, company_name="NVIDIA",
        sigma_daily=0.015, z=3.1, tier="high", open_price=136.0, previous_close=136.5,
        change_session=friday.isoformat(),
    )]

    async def _rank_and_read(_syms):
        return ranked, {"NVDA": None}, True, {}

    async def _ctx(_tickers, _index_rows, session_date):
        seen["ctx_session"] = session_date
        return wms._MarketContext()

    def _news(card, today_iso):
        seen["news_session"] = today_iso
        return None, False, False

    monkeypatch.setattr(svc, "_rank_and_read", _rank_and_read, raising=False)
    monkeypatch.setattr(svc, "_market_context", _ctx, raising=False)
    monkeypatch.setattr(wms, "_classified_today_news", _news)
    monkeypatch.setattr(wms, "session_trading_date", lambda: monday)

    await svc.attribute_ticker_move("NVDA")

    assert seen["ctx_session"] == friday.isoformat(), (
        "the market context was gated on MONDAY while the change being explained is "
        f"FRIDAY's — got {seen['ctx_session']}"
    )
    assert seen["news_session"] == friday.isoformat(), (
        "the news detector was asked about the wrong day, which is how the tile prints "
        "the confident negative 'no company news today'"
    )



# ══ 2026-09-30: the session fixes behind the "Tue close" screenshot ══════════════════


# ── the earnings window is previous_trading_day(session)..session ─────────────────────


class _FMP:
    """Per-day earnings calendar. `days` maps an ISO day to its rows, or to an Exception
    the call raises; a day not in `days` answers with the default reported ORCL row."""

    def __init__(self, days=None):
        self.windows = []
        self.days = days or {}

    async def get_earnings_calendar(self, from_date=None, to_date=None):
        self.windows.append((from_date, to_date))
        answer = self.days.get(from_date)
        if isinstance(answer, Exception):
            raise answer
        if answer is not None:
            return answer
        return [{"symbol": "ORCL", "date": from_date, "time": "amc", "epsActual": 1.1,
                 "epsEstimated": 1.0}]


class _Movers:
    async def get_industry_performance(self):
        return [{"industry": "Software", "changesPercentage": 1.2, "date": "2026-09-29"}]

    async def get_sector_performance(self):
        return [{"sector": "Technology", "changesPercentage": 0.9, "date": "2026-09-29"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("session, window, why", [
    (date(2026, 9, 29), ("2026-09-28", "2026-09-29"), "Tue: Monday's after-close print"),
    (date(2026, 9, 28), ("2026-09-25", "2026-09-28"), "Mon: FRIDAY's, not Sunday's"),
    (date(2026, 9, 8), ("2026-09-04", "2026-09-08"), "Tue after Labor Day: Friday's"),
])
async def test_the_earnings_window_ends_on_the_session_and_starts_a_trading_day_back(
    monkeypatch, session, window, why,
):
    """At 06:50 Wednesday the payload describes TUESDAY, but the window was the wall clock's
    Tue..Wed — so Monday's after-close print, the cause of Tuesday's move, was never even
    fetched. And a calendar-day window held nothing on a Monday (Sun..Mon).

    And it is TWO SINGLE-DAY calls, never one multi-day window: FMP cuts an answer at 4,000
    rows keeping the NEWEST dates, so a peak-season Fri..Mon request dropped Friday — the
    after-close prints the window exists to read. No weekend day is ever asked for."""
    prev_day, session_day = window
    # The prior day REPORTED (the cause); the session day only has it scheduled. The reported
    # row must win the merge even though it is the older one.
    fmp = _FMP(days={session_day: [{"symbol": "ORCL", "date": session_day, "time": "bmo",
                                    "epsActual": None, "epsEstimated": 1.0}]})
    monkeypatch.setattr(wm, "get_fmp_client", lambda: fmp)
    monkeypatch.setattr(wm, "get_market_movers_service", lambda: _Movers())

    ctx = await WidgetMoversService()._fetch_market_context(session)

    assert sorted(fmp.windows) == [(prev_day, prev_day), (session_day, session_day)], why
    assert ctx.earnings_available is True
    assert ctx.earnings_for("orcl")["date"] == prev_day, "the reported row lost the merge"


@pytest.mark.asyncio
async def test_a_day_at_fmps_row_cap_is_logged_as_truncated_and_still_used(monkeypatch, caplog):
    """A single-day answer of 4,000 rows is at FMP's silent cap — probably cut. That must be
    an ERROR someone can grep for, not a silent hole; the rows that did arrive still count."""
    import logging

    fri, mon = "2026-09-25", "2026-09-28"
    full = [{"symbol": f"S{i}", "date": fri, "time": "amc", "epsActual": 1.0,
             "epsEstimated": 0.9} for i in range(3999)]
    full.append({"symbol": "ORCL", "date": fri, "time": "amc", "epsActual": 1.47,
                 "epsEstimated": 1.30})
    assert len(full) == wm._EARNINGS_TRUNCATION_ROWS
    fmp = _FMP(days={fri: full, mon: []})
    monkeypatch.setattr(wm, "get_fmp_client", lambda: fmp)
    monkeypatch.setattr(wm, "get_market_movers_service", lambda: _Movers())

    with caplog.at_level(logging.ERROR, logger=wm.logger.name):
        ctx = await WidgetMoversService()._fetch_market_context(date(2026, 9, 28))

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("2026-09-25" in r.getMessage() and "TRUNCATED" in r.getMessage()
               for r in errors), [r.getMessage() for r in errors]
    assert not any(mon in r.getMessage() for r in errors), "an empty day is not truncated"
    assert ctx.earnings_available is True
    assert ctx.earnings_for("ORCL")["epsActual"] == 1.47
    assert len(ctx.earnings) == 4000


@pytest.mark.asyncio
async def test_one_failed_day_keeps_the_other_and_the_leg_stays_available(monkeypatch, caplog):
    import logging

    fri, mon = "2026-09-25", "2026-09-28"
    fmp = _FMP(days={fri: RuntimeError("FMP 503"),
                     mon: [{"symbol": "NVDA", "date": mon, "time": "bmo", "epsActual": 0.7,
                            "epsEstimated": 0.6}]})
    monkeypatch.setattr(wm, "get_fmp_client", lambda: fmp)
    monkeypatch.setattr(wm, "get_market_movers_service", lambda: _Movers())

    with caplog.at_level(logging.WARNING, logger=wm.logger.name):
        ctx = await WidgetMoversService()._fetch_market_context(date(2026, 9, 28))

    assert ctx.earnings_available is True
    assert ctx.earnings_for("NVDA")["date"] == mon
    assert ctx.earnings_for("ORCL") is None
    assert any(fri in r.getMessage() and "RuntimeError" in r.getMessage()
               for r in caplog.records), "the lost day must be named in the log"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [RuntimeError("FMP 503"), {"error": "not a list"}])
async def test_every_day_failing_is_unavailable_not_nobody_reported(monkeypatch, bad):
    """An empty dict from two outages is not "no company reported" — the flag must say so."""
    fmp = _FMP(days={"2026-09-25": bad, "2026-09-28": bad})
    monkeypatch.setattr(wm, "get_fmp_client", lambda: fmp)
    monkeypatch.setattr(wm, "get_market_movers_service", lambda: _Movers())

    ctx = await WidgetMoversService()._fetch_market_context(date(2026, 9, 28))

    assert ctx.earnings_available is False
    assert ctx.earnings == {}
    # The other legs are independent.
    assert ctx.industry_available is True and ctx.sector_available is True


@pytest.mark.asyncio
async def test_rows_are_kept_only_under_the_day_they_were_asked_for(monkeypatch):
    """A lenient upstream that answers a one-day request with OTHER days (or junk rows)
    must not leak them in: a Thursday print is not a cause of Monday's move."""
    fri, mon = "2026-09-25", "2026-09-28"
    fmp = _FMP(days={
        fri: [{"symbol": "OLD", "date": "2026-09-24", "epsActual": 1.0},
              {"symbol": "", "date": fri, "epsActual": 1.0},
              "not-a-row", None,
              {"symbol": "orcl", "date": f"{fri} 00:00:00", "epsActual": 1.47}],
        mon: None,      # a None body is an empty day, as before
    })
    monkeypatch.setattr(wm, "get_fmp_client", lambda: fmp)
    monkeypatch.setattr(wm, "get_market_movers_service", lambda: _Movers())

    ctx = await WidgetMoversService()._fetch_market_context(date(2026, 9, 28))

    assert ctx.earnings_available is True
    assert set(ctx.earnings) == {"ORCL"}
    assert ctx.earnings_for("OLD") is None


def test_the_widgets_row_cap_is_the_earnings_window_services():
    from app.services import earnings_window_service as ews

    assert wm._EARNINGS_TRUNCATION_ROWS == ews._TRUNCATION_ROWS


# ── the context cache is keyed by SESSION ─────────────────────────────────────────────


def _ctx_service(monkeypatch):
    svc = WidgetMoversService()
    fetched = []

    async def _fetch(session_day):
        fetched.append(session_day)
        return _MarketContext(earnings_available=True,
                              earnings={"ORCL": {"date": session_day.isoformat()}})

    async def _industries(tickers):
        return {}

    async def _one(ticker):
        return None

    monkeypatch.setattr(svc, "_fetch_market_context", _fetch)
    monkeypatch.setattr(svc, "_industries", _industries)
    monkeypatch.setattr(svc, "_industry_for_one", _one)
    return svc, fetched


@pytest.mark.asyncio
async def test_a_context_fetched_for_one_session_is_never_served_for_another(monkeypatch):
    """One shared slot filled at 04:01 Wednesday (window Tue..Wed) served the pre-market
    builds describing TUESDAY for an hour."""
    svc, fetched = _ctx_service(monkeypatch)

    tue = await svc._market_context(["ORCL"], {}, "2026-09-29")
    tue_again = await svc._market_context(["ORCL"], {}, "2026-09-29")
    wed = await svc._market_context(["ORCL"], {}, "2026-09-30")

    assert fetched == [date(2026, 9, 29), date(2026, 9, 30)]
    assert tue.earnings_for("ORCL")["date"] == "2026-09-29"
    assert tue_again.earnings_for("ORCL")["date"] == "2026-09-29"
    assert wed.earnings_for("ORCL")["date"] == "2026-09-30"
    assert set(svc._ctx_cache) == {"2026-09-29", "2026-09-30"}


@pytest.mark.asyncio
async def test_the_context_cache_is_bounded_oldest_session_first(monkeypatch):
    svc, _ = _ctx_service(monkeypatch)
    for d in ("2026-09-24", "2026-09-25", "2026-09-28", "2026-09-29"):
        await svc._market_context([], {}, d)
    assert sorted(svc._ctx_cache) == ["2026-09-25", "2026-09-28", "2026-09-29"]
    assert len(svc._ctx_cache) == wm._CTX_CACHE_MAX_SESSIONS


@pytest.mark.asyncio
async def test_an_unreadable_session_fetches_the_live_window_but_gates_closed(monkeypatch):
    svc, fetched = _ctx_service(monkeypatch)
    monkeypatch.setattr(wm, "session_trading_date", lambda now=None: date(2026, 9, 29))
    view = await svc._market_context(["NVDA"], {}, "not-a-date")
    assert fetched == [date(2026, 9, 29)]
    # The industry/sector gates still compare against the ORIGINAL string, so they fail
    # closed rather than adopting the wall clock.
    assert view.session_date == "not-a-date"


# ── a news card generated AFTER the session is unchecked ──────────────────────────────


def test_a_card_newer_than_the_session_is_unchecked_not_a_negative():
    """Pre-market Wednesday the payload describes Tuesday, but the 04:00 news pass has
    already REPLACED Tuesday's card (one row per scope) with this morning's. That card says
    nothing about Tuesday, and Tuesday's is gone — "No company news on Tue." would be a
    confident negative about a day we can no longer see."""
    from app.services.widget_movers_service import _classified_today_news

    wed_card = {"headline": "Oracle wins cloud deal", "generated_at": "2026-09-30T08:05:00Z"}
    assert _classified_today_news(wed_card, "2026-09-29") == ([], False, False)
    # CONTROL: an OLDER card is still a checked negative.
    mon_card = {"headline": "Oracle wins cloud deal", "generated_at": "2026-09-28T15:00:00Z"}
    assert _classified_today_news(mon_card, "2026-09-29") == ([], False, True)


def test_the_newer_card_never_reaches_the_tile_as_no_company_news(monkeypatch):
    monkeypatch.setattr(wm, "session_trading_date", lambda now=None: date(2026, 9, 30))
    monkeypatch.setattr(wm, "_et_calendar_day", lambda: date(2026, 9, 30))
    monkeypatch.setattr(wm, "session_label", lambda now=None: "Pre-market 6:50 AM ET")
    card = {"headline": "Oracle wins cloud deal", "generated_at": "2026-09-30T10:05:00Z"}
    out = WidgetMoversService()._payload(
        mode="portfolio", ranked=[_mover("ORCL", pct=-3.4, stamp="2026-09-29")],
        cards={"ORCL": card}, ctx=_MarketContext(news_available=True), basket=None,
        holdings_count=1,
    )
    detail = out.headline_mover.cause.detail
    assert "No company news" not in detail, detail
    assert "No clear catalyst" not in detail, detail
    assert out.session_label == "Tue close"


# ── Monday's after-close print explains Tuesday, read at Wed 06:50 ────────────────────


def test_monday_amc_explains_tuesday_on_a_wednesday_premarket_tile(monkeypatch):
    wed = date(2026, 9, 30)
    monkeypatch.setattr(wm, "session_trading_date", lambda now=None: wed)
    monkeypatch.setattr(wm, "_et_calendar_day", lambda: wed)
    monkeypatch.setattr(wm, "session_label", lambda now=None: "Pre-market 6:50 AM ET")
    ctx = _MarketContext(
        earnings_available=True, news_available=True,
        earnings={"ORCL": {"symbol": "ORCL", "date": "2026-09-28", "time": "amc",
                           "epsActual": 1.47, "epsEstimated": 1.30}},
    )
    out = WidgetMoversService()._payload(
        mode="portfolio", ranked=[_mover("ORCL", pct=9.1, stamp="2026-09-29")],
        cards={}, ctx=ctx, basket=None, holdings_count=1,
    )
    cause = out.headline_mover.cause
    assert out.session_date == "2026-09-29" and out.session_label == "Tue close"
    assert cause.kind == "earnings", cause.detail
    assert "after the prior close" in cause.detail
    assert "yesterday" not in cause.detail and "today" not in cause.detail.lower()


# ── pre-market plurality: one fresh sub-cent row must not evict the real session ──────

PHASES = ("premarket", "afterhours", "closed")


def _batch(n_tue=6, n_wed=1):
    rows = [_mover(f"T{i}", pct=-(i + 1.0), stamp="2026-09-29", z=3.0 - i * 0.1) for i in range(n_tue)]
    rows += [_mover(f"W{i}", pct=0.0004, stamp="2026-09-30", z=None) for i in range(n_wed)]
    return rows


@pytest.mark.parametrize("phase", PHASES)
def test_outside_regular_hours_the_plurality_session_wins(phase):
    """06:50 Wednesday: six holdings still carry Tuesday's close, one drifted a sub-cent and
    is stamped WEDNESDAY. Newest-wins evicted all six and headlined the ~0% row as
    "Pre-market"."""
    current, stale = drop_prior_session_movers(_batch(), phase=phase)
    assert [m.ticker for m in current] == [f"T{i}" for i in range(6)]
    assert [m.ticker for m in stale] == ["W0"], "the NEWER row must be dropped too"
    assert WidgetMoversService._session_of(current, date(2026, 9, 30))[2] == "on Tue"


@pytest.mark.parametrize("phase", ["regular", None])
def test_in_regular_hours_or_without_a_phase_the_newest_session_wins(phase):
    """With the tape open one live print IS the news; every older stamp is a halted or
    unprinted name. `phase=None` is the old behaviour, kept for the one-argument caller."""
    current, stale = drop_prior_session_movers(_batch(), phase=phase)
    assert [m.ticker for m in current] == ["W0"]
    assert len(stale) == 6


@pytest.mark.parametrize("phase", PHASES)
def test_a_newest_session_with_at_least_half_the_rows_still_wins(phase):
    current, _ = drop_prior_session_movers(_batch(n_tue=3, n_wed=3), phase=phase)
    assert {m.change_session for m in current} == {"2026-09-30"}, "ties go to the newer"


def test_a_tie_between_older_sessions_goes_to_the_newer_of_them():
    rows = ([_mover("M", stamp="2026-09-28")] * 1
            + [_mover(f"T{i}", stamp="2026-09-29") for i in range(2)]
            + [_mover(f"X{i}", stamp="2026-09-25") for i in range(2)]
            + [_mover("W", stamp="2026-09-30")])
    current, _ = drop_prior_session_movers(rows, phase="premarket")
    assert {m.change_session for m in current} == {"2026-09-29"}


def test_crypto_and_unstamped_rows_never_vote_and_are_never_dropped():
    rows = _batch(n_tue=2, n_wed=1) + [
        _mover("BTCUSD", pct=3.0, stamp="2026-09-30"),
        _mover("ETHUSD", pct=2.0, stamp="2026-09-30"),
        _mover("NOSTAMP", pct=1.0, stamp=None),
    ]
    current, stale = drop_prior_session_movers(rows, phase="premarket")
    names = [m.ticker for m in current]
    # Without the crypto exemption Wed would hold 3 of 5 votes and win.
    assert {"T0", "T1", "BTCUSD", "ETHUSD", "NOSTAMP"} == set(names)
    assert [m.ticker for m in stale] == ["W0"]


@pytest.mark.asyncio
async def test_rank_and_read_applies_the_phase_it_is_given(monkeypatch):
    rows = [{"symbol": f"T{i}", "changePercentage": -(i + 1.0), "price": 10.0,
             "changeSession": "2026-09-29"} for i in range(6)]
    rows.append({"symbol": "W0", "changePercentage": 0.0004, "price": 10.0,
                 "changeSession": "2026-09-30"})
    monkeypatch.setattr(wm, "price_source", lambda owner=None: _PS(rows))
    monkeypatch.setattr(wm, "get_volatility_cache_service", lambda: _Sigmas())
    monkeypatch.setattr(wm, "get_news_insight_service", lambda: _News())
    syms = [r["symbol"] for r in rows]

    pre, *_ = await WidgetMoversService()._rank_and_read(syms, phase="premarket")
    assert "W0" not in [m.ticker for m in pre] and len(pre) == 6
    live, *_ = await WidgetMoversService()._rank_and_read(syms, phase="regular")
    assert [m.ticker for m in live] == ["W0"]
    default, *_ = await WidgetMoversService()._rank_and_read(syms)
    assert [m.ticker for m in default] == ["W0"], "the one-argument form keeps newest-wins"


# ── the index band is session-gated (band AND market leg) ─────────────────────────────


def test_an_index_row_from_another_session_loses_its_change_and_the_market_leg():
    rows = {
        "SPY": {"symbol": "SPY", "price": 651.2, "changePercentage": -0.18, "change": -1.2,
                "changeSession": "2026-09-29"},
        "ONEQ": {"symbol": "ONEQ", "price": 80.4, "changePercentage": 0.0004,
                 "changeSession": "2026-09-30"},
        "DIA": {"symbol": "DIA", "price": 462.0, "changePercentage": -0.31},   # unstamped
    }
    snapshot = {k: dict(v) for k, v in rows.items()}

    view = _MarketContext().for_tickers({}, "2026-09-30", rows)

    assert view.market_change is None and view.market_available is False, (
        "Tuesday's SPY move was the 'moved with the market' denominator on Wednesday"
    )
    assert view.index_rows["SPY"]["changePercentage"] is None
    assert view.index_rows["SPY"]["change"] is None
    assert view.index_rows["SPY"]["price"] == 651.2, "the price is real — keep it"
    assert view.index_rows["ONEQ"]["changePercentage"] == 0.0004
    assert view.index_rows["DIA"]["changePercentage"] == -0.31, "unstamped fails open"
    assert rows == snapshot, "the caller's batch-quote dicts were mutated"


def test_a_same_session_spy_is_the_market_leg():
    rows = {"SPY": {"symbol": "SPY", "price": 651.2, "changePercentage": -1.6,
                    "changeSession": "2026-09-29"}}
    view = _MarketContext().for_tickers({}, "2026-09-29", rows)
    assert view.market_change == -1.6 and view.market_available is True


def test_a_gated_market_leg_cannot_explain_a_move(monkeypatch):
    """With SPY stamped for another session, a holding that 'matches' its −1.6% must not
    be told "The market fell 1.6%; NVDA moved with it." """
    monkeypatch.setattr(wm, "session_trading_date", lambda now=None: date(2026, 9, 30))
    monkeypatch.setattr(wm, "_et_calendar_day", lambda: date(2026, 9, 30))
    monkeypatch.setattr(wm, "session_label", lambda now=None: "Live 10:15 AM ET")
    rows = {"SPY": {"symbol": "SPY", "price": 651.2, "changePercentage": -1.6,
                    "changeSession": "2026-09-29"}}
    ctx = _MarketContext(news_available=True).for_tickers({}, "2026-09-30", rows)
    out = WidgetMoversService()._payload(
        mode="portfolio", ranked=[_mover("NVDA", pct=-1.5, stamp="2026-09-30")],
        cards={}, ctx=ctx, basket=None, holdings_count=1,
    )
    assert out.headline_mover.cause.kind != "market"
    assert out.headline_mover.context.market_change_percent is None
    assert out.market_context.indices[0].change_percent is None


# ── detail_aged: the headline's cause, worded for a later day ─────────────────────────

_RELATIVE_WORDS = ("today", "yesterday", "this morning", "tonight")


def _aged_payload(monkeypatch, *, live, stamp, ticker="NVDA", pct=-4.8, ctx=None, cards=None):
    monkeypatch.setattr(wm, "session_trading_date", lambda now=None: live)
    monkeypatch.setattr(wm, "_et_calendar_day", lambda: live)
    monkeypatch.setattr(wm, "session_label", lambda now=None: "Live 2:14 PM ET")
    return WidgetMoversService()._payload(
        mode="portfolio",
        ranked=[_mover(ticker, pct=pct, stamp=stamp, z=2.0),
                _mover("AMD", pct=-1.0, stamp=stamp, z=0.5)],
        cards=cards or {}, ctx=ctx or _MarketContext(news_available=True), basket=None,
        holdings_count=2,
    )


def _ctx_for(kind, iso):
    rows = {"SPY": {"symbol": "SPY", "price": 651.0, "changePercentage": -4.5, "changeSession": iso}}
    if kind == "market":
        return _MarketContext(news_available=True).for_tickers({}, iso, rows)
    if kind == "sector":
        c = _MarketContext(news_available=True, industry_available=True,
                           industry_changes={"semiconductors": -4.2},
                           industry_dates={"semiconductors": iso})
        return c.for_tickers({"NVDA": "Semiconductors"}, iso, {})
    if kind == "earnings":
        return _MarketContext(news_available=True, earnings_available=True, earnings={
            "NVDA": {"date": iso, "time": "bmo", "epsActual": 0.6, "epsEstimated": 0.8}})
    if kind == "unchecked":
        return _MarketContext(news_available=False)
    return _MarketContext(news_available=True)


@pytest.mark.parametrize("kind", ["none", "unchecked", "market", "sector", "earnings"])
def test_detail_aged_names_the_weekday_and_never_a_relative_day(monkeypatch, kind):
    """`detail` is true at `as_of`; the snapshot is still on the Home Screen tomorrow, where
    the footer says "Tue close" and a verbatim "today's news" contradicts it."""
    live = date(2026, 9, 29)
    iso = live.isoformat()
    # "none": a covered ticker with an old card → the checked negative "No company news".
    cards = {"NVDA": {"headline": "x", "generated_at": "2026-09-20T15:00:00Z"}} if kind == "none" else {}
    out = _aged_payload(monkeypatch, live=live, stamp=iso, ctx=_ctx_for(kind, iso), cards=cards)
    head = out.headline_mover
    if kind != "unchecked":
        assert head.cause.kind == ("none" if kind == "none" else kind), head.cause.detail
    aged = head.cause.detail_aged
    assert aged, "the headline must always carry an aged wording"
    assert "Tue" in aged, aged
    low = aged.lower()
    assert not any(w in low for w in _RELATIVE_WORDS), aged
    # Only the headline is aged (it is the one line the tile spells out).
    assert all(m.cause.detail_aged is None for m in out.runners_up + out.top_losers)


def test_detail_aged_for_a_prior_session_payload_is_still_that_weekday(monkeypatch):
    out = _aged_payload(monkeypatch, live=date(2026, 9, 30), stamp="2026-09-29")
    aged = out.headline_mover.cause.detail_aged
    assert "Tue" in aged and "Wed" not in aged, aged
    assert not any(w in aged.lower() for w in _RELATIVE_WORDS), aged


def test_a_company_news_headline_is_the_exempt_verbatim_case(monkeypatch):
    """The `company_news` detail IS the news headline, verbatim — its wording is the
    publisher's, so the "no relative day" rule does not apply to it. It must still be set."""
    live = date(2026, 9, 29)
    card = {"headline": "Nvidia lowers guidance for the year",
            "generated_at": "2026-09-29T15:00:00Z"}
    out = _aged_payload(monkeypatch, live=live, stamp=live.isoformat(), cards={"NVDA": card})
    head = out.headline_mover
    assert head.cause.kind == "company_news", head.cause.detail
    assert head.cause.detail_aged == head.cause.detail


def _full_day(abbrev):
    from app.services.daily_move_attribution import _DAY_NAMES
    return _DAY_NAMES[abbrev]


def _crypto_aged_payload(monkeypatch, *, session, calendar, ctx_kind="none", cards=None):
    """BTCUSD +6.1% heads a portfolio beside an equity stamped for `session`; the build runs
    on the ET calendar day `calendar` (Saturday: session Fri; Monday pre-market: Fri too)."""
    monkeypatch.setattr(wm, "session_trading_date", lambda now=None: session)
    monkeypatch.setattr(wm, "_et_calendar_day", lambda: calendar)
    monkeypatch.setattr(wm, "session_label", lambda now=None: "Closed")
    iso = session.isoformat()
    return WidgetMoversService()._payload(
        mode="portfolio",
        ranked=rank_movers([
            {"ticker": "BTCUSD", "change_percent": 6.1, "price": 112000.0},
            {"ticker": "AMD", "change_percent": -1.0, "price": 150.0, "change_session": iso},
        ], basis="abs_change"),
        cards=cards or {}, ctx=_ctx_for(ctx_kind, iso), basket=None, holdings_count=2,
    )


@pytest.mark.parametrize("ctx_kind", ["none", "unchecked", "market"])
@pytest.mark.parametrize("session, calendar, day, why", [
    (date(2026, 9, 29), date(2026, 9, 29), "Tue", "a live Tuesday"),
    (date(2026, 9, 25), date(2026, 9, 26), "Sat", "Saturday: the equity session is Friday"),
    (date(2026, 9, 25), date(2026, 9, 28), "Mon", "Monday pre-market: still Friday's tape"),
])
def test_a_round_the_clock_headline_is_aged_by_its_build_day(
    monkeypatch, ctx_kind, session, calendar, day, why,
):
    """Tuesday's snapshot is still on the Home Screen Wednesday (only the app refreshes the
    Holdings tile). A crypto headline with no aged wording fell back to the "today"-worded
    `detail` under a "Tue close" footer. It is named for the ET CALENDAR day of the build —
    never the equity session: "on Fri" would mislabel a live Saturday or Monday move."""
    out = _crypto_aged_payload(monkeypatch, session=session, calendar=calendar,
                               ctx_kind=ctx_kind)
    head = out.headline_mover
    assert head.ticker == "BTCUSD" and head.rolling_24h is True
    aged = head.cause.detail_aged
    assert aged, f"{why}: a 24/7 headline must carry an aged wording too"
    assert not any(w in aged.lower() for w in _RELATIVE_WORDS), (why, aged)
    names_day = f"on {day}" in aged or _full_day(day) in aged
    assert names_day, (why, aged)
    if calendar != session:
        assert "Fri" not in aged, f"{why}: named the equity session, not the build day: {aged}"
    # The live `detail` keeps its "today" — the rolling move IS today's at as_of.
    assert "Fri" not in head.cause.detail, head.cause.detail
    # Only the headline is aged; the equity loser keeps the equity session.
    assert out.top_losers[0].rolling_24h is False
    assert out.top_losers[0].cause.detail_aged is None



def test_a_live_crypto_move_never_moved_with_a_closed_market(monkeypatch):
    """Monday pre-market the equity leg describes FRIDAY (the tile says "Fri close"), while
    Bitcoin's change is its live rolling 24 h. "The market rose 4.0% today; BTCUSD moved
    with it." compares two different days and calls Friday "today"."""
    monday = date(2026, 9, 28)
    monkeypatch.setattr(wm, "session_trading_date", lambda now=None: monday)
    monkeypatch.setattr(wm, "_et_calendar_day", lambda: monday)
    monkeypatch.setattr(wm, "session_label", lambda now=None: "Pre-market 7:30 AM ET")
    fri = "2026-09-25"
    ctx = _MarketContext(news_available=True).for_tickers(
        {}, fri, {"SPY": {"symbol": "SPY", "price": 660.0, "changePercentage": 4.0,
                          "changeSession": fri}},
    )
    ranked = rank_movers([
        {"ticker": "BTCUSD", "change_percent": 4.5, "price": 112000.0},
        {"ticker": "NVDA", "change_percent": 3.9, "price": 130.0, "change_session": fri},
    ], basis="abs_change")
    out = WidgetMoversService()._payload(
        mode="portfolio", ranked=ranked, cards={}, ctx=ctx, basket=None, holdings_count=2,
    )
    assert out.session_label == "Fri close"
    btc = out.headline_mover
    assert btc.ticker == "BTCUSD" and btc.rolling_24h is True
    assert btc.cause.kind != "market", btc.cause.detail
    # CONTROL: the equity row of the SAME session keeps its market leg, worded for Friday.
    nvda = out.top_gainers[0]
    assert nvda.ticker == "NVDA" and nvda.cause.kind == "market", nvda.cause.detail
    assert "on Fri" in nvda.cause.detail


# ── 24/7 rows read news on THEIR OWN calendar day, never the equity session ──────────
#
# On a Saturday the payload's session is Friday, and the crypto off-hours news pass has
# already written Saturday's BTCUSD card. Gated on the equity session that card is NEWER
# than the session → unchecked, so a live Saturday catalyst was never classified and the
# tile said "Today's news could not be checked." all weekend (and every weekday pre-market).

_SAT = date(2026, 9, 26)
_FRI_ISO = "2026-09-25"
_SEC = "SEC charges major crypto exchange; bitcoin slides"


def _card(headline, generated_at):
    return {"headline": headline, "generated_at": generated_at}


@pytest.mark.parametrize("card, expected, why", [
    (_card(_SEC, "2026-09-26T15:00:00Z"),
     ([("Legal/Regulatory", _SEC)], True, True), "same day: classified"),
    # 23:30 ET Saturday is 03:30 UTC SUNDAY — the ET day decides, not the UTC one.
    (_card(_SEC, "2026-09-27T03:30:00Z"),
     ([("Legal/Regulatory", _SEC)], True, True), "same ET day across the UTC midnight"),
    (_card("Bitcoin slips as traders take profit", "2026-09-26T15:00:00Z"),
     ([], True, True), "same day, untagged: news exists, no clear catalyst"),
    (_card("", "2026-09-26T15:00:00Z"), ([], False, True), "same day, empty headline"),
    (_card(_SEC, "2026-09-25T20:00:00Z"), ([], False, False),
     "OLDER: unchecked — never 'No company news today'"),
    (_card(_SEC, "2026-09-27T15:00:00Z"), ([], False, False), "newer (clock skew): unchecked"),
    (_card(_SEC, None), ([], False, False), "undated: unchecked"),
    (_card(_SEC, "garbage"), ([], False, False), "unparseable: unchecked"),
    (None, ([], False, False), "no card at all"),
    ("not-a-card", ([], False, False), "wrong type"),
])
def test_a_rolling_rows_news_gate_is_its_own_calendar_day(card, expected, why):
    from app.services.widget_movers_service import _classified_rolling_news

    assert _classified_rolling_news(card, _SAT.isoformat()) == expected, why


def _weekend_payload(monkeypatch, *, cards, calendar=_SAT, crypto_pct=-5.2):
    monkeypatch.setattr(wm, "session_trading_date", lambda now=None: date(2026, 9, 25))
    monkeypatch.setattr(wm, "_et_calendar_day", lambda: calendar)
    monkeypatch.setattr(wm, "session_label", lambda now=None: "Closed")
    ranked = rank_movers([
        {"ticker": "BTCUSD", "change_percent": crypto_pct, "price": 104000.0},
        {"ticker": "AMD", "change_percent": -1.0, "price": 150.0, "change_session": _FRI_ISO},
    ], basis="abs_change")
    return WidgetMoversService()._payload(
        mode="portfolio", ranked=ranked, cards=cards,
        ctx=_MarketContext(news_available=True), basket=None, holdings_count=2,
    )


def test_a_crypto_row_classifies_its_saturday_card(monkeypatch):
    out = _weekend_payload(monkeypatch, cards={"BTCUSD": _card(_SEC, "2026-09-26T15:00:00Z")})
    assert out.session_date == _FRI_ISO, "the equity session is untouched"
    btc = out.headline_mover
    assert btc.ticker == "BTCUSD"
    assert btc.cause.kind == "company_news", btc.cause.detail
    assert btc.cause.tag == "Legal/Regulatory"
    assert "could not be checked" not in btc.cause.detail


def test_an_equity_row_with_the_same_saturday_card_stays_unchecked(monkeypatch):
    """CONTROL: the equity gate is unchanged — a card newer than Friday's session says
    nothing about Friday, so it is unchecked, not a negative and not a cause."""
    sat_card = _card("AMD wins a data-center deal", "2026-09-26T15:00:00Z")
    out = _weekend_payload(monkeypatch, cards={"AMD": sat_card})
    amd = out.top_losers[0]
    assert amd.ticker == "AMD"
    assert amd.cause.kind != "company_news", amd.cause.detail
    assert "No company news" not in amd.cause.detail
    assert "could not be checked" in amd.cause.detail, amd.cause.detail


def test_a_crypto_row_with_only_fridays_card_is_unchecked_on_saturday(monkeypatch):
    """Friday's card is not Saturday's news. Before, it was either reported as the cause of
    a move called "today" or turned into the confident "No company news today"."""
    out = _weekend_payload(monkeypatch, cards={"BTCUSD": _card(_SEC, "2026-09-25T20:00:00Z")})
    btc = out.headline_mover
    assert btc.cause.kind != "company_news", btc.cause.detail
    assert "No company news" not in btc.cause.detail
    assert "could not be checked" in btc.cause.detail, btc.cause.detail


def test_just_after_midnight_last_evenings_card_is_unchecked_not_no_news(monkeypatch):
    """00:30 ET Sunday: a card from 23:00 Saturday is still inside the rolling 24 h. A
    calendar-day gate must not turn it into "No company news today"."""
    out = _weekend_payload(
        monkeypatch, calendar=date(2026, 9, 27),
        cards={"BTCUSD": _card(_SEC, "2026-09-27T03:00:00Z")},   # 23:00 ET Sat
    )
    btc = out.headline_mover
    assert "No company news" not in btc.cause.detail, btc.cause.detail
    assert "No clear catalyst" not in btc.cause.detail, btc.cause.detail


# ── …and Ask Cay AI's single-ticker path gives the same answers ────────────────────


def _attribution_service(monkeypatch, *, mover, card, calendar=_SAT, index_rows=None):
    svc = WidgetMoversService()

    async def _rank_and_read(_syms):
        return [mover], {mover.ticker: card}, True, (
            index_rows if index_rows is not None
            else {"SPY": {"symbol": "SPY", "price": 651.0, "changePercentage": -0.2,
                          "changeSession": _FRI_ISO}}
        )

    async def _ctx(_tickers, rows, session_date):
        return _MarketContext(news_available=True).for_tickers({}, session_date, rows)

    monkeypatch.setattr(svc, "_rank_and_read", _rank_and_read)
    monkeypatch.setattr(svc, "_market_context", _ctx)
    monkeypatch.setattr(wm, "session_trading_date", lambda now=None: date(2026, 9, 25))
    monkeypatch.setattr(wm, "_et_calendar_day", lambda: calendar)
    return svc


def _rolling(ticker="BTCUSD", pct=-5.2, stamp=None):
    return RankedMover(ticker=ticker, change_percent=pct, price=104000.0, company_name=None,
                       sigma_daily=None, z=None, tier="notable", change_session=stamp)


@pytest.mark.asyncio
async def test_ask_cay_classifies_a_crypto_rows_saturday_card(monkeypatch):
    svc = _attribution_service(monkeypatch, mover=_rolling(),
                               card=_card(_SEC, "2026-09-26T15:00:00Z"))
    exp = await svc.attribute_ticker_move("BTCUSD")
    assert exp.attribution.kind.value == "company_news", exp.attribution.detail
    assert exp.session_word == "today"
    assert exp.session_date == _FRI_ISO


@pytest.mark.asyncio
async def test_ask_cay_leaves_an_equity_rows_saturday_card_unchecked(monkeypatch):
    svc = _attribution_service(
        monkeypatch, mover=_rolling("NVDA", pct=-4.8, stamp=_FRI_ISO),
        card=_card("Nvidia wins a sovereign AI contract", "2026-09-26T15:00:00Z"),
    )
    exp = await svc.attribute_ticker_move("NVDA")
    assert exp.attribution.kind.value != "company_news", exp.attribution.detail
    assert "No company news" not in exp.attribution.detail
    assert "could not be checked" in exp.attribution.detail


@pytest.mark.asyncio
async def test_ask_cay_leaves_a_crypto_rows_friday_card_unchecked_on_saturday(monkeypatch):
    svc = _attribution_service(monkeypatch, mover=_rolling(),
                               card=_card(_SEC, "2026-09-25T20:00:00Z"))
    exp = await svc.attribute_ticker_move("BTCUSD")
    assert exp.attribution.kind.value != "company_news", exp.attribution.detail
    assert "No company news" not in exp.attribution.detail
    assert "could not be checked" in exp.attribution.detail


@pytest.mark.asyncio
async def test_ask_cay_never_says_a_live_coin_moved_with_a_closed_market(monkeypatch):
    """The widget's per-row rule, on the single-ticker path too: Friday's SPY +4.0% is not
    what a Monday-morning Bitcoin +4.5% "moved with"."""
    fri_spy = {"SPY": {"symbol": "SPY", "price": 660.0, "changePercentage": 4.0,
                       "changeSession": _FRI_ISO}}
    svc = _attribution_service(monkeypatch, mover=_rolling(pct=4.5), card=None,
                               calendar=date(2026, 9, 28), index_rows=fri_spy)
    exp = await svc.attribute_ticker_move("BTCUSD")
    assert exp.attribution.kind.value != "market", exp.attribution.detail
    # CONTROL: an equity row of Friday's session keeps the market leg.
    svc2 = _attribution_service(monkeypatch, mover=_rolling("NVDA", pct=3.9, stamp=_FRI_ISO),
                                card=None, calendar=date(2026, 9, 28), index_rows=fri_spy)
    exp2 = await svc2.attribute_ticker_move("NVDA")
    assert exp2.attribution.kind.value == "market", exp2.attribution.detail
