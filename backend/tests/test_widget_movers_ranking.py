"""Widget ranking math: which ticker the home-screen widget leads with.

The widget shows exactly ONE ticker. Everything about whether it feels smart or
broken is decided here, and the failure modes are all silent — a wrong pick still
renders a perfectly nice card.

Two classes of bug this pins:

1. **NaN/None reaching the comparator.** This repo has shipped that at least four
   times (see `project_financials_tab_traps`, `project_analysis_tab_traps`). A NaN
   change that survives into `sort()` does not raise; it produces an arbitrary
   order, and a `None` coerced to 0.0 reads as "perfectly flat" for a ticker we
   simply could not read.

2. **Ranking by the wrong axis.** The product decision was volatility-relative, and
   the obvious existing helper — `move_score` — is tier-bucket + raw magnitude, so
   it inverts on exactly the case the decision was made for. `test_the_case_move_score_gets_wrong`
   is that inversion, written as a regression.
"""

from __future__ import annotations

import math

import pytest

from app.services.updates_materiality import _MIN_SIGMA_DAILY, move_score, move_z
from app.services.widget_movers_service import rank_movers


def _row(ticker, change, sigma=None, **extra):
    return {"ticker": ticker, "change_percent": change, "sigma_daily": sigma, **extra}


# ── move_z ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "change, sigma",
    [
        (float("nan"), 0.02),
        (float("inf"), 0.02),
        (float("-inf"), 0.02),
        (None, 0.02),
        ("not a number", 0.02),
        (5.0, float("nan")),
        (5.0, None),
        (5.0, 0.0),
        (5.0, -0.01),
    ],
)
def test_move_z_returns_none_never_zero_for_unusable_input(change, sigma):
    """None means "cannot judge". 0.0 would mean "judged, and perfectly normal".

    Collapsing the two lets an unreadable ticker sort as a calm one, which is how a
    broken quote feed silently becomes "nothing happened today".
    """
    assert move_z(change, sigma) is None


def test_move_z_is_symmetric_in_direction():
    assert move_z(4.0, 0.02) == move_z(-4.0, 0.02)


def test_sigma_floor_stops_a_frozen_ticker_headlining_a_two_basis_point_move():
    """A halted / barely-traded name can produce σ ≈ 0.0001.

    Without the floor, z = 0.02% / (0.0001·100) = 2.0 — an "Unusual" two-basis-point
    twitch that would outrank a genuine 6% selloff and take over the widget.
    """
    unfloored = 0.02 / (0.0001 * 100.0)
    assert unfloored == pytest.approx(2.0)

    z = move_z(0.02, 0.0001)
    assert z == pytest.approx(0.02 / (_MIN_SIGMA_DAILY * 100.0))
    assert z < 0.1

    assert move_z(6.0, 0.02) > z


def test_sigma_floor_does_not_distort_a_genuinely_calm_instrument():
    """The floor must bind only on degenerate data, never on real low-vol names.

    ^GSPC runs ~0.83%/day — comfortably above the floor — so its z is unchanged.
    """
    sigma_gspc = 0.0083
    assert sigma_gspc > _MIN_SIGMA_DAILY
    assert move_z(3.0, sigma_gspc) == pytest.approx(3.0 / (sigma_gspc * 100.0))


# ── rank_movers ───────────────────────────────────────────────────────


def test_the_case_move_score_gets_wrong():
    """The regression that motivated a continuous z.

    A Notable +9% (z≈1.1) vs an Unusual +3% (z≈2.4). `move_score` puts the 9% first
    because the raw-magnitude tiebreak (+9) overwhelms the 5-point bucket gap. The
    widget must lead with the 3%: it is the genuinely abnormal one.
    """
    big_pct = _row("BIGPCT", 9.0, 0.08)      # z ≈ 1.125 → Notable
    unusual = _row("UNUSUAL", 3.0, 0.0125)   # z = 2.4   → Unusual

    assert move_score("Notable", 9.0) > move_score("Unusual", 3.0)

    ranked = rank_movers([big_pct, unusual])
    assert [m.ticker for m in ranked] == ["UNUSUAL", "BIGPCT"]
    assert ranked[0].z > ranked[1].z


