"""An unmeasurable price arm must not vote — 50 is a claim, not an absence.

Both price helpers returned **50** for empty input. 50 is the exact middle of the scale, so
`_score_to_mood(50)` renders a confident "Neutral", and the unmeasured arm then carried
30-45% of the combined weight while asserting a fact. Pulling every real reading toward the
middle is the direction that most often flips Bullish/Bearish to Neutral — the one value the
UI treats as measured.

`_fetch_historical_prices`' docstring has flagged this since the crypto source gate went in.
It applies to equities too: FMP returns a short or empty series for recent listings and thin
names, and the 24h helper's "Always available since FMP's quote endpoint works per-ticker"
stopped being true when entitlement enforcement began 402ing whole symbol classes.
"""
from __future__ import annotations

import pytest

from app.services.sentiment_service import SentimentService as S

NAN = float("nan")


def _bars(*closes):
    return [{"close": c} for c in closes]


# ── the 7-day arm ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bars", [
    [], _bars(100.0),                          # too short to span a week
    _bars(None, None), _bars(0.0, 0.0),        # no usable closes
    [{}, {}],                                  # rows with no close at all
])
def test_an_unmeasurable_seven_day_window_is_none_not_neutral(bars):
    assert S._compute_price_sentiment_7d(bars) is None


def test_a_nan_close_degrades_instead_of_raising():
    """NaN defeats `not x` AND `x == 0`, so it used to reach `round(nan)` — a ValueError,
    i.e. a 500 on the sentiment endpoint rather than a degraded reading."""
    assert S._compute_price_sentiment_7d(_bars(100.0, NAN)) is None
    assert S._compute_price_sentiment_7d(_bars(NAN, 100.0)) is None


def test_a_real_seven_day_window_still_scores():
    up = S._compute_price_sentiment_7d(_bars(*[100.0] * 7, 110.0))
    down = S._compute_price_sentiment_7d(_bars(*[100.0] * 7, 90.0))
    assert up is not None and down is not None
    assert up > 50 > down


def test_a_genuinely_flat_week_is_still_a_measured_fifty():
    """The fix must not turn a real neutral into an absence — that is the same error
    mirrored."""
    assert S._compute_price_sentiment_7d(_bars(*[100.0] * 8)) == 50


# ── the 24-hour arm ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("quote", [{}, None, {"symbol": "AAPL"}, {"changePercentage": NAN}])
def test_an_unmeasurable_quote_is_none_not_neutral(quote):
    assert S._compute_price_sentiment(quote) is None


def test_a_measured_zero_change_is_a_real_fifty():
    """`or 0` mapped "absent" and "flat" to the same 50. They are different facts."""
    assert S._compute_price_sentiment({"changePercentage": 0.0}) == 50


def test_the_legacy_spelling_is_still_read():
    """Consumers are split across `changePercentage` and `changesPercentage`; dropping
    either silently blanks half the app."""
    assert S._compute_price_sentiment({"changesPercentage": 3.0}) == 71


# ── the blend ────────────────────────────────────────────────────────────────

def test_an_absent_price_arm_does_not_vote():
    """The headline case: strongly bullish news, no price data. Blending in 50 dragged an
    88 down to ~71; renormalising keeps the measurement."""
    with_fake_neutral = round(88 * 0.55 + 50 * 0.45)
    honest = S._combine_scores(88, 50, has_social=False, has_news=True, price_score=None)
    assert honest == 88
    assert honest != with_fake_neutral


def test_news_and_social_renormalise_to_forty_thirty():
    got = S._combine_scores(80, 30, has_social=True, has_news=True, price_score=None)
    assert got == round(80 * (0.40 / 0.70) + 30 * (0.30 / 0.70))


def test_social_only_with_no_price_is_the_social_score():
    assert S._combine_scores(50, 22, has_social=True, has_news=False, price_score=None) == 22


def test_nothing_measured_falls_back_to_fifty():
    """There is no other value available — and the caller REFUSES TO CACHE this response,
    which is what stops the fabrication becoming sticky for a TTL."""
    assert S._combine_scores(50, 30, has_social=False, has_news=False, price_score=None) == 50


# ⚠️ news=100 / social=100 / price=0, NOT a rounder-looking triple.
#
# The first attempt used 80/30/90, and `round(80*0.55 + 90*0.45)` and
# `round(80*(0.40/0.70) + 90*(0.30/0.70))` are BOTH 84 — so a mutation replacing the 55/45
# weights with a renormalisation stayed green. These inputs separate the two formulas after
# rounding (55 vs 57), which is the whole point of the test.
@pytest.mark.parametrize("has_news,has_social,expected", [
    (True, True, round(100 * 0.40 + 100 * 0.30 + 0 * 0.30)),
    (True, False, round(100 * 0.55 + 0 * 0.45)),
    (False, True, round(100 * 0.55 + 0 * 0.45)),
    (False, False, 0),
])
def test_the_price_present_weights_are_unchanged(has_news, has_social, expected):
    """55/45 is NOT a renormalisation of 40/30/30, so deriving it would silently move every
    existing score. Pinned so the new branch cannot be "simplified" into the old one."""
    assert S._combine_scores(100, 100, has_social, has_news, price_score=0) == expected


def test_the_result_is_always_an_int_because_the_wire_field_is_not_optional():
    """`SentimentAnalysisResponse.mood_score_7d` is a non-Optional `int`, so however
    degraded the inputs are, this may never return None."""
    for kwargs in (
        dict(has_social=False, has_news=False, price_score=None),
        dict(has_social=True, has_news=True, price_score=None),
        dict(has_social=False, has_news=True, price_score=42),
    ):
        assert isinstance(S._combine_scores(80, 30, **kwargs), int)
