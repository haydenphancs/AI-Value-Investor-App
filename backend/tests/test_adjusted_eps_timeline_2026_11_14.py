"""The report's Earnings Timeline plots ADJUSTED EPS on its reported years (2026-11-14).

Owner, after the platform research: "Switch to adjusted". Reported years used to plot GAAP
`epsDiluted` beside an adjusted consensus, so ORCL drew GAAP $5.83 → consensus $8.15 (a "+40%"
step that is mostly the gap between two measures) and the FY2027E EPS change was left blank.
Yahoo, stockanalysis, Finviz, Zacks, TIKR, Seeking Alpha and Morningstar all measure that year
against the last reported year on the consensus's own basis: ORCL 8.15 / 7.63 = +6.8%.

A reported year's adjusted EPS is the sum of its four earnings-feed quarters (ORCL FY2026:
1.47 + 2.26 + 1.79 + 2.11 = 7.63). The timeline keeps GAAP for the whole company when the last
reported year has no four comparable quarters, has no consensus to check against, lands far
from it (Berkshire's GAAP-like feed: $29.21 vs a $21.48 operating consensus), or the filer does
not report in USD. An older year without four comparable quarters is N/A — never GAAP.
Hermetic: every input is built here.
"""
from __future__ import annotations

import ast
import math
import re
from pathlib import Path

import pytest

from app.schemas.earnings import EarningsQuarterSchema, EarningsResponse
from app.schemas.ticker_report import RevenueProjectionResponse
from app.services.agents import narrative_prompts as np_
from app.services.agents import ticker_report_data_collector as C
from app.services.agents.persona_config import get_persona_config
from app.services import chat_context_resolver as chat
from app.services import earnings_service as es
from app.services.pdf_report_service import build_context, render_html

PERSONA = get_persona_config("warren_buffett")


# ── Fixtures: an ORCL-shaped May filer (real figures, 2026-11-14) ─────────────

def _income(rows, currency="USD", symbol="ORCL", month_day="05-31"):
    """[(fiscal year, revenue, GAAP EPS)] → FMP annual income rows (newest first)."""
    return [
        {"date": f"{fy}-{month_day}", "fiscalYear": str(fy), "period": "FY",
         "revenue": rev, "epsDiluted": eps, "reportedCurrency": currency, "symbol": symbol}
        for fy, rev, eps in reversed(rows)
    ]


def _estimates(rows, symbol="ORCL", month_day="05-31"):
    """[(fiscal year, revenueAvg, epsAvg)] → FMP annual analyst-estimate rows."""
    return [
        {"date": f"{fy}-{month_day}", "revenueAvg": rev, "epsAvg": eps,
         "numAnalystsRevenue": 30, "numAnalystsEps": 29, "symbol": symbol}
        for fy, rev, eps in rows
    ]


def _q(end, actual, *, has_estimate=True, surprise=2.0, estimate=None):
    return EarningsQuarterSchema(
        quarter=f"Q {end}", actual_value=actual,
        estimate_value=estimate if estimate is not None else (actual or 0.0),
        surprise_percent=surprise, fiscal_date=end, has_estimate=has_estimate,
    )


def _earnings(quarters, symbol="ORCL"):
    return EarningsResponse(
        symbol=symbol, eps_quarters=quarters, revenue_quarters=[], price_history=[],
    )


_ORCL_ENDS = {  # fiscal year → its four quarter ends (Aug, Nov, Feb, May)
    fy: [f"{fy - 1}-08-31", f"{fy - 1}-11-30",
         f"{fy}-02-{29 if fy % 4 == 0 else 28}", f"{fy}-05-31"]
    for fy in range(2021, 2028)
}
_ORCL_ADJUSTED_Q = {
    2021: [0.95, 1.06, 1.16, 1.54],
    2022: [1.03, 1.21, 1.13, 1.54],
    2023: [1.03, 1.21, 1.22, 1.67],
    2024: [1.19, 1.34, 1.41, 1.63],
    2025: [1.39, 1.47, 1.47, 1.70],
    2026: [1.47, 2.26, 1.79, 2.11],
}

