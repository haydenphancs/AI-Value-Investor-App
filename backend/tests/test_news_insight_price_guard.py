"""Ticker Insights cards state no price — the write guard, the read net and the sweep.

TestFlight CRWV (Tue 2026-10-06): the chip read +6.3% while the card said "CoreWeave shares
experienced a 2.2% slip" and concluded "…countered by recent price declines". The text was
written THAT session, with "Price context: … up ~6%" in the prompt — the model copied an
older article's move anyway. Owner decisions 2026-10-09: no price moves and no price levels
on any card whose chip shows a live % (the market card keeps its index moves); market cap as
a size and valuation multiples stay.

The detector itself (`price_claims`) is pinned by tests/test_insight_price_claims.py; this
file pins what the service DOES with a hit:

* a point that talks about the price  → dropped, no extra call
* price in the headline / no point    → ONE full-card retry (no EARNINGS sentence unless timing)
* price in the conclusion             → ONE conclusion-only repair
* price still in headline/conclusion  → nothing written ("price_guard: …")
* stored cards                        → `_row_to_card` drops points / hides the card
* the market card                     → untouched
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

import app.services.updates_insight_sweeper as sweeper_mod
from app.services.earnings_window_service import EarningsStatus, get_earnings_window_service
from app.services.news_cache_service import MARKET_SCOPE
from app.services.insight_conclusion import price_claims
from app.services.news_insight_service import NewsInsightService, price_terms
from app.services.updates_insight_sweeper import InsightSweeper
from app.services.updates_materiality import ACTION_GENERATE, Decision
from _price_fakes import PriceFromFMPFake

NOW = datetime(2026, 10, 6, 18, 47, tzinfo=timezone.utc)   # Tue 14:47 ET, session open

CRWV_ARTICLES = [
    {"headline": "Truist backs CoreWeave on pricing power",
     "summary": "Truist Securities issued a bullish note citing pricing power.",
     "published_at": "2026-10-06T13:00:00+00:00"},
    {"headline": "CoreWeave backlog hits a record",
     "summary": "The neocloud business is booming; a $1.5 billion debt offering priced.",
     "published_at": "2026-10-06T12:00:00+00:00"},
    {"headline": "Nvidia's Vera Rubin systems go into production for Cognition",
     "summary": "CoreWeave shares slipped 2.2% on Monday.",
     "published_at": "2026-10-05T20:00:00+00:00"},
]
P1 = ("Truist Securities issued a bullish endorsement, citing CoreWeave's pricing power "
      "and co-location advantages.")
P2 = "CoreWeave's neocloud business is experiencing a boom with record backlog levels."
P3 = ("Despite positive news like Nvidia's Vera Rubin NVL72 systems going into production "
      "for Cognition, CoreWeave shares experienced a 2.2% slip.")
HEADLINE = "CoreWeave Sees Mixed Signals Amid AI Infrastructure Demand"
GOOD = ("Analyst backing and a booming backlog reinforce each other, leaving execution as the "
        "main open question.")
BAD_CONCLUSION = ("Strong demand and analyst support are countered by recent price declines, "
                  "leaving the near-term outlook for CoreWeave uncertain.")


def _card(points, conclusion=GOOD, headline=HEADLINE, sentiment="neutral"):
    return json.dumps({
        "headline": headline, "points": points,
        "sentiment": sentiment, "conclusion": conclusion,
    })


class _Gemini:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    async def generate_json(self, **kwargs):
        self.calls.append(kwargs)
        return {"text": self.answers.pop(0)}


class _Svc(NewsInsightService):
    def __init__(self, gemini):
        self.supabase = None
        self._cache = {}
        self._inflight = {}
        self.gemini = gemini
        self.stored = []

    def _store(self, scope, card, *args, **kwargs):
        self.stored.append(card)
        return True


async def _run(svc, scope="CRWV", articles=CRWV_ARTICLES, quote=None, **kw):
    return await svc.generate_and_store(
        scope=scope, corpus=articles, inputset_id="iid", price_band="Notable",
        trigger_reason="t", quote=quote if quote is not None else {"changePercentage": 6.3},
        market_active=True, now=NOW, **kw,
    )


# ── the write guard ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_crwv_point_is_dropped_without_another_call():
    gem = _Gemini(_card([P1, P2, P3]))
    svc = _Svc(gem)
    card = await _run(svc, company_name="CoreWeave, Inc.")
    assert card["bullets"] == [P1, P2, GOOD]
    assert len(gem.calls) == 1
    assert svc.stored and "2.2%" not in json.dumps(svc.stored[0])


@pytest.mark.asyncio
async def test_the_crwv_conclusion_takes_one_conclusion_repair():
    gem = _Gemini(_card([P1, P2, P3], conclusion=BAD_CONCLUSION), json.dumps({"conclusion": GOOD}))
    svc = _Svc(gem)
    card = await _run(svc, company_name="CoreWeave, Inc.")
    assert card["bullets"] == [P1, P2, GOOD]
    assert len(gem.calls) == 2
    repair = gem.calls[1]
    assert repair["usage_tag"] == "insight_conclusion_repair"
    assert "described the price" in repair["prompt"]
    assert "- PRICE. Never mention the price" in repair["prompt"]
    # The repair concludes over the points the reader will see — never the dropped one.
    assert "2.2%" not in repair["prompt"] and "slip" not in repair["prompt"]


@pytest.mark.asyncio
async def test_a_conclusion_that_keeps_the_price_after_the_repair_is_not_written():
    gem = _Gemini(
        _card([P1, P2], conclusion=BAD_CONCLUSION),
        json.dumps({"conclusion": BAD_CONCLUSION}),
    )
    svc = _Svc(gem)
    assert await _run(svc) is None
    assert svc.stored == []
    reason = svc.pop_failure_reason("CRWV")
    assert reason.startswith("price_guard: conclusion:"), reason


@pytest.mark.asyncio
async def test_a_price_headline_takes_one_full_retry_without_the_earnings_sentence():
    gem = _Gemini(
        _card([P1, P2], headline="CoreWeave shares slip despite a record backlog"),
        _card([P1, P2], headline="CoreWeave draws analyst backing as its backlog hits a record"),
    )
    svc = _Svc(gem)
    card = await _run(svc)
    assert card["headline"] == "CoreWeave draws analyst backing as its backlog hits a record"
    assert len(gem.calls) == 2
    retry = gem.calls[1]
    assert retry["usage_tag"] == "insight_card"
    assert "REPAIR." in retry["prompt"] and "described the price" in retry["prompt"]
    assert "HAPPENED" not in retry["prompt"].split("REPAIR.", 1)[1], (
        "a price-only retry must not claim an earnings report happened"
    )


@pytest.mark.asyncio
async def test_a_synonym_swap_in_the_retry_is_still_price_talk():
    """The retry note names the phrase; a model that swaps "slip" for "pulled back" must not
    win the rank and get written."""
    gem = _Gemini(
        _card([P1, P2], headline="CoreWeave shares slip despite a record backlog"),
        _card([P1, P2], headline="CoreWeave shares pulled back despite a record backlog"),
    )
    svc = _Svc(gem)
    assert await _run(svc) is None
    assert svc.stored == []
    assert svc.pop_failure_reason("CRWV").startswith("price_guard: headline:")


@pytest.mark.asyncio
async def test_a_card_with_only_price_points_takes_the_full_retry():
    gem = _Gemini(
        _card([P3, "CoreWeave stock has more than doubled since its IPO."]),
        _card([P1, P2]),
    )
    svc = _Svc(gem)
    card = await _run(svc)
    assert card["bullets"] == [P1, P2, GOOD]
    assert len(gem.calls) == 2 and gem.calls[1]["usage_tag"] == "insight_card"


@pytest.mark.asyncio
async def test_a_conclusion_citing_only_the_dropped_point_is_repaired():
    """The conclusion is checked against the points that SURVIVE: "$1.5 billion" came only
    from the dropped price point, so it is a new figure there and the repair runs."""
    dropped = "CoreWeave shares slid 2.2% after a $1.5 billion debt offering."
    conclusion = "The $1.5 billion offering tempers an otherwise strong backlog story."
    gem = _Gemini(_card([P1, P2, dropped], conclusion=conclusion), json.dumps({"conclusion": GOOD}))
    svc = _Svc(gem)
    card = await _run(svc)
    assert card["bullets"] == [P1, P2, GOOD]
    assert len(gem.calls) == 2
    assert gem.calls[1]["usage_tag"] == "insight_conclusion_repair"
    assert "debt offering" not in gem.calls[1]["prompt"]


@pytest.mark.asyncio
async def test_a_writable_draft_is_never_replaced_by_a_rejectable_retry():
    """Rank inversion 1: a timing retry that drops the stale wording but brings an unrelated
    story (a new figure AND a new event) used to win on the timing key and then be rejected.
    The rejection test is now the rank's first key, so the writable draft stays."""
    reported = EarningsStatus("reported", date(2026, 10, 6))
    first_headline = "Oracle set to report results as cloud demand grows"
    gem = _Gemini(
        _card(["Cloud revenue rose 28%.", "Oracle shares jumped 9% after hours."],
              conclusion="Cloud growth carries the quarter.", headline=first_headline),
        _card(["Cloud revenue rose 28%."],
              conclusion="A proposed $5,000 dividend could lift Oracle if Congress acts.",
              headline="Oracle's cloud revenue rose 28% in its fiscal first quarter"),
    )
    svc = _Svc(gem)
    # "$5,000" is in an article, so the retry is not a FABRICATION — only hard + novelty
    # (an unrelated story), the key the old rank placed AFTER timing. Without the rejection
    # as the rank's first key the retry would win (no stale timing) and then be rejected.
    articles = [{"headline": "Oracle Announces Fiscal Q1 Results",
                 "summary": "Cloud revenue rose 28%. Lawmakers floated a $5,000 dividend bill "
                            "in Congress.",
                 "published_at": "2026-10-06T17:00:00+00:00"}]
    card = await _run(svc, scope="ORCL", articles=articles, earnings=reported)
    assert card is not None, svc.pop_failure_reason("ORCL")
    assert len(gem.calls) == 2, "the timing retry must have run"
    assert card["headline"] == first_headline
    assert card["bullets"] == ["Cloud revenue rose 28%.", "Cloud growth carries the quarter."]


