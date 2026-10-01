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

TWO AXES SINCE 2026-09-30, ONE PER MODE. The volatility-z order (`basis="z"`, the default)
is MARKET mode's — "the most unusual move among the stocks Caydex tracks" — and the
one-argument `_rank_and_read` that Ask Cay AI's `attribute_ticker_move` calls. Every
`rank_movers(...)` call below without a `basis` pins THAT axis. Portfolio mode ranks by
ABSOLUTE % (`basis="abs_change"`): under z the Holdings tile read AAPL −2.7% above
ORCL +4.0% and dropped every σ-less holding (outside the top-200 universe, all crypto)
below the 1+5 cap whatever its move — "random", in the user's word. The
`── abs_change ──` section pins that axis and the payload's gainer/loser columns.
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

    ranked = rank_movers([big_pct, unusual], basis="z")
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


# ── "today" is a calendar word, not a session word (2026-09-17) ──────────────

def test_session_word_is_on_fri_on_a_saturday():
    """The live session on a Saturday is Friday and every stamp is Friday: the DATE the
    detectors gate on is unchanged, but the WORD must not be "today" — the model said
    "down 4% today" all weekend and the catalyst gate re-bought a search for it."""
    from datetime import date
    from app.services.widget_movers_service import WidgetMoversService
    fri, sat = date(2026, 9, 11), date(2026, 9, 12)
    d, iso, word = WidgetMoversService._session_of([], fri, sat)
    assert (d, iso, word) == (fri, "2026-09-11", "on Fri")
    d, iso, word = WidgetMoversService._session_of([], fri, fri)
    assert (d, iso, word) == (fri, "2026-09-11", "today")
    # No calendar day supplied (legacy callers / tests) keeps the old behaviour.
    assert WidgetMoversService._session_of([], fri)[2] == "today"


# ── abs_change: portfolio mode's axis (2026-09-30) ───────────────────────────
#
# The Holdings tile promises "my biggest movers". Under z it led with AAPL −2.7% (z 1.78)
# over ORCL +4.0% (z 1.38) and cut a σ-less −15% holding entirely — the screenshot's
# "random" order. These pin the absolute-move axis and the selection built on it.


def test_the_default_basis_is_still_z_for_market_mode_and_ask_cay():
    """`attribute_ticker_move` calls `_rank_and_read(tickers)` with ONE argument and market
    mode keeps the z order; flipping the default would silently change both."""
    import inspect

    from app.services.widget_movers_service import WidgetMoversService

    assert inspect.signature(rank_movers).parameters["basis"].default == "z"
    params = inspect.signature(WidgetMoversService._rank_and_read).parameters
    assert params["basis"].default == "z"
    assert params["exclude_band"].default is True
    assert params["phase"].default is None


def test_a_sigma_less_minus_15_headlines_over_a_judged_minus_0_3():
    rows = [_row("CALM", -0.3, 0.004), _row("OUTSIDER", -15.0, None)]
    assert [m.ticker for m in rank_movers(rows, basis="abs_change")] == ["OUTSIDER", "CALM"]
    # CONTROL — the z axis still puts the judged row first; that is market mode's rule.
    assert [m.ticker for m in rank_movers(rows, basis="z")] == ["CALM", "OUTSIDER"]


def test_the_screenshot_case_orders_by_size_of_move():
    """AAPL −2.7% z≈1.78, ORCL +4.0% z≈1.38, RKLB +1.5% z≈0.61: z said AAPL, ORCL, RKLB."""
    rows = [
        _row("AAPL", -2.7, 0.01517),
        _row("ORCL", 4.0, 0.029),
        _row("RKLB", 1.5, 0.0246),
        _row("BTCUSD", 6.2, None),       # crypto never has a cached σ
    ]
    assert [m.ticker for m in rank_movers(rows)] == ["AAPL", "ORCL", "RKLB", "BTCUSD"]
    assert [m.ticker for m in rank_movers(rows, basis="abs_change")] == [
        "BTCUSD", "ORCL", "AAPL", "RKLB",
    ]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), None, "n/a"])
def test_abs_change_drops_an_unreadable_move_rather_than_ranking_it(bad):
    """`abs(nan)` sorts arbitrarily and `-inf` would headline the tile."""
    ranked = rank_movers([_row("GOOD", -1.0, 0.02), _row("BAD", bad, 0.02)], basis="abs_change")
    assert [m.ticker for m in ranked] == ["GOOD"]


