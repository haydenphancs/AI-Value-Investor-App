"""The iOS Moat card's market row never compounds on the device (owner decisions 2026-10-22).

"Measured only" removed the on-device projection; the owner then asked for "data from today
year to the next 5 year": the SERVER now sends a projection from public BLS / CMS / GDP paths
(`projection_*`), and the card only formats it. These scans pin both halves: no calendar or
arithmetic in the model, and an arrow pair only from a company quote's stated target or the
server's projection. The card shows no source line (owner: "no need to add source lines at
all"); the scope line it does show is a definition.

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
             "growth_source_label", "concentration_basis",
             "market_size_label", "scope_note", "projection_basis", "projection_year",
             "projection_tam", "projection_target_year", "projection_target_tam",
             "projection_rate_pct", "projection_source_label",
             "projection_year_label", "projection_target_year_label")
_STRING_LITERAL = re.compile(r'"(?:[^"\\]|\\.)*"')


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


def test_a_pair_is_only_a_company_target_or_a_backend_projection():
    values = _decl_body(_section(), "private var marketSizeValues: some View {")
    assert values.count("→") == 1, "one shared arrow view for every pair"
    branch = values[values.index("if let pair = md.marketSizePair {"):]
    single = branch[branch.index("} else {"):]
    for token in ("→", "pair", "target", "projection", "Target", "Projection"):
        assert token not in single, f"the single-value branch draws {token!r}"

    pair = _decl_body(_market_dynamics(), "var marketSizePair: MarketSizePair? {")
    quote_arm = pair[pair.index("case .companyQuote:"):pair.index("case .measured:")]
    assert "formattedTargetTAM" in quote_arm and "targetYearLabel" in quote_arm
    measured_arm = pair[pair.index("case .measured:"):pair.index("case .unknown:")]
    # A damaged global row (2026-10-24) draws the measured value, never half a world row.
    assert re.fullmatch(r"case \.measured:\s*return isBrokenGlobalRow \? nil : projectionPair\s*",
                        measured_arm), measured_arm
    assert re.search(r"case \.unknown:\s*return nil", pair)

    projection = _decl_body(_market_dynamics(), "private var projectionPair: MarketSizePair? {")
    assert 'tamBasis == "measured"' in projection
    assert re.search(r"start\s*<\s*target", projection)
    assert re.search(r"measured\s*<\s*start", projection)       # the US path's bound (global rows: none)
    assert "isFinite" in projection and "> 0" in projection
    code = _STRING_LITERAL.sub('""', projection)
    for banned in ("*", "/", "pow(", "cagr5Yr", "currentTAM", "futureTAM", "+ ", " - "):
        assert banned not in code, f"projectionPair must only format the server's values ({banned!r})"
    # A company quote's target is still gated on its basis.
    target = _decl_body(_market_dynamics(), "private var targetYear: Int? {")
    assert "basis == .companyQuote" in target


def test_the_card_draws_no_source_line_and_the_scope_line_wraps():
    section = _section()
    for banned in ("tamSourceLabel", "sourceCaption", "growthSourceLabel", "projectionSourceLabel",
                   '"Source', "Source:"):
        assert banned not in section, f"the card must show no source line ({banned!r}; owner 2026-10-22)"
    assert "var sourceCaption" not in _market_dynamics()
    caption = _decl_body(section, "private var scopeCaption: some View {")
    assert "data.marketDynamics.scopeCaption" in caption
    assert ".fixedSize(horizontal: false, vertical: true)" in caption
    assert ".lineLimit(1)" not in caption
    assert "scopeCaption" in _decl_body(section, "private var marketDynamicsSection: some View {")
    scope = _decl_body(_market_dynamics(), "var scopeCaption: String? {")
    assert re.search(r"guard\s+tamIsAvailable\s*,\s*basis\s*!=\s*\.unknown\s*,\s*!isBrokenGlobalRow\s+else", scope)


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
    (Phase B) figure: its label names the grounded publishers, so no scope line, no pair and
    no year."""
    basis = _decl_body(_market_dynamics(), "private var basis: Basis {")
    assert re.search(r'tamScope\?\.lowercased\(\)\s*==\s*"global"\s*\{\s*return\s+\.unknown', basis)
    measured = _decl_body(_market_dynamics(), "private var measuredYear: Int? {")
    assert re.search(r"case\s+\.unknown:\s*return\s+nil", measured)