@pytest.mark.asyncio
async def test_a_refusal_caused_by_price_talk_is_reported_as_price_guard():
    """Once both price points are dropped, the conclusion that summed them up carries their
    figure and name — it reads as an "unrelated story". The cause is the price talk, and only
    a `price_guard` refusal alerts the watchers (review 2026-10-09)."""
    draft = _card(
        ["CoreWeave shares jumped 12% after Microsoft signed a $10 billion contract.",
         "The stock hit a record high on Monday."],
        conclusion="The $10 billion Microsoft deal reshapes CoreWeave's growth outlook.",
        headline="CoreWeave rallies on a new AI capacity contract",
    )
    gem = _Gemini(draft, draft)
    svc = _Svc(gem)
    assert await _run(svc, company_name="CoreWeave, Inc.") is None
    reason = svc.pop_failure_reason("CRWV")
    assert reason.startswith("price_guard:"), reason


@pytest.mark.asyncio
async def test_a_retry_that_only_needs_points_dropped_beats_a_price_headline():
    """Rank inversion 2: a flat count of price phrases ranked a draft with ONE headline phrase
    (fatal) above a retry with TWO point phrases (both droppable) — then rejected it."""
    gem = _Gemini(
        _card([P1, P2], headline="CoreWeave shares slip despite a record backlog"),
        _card([P3, "CoreWeave stock has more than doubled since its IPO.", P1],
              headline="CoreWeave draws analyst backing"),
    )
    svc = _Svc(gem)
    card = await _run(svc)
    assert card is not None, svc.pop_failure_reason("CRWV")
    assert card["headline"] == "CoreWeave draws analyst backing"
    assert card["bullets"] == [P1, GOOD]