def test_abs_change_ties_break_on_z_then_sigma_then_ticker():
    rows = [
        _row("NOSIG", 4.0, None),
        _row("LOWZ", -4.0, 0.04),     # z = 1.0
        _row("HIGHZ", 4.0, 0.02),     # z = 2.0
        _row("BBB", -4.0, 0.02),      # z = 2.0, ties HIGHZ on |chg| and z
    ]
    first = [m.ticker for m in rank_movers(rows, basis="abs_change")]
    assert first == ["BBB", "HIGHZ", "LOWZ", "NOSIG"]
    assert first == [m.ticker for m in rank_movers(list(reversed(rows)), basis="abs_change")], (
        "equal movers swapped places between refreshes"
    )


def test_abs_change_keeps_negative_zero_and_duplicates():
    ranked = rank_movers(
        [_row("FLAT", -0.0, 0.02), _row("AAPL", 2.0, 0.02), _row("AAPL", -5.0, 0.02)],
        basis="abs_change",
    )
    assert [m.ticker for m in ranked] == ["AAPL", "AAPL", "FLAT"]
    assert ranked[0].change_percent == -5.0
    assert not (ranked[-1].change_percent or 0) > 0


@pytest.mark.parametrize("basis", ["", "abs", "Z", None, "magnitude"])
def test_an_unknown_basis_raises_rather_than_silently_picking_one(basis):
    with pytest.raises(ValueError):
        rank_movers([_row("A", 1.0, 0.02)], basis=basis)


# ── selection: headline, runners, gainers, losers, counts ───────────────────

from app.services.widget_movers_service import (  # noqa: E402
    _FLAT_PCT,
    _TOP_MOVERS,
    direction_counts,
    select_payload_movers,
)


def _abs(*specs):
    return rank_movers([_row(t, c, s) for t, c, s in specs], basis="abs_change")


def test_gainers_and_losers_exclude_the_headline():
    ranked = _abs(("HEAD", -15.0, None), ("UP1", 3.0, 0.02), ("DN1", -2.0, 0.02),
                  ("UP2", 1.0, 0.02), ("DN2", -0.5, 0.02))
    head, runners, gainers, losers = select_payload_movers(ranked)
    assert head.ticker == "HEAD"
    assert [m.ticker for m in gainers] == ["UP1", "UP2"]
    assert [m.ticker for m in losers] == ["DN1", "DN2"], "losers run most-negative first"
    assert "HEAD" not in {m.ticker for m in runners + gainers + losers}


def test_all_one_direction_leaves_the_other_column_empty():
    up = _abs(("A", 5.0, 0.02), ("B", 3.0, 0.02), ("C", 1.0, 0.02))
    assert select_payload_movers(up)[3] == []
    down = _abs(("A", -5.0, 0.02), ("B", -3.0, 0.02), ("C", -1.0, 0.02))
    assert select_payload_movers(down)[2] == []


@pytest.mark.parametrize("chg, flat", [
    (0.0, True), (-0.0, True), (0.004, True), (-0.004, True), (0.00499, True),
    (0.005, False), (-0.005, False), (0.0051, False),
])
def test_the_flat_line_is_the_clients_two_decimal_rounding(chg, flat):
    """|chg| < 0.005 DISPLAYS as 0.00%. Counting it "up" would print "▲1" beside a 0.00%
    badge, and putting it in RISING would list a stock that did not rise."""
    assert _FLAT_PCT == 0.005
    ranked = _abs(("HEAD", -9.0, 0.02), ("X", chg, 0.02))
    up, down, flat_n = direction_counts(ranked)
    assert flat_n == (1 if flat else 0)
    assert (up + down + flat_n) == 2
    _, _, gainers, losers = select_payload_movers(ranked)
    in_columns = "X" in {m.ticker for m in gainers + losers}
    assert in_columns is (not flat)


def test_counts_are_one_per_ticker_and_cover_every_ranked_row():
    ranked = _abs(("AAPL", 2.0, 0.02), ("AAPL", 2.0, 0.02), ("MSFT", -1.0, 0.02),
                  ("KO", 0.001, 0.01), ("BTCUSD", 3.0, None))
    assert direction_counts(ranked) == (2, 1, 1)
    assert direction_counts([]) == (0, 0, 0)