def test_the_pair_draws_exactly_the_pairs_values():
    """Review finding 2026-10-22: wrong-number edits (the 2024 measured value in the 2026 slot, a
    swapped mapping argument) stayed green. Pin what the pair branch draws and where each value
    of the pair comes from."""
    values = _decl_body(_section(), "private var marketSizeValues: some View {")
    branch = values[values.index("if let pair = md.marketSizePair {"):]
    pair_branch = branch[:branch.index("} else {")]
    for draw in ("metricValueText(pair.fromValue)", "metricValueText(pair.toValue)",
                 "yearText(pair.fromYear)", "yearText(pair.toYear)"):
        assert draw in pair_branch, draw
    assert "md." not in pair_branch.split("{", 1)[1], "the pair branch draws only the pair"

    projection = _decl_body(_market_dynamics(), "private var projectionPair: MarketSizePair? {")
    for bound in ("let start = Self.validYear(projectionYear)",
                  "let target = Self.validYear(projectionTargetYear)",
                  "let fromValue = projectionTAM", "let toValue = projectionTargetTAM",
                  "fromValue: Self.formatTAM(fromValue)", "toValue: Self.formatTAM(toValue)",
                  'fromYear: "(\\(Self.yearLabel(projectionYearLabel, start)))"',
                  'toYear: "(\\(Self.yearLabel(projectionTargetYearLabel, target)))"',
                  "isProjection: true"):
        assert bound in projection, bound
    # The server's "2026e" is drawn only for its own year (else the plain year).
    year_label = _decl_body(_market_dynamics(), "private static func yearLabel(_ label: String?, _ year: Int) -> String {")
    assert "text.hasPrefix(String(year))" in year_label and "return String(year)" in year_label
    # VoiceOver says a projected pair is projected.
    a11y = _decl_body(_market_dynamics(), "var marketSizeAccessibilityLabel: String {")
    assert 'pair.isProjection ? ", projected"' in a11y


def test_the_dto_forwards_every_rendered_projection_field():
    mapping = _decl_body(_strip_comments(_src(_RESPONSE)), "func toTickerReportData()")
    for model, dto in (("marketSizeLabel", "marketSizeLabel"), ("scopeNote", "scopeNote"),
                       ("projectionYear", "projectionYear"), ("projectionTAM", "projectionTam"),
                       ("projectionTargetYear", "projectionTargetYear"),
                       ("projectionTargetTAM", "projectionTargetTam"),
                       ("projectionYearLabel", "projectionYearLabel"),
                       ("projectionTargetYearLabel", "projectionTargetYearLabel")):
        assert re.search(rf"\b{model}:\s*moatCompetition\.marketDynamics\.{dto}\b", mapping), model


def test_the_market_size_header_stays_on_one_line():
    """A wrapped header pushed this column's value below the other two (review 2026-10-22)."""
    column = _decl_body(_section(), "private var marketSizeColumn: some View {")
    header = column[column.index("Text(data.marketDynamics.tamHeaderLabel)"):column.index("if data.marketDynamics.tamIsAvailable")]
    assert ".lineLimit(1)" in header and ".minimumScaleFactor(" in header


# ── Global market row (owner decisions 2026-10-24) ─────────────────────────────────────
#
# "Replace with global" + "Global rate": a row whose `projection_basis` is "global_estimate" is
# the worldwide market — the header, scope line and pair are the server's global row and the
# growth column shows its rate. All-or-nothing: a damaged global row never puts the world's
# header or scope line over the measured US value.

def test_the_dto_forwards_the_global_rows_basis_and_rate():
    mapping = _decl_body(_strip_comments(_src(_RESPONSE)), "func toTickerReportData()")
    for model, dto in (("projectionBasis", "projectionBasis"), ("projectionRatePct", "projectionRatePct"),
                       ("projectionSourceLabel", "projectionSourceLabel")):
        assert re.search(rf"\b{model}:\s*moatCompetition\.marketDynamics\.{dto}\b", mapping), model
    md = _market_dynamics()
    assert re.search(r"var projectionBasis:\s*String\?\s*=\s*nil", md)
    assert re.search(r"var projectionRatePct:\s*Double\?\s*=\s*nil", md)


