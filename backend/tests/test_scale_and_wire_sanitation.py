"""Two defects where a value was the right NUMBER on the wrong SCALE, or the wrong shape.

  1. `moat_scoring_service` fed a PERCENTAGE gross/operating margin against a sector
     median stored as a DECIMAL. The function's own docstring and its inline comment
     contradicted each other — "multiply to match sector_benchmarks scale (percentage)"
     versus "stores them in the same 0-1 scale, so no scaling needed" — and the code
     multiplied. Production medians settle it: `gross_margin` is 0.3419-0.3991 for
     Consumer Cyclical, topping out at 1.082. The ~100x mismatch SATURATED the driver, so
     a 6%-margin distributor and a 46%-margin firm both scored 10.0/10 and the metric
     contributed nothing to Brand Power for essentially every company.

     ⚠️ Not every "…to revenue" benchmark is a decimal, which is what made this easy to
     get wrong in either direction: `rd_to_revenue` and `sga_to_revenue` really are stored
     as PERCENTAGES (Biotechnology's R&D median is 200.0 — pre-revenue biotechs genuinely
     spend 200% of revenue on R&D). Their helpers correctly multiply and must keep doing so.

  2. `/stocks/{ticker}` and `/stocks/{ticker}/quote` ran `normalize_fmp_response` — the
     sole place `sanitize_non_finite` executes — over ONE of their four or five gathered
     FMP results, then wrote the rest in raw via `float(...)`. FMP emits bare NaN tokens,
     `json.loads` parses them, and the value reaches `JSONResponse`, which renders with
     allow_nan=False and raises INSIDE the renderer — after the handler's try/except has
     returned. A bare 500 for the whole screen that nothing catches and nothing logs.
"""

from __future__ import annotations

import inspect
import json
import math
import re

import pytest

from app.schemas.common import sanitize_non_finite
from app.services.moat_scoring_service import (
    _score_from_median_ratio,
    get_moat_scoring_service,
)

NAN, INF = float("nan"), float("inf")


# ── 1. margin scale ─────────────────────────────────────────────────────────

def test_margin_helpers_return_the_stored_decimal_scale():
    svc = get_moat_scoring_service()
    assert svc._gross_margin_ratio({"grossProfitMargin": 0.46}) == pytest.approx(0.46)
    assert svc._operating_margin_ratio({"operatingProfitMargin": 0.12}) == pytest.approx(0.12)


def test_the_gross_margin_driver_actually_discriminates():
    """The regression: on the percentage scale every one of these scored 10.0/10."""
    median = 0.3712                      # a real Consumer Cyclical median
    svc = get_moat_scoring_service()
    scores = [
        _score_from_median_ratio(
            svc._gross_margin_ratio({"grossProfitMargin": m}), median, higher_is_better=True
        )
        for m in (0.06, 0.20, 0.3712, 0.46, 0.75)
    ]
    assert len(set(scores)) > 1, f"the driver is saturated again: {scores}"
    assert scores == sorted(scores), f"a higher margin must not score lower: {scores}"
    assert scores[0] < 5.0 < scores[-1], (
        f"a 6% margin must score below and a 75% margin above the median: {scores}"
    )


def test_a_margin_at_the_median_scores_the_midpoint():
    assert _score_from_median_ratio(0.3712, 0.3712, higher_is_better=True) == pytest.approx(5.0)


def test_the_percentage_scaled_helpers_are_left_alone():
    """Anti-vacuity: rd/sga ARE stored as percentages — they must keep multiplying."""
    svc = get_moat_scoring_service()
    rd = svc._rd_to_revenue_pct({"revenue": 1000.0, "researchAndDevelopmentExpenses": 150.0})
    sga = svc._sga_to_revenue_pct(
        {"revenue": 1000.0, "sellingGeneralAndAdministrativeExpenses": 200.0}
    )
    assert rd == pytest.approx(15.0), "rd_to_revenue is stored as a percentage"
    assert sga == pytest.approx(20.0), "sga_to_revenue is stored as a percentage"