def test_selection_never_repeats_a_ticker_in_any_column():
    ranked = _abs(("DUP", -9.0, 0.02), ("DUP", -9.0, 0.02), ("AAA", 5.0, 0.02),
                  ("AAA", 4.0, 0.02), ("BBB", -3.0, 0.02))
    head, runners, gainers, losers = select_payload_movers(ranked)
    for col in (runners, gainers, losers):
        names = [m.ticker for m in col]
        assert len(names) == len(set(names)) and "DUP" not in names
    assert head.ticker == "DUP"


def test_an_empty_ranking_selects_nothing():
    assert select_payload_movers([]) == (None, [], [], [])


# ── the portfolio PAYLOAD built on that selection ───────────────────────────


def _portfolio(ranked, holdings_count, ctx=None):
    from app.services.widget_movers_service import WidgetMoversService, _SCOPE_PORTFOLIO

    return WidgetMoversService()._payload(
        mode="portfolio", ranked=ranked, cards={}, ctx=ctx or _ctx(), basket=None,
        scope_label=_SCOPE_PORTFOLIO, holdings_count=holdings_count,
    )


def test_one_holding_is_a_headline_and_nothing_else():
    p = _portfolio(_abs(("TSLA", -3.2, 0.03)), holdings_count=1)
    assert p.headline_mover.ticker == "TSLA"
    assert p.runners_up == [] and p.top_gainers == [] and p.top_losers == []
    assert (p.up_count, p.down_count, p.flat_count) == (0, 1, 0)
    assert p.holdings_count == 1


def test_250_holdings_fill_both_columns_and_count_every_ranked_row():
    specs = [(f"T{i:03d}", (i + 1) * 0.05 * (1 if i % 2 else -1), 0.02) for i in range(250)]
    ranked = _abs(*specs)
    p = _portfolio(ranked, holdings_count=250)

    assert len(p.top_gainers) == _TOP_MOVERS == 5
    assert len(p.top_losers) == 5
    assert len(p.runners_up) == 5
    assert p.up_count + p.down_count + p.flat_count == 250
    gains = [m.change_percent for m in p.top_gainers]
    losses = [m.change_percent for m in p.top_losers]
    assert gains == sorted(gains, reverse=True) and all(g > 0 for g in gains)
    assert losses == sorted(losses) and all(x < 0 for x in losses)
    head = p.headline_mover.ticker
    assert head not in {m.ticker for m in p.top_gainers + p.top_losers + p.runners_up}
    # The headline is the biggest |%| of all 250 — that is the whole product decision.
    assert abs(p.headline_mover.change_percent) == max(abs(c) for _, c, _ in specs)


def test_a_degraded_portfolio_withholds_the_counts_with_the_count():
    """`holdings_count=None` (the quote leg failed) must not ship "▲0 ▼0" beside it."""
    p = _portfolio(_abs(("A", 1.0, 0.02)), holdings_count=None)
    assert p.holdings_count is None
    assert (p.up_count, p.down_count, p.flat_count) == (None, None, None)


def test_market_mode_never_carries_the_holdings_fields():
    from app.services.widget_movers_service import WidgetMoversService, _SCOPE_MARKET

    ranked = rank_movers([_row(f"T{i}", -float(i + 1), 0.02) for i in range(8)])
    p = WidgetMoversService()._payload(
        mode="market", ranked=ranked, cards={}, ctx=_ctx(), basket=None,
        scope_label=_SCOPE_MARKET, holdings_count=8,
    )
    assert p.group_name is None and p.holdings_count is None
    assert (p.up_count, p.down_count, p.flat_count) == (None, None, None)
    assert p.top_gainers == [] and p.top_losers == []
    # …and the legacy movers installed builds render are still there.
    assert p.headline_mover is not None and len(p.runners_up) == 5


