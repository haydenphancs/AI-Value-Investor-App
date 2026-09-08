"""Guard: Street estimates are additive and never resurrect the zero-analyst falsehood.

WHY
---
`grades` and `price-target-consensus` are outside the signed FMP Order Form and answer 402,
so the ratings half of this response is all zero defaults and iOS renders `EmptyView()`
because `section_available` is False. `analyst-estimates` IS entitled (package 8) and gives
forward revenue/EPS with real analyst counts — a genuinely different dataset.

THE TRAP THIS FILE EXISTS FOR
-----------------------------
Wiring the new data in by flipping `section_available` to True would make every
ALREADY-SHIPPED iOS build render the legacy fields it guards — a confident `HOLD` at a
`$0.00` price target — to exactly the users who cannot update. So the estimates ride on
their own flag, every legacy key keeps being emitted, and `analyst_is_usable()` (which the
chat and the 20-credit report gate on) stays about the RATINGS half.

Hermetic: no network, no Supabase.
"""

from __future__ import annotations

import pytest

from app.schemas.analyst import AnalystAnalysisResponse
from app.services.analyst_service import AnalystService, _MIN_ESTIMATE_ANALYSTS
from app.services._analyst_common import (
    analyst_estimates_available,
    analyst_is_usable,
    analyst_section_available,
)

TODAY = "2026-09-08"


def _row(date, eps_avg=5.0, n=10, **kw):
    base = {
        "date": date,
        "revenueLow": 90.0, "revenueAvg": 100.0, "revenueHigh": 110.0,
        "epsLow": eps_avg - 1, "epsAvg": eps_avg, "epsHigh": eps_avg + 1,
        "numAnalystsEps": n, "numAnalystsRevenue": n,
    }
    base.update(kw)
    return base


# ── The separation of the two datasets ──────────────────────────────────────────────────

def test_estimates_are_entitled_while_ratings_are_not():
    """The premise. If this ever changes, the whole two-flag design should be revisited."""
    assert analyst_section_available() is False
    assert analyst_estimates_available() is True


def test_the_response_defaults_keep_an_old_client_safe():
    """Every estimate field is additive and defaulted, so a payload cached before this
    shipped still validates and an old iOS build simply never decodes them."""
    f = AnalystAnalysisResponse.model_fields
    for name in ("estimates_available", "estimates_have_coverage", "estimates",
                 "estimates_period", "estimates_next_period"):
        assert f[name].is_required() is False, f"{name} must be defaulted"
    assert f["estimates_available"].default is False
    assert f["estimates_have_coverage"].default is False


def test_estimates_never_flip_the_ratings_flags():
    """The load-bearing assertion of this file.

    `section_available` gates `EmptyView()` on every shipped build. If having estimates
    flipped it, those builds would render the legacy zero fields as a real HOLD at $0.00.
    """
    payload = {
        "symbol": "AAPL", "total_analysts": 0, "updated_date": "", "consensus": "HOLD",
        "target_price": 0.0, "target_upside": 0.0, "distributions": [],
        "price_target": {"low_price": 0, "average_price": 0, "high_price": 0,
                         "current_price": 230.0},
        "momentum_data": [], "net_positive": 0, "net_negative": 0,
        "actions_summary": {"upgrades": 0, "maintains": 0, "downgrades": 0}, "actions": [],
        "has_coverage": False, "section_available": False,
        "estimates_available": True, "estimates_have_coverage": True,
        "estimates": [{"fiscal_period": "FY2027", "date": "2027-09-27", "is_forward": True}],
    }
    r = AnalystAnalysisResponse.model_validate(payload)
    assert r.section_available is False and r.has_coverage is False
    assert r.estimates_have_coverage is True
    # ...and nothing may quote a rating or a target off the back of having estimates.
    assert analyst_is_usable(r) is False, (
        "analyst_is_usable is about the RATINGS half; estimates carry no consensus and no "
        "price target, and letting them satisfy it would put a $0 target back in a prompt"
    )


def test_every_legacy_key_survives():
    """13 of 15 iOS DTO fields are non-Optional — dropping one crashes every shipped build."""
    r = AnalystAnalysisResponse.model_validate({
        "symbol": "AAPL", "total_analysts": 0, "updated_date": "", "consensus": "HOLD",
        "target_price": 0.0, "target_upside": 0.0, "distributions": [],
        "price_target": {"low_price": 0, "average_price": 0, "high_price": 0,
                         "current_price": 1.0},
        "momentum_data": [], "net_positive": 0, "net_negative": 0,
        "actions_summary": {"upgrades": 0, "maintains": 0, "downgrades": 0}, "actions": [],
    })
    dumped = r.model_dump()
    for key in ("symbol", "total_analysts", "updated_date", "consensus", "has_coverage",
                "section_available", "target_price", "target_upside", "distributions",
                "price_target", "momentum_data", "net_positive", "net_negative",
                "actions_summary", "actions"):
        assert key in dumped and dumped[key] is not None, f"{key} missing or null"


# ── The builder ─────────────────────────────────────────────────────────────────────────

def test_rows_are_sorted_ascending_regardless_of_upstream_order():
    """FMP returns furthest-future FIRST. Nothing should depend on that going unchanged."""
    rows = [_row("2030-09-27"), _row("2026-09-27"), _row("2028-09-27")]
    out = AnalystService._build_estimates(rows, today=TODAY)
    assert [p.date for p in out] == ["2026-09-27", "2028-09-27", "2030-09-27"]