@pytest.mark.asyncio
async def test_the_company_name_lets_the_guard_read_a_name_headline():
    """"CoreWeave Gains 4%" names no shares or stock — only the company name makes it CRWV's
    price. The sweeper passes the watchlist name; without one the symbol alone is known."""
    price_headline = "CoreWeave Gains 4% on a New Microsoft Deal"
    clean = "CoreWeave wins a new Microsoft deal"
    gem = _Gemini(_card([P1, P2], headline=price_headline), _card([P1, P2], headline=clean))
    svc = _Svc(gem)
    card = await _run(svc, company_name="CoreWeave, Inc.")
    assert card["headline"] == clean and len(gem.calls) == 2


@pytest.mark.asyncio
async def test_a_ticker_conclusion_citing_the_live_move_is_a_figure_from_nowhere():
    """The ticker prompt carries no price line, so the quote's % is NOT an allowed figure on a
    ticker card (only the market card's prompt states it). Worded as no price claim, "6.3%"
    must still be caught — as a fabricated figure — not waved through by the chip's own %."""
    leaked = ("Analyst backing and a 6.3% larger backlog reinforce each other, leaving "
              "execution as the main open question.")
    gem = _Gemini(_card([P1, P2], conclusion=leaked), json.dumps({"conclusion": leaked}))
    svc = _Svc(gem)
    assert await _run(svc, quote={"changePercentage": 6.3}) is None
    assert len(gem.calls) == 2, "the live % must send the conclusion to its repair"
    assert gem.calls[1]["usage_tag"] == "insight_conclusion_repair"
    assert svc.stored == []
    assert svc.pop_failure_reason("CRWV").startswith("conclusion_guard: figure 6.3%")