def test_spy_in_holdings_is_counted_but_never_explains_itself(monkeypatch):
    """Portfolio mode ranks the band like any holding (the tile no longer draws it), so SPY
    can headline. `ctx.market_change` IS SPY's own change, so without the guard the cause
    reads "The market fell 1.6% today; SPY moved with it." — a ratio of exactly 1.0."""
    from datetime import date

    from app.services import widget_movers_service as wm

    live = date(2026, 9, 29)
    monkeypatch.setattr(wm, "session_trading_date", lambda now=None: live)
    monkeypatch.setattr(wm, "_et_calendar_day", lambda: live)
    monkeypatch.setattr(wm, "session_label", lambda now=None: "Live 2:14 PM ET")
    iso = live.isoformat()
    ctx = wm._MarketContext(news_available=True).for_tickers(
        {}, iso, {"SPY": {"symbol": "SPY", "price": 651.0, "changePercentage": -1.6,
                          "changeSession": iso}},
    )
    assert ctx.market_change == -1.6
    ranked = rank_movers(
        [{"ticker": "SPY", "change_percent": -1.6, "sigma_daily": 0.008, "change_session": iso},
         {"ticker": "NVDA", "change_percent": -1.5, "sigma_daily": 0.03, "change_session": iso}],
        basis="abs_change",
    )
    p = _portfolio(ranked, holdings_count=2, ctx=ctx)

    assert p.headline_mover.ticker == "SPY"
    assert p.headline_mover.cause.kind != "market", p.headline_mover.cause.detail
    assert "moved with it" not in p.headline_mover.cause.detail
    assert p.down_count == 2, "SPY is a holding and must be counted"
    # CONTROL: the guard is SPY-only — NVDA still gets the market leg.
    nvda = next(m for m in p.top_losers if m.ticker == "NVDA")
    assert nvda.cause.kind == "market", nvda.cause.detail


# ── the portfolio BUILD: degraded vs authoritative, and what it asks for ──────


def _portfolio_build(monkeypatch, rank_result, *, phase="premarket"):
    """A real `_build_portfolio` over a stubbed quote leg. Returns (service, seen)."""
    from app.services import widget_movers_service as wm

    svc = wm.WidgetMoversService()
    seen: dict = {}

    async def _rank_and_read(tickers, **kwargs):
        seen["tickers"] = list(tickers)
        seen["kwargs"] = kwargs
        return rank_result

    async def _market_context(tickers, index_rows, session_date):
        seen["ctx_tickers"] = list(tickers)
        return wm._MarketContext(news_available=True).for_tickers({}, session_date, index_rows)

    async def _sectors(user_id, tickers):
        return {}

    async def _head_grades(ranked):
        seen["grades_for"] = [m.ticker for m in ranked]
        return None

    monkeypatch.setattr(svc, "_rank_and_read", _rank_and_read)
    monkeypatch.setattr(svc, "_market_context", _market_context)
    monkeypatch.setattr(svc, "_sectors", _sectors)
    monkeypatch.setattr(svc, "_head_grades", _head_grades)
    monkeypatch.setattr(wm, "session_phase", lambda now=None: phase)
    return svc, seen


_SPY_OK = {"SPY": {"symbol": "SPY", "price": 651.0, "changePercentage": -0.2}}


@pytest.mark.asyncio
async def test_the_portfolio_build_ranks_by_size_includes_the_band_and_passes_the_phase(monkeypatch):
    svc, seen = _portfolio_build(monkeypatch, ([], {}, True, _SPY_OK), phase="premarket")
    await svc._build_portfolio("u1", ["AAPL", "SPY"])
    assert seen["kwargs"] == {"basis": "abs_change", "exclude_band": False, "phase": "premarket"}


@pytest.mark.asyncio
@pytest.mark.parametrize("index_rows", [
    {},                                                         # the batch returned nothing
    {"SPY": {"symbol": "SPY", "price": 651.0, "changePercentage": None}},
    {"ONEQ": {"symbol": "ONEQ", "price": 80.0, "changePercentage": 0.3}},
])
async def test_a_failed_quote_leg_is_degraded_not_no_prices(monkeypatch, index_rows):
    """"No prices for your 3 holdings today" is a FINDING. Built from an outage it is a lie,
    and the client would replace a good snapshot with it — so the count is withheld."""
    svc, _ = _portfolio_build(monkeypatch, ([], {}, True, index_rows))
    p = await svc._build_portfolio("u1", ["AAPL", "MSFT", "NVDA"])
    assert p.mode == "portfolio"
    assert p.holdings_count is None
    assert (p.up_count, p.down_count, p.flat_count) == (None, None, None)
    assert p.headline_mover is None


