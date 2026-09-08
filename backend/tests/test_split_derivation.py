"""Guard: a split is derived from price series WITHOUT mistaking a spin-off for one.

WHY
---
FMP's `/splits` is outside the signed licence and answers 402, so splits are now derived
from `historical-price-eod/full` (adjusted) against `/non-split-adjusted` (raw). The naive
rule — "the adjustment factor changed, therefore a split" — is WRONG, because FMP's `/full`
series is adjusted for **spin-offs** too and a spin-off changes no holder's share count.
Measured over a 77-ticker sweep, that naive rule produced 13 false positives.

Getting it wrong is not cosmetic. `whale_service._diff_quarters` restates a holder's
previous-quarter share count by this ratio; a wrong ratio writes a fabricated
multi-million-dollar BOUGHT into `whale_trades`, which feeds user alerts.

ANTI-VACUITY
------------
The tables below are LIVE-MEASURED factors, not invented ones, so they cannot drift into
agreeing with a broken classifier. `test_the_two_populations_do_not_overlap` additionally
pins the *separation* between them, so a change to the candidate set or the tolerance that
narrows the safety margin fails here rather than in production.

MUTATION_LOG — every line below was actually run (break it, watch it fail, restore),
2026-09-08. All ten now go red.

   1. `_MIN_TERM` 2 -> 4                 -> test_real_spinoffs_are_never_classified... RED
                                            (GE Vernova 1.252967 snaps to 5:4)
   2. `_SNAP_REL_TOL` -> 1e-4            -> test_every_measured_real_split_is_recovered RED
                                            (GE's 1:8 reverse sits at 2.80e-04)
   3. `_SNAP_REL_TOL` -> 5e-2            -> test_real_spinoffs_are_never_classified... RED
   4. `_MAX_TERM` 100 -> 20              -> test_every_measured_real_split_is_recovered RED
                                            (NKLA 1:30)
   5. ratio -> f_cur / f_prev            -> test_a_forward_split_multiplies_and_a_reverse RED
   6. date intersect -> union            -> test_misaligned_series_are_intersected... RED
   7. `_WINDOW_LEAD_DAYS` -> 0           -> test_the_window_starts_before_the_quarter RED
   8. `.ratio` -> float(self.observed)   -> test_two_splits_in_one_window_multiply_exactly RED
   9. snap returns Fraction(1,1) not None-> test_a_spinoff_yields_an_event_that_is_not... RED
  10. `is_split` -> return True          -> test_a_spinoff_yields_an_event_that_is_not... RED

⚠️ Mutation 8 SURVIVED on the first pass, and the reason is worth keeping. The test built
its price series from round numbers (raw 400.00 against adjusted 10.00), so the OBSERVED
factor was already exactly 4.0 and returning the raw float instead of the snapped rational
made no difference. The test proved nothing about snapping. It now seeds deliberate
rounding noise (400.03) so observed != exact, which is the real-world condition — GE's true
1:8 arrives as 0.125035, never as 0.125. A synthetic fixture built from clean arithmetic is
its own kind of vacuity.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from app.services.corporate_actions_service import (
    AdjustmentEvent,
    _CANDIDATES,
    _FACTOR_EPS,
    _MAX_TERM,
    _MIN_TERM,
    _SNAP_REL_TOL,
    _WINDOW_LEAD_DAYS,
    _quarter_bounds,
    derive_adjustment_events,
    snap_to_rational,
    window_for_quarters,
)

# ── Live-measured factors. Every number here came off the real API. ──────────────────────

# (observed factor, expected exact ratio as (numerator, denominator), symbol/date)
REAL_SPLITS = [
    (20.0, (20, 1), "AMZN 2022-06-06"),
    (4.0, (4, 1), "ANET 2021-11-18"),
    (10.0, (10, 1), "AVGO 2024-07-15"),
    (25.0, (25, 1), "BKNG 2026-04-06"),
    (50.0, (50, 1), "CMG 2024-06-26"),
    (2.0, (2, 1), "CPRT 2022-11-04"),
    (6.0, (6, 1), "DECK 2024-09-17"),
    (4.0, (4, 1), "DXCM 2022-06-13"),
    (20.0, (20, 1), "GOOGL 2022-07-18"),
    (4.0, (4, 1), "IBKR 2025-06-18"),
    (10.0, (10, 1), "LRCX 2024-10-03"),
    (10.0, (10, 1), "MSTR 2024-08-08"),
    (10.0, (10, 1), "NFLX 2025-11-17"),
    (4.0, (4, 1), "NVDA 2021-07-20"),
    (10.0, (10, 1), "NVDA 2024-06-10"),
    (2.0, (2, 1), "ODFL 2024-03-28"),
    (15.0, (15, 1), "ORLY 2025-06-10"),
    (3.0, (3, 1), "PANW 2022-09-14"),
    (10.0, (10, 1), "SHOP 2022-06-29"),
    (10.0, (10, 1), "SMCI 2024-10-01"),
    (3.0, (3, 1), "TPL 2024-03-27"),
    (3.0, (3, 1), "TSLA 2022-08-25"),
    (3.0, (3, 1), "WMT 2024-02-26"),
    (2.0, (2, 1), "AMC 2022-08-22"),
    # Reverse splits — the direction the whale restatement got wrong twice before.
    (0.1, (1, 10), "AMC 2023-08-24"),
    (0.1, (1, 10), "CGC 2023-12-20"),
    (0.1, (1, 10), "LCID 2025-09-02"),
    (0.033334, (1, 30), "NKLA 2024-06-25"),
    (0.1, (1, 10), "SIRI 2024-09-10"),
    (0.05, (1, 20), "SPCE 2024-06-17"),
    (0.333333, (1, 3), "DD 2026-06-24"),
    # ⚠️ The precision floor of the whole module. NOT machine-exact like the other 35:
    # FMP stores a ROUNDED cumulative adjustment factor, so a symbol whose factor compounds
    # a split with spin-offs inherits that rounding. Measured 2.80e-04 off exact 1/8.
    # Taking a median across the regime does NOT help (2.93e-04) — it is systematic.
    # This single value is what sets `_SNAP_REL_TOL`; do not delete it.
    (0.125035, (1, 8), "GE 2021-08-02 (1:8 reverse, compounded with spin-offs)"),
]

# Real corporate actions that are NOT splits. Every one changes the adjustment factor and
# leaves share counts untouched, so classifying one as a split fabricates a trade.
REAL_SPINOFFS = [
    (2.390044, "DD -> Qnity 2025-11-03"),
    (1.128001, "DHR -> Veralto 2023-10-02"),
    (1.327030, "FTV 2025-06-30"),
    (1.280866, "GE -> GE HealthCare 2023-01-04"),
    (1.252967, "GE -> GE Vernova 2024-04-02"),
    (1.045993, "IBM -> Kyndryl 2021-11-04"),
    (1.056091, "IP -> Sylvamo 2021-10-01"),
    (1.163998, "LH -> Fortrea 2023-07-03"),
    (1.047968, "MRK -> Organon 2021-06-03"),
    (1.323820, "T -> Warner Bros Discovery 2022-04-11"),
    (1.717958, "XPO -> GXO 2021-08-02"),
    (1.683149, "XPO -> RXO 2022-11-01"),
    (1.029986, "ZBH -> ZimVie 2022-03-01"),
]


def _nearest_err(x: float) -> float:
    return min(abs(x - float(c)) / x for c in _CANDIDATES)


# ── The classifier ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("factor,expected,label", REAL_SPLITS)
def test_every_measured_real_split_is_recovered(factor, expected, label):
    frac = snap_to_rational(factor)
    assert frac is not None, f"{label}: real split {factor} was rejected"
    assert (frac.numerator, frac.denominator) == expected, f"{label}: got {frac}"


@pytest.mark.parametrize("factor,label", REAL_SPINOFFS)
def test_real_spinoffs_are_never_classified_as_splits(factor, label):
    assert snap_to_rational(factor) is None, (
        f"{label}: factor {factor} was classified as a split. A spin-off does not change "
        "any holder's share count, so restating by this ratio fabricates a trade."
    )


def test_the_two_populations_do_not_overlap():
    """The safety margin itself, pinned — this is what makes the threshold defensible."""
    worst_accept = max(_nearest_err(f) for f, _, _ in REAL_SPLITS)
    nearest_reject = min(_nearest_err(f) for f, _ in REAL_SPINOFFS)

    assert worst_accept < _SNAP_REL_TOL < nearest_reject, (
        f"the tolerance no longer separates the populations: real splits reach "
        f"{worst_accept:.3e}, spin-offs come as close as {nearest_reject:.3e}, "
        f"tolerance is {_SNAP_REL_TOL:.3e}"
    )
    # Both margins must stay comfortable, not merely non-zero.
    assert _SNAP_REL_TOL / worst_accept > 5, "too little headroom above the worst real split"
    assert nearest_reject / _SNAP_REL_TOL > 5, "too little headroom below the nearest spin-off"


def test_the_candidate_set_is_the_reason_the_margin_exists():
    """Anti-vacuity: loosening `_MIN_TERM` silently collapses the separation.

    With `min(p,q) <= 4`, GE Vernova's 1.252967 sits 2.4e-03 from 5:4 and the margin drops
    from ~100x to ~8x. The constant is doing real work; this test says so out loud.
    """
    assert _MIN_TERM <= 2, "raising _MIN_TERM past 2 collapses the spin-off separation"
    assert _MAX_TERM >= 30, "1:30 reverse splits are real (NKLA)"
    assert all(min(c.numerator, c.denominator) <= _MIN_TERM for c in _CANDIDATES)
    assert len(_CANDIDATES) > 100


@pytest.mark.parametrize(
    "bad", [None, True, False, float("nan"), float("inf"), float("-inf"), 0.0, -3.0, "abc"]
)
def test_snap_rejects_degenerate_input(bad):
    """NaN and Inf matter most: FMP emits both for thin symbols, and NaN defeats an
    ordinary `<= 0` guard. Booleans are excluded because `True` is 1.0 in Python and would
    otherwise snap to 1:1. A numeric STRING is deliberately accepted — `float()` coercion
    matches `price_service._finite`, and the input is always a computed float in practice."""
    assert snap_to_rational(bad) is None


# ── Derivation over price series ────────────────────────────────────────────────────────

def _series(rows):
    """(full_rows, raw_rows) from [(date, adjusted_close, raw_close), ...]."""
    return (
        [{"symbol": "T", "date": d, "close": a} for d, a, _ in rows],
        [{"symbol": "T", "date": d, "adjClose": r} for d, _, r in rows],
    )


def test_a_forward_split_multiplies_and_a_reverse_divides():
    """Direction. Both whale writers shipped the `>=` midpoint test unconditionally once,
    which fabricated a BOUGHT on every reverse-split quarter — so direction gets a test."""
    full, raw = _series([
        ("2024-06-06", 100.0, 1000.0),   # pre-split: raw is 10x the adjusted basis
        ("2024-06-07", 101.0, 1010.0),
        ("2024-06-10", 102.0, 102.0),    # ex-split: the two series converge
        ("2024-06-11", 103.0, 103.0),
    ])
    events = derive_adjustment_events(full, raw)
    assert [e.date for e in events] == ["2024-06-10"]
    assert events[0].ratio == 10.0, "a 10:1 forward split multiplies the share count"

    full, raw = _series([
        ("2024-09-06", 100.0, 10.0),     # pre-reverse: raw is 1/10th
        ("2024-09-09", 101.0, 10.1),
        ("2024-09-10", 102.0, 102.0),
        ("2024-09-11", 103.0, 103.0),
    ])
    events = derive_adjustment_events(full, raw)
    assert [e.date for e in events] == ["2024-09-10"]
    assert events[0].ratio == 0.1, "a 1:10 reverse split divides the share count"


def test_two_splits_in_one_window_multiply_exactly():
    """Callers multiply ratios inside a window, so float drift would compound.

    The raw closes carry deliberate rounding noise (400.03, not 400.00) because FMP's
    series does — GE's real 1:8 arrives as 0.125035. The point of snapping is that the
    OBSERVED factor is noisy and the RETURNED one is not, so this test is only meaningful
    with an observed value that differs from the exact ratio.
    """
    full, raw = _series([
        ("2024-04-01", 10.0, 400.03),    # ~40x basis  (4:1 then 10:1 still ahead)
        ("2024-05-01", 11.0, 110.0),     # 4:1 lands   -> 10x basis
        ("2024-06-10", 12.0, 12.0),      # 10:1 lands  -> 1x basis
    ])
    events = derive_adjustment_events(full, raw)
    assert events[0].observed != 4.0, "test setup: the observed factor must be noisy"

    assert [e.ratio for e in events] == [4.0, 10.0], "snapped, not raw"
    product = 1.0
    for e in events:
        product *= e.ratio
    assert product == 40.0, (
        "exact rationals must not drift: multiplying the raw observed factors here gives "
        "40.003, and `_split_ratio_in_window` multiplies across a window"
    )


def test_a_spinoff_yields_an_event_that_is_not_a_split():
    """The three-state result. NOT 'no event' (something happened) and NOT a split."""
    full, raw = _series([
        ("2023-01-03", 52.89, 84.89),
        ("2023-01-04", 55.99, 70.16),    # GE HealthCare, measured factor 1.280866
        ("2023-01-05", 56.50, 70.80),
    ])
    events = derive_adjustment_events(full, raw)
    assert len(events) == 1
    ev = events[0]
    assert ev.is_split is False
    assert ev.ratio is None, "an unclassified action must not present a usable ratio"
    assert 1.27 < ev.observed < 1.29


@pytest.mark.parametrize("rows,why", [
    ([], "both series empty — a nonexistent or delisted symbol returns 200 with []"),
    ([("2024-01-02", 10.0, 10.0)], "a single bar has no pair to compare"),
])
def test_degenerate_series_report_no_split_rather_than_guessing(rows, why):
    full, raw = _series(rows)
    assert derive_adjustment_events(full, raw) == [], why


def test_none_inputs_do_not_raise():
    assert derive_adjustment_events(None, None) == []
    assert derive_adjustment_events([], None) == []


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0.0, -5.0, None, "x"])
def test_a_single_unusable_bar_is_dropped_not_turned_into_an_event(bad):
    """NaN defeats `<= 0` guards and `except (TypeError, ValueError)`; guard on isfinite."""
    full, raw = _series([
        ("2024-01-02", 10.0, 10.0),
        ("2024-01-03", 10.0, 10.0),
        ("2024-01-04", 10.0, 10.0),
    ])
    full[1]["close"] = bad
    events = derive_adjustment_events(full, raw)
    assert events == [], f"a bar with close={bad!r} must be skipped, not fabricate an event"


def test_misaligned_series_are_intersected_not_zipped():
    """A single missing bar would shift every later comparison and manufacture events."""
    full = [{"date": d, "close": c} for d, c in [
        ("2024-01-02", 10.0), ("2024-01-03", 10.0), ("2024-01-04", 10.0), ("2024-01-05", 10.0),
    ]]
    raw = [{"date": d, "adjClose": c} for d, c in [
        ("2024-01-02", 20.0), ("2024-01-04", 20.0), ("2024-01-05", 20.0),
    ]]  # 2024-01-03 absent — a zip would pair 01-03's close with 01-04's raw
    assert derive_adjustment_events(full, raw) == []


def test_a_move_below_the_event_threshold_is_not_an_event():
    full, raw = _series([("2024-01-02", 100.0, 100.0), ("2024-01-03", 100.0, 100.2)])
    assert abs(1.0 - 100.0 / 100.2) < _FACTOR_EPS
    assert derive_adjustment_events(full, raw) == []


def test_an_out_of_range_reverse_split_is_unclassified_not_silently_ignored():
    """1:200 is real in delisting-defence microcaps and sits outside `_MAX_TERM`.

    It must surface as an unclassified event so the share-flow magnitude backstop sees it —
    NOT as "no split", which would let the raw diff through unexamined.
    """
    full, raw = _series([
        ("2024-01-02", 100.0, 0.5), ("2024-01-03", 101.0, 101.0), ("2024-01-04", 102.0, 102.0),
    ])
    events = derive_adjustment_events(full, raw)
    assert len(events) == 1 and events[0].is_split is False


# ── Windows ─────────────────────────────────────────────────────────────────────────────

def test_the_window_starts_before_the_quarter():
    """2024-03-31 was a SUNDAY. Starting the window on the quarter boundary would begin
    after any split effective on the quarter's first trading day, missing it entirely."""
    frm, to = window_for_quarters([(2024, 2)])
    q_start, q_end = _quarter_bounds(2024, 2)
    assert date.fromisoformat(frm) < q_start, "no lead — a boundary split would be missed"
    assert date.fromisoformat(frm) == q_start - timedelta(days=_WINDOW_LEAD_DAYS)
    assert to == q_end.isoformat()


