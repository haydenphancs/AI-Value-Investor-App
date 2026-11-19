"""Jump mode in the Insights materiality gate (2026-10-28).

TestFlight, ORCL, Wed 2026-10-28: the card spent its tenth refresh at 13:24 ET on
ordinary news, the stock jumped ~12% from ~14:15 on its Investor Day, the press
release reached the news cache at 14:37 — and the card could not refresh for the
rest of the day (`daily_cap`). These tests pin the pure levers that fix it, all
no-ops at their defaults:

* `jump_band` — the one definition of JUMPING (an Unusual/Extreme move in the
  CURRENT session, never the market scope, never a stale band);
* ``jumping`` ADDS `JUMP_RESERVE` to the daily and attempt caps (never the market's);
* ``web_refreshed_at`` regenerates a card written before the jump's web search found
  a new set of pages — once, through the fingerprint, never through a cap or cooldown;
* `mwcb_in_force` — a market-wide dislocation suspends every per-ticker card.
"""

import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services.updates_materiality import (
    ACTION_GENERATE,
    ACTION_SKIP,
    AFTER_CLOSE_EARNINGS_RESERVE,
    BAND_EXTREME,
    COOLDOWN_SESSION_SECONDS,
    JUMP_RESERVE,
    JUMP_TIERS,
    PER_SCOPE_DAILY_CAP,
    PER_SCOPE_DAILY_CAP_MARKET,
    PER_SCOPE_FAILURE_ALLOWANCE,
    PROMPT_VERSION,
    TIER_EXTREME,
    TIER_UNUSUAL,
    attempt_cap_for,
    canonical_band,
    classify_move,
    compute_inputset_id,
    corpus_article_ids,
    daily_cap_for,
    decide,
    jump_band,
    mwcb_in_force,
    premarket_cap_for,
)
from app.utils.market_hours import SESSION_AFTERHOURS, SESSION_REGULAR

_ET = ZoneInfo("America/New_York")
MODEL = "gemini-2.5-flash-lite"
CORPUS = [
    {"external_id": "https://a.example/1", "headline": "Oracle Creates AI Workspace"},
    {"external_id": "https://a.example/2", "headline": "Oracle expands multicloud"},
]
NOW = datetime(2026, 10, 28, 14, 30, tzinfo=_ET)   # Wed, regular session
DAY = "2026-10-28"
JUMP_QUOTE = {"changePercentage": 12.04, "marketCap": 4e11}
CALM_QUOTE = {"changePercentage": 0.4, "marketCap": 4e11}


def _decide(*, quote=CALM_QUOTE, state=None, sigma=0.03, **over):
    kwargs = dict(
        scope="ORCL",
        corpus=CORPUS,
        quote=quote,
        state=state,
        market_change_percent=0.3,
        close_cycle_start=NOW - timedelta(hours=20),
        now=NOW,
        model=MODEL,
        market_active=True,
        is_market_scope=False,
        sigma_daily=sigma,
        session_phase=SESSION_REGULAR,
    )
    kwargs.update(over)
    return decide(**kwargs)


def _state(*, successes=0, attempts=None, last_generated_at=None, band=None,
           quote=CALM_QUOTE, sigma=0.03):
    """A same-day state row; with ``band`` set, its fingerprint equals the one the
    gate will compute for CORPUS and ``quote`` (an unchanged-inputs state)."""
    st = {
        "regen_day": DAY,
        "regen_count_today": successes,
        "attempts_today": successes if attempts is None else attempts,
        "close_cycle": NOW.isoformat(),
    }
    if last_generated_at is not None:
        st["last_generated_at"] = last_generated_at.isoformat()
    if band is not None:
        live = classify_move(quote["changePercentage"], sigma, market_cap=quote["marketCap"])
        st["last_inputset_id"] = compute_inputset_id(
            corpus_article_ids(CORPUS), canonical_band(live), MODEL, PROMPT_VERSION,
        )
        st["last_price_band"] = live
    return st


# ── jump_band: the one definition ──────────────────────────────────────

@pytest.mark.parametrize("change", [None, float("nan"), float("inf"), -math.inf, "abc", True, False])
def test_an_unusable_session_change_is_never_a_jump(change):
    assert jump_band(change, 0.03, 4e11, is_market_scope=False) is None