def test_price_terms_keep_names_and_drop_generic_heads():
    assert price_terms("CRWV", "CoreWeave, Inc.") == ["CRWV", "coreweave"]
    assert "first" not in price_terms("FSLR", "First Solar, Inc.")
    assert "first solar" in price_terms("FSLR", "First Solar, Inc.")
    assert "rocket lab" in price_terms("RKLB", "Rocket Lab USA, Inc.")
    assert "rocket" not in price_terms("RKLB", "Rocket Lab USA, Inc.")
    assert "target" in price_terms("TGT", "Target Corporation"), "a one-word name is the name"
    assert "meta" in price_terms("META", "Meta Platforms, Inc.")
    eth = price_terms("ETHUSD")
    assert {"ETHUSD", "ETH", "Ethereum", "Ether"} <= set(eth)
    assert price_terms("CRWV") == ["CRWV"]


@pytest.mark.asyncio
async def test_the_market_card_keeps_its_index_moves():
    gem = _Gemini(_card(
        ["The S&P 500 fell 1.2% as Treasury yields rose.", "Fed minutes are due Wednesday."],
        conclusion="Rising yields and the Fed calendar keep rates at the center of the market.",
        headline="Stocks slip as yields climb",
    ))
    svc = _Svc(gem)
    card = await _run(svc, scope=MARKET_SCOPE, quote={"changePercentage": -1.2})
    assert card["bullets"][0] == "The S&P 500 fell 1.2% as Treasury yields rose."
    assert card["headline"] == "Stocks slip as yields climb"
    assert len(gem.calls) == 1