def test_no_margin_helper_still_multiplies_by_a_hundred():
    src = inspect.getsource(get_moat_scoring_service().__class__._gross_margin_ratio)
    stripped = "\n".join(
        "" if l.strip().startswith("#") else re.sub(r"\s#.*$", "", l)
        for l in src.splitlines()
    )
    # The docstring legitimately mentions the old behaviour; check the CODE lines only.
    code = [l for l in stripped.splitlines()
            if l.strip() and not l.strip().startswith(('"""', "'''", "⚠️"))]
    assert not any("* 100.0" in l for l in code), (
        "the gross-margin helper multiplies again — it must match the stored decimal scale"
    )


# ── 2. non-finite values must never reach the wire ──────────────────────────

@pytest.mark.parametrize("bad", [NAN, INF, -INF])
def test_sanitize_replaces_every_non_finite(bad):
    assert sanitize_non_finite(bad) is None
    assert sanitize_non_finite({"a": bad, "b": 1.0})["a"] is None
    assert sanitize_non_finite([bad, 2.0])[0] is None


@pytest.mark.parametrize("handler", ["get_stock_details", "get_stock_quote"])
def test_the_stock_handlers_sanitise_at_the_exit(handler):
    """`normalize_fmp_response` covers ONE of the gathered results, not the rest."""
    from app.api.v1.endpoints import stocks

    fn = getattr(stocks, handler, None)
    assert fn is not None, f"guard is stale — {handler} was renamed or removed"
    src = inspect.getsource(fn)
    stripped = "\n".join(
        "" if l.strip().startswith("#") else re.sub(r"\s#.*$", "", l)
        for l in src.splitlines()
    )
    assert "return sanitize_non_finite(response)" in stripped, (
        f"{handler} returns the response without sanitising the fields written AFTER "
        "normalize_fmp_response — a NaN there 500s the whole screen from inside the renderer"
    )
    # And it must be the LAST thing: a bare `return response` after it reopens the hole.
    tail = stripped[stripped.find("return sanitize_non_finite(response)"):]
    assert "return response" not in tail.replace("return sanitize_non_finite(response)", "")


def test_a_nan_written_after_normalisation_is_caught_by_the_exit_sanitise():
    """The concrete shape: floatShares comes from a DIFFERENT FMP call."""
    from app.schemas.common import normalize_fmp_response

    response = normalize_fmp_response({"symbol": "AAPL", "price": 100.0})
    response["float_shares"] = float(NAN)          # the post-normalisation write
    response["percent_institutional"] = float(INF)

    with pytest.raises(ValueError):
        json.dumps(response, allow_nan=False)      # what the renderer would do

    cleaned = sanitize_non_finite(response)
    json.dumps(cleaned, allow_nan=False)           # must not raise
    assert cleaned["float_shares"] is None
    assert cleaned["percent_institutional"] is None
    assert cleaned["price"] == 100.0               # good data untouched


def test_the_benchmark_writer_still_stores_margins_as_decimals():
    """The AUTHORITY for the scale, pinned so a writer change cannot silently break it.

    `sector_benchmark_service` declares gross_margin / operating_margin / net_margin as
    `type: "direct"` straight from FMP's ratios fields, which are 0-1 — no x100 anywhere.
    If that ever changes to a computed x100, `_gross_margin_ratio` must change with it or
    the driver saturates again.
    """
    from app.services import sector_benchmark_service as sbs

    src = inspect.getsource(sbs)
    for metric, field in [("gross_margin", "grossProfitMargin"),
                          ("operating_margin", "operatingProfitMargin")]:
        row = next((l for l in src.splitlines()
                    if f'"name": "{metric}"' in l), None)
        assert row is not None, f"guard is stale — {metric} is no longer declared"
        assert '"type": "direct"' in row, (
            f"{metric} is no longer a direct FMP ratio — re-check the consumer's scale"
        )
        assert field in row