@pytest.mark.parametrize("change", [0.0, 0.004, -0.004, "0.001"])
def test_a_move_that_rounds_to_zero_is_never_a_jump(change):
    # σ tiny enough that the z would be huge: the rounding guard, not the tier, decides.
    assert jump_band(change, 0.00001, 4e11, is_market_scope=False) is None


def test_the_market_scope_never_jumps():
    assert jump_band(12.0, 0.01, None, is_market_scope=True) is None


@pytest.mark.parametrize("change,expected", [
    (12.04, TIER_EXTREME),    # z = 4.0
    (-12.04, TIER_EXTREME),   # sign does not matter
    (7.0, TIER_UNUSUAL),      # z ≈ 2.33
    (4.0, None),              # z ≈ 1.33: Notable is not a jump
    (1.0, None),              # Typical
])
def test_the_sigma_path(change, expected):
    assert jump_band(change, 0.03, 4e11, is_market_scope=False) == expected


@pytest.mark.parametrize("change,cap,expected", [
    (6.0, 4e11, BAND_EXTREME),   # ≥5% for a ≥$10B name
    (4.0, 4e11, None),           # notable only
    (6.0, 1e9, None),            # a small cap needs 10%
    (11.0, 1e9, BAND_EXTREME),
    (11.0, None, BAND_EXTREME),  # unknown cap → the 10% tier
])
@pytest.mark.parametrize("sigma", [None, 0.0, -0.02, float("nan")])
def test_the_fixed_band_fallback_without_sigma(change, cap, expected, sigma):
    assert jump_band(change, sigma, cap, is_market_scope=False) == expected


def test_jump_tiers_are_exactly_the_alert_tiers():
    assert set(JUMP_TIERS) == {TIER_UNUSUAL, TIER_EXTREME, BAND_EXTREME}


# ── MWCB ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mkt,expected", [
    (7.0, True), (-7.5, True), (6.99, False), (0.0, False),
    (None, False), (float("nan"), False), ("x", False),
])
def test_mwcb_in_force(mkt, expected):
    assert mwcb_in_force(mkt) is expected


def test_mwcb_suspends_a_jumping_ticker():
    d = _decide(quote=JUMP_QUOTE, state=_state(), jumping=True, market_change_percent=-8.0)
    assert (d.action, d.reason) == (ACTION_SKIP, "mwcb_market_only")


# ── the reserve: both caps, never the market ─────────────────────────────

def test_the_jump_reserve_adds_six_to_the_ticker_caps():
    assert JUMP_RESERVE == 6
    assert daily_cap_for(False, jumping=True) == PER_SCOPE_DAILY_CAP + JUMP_RESERVE == 16
    assert attempt_cap_for(False, jumping=True) == 16 + PER_SCOPE_FAILURE_ALLOWANCE == 20
    assert daily_cap_for(False) == PER_SCOPE_DAILY_CAP   # the default is unchanged


def test_the_market_scope_never_gets_the_reserve():
    assert daily_cap_for(True, jumping=True) == PER_SCOPE_DAILY_CAP_MARKET
    assert attempt_cap_for(True, jumping=True) == (
        PER_SCOPE_DAILY_CAP_MARKET + PER_SCOPE_FAILURE_ALLOWANCE
    )


def test_the_reserve_stacks_with_the_earnings_boosts():
    assert daily_cap_for(False, earnings_window=True, jumping=True) == (
        PER_SCOPE_DAILY_CAP_MARKET + JUMP_RESERVE
    )
    assert daily_cap_for(False, earnings_window=True, report_day=True, jumping=True) == (
        PER_SCOPE_DAILY_CAP_MARKET + AFTER_CLOSE_EARNINGS_RESERVE + JUMP_RESERVE
    )


def test_the_premarket_hold_is_unchanged():
    # Pre-market changes are blanked by the sweep (prior-session stamps), so a jump is
    # a regular-session event; the pre-market hold keeps its ordinary size.
    assert premarket_cap_for(False) == PER_SCOPE_DAILY_CAP // 3


