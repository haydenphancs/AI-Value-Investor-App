"""The widget's web-only cause gate and its `cause_card` link flag (2026-11-12).

A big mover with no FMP news may now get an Insights card written from the Updates jump
search's web pages alone ("Web search fills it"). Those pages are the card's only sources, and a
reader reaches them only on the Updates card. So:

* `cause_card` tells a new build that a mover's cause came from its Insights card, and the row
  links to `caydex://updates/<SYMBOL>` instead of the detail;
* a WEB-ONLY card may back a widget cause only for a caller that PROVES it can link it —
  `X-App-Version` >= `WEB_ONLY_CAUSE_MIN_APP_VERSION`, fail closed — and only on the ET day it was
  written (the feed's own visibility rule). Anyone else is served as if the ticker had no card;
* the 60 s payload cache is keyed on that gate, so one caller's answer never reaches the other.

Hermetic: the quote, context and card reads are stubbed; the attribution is the real one.
"""

from __future__ import annotations

import contextvars
import inspect
import logging
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints import widget
from app.core import client_app_version as cav
from app.core.client_app_version import (
    WEB_ONLY_CAUSE_MIN_APP_VERSION,
    capture_client_app_version,
    client_is_at_least,
    parse_app_version,
    set_client_app_version,
)
from app.services import widget_movers_service as wm
from app.services.widget_movers_service import (
    MARKET_SCOPE,
    RankedMover,
    WidgetMoversService,
    _MarketContext,
    _payload_card_scopes,
    _payload_key,
    cards_for_caller,
)

WED = date(2026, 10, 14)
WED_ISO = WED.isoformat()
# 14:00 ET on the Wednesday, the clock every build below judges cards against.
NOW = datetime(2026, 10, 14, 18, 0, tzinfo=timezone.utc)
HEADLINE = "CoreWeave Faces Investor Scrutiny Amid Rising Bond Yields"
SECRET_PAGE_TEXT = "page-snippet-that-must-never-be-logged"


def _in_fresh_context(fn, *args):
    """Run `fn` with a clean copy of the context, so a recorded version never leaks."""
    return contextvars.copy_context().run(fn, *args)


def _at_least_with_header(raw, minimum=WEB_ONLY_CAUSE_MIN_APP_VERSION) -> bool:
    def _run():
        set_client_app_version(raw)
        return client_is_at_least(minimum)
    return _in_fresh_context(_run)


# ── 1. client_is_at_least: fail closed ───────────────────────────────────────


def test_the_minimum_is_the_first_version_after_the_last_unlinked_one():
    """1.02 (build 13, live) does not carry the link, and builds sharing a marketing version send
    the same header, so the gate must sit strictly above 1.02. 1.03 carries it (no 1.03 build
    existed before the link landed; owner 2026-11-12: "keep all 1.03")."""
    floor = parse_app_version(WEB_ONLY_CAUSE_MIN_APP_VERSION)
    assert floor == (1, 3, 0)
    assert floor > parse_app_version("1.02")


@pytest.mark.parametrize("raw,expected", [
    ("1.04", True),
    ("1.4", True),
    ("1.04.1", True),
    ("1.10", True),      # (1, 10, 0) — whole-number compare, not string
    ("2.0", True),
    (" 1.04 ", True),    # parse trims
    ("1.03", True),
    ("1.02", False),
    ("1.0.9", False),
    ("1.01", False),
    ("1.0", False),
])
def test_a_parseable_header_is_compared_as_whole_numbers(raw, expected):
    assert _at_least_with_header(raw) is expected


@pytest.mark.parametrize("raw", [
    None, "", "   ", "v1.04", "1.04 (15)", "1.04.0.1", "latest", "1.x", "9" * 40,
])
def test_no_header_or_an_unreadable_one_is_never_at_least(raw):
    """The opposite of `client_is_older_than`: absence must HOLD, because the old widget
    extension sends no header at all."""
    assert _at_least_with_header(raw) is False


