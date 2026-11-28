"""Move cards in the Insights materiality gate (2026-11-27).

CRWV, Mon 2026-11-23: a Bullish card written at 09:50 ET for a +20% jump was replaced at
10:07 by a plain ``new_articles`` regeneration over a 72 h corpus — the window re-keyed
and fresh wire ids arrived, but nothing explained the move better. PLUG, Wed 2026-11-25:
the morning's −10% card kept serving after the stock turned to +5% and then +10%, because
the fingerprint is sign-blind.

The gate now HOLDS a same-day move card (`insight_move.held_move` → ``held_move``): a
changed fingerprint alone does not regenerate it. Only a breaker does — a reversal, a new
page set, earnings landing, or fresh news (60 min apart for a company card, 45 min for a
market or no-cause card) — each but the reversal measured against the evidence floor
(the later of the last success and the last failure), and never past the caps, MWCB or
the cooldowns. Every text below is HAND-WRITTEN test data, never a production card or a
web page.
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.services import updates_materiality as um
from app.services.insight_move import (
    KIND_COMPANY,
    KIND_MARKET,
    KIND_NONE,
    MoveTag,
    format_move_tag,
    held_move,
)
from app.services.updates_materiality import (
    ACTION_GENERATE,
    ACTION_SKIP,
    ACTION_TOUCH,
    MOVE_HOLD_COMPANY_NEWS_SPACING_SECONDS,
    MOVE_HOLD_NEWS_SPACING_SECONDS,
    PROMPT_VERSION,
    REASON_MOVE_HELD,
    REASON_NO_CAUSE_WAITING,
    canonical_band,
    classify_move,
    compute_inputset_id,
    corpus_article_ids,
    daily_cap_for,
    decide,
)
from app.utils.market_hours import SESSION_PREMARKET, SESSION_REGULAR

ET = ZoneInfo("America/New_York")
MODEL = "insight-test-model"
# Mon 2026-11-23, regular session: the CRWV replay.
NOW = datetime(2026, 11, 23, 10, 7, tzinfo=ET)
SIGMA = 0.06                                   # +20.58% is z ≈ 3.4: Extreme
JUMP = {"changePercentage": 20.58, "marketCap": 4e10}


def _corpus(n, tag="a"):
    return [
        {"external_id": f"https://news.example/{tag}/{i}",
         "headline": f"Hand-written test story {tag}{i}"}
        for i in range(n)
    ]


CORPUS = _corpus(9)


def _fp(corpus, quote=JUMP, sigma=SIGMA):
    live = classify_move(quote.get("changePercentage"), sigma, market_cap=quote.get("marketCap"))
    return compute_inputset_id(
        corpus_article_ids(corpus), canonical_band(live), MODEL, PROMPT_VERSION,
    )


def _state(*, kind=KIND_COMPANY, direction="up", generated=NOW - timedelta(minutes=17),
           failed=None, successes=3, attempts=None, band="Extreme", inputset="an-older-corpus",
           now=NOW):
    st = {
        "regen_day": now.astimezone(ET).date().isoformat(),
        "regen_count_today": successes,
        "attempts_today": successes if attempts is None else attempts,
        "close_cycle": (now - timedelta(hours=2)).isoformat(),
        "last_inputset_id": inputset,
        "last_price_band": band,
    }
    if generated is not None:
        st["last_generated_at"] = generated.isoformat()
    if failed is not None:
        st["last_failure_at"] = failed.isoformat()
    tag = format_move_tag(direction, kind)
    st["last_trigger_reason"] = f"{tag} + new_articles (5)" if tag else "new_articles (5)"
    return st


def _held(st, now=NOW):
    """What the sweeper passes: the same-day tag read off the stored reason."""
    return held_move(st.get("last_trigger_reason"), st.get("last_generated_at"), now)


def _decide(*, state, now=NOW, quote=JUMP, sigma=SIGMA, corpus=CORPUS, held=True, **over):
    kwargs = dict(
        scope="CRWV",
        corpus=corpus,
        quote=quote,
        state=state,
        market_change_percent=4.7,
        close_cycle_start=now - timedelta(hours=18),
        now=now,
        model=MODEL,
        market_active=True,
        is_market_scope=False,
        sigma_daily=sigma,
        session_phase=SESSION_REGULAR,
        jumping=True,
    )
    if held is True:
        kwargs["held_move"] = _held(state, now) if state else None
    elif held is not False:
        kwargs["held_move"] = held
    kwargs.update(over)
    return decide(**kwargs)


def test_the_reason_vocabulary_is_pinned_and_exported():
    assert REASON_MOVE_HELD == "move_card_held"
    assert REASON_NO_CAUSE_WAITING == "no_cause_waiting"
    assert MOVE_HOLD_COMPANY_NEWS_SPACING_SECONDS == 3600
    assert MOVE_HOLD_NEWS_SPACING_SECONDS == 2700
    for name in ("REASON_MOVE_HELD", "REASON_NO_CAUSE_WAITING", "MOVE_HOLD_NEWS_SPACING_SECONDS",
                 "MOVE_HOLD_COMPANY_NEWS_SPACING_SECONDS", "market_confirmation",
                 "JUMP_FLOOR", "JUMP_FLOOR_SESSION_PCT"):
        assert name in um.__all__


# ── the CRWV 10:07 replay: a company card is held against new_articles ──────

def test_crwv_2026_11_23_10_07_company_card_is_held_against_new_articles():
    st = _state(failed=NOW - timedelta(minutes=33))     # the 09:34 refusal, before 09:50
    # What happened: an untagged state regenerates on the re-keyed corpus.
    plain = _decide(state=st, held=False, web_refreshed_at=NOW - timedelta(minutes=33),
                    web_result_count=3)
    assert (plain.action, plain.reason) == (ACTION_GENERATE, "new_articles (9)")
    # The 09:50 card is a same-day move card now: the same inputs keep it, even with a
    # fresh story (published 10:02, newer than the floor, but inside the 60-minute spacing)
    # and the cooldown over.
    d = _decide(state=st, web_refreshed_at=NOW - timedelta(minutes=33), web_result_count=3,
                fresh_news_at=NOW - timedelta(minutes=5))
    assert (d.action, d.reason) == (ACTION_SKIP, REASON_MOVE_HELD)
    assert d.inputset_id == plain.inputset_id and d.price_band == "Extreme"


def test_crwv_the_noon_explainers_break_the_company_hold_once_an_hour_has_passed():
    noon = NOW.replace(hour=12, minute=5)
    st = _state(now=noon, generated=NOW.replace(minute=50, hour=9))
    d = _decide(state=st, now=noon, fresh_news_at=noon - timedelta(minutes=7))
    assert (d.action, d.reason) == (ACTION_GENERATE, "new_articles (9)")


# ── fresh news: 60 minutes for a company card, 45 for market / no-cause ─────

@pytest.mark.parametrize("kind,spacing", [
    (KIND_COMPANY, MOVE_HOLD_COMPANY_NEWS_SPACING_SECONDS),
    (KIND_MARKET, MOVE_HOLD_NEWS_SPACING_SECONDS),
    (KIND_NONE, MOVE_HOLD_NEWS_SPACING_SECONDS),
])
def test_fresh_news_breaks_a_hold_only_after_its_spacing(kind, spacing):
    held_reason = REASON_NO_CAUSE_WAITING if kind == KIND_NONE else REASON_MOVE_HELD
    written = NOW - timedelta(seconds=spacing) + timedelta(seconds=60)
    st = _state(kind=kind, generated=written)
    early = _decide(state=st, fresh_news_at=NOW - timedelta(minutes=1))
    assert (early.action, early.reason) == (ACTION_SKIP, held_reason)

    st = _state(kind=kind, generated=NOW - timedelta(seconds=spacing))
    due = _decide(state=st, fresh_news_at=NOW - timedelta(minutes=1))
    assert (due.action, due.reason) == (ACTION_GENERATE, "new_articles (9)")


@pytest.mark.parametrize("kind", [KIND_MARKET, KIND_NONE])
def test_market_and_no_cause_cards_yield_to_fresh_news_before_a_company_card(kind):
    st = _state(kind=kind, generated=NOW - timedelta(minutes=50))
    assert _decide(state=st, fresh_news_at=NOW - timedelta(minutes=3)).action == ACTION_GENERATE
    company = _state(kind=KIND_COMPANY, generated=NOW - timedelta(minutes=50))
    held = _decide(state=company, fresh_news_at=NOW - timedelta(minutes=3))
    assert (held.action, held.reason) == (ACTION_SKIP, REASON_MOVE_HELD)


@pytest.mark.parametrize("kind", [KIND_COMPANY, KIND_MARKET, KIND_NONE])
def test_news_not_newer_than_the_floor_never_breaks(kind):
    st = _state(kind=kind, generated=NOW - timedelta(hours=3))
    for stale in (NOW - timedelta(hours=3), NOW - timedelta(hours=5), None):
        d = _decide(state=st, fresh_news_at=stale)
        assert d.action == ACTION_SKIP
        assert d.reason in (REASON_MOVE_HELD, REASON_NO_CAUSE_WAITING)


def test_the_news_spacing_counts_from_the_last_attempt_not_the_last_card():
    """A kept or refused attempt moves the floor: fresh wire stories then wait a full
    spacing again, so a run of refusals cannot spend the reserve at the 15-minute
    failure cooldown."""
    st = _state(kind=KIND_NONE, generated=NOW - timedelta(hours=2),
                failed=NOW - timedelta(minutes=20), attempts=4)
    fresh = NOW - timedelta(minutes=10)                   # newer than the failure
    held = _decide(state=st, fresh_news_at=fresh)
    assert (held.action, held.reason) == (ACTION_SKIP, REASON_NO_CAUSE_WAITING)
    later = NOW + timedelta(minutes=25)                   # 45 min after the failure
    d = _decide(state=_state(kind=KIND_NONE, generated=NOW - timedelta(hours=2),
                             failed=NOW - timedelta(minutes=20), attempts=4, now=later),
                now=later, fresh_news_at=fresh)
    assert d.action == ACTION_GENERATE


def test_a_future_fresh_news_stamp_is_a_bad_clock():
    st = _state(kind=KIND_NONE, generated=NOW - timedelta(hours=2))
    d = _decide(state=st, fresh_news_at=NOW + timedelta(hours=1))
    assert (d.action, d.reason) == (ACTION_SKIP, REASON_NO_CAUSE_WAITING)
    # Within clock skew it still counts.
    assert _decide(state=st, fresh_news_at=NOW + timedelta(minutes=2)).action == ACTION_GENERATE


def test_a_string_or_naive_fresh_news_stamp_is_read_like_the_other_stamps():
    st = _state(kind=KIND_MARKET, generated=NOW - timedelta(hours=2))
    iso = (NOW - timedelta(minutes=3)).isoformat()
    naive_utc = (NOW - timedelta(minutes=3)).astimezone(timezone.utc).replace(tzinfo=None)
    assert _decide(state=st, fresh_news_at=iso).action == ACTION_GENERATE
    assert _decide(state=st, fresh_news_at=naive_utc).action == ACTION_GENERATE


# ── what never breaks a hold ─────────────────────────────────────────────────

def test_a_window_rekey_alone_keeps_a_none_card():
    """The corpus lost three stories to the window (a 72 h → 24 h re-key): the fingerprint
    moved, nothing new arrived, so the no-cause card keeps waiting for a cause."""
    full = _corpus(9)
    st = _state(kind=KIND_NONE, generated=NOW - timedelta(hours=2), inputset=_fp(full))
    d = _decide(state=st, corpus=full[:6])
    assert (d.action, d.reason) == (ACTION_SKIP, REASON_NO_CAUSE_WAITING)
    assert d.inputset_id != st["last_inputset_id"]


def test_band_escalation_alone_does_not_break_the_hold():
    unusual = {"changePercentage": 13.0, "marketCap": 4e10}    # z ≈ 2.2
    assert classify_move(13.0, SIGMA, market_cap=4e10) == "Unusual"
    st = _state(band="Unusual", inputset=_fp(CORPUS, quote=unusual),
                generated=NOW - timedelta(minutes=40))
    d = _decide(state=st)                                       # +20.58%: Extreme now
    assert (d.action, d.reason) == (ACTION_SKIP, REASON_MOVE_HELD)
    # Without the tag the same escalation regenerates, exactly as before.
    plain = _decide(state=st, held=False)
    assert plain.action == ACTION_GENERATE and plain.reason.startswith("band Unusual->Extreme")


def test_the_cooldown_ending_is_not_a_breaker():
    st = _state(generated=NOW - timedelta(hours=3))
    assert _decide(state=st).reason == REASON_MOVE_HELD


def test_an_unchanged_fingerprint_still_short_circuits_a_held_card():
    st = _state(inputset=_fp(CORPUS), generated=NOW - timedelta(minutes=30))
    d = _decide(state=st)
    assert (d.action, d.reason) == (ACTION_SKIP, "fingerprint_unchanged")
    st["close_cycle"] = (NOW - timedelta(days=2)).isoformat()
    assert _decide(state=st).action == ACTION_TOUCH


# ── new web pages: break once, then the floor consumes them ──────────────────

def test_new_pages_break_the_hold_once_then_the_floor_consumes_them():
    pages_at = NOW - timedelta(minutes=3)
    st = _state(kind=KIND_NONE, generated=NOW - timedelta(minutes=30))
    d = _decide(state=st, web_refreshed_at=pages_at, web_result_count=3)
    assert (d.action, d.reason) == (ACTION_GENERATE, "new_articles (9) + web (3)")

    # The attempt was KEPT (the writer refused a downgrade → a failure after the pages).
    kept = dict(st, last_failure_at=(pages_at + timedelta(seconds=40)).isoformat(),
                attempts_today=st["attempts_today"] + 1)
    later = NOW + timedelta(minutes=20)
    again = _decide(state=kept, now=later, web_refreshed_at=pages_at, web_result_count=3)
    assert (again.action, again.reason) == (ACTION_SKIP, REASON_NO_CAUSE_WAITING)

    # The attempt SUCCEEDED (a card written after the pages).
    written = dict(st, last_generated_at=(pages_at + timedelta(seconds=40)).isoformat())
    again = _decide(state=written, now=later, web_refreshed_at=pages_at, web_result_count=3)
    assert (again.action, again.reason) == (ACTION_SKIP, REASON_NO_CAUSE_WAITING)

    # A newer set breaks again.
    newer = _decide(state=kept, now=later, web_refreshed_at=later - timedelta(minutes=1),
                    web_result_count=2)
    assert newer.action == ACTION_GENERATE and "web (2)" in newer.reason


def test_consumed_pages_with_an_unchanged_corpus_read_fingerprint_unchanged():
    pages_at = NOW - timedelta(minutes=30)
    st = _state(inputset=_fp(CORPUS), generated=NOW - timedelta(minutes=40),
                failed=NOW - timedelta(minutes=25))
    d = _decide(state=st, web_refreshed_at=pages_at, web_result_count=3)
    assert (d.action, d.reason) == (ACTION_SKIP, "fingerprint_unchanged")
    # Untagged, the same pages still count against the last SUCCESS, as before.
    plain = _decide(state=st, held=False, web_refreshed_at=pages_at, web_result_count=3)
    assert (plain.action, plain.reason) == (ACTION_GENERATE, "web (3)")


@pytest.mark.parametrize("count", [0, None, -1, float("nan"), "x"])
def test_an_empty_page_set_never_breaks_a_hold(count):
    st = _state(kind=KIND_NONE, generated=NOW - timedelta(minutes=30))
    d = _decide(state=st, web_refreshed_at=NOW - timedelta(minutes=1), web_result_count=count)
    assert d.reason == REASON_NO_CAUSE_WAITING


# ── earnings landing ─────────────────────────────────────────────────────────

def test_earnings_landing_breaks_the_hold_once():
    landed = NOW - timedelta(minutes=4)
    st = _state(generated=NOW - timedelta(minutes=40))
    d = _decide(state=st, report_day=True, earnings_reported_at=landed)
    assert d.action == ACTION_GENERATE
    assert "earnings_reported" in d.reason
    # A kept or refused attempt after the landing consumed it.
    kept = dict(st, last_failure_at=(landed + timedelta(minutes=1)).isoformat())
    later = NOW + timedelta(minutes=30)
    again = _decide(state=kept, now=later, report_day=True, earnings_reported_at=landed)
    assert (again.action, again.reason) == (ACTION_SKIP, REASON_MOVE_HELD)


@pytest.mark.parametrize("quote", [None, {}, {"changePercentage": float("nan")},
                                   {"changePercentage": 0.0}, {"changePercentage": 1.0}])
def test_earnings_landing_breaks_the_hold_even_when_the_move_is_gone(quote):
    st = _state(generated=NOW - timedelta(minutes=40))
    d = _decide(state=st, quote=quote, report_day=True,
                earnings_reported_at=NOW - timedelta(minutes=4))
    assert d.action == ACTION_GENERATE and "earnings_reported" in d.reason


# ── reversals ────────────────────────────────────────────────────────────────

PLUG_NOW = datetime(2026, 11, 25, 12, 59, tzinfo=ET)
PLUG_SIGMA = 0.05


def _plug_state(**kw):
    kw.setdefault("now", PLUG_NOW)
    kw.setdefault("direction", "down")
    kw.setdefault("kind", KIND_COMPANY)
    kw.setdefault("band", "Unusual")
    kw.setdefault("generated", PLUG_NOW - timedelta(hours=3))
    return _state(**kw)


def _plug(st, change, **over):
    over.setdefault("now", PLUG_NOW)
    return _decide(state=st, quote={"changePercentage": change, "marketCap": 2e9},
                   sigma=PLUG_SIGMA, **over)


def test_plug_2026_11_25_reversal_regenerates():
    st = _plug_state()
    d = _plug(st, 10.05)                                        # z ≈ 2.01: Unusual
    assert (d.action, d.reason) == (ACTION_GENERATE, "reversal down->up")
    # Untagged, the same state reads the re-keyed corpus as "new_articles".
    assert _plug(st, 10.05, held=False).reason == "new_articles (9)"


def test_plug_reversal_with_an_identical_fingerprint_still_regenerates():
    """The fingerprint drops the sign: −10% and +10% are both "unusual". Without the
    reversal check before the short-circuit the down card would read current."""
    up = {"changePercentage": 10.05, "marketCap": 2e9}
    st = _plug_state(inputset=_fp(CORPUS, quote=up, sigma=PLUG_SIGMA))
    assert _plug(st, 10.05, held=False).reason == "fingerprint_unchanged"
    d = _plug(st, 10.05)
    assert (d.action, d.reason) == (ACTION_GENERATE, "reversal down->up")


def test_plug_12_06_a_notable_reversal_regenerates_and_reports_the_band():
    st = _plug_state()
    d = _plug(st, 5.49, now=PLUG_NOW.replace(hour=12, minute=6))   # z ≈ 1.1: Notable
    assert d.action == ACTION_GENERATE
    assert d.reason == "band Unusual->Notable (+5.49%) + reversal down->up"


@pytest.mark.parametrize("kind", [KIND_COMPANY, KIND_MARKET, KIND_NONE])
def test_a_reversal_breaks_every_kind_and_needs_no_news(kind):
    st = _plug_state(kind=kind)
    d = _plug(st, 7.0, fresh_news_at=None)
    assert d.action == ACTION_GENERATE and "reversal down->up" in d.reason


def test_an_up_card_reverses_down_too():
    st = _state(direction="up", generated=NOW - timedelta(minutes=40))
    d = _decide(state=st, quote={"changePercentage": -9.0, "marketCap": 4e10})
    assert d.action == ACTION_GENERATE and "reversal up->down" in d.reason


@pytest.mark.parametrize("change", [2.0, 4.9, 0.3])
def test_a_sub_notable_opposite_move_is_not_a_reversal(change):
    st = _plug_state(kind=KIND_NONE)
    d = _plug(st, change, fresh_news_at=PLUG_NOW - timedelta(minutes=2),
              web_refreshed_at=PLUG_NOW - timedelta(minutes=2), web_result_count=3)
    assert (d.action, d.reason) == (ACTION_SKIP, REASON_NO_CAUSE_WAITING)


def test_a_floor_move_that_sigma_calls_typical_still_reverses():
    """σ 12%: −10.5% is Typical, but the ≥10% floor counts it as a real move."""
    st = _state(direction="up", generated=NOW - timedelta(minutes=40))
    q = {"changePercentage": -10.5, "marketCap": 4e10}
    assert classify_move(-10.5, 0.12, market_cap=4e10) == "Typical"
    d = _decide(state=st, quote=q, sigma=0.12)
    assert d.action == ACTION_GENERATE and "reversal up->down" in d.reason
    # And a same-way floor move lets fresh news through (it is in move mode).
    st2 = _state(direction="down", kind=KIND_NONE, generated=NOW - timedelta(hours=1))
    assert _decide(state=st2, quote=q, sigma=0.12,
                   fresh_news_at=NOW - timedelta(minutes=2)).action == ACTION_GENERATE


def _rev_state(**kw):
    kw.setdefault("generated", PLUG_NOW - timedelta(hours=2))
    return _plug_state(**kw)


@pytest.mark.parametrize("state,over,why", [
    (lambda: _rev_state(successes=daily_cap_for(False, jumping=True)), {}, "daily_cap"),
    (lambda: _rev_state(successes=5, attempts=24), {}, "attempt_cap"),
    (lambda: _rev_state(generated=PLUG_NOW - timedelta(minutes=5)), {}, "cooldown"),
    (lambda: _rev_state(failed=PLUG_NOW - timedelta(minutes=5), attempts=4), {},
     "failure_cooldown"),
    (lambda: _rev_state(), {"market_change_percent": -8.0}, "mwcb_market_only"),
    (lambda: _rev_state(successes=3), {"session_phase": SESSION_PREMARKET},
     "premarket_reserved"),
])
def test_a_reversal_never_passes_caps_cooldown_or_mwcb(state, over, why):
    d = _plug(state(), 10.05, **over)
    assert (d.action, d.reason) == (ACTION_SKIP, why)


# ── an unknown or faded live move keeps the hold ─────────────────────────────

@pytest.mark.parametrize("quote", [None, {}, {"changePercentage": None},
                                   {"changePercentage": float("nan")},
                                   {"changePercentage": float("inf")},
                                   {"changePercentage": "abc"}, {"changePercentage": True},
                                   {"changePercentage": 0.0}, {"changePercentage": 0.004}])
@pytest.mark.parametrize("kind", [KIND_COMPANY, KIND_NONE])
def test_an_unusable_quote_keeps_the_hold(quote, kind):
    st = _state(kind=kind, generated=NOW - timedelta(hours=2))
    d = _decide(state=st, quote=quote, fresh_news_at=NOW - timedelta(minutes=1),
                web_refreshed_at=NOW - timedelta(minutes=1), web_result_count=3)
    assert d.action == ACTION_SKIP
    assert d.reason == (REASON_NO_CAUSE_WAITING if kind == KIND_NONE else REASON_MOVE_HELD)


def test_a_faded_same_way_move_holds_against_pages_and_news():
    """Move mode needs a live class ≥ Notable: below it a regeneration could only write a
    general card, which the writer refuses as a downgrade after the model call."""
    st = _state(generated=NOW - timedelta(hours=2))
    faded = {"changePercentage": 2.5, "marketCap": 4e10}         # z ≈ 0.4: Typical
    d = _decide(state=st, quote=faded, fresh_news_at=NOW - timedelta(minutes=1),
                web_refreshed_at=NOW - timedelta(minutes=1), web_result_count=3)
    assert (d.action, d.reason) == (ACTION_SKIP, REASON_MOVE_HELD)


# ── the no-cause card waits; the caller ends the wait ────────────────────────

def test_a_none_card_waits_until_the_caller_says_the_search_is_over():
    st = _state(kind=KIND_NONE, generated=NOW - timedelta(minutes=30))
    assert _decide(state=st).reason == REASON_NO_CAUSE_WAITING
    assert _decide(state=st, move_search_exhausted=True).reason == REASON_MOVE_HELD
    for odd in (1, "yes", "True", None, object()):
        assert _decide(state=st, move_search_exhausted=odd).reason == REASON_NO_CAUSE_WAITING


@pytest.mark.parametrize("kind", [KIND_COMPANY, KIND_MARKET])
def test_company_and_market_cards_read_held_whatever_the_search_says(kind):
    st = _state(kind=kind, generated=NOW - timedelta(minutes=30))
    for exhausted in (False, True):
        assert _decide(state=st, move_search_exhausted=exhausted).reason == REASON_MOVE_HELD


def test_the_caps_are_reported_before_the_hold():
    st = _state(successes=daily_cap_for(False, jumping=True))
    assert _decide(state=st).reason == "daily_cap"


# ── scope and tag plumbing ───────────────────────────────────────────────────

def test_the_market_scope_never_holds():
    st = _state(kind=KIND_MARKET, generated=NOW - timedelta(minutes=30))
    kw = dict(scope="__MARKET__", is_market_scope=True, sigma=0.008,
              quote={"changePercentage": 4.7})
    tagged = _decide(state=st, **kw)
    plain = _decide(state=st, held=False, **kw)
    assert tagged == plain and tagged.reason not in (REASON_MOVE_HELD, REASON_NO_CAUSE_WAITING)


def test_a_previous_days_tag_holds_nothing():
    """The caller's `held_move` returns None for yesterday's card, so a new day always
    writes."""
    st = _state(generated=NOW - timedelta(days=1))
    assert _held(st) is None
    assert _decide(state=st).reason == "new_articles (9)"


def test_the_stored_reason_round_trips_into_a_hold():
    st = _state(kind=KIND_MARKET, generated=NOW - timedelta(minutes=30))
    st["last_trigger_reason"] = "move:up:market + web (3)"
    assert _held(st) == MoveTag(kind=KIND_MARKET, direction="up")
    assert _decide(state=st).reason == REASON_MOVE_HELD
    # A tag that does not LEAD is not a move card.
    st["last_trigger_reason"] = "web_only + move:up:market"
    assert _held(st) is None
    assert _decide(state=st).action == ACTION_GENERATE


# ── defaults decide exactly as before ────────────────────────────────────────

def _matrix():
    quotes = [None, JUMP, {"changePercentage": -3.0, "marketCap": 4e10},
              {"changePercentage": 0.2}, {"changePercentage": float("nan")}]
    states = [
        None, {},
        _state(successes=0, generated=None, inputset=None, band=None),
        _state(generated=NOW - timedelta(minutes=5)),
        _state(generated=NOW - timedelta(hours=2), failed=NOW - timedelta(minutes=3)),
        _state(inputset=_fp(CORPUS)),
        _state(successes=20),
        _state(successes=3, attempts=24),
        _state(kind=KIND_NONE, band="Notable"),
    ]
    extras = [
        {},
        {"web_refreshed_at": NOW - timedelta(minutes=2), "web_result_count": 3},
        {"report_day": True, "earnings_reported_at": NOW - timedelta(minutes=8)},
        {"corpus": [], "web_only_ok": True, "web_refreshed_at": NOW - timedelta(minutes=2),
         "web_result_count": 2},
        {"session_phase": SESSION_PREMARKET},
        {"market_change_percent": -7.5},
        {"jumping": False},
    ]
    for q in quotes:
        for st in states:
            for ex in extras:
                yield q, st, ex


def test_untagged_state_decides_exactly_as_before():
    """With ``held_move=None`` the new inputs change nothing, whatever they hold — even
    with a move tag in the state's reason (the gate never parses the state itself)."""
    n = 0
    for quote, st, extra in _matrix():
        base = _decide(state=st, quote=quote, held=False, **extra)
        for fresh in (None, NOW - timedelta(minutes=1), "garbage"):
            for exhausted in (False, True):
                same = _decide(state=st, quote=quote, held=None, fresh_news_at=fresh,
                               move_search_exhausted=exhausted, **extra)
                assert same == base, (quote, st, extra, fresh, exhausted)
                n += 1
    assert n > 1000


