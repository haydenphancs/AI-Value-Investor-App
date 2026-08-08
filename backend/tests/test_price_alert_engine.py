"""Price-alert evaluation — pure decision function, exhaustively.

This is the one notification the user asks for BY NAME, which raises the bar twice: it
must fire when they expect it, and it must fire exactly once. The three ways a naive
implementation gets it wrong are each pinned below:

  * firing on a LEVEL instead of a CROSSING → a notification every cycle for as long as
    the condition holds;
  * treating an unknown baseline as "below" → an alert set above the current price fires
    one second after Save, about nothing;
  * no hysteresis → a stock ticking 249.99 / 250.01 fires on every tick, because each
    tick genuinely IS a crossing.
"""

import pytest

from app.services.price_alert_engine import (
    KIND_ABOVE,
    KIND_BELOW,
    KIND_PERCENT,
    REPEAT_DAILY,
    REPEAT_ONCE,
    evaluate_alert,
    finite_percent,
    finite_price,
)


def ev(**kw):
    base = dict(
        kind=KIND_ABOVE, threshold=250.0, repeat_mode=REPEAT_ONCE,
        armed=True, last_price=240.0, price=260.0, change_percent=None,
        rearm_pct=0.005,
    )
    base.update(kw)
    return evaluate_alert(**base)


# ── input hygiene ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", [
    None, "", "abc", float("nan"), float("inf"), float("-inf"), 0, 0.0, -1, -0.01,
    True, False, [250], {"p": 250},
])
def test_unusable_prices_are_rejected(raw):
    """A price of zero or below is not a delisting signal we can act on — it is an
    unusable reading. NaN is the dangerous one: it answers False for every comparison,
    which DISABLES a gate rather than tripping it."""
    assert finite_price(raw) is None


@pytest.mark.parametrize("raw,expected", [(250, 250.0), ("250.5", 250.5), (0.01, 0.01)])
def test_usable_prices_are_accepted(raw, expected):
    assert finite_price(raw) == expected


def test_percent_readings_may_be_zero_or_negative():
    """Unlike a price: a flat day is 0% and a decline is negative. Both are real."""
    assert finite_percent(0) == 0.0
    assert finite_percent(-4.2) == -4.2
    assert finite_percent(float("nan")) is None
    assert finite_percent(None) is None
    assert finite_percent(True) is None


# ── cold start ───────────────────────────────────────────────────────────────

def test_a_brand_new_alert_seeds_and_does_not_fire():
    """THE first-impression bug. Without this, an alert created at 'above $250' on a
    stock already trading at $260 fires on the very first cycle — a notification about
    nothing, one second after the user pressed Save."""
    d = ev(last_price=None, price=260.0)
    assert d.fire is False
    assert d.new_last_price == 260.0     # seeded
    assert d.reason == "seeded"


def test_the_seeded_baseline_makes_the_next_real_crossing_fire():
    """Seeding must not mean 'never fires' — the very next genuine crossing must work."""
    seeded = ev(last_price=None, price=240.0)
    assert seeded.fire is False
    crossed = ev(last_price=seeded.new_last_price, price=260.0)
    assert crossed.fire is True


# ── crossing, not level ──────────────────────────────────────────────────────

def test_price_above_fires_on_the_upward_crossing():
    assert ev(last_price=240.0, price=260.0).fire is True


def test_price_above_does_not_re_fire_while_the_condition_merely_HOLDS():
    """Firing on `price >= threshold` would notify on every cycle for as long as the
    stock stayed above the line — hundreds of times in an afternoon."""
    assert ev(last_price=260.0, price=270.0).fire is False


def test_price_below_fires_on_the_downward_crossing():
    d = ev(kind=KIND_BELOW, threshold=250.0, last_price=260.0, price=240.0)
    assert d.fire is True


def test_price_below_ignores_an_upward_move():
    assert ev(kind=KIND_BELOW, threshold=250.0, last_price=240.0, price=260.0).fire is False


def test_landing_exactly_on_the_threshold_counts_as_crossed():
    """`prev < limit <= price`. A stop set at $250 that trades exactly $250 has been
    reached; requiring strict inequality would silently miss the round numbers users
    actually pick."""
    assert ev(last_price=240.0, price=250.0).fire is True
    assert ev(kind=KIND_BELOW, last_price=260.0, price=250.0).fire is True


def test_a_gap_open_across_the_threshold_fires():
    """`last_price` is yesterday's close and `price` is today's open — the stock never
    traded at the threshold. That IS the crossing the user cares about; a rule that
    needed an intraday tick at the line would miss every overnight move."""
    assert ev(last_price=240.0, price=310.0).fire is True


# ── hysteresis: the oscillation case ─────────────────────────────────────────

def test_forty_ticks_across_the_threshold_produce_exactly_one_notification():
    """The headline property. Every one of these ticks is a genuine crossing, so a
    correct crossing detector WITHOUT a latch fires twenty times."""
    armed, last, fires = True, 249.0, 0
    for tick in [250.01, 249.99] * 20:
        d = evaluate_alert(
            kind=KIND_ABOVE, threshold=250.0, repeat_mode=REPEAT_DAILY,
            armed=armed, last_price=last, price=tick, rearm_pct=0.005,
        )
        fires += 1 if d.fire else 0
        armed, last = d.new_armed, d.new_last_price
    assert fires == 1


def test_the_latch_drops_on_fire():
    assert ev(last_price=240.0, price=260.0).new_armed is False