def test_nothing_recorded_is_never_at_least():
    assert _in_fresh_context(lambda: client_is_at_least("0.0")) is False


@pytest.mark.parametrize("minimum", ["", "abc", None, 104, (1, 4), (1, "4", 0), (True, 4, 0), (1, -4, 0)])
def test_an_unreadable_minimum_fails_closed_and_warns(minimum, caplog):
    with caplog.at_level(logging.WARNING, logger=cav.__name__):
        assert _at_least_with_header("9.9", minimum) is False
    assert "unreadable minimum" in caplog.text


def test_a_tuple_minimum_is_accepted():
    assert _at_least_with_header("1.04", (1, 4, 0)) is True
    assert _at_least_with_header("1.03", (1, 4, 0)) is False
    assert _at_least_with_header("1.03", (1, 3, 0)) is True


# ── 2. cards_for_caller: the hold itself ─────────────────────────────────────


def _card(*, web_only=False, generated_at="2026-10-14T17:09:05Z", sentiment="Bearish",
          headline=HEADLINE, web_assisted=False):
    card = {"scope": "CRWV", "headline": headline, "sentiment": sentiment,
            "generated_at": generated_at, "bullets": [SECRET_PAGE_TEXT], "ai_generated": True}
    if web_only:
        card["web_only"] = True
    if web_assisted:
        card["web_assisted"] = True
    return card


def test_a_linking_caller_keeps_todays_web_only_card():
    card = _card(web_only=True)
    out = cards_for_caller({"CRWV": card}, links_web_only=True, now=NOW)
    assert out["CRWV"] is card


def test_a_caller_that_cannot_link_gets_no_card_at_all():
    out = cards_for_caller({"CRWV": _card(web_only=True)}, links_web_only=False, now=NOW)
    assert out == {"CRWV": None}


@pytest.mark.parametrize("generated_at", [
    "2026-10-13T17:09:05Z",   # yesterday ET: the feed no longer shows it
    "2026-10-14T03:59:00Z",   # 23:59 ET on the 13th — still yesterday in ET
    "2026-10-15T09:00:00Z",   # future beyond clock skew
    None, "", "not-a-date", 12345,
])
def test_a_web_only_card_the_feed_hides_is_held_even_for_a_linking_caller(generated_at):
    out = cards_for_caller({"CRWV": _card(web_only=True, generated_at=generated_at)},
                           links_web_only=True, now=NOW)
    assert out == {"CRWV": None}, generated_at


@pytest.mark.parametrize("links", [True, False])
def test_an_fmp_or_mixed_card_is_never_held(links):
    """Only the server-only `web_only` marker holds a card: a card with FMP sources (mixed, so
    merely `web_assisted`) has links an old build already reaches by its detail screen."""
    plain, mixed = _card(), _card(web_assisted=True)
    out = cards_for_caller({"A": plain, "B": mixed, "C": None}, links_web_only=links, now=NOW)
    assert out == {"A": plain, "B": mixed, "C": None}


@pytest.mark.parametrize("marker", ["true", 1, "yes", {"x": 1}])
def test_only_a_literal_true_marker_counts(marker):
    card = _card()
    card["web_only"] = marker
    out = cards_for_caller({"CRWV": card}, links_web_only=False, now=NOW)
    assert out["CRWV"] is card


@pytest.mark.parametrize("cards", [None, [], "cards", 7])
def test_a_non_dict_card_map_reads_as_no_cards(cards):
    assert cards_for_caller(cards, links_web_only=True, now=NOW) == {}


def test_malformed_entries_pass_through_for_the_existing_gates():
    out = cards_for_caller({"A": "junk", "B": 3, "C": []}, links_web_only=False, now=NOW)
    assert out == {"A": "junk", "B": 3, "C": []}