@pytest.mark.parametrize("odd", [
    "move:up:company", {"kind": "company", "direction": "up"}, ("company", "up"), 42,
    MoveTag(kind="cause", direction="up"), MoveTag(kind="company", direction="sideways"),
    MoveTag(kind=None, direction=None), object(),
])
def test_an_odd_held_input_is_read_as_no_hold_and_never_raises(odd):
    st = _state(generated=NOW - timedelta(minutes=30))
    assert _decide(state=st, held=odd) == _decide(state=st, held=False)


@pytest.mark.parametrize("fresh", ["x", "", 123, float("nan"), True, object(), [], {}])
@pytest.mark.parametrize("kind", [KIND_COMPANY, KIND_NONE])
def test_odd_fresh_news_never_raises(fresh, kind):
    st = _state(kind=kind, generated=NOW - timedelta(hours=2))
    d = _decide(state=st, fresh_news_at=fresh)
    assert d.reason in (REASON_MOVE_HELD, REASON_NO_CAUSE_WAITING)


@pytest.mark.parametrize("field,value", [
    ("last_failure_at", "not-a-time"), ("last_failure_at", 17), ("last_generated_at", "x"),
    ("last_generated_at", None), ("regen_count_today", None), ("last_price_band", 3),
    ("last_inputset_id", None),
])
def test_odd_state_never_raises(field, value):
    st = _state(kind=KIND_NONE, generated=NOW - timedelta(hours=2))
    st[field] = value
    held = MoveTag(kind=KIND_NONE, direction="up")
    for extra in ({}, {"fresh_news_at": NOW - timedelta(minutes=1)},
                  {"web_refreshed_at": NOW - timedelta(minutes=1), "web_result_count": 2},
                  {"quote": {"changePercentage": -12.0, "marketCap": 4e10}}):
        d = _decide(state=st, held=held, **extra)
        assert d.reason != "gate_error"
        assert d.action in (ACTION_GENERATE, ACTION_SKIP, ACTION_TOUCH)