@pytest.mark.asyncio
async def test_unpriced_holdings_with_a_live_tape_are_an_authoritative_no_prices(monkeypatch):
    """SPY readable, nothing of mine rankable (all funds / delisted / unpriced): the quote
    leg WORKED, so the count stands and the tile says why it is empty."""
    svc, _ = _portfolio_build(monkeypatch, ([], {}, True, _SPY_OK))
    p = await svc._build_portfolio("u1", ["VFIAX", "DEADCO", "FXAIX"])
    assert p.holdings_count == 3
    assert (p.up_count, p.down_count, p.flat_count) == (0, 0, 0)
    assert p.headline_mover is None


@pytest.mark.asyncio
async def test_the_count_is_every_requested_holding_and_the_basket_says_so(monkeypatch):
    """Four of five holdings priced and fell together; the fifth is a fund that never ranks.
    The basket used to say "4 of your 4" — a denominator the user can see is wrong."""
    ranked = rank_movers(
        [_row(t, c, 0.02) for t, c in (("NVDA", -4.0), ("AMD", -5.0), ("AVGO", -3.5),
                                        ("MU", -4.5))],
        basis="abs_change",
    )
    svc, _ = _portfolio_build(monkeypatch, (ranked, {}, True, _SPY_OK))
    p = await svc._build_portfolio("u1", ["NVDA", "amd", "AMD", "AVGO", "MU", "VFIAX"])
    assert p.holdings_count == 5, "dedup'd and case-folded, unpriced FUND included"
    assert p.basket is not None
    assert p.basket.total_count == 5
    assert "4 of your 5 holdings fell together" in p.basket.text
    assert p.down_count == 4 and p.up_count == 0


@pytest.mark.asyncio
async def test_the_card_read_covers_losers_outside_the_runner_window(monkeypatch):
    """Under |%| order the top six can all be gainers, so a LOSER rendered in the Falling
    column sits outside head + runners. Without its card read, the tile would say "could
    not check the news" about a ticker whose card exists."""
    from app.services import widget_movers_service as wm

    quotes = {f"G{i}": {"symbol": f"G{i}", "price": 10.0, "changePercentage": 10.0 - i}
              for i in range(1, 9)}
    quotes["L1"] = {"symbol": "L1", "price": 10.0, "changePercentage": -0.5}
    quotes["L2"] = {"symbol": "L2", "price": 10.0, "changePercentage": -0.4}
    quotes["SPY"] = {"symbol": "SPY", "price": 651.0, "changePercentage": 0.3}
    asked: list = []

    svc = wm.WidgetMoversService()

    async def _quotes(symbols):
        return {s: quotes[s] for s in symbols if s in quotes}

    class _Vol:
        async def get_sigmas_bulk(self, symbols):
            return {}

    class _News:
        async def get_cards(self, scopes):
            asked.extend(scopes)
            return {}

    monkeypatch.setattr(svc, "_quotes", _quotes)
    monkeypatch.setattr(wm, "get_volatility_cache_service", lambda: _Vol())
    monkeypatch.setattr(wm, "get_news_insight_service", lambda: _News())

    ranked, _cards, ok, index_rows = await svc._rank_and_read(
        list(quotes), basis="abs_change", exclude_band=False,
    )
    assert ok is True
    head, runners, gainers, losers = select_payload_movers(ranked)
    assert {"L1", "L2"} <= {m.ticker for m in losers}
    assert "L1" not in {m.ticker for m in [head] + runners}, "the scenario must be outside the window"
    assert {"L1", "L2"} <= set(asked), f"the Falling column's cards were not read: {asked}"
    assert len(asked) == len(set(asked)), "one scope per card"
    assert "SPY" in index_rows


# ── the CRYPTO leg is a separate source: its outage is degraded too (R1/R2) ──────────
#
# SPY proves only that the EQUITY leg answered. 24/7 pairs are priced by CoinGecko, which
# `price_service` degrades on its own into "no row" (a 429 on the monthly quota, a 5xx).
# A group with nothing rankable but coins, while SPY printed, shipped an authoritative
# "No prices for your 3 holdings today" over the user's good snapshot.


@pytest.mark.asyncio
@pytest.mark.parametrize("group", [
    ["BTCUSD", "ETHUSD"],
    ["BTCUSD", "ETHUSD", "SOLUSD"],
    ["btcusd", " ethusd "],                 # case / whitespace as the client may send it
    ["BTCUSD", "VFIAX"],                    # coin + a fund that can never rank
    ["BTCUSD", "DEADCO", "VFIAX"],          # coin + delisted + fund
])
async def test_a_dark_crypto_leg_is_degraded_not_no_prices(monkeypatch, group):
    svc, _ = _portfolio_build(monkeypatch, ([], {}, True, _SPY_OK))
    p = await svc._build_portfolio("u1", group)
    assert p.holdings_count is None, "a CoinGecko outage reported as a finding"
    assert (p.up_count, p.down_count, p.flat_count) == (None, None, None)
    assert p.headline_mover is None