@pytest.mark.asyncio
async def test_a_coin_card_drops_the_coins_move():
    gem = _Gemini(_card(
        ["Bitcoin fell 3% as ETF outflows mounted.", "Spot ETF flows reversed on Friday."],
        conclusion="Fund flows are setting the tone for Bitcoin this week.",
        headline="Bitcoin ETF flows turn",
    ))
    svc = _Svc(gem)
    articles = [{"headline": "Bitcoin ETF flows reverse", "summary": "Bitcoin fell 3%.",
                 "published_at": "2026-10-06T15:00:00+00:00"}]
    card = await _run(svc, scope="BTCUSD", articles=articles, quote={"changePercentage": -3.0})
    assert card["bullets"] == ["Spot ETF flows reversed on Friday.",
                               "Fund flows are setting the tone for Bitcoin this week."]


# ── the read net (stored cards) ─────────────────────────────────────────────────

def _row(scope="CRWV", headline=HEADLINE, bullets=(P1, P2, P3, GOOD), generated_hours=1):
    return {
        "scope": scope, "headline": headline, "bullets": list(bullets),
        "sentiment": "neutral", "article_count": 5, "prompt_version": 7,
        "generated_at": (NOW - timedelta(hours=generated_hours)).isoformat(),
    }


def _reader():
    return NewsInsightService.__new__(NewsInsightService)


def test_a_stored_price_point_is_dropped_at_read_time():
    card = _reader()._row_to_card(_row(), market_active=False)
    assert card["bullets"] == [P1, P2, GOOD]


def test_a_stored_price_conclusion_hides_the_card():
    """The exact TestFlight card: its conclusion cannot be edited, so it is not served."""
    row = _row(bullets=(P1, P2, P3, BAD_CONCLUSION))
    assert _reader()._row_to_card(row, market_active=False) is None


def test_a_stored_price_headline_hides_the_card():
    row = _row(headline="CoreWeave shares slip 2.2% despite a record backlog",
               bullets=(P1, P2, GOOD))
    assert _reader()._row_to_card(row, market_active=False) is None


def test_a_card_whose_only_point_is_price_talk_is_hidden():
    assert _reader()._row_to_card(_row(bullets=(P3, GOOD)), market_active=False) is None


def test_a_stored_coin_headline_with_its_move_is_hidden():
    row = _row(scope="BTCUSD", headline="Bitcoin fell 3% as ETF outflows mounted",
               bullets=("Spot ETF flows reversed on Friday.", "Flows set the tone."))
    assert _reader()._row_to_card(row, market_active=False) is None


def test_the_stored_market_card_is_untouched():
    flagged = "Nvidia shares fell 3% as yields rose."
    assert price_claims(flagged, price_terms("CRWV")), "anti-vacuity: a ticker card drops it"
    bullets = (flagged, "Fed minutes are due.", "Rates sit at the center of the market.")
    card = _reader()._row_to_card(
        _row(scope=MARKET_SCOPE, headline="Stocks slip as yields climb", bullets=bullets),
        market_active=False,
    )
    assert card["bullets"][0] == flagged
    twin = _reader()._row_to_card(
        _row(scope="CRWV", headline="Stocks slip as yields climb", bullets=bullets),
        market_active=False,
    )
    assert twin["bullets"] == list(bullets[1:])


def test_the_headline_fallback_skips_price_headlines():
    corpus = [
        {"headline": "CoreWeave Stock Slips 2.2% After Downgrade", "external_id": "a"},
        {"headline": "CoreWeave backlog hits a record", "external_id": "b"},
        {"headline": "Truist backs CoreWeave on pricing power", "external_id": "c"},
    ]
    card = _reader().build_fallback_card("CRWV", corpus, market_active=False)
    assert card["bullets"] == ["CoreWeave backlog hits a record",
                               "Truist backs CoreWeave on pricing power"]
    assert all("Slips" not in (s.get("title") or "") for s in card["sources"] or [])


