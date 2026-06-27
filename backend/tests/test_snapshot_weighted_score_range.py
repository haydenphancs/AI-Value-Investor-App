"""Guard the load-bearing scale invariant the card→persona seam rests on.

Each Fundamentals snapshot emits a continuous `weighted` composite that is mapped
1.0–5.0 → 0–10 by `_card_weighted_to_score10` (1→0, 3→5, 5→10) to become the
per-persona vital factor. That seam ASSUMES `weighted ∈ [1, 5]`. It holds today
only because every component scorer returns an int in [1, 5] AND defaults MISSING
data to a NEUTRAL 3 (not 0). If a future change makes a scorer return 0/None for
missing data, `weighted` dips below 1 and the mapper silently votes a deflated /
0.0 "critical" factor far downstream.

These tests pin the invariant AT THE SOURCE — pure scorer functions, no network /
Supabase (per .claude/rules/testing.md: math/transformation tests build inputs
inline). They are the tripwire a deep reviewer demands for the whole mechanism.
"""

from app.services.growth_snapshot_service import _growth_score
from app.services.health_snapshot_service import _zscore_rating
from app.services.profitability_snapshot_service import _profitability_score
from app.services.valuation_snapshot_service import _valuation_score


# Same component weights the four services use to build `weighted`.
def _growth_weighted(s):
    r, e, f, o = s
    return r * 0.30 + e * 0.30 + f * 0.20 + o * 0.20


def _prof_weighted(s):
    g, op, n, roe, roa = s
    return g * 0.15 + op * 0.20 + n * 0.25 + roe * 0.25 + roa * 0.15


def _val_weighted(s):
    pe, pb, ps, pfcf, ev = s
    return pe * 0.25 + pb * 0.15 + ps * 0.15 + pfcf * 0.20 + ev * 0.25


def test_each_scorer_defaults_missing_data_to_neutral_3():
    # THE regression tripwire: the card→persona seam relies on missing components
    # voting a NEUTRAL 3 so a data-poor card maps to a neutral ~5/10 factor — never
    # a deflated 0. Flipping any of these to `return 0` would break the seam.
    assert _growth_score(None, None) == 3
    assert _growth_score(None, 10.0) == 3
    assert _profitability_score(None, None) == 3
    assert _profitability_score(None, 0.12) == 3
    assert _valuation_score(None, None) == 3
    assert _valuation_score(0.0, 20.0) == 3       # non-positive multiple → neutral
    assert _zscore_rating(None) == 3


def test_growth_weighted_in_unit_range():
    assert _growth_weighted([_growth_score(None, None)] * 4) == 3.0          # all-missing → neutral
    assert _growth_weighted([_growth_score(999.0, 0.0)] * 4) == 5.0          # all-best
    assert _growth_weighted([_growth_score(-999.0, 999.0)] * 4) == 1.0       # all-worst


def test_profitability_weighted_in_unit_range():
    assert _prof_weighted([_profitability_score(None, None)] * 5) == 3.0
    assert _prof_weighted([_profitability_score(9999.0, 0.01)] * 5) == 5.0
    assert _prof_weighted([_profitability_score(-9999.0, 0.50)] * 5) == 1.0


def test_valuation_weighted_in_unit_range():
    assert _val_weighted([_valuation_score(None, None)] * 5) == 3.0
    assert _val_weighted([_valuation_score(0.5, 50.0)] * 5) == 5.0           # ultra-cheap vs rich peer
    assert _val_weighted([_valuation_score(9999.0, 1.0)] * 5) == 1.0


def test_health_weighted_in_unit_range():
    # weighted = 0.4 * z_rating + 0.6 * pass_rating, both component ratings in [1,5].
    for z in (None, 0.0, 1.5, 2.0, 3.5, 9.0):
        zr = _zscore_rating(z)
        assert 1 <= zr <= 5
        for pass_rating in (1, 2, 3, 4, 5):
            w = 0.4 * zr + 0.6 * pass_rating
            assert 1.0 <= w <= 5.0
