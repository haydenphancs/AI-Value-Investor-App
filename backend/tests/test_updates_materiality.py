"""
Materiality gate — the predicate that decides whether an Updates-screen AI
Insights card is worth regenerating.

Every branch is exercised with hostile inputs, because a silent failure here is
expensive in two opposite directions:
  * too permissive → we pay Gemini for byte-identical cards, or hand it an empty
    corpus and it confabulates a market summary;
  * too restrictive → the card goes stale through exactly the crash the user
    opened the app to understand.
"""

import math
from datetime import datetime, timedelta, timezone

import pytest

from app.services.updates_materiality import (
    ACTION_GENERATE,
    ACTION_SKIP,
    ACTION_TOUCH,
    BAND_EXTREME,
    BAND_FLAT,
    BAND_NOTABLE,
    BAND_UNKNOWN,
    COOLDOWN_CLOSED_SECONDS,
    COOLDOWN_SESSION_SECONDS,
    PER_SCOPE_ATTEMPT_CAP,
    PER_SCOPE_DAILY_CAP,
    PER_SCOPE_DAILY_CAP_MARKET,
    daily_cap_for,
    premarket_cap_for,
    compute_inputset_id,
    corpus_article_ids,
    decide,
    finite,
    price_band,
)
from app.utils.market_hours import (
    ET,
    SESSION_AFTERHOURS,
    SESSION_CLOSED,
    SESSION_PREMARKET,
    SESSION_REGULAR,
    session_phase,
)

NOW = datetime(2026, 7, 20, 18, 0, tzinfo=timezone.utc)
CYCLE = NOW - timedelta(hours=3)
LARGE_CAP = 3_000_000_000_000.0
SMALL_CAP = 500_000_000.0
MODEL = "gemini-2.5-flash-lite"

CORPUS = [
    {"external_id": "https://a.example/1", "headline": "A"},
    {"external_id": "https://a.example/2", "headline": "B"},
]


def _decide(**over):
    kwargs = dict(
        scope="AAPL",
        corpus=CORPUS,
        quote={"changePercentage": 0.1, "marketCap": LARGE_CAP},
        state=None,
        market_change_percent=0.1,
        close_cycle_start=CYCLE,
        now=NOW,
        model=MODEL,
        market_active=True,
        is_market_scope=False,
    )
    kwargs.update(over)
    return decide(**kwargs)


# ── finite() ──────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "value,expected",
    [
        (1.5, 1.5), (0, 0.0), (-2, -2.0), ("3.25", 3.25),
        (None, None), ("", None), ("abc", None), ([], None), ({}, None),
        (float("nan"), None), (float("inf"), None), (float("-inf"), None),
        # bools are ints in Python; treating True as 1.0% would be nonsense.
        (True, None), (False, None),
    ],
)
def test_finite_rejects_every_non_number(value, expected):
    assert finite(value) == expected


# ── price_band() ──────────────────────────────────────────────────────

def test_price_band_large_cap_uses_luld_tier1_5pct():
    assert price_band(1.99, LARGE_CAP) == BAND_FLAT
    assert price_band(2.0, LARGE_CAP) == BAND_NOTABLE      # boundary, inclusive
    assert price_band(4.99, LARGE_CAP) == BAND_NOTABLE
    assert price_band(5.0, LARGE_CAP) == BAND_EXTREME      # boundary, inclusive
    assert price_band(12.0, LARGE_CAP) == BAND_EXTREME


def test_price_band_small_cap_uses_luld_tier2_10pct():
    assert price_band(5.0, SMALL_CAP) == BAND_NOTABLE      # not extreme for a small cap
    assert price_band(9.99, SMALL_CAP) == BAND_NOTABLE
    assert price_band(10.0, SMALL_CAP) == BAND_EXTREME


def test_price_band_is_symmetric_for_negative_moves():
    for cap in (LARGE_CAP, SMALL_CAP):
        for pct in (2.0, 5.0, 10.0, 12.0):
            assert price_band(pct, cap) == price_band(-pct, cap)


def test_price_band_missing_market_cap_is_treated_as_small_cap():
    # Unknown cap must NOT get the tighter large-cap band: that would classify a
    # routine 6% move on a micro-cap as "extreme" and bill a regeneration.
    assert price_band(6.0, None) == BAND_NOTABLE
    assert price_band(6.0, 0) == BAND_NOTABLE
    assert price_band(6.0, float("nan")) == BAND_NOTABLE


