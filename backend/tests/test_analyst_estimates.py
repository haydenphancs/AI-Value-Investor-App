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


# ── Repurchasing the package must RESTORE, never delete ─────────────────────────────────

def test_the_estimates_card_does_not_suppress_the_ratings_card():
    """🔴 It did — and that inverted the convention the whole rebuild rests on.

    `fmp_entitlements` advertises buying a package back as "one line … nothing else
    changes", and `analyst.py` as "flips back to True on its own". But the estimates card
    was the FIRST arm of an `if/else if` chain and was not conditioned on
    `sectionAvailable`, so in the post-repurchase state — `sectionAvailable`, `hasCoverage`
    and `estimatesAvailable` all true, i.e. every covered large cap — the chain
    short-circuited there and `AnalystRatingsSection` became unreachable. Repurchasing
    would have DELETED consensus, the price-target range, the momentum chart, the rating
    distribution and the "More" entry point.

    The two are different datasets and both belong on the tab, so the estimates card is now
    an additive sibling rendered above the chain rather than a branch inside it.

    Brace-bounded to `body` and comment-stripped — the prose above names every symbol.
    """
    import re
    from pathlib import Path

    view = (
        Path(__file__).resolve().parents[2]
        / "frontend/ios/ios/Views/Organisms/TickerAnalysisContent.swift"
    ).read_text()
    code = "\n".join(
        "" if l.strip().startswith("//") else re.sub(r"\s//.*$", "", l)
        for l in view.splitlines()
    )
    start = code.index("var body: some View")
    depth, body = 0, None
    for i in range(code.index("{", start), len(code)):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                body = code[code.index("{", start):i + 1]
                break
    assert body, "could not bound TickerAnalysisContent.body"

    est = body.find("ratingsData.estimatesAvailable")
    ratings = body.find("AnalystRatingsSection(")
    assert est != -1 and ratings != -1

    # The estimates block must NOT be an `else if` — that is what made it exclusive.
    est_line_start = body.rfind("\n", 0, est)
    est_stmt = body[est_line_start: est]
    preceding = body[max(0, est_line_start - 120): est_line_start]
    assert "else if" not in preceding.split("\n")[-1] and "else if" not in est_stmt, (
        "the estimates card is still an `else if` arm, so it suppresses the ratings chain "
        "whenever it renders — repurchasing the analyst package would delete the ratings card"
    )
    # ...and the ratings section must still be reachable in the same body.
    assert ratings > est, "AnalystRatingsSection must remain in the body below the estimates card"


