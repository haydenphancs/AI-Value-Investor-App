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


# ── Future Forecast chart, numbers around a correctly-timed chart (2026-11-14) ──
# The timing was measured correct on 14 live tickers; these pin the four iOS fixes from the
# same audit. Backend halves: tests/test_future_forecast_numbers_2026_11_14.py.


def test_yoy_chips_take_sign_and_colour_from_the_rounded_value():
    """A -0.47% change drew a red "-0%" (INTC FY2025): the sign came from the raw value."""
    proj = _decl_body(_code(_REPORT_MODELS), r"struct RevenueProjection\s*:\s*Identifiable\s*\{")
    assert "Self.yoyChipText(pct)" in _decl_body(proj, r"var revenueYoYText:\s*String\?\s*\{")
    assert "Self.yoyChipText(pct)" in _decl_body(proj, r"var epsYoYText:\s*String\?\s*\{")
    assert re.search(r"var revenueYoYColor:\s*Color\s*\{\s*Self\.yoyChipColor\(revenueYoyPct\)\s*\}", proj)
    assert re.search(r"var epsYoYColor:\s*Color\s*\{\s*Self\.yoyChipColor\(epsYoyPct\)\s*\}", proj)
    text = _decl_body(proj, r"static func yoyChipText\(_ pct: Double\)\s*->\s*String\s*\{")
    assert "pct.rounded(.toNearestOrEven)" in text
    assert 'if rounded == 0 { return "0%" }' in text
    assert 'rounded > 0 ? "+" : ""' in text and "pct >= 0" not in text
    color = _decl_body(proj, r"static func yoyChipColor\(_ pct: Double\?\)\s*->\s*Color\s*\{")
    assert "pct.rounded(.toNearestOrEven)" in color
    assert "if rounded == 0 { return AppColors.textMuted }" in color
    assert "rounded > 0 ? AppColors.bullish : AppColors.bearish" in color
    assert "pct >= 0 ?" not in proj


def _swift_cutoff_note(timeline, prices):
    """Python port of ReportEarningsTimelinePanel.priceCutoffNote + shortDate. `prices` are
    (date, price) pairs."""
    from datetime import date as _d
    if not timeline or any(c["is_forecast"] for c in timeline):
        return None
    ends = []
    for c in timeline:
        end = c.get("period_end")
        if not end or len(end) < 10:
            return None
        day = end[:10]
        if ends and day <= ends[-1]:
            return None
        ends.append(day)

    def num(text):
        return _d.fromisoformat(text[:10]).toordinal()

    # Drawn from the first column's period END (its bar: `yearEndShift`, 2026-11-14).
    first_end, last_end = num(ends[0]), num(ends[-1])
    drawn = [p for d, p in prices if first_end <= num(d) <= last_end]
    latest = max((num(d) for d, _ in prices), default=None)
    if len(drawn) < 2 or not max(drawn) > min(drawn) or latest is None or latest <= last_end:
        return None
    y, m, d = (int(x) for x in ends[-1].split("-"))
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return f"Price shown to {months[m - 1]} {d}, {y}."


_IN = [("2025-03-03", 10.0), ("2025-09-01", 12.0)]      # inside FY2024..FY2025's windows


