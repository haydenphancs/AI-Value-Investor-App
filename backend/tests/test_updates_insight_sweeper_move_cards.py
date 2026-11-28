"""The sweeper's move-card wiring (2026-11-27).

CRWV rose ~22% on Mon 2026-11-23 and PLUG reversed from −10% to +11% on Wed 2026-11-25; their
Insights cards explained neither move. The writer (`news_insight_service`), the gate
(`updates_materiality`) and the jump search each grew a move-card half; these tests drive the
REAL `run_sweep` through a stub with no network clients and pin the sweeper's half:

* the jump reserve is rebuilt from the durable move tag after a restart;
* move mode = live jumpers, reversals, and held cards whose live move is still ≥ Notable —
  never an MWCB day or a report day after the close; coins without market context or search;
* the move corpus is built once and is what the gate, the search's pre-check and the writer
  all see, while enrichment keeps the full window;
* a market-wide move is passed only when the code confirms it, and the market items are
  selected once per sweep;
* the writer's tag reaches the state row first; a kept attempt is a failure that consumes its
  evidence, and a kept cause is re-stamped current; a still-searched no-cause card is never
  called current;
* a ≥10% floor jumper gets the reserve, move mode and its alert.

Every headline here is hand-written; none is a production card or a web page.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import logging
from datetime import datetime, timedelta, timezone

import pytest

import app.services.updates_insight_sweeper as mod
from app.services.earnings_window_service import get_earnings_window_service
from app.services.insight_move import MOVE_KEPT, MoveContext, MoveOutcome, MoveTag, held_move
from app.services.news_cache_service import MARKET_SCOPE, BulkCorpus
from app.services.news_insight_service import select_move_corpus, select_recent_corpus
from app.services.updates_insight_sweeper import InsightSweeper
from app.services.updates_materiality import (
    ACTION_GENERATE,
    ACTION_SKIP,
    JUMP_FLOOR,
    REASON_MOVE_HELD,
    REASON_NO_CAUSE_WAITING,
    Decision,
)
from app.utils.market_hours import SESSION_AFTERHOURS, SESSION_REGULAR, session_trading_date
from _price_fakes import PriceFromFMPFake

# Mon 2026-11-23 10:30 ET (regular session) — the CRWV replay.
MON_1030 = datetime(2026, 11, 23, 15, 30, tzinfo=timezone.utc)
# Wed 2026-11-25 13:30 ET — the PLUG afternoon, the index about flat.
WED_1330 = datetime(2026, 11, 25, 18, 30, tzinfo=timezone.utc)
# Mon 2026-11-23 16:30 ET — after the close.
MON_1630 = datetime(2026, 11, 23, 21, 30, tzinfo=timezone.utc)


def _freeze(monkeypatch, at):
    """The sweeper reads its clock as `datetime.now(timezone.utc)`; pin it, so a same-ET-day
    tag never depends on when the suite runs."""
    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return at if tz is None else at.astimezone(tz)

    monkeypatch.setattr(mod, "datetime", _Clock)
    return at


@pytest.fixture(autouse=True)
def _fresh_earnings_singleton():
    get_earnings_window_service().reset()
    yield
    get_earnings_window_service().reset()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    async def _window(now, *, fmp):
        return frozenset()

    async def _statuses(now, *, fmp, symbols):
        return {}

    monkeypatch.setattr(mod, "symbols_in_earnings_window", _window)
    monkeypatch.setattr(mod, "earnings_statuses_for", _statuses)
    monkeypatch.setattr(mod, "is_market_active", lambda: True)
    _freeze(monkeypatch, MON_1030)


def _row(symbol, pct, *, at=MON_1030, cap=4e11, stamped=True, **extra):
    row = {"symbol": symbol, "price": 100.0, "changePercentage": pct, "marketCap": cap, **extra}
    if stamped:
        row["changeSession"] = session_trading_date(at).isoformat()
    return row


def _news(scope, eid, headline, hours_ago, *, at=MON_1030):
    return {"external_id": f"{scope}-{eid}", "headline": headline,
            "published_at": (at - timedelta(hours=hours_ago)).isoformat()}


def _default_rows(scope, at):
    """One news-bearing, subject-led story an hour old (it survives the move corpus)."""
    return [_news(scope, "1", f"{scope} signs a multiyear cloud contract with a hospital network",
                  1, at=at)]


def _state(tag, *, at=MON_1030, minutes_ago=60, successes=2, **extra):
    """A state row holding a card written `minutes_ago`; ``tag`` leads its reason (None = an
    ordinary card)."""
    reason = f"{tag} + new_articles (3)" if tag else "new_articles (3)"
    return {
        "regen_day": session_trading_date(at).isoformat(),
        "regen_count_today": successes, "attempts_today": successes,
        "last_inputset_id": "an-older-fingerprint",
        "last_generated_at": (at - timedelta(minutes=minutes_ago)).isoformat(),
        "last_trigger_reason": reason, "last_price_band": None,
        **extra,
    }


class _QuoteFMP:
    def __init__(self, rows):
        self.rows = rows

    async def get_batch_quotes_bulk(self, symbols):
        return [r for r in self.rows if r["symbol"] in symbols]


class _FakeWeb:
    def __init__(self, *, enabled=True, exhausted=False, pages=None, refreshed_at=None):
        self._enabled = enabled
        self.exhausted = exhausted
        self.pages = pages or {}
        self.refreshed_at = refreshed_at
        self.asked = []
        self.outcomes = []
        self.exhaust_asked = []
        self.closed = False

    def enabled(self):
        return self._enabled

    async def refresh(self, candidates, now):
        self.asked.append(list(candidates))
        return {"ok": len(candidates)}

    def context(self, scope, now, *, direction):
        pages = self.pages.get(scope, ())
        return (pages, self.refreshed_at) if pages else ((), None)

    def note_move_outcome(self, scope, now, outcome):
        self.outcomes.append((scope, outcome))

    def episode_exhausted(self, scope, now, *, direction):
        self.exhaust_asked.append((scope, direction))
        return self.exhausted

    async def aclose(self):
        self.closed = True


class _Stub(InsightSweeper):
    def __init__(self, rows, universe, *, states=None, bulk=None, sigmas=None, at=MON_1030,
                 card=None, failure=None, outcome=None, web=None):
        self.supabase = None
        self.fmp = object()
        self.price = PriceFromFMPFake(_QuoteFMP(rows))
        self.vol = self
        self.news = self
        self.insights = self
        self._enrich_day = None
        self._enrich_count = 0
        self._universe_value = list(universe)
        self._states = states or {}
        self._bulk = bulk or {}
        self._sigmas = sigmas or {}
        self._at = at
        self._card = card if card is not None else (lambda scope, kw: {"headline": "h"})
        self._failure = failure or {}
        self._outcome = outcome or {}
        if web is not None:
            self.web = web
        self.claims = {}
        self.generated = {}
        self.finished = {}
        self.notified = []
        self.verified = []
        self.bulk_calls = 0
        self.enrich_corpora = None

    async def _universe(self):
        return list(self._universe_value)

    def _company_names(self, scopes):
        return mod._MarkedRead(complete=True)

    def _load_state(self, scopes):
        return mod._MarkedRead(
            {s: r for s, r in self._states.items() if s in scopes}, complete=True,
        )

    def _record_skips(self, skips, now):
        pass

    async def get_sigmas_bulk(self, symbols):
        return dict(self._sigmas)

    def get_cached_bulk(self, scopes, limit):
        self.bulk_calls += 1
        return BulkCorpus(
            {s: list(self._bulk.get(s, _default_rows(s, self._at))) for s in scopes},
            complete=True,
        )

    async def mark_verified_current(self, scopes, market_active):
        self.verified.append(list(scopes))

    async def touch(self, scope, market_active):
        pass

    def _mark_cycle_touched(self, scopes, now):
        pass

    def _claim(self, scope, now, is_market_scope, earnings_window=False, report_day=False,
               jumping=False):
        self.claims[scope] = jumping
        return True

    def _consume_global_budget(self, now):
        return True

    async def generate_and_store(self, **kwargs):
        self.generated[kwargs["scope"]] = kwargs
        return self._card(kwargs["scope"], kwargs)

    def pop_failure_reason(self, scope):
        return self._failure.get(scope, "conclusion_guard: figure $5,000")

    def pop_move_outcome(self, scope):
        return self._outcome.get(scope)

    def _finish_claim(self, scope, now, decision, success, error=None):
        self.finished[scope] = (decision, success, error)

    async def _notify_watchers(self, scope, decision, card, now, quote=None):
        self.notified.append((scope, decision.price_band, card))

    async def _refresh_news(self, scopes):
        pass

    async def _enrich_windows(self, corpora, scopes, now):
        self.enrich_corpora = corpora
        return 0, 0

    @property
    def all_verified(self):
        return {s for batch in self.verified for s in batch}


def _record_decide(monkeypatch, verdict=None):
    """The gate replaced by a recorder; ``verdict(scope, kw)`` → the Decision (GENERATE by
    default). Records the LAST call per scope (the main loop runs after the pre-check)."""
    seen = {}

    def _decide(**kw):
        seen[kw["scope"]] = kw
        if verdict is not None:
            return verdict(kw["scope"], kw)
        return Decision(action=ACTION_GENERATE, reason="new_articles (1)", score=1.0,
                        price_band="Extreme", inputset_id="fp-1")

    monkeypatch.setattr(mod, "decide", _decide)
    return seen


def _spy_decide(monkeypatch):
    """The REAL gate, every call recorded per scope (pre-check first, then the main loop)."""
    real = mod.decide
    calls = {}

    def _spy(**kw):
        d = real(**kw)
        calls.setdefault(kw["scope"], []).append((kw, d))
        return d

    monkeypatch.setattr(mod, "decide", _spy)
    return calls


# ── the reserve survives a restart ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_restart_rebuilds_the_jump_reserve_from_todays_move_tag(monkeypatch):
    """The latch is process-local: after a restart a faded jumper fell back to its plain cap.
    A move card is only ever written for a scope that jumped today, and its tag is durable."""
    seen = _record_decide(monkeypatch)
    stub = _Stub(
        [_row("ORCL", 0.4), _row("AAPL", 0.4), _row("MSFT", 0.4)], ["ORCL", "AAPL", "MSFT"],
        states={
            "ORCL": _state("move:up:company"),
            "AAPL": _state("move:up:company", minutes_ago=24 * 60),   # yesterday's tag
            "MSFT": _state(None),                                     # an ordinary card
        },
    )
    assert "_jump_reserve_latch" not in stub.__dict__          # a fresh process
    await stub.run_sweep(refresh_news=False)
    assert seen["ORCL"]["jumping"] is True and stub.claims["ORCL"] is True
    assert seen["ORCL"]["held_move"] == MoveTag(kind="company", direction="up")
    for scope in ("AAPL", "MSFT"):
        assert seen[scope]["jumping"] is False and stub.claims[scope] is False
        assert seen[scope]["held_move"] is None


def test_the_reserve_latch_takes_held_scopes_but_never_the_market():
    stub = _Stub([], [])
    assert stub._reserve_jumpers({}, MON_1030, held=["ORCL", MARKET_SCOPE]) == {"ORCL"}
    assert stub._reserve_jumpers({}, MON_1030 + timedelta(days=1)) == frozenset()


# ── move mode ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_faded_jumper_with_a_move_card_stays_in_move_mode_only_while_notable(monkeypatch):
    seen = _record_decide(monkeypatch)
    states = {"ORCL": _state("move:up:company")}
    # σ 3%: +4% is Notable (z 1.3) — no jump, but still a material move in the card's direction.
    stub = _Stub([_row("ORCL", 4.0)], ["ORCL"], states=states, sigmas={"ORCL": 0.03})
    await stub.run_sweep(refresh_news=False)
    move = stub.generated["ORCL"]["move"]
    assert isinstance(move, MoveContext)
    assert (move.direction, move.basis) == ("up", "held")
    assert move.held == MoveTag(kind="company", direction="up")
    assert stub.generated["ORCL"]["jumping"] is True
    assert seen["ORCL"]["held_move"] == MoveTag(kind="company", direction="up")

    # +1% is Typical: the card stays held, but no regeneration of it is in move mode.
    stub = _Stub([_row("ORCL", 1.0)], ["ORCL"], states=states, sigmas={"ORCL": 0.03})
    await stub.run_sweep(refresh_news=False)
    assert stub.generated["ORCL"]["move"] is None
    assert stub.generated["ORCL"]["jumping"] is False
    assert seen["ORCL"]["held_move"] == MoveTag(kind="company", direction="up")


@pytest.mark.asyncio
async def test_a_reversed_latched_jumper_is_searched_and_regenerated_in_the_new_direction(
    monkeypatch,
):
    """PLUG 2026-11-25: down 10% at the open (a held down card), up 5.5% at noon — Notable
    the other way. The real gate forces the rewrite; the search treats it as a flip."""
    _freeze(monkeypatch, WED_1330)
    calls = _spy_decide(monkeypatch)
    web = _FakeWeb()
    stub = _Stub(
        [_row("PLUG", 5.5, at=WED_1330)], ["PLUG"], at=WED_1330, web=web,
        states={"PLUG": _state("move:down:none", at=WED_1330)}, sigmas={"PLUG": 0.03},
    )
    await stub.run_sweep(refresh_news=False)

    cand = web.asked[0][0]
    assert cand.scope == "PLUG" and cand.live is False and cand.held_kind is None
    kw, verdict = calls["PLUG"][-1]
    assert kw["held_move"] == MoveTag(kind="none", direction="down")
    assert kw["jumping"] is True                     # rebuilt from the tag
    assert verdict.action == ACTION_GENERATE and "reversal down->up" in verdict.reason
    move = stub.generated["PLUG"]["move"]
    assert (move.direction, move.basis) == ("up", "reversal")
    assert move.held == MoveTag(kind="none", direction="down")


@pytest.mark.asyncio
async def test_candidates_carry_the_held_kind_only_for_the_move_made_now(monkeypatch):
    calls = _spy_decide(monkeypatch)
    web = _FakeWeb()
    stub = _Stub(
        [_row("CRWV", 22.0), _row("NVTS", 15.0)], ["CRWV", "NVTS"], web=web,
        states={"CRWV": _state("move:up:company"), "NVTS": _state("move:down:market")},
        sigmas={"CRWV": 0.05, "NVTS": 0.05},
    )
    await stub.run_sweep(refresh_news=False)
    cands = {c.scope: c for c in web.asked[0]}
    assert cands["CRWV"].live is True and cands["CRWV"].held_kind == "company"
    # NVTS's market card explained a FALL: it is no cause for today's rise.
    assert cands["NVTS"].live is True and cands["NVTS"].held_kind is None
    assert stub.generated["NVTS"]["move"].basis == "sigma"       # a live jumper's basis
    assert calls["NVTS"][-1][1].action == ACTION_GENERATE
    assert "reversal up" not in calls["NVTS"][-1][1].reason
    assert "reversal down->up" in calls["NVTS"][-1][1].reason


@pytest.mark.asyncio
async def test_a_floor_jumper_gets_the_reserve_move_mode_and_its_alert(monkeypatch):
    """Owner: "Also count 10% or more". +10.5% on a σ-12% name is Typical by σ; the gate's
    band says so too, and the alert gate would refuse it without the live jump band."""
    seen = _record_decide(
        monkeypatch,
        lambda s, kw: Decision(action=ACTION_GENERATE, reason="new_articles (1)", score=25.0,
                               price_band="Typical", inputset_id="fp-1"),
    )
    stub = _Stub([_row("FLRX", 10.5)], ["FLRX"], sigmas={"FLRX": 0.12})
    await stub.run_sweep(refresh_news=False)
    assert seen["FLRX"]["jumping"] is True and stub.claims["FLRX"] is True
    assert stub.generated["FLRX"]["move"].basis == "floor"
    assert stub.notified == [("FLRX", JUMP_FLOOR, {"headline": "h"})]
    assert JUMP_FLOOR in mod._CATALYST_TIERS

    # Capped: the decoupled alert (6b) still carries the floor band.
    _record_decide(monkeypatch, lambda s, kw: Decision(action=ACTION_SKIP, reason="daily_cap"))
    stub = _Stub([_row("FLRX", 10.5)], ["FLRX"], sigmas={"FLRX": 0.12})
    await stub.run_sweep(refresh_news=False)
    assert stub.notified == [("FLRX", JUMP_FLOOR, None)]


@pytest.mark.asyncio
async def test_an_mwcb_day_writes_no_move_card_and_searches_nothing(monkeypatch, caplog):
    calls = _spy_decide(monkeypatch)
    web = _FakeWeb()
    stub = _Stub(
        [_row("ORCL", 12.0), _row("SPY", -8.0)], ["ORCL"], web=web,
        states={"ORCL": _state("move:down:company")}, sigmas={"ORCL": 0.02},
    )
    caplog.set_level(logging.INFO, logger=mod.__name__)
    await stub.run_sweep(refresh_news=False)
    assert web.asked == []
    assert calls["ORCL"][-1][1].reason == "mwcb_market_only"
    assert stub.generated == {}
    # Not even planned: the held down card under a +12% move is no reversal on an MWCB day
    # (the gate's own MWCB skip is the second line, not the only one).
    line = [r.getMessage() for r in caplog.records
            if r.getMessage().startswith("Insight sweep (")][-1]
    assert "held=1 move_mode=0 reversals=0" in line


@pytest.mark.asyncio
async def test_a_reversal_with_no_news_may_be_written_from_its_new_pages(monkeypatch):
    """A latched jumper that turned the other way, with no story in its move window: its
    search (as a reversal) found pages for the NEW direction, so the gate may write it from
    them alone, like a live jumper (`web_only_ok`)."""
    _freeze(monkeypatch, WED_1330)
    calls = _spy_decide(monkeypatch)
    web = _FakeWeb(pages={"PLUG": ("p1", "p2")},
                   refreshed_at=WED_1330 - timedelta(minutes=1))
    stub = _Stub(
        [_row("PLUG", 5.5, at=WED_1330)], ["PLUG"], at=WED_1330, web=web,
        bulk={"PLUG": []}, states={"PLUG": _state("move:down:none", at=WED_1330)},
        sigmas={"PLUG": 0.03},
    )
    await stub.run_sweep(refresh_news=False)
    kw, verdict = calls["PLUG"][-1]
    assert kw["web_only_ok"] is True and kw["corpus"] == []
    assert verdict.action == ACTION_GENERATE
    assert "reversal down->up" in verdict.reason and "web_only" in verdict.reason
    assert stub.generated["PLUG"]["move"].direction == "up"


@pytest.mark.asyncio
async def test_a_fund_jumper_gets_no_market_context_on_an_index_day(monkeypatch):
    """A fund moves on the macro story the market card carries; "moves with the broader
    market" is for a company. Market confirmation reads searchable equities only."""
    _record_decide(monkeypatch)
    stub = _Stub([_row("SOXQ", 12.0, isEtf=True), _row("SPY", 4.7)], ["SOXQ"],
                 sigmas={"SOXQ": 0.02})
    await stub.run_sweep(refresh_news=False)
    move = stub.generated["SOXQ"]["move"]
    assert move is not None and move.market is None