ORCL_INCOME = _income([
    (2021, 40.48e9, 4.55), (2022, 42.44e9, 2.41), (2023, 49.95e9, 3.07),
    (2024, 52.96e9, 3.71), (2025, 57.40e9, 4.34), (2026, 67.36e9, 5.83),
])
ORCL_FORECASTS = [
    (2027, 90.46e9, 8.15), (2028, 125.0e9, 11.10), (2029, 165.0e9, 15.77),
    (2030, 210.0e9, 20.29), (2031, 260.0e9, 27.05),
]
# FMP keeps a consensus row for reported years too. FY2024's is the broken kind measured on
# real data (3.99 beside a true adjusted 5.56): it must not drop that year.
ORCL_ESTIMATES = _estimates([(2024, 53.0e9, 3.99), (2026, 67.3e9, 7.49)] + ORCL_FORECASTS)


def _orcl_quarters(overrides=None, drop=(), extra=()):
    quarters = []
    for fy, values in _ORCL_ADJUSTED_Q.items():
        for i, (end, value) in enumerate(zip(_ORCL_ENDS[fy], values)):
            if (fy, i) in drop:
                continue
            quarters.append((overrides or {}).get((fy, i)) or _q(end, value))
    # FY2027 Q1 is already reported: it belongs to the FORECAST year and is never summed in.
    quarters.append(_q("2026-08-31", 1.71))
    quarters.append(_q("2026-11-30", None, surprise=None))  # upcoming, no actual
    quarters.extend(extra)
    return _earnings(quarters)


ORCL_EARNINGS = _orcl_quarters()


def _adjusted(income=ORCL_INCOME, estimates=ORCL_ESTIMATES, earnings=ORCL_EARNINGS,
              currency="USD"):
    return C._adjusted_annual_eps(income, estimates, earnings, currency=currency)


def _timeline(income=ORCL_INCOME, estimates=ORCL_ESTIMATES, earnings=ORCL_EARNINGS,
              currency="USD"):
    return C._build_revenue_forecast_partial(
        estimates, 20.0, 15.0, income, currency=currency, earnings=earnings,
    )


def _row(partial, period):
    return next(p for p in partial["annual_timeline"] if p["period"] == str(period))


# ── 1. ORCL: the reported years are the summed feed quarters ─────────────────

def test_each_reported_year_is_its_four_feed_quarters_summed():
    adjusted = _adjusted()
    for fy, values in _ORCL_ADJUSTED_Q.items():
        assert adjusted[fy] == pytest.approx(sum(values), abs=1e-9), fy
    assert adjusted[2026] == pytest.approx(7.63)
    assert 2027 not in adjusted  # a reported quarter of a FORECAST year is never summed


def test_orcl_timeline_plots_adjusted_with_the_gaap_figure_beside_it():
    partial = _timeline()
    fy26 = _row(partial, 2026)
    assert fy26["eps_basis"] == "adjusted"
    assert fy26["eps_label"] == "$7.63" and fy26["eps"] == 7.63
    assert fy26["eps_gaap_label"] == "$5.83"
    assert fy26["eps_yoy_pct"] == pytest.approx((7.63 - 6.03) / 6.03 * 100, abs=1e-3)
    reported = [p for p in partial["annual_timeline"] if not p["is_forecast"]]
    assert {p["eps_basis"] for p in reported} == {"adjusted"}
    # Old builds show their "reported years are GAAP" note only beside a "gaap" row.
    assert all(p["eps_basis"] != "gaap" for p in partial["annual_timeline"])
    for p in partial["annual_timeline"]:
        if p["is_forecast"]:
            assert p["eps_basis"] == "consensus" and p["eps_gaap_label"] is None


def test_orcl_fy2027_eps_change_is_like_for_like():
    fy27 = _row(_timeline(), 2027)
    assert fy27["eps_yoy_pct"] == pytest.approx((8.15 - 7.63) / 7.63 * 100, abs=1e-3)
    assert round(fy27["eps_yoy_pct"], 1) == 6.8
    # Never the GAAP → consensus mix the owner questioned (+40%).
    assert fy27["eps_yoy_pct"] < 10


def test_the_eps_rate_spans_last_reported_to_last_forecast_when_adjusted():
    adjusted = _adjusted()
    growth = C._timeline_growth(ORCL_INCOME, ORCL_ESTIMATES, adjusted_eps=adjusted)
    assert growth["eps_span"] == "FY2026–FY2031E"
    expected = round(((27.05 / 7.63) ** (1 / 5) - 1) * 100, 1)
    assert growth["eps_cagr"] == pytest.approx(expected)
    partial = _timeline()
    assert partial["eps_growth_span"] == "FY2026–FY2031E"
    assert partial["eps_growth"] == pytest.approx(expected)