def test_a_fallback_of_nothing_but_price_headlines_is_no_card():
    corpus = [{"headline": "CoreWeave Stock Slips 2.2% After Downgrade", "external_id": "a"}]
    assert _reader().build_fallback_card("CRWV", corpus, market_active=False) is None


def test_the_market_fallback_is_unfiltered():
    flagged = "Nvidia Stock Slips 2.2% After Downgrade"
    assert price_claims(flagged, price_terms("CRWV")), "anti-vacuity: a ticker fallback drops it"
    corpus = [{"headline": flagged, "external_id": "a"},
              {"headline": "Fed minutes due Wednesday", "external_id": "b"}]
    card = _reader().build_fallback_card(MARKET_SCOPE, corpus, market_active=False)
    assert card["bullets"][0] == flagged
    twin = _reader().build_fallback_card("CRWV", corpus, market_active=False)
    assert flagged not in twin["bullets"]


# ── the sweep: the name reaches the guard, a refusal still alerts ──────────────

class _Sweep(InsightSweeper):
    def __init__(self, reason):
        self.supabase = None
        self.fmp = object()
        self.price = PriceFromFMPFake(None)
        self.vol = self
        self.news = self
        self.insights = self
        self._enrich_day = None
        self._enrich_count = 0
        self.reason = reason
        self.generated = {}
        self.notified = []

    async def _universe(self):
        return ["CRWV"]

    def _company_names(self, scopes):
        return {"CRWV": "CoreWeave, Inc."}

    def _load_state(self, scopes):
        return {}

    def _record_skips(self, skips, now):
        pass

    async def get_sigmas_bulk(self, symbols):
        return {}

    def get_cached_bulk(self, scopes, limit):
        fresh = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        return {s: [{"external_id": f"{s}-1", "headline": "CoreWeave backlog hits a record",
                     "published_at": fresh}] for s in scopes}

    async def mark_verified_current(self, scopes, market_active):
        pass

    def _claim(self, scope, now, is_market_scope, earnings_window=False, report_day=False):
        return True

    def _consume_global_budget(self, now):
        return True

    async def generate_and_store(self, **kwargs):
        self.generated[kwargs["scope"]] = kwargs
        return None

    def pop_failure_reason(self, scope):
        return self.reason

    def _finish_claim(self, scope, now, decision, success, error=None):
        pass

    async def _notify_watchers(self, scope, decision, card, now, quote=None):
        self.notified.append((scope, card))


@pytest.fixture
def _sweep_env(monkeypatch):
    get_earnings_window_service().reset()

    async def _window(now, *, fmp):
        return frozenset()

    async def _statuses(now, *, fmp, symbols):
        return {}

    monkeypatch.setattr(sweeper_mod, "symbols_in_earnings_window", _window)
    monkeypatch.setattr(sweeper_mod, "earnings_statuses_for", _statuses)
    monkeypatch.setattr(sweeper_mod, "is_market_active", lambda: True)
    monkeypatch.setattr(sweeper_mod, "decide",
                        lambda **kw: Decision(action=ACTION_GENERATE, reason="band_change"))
    yield
    get_earnings_window_service().reset()


@pytest.mark.asyncio
async def test_the_sweep_hands_the_watchlist_name_to_the_guard(_sweep_env):
    sweep = _Sweep("price_guard: headline: \"shares slip\"")
    await sweep.run_sweep(refresh_news=False)
    assert sweep.generated["CRWV"]["company_name"] == "CoreWeave, Inc."


@pytest.mark.asyncio
async def test_a_price_guard_refusal_still_alerts_the_watchers(_sweep_env):
    """A big mover's coverage is often nothing but price talk — the day the guard refuses
    the card is the day the alert matters, and the alert never reads the card."""
    sweep = _Sweep("price_guard: headline: \"shares slip\"")
    await sweep.run_sweep(refresh_news=False)
    assert sweep.notified == [("CRWV", None)]