@pytest.mark.asyncio
async def test_a_report_day_after_the_close_releases_a_card_whose_landing_is_unplaced(monkeypatch):
    """Results in, but this process never saw them land (a restart after the release): the
    landing cannot be placed before the card, so the card is released and the earnings card
    is free to replace it — as before move cards."""
    _freeze(monkeypatch, MON_1630)
    monkeypatch.setattr(mod, "earnings_gate_inputs", lambda status, today: (True, False, None))
    seen = _record_decide(monkeypatch)
    stub = _Stub(
        [_row("ORCL", 12.0, at=MON_1630)], ["ORCL"], at=MON_1630,
        states={"ORCL": _state("move:up:company", at=MON_1630)}, sigmas={"ORCL": 0.02},
    )
    await stub.run_sweep(refresh_news=False)
    assert seen["ORCL"]["held_move"] is None             # never held against the earnings card
    assert seen["ORCL"]["jumping"] is True               # the reserve still applies
    assert stub.generated["ORCL"]["move"] is None
    assert stub.generated["ORCL"]["jumping"] is False    # `jumping` alone would derive a move


@pytest.mark.asyncio
async def test_coins_take_move_mode_without_market_context_or_a_search(monkeypatch):
    # The crypto pass: no index leg, no search.
    monkeypatch.setattr(mod, "is_market_active", lambda: False)
    _record_decide(monkeypatch)
    web = _FakeWeb()
    stub = _Stub([_row("ETHUSD", 12.0)], ["ETHUSD"], web=web)
    await stub.run_sweep(refresh_news=True, crypto_only=True)
    move = stub.generated["ETHUSD"]["move"]
    assert move.is_crypto is True and move.market is None and move.basis == "band"
    assert web.asked == []

    # The equity pass on a big index day: the coin still gets no market context.
    monkeypatch.setattr(mod, "is_market_active", lambda: True)
    stub = _Stub([_row("ETHUSD", 12.0), _row("SPY", 4.7)], ["ETHUSD"], web=_FakeWeb())
    await stub.run_sweep(refresh_news=False)
    assert stub.generated["ETHUSD"]["move"].market is None
    assert all(not batch for batch in stub.web.asked)