def test_a_broken_older_consensus_row_does_not_drop_a_good_year():
    # FY2024's consensus row (3.99) is 40% off the true adjusted 5.57: only the LAST reported
    # year is checked against its consensus.
    assert _adjusted()[2024] == pytest.approx(5.57)
    assert _row(_timeline(), 2024)["eps_label"] == "$5.57"


# ── 2. When the timeline keeps GAAP (whole company, unchanged output) ────────

def _gaap_reference(income=ORCL_INCOME, estimates=ORCL_ESTIMATES, currency="USD"):
    return C._build_revenue_forecast_partial(estimates, 20.0, 15.0, income, currency=currency)


def _assert_gaap(partial, income=ORCL_INCOME, estimates=ORCL_ESTIMATES, currency="USD"):
    assert partial == _gaap_reference(income, estimates, currency)
    reported = [p for p in partial["annual_timeline"] if not p["is_forecast"]]
    assert reported and {p["eps_basis"] for p in reported} == {"gaap"}
    assert all(p["eps_gaap_label"] is None for p in partial["annual_timeline"])
    first_forecast = next(p for p in partial["annual_timeline"] if p["is_forecast"])
    assert first_forecast["eps_yoy_pct"] is None


def test_without_earnings_the_timeline_is_unchanged_gaap():
    _assert_gaap(_timeline(earnings=None))
    assert _row(_timeline(earnings=None), 2026)["eps_label"] == "$5.83"


def test_the_last_year_far_from_its_consensus_keeps_gaap():
    # Berkshire-shaped: the feed's quarters are not on the consensus's basis.
    estimates = _estimates([(2026, 67.3e9, 5.0)] + ORCL_FORECASTS)
    assert _adjusted(estimates=estimates) is None
    _assert_gaap(_timeline(estimates=estimates), estimates=estimates)


def test_no_consensus_row_for_the_last_year_keeps_gaap():
    estimates = _estimates(ORCL_FORECASTS)
    assert _adjusted(estimates=estimates) is None
    _assert_gaap(_timeline(estimates=estimates), estimates=estimates)


def test_a_blank_zero_consensus_for_the_last_year_keeps_gaap():
    estimates = _estimates([(2026, 67.3e9, 0)] + ORCL_FORECASTS)
    assert _adjusted(estimates=estimates) is None


def test_a_consensus_row_more_than_ten_days_off_is_not_the_years():
    estimates = _estimates(ORCL_FORECASTS) + [
        {"date": "2026-06-15", "epsAvg": 7.49, "revenueAvg": 67.3e9, "symbol": "ORCL"},
    ]
    assert _adjusted(estimates=estimates) is None


@pytest.mark.parametrize("currency", ["EUR", "CNY", None, ""])
def test_a_non_usd_or_unknown_filer_keeps_gaap(currency):
    assert _adjusted(currency=currency) is None


def test_three_quarters_in_the_last_year_keep_gaap():
    assert _adjusted(earnings=_orcl_quarters(drop={(2026, 1)})) is None


@pytest.mark.parametrize("override", [
    _q("2025-11-30", 2.26, has_estimate=False),   # GAAP fallback / no comparable consensus
    _q("2025-11-30", 2.26, surprise=None),        # dropped-digit quarter, screened
    _q("2025-11-30", float("nan")),
])
def test_an_incomparable_quarter_in_the_last_year_keeps_gaap(override):
    assert _adjusted(earnings=_orcl_quarters(overrides={(2026, 1): override})) is None


def test_no_forecast_year_keeps_gaap():
    estimates = _estimates([(2026, 67.3e9, 7.49)])
    assert _adjusted(estimates=estimates) is None


def test_a_half_year_filer_keeps_gaap():
    income = _income([(2024, 5e9, 1.0), (2025, 6e9, 1.2)], month_day="06-30", symbol="BHP")
    estimates = _estimates([(2025, 6e9, 1.25), (2026, 7e9, 1.4)], month_day="06-30",
                           symbol="BHP")
    halves = _earnings([
        _q("2023-12-31", 0.5), _q("2024-06-30", 0.5),
        _q("2024-12-31", 0.6), _q("2025-06-30", 0.65),
    ], symbol="BHP")
    assert C._adjusted_annual_eps(income, estimates, halves, currency="USD") is None


