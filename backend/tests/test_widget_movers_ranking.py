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
from app.services.widget_movers_service import _group_change, rank_movers


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


# ── how many movers reach the payload ─────────────────────────────────


def _ctx():
    from app.services.widget_movers_service import _MarketContext
    from app.utils.market_hours import session_trading_date

    sd = session_trading_date().isoformat()
    return _MarketContext(
        industry_available=True, earnings_available=True, market_available=True,
        news_available=True, industry_dates={}, session_date=sd,
    )


def test_the_payload_carries_a_headline_plus_five_runners():
    """Medium renders a ranked column and Large lists five; both were showing ONE.

    Widening this costs nothing upstream — every mover comes from the single batch
    quote already made, and `get_cards` is one batched select regardless of how many
    scopes it receives. It is the highest value-per-risk change in the widget.
    """
    from app.services.widget_movers_service import (
        WidgetMoversService, _RUNNERS_UP, _SCOPE_MARKET,
    )

    assert _RUNNERS_UP == 6
    ranked = rank_movers([_row(f"T{i:02d}", -float(20 - i), 0.02) for i in range(12)])
    p = WidgetMoversService()._payload(
        mode="market", ranked=ranked, cards={}, ctx=_ctx(),
        basket=None, scope_label=_SCOPE_MARKET,
    )
    assert p.headline_mover is not None
    assert len(p.runners_up) == _RUNNERS_UP - 1 == 5


def test_a_short_universe_does_not_pad_or_repeat():
    from app.services.widget_movers_service import WidgetMoversService, _SCOPE_MARKET

    ranked = rank_movers([_row("AAA", -5.0, 0.02), _row("BBB", -3.0, 0.02)])
    p = WidgetMoversService()._payload(
        mode="market", ranked=ranked, cards={}, ctx=_ctx(),
        basket=None, scope_label=_SCOPE_MARKET,
    )
    assert p.headline_mover.ticker == "AAA"
    assert [m.ticker for m in p.runners_up] == ["BBB"]


def test_no_ticker_appears_twice_and_the_headline_never_repeats_below_itself():
    """iOS renders these with `ForEach(id: \\.ticker)`.

    Duplicate ids are undefined behaviour in SwiftUI — on a Home Screen, with no way
    for the user to recover. `rank_movers` deliberately keeps duplicates and
    `_swept_universe` trusts the RPC to be unique, so the guarantee has to be made
    where the payload is built.
    """
    from app.services.widget_movers_service import WidgetMoversService, _SCOPE_MARKET

    ranked = rank_movers([
        _row("DUP", -9.0, 0.02), _row("DUP", -9.0, 0.02),
        _row("AAA", -5.0, 0.02), _row("AAA", -4.0, 0.02), _row("BBB", -3.0, 0.02),
    ])
    p = WidgetMoversService()._payload(
        mode="market", ranked=ranked, cards={}, ctx=_ctx(),
        basket=None, scope_label=_SCOPE_MARKET,
    )
    tickers = [m.ticker for m in p.runners_up]
    assert len(tickers) == len(set(tickers)), tickers
    assert p.headline_mover.ticker not in tickers


# ── A group that closed exactly flat is a MEASUREMENT ───────────────────────────────


def test_a_group_at_exactly_zero_is_reported_flat_not_dropped():
    """`finite(r.get("changesPercentage") or r.get("averageChange"))` — `0.0` is FALSY.

    A sector or industry whose equal-weighted mean is exactly 0.0 fell through to
    `averageChange`, which the entitled substitute
    (`market_movers_service._group_performance`) does not emit at all. `finite(None)`
    returned None and the caller's `if chg is not None` then DROPPED the group from the
    market context — so a flat sector read downstream as "no signal" rather than "no
    move". Reachable: the substitute publishes `round(mean, 4)`.
    """
    assert _group_change({"industry": "Semiconductors", "changesPercentage": 0.0}) == 0.0


def test_the_legacy_average_change_key_is_still_honoured():
    """FMP's retired snapshot spelled it `averageChange`; a cached row may still carry it."""
    assert _group_change({"sector": "Technology", "averageChange": 1.5}) == 1.5