# ── market confirmation ─────────────────────────────────────────────────────────────

_MARKET_ROWS = [
    {"external_id": "m1", "headline": "Federal Reserve officials signal patience on interest rates",
     "published_at": (MON_1030 - timedelta(hours=2)).isoformat()},
    {"external_id": "m2", "headline": "Large chip designer reappoints its founder as chief executive",
     "published_at": (MON_1030 - timedelta(hours=3)).isoformat()},
    {"external_id": "m3", "headline": "Wall Street closes higher",
     "published_at": (MON_1030 - timedelta(hours=1)).isoformat()},
]


@pytest.mark.asyncio
async def test_the_market_context_needs_the_codes_confirmation(monkeypatch):
    """CRWV 2026-11-23: +22% on an S&P +4.7% day (4.7x the index) — the index basis, with the
    market scope's own general-news rows as the citable items (tape-only rows dropped)."""
    _record_decide(monkeypatch)
    stub = _Stub(
        [_row("CRWV", 22.0), _row("SPY", 4.7)], [MARKET_SCOPE, "CRWV"],
        bulk={MARKET_SCOPE: _MARKET_ROWS}, sigmas={"CRWV": 0.05},
    )
    await stub.run_sweep(refresh_news=False)
    market = stub.generated["CRWV"]["move"].market
    assert market is not None and market.basis == "index" and market.card_headline is None
    assert [r["external_id"] for r in market.items] == ["m1", "m2"]
    assert stub.generated[MARKET_SCOPE]["move"] is None        # the market card never moves

    # An UNSTAMPED index row cannot say which session it describes: no index basis.
    stub = _Stub(
        [_row("CRWV", 22.0), _row("SPY", 4.7, stamped=False)], [MARKET_SCOPE, "CRWV"],
        bulk={MARKET_SCOPE: _MARKET_ROWS}, sigmas={"CRWV": 0.05},
    )
    await stub.run_sweep(refresh_news=False)
    assert stub.generated["CRWV"]["move"].market is None


@pytest.mark.asyncio
async def test_no_market_context_on_the_wednesday_afternoon_rebound(monkeypatch):
    """PLUG 2026-11-25 afternoon: +11% with the index about flat and no group move."""
    _freeze(monkeypatch, WED_1330)
    _record_decide(monkeypatch)
    stub = _Stub(
        [_row("PLUG", 11.0, at=WED_1330), _row("SPY", 0.1, at=WED_1330)], ["PLUG"],
        at=WED_1330, sigmas={"PLUG": 0.047},
    )
    await stub.run_sweep(refresh_news=False)
    move = stub.generated["PLUG"]["move"]
    assert move.direction == "up" and move.market is None


@pytest.mark.asyncio
async def test_a_group_move_on_a_flat_index_is_the_breadth_basis(monkeypatch):
    _record_decide(monkeypatch)
    names = [f"G{i}" for i in range(7)]
    rows = [_row(s, 12.0 + i) for i, s in enumerate(names)] + [_row("SPY", 0.2)]
    stub = _Stub(rows, names, sigmas={s: 0.03 for s in names})
    await stub.run_sweep(refresh_news=False)
    for s in names:
        assert stub.generated[s]["move"].market.basis == "breadth"


@pytest.mark.asyncio
async def test_the_market_items_are_selected_once_per_sweep(monkeypatch):
    _record_decide(monkeypatch)
    real = mod.select_market_items
    calls = []

    def _count(rows, now):
        calls.append(list(rows))
        return real(rows, now)

    monkeypatch.setattr(mod, "select_market_items", _count)
    names = ["CRWV", "APLD", "IREN"]
    rows = [_row(s, 20.0) for s in names] + [_row("SPY", 4.7)]
    stub = _Stub(rows, [MARKET_SCOPE, *names], bulk={MARKET_SCOPE: _MARKET_ROWS},
                 sigmas={s: 0.05 for s in names})
    await stub.run_sweep(refresh_news=False)
    assert len(calls) == 1 and stub.bulk_calls == 1
    assert all(stub.generated[s]["move"].market.basis == "index" for s in names)

    # Nothing confirmed: never selected at all.
    calls.clear()
    rows = [_row(s, 20.0) for s in names] + [_row("SPY", 0.1)]
    stub = _Stub(rows, [MARKET_SCOPE, *names], bulk={MARKET_SCOPE: _MARKET_ROWS},
                 sigmas={s: 0.05 for s in names})
    await stub.run_sweep(refresh_news=False)
    assert calls == []


# ── the same corpus for the gate, the search and the writer ─────────────────────────


@pytest.mark.asyncio
async def test_the_gate_the_search_and_the_writer_see_one_move_corpus(monkeypatch):
    calls = _spy_decide(monkeypatch)
    web = _FakeWeb()
    raw = [
        _news("ORCL", "a", "ORCL signs a multiyear cloud contract with a hospital network", 1),
        _news("ORCL", "b", "ORCL shares jump in early trading", 0.5),      # a price recap
        _news("ORCL", "d", "ORCL names a new head of its database unit", 30),
    ]
    stub = _Stub([_row("ORCL", 12.0)], ["ORCL"], web=web, bulk={"ORCL": raw},
                 sigmas={"ORCL": 0.02})
    await stub.run_sweep(refresh_news=True)

    expected, recaps = select_move_corpus(raw, MON_1030, "ORCL", None)
    assert [r["external_id"] for r in expected] == ["ORCL-a", "ORCL-d"]
    pre_kw, _ = calls["ORCL"][0]
    gate_kw, _ = calls["ORCL"][-1]
    assert pre_kw["corpus"] == expected                       # the search's pre-check
    assert gate_kw["corpus"] == expected                      # the gate's fingerprint
    assert stub.generated["ORCL"]["corpus"] == expected       # the writer
    move = stub.generated["ORCL"]["move"]
    assert [r["external_id"] for r in move.recaps] == ["ORCL-b"]
    # Enrichment keeps the full card window, recap included.
    window = select_recent_corpus(raw, MON_1030, scope="ORCL")[0]
    assert stub.enrich_corpora["ORCL"] == window
    assert any(r["external_id"] == "ORCL-b" for r in window)


