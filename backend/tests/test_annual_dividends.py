"""Guard: annual dividend AMOUNTS, and the zero that means two different things.

WHY
---
FMP's `/dividends` went outside the signed Order Form on 2026-09-03, taking the
per-payment feed with it. `ratios` (period=annual) is entitled and carries the amounts —
verified exact against declared totals: KO 2024 = 1.9399 against a declared $1.94,
2025 = 2.0402 against $2.04.

⚠️ `ratios period=quarter` ALSO has `dividendPerShare` and is a trap. It is
`commonDividendsPaid / weightedAverageShsOut`, i.e. cash that settled in the period, so KO
reads $0.0207 in Q1 2025 and $1.0199 in Q4 2025 on a dividend that never changed — a 50x
swing driven purely by payment timing. Only the annual figure is safe, because over a full
year the timing cancels.

THE ZERO
--------
A `0.0` in this series means "paid nothing that year", which is a MEASUREMENT — and the
same literal zero appears for years before a company ever paid, which is not. The two are
separated by position: leading zeros are trimmed, everything else is kept. Measured:

    INTC  1.4598 -> 0.7370 -> 0.3736 -> 0.0000   (wound down, then suspended)
    META  0, 0, 0, 0, 2.0016, 2.1119             (did not pay before 2024)

Dropping Intel's trailing zero deletes the most important point in the series; rendering
META's four leading zeros is noise. Hermetic — no network.
"""

from __future__ import annotations

import pytest

from app.schemas.signal_of_confidence import AnnualDividendSchema
from app.services.signal_of_confidence_service import (
    SignalOfConfidenceService as S,
    _ANNUAL_DIVIDEND_YEARS,
)


def _rows(pairs):
    return [{"date": f"{y}-12-31", "dividendPerShare": v} for y, v in pairs]


def _series(pairs):
    return [AnnualDividendSchema(year=str(y), per_share=v) for y, v in pairs]


# ── Real measured series ────────────────────────────────────────────────────────────────

def test_a_steady_grower_is_reported_in_order():
    out = S._build_annual_dividends(_rows([
        (2020, 0.8115), (2021, 0.8662), (2022, 0.9152),
        (2023, 0.9543), (2024, 0.9928), (2025, 1.0316),   # AAPL, measured
    ]))
    assert [p.year for p in out] == ["2020", "2021", "2022", "2023", "2024", "2025"]
    assert out[-1].per_share == 1.0316
    assert S._dividend_growth(out) == (27.1, 5)


def test_a_suspended_dividend_keeps_its_trailing_zero():
    """INTC, measured. The final 0.0000 is the whole story and must survive."""
    out = S._build_annual_dividends(_rows([
        (2020, 1.3260), (2021, 1.3905), (2022, 1.4598),
        (2023, 0.7370), (2024, 0.3736), (2025, 0.0),
    ]))
    assert len(out) == 6
    assert out[-1].per_share == 0.0, "the suspension was dropped from the series"
    assert S._dividend_growth(out) == (-100.0, 5)


def test_a_new_payer_has_its_leading_zeros_trimmed_and_no_growth_rate_invented():
    """META/GOOGL, measured. Growth from zero is not -- and must not render as -- a number."""
    out = S._build_annual_dividends(_rows([
        (2020, 0.0), (2021, 0.0), (2022, 0.0), (2023, 0.0), (2024, 2.0016), (2025, 2.1119),
    ]))
    assert [p.year for p in out] == ["2024", "2025"], "leading zeros were rendered"
    assert S._dividend_growth(out) == (5.5, 1)


def test_a_company_that_has_never_paid_has_no_series_at_all():
    """BRK-B / TSLA, measured. A flat line at $0.00 across six years is not a dividend
    history; it is the absence of one, and the card is hidden on the strength of this."""
    assert S._build_annual_dividends(_rows([(y, 0.0) for y in range(2020, 2026)])) == []


def test_an_interior_zero_survives():
    """A skipped year between two paying ones is real and is not a leading zero."""
    out = S._build_annual_dividends(_rows([(2022, 1.0), (2023, 0.0), (2024, 1.2)]))
    assert [p.per_share for p in out] == [1.0, 0.0, 1.2]


# ── Growth is None when it is undefined, never 0.0 ──────────────────────────────────────

@pytest.mark.parametrize("pairs,why", [
    ([], "no data"),
    ([(2025, 1.0)], "a single year has no rate"),
    ([(2024, 0.0), (2025, 1.0)], "growth from zero is undefined, not infinite"),
])
def test_growth_is_none_when_undefined(pairs, why):
    assert S._dividend_growth(_series(pairs)) == (None, None), why