def test_price_band_unusable_quote_is_unknown_not_flat():
    # "unknown" must be distinguishable from "calm" — mistaking a missing quote
    # for a flat tape is how a real move goes unnoticed.
    for bad in (None, float("nan"), float("inf"), "n/a", ""):
        assert price_band(bad, LARGE_CAP) == BAND_UNKNOWN


def test_price_band_index_scale_is_much_tighter():
    # A 3% S&P day is historic; a 3% single-stock day is a Tuesday.
    assert price_band(0.9, None, is_index=True) == BAND_FLAT
    assert price_band(1.0, None, is_index=True) == BAND_NOTABLE
    assert price_band(3.0, None, is_index=True) == BAND_EXTREME
    assert price_band(3.0, LARGE_CAP, is_index=False) == BAND_NOTABLE


# ── fingerprint ───────────────────────────────────────────────────────

def test_fingerprint_is_order_independent():
    # FMP reordering the same articles is not new news.
    a = compute_inputset_id(["u1", "u2", "u3"], BAND_FLAT, MODEL)
    b = compute_inputset_id(["u3", "u1", "u2"], BAND_FLAT, MODEL)
    assert a == b


def test_fingerprint_changes_with_band_model_prompt_and_articles():
    base = compute_inputset_id(["u1"], BAND_FLAT, MODEL)
    assert base != compute_inputset_id(["u1", "u2"], BAND_FLAT, MODEL)
    assert base != compute_inputset_id(["u1"], BAND_NOTABLE, MODEL)
    assert base != compute_inputset_id(["u1"], BAND_FLAT, "other-model")
    assert base != compute_inputset_id(["u1"], BAND_FLAT, MODEL, prompt_version=2)


def test_fingerprint_ignores_empty_and_null_ids():
    assert compute_inputset_id(["u1", None, ""], BAND_FLAT, MODEL) == \
        compute_inputset_id(["u1"], BAND_FLAT, MODEL)


def test_corpus_ids_prefer_external_id_over_db_id():
    # The DB id is a fresh UUID every 6h cache cycle. Keying the fingerprint on
    # it would make the input set "change" every cycle and defeat the whole
    # design — every scope would regenerate on a timer after all.
    rows = [{"id": "uuid-1", "external_id": "https://x/1"}]
    assert corpus_article_ids(rows) == ["https://x/1"]
    assert corpus_article_ids([{"id": "uuid-1"}]) == ["uuid-1"]
    assert corpus_article_ids([{}, None, "junk"]) == []


# ── decide(): the gate ────────────────────────────────────────────────

def test_empty_corpus_never_reaches_the_model():
    d = _decide(corpus=[])
    assert d.action == ACTION_SKIP
    assert d.reason == "no_corpus"
    # No fingerprint is computed either — there is nothing to fingerprint.
    assert d.inputset_id is None


def test_cold_start_generates():
    d = _decide()
    assert d.action == ACTION_GENERATE
    assert "cold_start" in d.reason
    assert d.inputset_id


def test_unchanged_fingerprint_skips_even_on_a_big_move():
    # The price is baked INTO the fingerprint, so if the digest matches, the
    # band matched too — nothing can have changed.
    first = _decide()
    state = {
        "last_inputset_id": first.inputset_id,
        "last_price_band": first.price_band,
        "close_cycle": CYCLE.isoformat(),
    }
    d = _decide(state=state)
    assert d.action == ACTION_SKIP
    assert d.reason == "fingerprint_unchanged"


def test_band_crossing_regenerates():
    calm = _decide()
    state = {
        "last_inputset_id": calm.inputset_id,
        "last_price_band": calm.price_band,
        "close_cycle": CYCLE.isoformat(),
    }
    crash = _decide(
        state=state,
        quote={"changePercentage": -8.4, "marketCap": LARGE_CAP},
    )
    assert crash.action == ACTION_GENERATE
    assert "extreme" in crash.reason
    assert crash.price_band == BAND_EXTREME
    # Bigger moves must outrank smaller ones for budget admission.
    assert crash.score > calm.score


def test_new_articles_regenerate():
    first = _decide()
    state = {
        "last_inputset_id": first.inputset_id,
        "last_price_band": first.price_band,
        "close_cycle": CYCLE.isoformat(),
    }
    bigger = CORPUS + [{"external_id": "https://a.example/3", "headline": "C"}]
    d = _decide(state=state, corpus=bigger)
    assert d.action == ACTION_GENERATE
    assert "new_articles" in d.reason


def test_close_cycle_ceiling_touches_instead_of_regenerating():
    # Nothing changed, but a new trading day settled: re-stamp freshness for
    # free rather than paying for an identical card.
    first = _decide()
    state = {
        "last_inputset_id": first.inputset_id,
        "last_price_band": first.price_band,
        "close_cycle": (CYCLE - timedelta(days=2)).isoformat(),
    }
    d = _decide(state=state)
    assert d.action == ACTION_TOUCH
    assert d.reason == "cycle_touch"