def test_analyst_counts_stay_separate_through_the_boundary():
    """`max(eps, revenue)` walked around the `_MIN_ESTIMATE_ANALYSTS` floor.

    AMC's measured shape is `revenue=2, eps=1` — it clears the gate on the revenue count,
    and the EPS column, backed by a single desk, was then labelled "2 analysts". The floor
    exists precisely so one desk is never presented as "the Street".
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "frontend/ios/ios"
    repo = (root / "Core/Repositories/StockRepository.swift").read_text()
    code = "\n".join(
        "" if l.strip().startswith("//") else re.sub(r"\s//.*$", "", l)
        for l in repo.splitlines()
    )
    assert "max($0.numAnalystsEps" not in code, (
        "the two analyst counts are collapsed to max() at the boundary, so a 1-desk EPS "
        "column inherits the revenue column's count"
    )
    assert "revenueAnalysts:" in code and "epsAnalysts:" in code

    models = (root / "Models/TickerDetailModels.swift").read_text()
    assert "let revenueAnalysts: Int" in models and "let epsAnalysts: Int" in models


def test_the_estimate_revenue_formatter_cannot_print_a_fabricated_zero():
    """A $42M forecast rendered as "$0.0B" beside a live analyst count."""
    import re
    from pathlib import Path

    models = (
        Path(__file__).resolve().parents[2] / "frontend/ios/ios/Models/TickerDetailModels.swift"
    ).read_text()
    code = "\n".join(
        "" if l.strip().startswith("//") else re.sub(r"\s//.*$", "", l)
        for l in models.splitlines()
    )
    i = code.index("var formattedRevenue")
    block = code[i: i + 300]
    assert "CompactNumberFormat.string(" in block, (
        "formattedRevenue uses a private formatter again — a billions-only one renders a "
        "real $42M forecast as $0.0B and squashes $80M and $149M both to $0.1B"
    )
    assert "1_000_000_000" not in block, "a hand-rolled billions tier is back"


def test_every_backend_rating_label_has_an_ios_case():
    """The backend refuses to fold an unreadable grade into Hold; iOS must not either.

    `_analyst_common._RATING_RANK` is the vocabulary FMP actually emits. `mapRatingType`
    was missing five of them — including `sector underperform` and `market underperform`,
    both RANK 1 (Sells) — so they hit `default: nil` and the caller's `?? .neutral` turned
    a downgrade into "to Neutral". That string then flows into `groundingLines` and on into
    a credit-charged AI turn.
    """
    import re
    from pathlib import Path

    from app.services._analyst_common import _RATING_RANK

    repo = (
        Path(__file__).resolve().parents[2]
        / "frontend/ios/ios/Core/Repositories/StockRepository.swift"
    ).read_text()
    code = "\n".join(
        "" if l.strip().startswith("//") else re.sub(r"\s//.*$", "", l)
        for l in repo.splitlines()
    )
    start = code.index("private static func mapRatingType")
    block = code[start: code.index("\n    }", start)]
    handled = {m.lower() for m in re.findall(r'"([^"]+)"', block)}

    missing = sorted(set(_RATING_RANK) - handled)
    assert not missing, (
        f"iOS has no case for {missing} — each falls to `?? .neutral`, so a Sell is "
        f"rendered (and narrated to the model) as Neutral"
    )


def test_the_underperform_label_is_spelled_correctly():
    """`"Underpeform"` was a user-visible raw value AND an AI grounding string."""
    from pathlib import Path

    models = (
        Path(__file__).resolve().parents[2] / "frontend/ios/ios/Models/TickerDetailModels.swift"
    ).read_text()
    assert "Underpeform" not in models
    assert 'case underperform = "Underperform"' in models


# ── The per-column analyst floor ────────────────────────────────────────────────────


def test_a_thin_eps_column_is_nulled_while_a_real_revenue_column_survives():
    """AMC's measured shape: `numAnalystsRevenue=2, numAnalystsEps=1`.

    `max(1, 2) >= 2` keeps the period — correctly, the revenue column is real — but every
    column was then emitted regardless, so the EPS figure was ONE desk's guess rendered in
    a card headed "Street Estimates". Worse, `epsAnalystsLabel` correctly returns nil below
    the floor, so it appeared with no count beside it: nothing on screen said so.
    `_MIN_ESTIMATE_ANALYSTS` exists precisely so one desk is never called "the Street".
    """
    periods = AnalystService._build_estimates(
        [_row("2027-09-27", numAnalystsEps=1, numAnalystsRevenue=2)], today="2026-09-08"
    )

    assert len(periods) == 1
    assert periods[0].eps is None, "one desk is not the Street"
    assert periods[0].revenue is not None and periods[0].revenue.avg == 100.0
    assert periods[0].num_analysts_eps == 1, "the count itself is still reported"


def test_a_thin_revenue_column_takes_its_modelled_lines_with_it():
    """EBITDA / EBIT / net income are modelled off revenue and share its count."""
    periods = AnalystService._build_estimates(
        [_row("2027-09-27", numAnalystsEps=6, numAnalystsRevenue=1,
              ebitdaAvg=50.0, ebitAvg=40.0, netIncomeAvg=30.0)],
        today="2026-09-08",
    )

    assert len(periods) == 1
    p = periods[0]
    assert p.eps is not None and p.eps.avg == 5.0
    assert (p.revenue, p.ebitda, p.ebit, p.net_income) == (None, None, None, None)


def test_a_period_thin_on_both_columns_is_dropped_entirely():
    assert AnalystService._build_estimates(
        [_row("2027-09-27", numAnalystsEps=1, numAnalystsRevenue=1)], today="2026-09-08"
    ) == []


def test_a_well_covered_period_keeps_both_columns():
    """Mutation guard — the floor must not null everything."""
    periods = AnalystService._build_estimates([_row("2027-09-27")], today="2026-09-08")

    assert len(periods) == 1
    assert periods[0].eps is not None and periods[0].revenue is not None