@pytest.mark.asyncio
async def test_any_other_refusal_does_not_alert(_sweep_env):
    sweep = _Sweep("conclusion_guard: figure $5,000")
    await sweep.run_sweep(refresh_news=False)
    assert sweep.notified == []


# ── a refused day is not a quiet day (widget + Ask Cay move attribution) ─────────

class _StateTable:
    def __init__(self, rows, boom=False):
        self.rows, self.boom, self.asked = rows, boom, None

    def table(self, name):
        assert name == "updates_insight_state"
        return self

    def select(self, cols):
        return self

    def in_(self, col, scopes):
        self.asked = list(scopes)
        return self

    def execute(self):
        if self.boom:
            raise RuntimeError("supabase down")
        return type("R", (), {"data": self.rows})()


def _state_reader(rows, boom=False):
    svc = NewsInsightService.__new__(NewsInsightService)
    svc.supabase = _StateTable(rows, boom)
    return svc


@pytest.mark.asyncio
async def test_a_card_older_than_a_later_failed_day_is_flagged():
    cards = {
        "CRWV": {"generated_at": "2026-10-05T19:30:00Z"},   # Mon card
        "ORCL": {"generated_at": "2026-10-06T14:00:00Z"},   # Tue card, failure later Tue
        "NVDA": {"generated_at": "2026-10-06T14:00:00Z"},   # failure BEFORE the card
        "AAPL": None,
    }
    svc = _state_reader([
        {"scope": "CRWV", "last_failure_at": "2026-10-06T18:47:00Z"},
        {"scope": "ORCL", "last_failure_at": "2026-10-06T18:47:00Z"},
        {"scope": "NVDA", "last_failure_at": "2026-10-05T18:00:00Z"},
    ])
    assert await svc.scopes_failed_after_their_card(cards) == {"CRWV"}
    assert sorted(svc.supabase.asked) == ["CRWV", "NVDA", "ORCL"], "never asks for a missing card"


@pytest.mark.asyncio
async def test_a_failed_state_read_changes_nothing():
    svc = _state_reader([], boom=True)
    assert await svc.scopes_failed_after_their_card(
        {"CRWV": {"generated_at": "2026-10-05T19:30:00Z"}}) == set()


@pytest.mark.asyncio
async def test_the_widget_reads_a_card_behind_a_failure_as_unchecked(monkeypatch):
    """The widget's own reader: a card from Monday + a refusal on Tuesday must never become
    "No company news today." — `_classified_today_news(None)` is the unchecked state."""
    from app.services import widget_movers_service as wm

    quotes = {"CRWV": {"symbol": "CRWV", "price": 120.0, "changePercentage": 12.3},
              "SPY": {"symbol": "SPY", "price": 651.0, "changePercentage": 0.3}}
    svc = wm.WidgetMoversService()

    async def _quotes(symbols):
        return {s: quotes[s] for s in symbols if s in quotes}

    class _Vol:
        async def get_sigmas_bulk(self, symbols):
            return {}

    class _News:
        async def get_cards(self, scopes):
            return {s: {"headline": "Older news", "generated_at": "2026-10-05T19:30:00Z"}
                    for s in scopes}

        async def scopes_failed_after_their_card(self, cards):
            return {"CRWV"} & set(cards)

    monkeypatch.setattr(svc, "_quotes", _quotes)
    monkeypatch.setattr(wm, "get_volatility_cache_service", lambda: _Vol())
    monkeypatch.setattr(wm, "get_news_insight_service", lambda: _News())
    _ranked, cards, ok, _idx = await svc._rank_and_read(
        ["CRWV"], basis="abs_change", exclude_band=False,
    )
    assert ok is True
    assert "CRWV" in cards and cards["CRWV"] is None
    assert wm._classified_today_news(cards["CRWV"], "2026-10-06") == ([], False, False)