def test_a_held_decision_carries_the_same_fingerprint_and_band_as_an_untagged_one():
    st = _state(kind=KIND_MARKET, generated=NOW - timedelta(minutes=30))
    held = _decide(state=st)
    plain = _decide(state=st, held=False)
    assert held.action == ACTION_SKIP and plain.action == ACTION_GENERATE
    assert replace(held, action=plain.action, reason=plain.reason, score=plain.score) == plain


# ── an outage consumes no evidence (review 2026-11-27) ───────────────────────

def _unjudged(st, at, error="model call failed: TimeoutError"):
    """The state after an attempt that judged nothing: the sweeper leads `last_error` with
    the unjudged mark (`updates_insight_sweeper._mark_unjudged`)."""
    return dict(st, last_failure_at=at.isoformat(),
                last_error=um.UNJUDGED_FAILURE_PREFIX + error,
                attempts_today=st["attempts_today"] + 1)


def test_an_outage_does_not_consume_the_page_set_that_broke_the_hold():
    """CRWV 11-23 shape: a held no-cause card from 09:50, a 3-page set at 12:00 that explains
    the move, and the model timing out on the 12:05 attempt. The pages get their attempt once
    the failure cooldown is over — before, the outage moved the evidence floor past them and
    the card read "No news yet explains …" for the rest of the day."""
    card_at = NOW.replace(hour=9, minute=50)
    pages_at = NOW.replace(hour=12, minute=0)
    failed = NOW.replace(hour=12, minute=5)
    st = _state(kind=KIND_NONE, generated=card_at, inputset=_fp(CORPUS), now=failed)
    first = _decide(state=st, now=failed, web_refreshed_at=pages_at, web_result_count=3)
    assert (first.action, first.reason) == (ACTION_GENERATE, "web (3)")

    outage = _unjudged(st, failed)
    soon = _decide(state=outage, now=failed + timedelta(minutes=10),
                   web_refreshed_at=pages_at, web_result_count=3)
    assert (soon.action, soon.reason) == (ACTION_SKIP, "failure_cooldown")
    for later in (failed + timedelta(minutes=20), NOW.replace(hour=13, minute=30),
                  NOW.replace(hour=15, minute=30)):
        for exhausted in (False, True):
            d = _decide(state=outage, now=later, web_refreshed_at=pages_at,
                        web_result_count=3, move_search_exhausted=exhausted)
            assert (d.action, d.reason) == (ACTION_GENERATE, "web (3)"), (later, exhausted)

    # A JUDGED refusal still consumes the pages it was given.
    judged = dict(outage, last_error="conclusion_guard: figure $5,000")
    d = _decide(state=judged, now=failed + timedelta(minutes=20),
                web_refreshed_at=pages_at, web_result_count=3)
    assert (d.action, d.reason) == (ACTION_SKIP, "fingerprint_unchanged")