def test_growth_spans_are_reported_so_a_label_cannot_overstate_them():
    """GOOGL's history is one year long. A card saying "5Y growth" over it would lie."""
    _, years = S._dividend_growth(_series([(2024, 0.5977), (2025, 0.8294)]))
    assert years == 1


def test_two_rows_in_the_same_year_do_not_produce_a_zero_span():
    """A zero span would divide by zero or render "over 0y"."""
    assert S._dividend_growth(_series([(2025, 1.0), (2025, 2.0)])) == (None, None)


# ── Malformed upstream ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rows", [
    None, [], "nonsense", 42, [None], [{}], [{"date": None}], [{"date": "oops"}],
    [{"date": "2025-12-31"}],                                   # no dividend field
    [{"date": "2025-12-31", "dividendPerShare": None}],
    [{"date": "2025-12-31", "dividendPerShare": "x"}],
    [{"date": "2025-12-31", "dividendPerShare": float("nan")}],
    [{"date": "2025-12-31", "dividendPerShare": float("inf")}],
    [{"date": "2025-12-31", "dividendPerShare": -1.0}],          # negative is not a payout
])
def test_malformed_rows_never_raise_and_never_fabricate(rows):
    assert S._build_annual_dividends(rows) == []


def test_a_negative_payout_is_dropped_even_between_two_good_years():
    """A dividend cannot be negative, and the leading-zero trim does NOT catch this.

    Mutation-testing found it: with a lone negative row the trim already returns `[]`, so
    removing the `value < 0` guard looked harmless. Surrounded by real years it is not —
    the row survives and renders as a negative dividend.
    """
    out = S._build_annual_dividends(
        _rows([(2023, 1.0), (2024, -1.0), (2025, 1.2)])
    )
    assert [p.year for p in out] == ["2023", "2025"]
    assert all(p.per_share >= 0 for p in out)


def test_a_bad_row_does_not_take_the_good_ones_with_it():
    out = S._build_annual_dividends(
        _rows([(2023, 1.0), (2025, 1.2)]) + [{"date": "2024-12-31", "dividendPerShare": None}]
    )
    assert [p.year for p in out] == ["2023", "2025"]


def test_rows_are_sorted_regardless_of_upstream_order():
    """FMP returns newest first; nothing should depend on that."""
    out = S._build_annual_dividends(_rows([(2025, 3.0), (2023, 1.0), (2024, 2.0)]))
    assert [p.year for p in out] == ["2023", "2024", "2025"]


def test_the_window_covers_a_full_cut_and_recover_cycle():
    """Intel took four years to go from 1.4598 to 0. A shorter window would show only the
    tail and read as a company that has simply never paid much."""
    assert _ANNUAL_DIVIDEND_YEARS >= 6


# ── backend ↔ iOS contract ──────────────────────────────────────────────────────────────

def _swift(rel: str) -> str:
    from pathlib import Path
    import re

    src = (Path(__file__).resolve().parents[2] / rel).read_text()
    return "\n".join(
        "" if l.strip().startswith("//") else re.sub(r"\s//.*$", "", l)
        for l in src.splitlines()
    )


def test_ios_decodes_every_annual_dividend_key():
    """A renamed wire field is a decode failure in production. Comment-stripped, because
    the prose around these DTOs names every key."""
    from app.schemas.signal_of_confidence import (
        AnnualDividendSchema as A,
        DividendInfoSchema as D,
    )

    repo = _swift("frontend/ios/ios/Core/Repositories/StockRepository.swift")
    assert "struct AnnualDividendDTO" in repo, "the iOS DTO is gone"
    assert set(A.model_fields) == {"year", "per_share"}, (
        "the annual row shape moved — update AnnualDividendDTO in the same change"
    )
    for key in ("annual_dividends", "dividend_per_share", "dividend_per_share_year",
                "dividend_growth_pct", "dividend_growth_years"):
        assert f'= "{key}"' in repo, f"iOS never decodes {key!r}"

    # ⚠️ Bounded to AnnualDividendDTO, and matched as a CodingKey rather than as a bare
    # substring. `"per_share" in repo` passes on `"dividend_per_share"` — mutation-testing
    # caught exactly that, so renaming the nested key looked harmless.
    nested = repo[repo.index("struct AnnualDividendDTO"):]
    nested = nested[:nested.index("\n}")]
    assert 'case perShare = "per_share"' in nested, (
        "AnnualDividendDTO no longer decodes `per_share` — every annual amount would "
        "arrive as a decode failure"
    )
    assert "case year" in nested

    # Every new field must be Optional on the wire so an older backend still decodes.
    start = repo.index("struct DividendInfoDTO")
    block = repo[start:repo.index("enum CodingKeys", start)]
    for field, swift_type in (("annualDividends", "[AnnualDividendDTO]?"),
                              ("dividendPerShare", "Double?"),
                              ("dividendPerShareYear", "String?"),
                              ("dividendGrowthPct", "Double?"),
                              ("dividendGrowthYears", "Int?")):
        assert f"let {field}: {swift_type}" in block, (
            f"DividendInfoDTO.{field} must be `{swift_type}` — the backend deploys before "
            "the client, and a non-Optional would crash on an older payload"
        )
    assert D.model_fields["dividend_growth_pct"].is_required() is False


