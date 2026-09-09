"""A "trailing twelve month" total must actually span twelve months.

Both health services sum the last four quarterly income statements. Below four quarters
`health_snapshot_service` fell back to `sorted_q = quarterly[:4]` — the SAME records, minus
the sort — under the comment "Better to expose partial data than render '—' for new
tickers". So a company with two filings (a recent IPO, or a symbol FMP holds partial history
for: GMRS returns exactly 2 quarterly records, verified live) had its **half-year** EBIT and
revenue summed and published as TTM.

Not cosmetic: Altman Z weights `ebit/assets` at **3.3** and `revenue/assets` at 1.0, so
halving both numerators can move the verdict a whole band — and the Z is 40% of that card's
rating. `health_check_service` was fixed for this; its twin was not, and the twin's own
docstring claimed the fixed behaviour the whole time.
"""
from __future__ import annotations

import pytest

from app.services.health_check_service import _sum_ttm_income as _check_ttm
from app.services.health_snapshot_service import _sum_ttm_income as _snapshot_ttm

_IMPLS = pytest.mark.parametrize(
    "ttm", [_check_ttm, _snapshot_ttm], ids=["health_check", "health_snapshot"],
)


def _q(date, revenue=100.0, ebit=20.0):
    return {"date": date, "revenue": revenue, "operatingIncome": ebit}


_FOUR = [_q("2025-03-31"), _q("2025-06-30"), _q("2025-09-30"), _q("2025-12-31")]


@_IMPLS
@pytest.mark.parametrize("n", [1, 2, 3])
def test_fewer_than_four_quarters_yields_nothing(ttm, n):
    assert ttm(_FOUR[:n]) == {}, f"{n} quarter(s) were summed and labelled TTM"


@_IMPLS
def test_four_quarters_sum(ttm):
    out = ttm(_FOUR)
    assert out["revenue"] == 400.0
    assert out["operatingIncome"] == 80.0


@_IMPLS
def test_only_the_four_most_recent_are_used(ttm):
    """A fifth, older quarter must not inflate the total — and the records arrive in
    arbitrary order, so the sort is load-bearing."""
    out = ttm([_q("2024-12-31", revenue=999.0)] + list(reversed(_FOUR)))
    assert out["revenue"] == 400.0


@_IMPLS
def test_a_field_missing_in_any_quarter_is_dropped_not_partially_summed(ttm):
    q = [dict(r) for r in _FOUR]
    del q[2]["operatingIncome"]
    out = ttm(q)
    assert "operatingIncome" not in out
    assert out["revenue"] == 400.0        # the others survive


@_IMPLS
@pytest.mark.parametrize("bad", [None, {}, "not a list", 42])
def test_a_malformed_payload_degrades_instead_of_raising(ttm, bad):
    assert ttm(bad) == {}


@_IMPLS
def test_non_dict_rows_are_filtered(ttm):
    """FMP error shapes returned with a 200 iterate as string KEYS."""
    assert ttm(["oops", None, 7] + _FOUR)["revenue"] == 400.0


@_IMPLS
def test_a_partial_ttm_would_have_halved_the_heaviest_altman_term(ttm):
    """The consequence, made concrete: the two-quarter sum is exactly half, and it lands
    in a term weighted 3.3."""
    from app.services.health_check_service import _compute_z_score

    bs = {"totalAssets": 1000.0, "totalLiabilities": 400.0,
          "totalCurrentAssets": 300.0, "totalCurrentLiabilities": 200.0,
          "retainedEarnings": 100.0}
    full = _compute_z_score(bs, ttm(_FOUR), 800.0)
    half = _compute_z_score(bs, {"revenue": 200.0, "operatingIncome": 40.0}, 800.0)
    assert full is not None and half is not None
    assert full - half > 0.1, (full, half)
    # ...and with the guard in place the two-quarter input produces no TTM at all.
    assert ttm(_FOUR[:2]) == {}
    assert _compute_z_score(bs, ttm(_FOUR[:2]), 800.0) is None