def test_an_outage_does_not_consume_an_earnings_landing():
    landed = NOW - timedelta(minutes=4)
    st = _state(generated=NOW - timedelta(minutes=40))
    outage = _unjudged(st, landed + timedelta(minutes=1))
    later = NOW + timedelta(minutes=30)
    d = _decide(state=outage, now=later, report_day=True, earnings_reported_at=landed)
    assert d.action == ACTION_GENERATE and "earnings_reported" in d.reason
    judged = dict(outage, last_error="price_guard: share price talk")
    again = _decide(state=judged, now=later, report_day=True, earnings_reported_at=landed)
    assert (again.action, again.reason) == (ACTION_SKIP, REASON_MOVE_HELD)


def test_an_outage_leaves_the_fresh_news_spacing_counted_from_the_last_card():
    """A judged attempt restarts the 45-minute news spacing; an outage does not (its retry is
    bounded by the failure cooldown instead)."""
    st = _state(kind=KIND_NONE, generated=NOW - timedelta(hours=2), attempts=4)
    fresh = NOW - timedelta(minutes=10)
    outage = _unjudged(st, NOW - timedelta(minutes=20))
    d = _decide(state=outage, fresh_news_at=fresh)
    assert d.action == ACTION_GENERATE
    judged = dict(outage, last_error="move_kept: none->none")
    held = _decide(state=judged, fresh_news_at=fresh)
    assert (held.action, held.reason) == (ACTION_SKIP, REASON_NO_CAUSE_WAITING)