# ── 3. Older years: N/A, never GAAP, never a change across the gap ───────────

def test_an_older_year_missing_a_quarter_is_na_not_gaap():
    partial = _timeline(earnings=_orcl_quarters(drop={(2024, 2)}))
    fy24, fy25 = _row(partial, 2024), _row(partial, 2025)
    assert fy24["eps_label"] == "N/A" and fy24["eps"] == 0.0
    # N/A: no GAAP figure beside it either (nothing to compare) — and never GAAP in its place.
    assert fy24["eps_basis"] == "adjusted" and fy24["eps_gaap_label"] is None
    assert fy24["eps_yoy_pct"] is None and fy25["eps_yoy_pct"] is None
    assert _row(partial, 2027)["eps_yoy_pct"] == pytest.approx(6.815, abs=1e-3)


@pytest.mark.parametrize("override", [
    _q("2023-11-30", 1.34, has_estimate=False),
    _q("2023-11-30", 1.34, surprise=None),
    _q("2023-11-30", 1.34, has_estimate=None),     # an older build that never set the flag
])
def test_an_incomparable_older_quarter_makes_that_year_na(override):
    adjusted = _adjusted(earnings=_orcl_quarters(overrides={(2024, 1): override}))
    assert adjusted is not None and 2024 not in adjusted and 2025 in adjusted


@pytest.mark.parametrize("bad", [True, float("inf"), float("nan"), None, "n/a"])
def test_a_quarter_whose_actual_is_not_a_plain_number_is_not_comparable(bad):
    # The schema coerces a bool to 1.0, so this guards duck-typed inputs (a cached or
    # hand-built quarter): a bool, an infinity or a non-number never enters a sum.
    from types import SimpleNamespace

    quarter = SimpleNamespace(fiscal_date="2025-03-31", actual_value=bad,
                              surprise_percent=2.0, has_estimate=True)
    assert C._comparable_quarter(quarter)[1] is None
    good = SimpleNamespace(fiscal_date="2025-03-31", actual_value=1.34,
                           surprise_percent=2.0, has_estimate=True)
    assert C._comparable_quarter(good)[1] == pytest.approx(1.34)


def test_a_duplicated_quarter_end_makes_that_year_na():
    dup = _q("2024-02-29", 1.41)
    adjusted = _adjusted(earnings=_orcl_quarters(extra=[dup]))
    assert 2024 not in adjusted and 2026 in adjusted


def test_a_fifth_quarter_in_a_window_makes_that_year_na():
    stray = _q("2024-04-15", 0.5)
    adjusted = _adjusted(earnings=_orcl_quarters(extra=[stray]))
    assert 2024 not in adjusted


def test_five_evenly_spaced_quarters_in_one_window_are_na():
    # Five quarters 76 days apart (each gap inside the band, the last on the year end): only
    # the count stops a five-quarter sum.
    five = [_q(end, 1.2) for end in
            ("2023-07-31", "2023-10-16", "2023-12-31", "2024-03-16", "2024-05-31")]
    adjusted = _adjusted(earnings=_orcl_quarters(drop={(2024, i) for i in range(4)}, extra=five))
    assert 2024 not in adjusted and 2026 in adjusted


def test_a_year_whose_last_quarter_ends_far_from_the_year_end_is_na():
    # Four evenly spaced quarters (gaps 92/92/90 days, inside the band) whose last one ends
    # three weeks before the annual row: not that year's four quarters.
    shifted = {
        (2024, i): _q(end, value)
        for i, (end, value) in enumerate(zip(
            ("2023-08-10", "2023-11-10", "2024-02-10", "2024-05-10"), _ORCL_ADJUSTED_Q[2024],
        ))
    }
    adjusted = _adjusted(earnings=_orcl_quarters(overrides=shifted))
    assert 2024 not in adjusted and 2023 in adjusted and 2025 in adjusted


