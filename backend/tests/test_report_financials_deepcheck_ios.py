"""Source-scan guards for the report-side (TickerReportView) half of the Financials deep
check — there is no XCTest target, so the Swift invariants are pinned from Python.

Each guard strips comments first (the explanatory comment beside a fix names every token
it greps for) and is BRACE-BOUND to the declaration it means, so a token living in a
different type or a preview cannot satisfy it. Each was mutation-tested once by hand:
break the Swift, watch the guard fail, restore.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_FORECAST_SECTION = _IOS / "Views/Organisms/ReportFutureForecastSection.swift"
_TIMELINE_PANEL = _IOS / "Views/Organisms/ReportEarningsTimelinePanel.swift"
_REVENUE_SECTION = _IOS / "Views/Organisms/ReportRevenueEngineSection.swift"
_TIMELINE_CHART = _IOS / "Views/Molecules/EarningsTimelineChart.swift"
_REPORT_MODELS = _IOS / "Models/TickerReportModels.swift"
_REPORT_DTOS = _IOS / "Models/TickerReportResponse.swift"
_REVENUE_MODELS = _IOS / "Models/RevenueEngineModels.swift"


def _strip_comments(src: str) -> str:
    """Drop `//` line comments and `/* */` blocks, leaving string literals intact (a
    `//` inside a string is not a comment)."""
    out = []
    i, n = 0, len(src)
    in_str = False
    while i < n:
        c = src[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(src[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j == -1 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _code(path: Path) -> str:
    assert path.exists(), f"guard is stale — {path.name} moved"
    return _strip_comments(path.read_text(encoding="utf-8"))


def _decl_body(src: str, decl_regex: str) -> str:
    """The brace-balanced body of the FIRST declaration matching `decl_regex`."""
    m = re.search(decl_regex, src)
    assert m, f"declaration not found: {decl_regex}"
    start = src.index("{", m.end() - 1 if src[m.end() - 1] == "{" else m.end())
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"unbalanced braces after {decl_regex!r}")


# ── EPS Track Record: tri-state cells (#79, #90) ────────────────────────────────


def test_track_record_cell_draws_the_tri_state_outcome_not_the_beat_flag():
    strip = _decl_body(_code(_FORECAST_SECTION), r"private var beatMissStrip\s*:\s*some View\s*\{")
    cell = _decl_body(strip, r"ForEach\(forecast\.earningsTrackRecord\)\s*\{")
    assert "q.outcomeSymbol" in cell and "q.outcomeColor" in cell
    assert "q.beat" not in cell, "a two-state `q.beat` branch draws a MET quarter as a miss"


def test_beat_summary_capsule_is_neutral():
    strip = _decl_body(_code(_FORECAST_SECTION), r"private var beatMissStrip\s*:\s*some View\s*\{")
    summary = strip[strip.index("forecast.beatSummary"):strip.index("ScrollView(")]
    assert "AppColors.bullish" not in summary and "AppColors.gain" not in summary
    assert "AppColors.textSecondary" in summary


def test_track_record_outcome_reads_result_and_falls_back_to_beat():
    point = _decl_body(_code(_REPORT_MODELS), r"struct EarningsTrackRecordPoint\s*:\s*Identifiable\s*\{")
    assert re.search(r"var result:\s*String\?\s*=\s*nil", point)
    outcome = _decl_body(point, r"var outcome:\s*EarningsTrackRecordOutcome\s*\{")
    assert 'case "met": return .met' in outcome
    assert 'case "beat": return .beat' in outcome and 'case "miss": return .miss' in outcome
    # Legacy fallback (no `result`): a beat stays a beat; a non-beat is a miss unless its
    # stored surprise rounds to 0.0 — that is `.inLine` (round 2, R29/R45; pinned in
    # test_report_round2_ios.py).
    assert re.search(r"default:\s*if beat \{ return \.beat \}", outcome)
    text = _decl_body(point, r"var surpriseText:\s*String\s*\{")
    assert '"0%"' in text and '"<0.1%"' in text
    color = _decl_body(point, r"var outcomeColor:\s*Color\s*\{")
    # Text role: the cell's percentage is TEXT, so never a *Graphic token.
    assert "Graphic" not in color
    assert "AppColors.gain" in color and "AppColors.loss" in color


def test_track_record_dto_decodes_result_optionally_and_maps_it():
    src = _code(_REPORT_DTOS)
    dto = _decl_body(src, r"struct EarningsTrackRecordPointDTO\s*:\s*Codable\s*\{")
    assert re.search(r"let result:\s*String\?", dto)
    assert re.search(r"case result\b", dto)
    assert re.search(r"EarningsTrackRecordPoint\([^)]*result:\s*\$0\.result", src, re.S)


# ── Earnings Timeline: period ends and EPS basis (#45, #48, #80) ───────────────


def test_timeline_dto_decodes_period_end_and_eps_basis_optionally():
    src = _code(_REPORT_DTOS)
    dto = _decl_body(src, r"struct RevenueProjectionDTO\s*:\s*Codable\s*\{")
    assert re.search(r"let periodEnd:\s*String\?", dto)
    assert re.search(r"let epsBasis:\s*String\?", dto)
    assert 'case periodEnd = "period_end"' in dto and 'case epsBasis = "eps_basis"' in dto
    assert re.search(r"periodEnd:\s*\$0\.periodEnd", src)
    assert re.search(r"epsBasis:\s*\$0\.epsBasis", src)


def test_revenue_projection_display_fields_default_so_inits_compile():
    proj = _decl_body(_code(_REPORT_MODELS), r"struct RevenueProjection\s*:\s*Identifiable\s*\{")
    assert re.search(r"var periodEnd:\s*String\?\s*=\s*nil", proj)
    assert re.search(r"var epsBasis:\s*String\?\s*=\s*nil", proj)


def test_price_overlay_is_placed_by_period_end_with_a_calendar_fallback():
    src = _code(_TIMELINE_CHART)
    compute = _decl_body(src, r"private func computePriceColumns\(\)[^{]*\{")
    assert "periodEndEdges()" in compute
    assert "priceColumnsByPeriodEnd(edges:" in compute
    assert "priceColumnsByCalendarYear()" in compute
    edges = _decl_body(src, r"private func periodEndEdges\(\)[^{]*\{")
    assert "p.periodEnd" in edges
    assert "day <= last" in edges, "non-increasing period ends must fall back, not mis-map"
    by_end = _decl_body(src, r"private func priceColumnsByPeriodEnd\(edges:[^{]*\{")
    assert "edges[lo - 1]" in by_end and "Self.firstColumnDays" in by_end
    assert "Int(dp.date.prefix(4))" not in by_end, "the period-end path must not key on the calendar year"


def test_eps_basis_footnote_reads_the_wire_field():
    src = _code(_TIMELINE_PANEL)
    mixes = _decl_body(src, r"private var mixesEPSBases:\s*Bool\s*\{")
    assert r"\.epsBasis" in mixes and '"gaap"' in mixes and '"consensus"' in mixes
    body = _decl_body(src, r"var body:\s*some View\s*\{")
    assert "if mixesEPSBases" in body


# ── Revenue Engine: the eliminations line (#7, #50) ──────────────────────────────


def test_revenue_engine_dto_decodes_eliminations_optionally_and_maps_it():
    src = _code(_REPORT_DTOS)
    dto = _decl_body(src, r"struct RevenueEngineDTO\s*:\s*Codable\s*\{")
    assert re.search(r"let intersegmentEliminations:\s*Double\?", dto)
    assert 'case intersegmentEliminations = "intersegment_eliminations"' in dto
    assert "intersegmentEliminations: revenueEngine.intersegmentEliminations" in src


def test_revenue_engine_section_draws_the_eliminations_line():
    src = _code(_REVENUE_SECTION)
    body = _decl_body(src, r"var body:\s*some View\s*\{")
    assert "if data.hasEliminations" in body and "eliminationsRow" in body
    row = _decl_body(src, r"private var eliminationsRow:\s*some View\s*\{")
    assert "data.formattedEliminations" in row and "data.formattedEliminationsPercentage" in row


def test_revenue_engine_model_eliminations_default_and_guard():
    data = _decl_body(_code(_REVENUE_MODELS), r"struct ReportRevenueEngineData\s*\{")
    assert re.search(r"var intersegmentEliminations:\s*Double\?\s*=\s*nil", data)
    has = _decl_body(data, r"var hasEliminations:\s*Bool\s*\{")
    assert "isFinite" in has and "e > 0" in has


@pytest.mark.parametrize("path", [
    _FORECAST_SECTION, _TIMELINE_PANEL, _REVENUE_SECTION, _TIMELINE_CHART,
])
def test_every_touched_view_keeps_a_preview(path):
    assert "#Preview" in _code(path)