def test_orcl_capped_at_ten_regenerates_only_while_jumping():
    capped = _state(successes=10, last_generated_at=NOW - timedelta(minutes=66))
    still_capped = _decide(quote=JUMP_QUOTE, state=capped, jumping=False)
    assert (still_capped.action, still_capped.reason) == (ACTION_SKIP, "daily_cap")

    jump = _decide(quote=JUMP_QUOTE, state=capped, jumping=True)
    assert jump.action == ACTION_GENERATE
    assert "jump_reserve" in jump.reason
    assert jump.price_band == TIER_EXTREME


def test_the_reserve_runs_out():
    spent = _state(successes=16, last_generated_at=NOW - timedelta(minutes=30))
    d = _decide(quote=JUMP_QUOTE, state=spent, jumping=True)
    assert (d.action, d.reason) == (ACTION_SKIP, "daily_cap")


def test_failures_hit_the_widened_attempt_cap():
    failing = _state(successes=12, attempts=20, last_generated_at=NOW - timedelta(minutes=30))
    d = _decide(quote=JUMP_QUOTE, state=failing, jumping=True)
    assert (d.action, d.reason) == (ACTION_SKIP, "attempt_cap")


def test_no_jump_reserve_token_below_the_ordinary_cap():
    d = _decide(quote=JUMP_QUOTE, state=_state(successes=3), jumping=True)
    assert d.action == ACTION_GENERATE
    assert "jump_reserve" not in d.reason


def test_a_pending_reporter_spends_the_jump_reserve_but_holds_the_after_close_six():
    # Report day, results not in: the session may use the ordinary 10 + the jump 6;
    # the after-close 6 stay held for the release.
    kwargs = dict(quote=JUMP_QUOTE, jumping=True, report_day=True, earnings_pending_today=True)
    ok = _decide(state=_state(successes=12, last_generated_at=NOW - timedelta(minutes=30)),
                 **kwargs)
    assert ok.action == ACTION_GENERATE
    held = _decide(state=_state(successes=16, last_generated_at=NOW - timedelta(minutes=30)),
                   **kwargs)
    assert (held.action, held.reason) == (ACTION_SKIP, "earnings_reserved")


def test_the_after_close_six_need_the_reserve_to_outlive_the_move():
    """Review 2026-10-29: a reporter that spent the jump six in the session keeps the
    after-close six only while `jumping` stays true once the move has faded — so the sweeper
    latches the reserve for the ET day (`InsightSweeper._reserve_jumpers`). Read off the live
    move, the release card was lost (`daily_cap`)."""
    after_close = NOW.replace(hour=16, minute=20)
    kwargs = dict(
        state=_state(successes=16, last_generated_at=after_close - timedelta(minutes=40)),
        report_day=True, earnings_pending_today=False,
        earnings_reported_at=after_close - timedelta(minutes=10),
        now=after_close, session_phase=SESSION_AFTERHOURS,
    )
    assert _decide(jumping=True, **kwargs).action == ACTION_GENERATE
    assert _decide(jumping=False, **kwargs).reason == "daily_cap"


def test_a_capped_scope_with_an_unchanged_fingerprint_reports_its_cap_to_a_web_question():
    """Review 2026-10-29: the sweeper asks whether NEW pages could regenerate the card
    (`web_refreshed_at=now`) before it buys a search. Without a web input the fingerprint
    answers first and hides the cap."""
    capped = _state(successes=16, last_generated_at=NOW - timedelta(minutes=40), band=True,
                    quote=JUMP_QUOTE)
    assert _decide(quote=JUMP_QUOTE, state=capped, jumping=True).reason == "fingerprint_unchanged"
    asked = _decide(quote=JUMP_QUOTE, state=capped, jumping=True,
                    web_refreshed_at=NOW, web_result_count=1)
    assert (asked.action, asked.reason) == (ACTION_SKIP, "daily_cap")


# ── the web trigger ───────────────────────────────────────────────────────