def test_quarters_spaced_like_half_years_inside_one_window_are_na():
    # Four quarters in the window whose first gap is 130 days: not quarterly reporting.
    odd = {
        (2024, 0): _q("2023-06-30", 1.19), (2024, 1): _q("2023-11-07", 1.34),
        (2024, 2): _q("2024-02-29", 1.41), (2024, 3): _q("2024-05-31", 1.63),
    }
    adjusted = _adjusted(earnings=_orcl_quarters(overrides=odd))
    assert 2024 not in adjusted


# ── 4. Calendars and signs ───────────────────────────────────────────────────

def test_a_52_53_week_filer_counts_quarters_a_few_days_off_the_year_end():
    income = [
        {"date": "2024-12-28", "fiscalYear": "2024", "period": "FY", "revenue": 10e9,
         "epsDiluted": 2.0, "reportedCurrency": "USD", "symbol": "XYZ"},
        {"date": "2025-12-27", "fiscalYear": "2025", "period": "FY", "revenue": 11e9,
         "epsDiluted": 2.2, "reportedCurrency": "USD", "symbol": "XYZ"},
    ]
    estimates = [
        {"date": "2025-12-31", "epsAvg": 2.55, "revenueAvg": 11e9, "symbol": "XYZ"},
        {"date": "2026-12-31", "epsAvg": 2.9, "revenueAvg": 12e9, "symbol": "XYZ"},
    ]
    quarters = _earnings([
        _q("2025-03-29", 0.6), _q("2025-06-28", 0.6), _q("2025-09-27", 0.65),
        _q("2026-01-02", 0.65),  # 6 days after the annual period end: still FY2025's
    ], symbol="XYZ")
    adjusted = C._adjusted_annual_eps(income, estimates, quarters, currency="USD")
    assert adjusted == {2025: pytest.approx(2.5)}


def _flat_case(quarter_value, consensus):
    income = _income([(2025, 1e9, quarter_value * 4)], symbol="FLT", month_day="12-31")
    estimates = _estimates([(2025, 1e9, consensus), (2026, 1.2e9, 1.0)], symbol="FLT",
                           month_day="12-31")
    quarters = _earnings([
        _q(end, quarter_value)
        for end in ("2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31")
    ], symbol="FLT")
    return C._adjusted_annual_eps(income, estimates, quarters, currency="USD")


def test_the_consensus_tolerance_is_inclusive_at_25_percent():
    assert _flat_case(1.0, 3.2) == {2025: pytest.approx(4.0)}   # 0.8 off = 25% of 3.2
    assert _flat_case(1.0, 3.19) is None                         # 0.81 > 0.7975


def test_a_small_loss_uses_the_ten_cent_floor():
    assert _flat_case(-0.08, -0.25) == {2025: pytest.approx(-0.32)}  # 0.07 ≤ 0.10
    assert _flat_case(-0.08, -0.20) is None                          # 0.12 > 0.10


# ── 5. The legacy 4-bar projections read the adjusted base too ───────────────

def test_the_legacy_first_bar_reads_against_the_adjusted_last_year():
    # Fewer than 4 forward years → no anchor → the first bar reads against the last
    # REPORTED year. On GAAP that EPS change stays blank; on adjusted it is measured.
    estimates = _estimates([(2026, 67.3e9, 7.49)] + ORCL_FORECASTS[:3])
    adjusted = _timeline(estimates=estimates)
    gaap = _timeline(estimates=estimates, earnings=None)
    first_adj, first_gaap = adjusted["projections"][0], gaap["projections"][0]
    assert first_adj["period"] == first_gaap["period"] == "2027"
    assert first_adj["eps_yoy_pct"] == pytest.approx(6.815, abs=1e-3)
    assert first_gaap["eps_yoy_pct"] is None


# ── 6. Wire, prompt, fetch window ────────────────────────────────────────────

def test_every_timeline_row_validates_against_the_response_schema():
    for row in _timeline()["annual_timeline"] + _timeline(earnings=None)["annual_timeline"]:
        model = RevenueProjectionResponse.model_validate(row)
        assert model.eps_gaap_label == row["eps_gaap_label"]
    assert RevenueProjectionResponse.model_fields["eps_gaap_label"].default is None


def _prompt(partial):
    shell = {"revenue_forecast": {**partial, "management_guidance": "unknown"}}
    return np_._revenue_forecast_insight_prompt(PERSONA, "EVIDENCE", shell)