def test_unreadable_rows_are_dropped_not_ranked_as_flat():
    ranked = rank_movers(
        [
            _row("GOOD", -4.0, 0.02),
            _row("NANNY", float("nan"), 0.02),
            _row("INFTY", float("inf"), 0.02),
            _row("NULLY", None, 0.02),
            _row("", 9.0, 0.02),
            _row("   ", 9.0, 0.02),
        ]
    )
    assert [m.ticker for m in ranked] == ["GOOD"]


def test_rows_without_sigma_sort_after_every_judged_row():
    """A big move we cannot judge must not displace a smaller one we can.

    σ-less rows are still returned — a widget with only unjudgeable holdings should
    show something — but they can never outrank a measured mover.
    """
    ranked = rank_movers(
        [
            _row("NOSIGMA", 7.0, None),
            _row("JUDGED", 1.0, 0.02),   # z = 0.5, small but measurable
        ]
    )
    assert [m.ticker for m in ranked] == ["JUDGED", "NOSIGMA"]
    assert ranked[0].z is not None
    assert ranked[1].z is None


def test_sigma_less_rows_order_among_themselves_by_raw_move():
    ranked = rank_movers([_row("SMALL", 1.0, None), _row("LARGE", 8.0, None)])
    assert [m.ticker for m in ranked] == ["LARGE", "SMALL"]


def test_ties_break_deterministically_so_the_widget_does_not_flicker():
    """Two identical movers must not swap places between refreshes."""
    rows = [_row("ZZZ", 4.0, 0.02), _row("AAA", 4.0, 0.02), _row("MMM", 4.0, 0.02)]
    first = [m.ticker for m in rank_movers(rows)]
    assert first == ["AAA", "MMM", "ZZZ"]
    assert first == [m.ticker for m in rank_movers(list(reversed(rows)))]


def test_direction_does_not_affect_rank_only_magnitude_does():
    ranked = rank_movers([_row("UP", 4.0, 0.02), _row("DOWN", -4.0, 0.02)])
    assert {m.ticker for m in ranked} == {"UP", "DOWN"}
    assert ranked[0].z == ranked[1].z


def test_negative_zero_is_preserved_not_flipped_positive():
    """`-0.0 > 0` is False in Python, so a signed zero must not paint a gainer.

    Guarded here because `home_dashboard_service` already carries a fix for exactly
    this, and the widget re-derives direction from the same field.
    """
    ranked = rank_movers([_row("FLAT", -0.0, 0.02)])
    assert len(ranked) == 1
    assert not (ranked[0].change_percent or 0) > 0


def test_empty_and_degenerate_inputs_do_not_raise():
    assert rank_movers([]) == []
    assert rank_movers([_row("ONLY", 2.0, 0.02)])[0].ticker == "ONLY"


def test_duplicate_tickers_are_all_kept_and_ordered_stably():
    """Dedup is the caller's job; ranking must not silently swallow rows."""
    ranked = rank_movers([_row("AAPL", 2.0, 0.02), _row("AAPL", 5.0, 0.02)])
    assert [m.ticker for m in ranked] == ["AAPL", "AAPL"]
    assert ranked[0].change_percent == 5.0


def test_ticker_is_normalised_to_upper():
    assert rank_movers([_row(" achr ", -4.0, 0.02)])[0].ticker == "ACHR"


def test_every_ranked_row_has_a_finite_change():
    ranked = rank_movers(
        [_row(f"T{i}", v, 0.02) for i, v in enumerate([1.0, float("nan"), -3.0, None])]
    )
    assert all(math.isfinite(m.change_percent) for m in ranked)