@pytest.mark.asyncio
@pytest.mark.parametrize("group", [
    ["BTC", "ETH"],          # BARE tickers are FMP-listed ETFs (Grayscale), not coins
    ["VFIAX", "DEADCO", "FXAIX"],
])
async def test_an_unpriced_group_without_coins_stays_authoritative(monkeypatch, group):
    """CONTROL: the crypto rule is source-based — a bare BTC is the Grayscale ETF on FMP,
    whose miss with SPY printing is a real "no price", not an outage."""
    svc, _ = _portfolio_build(monkeypatch, ([], {}, True, _SPY_OK))
    p = await svc._build_portfolio("u1", group)
    assert p.holdings_count == len(group)
    assert (p.up_count, p.down_count, p.flat_count) == (0, 0, 0)


@pytest.mark.asyncio
async def test_a_coin_that_ranked_proves_the_crypto_leg_answered(monkeypatch):
    """One coin priced, one did not (an unresolvable token): the leg WORKED, so the count
    stands and the miss is an honest "1 no price"."""
    ranked = rank_movers([_row("BTCUSD", 2.4)], basis="abs_change")
    svc, _ = _portfolio_build(monkeypatch, (ranked, {}, True, _SPY_OK))
    p = await svc._build_portfolio("u1", ["BTCUSD", "NEWCOINUSD"])
    assert p.holdings_count == 2
    assert (p.up_count, p.down_count, p.flat_count) == (1, 0, 0)
    assert p.headline_mover.ticker == "BTCUSD"


@pytest.mark.asyncio
async def test_a_mixed_group_with_equities_ranked_keeps_its_movers(monkeypatch):
    """Coins dark, equities priced: the tile still shows real movers — only the "no price"
    figure absorbs the coins. Not degraded (the fix must not blank a working tile)."""
    ranked = rank_movers([_row("NVDA", -4.0, 0.02), _row("AMD", 1.0, 0.02)],
                         basis="abs_change")
    svc, _ = _portfolio_build(monkeypatch, (ranked, {}, True, _SPY_OK))
    p = await svc._build_portfolio("u1", ["NVDA", "AMD", "BTCUSD"])
    assert p.holdings_count == 3
    assert p.headline_mover.ticker == "NVDA"


# ── a DEGRADED build is MOVER-LESS (R10) ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_degraded_build_carries_no_movers_and_spends_no_grades(monkeypatch):
    """Screener down, CoinGecko up: the ranking held only BTCUSD, so the degraded payload
    headlined it as the group's "biggest mover" while NVDA may have fallen 8% — and a
    payload with a headline is content, so the client replaced its good snapshot."""
    ranked = rank_movers([_row("BTCUSD", 0.8), _row("ETHUSD", -0.3)], basis="abs_change")
    svc, seen = _portfolio_build(monkeypatch, (ranked, {}, True, {}))
    p = await svc._build_portfolio("u1", ["NVDA", "AMD", "AAPL", "MSFT", "BTCUSD", "ETHUSD"])
    assert p.holdings_count is None
    assert p.headline_mover is None
    assert p.runners_up == [] and p.top_gainers == [] and p.top_losers == []
    assert p.basket is None
    assert (p.up_count, p.down_count, p.flat_count) == (None, None, None)
    assert seen["grades_for"] == [], "a paid grades lookup for a headline never drawn"
    assert seen["ctx_tickers"] == []


@pytest.mark.asyncio
async def test_a_degraded_build_never_claims_a_basket_from_the_coins_alone(monkeypatch):
    """Three coins fell together while the equity leg was down: "3 of your 7 holdings fell
    together" is a group claim about holdings we could not even read."""
    ranked = rank_movers([_row("BTCUSD", -6.0), _row("ETHUSD", -7.0), _row("SOLUSD", -8.0)],
                         basis="abs_change")
    # CONTROL: the same rows DO make a basket when the build is healthy.
    from app.services.widget_movers_service import detect_basket
    assert detect_basket(ranked, {}, holdings_count=7) is not None
    svc, _ = _portfolio_build(monkeypatch, (ranked, {}, True, {}))
    p = await svc._build_portfolio(
        "u1", ["NVDA", "AMD", "AAPL", "MSFT", "BTCUSD", "ETHUSD", "SOLUSD"],
    )
    assert p.holdings_count is None
    assert p.basket is None and p.headline_mover is None


