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

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ConfigDict

import app.services.signal_of_confidence_service as sos
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


# ── The status ratio must compare like with like ────────────────────────────────────────

def test_a_re_rated_stock_keeps_its_dividend_verdict():
    """🔴 Both sides of the ratio must use the SAME market-cap basis.

    `summary.dividend_yield` divides by the CURRENT cap; `five_year_avg_yield` averages
    per-quarter yields each divided by that quarter's POINT-IN-TIME cap. Comparing them
    directly meant a stock that merely re-rated upward scored low with no change at all in
    its payout.

    Measured on real data, 4 of 8 mega-caps changed verdict once the bases matched — most
    visibly JNJ, a dividend king, which the app reported as **"Low"**.

    Here: the payout never moves (every quarter yields 3.0%) but the cap has doubled, so the
    current-cap trailing yield reads 1.5%. The verdict must follow the payout, not the price.

    EIGHT points, not four. Four is the whole trailing window, leaving no independent
    baseline — the ratio path is refused and the absolute ladder runs instead, which is
    not what this test is about. (An earlier version used four and asserted "High"; that
    passed only because the ratio was then SELF-REFERENTIAL and identically 1.0.)
    """
    svc = _svc()
    flat = [_DP(3.0) for _ in range(8)]
    info = svc._build_dividend_info(
        [], 1.5, 1.0, 0.0, data_points=flat,
        annual_ratios=_rows([(2023, 1.0), (2024, 1.0), (2025, 1.0)]),
    )
    assert info is not None
    assert info.five_year_avg_yield == 3.0
    assert info.status != "Low", (
        "a stock whose payout never changed was marked Low purely because its market cap "
        "grew — the ratio compared a current-cap numerator against point-in-time denominators"
    )


def test_the_comparison_baseline_excludes_its_own_numerator():
    """🔴 The ratio must not contain the value it is measuring.

    Putting the trailing window on a point-in-time basis fixed the UNITS but left
    `five_year_avg_yield` — the mean of ALL points, the same four among them — as the
    denominator. Measured on that intermediate version:

      * 4 points  -> ratio IDENTICALLY 1.0, so a 0.05% token yield published as green "High"
      * 8 flat    -> ratio exactly 1.0, the first value of the "High" bucket
      * 3.00 -> 2.99 vs 2.99 -> 3.00 -> the verdict flipped Fair <-> High on a 0.3% wiggle
      * a 40% CUT -> still "Fair", because ratio = 2B/(A+B) compresses toward 1.0

    The baseline is now the OLDER points only, so the two sides vary independently.
    """
    svc = _svc()

    def status(older, newer, t12m):
        info = svc._build_dividend_info(
            [], t12m, 1.0, 0.0,
            data_points=[_DP(y) for y in ([older] * 4 + [newer] * 4)],
            annual_ratios=_rows([(2023, 1.0), (2024, 1.0), (2025, 1.0)]),
        )
        assert info is not None
        return info.status

    assert status(3.0, 1.8, 1.8) == "Low", "a 40% dividend cut read 'Fair'"
    assert status(3.0, 2.0, 2.0) == "Low", "a 33% cut"
    assert status(1.0, 4.0, 4.0) == "Very High", "a 4x increase"
    assert status(2.0, 2.5, 2.5) == "High", "a 25% raise"


def test_too_little_history_refuses_the_ratio_rather_than_faking_one():
    """Below four BASELINE quarters there is nothing independent to compare against.

    With exactly four points the trailing window IS the whole series, so any ratio built
    from them is 1.0 by construction — which put a 0.05% token yield in the green "High"
    bucket. The absolute ladder is the honest answer there.
    """
    svc = _svc()
    info = svc._build_dividend_info(
        [], 0.05, 1.0, 0.0, data_points=[_DP(0.05) for _ in range(4)],
        annual_ratios=_rows([(2023, 0.01), (2024, 0.01), (2025, 0.01)]),
    )
    assert info is not None
    assert info.status == "Low", "0.05% is a token yield, not a green 'High'"


def test_a_genuine_dividend_cut_still_reads_low():
    """The other direction — the fix must not make the ladder inert.

    Was `[1.0, 1.0, 1.0, 1.0]` with no assertion on `status` at all: a FLAT series under
    a name promising a falling one, checking only the average it was handed. It measured
    "High". The series below actually falls, and the verdict is asserted.
    """
    svc = _svc()
    falling = [_DP(4.0), _DP(4.0), _DP(4.0), _DP(4.0),
               _DP(1.0), _DP(1.0), _DP(1.0), _DP(1.0)]
    info = svc._build_dividend_info(
        [], 1.0, 1.0, 0.0, data_points=falling,
        annual_ratios=_rows([(2023, 4.0), (2024, 2.0), (2025, 1.0)]),
    )
    assert info is not None
    assert info.five_year_avg_yield == 2.5
    assert info.status == "Low", "a 75% cut must read Low"