def test_the_hold_never_mutates_the_shared_map_and_logs_counts_only(caplog):
    shared = {"CRWV": _card(web_only=True), "NYAX": _card()}
    before = {k: dict(v) for k, v in shared.items()}
    with caplog.at_level(logging.INFO, logger=wm.__name__):
        out = cards_for_caller(shared, links_web_only=False, now=NOW)
    assert out is not shared and shared == before
    assert "held 1 web-only card(s) of 2" in caplog.text
    assert SECRET_PAGE_TEXT not in caplog.text and HEADLINE not in caplog.text


# ── 3. Cache keys ────────────────────────────────────────────────────────────


def test_each_gate_result_has_its_own_key_and_the_scopes_survive_it():
    assert _payload_key("market", False) == "market"
    assert _payload_key("market", True) != "market"
    assert _payload_card_scopes(_payload_key("market", True)) == frozenset({MARKET_SCOPE})
    key = _payload_key("portfolio:u1:AAPL,CRWV", True)
    assert _payload_card_scopes(key) == frozenset({"AAPL", "CRWV"})
    assert _payload_card_scopes("portfolio:u1:AAPL,CRWV") == frozenset({"AAPL", "CRWV"})


def test_a_new_card_evicts_both_gate_entries():
    svc = WidgetMoversService()
    plain = _payload_key("portfolio:u1:CRWV,NYAX", False)
    linked = _payload_key("portfolio:u1:CRWV,NYAX", True)
    svc._cache[plain] = (0.0, object())
    svc._cache[linked] = (0.0, object())
    svc.forget_payloads_for("crwv")
    assert plain not in svc._cache and linked not in svc._cache


# ── 4. The build: cause_card and the hold, end to end over the real attribution ──


def _crwv():
    return RankedMover(ticker="CRWV", change_percent=-15.46, price=71.2,
                       company_name="CoreWeave, Inc.", sigma_daily=0.0602, z=2.57,
                       tier="Unusual", change_session=WED_ISO)


def _nyax():
    return RankedMover(ticker="NYAX", change_percent=2.08, price=40.0, company_name="Nayax Ltd.",
                       sigma_daily=0.0327, z=0.64, tier="Typical", change_session=WED_ISO)


_SPY_OK = {"SPY": {"symbol": "SPY", "price": 651.0, "changePercentage": -0.2}}


def _service(monkeypatch, cards):
    """A real `WidgetMoversService` whose quote, context, sectors and grades are stubbed.
    Returns (service, builds) — `builds` counts the card reads, i.e. the real builds."""
    monkeypatch.setattr(wm, "session_trading_date", lambda now=None: WED)
    monkeypatch.setattr(wm, "_et_calendar_day", lambda: WED)
    monkeypatch.setattr(wm, "session_label", lambda now=None: "Closed")
    monkeypatch.setattr(wm, "session_phase", lambda now=None: "regular")
    monkeypatch.setattr(wm, "_utc_now", lambda: NOW)
    svc = WidgetMoversService()
    builds = {"n": 0}

    async def _rank_and_read(tickers, **kwargs):
        builds["n"] += 1
        # A fresh map each build, as `get_cards` returns.
        return [_crwv(), _nyax()], dict(cards), True, dict(_SPY_OK)

    async def _market_context(tickers, index_rows, session_date):
        c = _MarketContext(news_available=True, industry_available=True,
                           industry_changes={"software - infrastructure": -4.0},
                           industry_dates={"software - infrastructure": WED_ISO})
        return c.for_tickers({"CRWV": "Software - Infrastructure"}, session_date, index_rows)

    async def _sectors(user_id, tickers):
        return {}

    async def _head_grades(ranked):
        return None

    monkeypatch.setattr(svc, "_rank_and_read", _rank_and_read)
    monkeypatch.setattr(svc, "_market_context", _market_context)
    monkeypatch.setattr(svc, "_sectors", _sectors)
    monkeypatch.setattr(svc, "_head_grades", _head_grades)
    return svc, builds


