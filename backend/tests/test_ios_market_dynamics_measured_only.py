"""The iOS Moat card's market row is MEASURED, never projected (owner decision 2026-10-22,
"Measured only").

Builds up to 1.1 re-anchored the source year to the device year by compounding a backward
Census rate — RKLB's 2024 $247.0B (−0.2%/yr 2017→2024) became "$246B (2026) → $244B (2031)"
— and read that year from `Calendar.current`, so a Buddhist-calendar phone showed "(2569)".
There is no XCTest target, so these source scans pin the fix. Every scan strips comments and
is brace-bounded to the declaration it means (testing.md: a scan over a whole file passes on
prose or on another type).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.schemas.ticker_report import MarketDynamicsResponse

_REPO = Path(__file__).resolve().parents[2]
_MODELS = _REPO / "frontend/ios/ios/Models/TickerReportModels.swift"
_RESPONSE = _REPO / "frontend/ios/ios/Models/TickerReportResponse.swift"
_SECTION = _REPO / "frontend/ios/ios/Views/Organisms/ReportMoatCompetitionSection.swift"

_NEW_KEYS = ("tam_basis", "tam_year", "tam_target_year", "cagr_start_year", "cagr_end_year",
             "growth_source_label", "concentration_basis")


def _src(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"{path} not present")
    return path.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    out = []
    for line in src.splitlines():
        if line.lstrip().startswith("//"):
            continue
        out.append(re.sub(r"\s//(?![^\"]*\"[^\"]*$).*$", "", line))   # trailing // comments
    return "\n".join(out)


def _decl_body(src: str, header: str) -> str:
    start = src.index(header)
    depth, i = 0, src.index("{", start)
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError(f"unbalanced braces after {header!r}")


def _market_dynamics() -> str:
    return _decl_body(_strip_comments(_src(_MODELS)), "struct MarketDynamics {")


def _dto() -> str:
    return _decl_body(_strip_comments(_src(_RESPONSE)), "struct MarketDynamicsDTO: Codable {")


def _section() -> str:
    return _strip_comments(_src(_SECTION))


def test_the_model_reads_no_calendar_and_never_compounds():
    body = _market_dynamics()
    for banned in ("Calendar", "Date()", "pow(", "projectionMultiplier", "displayed", "todayYear"):
        assert banned not in body, f"MarketDynamics must not use {banned!r} (measured only)"


def test_every_response_field_is_a_dto_coding_key():
    dto = _dto()
    keys = _decl_body(dto, "enum CodingKeys: String, CodingKey {")
    raw_values = set(re.findall(r'=\s*"([a-z0-9_]+)"', keys))
    bare = set()
    for line in keys.splitlines():
        m = re.match(r"\s*case\s+(.+)$", line)
        if m and "=" not in m.group(1):
            bare.update(x.strip() for x in m.group(1).split(","))
    missing = [f for f in MarketDynamicsResponse.model_fields if f not in raw_values | bare]
    assert not missing, f"MarketDynamicsResponse fields the DTO never decodes: {missing}"


def test_the_dto_decodes_every_new_key_defensively():
    init = _decl_body(_dto(), "init(from decoder: Decoder) throws {")
    for key in _NEW_KEYS:
        camel = re.sub(r"_([a-z])", lambda m: m.group(1).upper(), key)
        assert re.search(rf"self\.{camel}\s*=\s*try\?\s*c\.decodeIfPresent\(", init), (
            f"{key} must decode with `try? decodeIfPresent` — a drifted type on the raw-JSONB "
            "path must be nil, never a failed report"
        )
    # The five fields old builds already decode stay as they were (non-optional, no try?).
    for camel in ("industry", "concentration", "currentYear", "futureYear", "lifecyclePhase"):
        assert re.search(rf"self\.{camel}\s*=\s*try\s+c\.decode\(String\.self", init), camel


def test_the_growth_column_is_avg_per_year_with_its_window_in_a_neutral_token():
    section = _section()
    assert "CAGR (5Yr)" not in section
    growth = _decl_body(section, "private var growthColumn: some View {")
    assert 'label: "Avg/yr"' in growth and "growthWindowLabel" in growth
    for verdict in ("bullish", "bearish", "AppColors.gain", "AppColors.loss", "cagrColor"):
        assert verdict not in growth, f"the growth rate must not be coloured as a verdict ({verdict})"
    column = _decl_body(section, "private func marketMetricColumn(")
    subtitle = column[column.index("if let subtitle = subtitle"):]
    assert ".lineLimit(1)" in subtitle and ".minimumScaleFactor(" in subtitle


def test_a_measured_row_never_draws_a_future_value():
    values = _decl_body(_section(), "private var marketSizeValues: some View {")
    branch = values[values.index("if let target = md.formattedTargetTAM, let targetYear = md.targetYearLabel {"):]
    measured = branch[branch.index("} else {"):]
    assert "→" not in measured and "formattedTargetTAM" not in measured and "target" not in measured
    assert values.count("→") == 1
    # The target value and label exist only for a company quote with a stated year.
    target = _decl_body(_market_dynamics(), "private var targetYear: Int? {")
    assert "basis == .companyQuote" in target


def test_the_source_caption_is_rendered_and_wraps():
    caption = _decl_body(_section(), "private var sourceCaption: some View {")
    assert "data.marketDynamics.sourceCaption" in caption
    assert ".fixedSize(horizontal: false, vertical: true)" in caption
    assert ".lineLimit(1)" not in caption
    assert "sourceCaption" in _decl_body(_section(), "private var marketDynamicsSection: some View {")


def test_unknown_concentration_renders_a_dash():
    models = _strip_comments(_src(_MODELS))
    enum = _decl_body(models, "enum MarketConcentration: String {")
    assert re.search(r'case\s+unknown\s*=\s*"—"', enum)
    mapper = _decl_body(_strip_comments(_src(_RESPONSE)),
                        "private static func mapConcentration(_ s: String) -> MarketConcentration {")
    assert re.search(r"default:\s*return\s+\.unknown", mapper)
    assert re.search(r'case\s+"fragmented":\s*return\s+\.fragmented', mapper)


def test_market_dynamics_dividers_use_overlay():
    divider = _decl_body(_section(), "private var columnDivider: some View {")
    assert ".overlay(AppColors.divider)" in divider and ".background(" not in divider


def test_a_legacy_global_row_shows_no_caption_and_no_year():
    """A report stored before `tam_basis` with tam_scope 'global' is a retired grounded
    (Phase B) figure: its label names the grounded publishers, so no caption and no year."""
    basis = _decl_body(_market_dynamics(), "private var basis: Basis {")
    assert re.search(r'tamScope\?\.lowercased\(\)\s*==\s*"global"\s*\{\s*return\s+\.unknown', basis)
    caption = _decl_body(_market_dynamics(), "var sourceCaption: String? {")
    assert re.search(r"case\s+\.unknown:\s*return\s+nil", caption)