def test_new_web_pages_regenerate_an_unchanged_card_once():
    written = NOW - timedelta(minutes=20)
    st = _state(successes=4, last_generated_at=written, band=True)
    # Without web: the fingerprint proves the card current.
    assert _decide(state=st).reason == "fingerprint_unchanged"
    # Pages found after the card was written: regenerate, and say why.
    d = _decide(state=st, web_refreshed_at=NOW - timedelta(minutes=2), web_result_count=3)
    assert d.action == ACTION_GENERATE
    assert d.reason == "web (3)"
    # Pages found BEFORE the card was written are already in it.
    old = _decide(state=st, web_refreshed_at=written - timedelta(minutes=1), web_result_count=3)
    assert old.reason == "fingerprint_unchanged"


@pytest.mark.parametrize("count", [0, None, -2, float("nan"), "x"])
def test_an_empty_web_set_is_not_a_trigger(count):
    st = _state(successes=4, last_generated_at=NOW - timedelta(minutes=20), band=True)
    d = _decide(state=st, web_refreshed_at=NOW - timedelta(minutes=2), web_result_count=count)
    assert d.reason == "fingerprint_unchanged"


def test_the_web_trigger_never_passes_the_cap_or_the_cooldown():
    capped = _state(successes=10, last_generated_at=NOW - timedelta(minutes=20), band=True)
    d = _decide(state=capped, web_refreshed_at=NOW - timedelta(minutes=2), web_result_count=3)
    assert (d.action, d.reason) == (ACTION_SKIP, "daily_cap")

    recent = _state(successes=4, last_generated_at=NOW - timedelta(minutes=5), band=True)
    d = _decide(state=recent, web_refreshed_at=NOW - timedelta(minutes=2), web_result_count=3)
    assert (d.action, d.reason) == (ACTION_SKIP, "cooldown")
    assert COOLDOWN_SESSION_SECONDS == 900


def test_new_articles_and_new_pages_report_both():
    st = _state(successes=4, last_generated_at=NOW - timedelta(minutes=20), band=True)
    st["last_inputset_id"] = "an-older-corpus"
    d = _decide(state=st, web_refreshed_at=NOW - timedelta(minutes=2), web_result_count=2)
    assert d.reason == "new_articles (2) + web (2)"


def test_the_market_scope_ignores_web_and_jump_inputs():
    st = _state(successes=4, last_generated_at=NOW - timedelta(minutes=20))
    st["last_inputset_id"] = compute_inputset_id(
        corpus_article_ids(CORPUS), canonical_band(classify_move(0.4, 0.008, is_index=True)),
        MODEL, PROMPT_VERSION,
    )
    d = _decide(scope="__MARKET__", is_market_scope=True, quote={"changePercentage": 0.4},
                sigma=0.008, state=st, jumping=True,
                web_refreshed_at=NOW - timedelta(minutes=2), web_result_count=3)
    assert d.reason == "fingerprint_unchanged"


def test_a_naive_web_timestamp_is_read_as_utc():
    written = NOW - timedelta(minutes=20)
    st = _state(successes=4, last_generated_at=written, band=True)
    naive_after = (NOW - timedelta(minutes=2)).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    d = _decide(state=st, web_refreshed_at=naive_after, web_result_count=1)
    assert d.reason == "web (1)"


# ── web-only cards (2026-11-12, JOBY) ─────────────────────────────────────
# JOBY, Wed 2026-11-12: −8% as an Unusual mover, newest FMP story 66 h old, so the 48 h
# window was empty, the gate said `no_corpus` and the jump's web search was never bought.
# A LIVE equity jumper (`web_only_ok`, the sweeper's call) holding kept pages may now get a
# card from those pages alone — always tagged `web_only`, once per page set, and at most
# `WEB_ONLY_FAILURE_CAP` failures a day so the article path keeps its attempts.

from app.services.updates_materiality import (  # noqa: E402
    WEB_ONLY_FAILURE_CAP,
    WEB_ONLY_REASON,
)

PAGES_AT = NOW - timedelta(minutes=2)


def _web_only(**over):
    kw = dict(corpus=[], quote=JUMP_QUOTE, jumping=True, web_only_ok=True,
              web_refreshed_at=PAGES_AT, web_result_count=3)
    kw.update(over)
    return _decide(**kw)


def _empty_fp(quote=JUMP_QUOTE, sigma=0.03):
    live = classify_move(quote["changePercentage"], sigma, market_cap=quote["marketCap"])
    return compute_inputset_id([], canonical_band(live), MODEL, PROMPT_VERSION)