def test_a_global_row_is_only_the_servers_global_basis_with_a_real_rate():
    md = _market_dynamics()
    basis = _decl_body(md, "private var isGlobalBasis: Bool {")
    assert 'tamBasis == "measured"' in basis and 'projectionBasis == "global_estimate"' in basis
    rate = _decl_body(md, "private var globalRate: Double? {")
    assert ("guard isGlobalBasis, let rate = projectionRatePct, rate.isFinite, abs(rate) <= 1000 "
            "else { return nil }") in rate
    shows = " ".join(_decl_body(md, "var showsGlobalRow: Bool {").split())
    # The backend's `is_complete_global_row`, field for field: pair, rate, header, credit.
    assert ("globalRate != nil && projectionPair != nil && Self.trimmed(marketSizeLabel) != nil "
            "&& Self.trimmed(projectionSourceLabel) != nil") in shows
    broken = _decl_body(md, "private var isBrokenGlobalRow: Bool {")
    assert "isGlobalBasis && !showsGlobalRow" in broken


def test_a_damaged_global_row_falls_back_to_the_measured_us_value():
    md = _market_dynamics()
    header = _decl_body(md, "var tamHeaderLabel: String {")
    assert re.search(r"basis == \.measured, !isBrokenGlobalRow, let label = Self\.trimmed\(marketSizeLabel\)", header)
    scope = _decl_body(md, "var scopeCaption: String? {")
    assert "!isBrokenGlobalRow" in scope
    pair = _decl_body(md, "var marketSizePair: MarketSizePair? {")
    assert re.search(r"case \.measured:\s*return isBrokenGlobalRow \? nil : projectionPair", pair)


def test_only_a_us_projection_is_bounded_by_the_measured_year():
    projection = _decl_body(_market_dynamics(), "private var projectionPair: MarketSizePair? {")
    guard = projection[:projection.index("else { return nil }")]
    assert "start < target" in guard and "measuredYear" not in guard
    us_bound = _decl_body(projection, "if !isGlobalBasis {")
    assert "guard let measured = measuredYear, measured < start else { return nil }" in us_bound


def test_the_growth_column_shows_the_global_rate_on_a_global_row():
    md = _market_dynamics()
    shown = _decl_body(md, "var shownGrowthRate: Double? {")
    assert "showsGlobalRow ? globalRate : cagr5Yr" in shown
    assert "guard let cagr = shownGrowthRate, cagr.isFinite" in _decl_body(md, "var formattedGrowth: String {")
    a11y = _decl_body(md, "var growthAccessibilityLabel: String {")
    assert "guard let cagr = shownGrowthRate" in a11y and '", projected, ' in a11y
    window = _decl_body(md, "var growthWindowLabel: String? {")
    assert window.split("{", 1)[1].lstrip().startswith("if showsGlobalRow { return globalWindowLabel }")
    gw = _decl_body(md, "private var globalWindowLabel: String? {")
    for token in ("guard showsGlobalRow", "Self.validYear(projectionYear)", "Self.validYear(projectionTargetYear)",
                  "Self.yearLabel(projectionTargetYearLabel, target)", "dropFirst(String(target).count)"):
        assert token in gw, token
    growth = _decl_body(_section(), "private var growthColumn: some View {")
    assert "md.shownGrowthRate == nil ? AppColors.textMuted" in growth and "cagr5Yr" not in growth


def test_the_global_chip_preview_exists():
    md = _src(_MODELS)
    assert "static let sampleGlobalChips = MarketDynamics(" in md
    sample = md[md.index("static let sampleGlobalChips"):]
    sample = sample[:sample.index("\n    )\n")]
    assert 'projectionBasis: "global_estimate"' in sample and "projectionRatePct: 12.5" in sample
    assert 'marketSizeLabel: "Global chip market"' in sample
    assert "moatPreviewData(.sampleGlobalChips)" in _section()



def test_the_ios_rate_bound_is_the_backends():
    from app.services.industry_global_market import GLOBAL_RATE_MAX_ABS

    rate = _decl_body(_market_dynamics(), "private var globalRate: Double? {")
    bound = re.search(r"abs\(rate\)\s*<=\s*(\d+(?:\.\d+)?)", rate)
    assert bound and float(bound.group(1)) == GLOBAL_RATE_MAX_ABS


def test_the_card_never_draws_the_credit():
    """`projectionSourceLabel` is read for the all-or-nothing test only (owner: no source line)."""
    assert "projectionSourceLabel" not in _section()