# ── Supabase cache: a VERSION, not a key probe ──────────────────────────────────────


def _cache_svc():
    # `__new__` — the real __init__ constructs a Supabase client and an FMP client, and
    # `_check_supabase_cache` needs neither (the test injects `supabase`).
    return sos.SignalOfConfidenceService.__new__(sos.SignalOfConfidenceService)


def _cache_entry(response_json):
    return SimpleNamespace(data=[{
        "response_json": response_json,
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "next_earnings_date": None,
    }])


class _FakeTable:
    def __init__(self, result):
        self._result = result

    def select(self, *_a, **_k): return self
    def eq(self, *_a, **_k): return self
    def limit(self, *_a, **_k): return self
    def execute(self): return self._result


def _minimal_payload(**over):
    payload = {
        "symbol": "JNJ",
        "data_points": [],
        "summary": {
            "total_yield": 3.0, "dividend_yield": 3.0, "buyback_yield": 0.0,
            "share_count_change": 0.0, "buyback_status": "Low",
        },
        "dividend_info": None,
        "payload_version": sos._PAYLOAD_VERSION,
    }
    payload.update(over)
    return payload


def _read(monkeypatch, payload):
    svc = _cache_svc()
    monkeypatch.setattr(
        svc, "supabase",
        SimpleNamespace(table=lambda _n: _FakeTable(_cache_entry(payload))),
        raising=False,
    )
    return svc._check_supabase_cache("JNJ")


def test_a_current_row_is_served(monkeypatch):
    """Mutation guard: the version check must not reject everything."""
    out = _read(monkeypatch, _minimal_payload())
    assert out is not None and out.symbol == "JNJ"


def test_a_row_written_before_versioning_is_recomputed(monkeypatch):
    """The old guard tested `"buyback_status" not in summary` — one historical change.

    A row written after that key landed but BEFORE the annual-dividend fields passes it
    and is served for 24h with `annual_dividends` defaulted to `[]`: "no dividend
    history" for a dividend king. Worse, a row written before the `status` denominator
    was corrected also passes while carrying a value that is simply WRONG (JNJ cached as
    "Low"). A key probe cannot see a changed formula at all.
    """
    payload = _minimal_payload()
    del payload["payload_version"]
    assert _read(monkeypatch, payload) is None


@pytest.mark.parametrize("version", [1, 0, "2", None, sos._PAYLOAD_VERSION - 1,
                                     sos._PAYLOAD_VERSION + 1])
def test_any_other_version_is_recomputed(monkeypatch, version):
    """Both directions. A row from a NEWER deploy is also refused rather than coerced."""
    assert _read(monkeypatch, _minimal_payload(payload_version=version)) is None


def test_the_version_key_never_reaches_the_response_model(monkeypatch):
    """`payload_version` is cache metadata, not a response field.

    Pydantic v2 ignores extras by DEFAULT, so `assert not hasattr(out, ...)` passes
    whether or not the key is stripped — a vacuous guard (this test was written that way
    first, and the mutation run caught it). Asserting against a strict model is what
    actually exercises the strip: under a later `extra="forbid"` an unstripped key turns
    every cache hit into a ValidationError, swallowed by the broad `except` — a silent
    100% cache miss on a 6-FMP-call path, which would look like a latency regression
    rather than a bug.
    """
    class _Strict(sos.SignalOfConfidenceResponse):
        model_config = ConfigDict(extra="forbid")

    monkeypatch.setattr(sos, "SignalOfConfidenceResponse", _Strict)

    out = _read(monkeypatch, _minimal_payload())

    assert out is not None, (
        "the cached row failed to validate — `payload_version` reached the model"
    )


# ── A partial first paying year inflates every growth figure ────────────────────────


def _ratio_rows(pairs):
    return [{"date": f"{y}-12-31", "dividendPerShare": v} for y, v in pairs]


def test_a_recently_initiated_payer_gets_no_fabricated_growth():
    """GOOGL, live: 2024 = 0.60 (three $0.20 payments), 2025 = 0.83 (0.20 + three 0.21).

    `dividendPerShare` is a full-CALENDAR-year total, so an initiation part-way through
    the year books a fraction of the run-rate. Dividing straight through it rendered
    "Dividend Growth +38.3% over 1y" in the gain colour, for a per-quarter dividend that
    went 0.20 -> 0.21 (+5%). META is in the same position.

    One full year is not a growth rate. Undefined is the honest answer, exactly as it
    already is for the `0 -> N` case.
    """
    svc = _svc()
    rows = _ratio_rows([(2020, 0), (2021, 0), (2022, 0), (2023, 0),
                        (2024, 0.60), (2025, 0.83)])
    series = svc._build_annual_dividends(rows)

    assert [p.year for p in series] == ["2024", "2025"], "leading zeros still trimmed"
    assert svc._initiation_observed(rows, series) is True
    assert svc._dividend_growth(series, True) == (None, None)