def test_a_latched_alert_re_arms_only_after_a_real_retreat():
    """249.99 is not below the 248.75 re-arm band, so it stays latched. 240 is."""
    still = ev(armed=False, last_price=260.0, price=249.99)
    assert still.fire is False and still.new_armed is False and still.reason == "latched"

    back = ev(armed=False, last_price=260.0, price=240.0)
    assert back.new_armed is True and back.reason == "rearmed"


def test_a_re_armed_alert_can_fire_again():
    rearmed = ev(armed=False, last_price=260.0, price=240.0, repeat_mode=REPEAT_DAILY)
    again = ev(armed=rearmed.new_armed, last_price=rearmed.new_last_price,
               price=260.0, repeat_mode=REPEAT_DAILY)
    assert again.fire is True


def test_price_below_re_arms_on_the_OTHER_side():
    """Mirror of the above case; getting the direction wrong here would leave a
    price_below alert latched forever after its first fire."""
    latched = ev(kind=KIND_BELOW, threshold=250.0, armed=False,
                 last_price=240.0, price=250.5)
    assert latched.new_armed is False           # inside the 251.25 band
    freed = ev(kind=KIND_BELOW, threshold=250.0, armed=False,
               last_price=240.0, price=260.0)
    assert freed.new_armed is True


# ── repeat modes ─────────────────────────────────────────────────────────────

def test_a_one_shot_alert_deactivates_on_fire():
    """Belt AND braces: the dedup key would suppress a repeat anyway, but deactivating
    stops the rule being evaluated (and quoted) at all."""
    assert ev(repeat_mode=REPEAT_ONCE, last_price=240.0, price=260.0).deactivate is True


def test_a_daily_alert_stays_active():
    assert ev(repeat_mode=REPEAT_DAILY, last_price=240.0, price=260.0).deactivate is False


# ── percent_move ─────────────────────────────────────────────────────────────

def test_a_percent_move_fires_on_magnitude_in_either_direction():
    up = ev(kind=KIND_PERCENT, threshold=5.0, change_percent=6.2, price=100.0)
    down = ev(kind=KIND_PERCENT, threshold=5.0, change_percent=-6.2, price=100.0)
    assert up.fire is True and down.fire is True


def test_a_percent_move_below_the_threshold_is_quiet():
    assert ev(kind=KIND_PERCENT, threshold=5.0, change_percent=4.9, price=100.0).fire is False


def test_the_percent_threshold_is_inclusive():
    assert ev(kind=KIND_PERCENT, threshold=5.0, change_percent=5.0, price=100.0).fire is True


def test_a_percent_move_needs_no_baseline():
    """`changePercentage` is already relative to the previous close, so the measure
    resets itself at each day roll — no latch and no `last_price` required."""
    d = ev(kind=KIND_PERCENT, threshold=5.0, last_price=None,
           change_percent=8.0, price=100.0)
    assert d.fire is True


@pytest.mark.parametrize("reading", [None, float("nan"), float("inf"), "big", True])
def test_a_percent_move_with_an_unusable_reading_holds(reading):
    d = ev(kind=KIND_PERCENT, threshold=5.0, change_percent=reading, price=100.0)
    assert d.fire is False and d.reason == "no_percent_reading"


# ── garbage never destroys good state ────────────────────────────────────────

@pytest.mark.parametrize("bad", [None, float("nan"), 0, -5, "n/a"])
def test_a_missing_or_bad_price_holds_the_baseline(bad):
    """Overwriting `last_price` with nothing would destroy the reference the NEXT
    crossing is measured against — and the alert would then miss the move it exists for.
    This is the delisted-mid-session / halted / not-in-this-batch case."""
    d = ev(last_price=240.0, price=bad)
    assert d.fire is False
    assert d.new_last_price == 240.0
    assert d.reason == "no_price"


@pytest.mark.parametrize("bad", [None, float("nan"), 0, -1, "n/a"])
def test_an_unusable_threshold_never_fires_and_changes_nothing(bad):
    """`NUMERIC` legally holds NaN in Postgres, so a threshold can be NaN if the CHECK
    was ever dropped. Every comparison against NaN is False, so the rule would silently
    do nothing forever — this makes that state explicit instead."""
    d = ev(threshold=bad, last_price=240.0, price=260.0)
    assert d.fire is False
    assert d.new_last_price == 240.0
    assert d.reason == "unusable_threshold"


@pytest.mark.parametrize("kind", ["", "price_equals", "PRICE_ABOVE", None, 7])
def test_an_unknown_kind_is_inert_not_a_crash(kind):
    d = ev(kind=kind)
    assert d.fire is False and d.reason.startswith("unknown_kind")


def test_a_bad_stored_baseline_is_treated_as_a_cold_start():
    """A NaN in `last_price` must not make every comparison silently False; it means
    'no usable observation', which is what a cold start is."""
    d = ev(last_price=float("nan"), price=260.0)
    assert d.fire is False and d.reason == "seeded" and d.new_last_price == 260.0


# ── a full session, end to end ───────────────────────────────────────────────

def test_a_realistic_session_fires_once_on_the_right_tick():
    """Opens below, drifts up through the line, oscillates, closes above."""
    prices = [242, 245, 248, 249.9, 250.4, 251, 249.8, 250.2, 253, 255]
    armed, last, fires = True, 240.0, []
    for p in prices:
        d = evaluate_alert(
            kind=KIND_ABOVE, threshold=250.0, repeat_mode=REPEAT_DAILY,
            armed=armed, last_price=last, price=p, rearm_pct=0.005,
        )
        if d.fire:
            fires.append(p)
        armed, last = d.new_armed, d.new_last_price
    assert fires == [250.4]