@pytest.mark.parametrize("value, expected", [
    ("unjudged: model call failed", True),
    ("unjudged: ", True),
    ("unjudged:model call failed", False),
    ("Unjudged: model call failed", False),
    ("conclusion_guard: unjudged: x", False),
    ("", False), (None, False), (b"unjudged: x", False), (1, False), (["unjudged: x"], False),
])
def test_failure_unjudged_is_strict(value, expected):
    assert um.UNJUDGED_FAILURE_PREFIX == "unjudged: "
    assert um.failure_unjudged(value) is expected


def test_the_unjudged_mark_changes_nothing_without_a_held_card():
    """Only a held card's floor reads the mark: every untagged decision is unchanged by it."""
    n = 0
    for quote, st, extra in _matrix():
        if not st:
            continue
        base = _decide(state=st, quote=quote, held=False, **extra)
        marked = dict(st, last_error=um.UNJUDGED_FAILURE_PREFIX + "model call failed")
        assert _decide(state=marked, quote=quote, held=False, **extra) == base
        n += 1
    assert n > 100


def test_an_outage_does_not_spend_a_held_web_only_page_set():
    """A held no-cause card with no FMP story in its move window: the 12:00 pages are its only
    evidence. An outage on their attempt leaves them a retry (past both the evidence floor and
    the web-only one-try bound); a judged refusal consumes them."""
    pages_at = NOW.replace(hour=12, minute=0)
    failed = NOW.replace(hour=12, minute=5)
    st = _state(kind=KIND_NONE, generated=NOW.replace(hour=9, minute=50), now=failed)
    web = dict(corpus=[], web_only_ok=True, web_refreshed_at=pages_at, web_result_count=2)
    first = _decide(state=st, now=failed, **web)
    assert first.action == ACTION_GENERATE and "web_only" in first.reason
    later = failed + timedelta(minutes=20)
    retry = _decide(state=_unjudged(st, failed), now=later, **web)
    assert retry.action == ACTION_GENERATE and "web_only" in retry.reason
    judged = dict(_unjudged(st, failed), last_error="web_only_judge: headline=unsupported")
    d = _decide(state=judged, now=later, **web)
    assert (d.action, d.reason) == (ACTION_SKIP, REASON_NO_CAUSE_WAITING)