@pytest.mark.parametrize("columns, prices, note", [
    ([("2024-12-31", False), ("2025-12-31", False)], _IN + [("2026-11-13", 9.0)],
     "Price shown to Dec 31, 2025."),
    ([("2024-12-31", False), ("2025-12-31", True)], _IN + [("2026-11-13", 9.0)], None),   # forecast
    ([("2024-12-31", False), ("2025-12-31", False)], _IN, None),                          # nothing cut off
    ([("2025-12-31", False), ("2024-12-31", False)], _IN + [("2026-11-13", 9.0)], None),  # unordered
    ([("2024-12-31", False), (None, False)], _IN + [("2026-11-13", 9.0)], None),          # older report
    ([("2024-12-31", False)], [], None),
    # Review 2026-11-14: listed AFTER the last year-end — no drawn line, so no "shown to".
    ([("2024-12-31", False), ("2025-12-31", False)], [("2026-03-02", 5.0), ("2026-11-13", 9.0)], None),
    # Closes only inside the FIRST fiscal year, before its end: since the price line moved
    # onto year-ends (2026-11-14) they sit left of the first bar, trimmed — nothing drawn.
    ([("2024-12-31", False), ("2025-12-31", False)],
     [("2024-03-01", 5.0), ("2024-09-02", 8.0), ("2026-11-13", 9.0)], None),
    # One drawn close, or a flat drawn series: the chart draws no line either.
    ([("2024-12-31", False), ("2025-12-31", False)], [("2025-06-02", 5.0), ("2026-11-13", 9.0)], None),
    ([("2024-12-31", False), ("2025-12-31", False)],
     [("2025-03-03", 5.0), ("2025-06-02", 5.0), ("2026-11-13", 9.0)], None),
])
def test_price_cutoff_caption_rule(columns, prices, note):
    timeline = [{"period_end": e, "is_forecast": f} for e, f in columns]
    assert _swift_cutoff_note(timeline, prices) == note


def test_no_forecast_column_gets_a_price_cutoff_caption():
    """Owner 2026-11-14 ("Caption"): without forecasts the line stops at the last year-end."""
    src = _code(_TIMELINE_PANEL)
    body = _decl_body(src, r"var body:\s*some View\s*\{")
    assert "if showPrice, let cutoff = Self.priceCutoffNote(timeline: timeline, prices: dailyPrices)" in body
    note = _decl_body(src, r"static func priceCutoffNote\(")
    assert "!timeline.contains(where: { $0.isForecast })" in note
    assert "if let previous = ends.last, day <= previous { return nil }" in note
    # Only when a line is drawn: >= 2 closes inside the windows, not flat, and a later close.
    # Drawn closes start at the first year's END (`yearEndShift` + the first-bar trim).
    assert "if day >= firstEndDay, day <= lastEndDay, point.price.isFinite" in note
    assert "windowOpensAfter" not in note
    assert "drawn.count >= 2" in note and "high > low" in note and "latest > lastEndDay" in note
    assert '"Price shown to \\(shown)."' in note
    short = _decl_body(src, r"static func shortDate\(_ ymd: String\)\s*->\s*String\?\s*\{")
    assert "DateFormatter" not in short and "(1...12).contains(month)" in short


def test_only_column_zero_lead_in_is_trimmed():
    """A listing that starts in a later column keeps its first trading months."""
    compute = _decl_body(_code(_TIMELINE_CHART), r"private func computePriceColumns\(\)[^{]*\{")
    assert "cols.filter { $0.colX >= 0.5 }" in compute
    assert "minColX" not in compute


def test_boundary_anchor_id_is_applied_before_position():
    """`.position` wraps the view in a chart-sized container: an id after it centred the whole
    chart, which is the boundary only for 5 actual + 5 forecast columns."""
    src = _code(_TIMELINE_CHART)
    at = src.index(".id(boundaryAnchorID)")
    chain = src[src.rindex("Color.clear", 0, at):src.index("\n", src.index(".position(", at))]
    assert ".frame(width: 1, height: 1)" in chain
    assert chain.index(".id(boundaryAnchorID)") < chain.index(".position(x: anchorX, y: topPad)")
    assert ".position(" not in chain[:chain.index(".id(boundaryAnchorID)")]


def test_no_dashed_actual_forecast_divider_is_drawn():
    """Owner 2026-11-14 ("we remove the dash line at the middle"): with the price line on
    fiscal year-ends, a divider in the gap sat beside the last close and read as a date. The
    invisible scroll anchor at that spot stays (the chart still opens centred on it)."""
    src = _code(_TIMELINE_CHART)
    assert "let bx = sidePad + CGFloat(fi) * colW" not in src
    # The only dashed stroke left is the tapped column's highlight, drawn at its centre.
    assert src.count("dash: [3, 3]") == 1
    highlight = src[src.index("if let sel = selectedIndex, points.indices.contains(sel)"):]
    assert highlight.index("dash: [3, 3]") < highlight.index("let months")
    assert ".id(boundaryAnchorID)" in src and "firstForecastIndex.map" in src
