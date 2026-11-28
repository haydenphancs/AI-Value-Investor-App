"""The widget and Ask Cay AI print a same-session MOVE card's headline as-is (2026-11-27).

Owner decision, verbatim choice: "Trust move cards (Recommended)" — "When the head stock's card
from today's session is a company or market move card in the same direction as the move, the
widget prints that headline as-is, without the extra 2σ and name checks (the card's own judge
already verified it). A 'no news yet' card keeps the widget's industry line ('Semiconductors
rose 13.2%. No clear catalyst…'). Ordinary cards keep today's strict check."

Why: move mode writes a card for a 10%+ move, a band move (no σ) or a held move — often below
z 2 — and its company headline names the company by its DISPLAY name, which the widget's
read-time subject terms (`price_terms`) need not hold. The Updates card showed a judged cause
while the tile said "Today's news leaned bullish."

These tests cover the SERVICE half: `_card_move_tag` reads the server-only `card["move"]` marker,
`_session_news_lean` copies it into the lean, `_classified_today_news` never classifies a
no-cause card, and every surface (Holdings payload, chat) gets the same answer. The pure gate is
in `test_daily_move_attribution.py` (section "Trusted move cards"). Hermetic: quotes, context
and cards are stubbed; the attribution is the real one.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.services import chat_market_tools as cmt
from app.services import widget_movers_service as wm
from app.services.daily_move_attribution import CauseKind
from app.services.widget_movers_service import (
    RankedMover,
    WidgetMoversService,
    _MarketContext,
    _card_move_tag,
    _classified_today_news,
    _market_brief,
    _session_news_lean,
    rank_movers,
)

WED = date(2026, 10, 14)
WED_ISO = WED.isoformat()
THU = date(2026, 10, 15)
NOW = datetime(2026, 10, 14, 18, 0, tzinfo=timezone.utc)          # 14:00 ET Wednesday
OVERNIGHT = datetime(2026, 10, 15, 6, 0, tzinfo=timezone.utc)     # 02:00 ET Thursday
UP_HEADLINE = "CoreWeave wins a new capacity contract with OpenAI"
MARKET_HEADLINE = "CoreWeave moves with the broader market after Nvidia reinstated its CEO"
NONE_HEADLINE = "No news yet explains CoreWeave's move"
DOWN_HEADLINE = "CoreWeave Faces Investor Scrutiny Amid Rising Bond Yields"
INDUSTRY = "Software - Infrastructure"


@pytest.fixture(autouse=True)
def _fresh_logs():
    wm._MALFORMED_MOVE_LOGGED.clear()
    yield
    wm._MALFORMED_MOVE_LOGGED.clear()


def _card(headline=UP_HEADLINE, sentiment="Bullish", generated_at="2026-10-14T17:09:05Z",
          move=("company", "up"), **extra):
    card = {"scope": "CRWV", "headline": headline, "sentiment": sentiment,
            "generated_at": generated_at, "bullets": ["a", "b"], "ai_generated": True}
    if move is not None:
        card["move"] = {"kind": move[0], "direction": move[1]}
    card.update(extra)
    return card


def _plain(card):
    return {k: v for k, v in card.items() if k != "move"}


def _crwv(pct=22.0, z=1.6, stamp=WED_ISO, name=None):
    return RankedMover(ticker="CRWV", change_percent=pct, price=91.0, company_name=name,
                       sigma_daily=0.12, z=z, tier="Notable", change_session=stamp)


def _other(stamp=WED_ISO):
    return RankedMover(ticker="NYAX", change_percent=2.08, price=40.0, company_name="Nayax Ltd.",
                       sigma_daily=0.0327, z=0.64, tier="Typical", change_session=stamp)


def _spy_rows(spy, iso=WED_ISO):
    if spy is None:
        return {}
    return {"SPY": {"symbol": "SPY", "price": 651.0, "changePercentage": spy,
                    "changeSession": iso}}


def _ctx(iso=WED_ISO, ind=4.0, earnings=None, spy=None):
    c = _MarketContext(news_available=True, industry_available=True,
                       industry_changes={INDUSTRY.lower(): ind},
                       industry_dates={INDUSTRY.lower(): iso},
                       earnings_available=earnings is not None, earnings=earnings or {})
    return c.for_tickers({"CRWV": INDUSTRY}, iso, _spy_rows(spy, iso))


def _payload(monkeypatch, *, cards, live=WED, calendar=None, ranked=None, ctx=None):
    monkeypatch.setattr(wm, "session_trading_date", lambda now=None: live)
    monkeypatch.setattr(wm, "_et_calendar_day", lambda: calendar or live)
    monkeypatch.setattr(wm, "session_label", lambda now=None: "Closed")
    return WidgetMoversService()._payload(
        mode="portfolio",
        ranked=ranked or [_crwv(), _other()],
        cards=cards, ctx=ctx or _ctx(), basket=None, holdings_count=14,
    )


def _head(monkeypatch, card, **kw):
    return _payload(monkeypatch, cards={"CRWV": card}, **kw).headline_mover


# ── S1: the marker ─────────────────────────────────────────────────────────────


def test_s1_a_valid_marker_is_read_and_copied_into_the_lean():
    tag = _card_move_tag(_card(move=("market", "down")))
    assert (tag.kind, tag.direction) == ("market", "down")
    lean = _session_news_lean(_card(), True, "CRWV", None, rolling=False, day=WED)
    assert (lean.move_kind, lean.move_direction) == ("company", "up")


def test_s1_no_marker_is_an_ordinary_card_and_logs_nothing(caplog):
    with caplog.at_level(logging.WARNING, logger=wm.__name__):
        assert _card_move_tag(_card(move=None)) is None
        assert _card_move_tag({**_card(move=None), "move": None}) is None
        assert _card_move_tag(None) is None
        lean = _session_news_lean(_card(move=None), True, "CRWV", None, rolling=False, day=WED)
    assert (lean.move_kind, lean.move_direction) == (None, None)
    assert not [r for r in caplog.records if "move marker" in r.getMessage()]


@pytest.mark.parametrize("marker", [
    [], "move:up:company", {}, {"kind": "company"}, {"kind": "company", "direction": "UP"},
    {"kind": ["company"], "direction": "up"}, {"kind": "Company", "direction": "up"},
    {"kind": "bogus", "direction": "down"}, 3,
])
def test_s1_a_malformed_marker_is_ignored_with_one_warning_naming_the_scope(marker, caplog):
    card = {**_card(move=None), "move": marker}
    with caplog.at_level(logging.WARNING, logger=wm.__name__):
        assert _card_move_tag(card) is None
        assert _card_move_tag(card) is None                   # the same card, read again
        lean = _session_news_lean(card, True, "CRWV", None, rolling=False, day=WED)
    assert (lean.move_kind, lean.move_direction) == (None, None)
    warned = [r for r in caplog.records if "malformed move marker" in r.getMessage()]
    assert len(warned) == 1
    assert "CRWV" in warned[0].getMessage() and type(marker).__name__ in warned[0].getMessage()


def test_s1_the_malformed_marker_log_is_bounded():
    for i in range(wm._MALFORMED_MOVE_LOG_MAX + 5):
        _card_move_tag({**_card(move=None, generated_at=f"g{i}"), "move": "junk"})
    assert len(wm._MALFORMED_MOVE_LOGGED) <= wm._MALFORMED_MOVE_LOG_MAX


# ── S2-S6: the Holdings payload ───────────────────────────────────────────────


def test_s2_a_company_move_card_below_two_sigma_with_no_company_name_is_the_cause(monkeypatch):
    p = _payload(monkeypatch, cards={"CRWV": _card()})
    head = p.headline_mover
    assert head.ticker == "CRWV"
    assert head.cause.kind == "company_news" and head.cause.tag == "Bullish News"
    assert head.cause.detail == UP_HEADLINE + "."
    assert head.cause.detail_aged == head.cause.detail
    assert head.cause.note == f"{INDUSTRY} rose 4.0%."
    assert head.cause_card is True
    cause = p.model_dump()["headline_mover"]["cause"]
    assert set(cause) == {"kind", "tag", "detail", "detail_aged", "note"}
    assert cause["kind"] in {"earnings", "analyst", "company_news", "sector", "market", "none"}
    # CONTROL: the same card without its marker is the strict gate's withheld lean.
    plain = _head(monkeypatch, _plain(_card()))
    assert plain.cause.kind == "none" and plain.cause.detail.endswith("Today's news leaned bullish.")


def test_s3_a_market_move_card_prints_its_composed_headline(monkeypatch):
    head = _head(monkeypatch, _card(headline=MARKET_HEADLINE, move=("market", "up")))
    assert head.cause.kind == "company_news" and head.cause.tag == "Bullish News"
    assert head.cause.detail == MARKET_HEADLINE + "."
    assert head.cause.note == f"{INDUSTRY} rose 4.0%."


def test_s3_a_market_move_card_is_withheld_once_the_index_turned_the_other_way(monkeypatch):
    """Review 2026-11-27: the S&P 500 −0.9% this session while CRWV is up. The market card's
    "moves with the broader market" no longer stands (the writer's `market_card_lost`); the
    tile states the lean, and its own context still prints the index figure it is checked
    against. A company card under the same index is unaffected."""
    market = _card(headline=MARKET_HEADLINE, move=("market", "up"))
    head = _head(monkeypatch, market, ctx=_ctx(spy=-0.9))
    # The lean still comes from the card, so the row still links to it (`cause_card`).
    assert head.cause.kind == "none" and head.cause_card is True
    assert head.cause.detail.endswith("Today's news leaned bullish."), head.cause.detail
    assert head.context.market_change_percent == -0.9
    # The same way, or under the line: still trusted.
    for spy in (0.3, -0.3):
        kept = _head(monkeypatch, market, ctx=_ctx(spy=spy))
        assert kept.cause.kind == "company_news" and kept.cause.detail == MARKET_HEADLINE + "."
    company = _head(monkeypatch, _card(), ctx=_ctx(spy=-0.9))
    assert company.cause.kind == "company_news" and company.cause.detail == UP_HEADLINE + "."


@pytest.mark.parametrize("sentiment", ["Neutral", "Bullish"])
def test_s4_a_no_cause_card_keeps_the_industry_line(monkeypatch, sentiment):
    head = _head(monkeypatch, _card(headline=NONE_HEADLINE, sentiment=sentiment,
                                    move=("none", "up")),
                 ranked=[_crwv(pct=31.0), _other()], ctx=_ctx(ind=13.2))
    assert head.cause.kind == "none"
    assert head.cause.detail == f"{INDUSTRY} rose 13.2%. No clear catalyst in today's news."
    assert head.cause.tag is None and head.cause.note is None
    assert head.cause_card is False


def test_s5_a_no_cause_cards_headline_is_never_classified(monkeypatch):
    card = _card(headline="CoreWeave raises guidance for the year", sentiment="Neutral",
                 move=("none", "up"))
    assert _classified_today_news(card, WED_ISO) == ([], True, True)
    head = _head(monkeypatch, card)
    assert head.cause.kind == "none" and head.cause.tag is None
    assert head.cause.detail.endswith("No clear catalyst in today's news."), head.cause.detail
    # CONTROL: unmarked, the classifier tags it.
    plain = _head(monkeypatch, _plain(card))
    assert plain.cause.kind == "company_news" and plain.cause.tag == "Guidance Raised"


def test_s6_a_move_card_for_the_other_direction_is_refused(monkeypatch):
    head = _head(monkeypatch, _card(), ranked=[_crwv(pct=-6.0, z=1.6), _other()],
                 ctx=_ctx(ind=-1.0))
    assert head.cause.kind == "none"
    assert UP_HEADLINE not in head.cause.detail and "leaned" not in head.cause.detail
    assert head.cause.detail.endswith("No clear catalyst in today's news.")


# ── S7-S10: the gates that are kept ────────────────────────────────────────────


def test_s7_a_card_newer_than_the_session_stays_unchecked(monkeypatch):
    head = _head(monkeypatch, _card(headline=DOWN_HEADLINE, sentiment="Bearish",
                                    move=("company", "down"),
                                    generated_at="2026-10-15T10:05:00Z"),
                 live=THU, ranked=[_crwv(pct=-15.0), _other()], ctx=_ctx(ind=-4.0))
    assert head.cause.kind != "company_news", head.cause.detail
    assert "leaned" not in head.cause.detail and "No company news" not in head.cause.detail


def test_s7_yesterdays_move_card_explains_yesterday_on_a_pre_market_tile(monkeypatch):
    """Thursday pre-market, the tile describes Wednesday: Wednesday's move card is trusted —
    no second date gate (`move_card_today` would have refused it)."""
    head = _head(monkeypatch, _card(), live=THU)
    assert head.cause.kind == "company_news" and head.cause.detail == UP_HEADLINE + "."
    assert head.cause.detail_aged == head.cause.detail


_SPY_OK = {"SPY": {"symbol": "SPY", "price": 651.0, "changePercentage": 0.2}}


def _service(monkeypatch, cards, *, now=NOW, calendar=WED):
    monkeypatch.setattr(wm, "session_trading_date", lambda now=None: WED)
    monkeypatch.setattr(wm, "_et_calendar_day", lambda: calendar)
    monkeypatch.setattr(wm, "session_label", lambda now=None: "Closed")
    monkeypatch.setattr(wm, "session_phase", lambda now=None: "regular")
    monkeypatch.setattr(wm, "_utc_now", lambda: now)
    svc = WidgetMoversService()

    async def _rank_and_read(tickers, **kwargs):
        return [_crwv(), _other()], dict(cards), True, dict(_SPY_OK)

    async def _market_context(tickers, index_rows, session_date):
        return _ctx(session_date).for_tickers({"CRWV": INDUSTRY}, session_date, index_rows)

    async def _sectors(user_id, tickers):
        return {}

    async def _head_grades(ranked):
        return None

    monkeypatch.setattr(svc, "_rank_and_read", _rank_and_read)
    monkeypatch.setattr(svc, "_market_context", _market_context)
    monkeypatch.setattr(svc, "_sectors", _sectors)
    monkeypatch.setattr(svc, "_head_grades", _head_grades)
    return svc


@pytest.mark.asyncio
async def test_s8_a_web_only_move_card_keeps_the_web_only_hold(monkeypatch):
    card = _card(web_only=True, web_assisted=True)
    held = (await _service(monkeypatch, {"CRWV": card, "NYAX": None})
            .get_portfolio_mover("u1", ["CRWV", "NYAX"], links_web_only=False)).headline_mover
    assert held.cause.kind != "company_news" and UP_HEADLINE not in held.cause.detail
    assert held.cause_card is False
    shown = (await _service(monkeypatch, {"CRWV": card, "NYAX": None})
             .get_portfolio_mover("u1", ["CRWV", "NYAX"], links_web_only=True)).headline_mover
    assert shown.cause.kind == "company_news" and shown.cause.detail == UP_HEADLINE + "."
    assert shown.cause_card is True
    # 02:00 ET Thursday, the row still Wednesday's: the card is no longer on the feed → held.
    overnight = (await _service(monkeypatch, {"CRWV": card, "NYAX": None}, now=OVERNIGHT,
                                calendar=THU)
                 .get_portfolio_mover("u1", ["CRWV", "NYAX"], links_web_only=True)).headline_mover
    assert overnight.cause.kind != "company_news" and UP_HEADLINE not in overnight.cause.detail
    # CONTROL: the same night, an FMP-news move card still explains its session.
    fmp = (await _service(monkeypatch, {"CRWV": _card(), "NYAX": None}, now=OVERNIGHT,
                          calendar=THU)
           .get_portfolio_mover("u1", ["CRWV", "NYAX"], links_web_only=True)).headline_mover
    assert fmp.cause.kind == "company_news"


def test_s9_an_after_close_move_card_on_a_report_day_only_leans(monkeypatch):
    earnings = {"CRWV": {"date": WED_ISO, "time": "amc", "epsActual": -0.21, "epsEstimated": 0.05}}
    head = _head(monkeypatch, _card(headline="CoreWeave Misses Revenue Estimates",
                                    sentiment="Bearish", move=("company", "down"),
                                    generated_at="2026-10-14T20:40:00Z"),
                 ranked=[_crwv(pct=-15.46), _other()], ctx=_ctx(ind=-4.0, earnings=earnings))
    assert head.cause.kind == "none"
    assert head.cause.detail.endswith("Today's news leaned bearish."), head.cause.detail


def _btc(monkeypatch, generated_at, move=("company", "up")):
    card = _card(headline="Hong Kong approves new spot Bitcoin funds", generated_at=generated_at,
                 move=move)
    card["scope"] = "BTCUSD"
    return _payload(
        monkeypatch, live=date(2026, 10, 16), calendar=date(2026, 10, 17),
        cards={"BTCUSD": card},
        ranked=rank_movers([
            {"ticker": "BTCUSD", "change_percent": 3.0, "price": 104000.0},
            {"ticker": "AMD", "change_percent": -1.0, "price": 150.0, "change_session": "2026-10-16"},
        ], basis="abs_change"),
        ctx=_MarketContext(news_available=True),
    ).headline_mover


def test_s10_a_coins_move_card_on_its_own_calendar_day_is_trusted(monkeypatch):
    head = _btc(monkeypatch, "2026-10-17T15:00:00Z")          # Saturday 11:00 ET, on Saturday
    assert head.ticker == "BTCUSD"
    assert head.cause.kind == "company_news"
    assert head.cause.detail == "Hong Kong approves new spot Bitcoin funds."
    # CONTROL: unmarked, a σ-less +3% is below the 5% fixed band.
    assert _btc(monkeypatch, "2026-10-17T15:00:00Z", move=None).cause.kind != "company_news"


def test_s10_a_coins_friday_move_card_is_not_saturdays_cause(monkeypatch):
    head = _btc(monkeypatch, "2026-10-16T18:00:00Z")
    assert head.cause.kind != "company_news"
    assert "leaned" not in head.cause.detail


# ── S11: the Market tile ───────────────────────────────────────────────────────


def test_s11_a_forged_move_marker_changes_nothing_on_the_market_brief():
    card = {"scope": wm.MARKET_SCOPE, "headline": "Stocks weigh a new tariff announcement",
            "sentiment": "Bearish", "generated_at": "2026-10-14T17:00:00Z"}
    forged = {**card, "move": {"kind": "company", "direction": "down"}}
    assert _market_brief(forged, WED_ISO) == _market_brief(card, WED_ISO)
    assert _market_brief(card, WED_ISO) is not None


# ── S12: Ask Cay AI gives the widget's answer ──────────────────────────────────


def _attribution_service(monkeypatch, *, card, mover=None, ind=4.0, spy=0.4):
    svc = WidgetMoversService()
    mover = mover or _crwv()
    rows = _spy_rows(spy)

    async def _rank_and_read(_syms):
        return [mover], {mover.ticker: card}, True, rows

    async def _context(_tickers, index_rows, session_date):
        return _ctx(session_date, ind=ind).for_tickers({"CRWV": INDUSTRY}, session_date, index_rows)

    monkeypatch.setattr(svc, "_rank_and_read", _rank_and_read)
    monkeypatch.setattr(svc, "_market_context", _context)
    monkeypatch.setattr(wm, "session_trading_date", lambda now=None: WED)
    monkeypatch.setattr(wm, "_et_calendar_day", lambda: WED)
    monkeypatch.setattr(wm, "_utc_now", lambda: NOW)
    return svc


async def _explain(monkeypatch, svc):
    monkeypatch.setattr(wm, "get_widget_movers_service", lambda: svc)
    monkeypatch.setattr(cmt, "fetch_ticker_news",
                        AsyncMock(return_value={"news_available": True, "articles": [{"title": "x"}]}))
    return await cmt.explain_price_move("CRWV")


@pytest.mark.asyncio
async def test_s12_chat_gets_the_widgets_move_card_answer(monkeypatch):
    svc = _attribution_service(monkeypatch, card=_card())
    exp = await svc.attribute_ticker_move("CRWV")
    a = exp.attribution
    assert a.kind is CauseKind.COMPANY_NEWS and a.tag == "Bullish News"
    assert a.move_card == "company" and exp.web_assisted is False
    widget = _head(monkeypatch, _card())
    assert widget.cause.detail == a.detail and widget.cause.note == a.note

    out = await _explain(monkeypatch, svc)
    assert out["cause"] == "Bullish News" and out["explanation"] == UP_HEADLINE + "."
    assert out["explanation_basis"].startswith("news_lean:")
    assert "web_assisted" not in out and "no_single_catalyst" not in out


@pytest.mark.asyncio
async def test_s12_a_web_assisted_move_card_marks_the_chat_turn(monkeypatch):
    svc = _attribution_service(monkeypatch, card=_card(web_assisted=True))
    assert (await svc.attribute_ticker_move("CRWV")).web_assisted is True
    out = await _explain(monkeypatch, svc)
    assert out["web_assisted"] is True and out["explanation"] == UP_HEADLINE + "."


@pytest.mark.asyncio
async def test_s12_chat_withholds_a_market_card_under_a_turned_index_like_the_widget(monkeypatch):
    market = _card(headline=MARKET_HEADLINE, move=("market", "up"))
    svc = _attribution_service(monkeypatch, card=market, spy=-0.9)
    exp = await svc.attribute_ticker_move("CRWV")
    assert exp.attribution.kind is CauseKind.NONE and exp.attribution.lean == "bullish"
    assert exp.attribution.move_card is None and exp.market_change_percent == -0.9
    out = await _explain(monkeypatch, svc)
    assert "news leaned bullish" in out["bottom_line"]
    assert "explanation_basis" not in out
    # CONTROL: the index the same way → the card is the answer.
    svc = _attribution_service(monkeypatch, card=market, spy=0.4)
    assert (await svc.attribute_ticker_move("CRWV")).attribution.move_card == "market"


@pytest.mark.asyncio
async def test_s12_a_no_cause_card_gives_chat_no_lean(monkeypatch):
    svc = _attribution_service(monkeypatch, card=_card(headline=NONE_HEADLINE, sentiment="Bullish",
                                                       move=("none", "up")))
    exp = await svc.attribute_ticker_move("CRWV")
    assert exp.attribution.kind is CauseKind.NONE and exp.attribution.lean is None
    out = await _explain(monkeypatch, svc)
    assert out["no_single_catalyst"] is True
    assert "leaned" not in out["bottom_line"]
    assert "explanation_basis" not in out


# ── S13: rows below the head ───────────────────────────────────────────────────


def test_s13_a_runner_row_carries_the_move_card_but_never_an_aged_wording(monkeypatch):
    nyax = RankedMover(ticker="NYAX", change_percent=40.0, price=40.0, company_name="Nayax Ltd.",
                       sigma_daily=0.0327, z=12.0, tier="Extreme", change_session=WED_ISO)
    out = _payload(monkeypatch, cards={"CRWV": _card()}, ranked=[nyax, _crwv()])
    assert out.headline_mover.ticker == "NYAX"
    rows = out.top_gainers + out.top_losers + out.runners_up
    crwv = [m for m in rows if m.ticker == "CRWV"]
    assert crwv and crwv[0].cause.kind == "company_news"
    assert crwv[0].cause.detail == UP_HEADLINE + "."
    assert all(m.cause.detail_aged is None for m in rows)