@pytest.mark.parametrize("row, expected", [
    ({"changesPercentage": None, "averageChange": 2.5}, 2.5),
    ({"changesPercentage": float("nan"), "averageChange": 3.5}, 3.5),
    ({"changesPercentage": float("inf"), "averageChange": 4.5}, 4.5),
    ({"changesPercentage": "junk", "averageChange": 5.5}, 5.5),
    ({"changesPercentage": -0.0}, -0.0),
])
def test_an_unusable_primary_falls_back_to_the_legacy_key(row, expected):
    assert _group_change(row) == expected


def test_a_row_with_no_usable_change_at_all_is_none():
    """Absent stays absent — the caller must be able to skip it."""
    assert _group_change({"industry": "Semiconductors"}) is None
    assert _group_change({"changesPercentage": None, "averageChange": None}) is None
    assert _group_change({}) is None


# ── The cross-session age gate must not be disarmable by an absent date ─────────────


def _industry_ctx(snapshot_date, session_date):
    from app.services.widget_movers_service import _MarketContext

    c = _MarketContext()
    c.ticker_industry = {"NVDA": "Semiconductors"}
    c.industry_available = True
    c.industry_changes = {"semiconductors": -1.2}
    # Per-industry stamps now, not one scalar for the batch: the scalar was the FIRST row's
    # date and rows are sorted by % change desc, so the day's top-gaining industry decided
    # attribution for every card. `{}` models "no date at all", which must still fail closed.
    c.industry_dates = {"semiconductors": snapshot_date} if snapshot_date else {}
    c.session_date = session_date
    return c


def test_a_same_session_industry_move_is_reported():
    """Control — the gate must not simply suppress everything."""
    assert _industry_ctx("2026-09-08", "2026-09-08").industry_for("NVDA") == ("Semiconductors", -1.2)


@pytest.mark.parametrize("snapshot, session, why", [
    ("2026-09-05", "2026-09-08", "Friday's move served on Monday"),
    (None, "2026-09-08", "no date at all — the state that disarmed the gate"),
    ("2026-09-08", None, "no session to compare against"),
    (None, None, "neither known"),
])
def test_an_unverifiable_session_yields_the_name_without_a_number(snapshot, session, why):
    """FAIL CLOSED. The guard used to short-circuit on `self.industry_snapshot_date`.

    FMP's dated `industry-performance-snapshot` is outside the licence (402), and the
    entitled substitute — screener rows grouped by industry — carried no `date`. So the
    gate read green while printing a previous session's move as today's cause: at 07:00 ET
    on a Monday the screener still reports Friday's close, so the average IS Friday's move,
    and `daily_move_attribution` emitted "Aerospace & Defense fell 1.2%; NVDA went the
    other way." That sentence is the one the guard's docstring says it exists to prevent.

    `(name, None)` rather than `(name, stale_number)` is the whole point of this method.
    """
    name, change = _industry_ctx(snapshot, session).industry_for("NVDA")

    assert name == "Semiconductors", "the industry NAME is never in doubt"
    assert change is None, why


def test_one_stale_industry_does_not_disarm_attribution_for_the_others():
    """🔴 The bug this replaced a scalar to fix.

    `_industries()` kept only the FIRST row's date, and rows arrive sorted by
    `changesPercentage` DESCENDING — so the stamp belonged to whichever industry led the
    day. Mid-session that is often one whose members are mostly untraded, whose prices still
    equal the stored close, and which `_group_performance` therefore correctly stamps with
    the PREVIOUS trade date. `industry_for` then failed closed for EVERY industry, for the
    whole hourly context-cache window — including ones stamped today.
    """
    from app.services.widget_movers_service import _MarketContext

    session = "2026-09-08"
    c = _MarketContext()
    c.industry_available = True
    c.ticker_industry = {"NVDA": "Semiconductors", "BA": "Aerospace & Defense"}
    c.industry_changes = {"semiconductors": -1.2, "aerospace & defense": 4.0}
    # The top gainer is stale (untraded members still at Friday's close); the other is live.
    c.industry_dates = {"aerospace & defense": "2026-09-05", "semiconductors": session}
    c.session_date = session

    assert c.industry_for("NVDA") == ("Semiconductors", -1.2), (
        "a same-session industry lost its number because ANOTHER industry was stale"
    )
    # ...and the genuinely stale one is still refused.
    assert c.industry_for("BA") == ("Aerospace & Defense", None)