@pytest.mark.asyncio
async def test_a_degraded_build_with_spy_unreadable_and_rows_present_keeps_its_movers(monkeypatch):
    """CONTROL: something RANKED and the index band answered (SPY's change is merely
    unreadable) — that is not an outage, so the movers and the count stand."""
    ranked = rank_movers([_row("NVDA", -4.0, 0.02)], basis="abs_change")
    rows = {"SPY": {"symbol": "SPY", "price": 651.0, "changePercentage": None}}
    svc, _ = _portfolio_build(monkeypatch, (ranked, {}, True, rows))
    p = await svc._build_portfolio("u1", ["NVDA"])
    assert p.holdings_count == 1 and p.headline_mover.ticker == "NVDA"


# ── end to end over the REAL `_rank_and_read`, only the quote batch stubbed ───────────


def _real_rank_build(monkeypatch, quotes):
    from app.services import widget_movers_service as wm

    svc = wm.WidgetMoversService()

    async def _quotes(symbols):
        return {s: quotes[s] for s in symbols if s in quotes}

    class _Vol:
        async def get_sigmas_bulk(self, symbols):
            return {}

    class _News:
        async def get_cards(self, scopes):
            return {}

    async def _market_context(tickers, index_rows, session_date):
        return wm._MarketContext(news_available=True).for_tickers({}, session_date, index_rows)

    async def _sectors(user_id, tickers):
        return {}

    async def _head_grades(ranked):
        return None

    monkeypatch.setattr(svc, "_quotes", _quotes)
    monkeypatch.setattr(svc, "_market_context", _market_context)
    monkeypatch.setattr(svc, "_sectors", _sectors)
    monkeypatch.setattr(svc, "_head_grades", _head_grades)
    monkeypatch.setattr(wm, "get_volatility_cache_service", lambda: _Vol())
    monkeypatch.setattr(wm, "get_news_insight_service", lambda: _News())
    monkeypatch.setattr(wm, "session_phase", lambda now=None: "regular")
    return svc


_TAPE = {"SPY": {"symbol": "SPY", "price": 651.0, "changePercentage": -0.2},
         "ONEQ": {"symbol": "ONEQ", "price": 80.0, "changePercentage": 0.1}}


@pytest.mark.asyncio
async def test_e2e_coingecko_down_and_fmp_up_is_degraded(monkeypatch):
    svc = _real_rank_build(monkeypatch, dict(_TAPE))      # no coin rows at all
    p = await svc._build_portfolio("u1", ["BTCUSD", "ETHUSD", "SOLUSD"])
    assert p.holdings_count is None and p.headline_mover is None


@pytest.mark.asyncio
async def test_e2e_fmp_down_and_coingecko_up_is_mover_less(monkeypatch):
    svc = _real_rank_build(monkeypatch, {
        "BTCUSD": {"symbol": "BTCUSD", "price": 112000.0, "changePercentage": 0.8},
    })
    p = await svc._build_portfolio("u1", ["NVDA", "AMD", "AAPL", "MSFT", "BTCUSD"])
    assert p.holdings_count is None
    assert p.headline_mover is None and p.top_gainers == [] and p.runners_up == []


@pytest.mark.asyncio
async def test_e2e_both_legs_up_is_authoritative(monkeypatch):
    """CONTROL for the two above: with both sources answering, everything is counted."""
    quotes = dict(_TAPE)
    quotes["BTCUSD"] = {"symbol": "BTCUSD", "price": 112000.0, "changePercentage": 6.1}
    quotes["NVDA"] = {"symbol": "NVDA", "price": 130.0, "changePercentage": -1.0}
    svc = _real_rank_build(monkeypatch, quotes)
    p = await svc._build_portfolio("u1", ["NVDA", "BTCUSD"])
    assert p.holdings_count == 2
    assert p.headline_mover.ticker == "BTCUSD"
    assert (p.up_count, p.down_count, p.flat_count) == (1, 1, 0)