def test_the_insight_prompt_names_the_adjusted_basis():
    adjusted = _prompt(_timeline())
    assert "EPS BASIS: the chart's EPS, reported years included, is ADJUSTED" in adjusted
    assert "Never set it beside a GAAP EPS figure from EVIDENCE" in adjusted
    assert "EPS BASIS" not in _prompt(_timeline(earnings=None))


def test_the_prompt_lists_the_measured_first_forecast_eps_change():
    assert "2027: rev $90.5B (+34% YoY), EPS $8.15 (+7% YoY)" in _prompt(_timeline())


def test_the_collector_passes_the_collections_earnings():
    src = Path(C.__file__).read_text(encoding="utf-8")
    call = src[src.index("out.revenue_forecast_partial = _build_revenue_forecast_partial("):]
    call = call[:call.index("\n        )\n")]
    assert 'earnings=getattr(out, "earnings", None)' in call


def test_the_earnings_service_fetches_28_quarterly_income_rows():
    from app.services import earnings_service as E

    tree = ast.parse(Path(E.__file__).read_text(encoding="utf-8"))
    build = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_build_earnings"
    )
    limits = [
        kw.value.value
        for call in ast.walk(build)
        if isinstance(call, ast.Call) and getattr(call.func, "attr", "") == "get_income_statement"
        for kw in call.keywords if kw.arg == "limit"
    ]
    # 5 drawn years + the off-screen anchor + up to 3 quarters of a year in progress = 27.
    assert limits == [28]


# ── 7. iOS: the label, the note and the popup line (source scans) ────────────

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"