@pytest.mark.asyncio
async def test_a_held_scope_gets_its_newest_news_and_the_search_verdict(monkeypatch):
    seen = _record_decide(
        monkeypatch, lambda s, kw: Decision(action=ACTION_SKIP, reason=REASON_NO_CAUSE_WAITING),
    )
    web = _FakeWeb(exhausted=False)
    raw = [
        _news("ORCL", "a", "ORCL signs a multiyear cloud contract with a hospital network", 2),
        _news("ORCL", "d", "ORCL names a new head of its database unit", 0.5),
    ]
    stub = _Stub([_row("ORCL", 12.0), _row("AAPL", 12.0)], ["ORCL", "AAPL"], web=web,
                 bulk={"ORCL": raw}, states={"ORCL": _state("move:up:none")},
                 sigmas={"ORCL": 0.02, "AAPL": 0.02})
    await stub.run_sweep(refresh_news=False)
    kw = seen["ORCL"]
    assert kw["fresh_news_at"] == MON_1030 - timedelta(hours=0.5)
    assert kw["move_search_exhausted"] is False
    assert ("ORCL", "up") in web.exhaust_asked
    # No held card: neither input is passed.
    assert seen["AAPL"]["held_move"] is None and seen["AAPL"]["fresh_news_at"] is None
    assert seen["AAPL"]["move_search_exhausted"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("exhausted, reason, current", [
    (False, REASON_NO_CAUSE_WAITING, False),
    (True, REASON_MOVE_HELD, True),
])
async def test_a_held_no_cause_card_is_current_only_once_its_search_is_over(
    monkeypatch, exhausted, reason, current,
):
    """The real gate: ORCL still +12% with today's no-cause card, no new evidence."""
    calls = _spy_decide(monkeypatch)
    web = _FakeWeb(exhausted=exhausted)
    raw = [_news("ORCL", "a", "ORCL signs a multiyear cloud contract with a hospital network", 2)]
    stub = _Stub([_row("ORCL", 12.0)], ["ORCL"], web=web, bulk={"ORCL": raw},
                 states={"ORCL": _state("move:up:none")}, sigmas={"ORCL": 0.02})
    await stub.run_sweep(refresh_news=False)
    assert calls["ORCL"][-1][1].reason == reason
    assert ("ORCL" in stub.all_verified) is current
    assert stub.generated == {}


# ── the state row and the verified stamp ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_writers_stored_reason_reaches_the_state_row_tag_first(monkeypatch):
    _record_decide(monkeypatch)
    stored = "move:up:company + new_articles (1) + web (2)"
    stub = _Stub(
        [_row("ORCL", 12.0)], ["ORCL"], sigmas={"ORCL": 0.02},
        card=lambda s, kw: {"headline": "h", "stored_reason": stored,
                            "move_tag": "move:up:company"},
        outcome={"ORCL": MoveOutcome(kind="company", direction="up", verdict="supported",
                                     why="judged", written=True)},
    )
    await stub.run_sweep(refresh_news=False)
    decision, success, error = stub.finished["ORCL"]
    assert success is True and decision.reason == stored and decision.inputset_id == "fp-1"

    # The real `_finish_claim` stores it; the gate reads it back as today's held card.
    sb = _FakeSupabase()
    real = InsightSweeper.__new__(InsightSweeper)
    real.supabase = sb
    InsightSweeper._finish_claim(real, "ORCL", MON_1030, decision, True)
    patch = sb.upserts[0]
    assert patch["last_trigger_reason"] == stored
    assert held_move(patch["last_trigger_reason"], patch["last_generated_at"], MON_1030) == (
        MoveTag(kind="company", direction="up")
    )

    # …and a later sweep holds it.
    seen = _record_decide(monkeypatch,
                          lambda s, kw: Decision(action=ACTION_SKIP, reason=REASON_MOVE_HELD))
    later = _Stub([_row("ORCL", 12.0)], ["ORCL"], sigmas={"ORCL": 0.02},
                  states={"ORCL": {**_state(None), **patch}})
    await later.run_sweep(refresh_news=False)
    assert seen["ORCL"]["held_move"] == MoveTag(kind="company", direction="up")


@pytest.mark.asyncio
async def test_a_card_without_a_stored_reason_keeps_the_gate_reason(monkeypatch):
    _record_decide(monkeypatch)
    stub = _Stub([_row("AAPL", 0.4)], ["AAPL"])
    await stub.run_sweep(refresh_news=False)
    decision, success, _ = stub.finished["AAPL"]
    assert success is True and decision.reason == "new_articles (1)"
    assert stub.generated["AAPL"]["move"] is None and stub.generated["AAPL"]["jumping"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("error, restamped", [
    ("move_kept: company->none", True),
    ("move_kept: market->none", True),
    ("move_kept: none->none", False),
])
async def test_a_kept_attempt_is_a_failure_and_a_kept_cause_is_restamped(
    monkeypatch, error, restamped,
):
    _record_decide(monkeypatch)
    web = _FakeWeb()
    outcome = MoveOutcome(kind=None, direction="up", verdict=None, why="kept")
    stub = _Stub([_row("ORCL", 12.0)], ["ORCL"], web=web, sigmas={"ORCL": 0.02},
                 card=lambda s, kw: None, failure={"ORCL": error}, outcome={"ORCL": outcome})
    await stub.run_sweep(refresh_news=False)
    _, success, err = stub.finished["ORCL"]
    assert success is False and err == error
    assert (["ORCL"] in stub.verified) is restamped
    assert web.outcomes == [("ORCL", outcome)]          # the search hears every attempt


def test_the_real_finish_claim_records_a_kept_attempt_as_a_consuming_failure():
    decision = Decision(action=ACTION_GENERATE, reason="web (3)", inputset_id="fp-new",
                        price_band="Extreme")
    sb = _FakeSupabase()
    real = InsightSweeper.__new__(InsightSweeper)
    real.supabase = sb
    InsightSweeper._finish_claim(real, "ORCL", MON_1030, decision, False,
                                 "move_kept: company->none")
    patch = sb.upserts[0]
    assert patch["last_failure_at"] == MON_1030.isoformat()
    assert patch["last_inputset_id"] == "fp-new" and patch["last_price_band"] == "Extreme"
    assert "last_generated_at" not in patch and "last_trigger_reason" not in patch
    assert sb.rpcs == []                                  # never counted as a success

    # An ordinary failure still leaves the fingerprint alone.
    sb = _FakeSupabase()
    real.supabase = sb
    InsightSweeper._finish_claim(real, "ORCL", MON_1030, decision, False,
                                 "price_guard: share price talk")
    assert "last_inputset_id" not in sb.upserts[0]
    assert "last_failure_at" in sb.upserts[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("pct", [-0.5, 0.5])
async def test_a_capped_card_is_called_up_to_date_whichever_way_the_stock_moves(monkeypatch, pct):
    """Capped, the card cannot be rewritten today: "checking for updates" would promise a
    refresh that cannot come (the ORCL 2026-09-11 bug), contradicted or not (approved plan;
    review 2026-11-27)."""
    _record_decide(monkeypatch, lambda s, kw: Decision(action=ACTION_SKIP, reason="daily_cap"))
    stub = _Stub([_row("ORCL", pct)], ["ORCL"], states={"ORCL": _state("move:up:company")},
                 sigmas={"ORCL": 0.02})
    await stub.run_sweep(refresh_news=False)
    assert "ORCL" in stub.all_verified


@pytest.mark.asyncio
async def test_a_card_contradicted_below_notable_is_held_and_called_up_to_date(monkeypatch):
    """The real gate. −11% at 10:00 (a held down card), +0.8% at 14:00 with σ 4.7% — Typical,
    so no reversal can rewrite the card: the gate holds it (`move_card_held`), and a held
    card the gate will not rewrite is current, not "checking for updates" until 20:00."""
    at = datetime(2026, 11, 23, 19, 0, tzinfo=timezone.utc)          # 14:00 ET
    _freeze(monkeypatch, at)
    calls = _spy_decide(monkeypatch)
    stub = _Stub([_row("ORCL", 0.8, at=at)], ["ORCL"], at=at,
                 states={"ORCL": _state("move:down:company", at=at, minutes_ago=240)},
                 sigmas={"ORCL": 0.047})
    await stub.run_sweep(refresh_news=False)
    kw, verdict = calls["ORCL"][-1]
    assert kw["held_move"] == MoveTag(kind="company", direction="down")
    assert (verdict.action, verdict.reason) == (ACTION_SKIP, REASON_MOVE_HELD)
    assert "ORCL" in stub.all_verified
    assert stub.generated == {}


@pytest.mark.parametrize("reason, held, over, expected", [
    ("fingerprint_unchanged", None, False, True),
    ("cooldown", None, True, False),
    ("failure_cooldown", MoveTag("company", "up"), True, False),
    (REASON_MOVE_HELD, MoveTag("company", "up"), False, True),
    (REASON_MOVE_HELD, MoveTag("market", "down"), True, True),
    ("daily_cap", MoveTag("market", "down"), False, True),
    ("daily_cap", MoveTag("none", "up"), True, False),             # never for a cap
    ("attempt_cap", MoveTag("none", "up"), True, False),
    ("premarket_reserved", MoveTag("none", "up"), True, False),
    ("fingerprint_unchanged", MoveTag("none", "up"), False, False),
    ("fingerprint_unchanged", MoveTag("none", "up"), True, True),
    (REASON_NO_CAUSE_WAITING, MoveTag("none", "up"), True, False),
])
def test_stamp_current(reason, held, over, expected):
    assert InsightSweeper._stamp_current(reason, held, over) is expected


# ── odd inputs never raise ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("quote", [
    None, "junk", {}, {"changePercentage": None}, {"changePercentage": float("nan")},
    {"changePercentage": float("inf")}, {"changePercentage": True},
    {"changePercentage": 0.001},
    {"changePercentage": 12.0, "changeSession": "2020-01-02"},     # a prior session's move
])
def test_an_unusable_quote_is_no_live_move(quote):
    assert mod._live_move(quote, 0.02, MON_1030) == (None, False, "unknown")


@pytest.mark.parametrize("pct, sigma, expected", [
    (4.0, 0.03, ("up", True)),          # Notable
    (-1.0, 0.03, ("down", False)),      # Typical
    (10.5, 0.12, ("up", True)),         # Typical by σ, but the ≥10% floor
    (-9.9, 0.12, ("down", False)),
    (3.0, None, ("up", True)),          # the fixed band: notable at 2%
    (3.0, "x", ("up", True)),           # junk σ → the fixed band
])
def test_live_move_reads_the_gates_material_rule(pct, sigma, expected):
    direction, material, _ = mod._live_move(_row("X", pct), sigma, MON_1030)
    assert (direction, material) == expected


@pytest.mark.parametrize("quote, ok", [
    (_row("SPY", 4.7), True),
    (_row("SPY", 4.7, stamped=False), False),
    ({"changeSession": "2026-11-20"}, False),       # an earlier session
    ({"changeSession": "not a date"}, False),
    ({"changeSession": 20261120}, False),           # read like `price_service`'s stamp
    (None, False),
    ("junk", False),
])
def test_only_a_current_session_stamp_vouches_for_the_index(quote, ok):
    assert mod._stamped_this_session(quote, MON_1030) is ok


def test_newest_arrival_skips_what_it_cannot_read():
    rows = [
        None, "junk", {}, {"published_at": None}, {"published_at": "yesterday"},
        {"published_at": "2026-11-23T14:00:00Z"},
        {"published_at": "2026-11-23T15:00:00"},        # naive → UTC
        {"published_at": 1700000000},
        # A row with no readable publication time is skipped whatever else it carries.
        {"published_at": "nope", "cached_at": "2026-11-23T16:00:00Z", "external_id": "x"},
    ]
    seen = {"x": datetime(2026, 11, 23, 16, 30, tzinfo=timezone.utc)}
    assert mod._newest_arrival(rows) == datetime(2026, 11, 23, 15, 0, tzinfo=timezone.utc)
    assert mod._newest_arrival(rows, seen) == datetime(2026, 11, 23, 15, 0, tzinfo=timezone.utc)
    assert mod._newest_arrival([]) is None
    assert mod._newest_arrival(None) is None
    assert mod._newest_arrival(rows, "not a mapping") == mod._newest_arrival(rows)


def test_plan_moves_reads_only_a_valid_tag_written_today():
    future = (MON_1030 + timedelta(hours=2)).isoformat()
    states = {
        "A": {"last_trigger_reason": "move:up:company", "last_generated_at": future},
        "B": {"last_trigger_reason": "web_only + move:up:company",
              "last_generated_at": MON_1030.isoformat()},
        "C": {"last_trigger_reason": "move:sideways:company",
              "last_generated_at": MON_1030.isoformat()},
        "D": "not a row",
        "E": {"last_trigger_reason": None, "last_generated_at": None},
        "F": {"last_trigger_reason": "move:down:none + web (2)",
              "last_generated_at": (MON_1030 - timedelta(minutes=5)).isoformat()},
        MARKET_SCOPE: {"last_trigger_reason": "move:up:market",
                       "last_generated_at": MON_1030.isoformat()},
    }
    scopes = [MARKET_SCOPE, "A", "B", "C", "D", "E", "F"]
    quotes = {s: _row(s, 4.0) for s in scopes}
    plan = InsightSweeper._plan_moves(
        scopes, {}, quotes, {s: 0.03 for s in scopes}, states,
        {s: (False, False, None) for s in scopes}, "regular", False, MON_1030,
    )
    assert plan.held == {"F": MoveTag(kind="none", direction="down")}
    assert plan.basis == {"F": "reversal"} and plan.reversals == {"F": "Notable"}
    assert MARKET_SCOPE not in plan.gate_held and MARKET_SCOPE not in plan.basis

    # An MWCB day: tags are still read (the reserve), nothing is planned.
    plan = InsightSweeper._plan_moves(
        scopes, {}, quotes, {s: 0.03 for s in scopes}, states,
        {s: (False, False, None) for s in scopes}, "regular", True, MON_1030,
    )
    assert plan.held and plan.basis == {} and plan.reversals == {}


# ── admission, outcomes, logs ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_reversal_is_urgent_in_admission(monkeypatch):
    names = [f"N{i}" for i in range(9)]

    def _verdict(scope, kw):
        if scope == "REV":
            return Decision(action=ACTION_GENERATE, reason="reversal down->up", score=1.0,
                            inputset_id="fp")
        return Decision(action=ACTION_GENERATE, reason="new_articles (1)", score=50.0,
                        inputset_id="fp")

    _record_decide(monkeypatch, _verdict)
    stub = _Stub([_row(s, 0.3) for s in [*names, "REV"]], [*names, "REV"])
    await stub.run_sweep(refresh_news=False)
    assert "REV" in stub.generated and len(stub.generated) == mod._PER_CYCLE_REGEN_CAP


@pytest.mark.parametrize("reason, expected", [
    ("reversal down->up", True),
    ("band Notable->Unusual (+5.49%) + reversal down->up", True),
    ("new_articles (3)", False),
    ("web (2)", False),
    (None, False),
])
def test_is_reversal(reason, expected):
    assert mod._is_reversal(reason) is expected


@pytest.mark.parametrize("error, kind", [
    ("move_kept: company->none", "company"),
    ("move_kept: market->none", "market"),
    ("move_kept: none->none", "none"),
    ("move_kept: banana->none", None),
    ("move_unverified: stored card unreadable (new none)", None),
    (None, None),
])
def test_kept_held_kind(error, kind):
    assert mod._kept_held_kind(error) == kind


@pytest.mark.asyncio
async def test_the_sweep_log_counts_move_cards_and_carries_no_text(monkeypatch, caplog):
    _record_decide(monkeypatch)
    web = _FakeWeb()
    stub = _Stub(
        [_row("CRWV", 22.0), _row("SPY", 4.7)], [MARKET_SCOPE, "CRWV"], web=web,
        bulk={MARKET_SCOPE: _MARKET_ROWS}, states={"CRWV": _state("move:up:company")},
        sigmas={"CRWV": 0.05},
        outcome={"CRWV": MoveOutcome(kind="company", direction="up", verdict="supported",
                                     why="judged", written=True)},
    )
    caplog.set_level(logging.INFO, logger=mod.__name__)
    await stub.run_sweep(refresh_news=False)
    line = [r.getMessage() for r in caplog.records
            if r.getMessage().startswith("Insight sweep (")][-1]
    for part in ("held=1", "move_mode=1", "reversals=0", "market_confirmed=1",
                 "move_attempts=1", "kept=0"):
        assert part in line
    for text in ("Federal Reserve", "chief executive", "hospital network"):
        assert text not in caplog.text


@pytest.mark.asyncio
async def test_a_move_mode_card_without_a_tag_is_logged(monkeypatch, caplog):
    _record_decide(monkeypatch)
    stub = _Stub([_row("ORCL", 12.0)], ["ORCL"], sigmas={"ORCL": 0.02},
                 card=lambda s, kw: {"headline": "h", "stored_reason": "new_articles (1)",
                                     "move_tag": None})
    caplog.set_level(logging.INFO, logger=mod.__name__)
    await stub.run_sweep(refresh_news=False)
    assert any("stored without a move tag" in r.getMessage() for r in caplog.records
               if r.levelno == logging.WARNING)


# ── shutdown ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_loop_closes_the_jump_search_when_cancelled_in_its_sleep(monkeypatch):
    web = _FakeWeb()
    stub = _Stub([], [], web=web)
    ticked = asyncio.Event()

    async def _tick(sweeper, state, *, market_active, monotonic):
        ticked.set()
        return None

    monkeypatch.setattr(mod, "_START_DELAY_SECONDS", 0)
    monkeypatch.setattr(mod, "get_insight_sweeper", lambda: stub)
    monkeypatch.setattr(mod, "_run_one_tick", _tick)
    task = asyncio.ensure_future(mod.run_insight_sweeper_loop())
    await asyncio.wait_for(ticked.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert web.closed is True


@pytest.mark.asyncio
async def test_a_stuck_close_never_holds_the_shutdown(monkeypatch, caplog):
    class _Stuck(_FakeWeb):
        async def aclose(self):
            await asyncio.sleep(10)

    stub = _Stub([], [], web=_Stuck())
    monkeypatch.setattr(mod, "_WEB_CLOSE_SECONDS", 0.01)
    caplog.set_level(logging.WARNING, logger=mod.__name__)
    await mod._close_jump_search(stub)
    assert "did not stop" in caplog.text
    await mod._close_jump_search(_Stub([], []))          # no search at all: a no-op


# ── review 2026-11-27: the report-day close, outages, a stale tag, late stories ──────────

def _utc(hour, minute, day=23):
    """Mon 2026-11-<day> at <hour>:<minute> ET (EST, UTC-5)."""
    return datetime(2026, 11, day, hour + 5, minute, tzinfo=timezone.utc)


def _state_at(tag, written, at, **extra):
    """A state row holding a card written at ``written`` on the ET day of ``at``."""
    return {
        "regen_day": session_trading_date(at).isoformat(),
        "regen_count_today": 2, "attempts_today": 2,
        "last_inputset_id": "an-older-fingerprint",
        "last_generated_at": written.isoformat(),
        "last_trigger_reason": f"{tag} + new_articles (3)" if tag else "new_articles (3)",
        "last_price_band": None,
        **extra,
    }


def _gate_inputs(monkeypatch, inputs):
    monkeypatch.setattr(mod, "earnings_gate_inputs", lambda status, today: inputs)


@pytest.mark.asyncio
async def test_a_held_report_day_card_is_kept_after_the_close_while_results_are_pending(
    monkeypatch,
):
    """An after-close reporter holding a company card from 10:05, results not in at 16:02.
    The card window replaces the move corpus at the bell (here one more price recap), so the
    fingerprint re-keys with no new evidence: the real gate must HOLD the card — before, it
    paid for a general card before the release, and that card's 30-minute report-day cooldown
    then delayed the earnings card."""
    at = _freeze(monkeypatch, _utc(16, 2))
    _gate_inputs(monkeypatch, (True, True, None))
    calls = _spy_decide(monkeypatch)
    raw = [
        _news("ORCL", "a", "ORCL signs a multiyear cloud contract with a hospital network", 6,
              at=at),
        _news("ORCL", "b", "Why ORCL stock is soaring today", 1, at=at),
    ]
    stub = _Stub([_row("ORCL", 12.0, at=at)], ["ORCL"], at=at, bulk={"ORCL": raw},
                 states={"ORCL": _state_at("move:up:company", _utc(10, 5), at)},
                 sigmas={"ORCL": 0.02})
    await stub.run_sweep(refresh_news=False)
    kw, verdict = calls["ORCL"][-1]
    assert kw["held_move"] == MoveTag(kind="company", direction="up")
    assert (verdict.action, verdict.reason) == (ACTION_SKIP, REASON_MOVE_HELD)
    assert stub.generated == {}
    assert "ORCL" in stub.all_verified


@pytest.mark.asyncio
async def test_results_landing_after_the_card_release_it_for_the_earnings_card(monkeypatch):
    at = _freeze(monkeypatch, _utc(16, 12))
    _gate_inputs(monkeypatch, (True, False, _utc(16, 8)))
    calls = _spy_decide(monkeypatch)
    stub = _Stub([_row("ORCL", 12.0, at=at)], ["ORCL"], at=at,
                 states={"ORCL": _state_at("move:up:company", _utc(10, 5), at)},
                 sigmas={"ORCL": 0.02})
    await stub.run_sweep(refresh_news=False)
    kw, verdict = calls["ORCL"][-1]
    assert kw["held_move"] is None
    assert verdict.action == ACTION_GENERATE and "earnings_reported" in verdict.reason
    assert stub.generated["ORCL"]["move"] is None
    assert stub.generated["ORCL"]["jumping"] is False


@pytest.mark.asyncio
async def test_a_before_the_open_reporters_card_stays_held_in_move_mode_after_the_close(
    monkeypatch,
):
    """Results at 07:00, the move card at 09:40 tells them: after the close nothing is waiting
    to replace it. Before, report day alone released it at 16:00 and a plain card (its chip the
    articles' net lean) replaced it every evening."""
    at = _freeze(monkeypatch, MON_1630)
    _gate_inputs(monkeypatch, (True, False, _utc(7, 0)))
    calls = _spy_decide(monkeypatch)
    states = {"ORCL": _state_at("move:up:company", _utc(9, 40), at)}
    # The results story, published before the card: nothing new since (fresh news after the
    # card would break the hold here exactly as in the session).
    bulk = {"ORCL": [_news("ORCL", "r", "ORCL quarterly revenue rises on cloud demand", 8.5,
                           at=at)]}
    stub = _Stub([_row("ORCL", 12.0, at=at)], ["ORCL"], at=at, states=states, bulk=bulk,
                 sigmas={"ORCL": 0.02})
    await stub.run_sweep(refresh_news=False)
    kw, verdict = calls["ORCL"][-1]
    assert kw["held_move"] == MoveTag(kind="company", direction="up")
    assert (verdict.action, verdict.reason) == (ACTION_SKIP, REASON_MOVE_HELD)
    assert stub.generated == {}

    # Still in move mode: a regeneration (here forced) is a move card, never a plain one.
    _record_decide(monkeypatch)
    stub = _Stub([_row("ORCL", 12.0, at=at)], ["ORCL"], at=at, states=states, bulk=bulk,
                 sigmas={"ORCL": 0.02})
    await stub.run_sweep(refresh_news=False)
    move = stub.generated["ORCL"]["move"]
    assert move is not None and move.held == MoveTag(kind="company", direction="up")
    assert stub.generated["ORCL"]["jumping"] is True


@pytest.mark.parametrize("inputs, phase, gate_held, move_mode", [
    ((True, True, None), SESSION_AFTERHOURS, True, False),           # pending: held only
    ((True, False, _utc(7, 0)), SESSION_AFTERHOURS, True, True),     # results before the card
    ((True, False, _utc(10, 5)), SESSION_AFTERHOURS, True, True),    # the same instant
    ((True, False, _utc(16, 8)), SESSION_AFTERHOURS, False, False),  # results after the card
    ((True, False, None), SESSION_AFTERHOURS, False, False),         # landing never seen
    ((True, False, "not a time"), SESSION_AFTERHOURS, False, False),
    ((True, True, None), SESSION_REGULAR, True, True),               # in the session: as ever
    ((False, False, None), SESSION_AFTERHOURS, True, True),          # no report today
])
def test_plan_moves_on_a_report_day_after_the_close(inputs, phase, gate_held, move_mode):
    states = {"ORCL": _state_at("move:up:company", _utc(10, 5), MON_1630)}
    plan = InsightSweeper._plan_moves(
        ["ORCL"], {}, {"ORCL": _row("ORCL", 4.0, at=MON_1630)}, {"ORCL": 0.03}, states,
        {"ORCL": inputs}, phase, False, MON_1630,
    )
    assert plan.held == {"ORCL": MoveTag(kind="company", direction="up")}
    assert ("ORCL" in plan.gate_held) is gate_held
    assert ("ORCL" in plan.basis) is move_mode


def test_a_card_postdates_results_only_with_both_times_known():
    tag = MoveTag(kind="company", direction="up")
    row = {"last_generated_at": _utc(9, 40).isoformat()}
    assert mod._card_postdates_results(tag, row, _utc(7, 0)) is True
    assert mod._card_postdates_results(tag, row, _utc(9, 41)) is False
    assert mod._card_postdates_results(None, row, _utc(7, 0)) is False
    assert mod._card_postdates_results(tag, "junk", _utc(7, 0)) is False
    assert mod._card_postdates_results(tag, {"last_generated_at": None}, _utc(7, 0)) is False
    assert mod._card_postdates_results(tag, row, None) is False
    # A naive landing reads as UTC, like every other stamp.
    assert mod._card_postdates_results(tag, row, _utc(7, 0).replace(tzinfo=None)) is True


# An attempt that judged nothing consumes no evidence (the gate's floor ignores it).

@pytest.mark.parametrize("why, unjudged", [
    ("model_unavailable", True), ("stored_unreadable", True), ("judge_unavailable", True),
    ("judge_budget", True), ("write_failed", True),
    ("kept", False), ("web_only_no_source", False), ("web_only_no_content", False),
    ("empty_corpus", False), ("supported", False),
])
def test_a_move_attempt_is_judged_by_its_outcome(why, unjudged):
    outcome = MoveOutcome(kind=None, direction="up", verdict=None, why=why)
    # The outcome decides, whatever the failure text says.
    assert mod._attempt_unjudged("conclusion_guard: figure $5", outcome) is unjudged
    assert mod._attempt_unjudged("malformed JSON", outcome) is unjudged


@pytest.mark.parametrize("error, unjudged", [
    ("price_guard: share price talk", False),
    ("conclusion_guard: figure $5,000", False),
    ("web_only_guard: unsupported figure", False),
    ("web_only_no_source: 0 of 2 page(s) linkable", False),
    ("web_only_no_content: price talk only (pages=2)", False),
    ("web_only_judge: headline=unsupported conclusion=ok points=1/3", False),
    ("empty corpus", False),
    ("move_kept: company->none", False),
    ("web_only_judge: unavailable", True),
    ("web_only_judge: budget", True),
    ("malformed JSON", True),
    ("invalid output shape", True),
    ("cache write failed", True),
    ("generation returned no card [web pages: 2]", True),
    ("ReadTimeout: the model did not answer", True),
    (None, True), ("", True),
])
def test_an_ordinary_attempt_is_judged_by_its_failure_reason(error, unjudged):
    assert mod._attempt_unjudged(error, None) is unjudged


def test_the_outcome_codes_the_sweeper_reads_are_the_writers():
    """Every code the classification names is a string the writer really produces (its code,
    never a docstring), so a rename there fails here instead of silently reading every outage
    as judged."""
    import app.services.news_insight_service as writer

    tree = ast.parse(inspect.getsource(writer))
    docstrings = {
        id(node.value) for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }
    literals = {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in docstrings
    }
    for why in mod._UNJUDGED_MOVE_WHYS:
        assert why in literals, why
    assert MOVE_KEPT in mod._JUDGED_FAILURE_PREFIXES
    for prefix in mod._JUDGED_FAILURE_PREFIXES:
        if prefix != MOVE_KEPT:
            assert any(text.startswith(prefix) for text in literals), prefix


def test_mark_unjudged_marks_once():
    assert mod._mark_unjudged("malformed JSON") == "unjudged: malformed JSON"
    assert mod._mark_unjudged("unjudged: malformed JSON") == "unjudged: malformed JSON"
    assert mod._mark_unjudged(None) == "unjudged: generation returned no card"
    assert mod._without_unjudged("unjudged: move_kept: company->none") == (
        "move_kept: company->none"
    )
    assert mod._without_unjudged(None) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("error, why, stored, kept, restamped", [
    ("model call failed: TimeoutError", "model_unavailable",
     "unjudged: model call failed: TimeoutError", 0, False),
    ("move_kept: company->none", "judge_unavailable",
     "unjudged: move_kept: company->none", 0, False),
    ("move_kept: company->none", "kept", "move_kept: company->none", 1, True),
])
async def test_an_attempt_that_judged_nothing_is_recorded_unjudged(
    monkeypatch, caplog, error, why, stored, kept, restamped,
):
    _record_decide(monkeypatch)
    outcome = MoveOutcome(kind=None, direction="up", verdict=None, why=why)
    stub = _Stub([_row("ORCL", 12.0)], ["ORCL"], sigmas={"ORCL": 0.02},
                 states={"ORCL": _state("move:up:company")},
                 card=lambda s, kw: None, failure={"ORCL": error}, outcome={"ORCL": outcome})
    caplog.set_level(logging.INFO, logger=mod.__name__)
    await stub.run_sweep(refresh_news=False)
    decision, success, err = stub.finished["ORCL"]
    assert success is False and err == stored
    # The state already names the kept card: the gate's decision is recorded unchanged.
    assert decision.reason == "new_articles (1)"
    assert (["ORCL"] in stub.verified) is restamped
    line = [r.getMessage() for r in caplog.records
            if r.getMessage().startswith("Insight sweep (")][-1]
    assert f"kept={kept}" in line and f"unjudged={1 - kept}" in line


@pytest.mark.asyncio
async def test_a_raise_is_recorded_unjudged(monkeypatch):
    _record_decide(monkeypatch)

    def _boom(scope, kw):
        raise RuntimeError("the model client broke")

    stub = _Stub([_row("ORCL", 12.0)], ["ORCL"], sigmas={"ORCL": 0.02}, card=_boom)
    await stub.run_sweep(refresh_news=False)
    _, success, err = stub.finished["ORCL"]
    assert success is False and err == "unjudged: RuntimeError: the model client broke"


def test_the_real_finish_claim_keeps_an_unjudged_keeps_corpus_unconsumed():
    decision = Decision(action=ACTION_GENERATE, reason="web (3)", inputset_id="fp-new",
                        price_band="Extreme")
    sb = _FakeSupabase()
    real = InsightSweeper.__new__(InsightSweeper)
    real.supabase = sb
    InsightSweeper._finish_claim(real, "ORCL", MON_1030, decision, False,
                                 "unjudged: move_kept: company->none")
    patch = sb.upserts[0]
    assert patch["last_error"] == "unjudged: move_kept: company->none"
    assert patch["last_failure_at"] == MON_1030.isoformat()
    assert "last_inputset_id" not in patch and "last_trigger_reason" not in patch
    assert sb.rpcs == []


@pytest.mark.asyncio
@pytest.mark.parametrize("error, why, retried", [
    ("model call failed: TimeoutError", "model_unavailable", True),
    ("web_only_no_source: 0 of 2 page(s) linkable", "web_only_no_source", False),
])
async def test_pages_that_met_an_outage_get_their_attempt_after_the_failure_cooldown(
    monkeypatch, error, why, retried,
):
    """End to end with the real gate and the real state write: a held no-cause card, a page
    set at 12:00, the 12:05 attempt fails. An outage leaves the pages their attempt after the
    failure cooldown; a judged refusal consumes them."""
    first = _freeze(monkeypatch, _utc(12, 5))
    calls = _spy_decide(monkeypatch)
    web = _FakeWeb(pages={"ORCL": ("p1", "p2")}, refreshed_at=_utc(12, 0))
    raw = [_news("ORCL", "a", "ORCL signs a multiyear cloud contract with a hospital network",
                 3, at=first)]
    state = _state_at("move:up:none", _utc(9, 50), first)
    outcome = MoveOutcome(kind=None, direction="up", verdict=None, why=why)
    stub = _Stub([_row("ORCL", 12.0, at=first)], ["ORCL"], at=first, web=web,
                 bulk={"ORCL": raw}, states={"ORCL": state}, sigmas={"ORCL": 0.02},
                 card=lambda s, kw: None, failure={"ORCL": error}, outcome={"ORCL": outcome})
    await stub.run_sweep(refresh_news=False)
    verdict = calls["ORCL"][-1][1]
    assert verdict.action == ACTION_GENERATE and "web (2)" in verdict.reason

    # The real `_finish_claim` writes the outcome into the state the next sweep reads.
    decision, success, err = stub.finished["ORCL"]
    sb = _FakeSupabase()
    real = InsightSweeper.__new__(InsightSweeper)
    real.supabase = sb
    InsightSweeper._finish_claim(real, "ORCL", first, decision, success, err)
    stub._states = {"ORCL": {**state, **sb.upserts[0], "attempts_today": 3}}
    stub.generated.clear()

    later = _freeze(monkeypatch, _utc(12, 21))
    stub._at = later
    await stub.run_sweep(refresh_news=False)
    verdict = calls["ORCL"][-1][1]
    if retried:
        assert verdict.action == ACTION_GENERATE and "web (2)" in verdict.reason
    else:
        assert verdict.action == ACTION_SKIP
        assert verdict.reason in (REASON_NO_CAUSE_WAITING, "fingerprint_unchanged")


# A kept attempt names the stored card when the state's tag is stale.

@pytest.mark.asyncio
async def test_a_kept_card_the_state_does_not_name_is_recorded_and_ends_the_reversal_loop(
    monkeypatch, caplog,
):
    """The state says `move:up:company` (10:00); the stored row holds `move:down:company`
    (14:00), because the state write after that card failed. Live −5% (Unusual): the gate
    reads a reversal of the stale tag, the writer keeps the stored down card. Before, the
    state kept the stale tag and every failure cooldown bought another paid "reversal
    up->down" until the attempt cap."""
    first = _freeze(monkeypatch, _utc(14, 16))
    calls = _spy_decide(monkeypatch)
    state = _state_at("move:up:company", _utc(10, 0), first)
    outcome = MoveOutcome(kind=None, direction="down", verdict=None, why="kept")
    stub = _Stub([_row("ORCL", -5.0, at=first)], ["ORCL"], at=first, states={"ORCL": state},
                 sigmas={"ORCL": 0.025}, card=lambda s, kw: None,
                 failure={"ORCL": "move_kept: company->none"}, outcome={"ORCL": outcome})
    caplog.set_level(logging.WARNING, logger=mod.__name__)
    await stub.run_sweep(refresh_news=False)
    verdict = calls["ORCL"][-1][1]
    assert verdict.action == ACTION_GENERATE and "reversal up->down" in verdict.reason
    decision, success, err = stub.finished["ORCL"]
    assert success is False and err == "move_kept: company->none"
    assert decision.reason == "move:down:company + move_kept"
    assert decision.inputset_id == verdict.inputset_id
    assert "named another move card" in caplog.text

    sb = _FakeSupabase()
    real = InsightSweeper.__new__(InsightSweeper)
    real.supabase = sb
    InsightSweeper._finish_claim(real, "ORCL", first, decision, success, err)
    patch = sb.upserts[0]
    assert patch["last_trigger_reason"] == "move:down:company + move_kept"
    assert "last_generated_at" not in patch and sb.rpcs == []
    stub._states = {"ORCL": {**state, **patch, "attempts_today": 3}}

    later = _freeze(monkeypatch, _utc(14, 32))
    stub._at = later
    stub.generated.clear()
    await stub.run_sweep(refresh_news=False)
    kw, verdict = calls["ORCL"][-1]
    assert kw["held_move"] == MoveTag(kind="company", direction="down")
    # The corpus it judged is unchanged: no paid "reversal up->down" again.
    assert verdict.action != ACTION_GENERATE and "reversal" not in verdict.reason
    assert stub.generated == {}


def test_the_real_finish_claim_names_a_kept_card_only_for_a_kept_failure():
    sb = _FakeSupabase()
    real = InsightSweeper.__new__(InsightSweeper)
    real.supabase = sb
    tagged = Decision(action=ACTION_GENERATE, reason="move:down:company + move_kept",
                      inputset_id="fp-new", price_band="Unusual")
    # An unjudged keep still names the stored card (its tag is known), consuming nothing.
    InsightSweeper._finish_claim(real, "ORCL", MON_1030, tagged, False,
                                 "unjudged: move_kept: company->none")
    assert sb.upserts[-1]["last_trigger_reason"] == "move:down:company + move_kept"
    assert "last_inputset_id" not in sb.upserts[-1]
    # Any other failure never writes a tag.
    InsightSweeper._finish_claim(real, "ORCL", MON_1030, tagged, False,
                                 "price_guard: share price talk")
    assert "last_trigger_reason" not in sb.upserts[-1]
    # A gate reason never leads with a tag, so a kept failure writes none.
    plain = Decision(action=ACTION_GENERATE, reason="reversal up->down", inputset_id="fp-new",
                     price_band="Unusual")
    InsightSweeper._finish_claim(real, "ORCL", MON_1030, plain, False,
                                 "move_kept: company->none")
    assert "last_trigger_reason" not in sb.upserts[-1]


@pytest.mark.parametrize("error, direction, expected", [
    ("move_kept: company->none", "down", MoveTag(kind="company", direction="down")),
    ("move_kept: market->none", "up", MoveTag(kind="market", direction="up")),
    ("move_kept: company->none", "sideways", None),
    ("move_kept: banana->none", "up", None),
    ("unjudged: move_kept: company->none", "up", None),
    ("price_guard: x", "up", None),
    (None, "up", None),
])
def test_kept_tag(error, direction, expected):
    assert mod._kept_tag(error, direction) == expected


# A story FMP delivers late still breaks the hold: arrival, not publication alone.

def test_first_seen_rows_dates_a_late_row_to_the_sweep_that_first_saw_it():
    stub = _Stub([], [])
    t0 = _utc(12, 5)
    a = {"external_id": "a", "published_at": (t0 - timedelta(hours=2)).isoformat()}
    b = {"external_id": "b", "published_at": _utc(11, 58).isoformat()}
    # The first look cannot place a row: its publication time stands.
    assert stub._first_seen_rows("ORCL", [a], t0) == {"a": t0 - timedelta(hours=2)}
    # Watched since the last sweep: a new row arrived now, however old its stamp.
    t1 = t0 + timedelta(minutes=15)
    seen = stub._first_seen_rows("ORCL", [a, b], t1)
    assert seen == {"a": t0 - timedelta(hours=2), "b": t1}
    assert mod._newest_arrival([a, b], seen) == t1
    # After a longer gap (a restart, out of move mode) a new row counts from its stamp.
    c = {"external_id": "c", "published_at": (t1 - timedelta(minutes=3)).isoformat()}
    seen = stub._first_seen_rows("ORCL", [a, b, c], t1 + timedelta(minutes=16))
    assert seen["c"] == t1 - timedelta(minutes=3) and seen["b"] == t1
    # Scopes are watched apart, and a new ET day forgets everything.
    assert stub._first_seen_rows("AAPL", [b], t1 + timedelta(minutes=20)) == {
        "b": _utc(11, 58)
    }
    assert stub._first_seen_rows("ORCL", [b], t0 + timedelta(days=1)) == {"b": _utc(11, 58)}
    # Odd rows are skipped, never raised on.
    assert stub._first_seen_rows("MSFT", [None, "junk", {}, {"external_id": "z"}], t0) == {}


def test_newest_arrival_reads_cached_at_when_the_read_carries_it():
    row = {"external_id": "b", "published_at": _utc(11, 58).isoformat(),
           "cached_at": _utc(12, 20).isoformat()}
    assert mod._newest_arrival([row]) == _utc(12, 20)
    # Never earlier than its publication.
    row["cached_at"] = _utc(11, 0).isoformat()
    assert mod._newest_arrival([row]) == _utc(11, 58)


@pytest.mark.asyncio
async def test_a_story_delivered_after_the_card_breaks_a_held_no_cause_card(monkeypatch):
    """CRWV 11-23 shape: the no-cause card at 12:05; the explaining story is stamped 11:58
    but reaches the corpus at 12:20. Read by its stamp it is older than the card and never
    breaks the hold; read by its arrival it does, once the 45-minute spacing has passed."""
    calls = _spy_decide(monkeypatch)
    card_at = _utc(12, 5)
    old = _news("ORCL", "a", "ORCL signs a multiyear cloud contract with a hospital network", 2,
                at=card_at)
    late = {"external_id": "ORCL-b", "headline": "ORCL names a new head of its database unit",
            "published_at": _utc(11, 58).isoformat()}
    state = _state_at("move:up:none", card_at, card_at)
    stub = _Stub([_row("ORCL", 12.0, at=card_at)], ["ORCL"], at=card_at, bulk={"ORCL": [old]},
                 states={"ORCL": state}, sigmas={"ORCL": 0.02})

    for at, rows in ((_utc(12, 10), [old]), (_utc(12, 20), [old, late])):
        _freeze(monkeypatch, at)
        stub._bulk = {"ORCL": rows}
        await stub.run_sweep(refresh_news=False)
        kw, verdict = calls["ORCL"][-1]
        assert verdict.action == ACTION_SKIP, at
    assert kw["fresh_news_at"] == _utc(12, 20)

    _freeze(monkeypatch, _utc(12, 55))
    await stub.run_sweep(refresh_news=False)
    kw, verdict = calls["ORCL"][-1]
    assert kw["fresh_news_at"] == _utc(12, 20)
    assert verdict.action == ACTION_GENERATE and "new_articles" in verdict.reason
    assert stub.generated["ORCL"]["move"] is not None


# ── fakes ───────────────────────────────────────────────────────────────────────────


class _Result:
    def __init__(self, data):
        self.data = data


class _FakeSupabase:
    """Captures `_finish_claim`'s one upsert and its increment RPC."""

    def __init__(self):
        self.upserts = []
        self.rpcs = []

    def table(self, name):
        outer = self

        class _T:
            def upsert(self, row, on_conflict=None):
                outer.upserts.append({k: v for k, v in row.items() if k != "scope"})
                return self

            def execute(self):
                return _Result(None)

        return _T()

    def rpc(self, name, params):
        self.rpcs.append(name)

        class _E:
            def execute(self):
                return _Result([1])

        return _E()


# ── a held MARKET card whose S&P 500 turned (integration review 2026-11-27) ──────────

_SPY_STAMPED = {"symbol": "SPY", "changePercentage": -0.8, "changeSession": "2026-11-23"}


@pytest.mark.parametrize("held, quote, change, expected", [
    (MoveTag("market", "up"), _SPY_STAMPED, -0.8, True),           # turned down by 0.8%
    (MoveTag("market", "up"), _SPY_STAMPED, -0.49, False),         # under the 0.5% bar
    (MoveTag("market", "up"), _SPY_STAMPED, 1.2, False),           # still the same way
    (MoveTag("market", "down"), _SPY_STAMPED, 0.6, True),
    (MoveTag("company", "up"), _SPY_STAMPED, -2.0, False),         # only a market card
    (MoveTag("none", "up"), _SPY_STAMPED, -2.0, False),
    (None, _SPY_STAMPED, -2.0, False),
    (MoveTag("market", "up"), {"symbol": "SPY", "changePercentage": -0.8}, -0.8, False),
    (MoveTag("market", "up"), None, -0.8, False),
    (MoveTag("market", "up"), _SPY_STAMPED, float("nan"), False),
    (MoveTag("market", "up"), _SPY_STAMPED, None, False),
    (MoveTag("market", "up"), _SPY_STAMPED, True, False),
    (MoveTag("market", "up"), _SPY_STAMPED, "x", False),
])
def test_index_against_reads_a_stamped_index_turned_the_other_way(held, quote, change, expected):
    assert mod._index_against(held, quote, change, MON_1030) is expected


def test_a_turned_market_card_is_never_called_up_to_date():
    market = MoveTag("market", "up")
    for reason in sorted(mod._VERIFIED_CURRENT_REASONS):
        assert InsightSweeper._stamp_current(reason, market, True, index_against=True) is False
        assert InsightSweeper._stamp_current(reason, market, True) is True
    company = MoveTag("company", "up")
    assert InsightSweeper._stamp_current("move_card_held", company, True, index_against=True)


@pytest.mark.asyncio
async def test_a_turned_index_reaches_the_gate_and_the_writer_for_a_held_market_card(monkeypatch):
    """CRWV holds this morning's "moves with the broader market" card (up) and is still up 22%,
    but the S&P 500 has turned down 0.9%: the gate gets ``index_against_held`` (it breaks the
    hold) and the writer's MoveContext ``index_against`` (it replaces the market card)."""
    seen = _record_decide(monkeypatch)
    stub = _Stub(
        [_row("CRWV", 22.0), _row("SPY", -0.9)], [MARKET_SCOPE, "CRWV"],
        bulk={MARKET_SCOPE: _MARKET_ROWS}, sigmas={"CRWV": 0.05},
        states={"CRWV": _state("move:up:market")},
    )
    await stub.run_sweep(refresh_news=False)
    assert seen["CRWV"]["index_against_held"] is True
    assert stub.generated["CRWV"]["move"].index_against is True

    # The same card while the index still rises: nothing turned.
    seen = _record_decide(monkeypatch)
    stub = _Stub(
        [_row("CRWV", 22.0), _row("SPY", 1.1)], [MARKET_SCOPE, "CRWV"],
        bulk={MARKET_SCOPE: _MARKET_ROWS}, sigmas={"CRWV": 0.05},
        states={"CRWV": _state("move:up:market")},
    )
    await stub.run_sweep(refresh_news=False)
    assert seen["CRWV"]["index_against_held"] is False
    assert stub.generated["CRWV"]["move"].index_against is False

    # A held COMPANY card never reads the index.
    seen = _record_decide(monkeypatch)
    stub = _Stub(
        [_row("CRWV", 22.0), _row("SPY", -0.9)], [MARKET_SCOPE, "CRWV"],
        bulk={MARKET_SCOPE: _MARKET_ROWS}, sigmas={"CRWV": 0.05},
        states={"CRWV": _state("move:up:company")},
    )
    await stub.run_sweep(refresh_news=False)
    assert seen["CRWV"]["index_against_held"] is False