def test_ios_hides_the_growth_row_rather_than_rendering_a_zero():
    """Growth is genuinely undefined for a company that started paying inside the window
    (GOOGL and META, both 2024). `formattedGrowth` returns nil and the card drops the row —
    it must never fall back to "+0.0%", which reads as "the dividend is flat"."""
    models = _swift("frontend/ios/ios/Models/SignalOfConfidenceModels.swift")
    i = models.index("var formattedGrowth")
    body = models[i:i + 300]
    assert "var formattedGrowth: String? {" in models, (
        "formattedGrowth must be Optional, not a formatted zero"
    )
    assert "return nil" in body

    card = _swift("frontend/ios/ios/Views/Molecules/DividendInfoCard.swift")
    assert "if let growth = dividendInfo.formattedGrowth {" in card, (
        "the card renders the growth row unconditionally, so an undefined rate shows as a "
        "number"
    )


def test_ios_hides_the_payment_date_row_while_it_is_unobtainable():
    """A payment date cannot be derived from price series the way an ex-date can, so it is
    permanently nil. A row reading "Payment Date  N/A" on every stock is chrome."""
    card = _swift("frontend/ios/ios/Views/Molecules/DividendInfoCard.swift")
    assert "if dividendInfo.paymentDate != nil {" in card


def test_ios_formats_a_token_dividend_without_rounding_it_to_zero():
    """NVDA paid $0.0160/share for four straight years. Two decimals renders that $0.02,
    and a hypothetical smaller one $0.00 — a fabricated non-payer."""
    models = _swift("frontend/ios/ios/Models/SignalOfConfidenceModels.swift")
    i = models.index("var formattedPerShare")
    assert '"$%.4f"' in models[i:i + 220], "per-share must keep 4 decimals"


# ── Who gets a dividend card at all ─────────────────────────────────────────────────────

def _svc():
    from app.services.signal_of_confidence_service import SignalOfConfidenceService
    return SignalOfConfidenceService.__new__(SignalOfConfidenceService)


class _DP:
    def __init__(self, y): self.dividend_yield = y


def test_the_per_share_record_outranks_a_rounding_level_yield():
    """TSLA has never paid a common dividend, yet shows a 0.01% trailing yield.

    That yield is `dividendsPaid / market cap` from the cash-flow statement, which picks up
    preferred and one-off distributions — a different question from "does this company pay
    a dividend". On the strength of it TSLA rendered a whole dividend card of em dashes.
    When the authoritative annual per-share record is present and says zero, it wins.
    """
    never_paid = _rows([(y, 0.0) for y in range(2020, 2026)])
    assert _svc()._build_dividend_info(
        [], 0.01, 1.0, 0.0, data_points=[_DP(0.02)], annual_ratios=never_paid
    ) is None


def test_a_token_but_real_payer_still_gets_a_card():
    """NVDA's yield is the same order of magnitude as TSLA's noise — the difference is
    that NVDA has a real per-share record, which is exactly why the record decides."""
    info = _svc()._build_dividend_info(
        [], 0.02, 1.0, 0.0, data_points=[],
        annual_ratios=_rows([(2024, 0.016), (2025, 0.034), (2026, 0.04)]),
    )
    assert info is not None
    assert info.dividend_per_share == 0.04


def test_a_failed_ratios_fetch_falls_back_to_the_yield():
    """One upstream call going down must not hide a real payer's card."""
    info = _svc()._build_dividend_info(
        [], 2.4, 1.0, 0.0, data_points=[], annual_ratios=[]
    )
    assert info is not None, "a payer lost its card because `ratios` was unavailable"
    assert info.annual_dividends == []
    assert info.dividend_per_share is None


def test_a_suspended_payer_keeps_its_card():
    """INTC yields nothing now, but its history is the point."""
    info = _svc()._build_dividend_info(
        [], 0.0, 1.0, 0.0, data_points=[],
        annual_ratios=_rows([(2022, 1.4598), (2023, 0.737), (2024, 0.3736), (2025, 0.0)]),
    )
    assert info is not None
    assert info.dividend_growth_pct == -100.0