def test_the_web_only_reason_matches_the_read_sides_mark():
    from app.services import news_insight_service as nis
    assert WEB_ONLY_REASON == nis.WEB_ONLY_REASON == "web_only"
    d = _web_only()
    row = {"trigger_reason": d.reason,
           "sources": [{"title": "Joby falls", "origin": "web", "url": "https://x.example/a"}]}
    assert nis.is_web_only_row(row)


def test_an_empty_corpus_with_pages_generates_a_web_only_card_on_a_cold_start():
    d = _web_only()
    assert d.action == ACTION_GENERATE
    assert d.reason == "cold_start + web_only + web (3)"
    assert "new_articles" not in d.reason
    assert d.score > 0


def test_a_band_change_still_carries_the_web_only_tag():
    st = _state(successes=1, last_generated_at=NOW - timedelta(hours=2))
    st["last_inputset_id"] = "an-older-corpus"
    st["last_price_band"] = "Notable"
    d = _web_only(state=st)
    assert d.action == ACTION_GENERATE
    assert d.reason.startswith("band Notable->")
    assert WEB_ONLY_REASON in d.reason and "new_articles" not in d.reason


def test_web_only_without_a_web_trigger_is_tagged_web_only_never_new_articles_0():
    """The articles aged out after a card that already used these pages: the fingerprint
    moves (the corpus emptied) but the pages are not new. Still never "new_articles (0)"."""
    st = _state(successes=1, last_generated_at=NOW - timedelta(minutes=30))
    st["last_inputset_id"] = "a-corpus-with-articles"
    d = _web_only(state=st, web_refreshed_at=NOW - timedelta(hours=1))
    assert (d.action, d.reason) == (ACTION_GENERATE, WEB_ONLY_REASON)


def test_the_web_only_fingerprint_holds_no_web_data():
    a = _web_only()
    b = _web_only(web_result_count=7, web_refreshed_at=NOW - timedelta(minutes=9))
    assert a.inputset_id == b.inputset_id == _empty_fp()


def test_a_written_web_only_card_is_not_rewritten_from_the_same_pages():
    st = _state(successes=1, last_generated_at=PAGES_AT + timedelta(seconds=30))
    st["last_inputset_id"] = _empty_fp()
    d = _web_only(state=st)
    assert (d.action, d.reason) == (ACTION_SKIP, "fingerprint_unchanged")


@pytest.mark.parametrize("count", [0, None, -1, float("nan"), float("inf"), "x", True])
def test_no_kept_pages_is_still_no_corpus(count):
    d = _web_only(web_result_count=count)
    assert (d.action, d.reason) == (ACTION_SKIP, "no_corpus")


@pytest.mark.parametrize("at", [None, "not-a-time", 12345, ""])
def test_an_unparseable_page_time_is_still_no_corpus(at):
    d = _web_only(web_refreshed_at=at)
    assert (d.action, d.reason) == (ACTION_SKIP, "no_corpus")


@pytest.mark.parametrize("ok", [False, None, 1, "yes", "True"])
def test_only_a_strict_true_web_only_ok_lets_an_empty_corpus_through(ok):
    d = _web_only(web_only_ok=ok)
    assert (d.action, d.reason) == (ACTION_SKIP, "no_corpus")


def test_the_market_scope_never_gets_a_web_only_card():
    d = _web_only(scope="__MARKET__", is_market_scope=True,
                  quote={"changePercentage": -3.0}, sigma=0.008)
    assert (d.action, d.reason) == (ACTION_SKIP, "no_corpus")


def test_a_none_corpus_is_read_as_empty():
    assert _web_only(corpus=None).action == ACTION_GENERATE
    assert _decide(corpus=None).reason == "no_corpus"


def test_web_only_ok_never_changes_a_decision_with_articles():
    st = _state(successes=4, last_generated_at=NOW - timedelta(minutes=20))
    st["last_inputset_id"] = "an-older-corpus"
    with_flag = _decide(state=st, web_only_ok=True, web_refreshed_at=PAGES_AT,
                        web_result_count=2)
    without = _decide(state=st, web_refreshed_at=PAGES_AT, web_result_count=2)
    assert with_flag == without
    assert with_flag.reason == "new_articles (2) + web (2)"