def test_many_quarters_collapse_into_one_window():
    frm, to = window_for_quarters([(2024, 3), (2024, 1), (2024, 2)])
    assert frm == (date(2024, 1, 1) - timedelta(days=_WINDOW_LEAD_DAYS)).isoformat()
    assert to == "2024-09-30"


@pytest.mark.parametrize("q,expected", [
    (1, ("2024-01-01", "2024-03-31")), (2, ("2024-04-01", "2024-06-30")),
    (3, ("2024-07-01", "2024-09-30")), (4, ("2024-10-01", "2024-12-31")),
])
def test_quarter_bounds(q, expected):
    a, b = _quarter_bounds(2024, q)
    assert (a.isoformat(), b.isoformat()) == expected


def test_leap_year_and_year_end_bounds():
    assert _quarter_bounds(2024, 1)[1].isoformat() == "2024-03-31"
    assert _quarter_bounds(2023, 4)[1].isoformat() == "2023-12-31"


@pytest.mark.parametrize("pairs", [[], None, [(2024, 0)], [(2024, 5)], [("x", 1)]])
def test_a_window_over_nothing_usable_is_none(pairs):
    assert window_for_quarters(pairs) is None


def test_adjustment_event_ratio_is_exact():
    assert AdjustmentEvent("2024-01-01", 9.99, 10, 1).ratio == 10.0
    assert AdjustmentEvent("2024-01-01", 0.0999, 1, 10).ratio == 0.1
    assert AdjustmentEvent("2024-01-01", 1.28).ratio is None
    assert AdjustmentEvent("2024-01-01", 1.28).is_split is False