def test_is_forward_is_relative_to_today():
    out = AnalystService._build_estimates(
        [_row("2025-09-27"), _row("2027-09-27")], today=TODAY
    )
    assert [(p.fiscal_period, p.is_forward) for p in out] == [
        ("FY2025", False), ("FY2027", True)
    ]


@pytest.mark.parametrize("n_eps,n_rev,kept", [
    (1, 1, False),   # GME: measured, a single desk across all eight years
    (2, 1, True),
    (1, 2, True),    # AMC: measured, thin on EPS but not on revenue
    (0, 0, False),
    (None, None, False),
])
def test_thin_coverage_is_dropped_not_annotated(n_eps, n_rev, kept):
    """One desk's forecast is not "the Street". A card has nowhere to render "n=1", so the
    honest option is absence — the same rule `market_movers_service` uses for thin groups."""
    out = AnalystService._build_estimates(
        [_row("2027-09-27", numAnalystsEps=n_eps, numAnalystsRevenue=n_rev)], today=TODAY
    )
    assert bool(out) is kept
    assert _MIN_ESTIMATE_ANALYSTS == 2


def test_an_absent_number_is_none_never_zero():
    """A $0 revenue forecast beside a real analyst count is a fabricated measurement."""
    out = AnalystService._build_estimates([{
        "date": "2027-09-27", "numAnalystsEps": 9, "numAnalystsRevenue": 9,
        "epsAvg": 4.0,   # eps present; revenue entirely absent
    }], today=TODAY)
    assert len(out) == 1
    assert out[0].revenue is None, "an absent range must be omitted, not zero-filled"
    assert out[0].eps.avg == 4.0
    assert out[0].eps.low is None and out[0].eps.high is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), None, "abc", True])
def test_non_finite_estimates_are_dropped(bad):
    """NaN defeats `<= 0` guards AND `except (TypeError, ValueError)`; guard on isfinite."""
    assert AnalystService._opt_num(bad) is None


def test_a_period_with_no_usable_line_items_is_dropped():
    out = AnalystService._build_estimates(
        [{"date": "2027-09-27", "numAnalystsEps": 9, "numAnalystsRevenue": 9}], today=TODAY
    )
    assert out == [], "a period with analyst counts but no numbers has nothing to render"


@pytest.mark.parametrize("rows", [[], None, "nonsense", [None], [{}], [{"date": "oops"}]])
def test_degenerate_payloads_never_raise(rows):
    assert AnalystService._build_estimates(rows, today=TODAY) == []


def test_fiscal_period_label_comes_from_the_date():
    out = AnalystService._build_estimates([_row("2027-01-31")], today=TODAY)
    assert out[0].fiscal_period == "FY2027"


# ── backend ↔ iOS contract ──────────────────────────────────────────────────────────────

def _swift(rel: str) -> str:
    from pathlib import Path
    import re

    src = (Path(__file__).resolve().parents[2] / rel).read_text()
    return "\n".join(
        "" if l.strip().startswith("//") else re.sub(r"\s//.*$", "", l)
        for l in src.splitlines()
    )


def test_every_estimate_json_key_is_decoded_by_ios():
    """A renamed field is a decode failure in production, so pin the wire names.

    Comment-stripped, because the prose around these DTOs names every key.
    """
    from app.schemas.analyst import AnalystEstimatePeriod as P

    code = _swift("frontend/ios/ios/Core/Repositories/StockRepository.swift")
    assert "struct AnalystEstimatePeriodDTO" in code, "the iOS DTO is gone"

    wire = {
        "fiscal_period", "date", "is_forward", "revenue", "ebitda", "ebit",
        "net_income", "eps", "num_analysts_revenue", "num_analysts_eps",
    }
    assert set(P.model_fields) == {
        "fiscal_period", "date", "is_forward", "revenue", "ebitda", "ebit",
        "net_income", "eps", "num_analysts_revenue", "num_analysts_eps",
    }, "the Pydantic period shape moved — update the iOS DTO in the same change"

    for key in wire:
        assert key in code, f"iOS never decodes {key!r}"

    for key in ("estimates_available", "estimates_have_coverage",
                "estimates", "estimates_next_period"):
        assert key in code, f"iOS never decodes top-level {key!r}"


def test_the_ios_estimate_dtos_are_optional_where_the_backend_sends_null():
    """`low/avg/high` are `Optional[float]` server-side precisely so an unknown number is
    absent rather than zero. Decoding them as non-Optional `Double` would either crash on
    null or, worse, require the backend to send 0.0 and undo the whole point."""
    code = _swift("frontend/ios/ios/Core/Repositories/StockRepository.swift")
    start = code.index("struct AnalystEstimateRangeDTO")
    block = code[start:code.index("}", start)]
    for field in ("low", "avg", "high"):
        assert f"let {field}: Double?" in block, (
            f"AnalystEstimateRangeDTO.{field} must be Optional — the backend sends null "
            "for an unknown forecast and a 0.0 would read as a measurement"
        )


def test_ios_renders_an_em_dash_not_a_zero_for_a_missing_forecast():
    code = _swift("frontend/ios/ios/Models/TickerDetailModels.swift")
    start = code.index("struct AnalystEstimatePeriod")
    block = code[start:code.index("\nstruct ", start + 10)] if "\nstruct " in code[start + 10:] else code[start:]
    for prop in ("formattedRevenue", "formattedEPS"):
        i = block.index(prop)
        body = block[i:i + 260]
        assert 'return "—"' in body, (
            f"{prop} must render an em dash when the value is nil, never a formatted zero"
        )