def test_a_q4_initiation_that_never_rises_reads_flat_not_plus_300_percent():
    """0.25 (one payment) then 1.00 five years running.

    Measured on the unfixed code: `(300.0, 5)` -> "+300.0% over 5y" in green, for a
    dividend that has not moved in five years.
    """
    svc = _svc()
    rows = _ratio_rows([(2019, 0), (2020, 0.25)] + [(y, 1.00) for y in range(2021, 2026)])
    series = svc._build_annual_dividends(rows)

    assert svc._dividend_growth(series, svc._initiation_observed(rows, series)) == (0.0, 4)


def test_a_mature_payer_is_untouched():
    """The window merely BEGINS mid-stream — no observed zero, so no stub to drop.

    Mutation guard both ways: inferring "partial" from the shape of the numbers (a large
    year-two rise) would discard genuine raises here.
    """
    svc = _svc()
    rows = _ratio_rows([(2020, 1.0), (2021, 1.1), (2022, 1.2),
                        (2023, 1.3), (2024, 1.4), (2025, 1.5)])
    series = svc._build_annual_dividends(rows)

    assert svc._initiation_observed(rows, series) is False
    assert svc._dividend_growth(series, False) == (50.0, 5)


def test_a_wind_down_still_reports_minus_100():
    """Intel: 1.4598 -> 0.7370 -> 0.3736 -> 0.0000. The most important number on the card."""
    svc = _svc()
    rows = _ratio_rows([(2022, 1.4598), (2023, 0.7370), (2024, 0.3736), (2025, 0.0)])
    series = svc._build_annual_dividends(rows)

    assert svc._initiation_observed(rows, series) is False
    assert svc._dividend_growth(series, False) == (-100.0, 3)


def test_the_stub_probe_requires_an_observed_zero_not_a_guess():
    """A gap in the feed is not evidence of an initiation."""
    svc = _svc()
    rows = _ratio_rows([(2024, 0.60), (2025, 0.83)])   # no 2023 row at all
    series = svc._build_annual_dividends(rows)

    assert svc._initiation_observed(rows, series) is False, (
        "absent is not zero — without the prior year we cannot tell a stub from a raise"
    )


def test_yielding_your_own_history_is_fair_not_green():
    """The `>= 1.0 -> High` cut split hairs it cannot measure.

    T at ratio 0.993 and VZ at 1.011 are 1.8% apart in trailing yield and were rendered in
    different colours, one of them as a positive signal. Matching the two denominators put
    stable payers very close to 1.0 BY CONSTRUCTION, where the old mismatched bases
    scattered them — so the boundary went from rarely-hit to crowded.
    """
    svc = _svc()

    def status(older, newer):
        info = svc._build_dividend_info(
            [], newer, 1.0, 0.0,
            data_points=[_DP(y) for y in ([older] * 4 + [newer] * 4)],
            annual_ratios=_rows([(2023, 1.0), (2024, 1.0), (2025, 1.0)]),
        )
        assert info is not None
        return info.status

    assert status(3.00, 3.00) == "Fair", "exactly its own average is not a positive signal"
    assert status(6.21, 6.28) == "Fair", "VZ-like, ratio 1.011"
    assert status(4.54, 4.51) == "Fair", "T-like, ratio 0.993"


def test_the_dead_band_is_narrow_and_does_not_re_centre_the_ladder():
    """Measured: re-centring to 0.85/1.15 moves 10 of 20 real payers and drops JNJ (0.740)
    and CSCO (0.727) into "Low" — reintroducing the dividend-king-reads-Low defect this
    section exists to fix. Only the immediate neighbourhood of 1.0 moves."""
    svc = _svc()

    def status(older, newer):
        info = svc._build_dividend_info(
            [], newer, 1.0, 0.0,
            data_points=[_DP(y) for y in ([older] * 4 + [newer] * 4)],
            annual_ratios=_rows([(2023, 1.0), (2024, 1.0), (2025, 1.0)]),
        )
        return info.status

    assert status(3.23, 2.39) == "Fair", "JNJ-like 0.740 must NOT become Low"
    assert status(3.83, 3.98) == "High", "TGT-like 1.039 is outside the band"
    assert status(2.00, 2.50) == "High", "a genuine 25% raise is still High"
    assert status(3.00, 1.80) == "Low", "a 40% cut is still Low"
    assert status(1.00, 4.00) == "Very High"


def test_the_status_change_bumps_the_cache_payload_version():
    """A stored verdict whose FORMULA changed must not be served for another 24h.

    This is the case a key-presence probe cannot see, and the reason the version exists.
    """
    assert sos._PAYLOAD_VERSION >= 3