# ── a held MARKET card whose S&P 500 turned (integration review 2026-11-27) ──────────

def test_a_held_market_card_breaks_its_hold_when_the_index_turns_against_it():
    """Same corpus, same direction, still moving: only the S&P 500's turn can say the "moves
    with the broader market" sentence stopped holding. The fingerprint short-circuit must not
    hide it (the corpus is unchanged)."""
    st = _state(kind=KIND_MARKET, generated=NOW - timedelta(minutes=30), inputset=_fp(CORPUS))
    held = _decide(state=st)
    assert (held.action, held.reason) == (ACTION_SKIP, "fingerprint_unchanged")
    turned = _decide(state=st, index_against_held=True)
    assert turned.action == ACTION_GENERATE
    assert "market_turned" in turned.reason


@pytest.mark.parametrize("kind", [KIND_COMPANY, KIND_NONE])
def test_index_against_never_breaks_a_company_or_no_cause_card(kind):
    st = _state(kind=kind, generated=NOW - timedelta(minutes=30))
    d = _decide(state=st, index_against_held=True)
    assert d.action == ACTION_SKIP and "market_turned" not in d.reason


@pytest.mark.parametrize("odd", [1, "yes", "True", None, object()])
def test_index_against_must_be_strictly_true(odd):
    st = _state(kind=KIND_MARKET, generated=NOW - timedelta(minutes=30))
    assert _decide(state=st, index_against_held=odd).reason == REASON_MOVE_HELD


def test_a_faded_market_card_is_not_rewritten_when_the_index_turns():
    """Like every breaker but earnings, the turn needs a live material move: below it a rewrite
    could only write a general card."""
    st = _state(kind=KIND_MARKET, generated=NOW - timedelta(hours=2))
    faded = {"changePercentage": 2.5, "marketCap": 4e10}
    d = _decide(state=st, quote=faded, index_against_held=True)
    assert (d.action, d.reason) == (ACTION_SKIP, REASON_MOVE_HELD)


def test_a_turned_market_card_still_respects_the_cooldown_and_caps():
    st = _state(kind=KIND_MARKET, generated=NOW - timedelta(minutes=3))
    assert _decide(state=st, index_against_held=True).action != ACTION_GENERATE
    capped = _state(kind=KIND_MARKET, generated=NOW - timedelta(minutes=30), successes=40,
                    attempts=60)
    assert _decide(state=capped, index_against_held=True).action != ACTION_GENERATE