def test_a_page_set_that_failed_is_not_retried():
    failed = PAGES_AT + timedelta(minutes=1)
    st = _state(successes=0, attempts=1)
    st["last_failure_at"] = failed.isoformat()
    # Twenty minutes on: past the failure cooldown, so only the one-shot rule holds it.
    d = _web_only(state=st, now=failed + timedelta(minutes=20))
    assert (d.action, d.reason) == (ACTION_SKIP, "web_only_retried")
    # A failure at the very instant the pages were found counts as their try too.
    st["last_failure_at"] = PAGES_AT.isoformat()
    assert _web_only(state=st, now=NOW + timedelta(minutes=20)).reason == "web_only_retried"


def test_a_new_page_set_after_a_failure_gets_its_own_try():
    failed_at = NOW - timedelta(minutes=30)
    st = _state(successes=0, attempts=1)
    st["last_failure_at"] = failed_at.isoformat()
    d = _web_only(state=st, web_refreshed_at=failed_at + timedelta(minutes=10))
    assert d.action == ACTION_GENERATE and WEB_ONLY_REASON in d.reason


def test_a_failure_before_the_pages_were_found_does_not_block_them():
    st = _state(successes=0, attempts=1)
    st["last_failure_at"] = (PAGES_AT - timedelta(hours=1)).isoformat()
    assert _web_only(state=st).action == ACTION_GENERATE


@pytest.mark.parametrize("bad", ["garbage", None, ""])
def test_an_unreadable_failure_time_does_not_block_the_pages(bad):
    st = _state(successes=0, attempts=1)
    st["last_failure_at"] = bad
    assert _web_only(state=st).action == ACTION_GENERATE


def test_two_failures_a_day_stop_the_web_only_path_but_not_the_articles():
    assert WEB_ONLY_FAILURE_CAP == 2
    assert WEB_ONLY_FAILURE_CAP < PER_SCOPE_FAILURE_ALLOWANCE
    st = _state(successes=1, attempts=1 + WEB_ONLY_FAILURE_CAP)
    st["last_failure_at"] = (PAGES_AT - timedelta(hours=1)).isoformat()   # an older set
    d = _web_only(state=st)
    assert (d.action, d.reason) == (ACTION_SKIP, "web_only_failure_cap")
    # The same state with a real FMP story later that day: the article path still has
    # its attempt budget.
    later = _decide(state=st, quote=JUMP_QUOTE, jumping=True, web_only_ok=True,
                    web_refreshed_at=PAGES_AT, web_result_count=3)
    assert later.action == ACTION_GENERATE
    assert WEB_ONLY_REASON not in later.reason


def test_one_failure_leaves_the_web_only_path_open():
    st = _state(successes=2, attempts=3)
    assert _web_only(state=st).action == ACTION_GENERATE


def test_yesterdays_failures_do_not_count_today():
    st = _state(successes=0, attempts=4)
    st["regen_day"] = "2026-10-27"
    assert _web_only(state=st).action == ACTION_GENERATE


def test_a_counter_skew_with_more_successes_than_attempts_leaves_the_path_open():
    """Successes above attempts (a counter skew) must not close the web-only path. This
    pins the OUTCOME only: the `max(0, …)` clamp in the gate is cosmetic (a negative count
    is never >= the cap), and removing it changes nothing here — by design."""
    st = _state(successes=5, attempts=1)
    assert _web_only(state=st).action == ACTION_GENERATE


def test_the_cap_and_the_cooldown_still_bind_a_web_only_card():
    capped = _state(successes=PER_SCOPE_DAILY_CAP + JUMP_RESERVE)
    assert _web_only(state=capped).reason == "daily_cap"
    recent = _state(successes=1, last_generated_at=NOW - timedelta(minutes=5))
    recent["last_inputset_id"] = "an-older-corpus"
    assert _web_only(state=recent).reason == "cooldown"


def test_an_mwcb_day_still_suspends_a_web_only_card():
    assert _web_only(market_change_percent=-8.0).reason == "mwcb_market_only"