def test_missing_close_cycle_touches_rather_than_skipping_forever():
    first = _decide()
    d = _decide(state={"last_inputset_id": first.inputset_id, "close_cycle": None})
    assert d.action == ACTION_TOUCH


def test_market_wide_dislocation_suppresses_per_ticker_cards():
    # One macro story, not N restatements of it.
    d = _decide(market_change_percent=-7.5)
    assert d.action == ACTION_SKIP
    assert d.reason == "mwcb_market_only"


def test_market_wide_dislocation_still_regenerates_the_market_card():
    d = _decide(
        scope="__MARKET__",
        is_market_scope=True,
        market_change_percent=-7.5,
        quote={"changePercentage": -7.5},
    )
    assert d.action == ACTION_GENERATE


def test_mwcb_threshold_is_not_tripped_by_a_merely_bad_day():
    assert _decide(market_change_percent=-6.9).action == ACTION_GENERATE


def test_daily_cap_blocks_further_generations():
    d = _decide(state={
        "regen_day": NOW.date().isoformat(),
        "regen_count_today": PER_SCOPE_DAILY_CAP,
    })
    assert d.action == ACTION_SKIP
    assert d.reason == "daily_cap"


def test_the_market_card_gets_a_larger_daily_allowance_than_a_ticker():
    # The market scope backs the default tab, the wire never goes quiet, and it
    # is ONE scope — so the extra spend is ~10 calls/day total, not per ticker.
    assert PER_SCOPE_DAILY_CAP_MARKET > PER_SCOPE_DAILY_CAP
    assert daily_cap_for(is_market_scope=True) == PER_SCOPE_DAILY_CAP_MARKET
    assert daily_cap_for(is_market_scope=False) == PER_SCOPE_DAILY_CAP

    # A ticker's ceiling must not gate the market card.
    d = _decide(
        scope="__MARKET__",
        is_market_scope=True,
        market_change_percent=0.1,
        state={
            "regen_day": NOW.date().isoformat(),
            "regen_count_today": PER_SCOPE_DAILY_CAP,
        },
    )
    assert d.action == ACTION_GENERATE

    d = _decide(
        scope="__MARKET__",
        is_market_scope=True,
        market_change_percent=0.1,
        state={
            "regen_day": NOW.date().isoformat(),
            "regen_count_today": PER_SCOPE_DAILY_CAP_MARKET,
        },
    )
    assert d.action == ACTION_SKIP
    assert d.reason == "daily_cap"


# ── Pre-market reserve ────────────────────────────────────────────────
# The sweeper wakes at 04:00 ET and the wire is busiest overnight-into-morning,
# so without a reserve the whole daily allowance was spent before the opening
# bell. Observed live: __MARKET__ generated for the 6th and last time at 05:49
# ET, then sat frozen through the entire regular session on `daily_cap`.


@pytest.mark.parametrize("is_market", [True, False])
def test_premarket_reserve_leaves_most_of_the_budget_for_the_open(is_market):
    cap = daily_cap_for(is_market)
    reserve = premarket_cap_for(is_market)
    assert 1 <= reserve < cap
    # At least half the allowance must survive to 09:30.
    assert cap - reserve >= cap / 2


@pytest.mark.parametrize("is_market", [True, False])
def test_premarket_blocks_once_the_morning_allowance_is_spent(is_market):
    scope = "__MARKET__" if is_market else "AAPL"
    d = _decide(
        scope=scope,
        is_market_scope=is_market,
        session_phase=SESSION_PREMARKET,
        state={
            "regen_day": NOW.date().isoformat(),
            "regen_count_today": premarket_cap_for(is_market),
        },
    )
    assert d.action == ACTION_SKIP
    assert d.reason == "premarket_reserved"


@pytest.mark.parametrize("is_market", [True, False])
def test_premarket_still_allows_the_first_card_of_the_day(is_market):
    # A cold scope must be able to get its first card before the bell — the
    # reserve floors at 1 precisely so this cannot be starved to zero.
    d = _decide(
        scope="__MARKET__" if is_market else "AAPL",
        is_market_scope=is_market,
        session_phase=SESSION_PREMARKET,
        state={"regen_day": NOW.date().isoformat(), "regen_count_today": 0},
    )
    assert d.action == ACTION_GENERATE