def _row(payload, ticker):
    rows = [payload.headline_mover, *payload.runners_up, *payload.top_gainers, *payload.top_losers]
    return next(r for r in rows if r is not None and r.ticker == ticker)


@pytest.mark.asyncio
async def test_a_linking_caller_gets_the_web_only_cause_and_the_card_link(monkeypatch):
    svc, _ = _service(monkeypatch, {"CRWV": _card(web_only=True), "NYAX": None})
    p = await svc.get_portfolio_mover("u1", ["CRWV", "NYAX"], links_web_only=True)
    head = p.headline_mover
    assert head.ticker == "CRWV"
    assert head.cause.kind == "company_news" and head.cause.detail == HEADLINE + "."
    assert head.cause_card is True
    assert p.model_dump()["headline_mover"]["cause_card"] is True
    # No card behind NYAX: its cause never claims one.
    assert _row(p, "NYAX").cause_card is False


@pytest.mark.asyncio
async def test_an_old_build_is_served_as_if_there_were_no_card(monkeypatch):
    svc, _ = _service(monkeypatch, {"CRWV": _card(web_only=True), "NYAX": None})
    held = (await svc.get_portfolio_mover("u1", ["CRWV", "NYAX"])).headline_mover

    # The reference: the same build with NO card for CRWV at all.
    svc2, _ = _service(monkeypatch, {"CRWV": None, "NYAX": None})
    bare = (await svc2.get_portfolio_mover("u1", ["CRWV", "NYAX"])).headline_mover

    assert held.cause_card is False
    assert held.cause.kind != "company_news"
    assert HEADLINE not in held.cause.detail and HEADLINE not in (held.cause.detail_aged or "")
    assert held.cause.model_dump() == bare.cause.model_dump()


@pytest.mark.asyncio
async def test_the_cache_never_hands_one_gate_the_others_payload(monkeypatch):
    svc, builds = _service(monkeypatch, {"CRWV": _card(web_only=True), "NYAX": None})
    linked = await svc.get_portfolio_mover("u1", ["CRWV", "NYAX"], links_web_only=True)
    held = await svc.get_portfolio_mover("u1", ["CRWV", "NYAX"], links_web_only=False)
    assert builds["n"] == 2, "the second gate was answered from the first gate's cache entry"
    assert linked.headline_mover.cause_card is True
    assert held.headline_mover.cause_card is False and held.headline_mover.cause.kind != "company_news"
    # And each gate IS cached for itself.
    again_linked = await svc.get_portfolio_mover("u1", ["CRWV", "NYAX"], links_web_only=True)
    again_held = await svc.get_portfolio_mover("u1", ["CRWV", "NYAX"])
    assert builds["n"] == 2
    assert again_linked is linked and again_held is held


@pytest.mark.asyncio
async def test_the_held_order_first_does_not_leak_either(monkeypatch):
    svc, builds = _service(monkeypatch, {"CRWV": _card(web_only=True), "NYAX": None})
    held = await svc.get_portfolio_mover("u1", ["CRWV", "NYAX"])
    linked = await svc.get_portfolio_mover("u1", ["CRWV", "NYAX"], links_web_only=True)
    assert builds["n"] == 2
    assert held.headline_mover.cause_card is False
    assert linked.headline_mover.cause_card is True


@pytest.mark.asyncio
@pytest.mark.parametrize("links", [True, False])
async def test_an_fmp_card_backs_the_cause_for_every_build(links, monkeypatch):
    """The flag is additive: an old build ignores `cause_card` and keeps today's cause."""
    svc, _ = _service(monkeypatch, {"CRWV": _card(), "NYAX": None})
    head = (await svc.get_portfolio_mover("u1", ["CRWV", "NYAX"], links_web_only=links)).headline_mover
    assert head.cause.kind == "company_news" and head.cause_card is True