def _strip_comments(src: str) -> str:
    out, i, n, in_str = [], 0, len(src), False
    while i < n:
        c = src[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(src[i + 1])
                i += 2
                continue
            in_str = c != '"'
            i += 1
            continue
        if c == '"':
            in_str = True
        elif src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j == -1 else j
            continue
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _code(rel: str) -> str:
    path = _IOS / rel
    assert path.exists(), f"guard is stale — {rel} moved"
    return _strip_comments(path.read_text(encoding="utf-8"))


def _body(src: str, decl: str) -> str:
    m = re.search(decl, src)
    assert m, f"declaration not found: {decl}"
    start = src.index("{", m.end() - 1)
    depth = 0
    for i in range(start, len(src)):
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        if depth == 0:
            return src[start:i + 1]
    raise AssertionError(f"unbalanced: {decl}")


def test_ios_decodes_the_gaap_label_as_optional():
    dto = _body(_code("Models/TickerReportResponse.swift"), r"struct RevenueProjectionDTO\s*:\s*Codable\s*\{")
    assert re.search(r"let epsGaapLabel:\s*String\?", dto)
    assert 'case epsGaapLabel = "eps_gaap_label"' in dto
    assert "epsGaapLabel: $0.epsGaapLabel" in _code("Models/TickerReportResponse.swift")
    model = _body(_code("Models/TickerReportModels.swift"), r"struct RevenueProjection\s*:\s*Identifiable\s*\{")
    assert re.search(r"var epsGaapLabel:\s*String\?\s*=\s*nil", model)


def test_ios_one_rule_decides_adjusted_for_legend_note_and_popup():
    model = _code("Models/TickerReportModels.swift")
    label = _body(model, r"var adjustedYearGAAPLabel:\s*String\?\s*\{")
    for clause in ("!isForecast", 'epsBasis == "adjusted"', "!label.isEmpty", 'label != "N/A"'):
        assert clause in label
    shows = _body(model, r"static func showsAdjustedEPS\(_ timeline: \[RevenueProjection\]\)\s*->\s*Bool\s*\{")
    assert "adjustedYearGAAPLabel != nil" in shows


def test_ios_panel_marks_adjusted_eps_with_the_legend_only():
    # Owner, 2026-11-14: "we don't need to show GAAP anymore", then "Remove the note": the
    # "Adj. EPS" legend is the only marker — no note line and no GAAP figure.
    src = _code("Views/Organisms/ReportEarningsTimelinePanel.swift")
    assert "adjustedEPSNote" not in src
    assert "EPS is adjusted" not in src and "GAAP EPS for" not in src and "epsGaapLabel" not in src
    legend = _body(src, r"private var legend: some View\s*\{")
    assert 'showsAdjustedEPS ? "Adj. EPS" : "EPS"' in legend
    shows = _body(src, r"private var showsAdjustedEPS:\s*Bool\s*\{")
    assert "RevenueProjection.showsAdjustedEPS(timeline)" in shows


def test_ios_popup_shows_no_gaap_and_no_na_on_a_reported_year():
    # Owner, 2026-11-14: a past year shows its change and value only — no "n/a" analyst line,
    # no "GAAP $5.83". A forecast year keeps its analyst count ("n/a" when FMP had none).
    src = _code("Views/Molecules/EarningsTimelineChart.swift")
    assert "GAAP" not in src and "epsGaapLabel" not in src and "adjustedYearGAAPLabel" not in src
    series = _body(src, r"private var epsSeriesLabel:\s*String\s*\{")
    assert 'RevenueProjection.showsAdjustedEPS(timeline) ? "Adj. EPS" : "EPS"' in series
    popup = _body(src, r"private func inspectPopup\(")
    assert "label: epsSeriesLabel" in popup
    assert popup.count("isForecast: pt.isForecast") == 2   # both columns
    column = _body(src, r"private func popupColumn\(")
    gate = _body(column, r"if isForecast\s*\{")
    assert "Analyst" in gate and '"n/a"' in gate
    assert column.count('"n/a"') == 1 and column.count("Analyst") == 1   # only inside the gate
    # The edge clamp grows with Dynamic Type, capped like the caption font (1.4x).
    assert re.search(r"@ScaledMetric\(relativeTo: \.caption2\) private var popupHalfWidthScaled", src)
    assert "min(popupHalfWidthScaled, 140)" in src
    init = _body(src, r"\n    init\(\s*timeline: \[RevenueProjection\],")
    assert "self._selectedIndex = selectedIndex" in init


# ── 8. Real-data shape check: every adjusted value is a finite number ────────

def test_adjusted_values_are_finite_and_rounded():
    for fy, value in _adjusted().items():
        assert math.isfinite(value) and round(value, 4) == value, fy


# ── 9. Review fixes (2026-11-14 review workflow) ─────────────────────────────

def test_a_12_12_12_16_week_calendar_is_counted():
    # Costco / PepsiCo: a 112-day fourth quarter (gaps 84, 84, 112) is still quarterly.
    income = [
        {"date": "2024-09-01", "fiscalYear": "2024", "period": "FY", "revenue": 254e9,
         "epsDiluted": 16.56, "reportedCurrency": "USD", "symbol": "COST"},
        {"date": "2025-08-31", "fiscalYear": "2025", "period": "FY", "revenue": 275e9,
         "epsDiluted": 18.21, "reportedCurrency": "USD", "symbol": "COST"},
    ]
    estimates = [
        {"date": "2025-08-31", "epsAvg": 18.0, "revenueAvg": 275e9, "symbol": "COST"},
        {"date": "2026-08-31", "epsAvg": 19.9, "revenueAvg": 293e9, "symbol": "COST"},
    ]
    quarters = _earnings([
        _q("2024-11-24", 4.04), _q("2025-02-16", 4.02), _q("2025-05-11", 4.28),
        _q("2025-08-31", 5.87),
    ], symbol="COST")
    adjusted = C._adjusted_annual_eps(income, estimates, quarters, currency="USD")
    assert adjusted == {2025: pytest.approx(18.21)}


def _gaap_only_case():
    """An Apple-shaped GAAP-only reporter: the feed's quarters sum to the GAAP figure."""
    income = _income([(2024, 391e9, 6.08), (2025, 416e9, 7.46)], symbol="AAPL",
                     month_day="09-27")
    estimates = _estimates([(2025, 410e9, 7.38), (2026, 450e9, 8.20)], symbol="AAPL",
                           month_day="09-27")
    quarters = _earnings([
        _q("2024-12-28", 2.40), _q("2025-03-29", 1.65), _q("2025-06-28", 1.57),
        _q("2025-09-27", 1.85),
    ], symbol="AAPL")
    return C._build_revenue_forecast_partial(
        estimates, 5.0, 8.0, income, currency="USD", earnings=quarters,
    )


def test_a_gaap_only_reporter_is_never_labelled_non_gaap():
    partial = _gaap_only_case()
    fy25 = _row(partial, 2025)
    assert fy25["eps_basis"] == "adjusted" and fy25["eps_label"] == "$7.47"
    assert all(p["eps_gaap_label"] is None for p in partial["annual_timeline"])
    # The first forecast year is still measured (same basis as the consensus).
    assert _row(partial, 2026)["eps_yoy_pct"] == pytest.approx((8.20 - 7.47) / 7.47 * 100, abs=1e-3)
    assert "EPS BASIS" not in _prompt(partial)
    html = render_html(build_context({"revenue_forecast": partial}, None))
    assert "non-GAAP" not in html
    _head, items, _cap = chat._forecast_figures({"revenue_forecast": partial})[0]
    assert not any(i.startswith("adjusted EPS") for i in items)


@pytest.mark.parametrize("adjusted, gaap, shown", [
    (7.63, 5.83, True),     # ORCL
    (7.47, 7.46, False),    # a cent apart
    (8.93, 8.98, False),    # 0.6%: under 2%
    (1.00, 1.04, True),     # 4 cents: over the $0.03 floor and 2%
    (0.10, 0.12, False),    # 2 cents: under the floor
])
def test_the_gaap_label_appears_only_where_the_two_figures_differ(adjusted, gaap, shown):
    row = C._TimelineRow(2025, 1e9, adjusted, False, None, None, "2025-12-31", "adjusted", gaap)
    nxt = C._TimelineRow(2026, 1.1e9, 1.0, True, 5, 5, "2026-12-31", "consensus")
    label = C._signed_eps_label(gaap)
    # Drive the row through the builder's own rule via a two-row window.
    from unittest.mock import patch
    with patch.object(C, "_timeline_window", return_value=([], [row, nxt])):
        series = C._build_annual_timeline([], [])
    assert series[0]["eps_gaap_label"] == (label if shown else None)


def test_quarters_that_cancel_out_print_zero_not_minus_zero():
    assert C._signed_eps_label(-0.0) == "$0.00"
    flat = _orcl_quarters(overrides={
        (2023, 0): _q("2022-08-31", -0.05), (2023, 1): _q("2022-11-30", -0.02),
        (2023, 2): _q("2023-02-28", 0.03), (2023, 3): _q("2023-05-31", 0.04),
    })
    adjusted = _adjusted(earnings=flat)
    assert adjusted[2023] == 0.0 and math.copysign(1.0, adjusted[2023]) == 1.0
    assert _row(_timeline(earnings=flat), 2023)["eps_label"] == "$0.00"


def test_the_chat_lead_names_an_adjusted_eps_rate():
    _head, items, _cap = chat._forecast_figures({"revenue_forecast": _timeline()})[0]
    assert any(i.startswith("adjusted EPS CAGR FY2026–FY2031E") for i in items)
    _head, gaap_items, _cap = chat._forecast_figures(
        {"revenue_forecast": _timeline(earnings=None)})[0]
    assert not any(i.startswith("adjusted EPS") for i in gaap_items)


def test_the_pdf_prints_no_adjusted_note_and_no_gaap_figure():
    # As in the app ("Remove the note"): the PDF names no basis line and no GAAP figure.
    html = render_html(build_context({"revenue_forecast": _timeline()}, None))
    assert "EPS is adjusted" not in html and "Reported GAAP EPS" not in html


def test_the_earnings_payload_version_evicts_20_quarter_rows():
    assert es._EARNINGS_PAYLOAD_VERSION == 6
    assert es._DAILY_PRICE_QUARTERS == 20


def test_a_quarter_before_the_price_window_is_omitted_quietly(caplog):
    history = []
    with caplog.at_level("DEBUG", logger=es.logger.name):
        es.EarningsService._append_price_point(
            "ORCL", "Q2 '20", "2019-11-30", {}, history, prices_from="2020-11-15",
        )
        assert history == []
        assert not [r for r in caplog.records if r.levelname == "WARNING"]
        es.EarningsService._append_price_point(
            "ORCL", "Q2 '21", "2020-11-30", {}, history, prices_from="2020-11-15",
        )
    assert history == []
    assert [r for r in caplog.records if r.levelname == "WARNING"]  # a gap INSIDE the window


def test_the_daily_series_keeps_its_20_quarter_span():
    src = Path(es.__file__).read_text(encoding="utf-8")
    assert "range_start = sorted_dates[-_DAILY_PRICE_QUARTERS:][0]" in src