@pytest.mark.parametrize("phase", [SESSION_REGULAR, SESSION_AFTERHOURS])
def test_the_reserve_does_not_apply_once_the_bell_has_rung(phase):
    # After-hours is deliberately exempt: that window is when earnings land,
    # the most material news a ticker has all quarter.
    d = _decide(
        session_phase=phase,
        state={
            "regen_day": NOW.date().isoformat(),
            "regen_count_today": premarket_cap_for(False),
        },
    )
    assert d.action == ACTION_GENERATE


def test_the_reserve_never_outranks_the_hard_daily_cap():
    # Ordering matters: a scope at its daily ceiling in pre-market must report
    # `daily_cap`, the durable reason, not the transient `premarket_reserved`.
    d = _decide(
        session_phase=SESSION_PREMARKET,
        state={
            "regen_day": NOW.date().isoformat(),
            "regen_count_today": PER_SCOPE_DAILY_CAP,
        },
    )
    assert d.reason == "daily_cap"


def test_est_evening_spend_does_not_pre_consume_the_next_premarket_reserve():
    """The daily budget is keyed on the ET trading date, not the UTC date.

    Under EDT the UTC day rolls at 20:00 ET — exactly when the sweeper stops —
    so the two agreed by luck. Under EST it rolls at 19:00 ET, one hour INSIDE
    the sweep window, so the previous evening's after-hours generations landed
    on the same key as the next morning's pre-market and pre-spent its reserve.
    That hour is the earnings window, so the generations most likely to fall
    there are exactly the ones that would starve 04:00-09:30.
    """
    # 2026-12-01 19:30 EST == 2026-12-02T00:30Z. UTC-keyed this is "Dec 2";
    # ET-keyed it is "Dec 1".
    evening = datetime(2026, 12, 2, 0, 30, tzinfo=timezone.utc)
    assert evening.astimezone(ET).date().isoformat() == "2026-12-01"
    assert evening.astimezone(timezone.utc).date().isoformat() == "2026-12-02"

    # Next morning, 2026-12-02 05:00 EST == 10:00Z — pre-market.
    morning = datetime(2026, 12, 2, 10, 0, tzinfo=timezone.utc)
    assert morning.astimezone(ET).date().isoformat() == "2026-12-02"

    # A state row stamped by the previous evening must NOT gate the morning.
    d = _decide(
        now=morning,
        close_cycle_start=morning - timedelta(hours=3),
        session_phase=SESSION_PREMARKET,
        state={
            "regen_day": evening.astimezone(ET).date().isoformat(),
            "regen_count_today": PER_SCOPE_DAILY_CAP,
        },
    )
    assert d.action == ACTION_GENERATE, (
        "yesterday-ET spend must not consume today's pre-market reserve"
    )


def test_the_trading_day_key_rolls_at_et_midnight_not_at_19_or_20_et():
    """A ceiling that resets mid-session is not a ceiling: under the old UTC key
    every scope silently received a second full allowance at 19:00 EST."""
    # 18:59 and 19:30 EST on the same evening must share one key.
    before = datetime(2026, 12, 1, 23, 59, tzinfo=timezone.utc)   # 18:59 EST
    after = datetime(2026, 12, 2, 0, 30, tzinfo=timezone.utc)     # 19:30 EST
    assert before.astimezone(ET).date() == after.astimezone(ET).date()
    # ...which the UTC key did not.
    assert before.astimezone(timezone.utc).date() != after.astimezone(timezone.utc).date()

    d = _decide(
        now=after,
        close_cycle_start=after - timedelta(hours=3),
        session_phase=SESSION_AFTERHOURS,
        state={
            "regen_day": before.astimezone(ET).date().isoformat(),
            "regen_count_today": PER_SCOPE_DAILY_CAP,
        },
    )
    assert d.action == ACTION_SKIP
    assert d.reason == "daily_cap"


def test_yesterdays_premarket_spend_does_not_gate_today():
    d = _decide(
        session_phase=SESSION_PREMARKET,
        state={
            "regen_day": (NOW - timedelta(days=1)).date().isoformat(),
            "regen_count_today": 99,
        },
    )
    assert d.action == ACTION_GENERATE


def test_attempt_cap_blocks_a_failing_scope():
    d = _decide(state={
        "regen_day": NOW.date().isoformat(),
        "attempts_today": PER_SCOPE_ATTEMPT_CAP,
    })
    assert d.action == ACTION_SKIP
    assert d.reason == "attempt_cap"


def test_caps_reset_on_a_new_day():
    # Yesterday's exhausted counters must not carry over.
    d = _decide(state={
        "regen_day": (NOW - timedelta(days=1)).date().isoformat(),
        "regen_count_today": PER_SCOPE_DAILY_CAP,
        "attempts_today": PER_SCOPE_ATTEMPT_CAP,
    })
    assert d.action == ACTION_GENERATE