@pytest.mark.asyncio
async def test_a_card_that_does_not_back_the_cause_sets_no_flag(monkeypatch):
    """A Bullish card under a −15% move is refused by the lean gate: the cause is the industry or
    none, so the row must not open the card as if it explained the move."""
    svc, _ = _service(monkeypatch, {"CRWV": _card(sentiment="Bullish"), "NYAX": None})
    head = (await svc.get_portfolio_mover("u1", ["CRWV", "NYAX"], links_web_only=True)).headline_mover
    assert head.cause.kind != "company_news"
    assert head.cause_card is False


@pytest.mark.asyncio
async def test_yesterdays_web_only_card_never_backs_a_linking_callers_cause(monkeypatch):
    svc, _ = _service(monkeypatch, {
        "CRWV": _card(web_only=True, generated_at="2026-10-13T17:09:05Z"), "NYAX": None,
    })
    head = (await svc.get_portfolio_mover("u1", ["CRWV", "NYAX"], links_web_only=True)).headline_mover
    assert head.cause_card is False and head.cause.kind != "company_news"


@pytest.mark.asyncio
async def test_the_market_build_applies_the_same_hold(monkeypatch):
    svc, builds = _service(monkeypatch, {"CRWV": _card(web_only=True), "NYAX": None})

    async def _universe():
        return ["CRWV", "NYAX"]

    async def _none():
        return None

    async def _grades(_ranked):
        return None

    monkeypatch.setattr(svc, "_swept_universe", _universe)
    monkeypatch.setattr(svc, "_market_card", _none)
    monkeypatch.setattr(svc, "_crypto_tile", _none)
    monkeypatch.setattr(svc, "_head_grades", _grades)
    # Market payloads are cached only when they carry assets or a brief; the hold is what this
    # checks, so take the build directly too.
    held = await svc._build_market()
    linked = await svc._build_market(links_web_only=True)
    assert _row(held, "CRWV").cause_card is False
    assert _row(held, "CRWV").cause.kind != "company_news"
    assert _row(linked, "CRWV").cause_card is True
    via_get = await svc.get_market_mover(links_web_only=True)
    assert _row(via_get, "CRWV").cause_card is True


# ── 4b. Ask Cay AI's attribution obeys the same same-ET-day rule ─────────────

THU = date(2026, 10, 15)
# 02:00 ET on the Thursday: the row is still Wednesday's session, so `_classified_today_news`
# would still accept Wednesday's card — the overnight window the hold must close.
OVERNIGHT = datetime(2026, 10, 15, 6, 0, tzinfo=timezone.utc)


def _overnight(monkeypatch, cards, *, now):
    svc, _ = _service(monkeypatch, cards)
    monkeypatch.setattr(wm, "_et_calendar_day", lambda: THU)
    monkeypatch.setattr(wm, "_utc_now", lambda: now)
    return svc


@pytest.mark.asyncio
async def test_chat_attribution_uses_todays_web_only_card(monkeypatch):
    """The control: on the card's own ET day chat cites it, so the overnight case below is not
    vacuous (the hold is what flips it, not some other gate)."""
    svc, _ = _service(monkeypatch, {"CRWV": _card(web_only=True), "NYAX": None})
    exp = await svc.attribute_ticker_move("CRWV")
    assert exp.attribution.kind.value == "company_news"
    assert exp.attribution.detail == HEADLINE + "."


@pytest.mark.asyncio
async def test_chat_attribution_holds_yesterdays_web_only_card_overnight(monkeypatch):
    svc = _overnight(monkeypatch, {"CRWV": _card(web_only=True, web_assisted=True), "NYAX": None},
                     now=OVERNIGHT)
    exp = await svc.attribute_ticker_move("CRWV")
    assert exp is not None
    assert exp.attribution.kind.value != "company_news"
    assert HEADLINE not in (exp.attribution.detail or "")
    assert exp.attribution.lean is None
    # The held card backs nothing, so the turn is not marked web-assisted either.
    assert exp.web_assisted is False

    # Exactly what the same night gives with NO card at all.
    bare = await _overnight(monkeypatch, {"CRWV": None, "NYAX": None},
                            now=OVERNIGHT).attribute_ticker_move("CRWV")
    assert exp.attribution == bare.attribution


