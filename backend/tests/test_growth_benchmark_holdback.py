"""Growth benchmark hold-back: a THIN just-completed-FY benchmark cell (n < the
MATURE_SAMPLE_FLOOR=20) is replaced by the last MATURE value, so a contaminated
latest-period median can't make a genuine grower read "below sector".

The motivating real case (from the persona-scoring validation): the Semiconductors
FY2026 EPS-growth median is +79% from n=9 early hypergrowth reporters, vs a credible
+4.9% from n=77 in FY2025 — NVDA's 67% EPS growth scored 1/5 against the n=9 sample.
"""

from app.services.growth_service import _hold_back_thin_benchmarks


def _cells(*triples):
    return {p: {"value": v, "n": n} for p, v, n in triples}


def test_thin_latest_period_holds_back_to_mature():
    rich = {"eps_yoy": _cells(("2024", -14.44, 80), ("2025", 4.86, 77), ("2026", 79.36, 9))}
    flat = _hold_back_thin_benchmarks(rich)
    assert flat["eps_yoy"]["2024"] == -14.44   # mature: own value
    assert flat["eps_yoy"]["2025"] == 4.86      # mature: own value
    assert flat["eps_yoy"]["2026"] == 4.86      # thin (n=9) → held back to 2025


def test_quarterly_thin_latest_holds_back_across_year_boundary():
    # _period_sort_key orders Q1'26 after Q4'25 (a lexical sort would not).
    rich = {"revenue_qoq": _cells(("Q3'25", 3.0, 60), ("Q4'25", 4.0, 55), ("Q1'26", 99.0, 7))}
    flat = _hold_back_thin_benchmarks(rich)
    assert flat["revenue_qoq"]["Q1'26"] == 4.0   # thin → held back to the latest MATURE (Q4'25)


def test_all_periods_mature_unchanged():
    rich = {"revenue_yoy": _cells(("2024", 4.68, 79), ("2025", 7.915, 76))}
    assert _hold_back_thin_benchmarks(rich) == {"revenue_yoy": {"2024": 4.68, "2025": 7.915}}


def test_older_thin_period_holds_back_to_past_not_future():
    # A mid-series thin period must hold to the latest mature value AT OR BEFORE it —
    # NEVER a FUTURE year's median (a lookahead that paints an old year wrong).
    rich = {"eps_yoy": _cells(("2023", 5.0, 80), ("2024", 999.0, 3), ("2025", 7.0, 77))}
    flat = _hold_back_thin_benchmarks(rich)
    assert flat["eps_yoy"]["2024"] == 5.0   # 2023 (past), NOT 2025 (future 7.0)


def test_early_thin_year_with_no_earlier_mature_keeps_own():
    # An early thin year with no mature period before it keeps its own value rather
    # than importing a later-year median.
    rich = {"revenue_yoy": _cells(("2021", 50.0, 8), ("2022", 9.0, 77), ("2023", 10.0, 80))}
    flat = _hold_back_thin_benchmarks(rich)
    assert flat["revenue_yoy"]["2021"] == 50.0


def test_no_mature_period_keeps_own_values():
    # Nothing meets the floor → don't substitute a thin value for another; keep each.
    rich = {"fcf_yoy": _cells(("2025", 10.0, 8), ("2026", 50.0, 5))}
    assert _hold_back_thin_benchmarks(rich) == {"fcf_yoy": {"2025": 10.0, "2026": 50.0}}


def test_empty_and_missing_n():
    assert _hold_back_thin_benchmarks({"eps_yoy": {}}) == {"eps_yoy": {}}
    # a cell with no n is treated as thin (0); held back when a mature peer exists.
    rich = {"eps_yoy": _cells(("2024", 5.0, 50)) | {"2025": {"value": 88.0}}}
    assert _hold_back_thin_benchmarks(rich)["eps_yoy"]["2025"] == 5.0