def test_cooldown_blocks_a_rapid_second_generation():
    d = _decide(state={
        "last_generated_at": (NOW - timedelta(seconds=60)).isoformat(),
    })
    assert d.action == ACTION_SKIP
    assert d.reason == "cooldown"


def test_cooldown_expires():
    d = _decide(state={
        "last_generated_at": (
            NOW - timedelta(seconds=COOLDOWN_SESSION_SECONDS + 1)
        ).isoformat(),
    })
    assert d.action == ACTION_GENERATE


def test_cooldown_is_longer_when_the_market_is_closed():
    just_past_session = NOW - timedelta(seconds=COOLDOWN_SESSION_SECONDS + 60)
    state = {"last_generated_at": just_past_session.isoformat()}
    assert _decide(state=state, market_active=True).action == ACTION_GENERATE
    assert _decide(state=state, market_active=False).action == ACTION_SKIP
    assert COOLDOWN_CLOSED_SECONDS > COOLDOWN_SESSION_SECONDS


def test_future_last_generated_at_does_not_spin():
    # Clock skew between instances must not read as "generated long ago".
    d = _decide(state={
        "last_generated_at": (NOW + timedelta(hours=1)).isoformat(),
    })
    assert d.action == ACTION_SKIP
    assert d.reason == "cooldown"


# ── hostile inputs ────────────────────────────────────────────────────

@pytest.mark.parametrize("bad_quote", [
    {"changePercentage": float("nan"), "marketCap": LARGE_CAP},
    {"changePercentage": float("inf")},
    {"changePercentage": None},
    {},
])
def test_unusable_quote_still_decides_on_news_alone(bad_quote):
    # A broken quote must not crash the sweep, and must not silently freeze the
    # card either — news changes alone are still a valid trigger.
    d = _decide(quote=bad_quote)
    assert d.action == ACTION_GENERATE
    assert d.price_band == BAND_UNKNOWN


@pytest.mark.parametrize("bad_state", [
    {"regen_count_today": "not-a-number"},
    {"last_generated_at": "garbage-timestamp"},
    {"close_cycle": 12345},
    {"regen_day": object()},
])
def test_malformed_state_degrades_to_a_reason_never_an_exception(bad_state):
    d = _decide(state=bad_state)
    assert d.action in (ACTION_GENERATE, ACTION_SKIP, ACTION_TOUCH)
    assert d.reason


def test_a_raising_input_degrades_to_gate_error():
    # decide() must never propagate: one malformed FMP row cannot be allowed to
    # break the whole sweep or 500 the Updates screen.
    d = _decide(quote="not-a-dict")
    assert d.action == ACTION_SKIP
    assert d.reason == "gate_error"


def test_corpus_with_non_dict_rows_is_survivable():
    d = _decide(corpus=[{"external_id": "u1"}, None, "junk", 42])
    assert d.action == ACTION_GENERATE


def test_every_decision_carries_a_reason():
    # The reason string is persisted and is the whole debuggability story.
    cases = [
        _decide(corpus=[]),
        _decide(),
        _decide(quote="not-a-dict"),
        _decide(market_change_percent=-9.0),
        _decide(state={"regen_day": NOW.date().isoformat(),
                       "regen_count_today": PER_SCOPE_DAILY_CAP}),
    ]
    for d in cases:
        assert d.reason and isinstance(d.reason, str)


# ── regressions found by the adversarial review ───────────────────────

def test_missing_quote_does_not_fabricate_a_change_event():
    """An absent price signal must be NEUTRAL to the gate, not a change.

    The band feeds the fingerprint, so letting `flat -> unknown` through re-keys
    the digest: a byte-identical corpus gets reported as "new_articles", and a
    second time on the way back. A universe-wide quote hiccup would cost roughly
    two generations per scope for nothing.
    """
    first = _decide()
    state = {
        "last_inputset_id": first.inputset_id,
        "last_price_band": first.price_band,
        "close_cycle": CYCLE.isoformat(),
    }
    # Same articles, but the quote went missing.
    for bad in ({}, {"changePercentage": None}, {"changePercentage": float("nan")}):
        d = _decide(state=state, quote=bad)
        assert d.action == ACTION_SKIP, f"{bad} triggered {d.reason}"
        assert d.reason == "fingerprint_unchanged"


def test_unknown_band_on_a_cold_scope_still_generates():
    # The fallback must not block a genuine first generation.
    d = _decide(state=None, quote={})
    assert d.action == ACTION_GENERATE