@pytest.mark.asyncio
async def test_chat_attribution_keeps_an_fmp_card_overnight(monkeypatch):
    """Only WEB-ONLY cards are held: an FMP-news card still explains its session overnight."""
    svc = _overnight(monkeypatch, {"CRWV": _card(), "NYAX": None}, now=OVERNIGHT)
    exp = await svc.attribute_ticker_move("CRWV")
    assert exp.attribution.kind.value == "company_news"


# ── 5. The routes record the version and pass the gate ───────────────────────


def test_both_routes_capture_the_app_version():
    for route in (widget.get_market_mover, widget.get_portfolio_mover):
        dep = inspect.signature(route).parameters["_app_version"].default
        assert getattr(dep, "dependency", None) is capture_client_app_version, route.__name__


class _Recorder:
    def __init__(self):
        self.calls = []

    async def get_market_mover(self, *, links_web_only=False):
        self.calls.append(links_web_only)
        from app.schemas.widget import WidgetMoverPayload
        return WidgetMoverPayload(mode="market", as_of="2026-10-14T18:00:00Z",
                                  market_session="regular", session_date=WED_ISO,
                                  session_label="Open")


@pytest.mark.parametrize("header,expected", [
    (None, False), ("1.02", False), ("1.03 (14)", False), ("", False),
    ("1.03", True), ("1.04", True), ("1.10", True),
])
def test_the_market_route_passes_the_gate_from_the_real_header(header, expected, monkeypatch):
    """Over HTTP, with the extension's widget token: the dependency's ContextVar reaches the
    handler, and a missing header (every extension before 1.03) holds."""
    from app.core.security import create_widget_token
    from app.dependencies import WIDGET_TOKEN_HEADER
    from app.main import app

    recorder = _Recorder()
    monkeypatch.setattr(widget, "get_widget_movers_service", lambda: recorder)
    headers = {WIDGET_TOKEN_HEADER: create_widget_token("11111111-2222-3333-4444-555555555555")}
    if header is not None:
        headers["X-App-Version"] = header
    # No `with`: the lifespan (background loops) is not this test's business.
    resp = TestClient(app).get("/api/v1/widget/market-mover", headers=headers)
    assert resp.status_code == 200, resp.text
    assert recorder.calls == [expected]


@pytest.mark.asyncio
@pytest.mark.parametrize("header,expected", [(None, False), ("1.02", False), ("1.03", True)])
async def test_the_portfolio_route_passes_the_gate(header, expected, monkeypatch):
    seen = []

    class _Movers:
        async def get_portfolio_mover(self, user_id, tickers, *, links_web_only=False):
            seen.append(links_web_only)
            from app.schemas.widget import WidgetMoverPayload
            return WidgetMoverPayload(mode="portfolio", as_of="2026-10-14T18:00:00Z",
                                      market_session="regular", session_date=WED_ISO,
                                      session_label="Open", holdings_count=1)

    async def _group(_uid):
        return None

    async def _watchlist(_uid):
        return ["CRWV"]

    monkeypatch.setattr(widget, "get_active_group", _group)
    monkeypatch.setattr(widget, "_watchlist_tickers", _watchlist)
    monkeypatch.setattr(widget, "get_widget_movers_service", lambda: _Movers())
    token = cav._client_app_version.set(header)
    try:
        out = await widget.get_portfolio_mover(user={"id": "u1"}, _rate_limit=None)
    finally:
        cav._client_app_version.reset(token)
    assert out.mode == "portfolio"
    assert seen == [expected]
