"""There must be exactly ONE Altman Z-Score, and a missing term must omit it.

`health_check_service._compute_z_score` was fixed to return None rather than substitute 0
for a missing market value of equity — on Apple-shaped inputs the substitution turns Z=8.9
("fortress") into Z=2.1 ("Grey zone. Moderate financial stress signals"), a confident wrong
verdict that also moves `overall_rating` and is cached for 24h.

`stock_overview_service._build_health_snapshot` held a SECOND, independently written copy
that still had the defect — and worse, because that module's `_safe_float` returns `0.0`
rather than `None`, so `revenue` (weight 1.0) and `ebit` (weight **3.3**) had the same hole
with no way to tell an absent field from a measured zero.

The fix is to share the implementation, so the two cannot drift apart a third time.
"""
from __future__ import annotations

import math

import pytest

from app.services.health_check_service import _compute_z_score
from app.services.stock_overview_service import StockOverviewService


# Apple-shaped, in whole dollars. The magnitudes are what make the substitution move the
# ZONE rather than just the precision.
_BS = {
    "totalAssets": 364_980_000_000.0,
    "totalLiabilities": 308_030_000_000.0,
    "totalCurrentAssets": 152_990_000_000.0,
    "totalCurrentLiabilities": 176_390_000_000.0,
    "retainedEarnings": -19_150_000_000.0,
}
_INC = {"operatingIncome": 123_220_000_000.0, "revenue": 391_040_000_000.0}
_MCAP = 3_400_000_000_000.0


def _overview_z(bs=None, inc=None, mcap=_MCAP):
    svc = StockOverviewService.__new__(StockOverviewService)
    item = svc._build_health_snapshot(
        bs if bs is not None else _BS,
        inc if inc is not None else _INC,
        {}, {}, {}, mcap,
    )
    row = next(m for m in item.metrics if m.name == "Altman Z-Score")
    return row.value, item.rating


def test_the_two_services_agree_on_the_same_inputs():
    """The whole point of sharing: no drift."""
    value, _ = _overview_z()
    assert value == f"{_compute_z_score(_BS, _INC, _MCAP)}"


def test_a_healthy_company_still_scores_in_the_safe_zone():
    """Guard against "fix" by omission — the metric must still compute when it can."""
    value, rating = _overview_z()
    assert value != "—"
    assert float(value) > 3.0, value
    assert rating == 5


@pytest.mark.parametrize("missing", ["market_cap", "ebit", "revenue"])
def test_a_missing_material_term_omits_the_metric_rather_than_scoring_zero(missing):
    """Substituting 0 is what turned a fortress into a distress verdict."""
    if missing == "market_cap":
        value, rating = _overview_z(mcap=None)
    else:
        inc = dict(_INC)
        inc.pop({"ebit": "operatingIncome", "revenue": "revenue"}[missing])
        value, rating = _overview_z(inc=inc)
    assert value == "—", f"{missing} was substituted rather than omitted"
    assert rating == 0


def test_a_zero_market_cap_is_treated_as_absent_not_as_a_worthless_company():
    """`_safe_float` in `stock_overview_service` defaults to 0.0, so "no quote AND no
    profile" arrives as 0.0, not None — indistinguishable from a real zero, and a listed
    company's equity is never actually worth nothing."""
    value, _ = _overview_z(mcap=0.0)
    assert value == "—"


def test_ebitda_is_no_longer_substituted_for_ebit():
    """EBITDA adds back D&A, so using it in the EBIT slot overstates the 3.3-weighted term
    — the heaviest in the formula."""
    inc = {"ebitda": 134_660_000_000.0, "revenue": _INC["revenue"]}
    value, rating = _overview_z(inc=inc)
    assert value == "—"
    assert rating == 0


def test_a_measured_zero_z_renders_as_zero_not_as_no_data():
    """`if z_score else "—"` treated a genuine deep-distress 0.0 as missing."""
    svc = StockOverviewService.__new__(StockOverviewService)
    # totalAssets/liabilities present, and terms chosen so the sum rounds to 0.0.
    bs = {"totalAssets": 100.0, "totalLiabilities": 100.0,
          "totalCurrentAssets": 0.0, "totalCurrentLiabilities": 0.0,
          "retainedEarnings": 0.0}
    inc = {"operatingIncome": 0.0, "revenue": 0.0}
    z = _compute_z_score(bs, inc, 0.0001)
    assert z is not None and abs(z) < 0.05, z
    item = svc._build_health_snapshot(bs, inc, {}, {}, {}, 0.0001)
    row = next(m for m in item.metrics if m.name == "Altman Z-Score")
    assert row.value != "—", "a measured 0.0 rendered as missing"


def test_there_is_only_one_z_score_formula_in_the_app():
    """Structural: the 1.2 / 1.4 / 3.3 / 0.6 / 1.0 weights must appear in exactly one place.
    A second copy is how this defect survived being fixed once already — there were FOUR.

    ⚠️ DOCSTRINGS are stripped, not just `#` comments. The first version of this guard
    stripped only `#` and immediately failed on the explanation beside one of the fixes
    ("Altman Z weights `ebit/assets` at **3.3**"), because `3.3\s*\*` matches inside
    `**3.3**`. That is the same vacuity trap in mirror image: prose that satisfies a scan
    would equally let a REVERTED fix pass. All three weights must co-occur, too — `0.6 *`
    alone appears in unrelated rating blends.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    hits = []
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                      # pragma: no cover - not our concern here
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
                if (node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)):
                    node.body = node.body[1:] or [ast.Pass()]
        body = ast.unparse(tree)
        # Comments are already gone (ast drops them); this is the FORMULA, all of it.
        if all(w in body for w in ("1.2 *", "1.4 *", "3.3 *", "0.6 *")):
            hits.append(str(path.relative_to(root)))
    assert hits == ["services/health_check_service.py"], hits
