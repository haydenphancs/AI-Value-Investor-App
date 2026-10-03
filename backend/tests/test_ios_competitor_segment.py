"""Source-scan guards for the report's Competitors list on iOS (TestFlight #57, AVGO).

The tester asked "Is NVIDIA the main competitor? How did we get the competitors?" — the
rows were ordered by threat score while nothing on screen said so. The backend now
sends a per-row `segment` ("competes in") and a list-level `competitor_order` marker
("direct" | "threat", ABSENT on every report stored before 2026-10-01).

Two failure modes these guards exist for:

* A DECODE CRASH. `segment` / `competitor_order` are absent on every old report, so a
  non-optional property, or a custom `init(from:)` that `decode`s instead of
  `decodeIfPresent`s, fails the WHOLE report decode in production.
* A FALSE CLAIM. Old reports are threat-ordered. Anything but the exact wire value
  "direct" must read "Highest threat first", never "Most direct first" — the caption,
  and the info sheet's order and customer-exclusion paragraphs, follow the marker.

There is no XCTest target, so these pin the Swift source from Python (testing.md):
every scan is comment-stripped (the comments beside the fix name every token grepped
for) and BRACE-BOUND to the declaration it means. Mutation-tested once by hand.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_REPORT_DTOS = _IOS / "Models/TickerReportResponse.swift"
_REPORT_MODELS = _IOS / "Models/TickerReportModels.swift"
_PEER_ROW = _IOS / "Views/Molecules/ReportPeerComparisonRow.swift"
_MOAT_SECTION = _IOS / "Views/Organisms/ReportMoatCompetitionSection.swift"
_INFO_SHEET = _IOS / "Views/Molecules/CompetitorsInfoSheet.swift"

_SEGMENT_MAX_CHARS = 48   # competitor_intel_service.SEGMENT_MAX_CHARS (the contract)
# The segment line's limit: lifted at accessibility sizes, 2 lines otherwise.
_SEGMENT_LINE_LIMIT = ".lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 2)"


# ── helpers ──────────────────────────────────────────────────────────────────────


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
    assert path.exists(), f"guard is stale — {path.name} moved; update this guard, do not delete it"
    return _strip_comments(path.read_text(encoding="utf-8"))


def _match_close(src: str, open_at: int, open_ch: str, close_ch: str) -> int:
    """Index of the bracket closing the one at ``open_at``; string literals are skipped
    so a "(0–10)" inside a caption cannot unbalance the count."""
    depth = 0
    in_str = False
    i = open_at
    while i < len(src):
        c = src[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise AssertionError(f"unbalanced {open_ch}{close_ch} after offset {open_at}")


def _decl_body(src: str, decl_regex: str) -> str:
    """The brace-balanced body of the FIRST declaration matching ``decl_regex``."""
    m = re.search(decl_regex, src)
    assert m, f"declaration not found: {decl_regex}"
    start = src.index("{", m.end() - 1 if src[m.end() - 1] == "{" else m.end())
    return src[start:_match_close(src, start, "{", "}") + 1]


def _call_args(src: str, call_regex: str) -> str:
    """The paren-balanced argument list of the FIRST call matching ``call_regex``
    (which must end at the opening paren)."""
    m = re.search(call_regex, src)
    assert m, f"call not found: {call_regex}"
    start = m.end() - 1
    assert src[start] == "(", f"{call_regex!r} must end at the call's '('"
    return src[start:_match_close(src, start, "(", ")") + 1]


def _stored_properties(body: str) -> list[str]:
    """Names of the stored `let`/`var` properties declared at the TOP level of a type
    body, in order. Computed properties (`var x: T {`) are excluded."""
    inner = body[1:-1]
    top, depth = [], 0
    for ch in inner:
        # Keep only depth-0 text (nested decls don't count) plus the braces that open
        # and close a depth-1 block, so a computed `var x: T {…}` still shows its `{`.
        if ch == "{":
            if depth == 0:
                top.append(ch)
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                top.append(ch)
        elif depth == 0:
            top.append(ch)
    names = []
    for line in "".join(top).splitlines():
        m = re.match(r"\s*(?:@\w+\s+)*(?:private\s+|fileprivate\s+)?(?:let|var)\s+(\w+)\s*[:=]", line)
        if m and "{" not in line[m.end():]:
            names.append(m.group(1))
    return names


def _switch_arm(body: str, case: str) -> str:
    """The text of one `case .x:` arm in a `switch self` body, up to the next arm."""
    m = re.search(rf"case\s+\.{case}\s*:", body)
    assert m, f"no `case .{case}:` arm"
    nxt = re.search(r"\bcase\s+\.\w+\s*:|\bdefault\s*:", body[m.end():])
    return body[m.end(): m.end() + nxt.start()] if nxt else body[m.end():]


# ── decode: optional on the wire, so old reports never crash ────────────────────


def test_competitor_dto_decodes_segment_optionally():
    dto = _decl_body(_code(_REPORT_DTOS), r"struct CompetitorDTO\s*:\s*Codable\s*\{")
    assert re.search(r"\blet segment:\s*String\?", dto), (
        "CompetitorDTO.segment must be `String?` — the key is absent on every report "
        "stored before 2026-10-01, and a non-optional one fails the whole decode"
    )
    keys = _decl_body(dto, r"enum CodingKeys\s*:\s*String\s*,\s*CodingKey\s*\{")
    assert re.search(r"\bcase\b[^\n]*\bsegment\b", keys), "segment is missing from CodingKeys"
    assert not re.search(r'\bsegment\s*=\s*"(?!segment")', keys), (
        'the wire key is plain "segment"'
    )
    if "init(from" in dto:   # a hand-written decoder must not require the key
        assert "decode(String.self, forKey: .segment)" not in dto


def test_moat_competition_dto_decodes_the_order_marker_optionally():
    dto = _decl_body(_code(_REPORT_DTOS), r"struct MoatCompetitionDTO\s*:\s*Codable\s*\{")
    assert re.search(r"\blet competitorOrder:\s*String\?", dto)
    assert 'case competitorOrder = "competitor_order"' in dto
    if "init(from" in dto:
        assert "decode(String.self, forKey: .competitorOrder)" not in dto


def test_mapper_passes_segment_and_order_through():
    src = _code(_REPORT_DTOS)
    row = _call_args(src, r"CompetitorComparison\(")
    assert re.search(r"segment:\s*Self\.mapCompetitorSegment\(c\.segment\)", row), (
        "the mapper dropped the segment — the row would never show it"
    )
    moat = _call_args(src, r"ReportMoatCompetitionData\(")
    assert re.search(
        r"competitorOrder:\s*CompetitorListOrder\(wireValue:\s*moatCompetition\.competitorOrder\)",
        moat,
    ), "the mapper dropped the order marker — every list would read as threat-ordered"
    clean = _decl_body(src, r"private static func mapCompetitorSegment\([^)]*\)[^{]*\{")
    assert "trimmingCharacters(in: .whitespacesAndNewlines)" in clean
    assert "isEmpty" in clean and "return nil" in clean, (
        "a blank segment must become nil, or the row draws an empty line"
    )


# ── the domain model ────────────────────────────────────────────────────────────


def test_competitor_comparison_segment_defaults_to_nil_and_is_last():
    body = _decl_body(_code(_REPORT_MODELS), r"struct CompetitorComparison\s*:\s*Identifiable\s*\{")
    assert re.search(r"\bvar segment:\s*String\?\s*=\s*nil", body)
    props = _stored_properties(body)
    assert props and props[-1] == "segment", (
        f"segment must be the LAST stored property (got {props}) so every existing "
        "memberwise call keeps compiling"
    )


def test_moat_model_order_defaults_to_the_safe_reading_and_is_last():
    body = _decl_body(_code(_REPORT_MODELS), r"struct ReportMoatCompetitionData\s*\{")
    assert re.search(r"\bvar competitorOrder:\s*CompetitorListOrder\s*=\s*\.highestThreat\b", body)
    props = _stored_properties(body)
    assert props and props[-1] == "competitorOrder", f"got {props}"


def test_only_the_exact_wire_value_direct_means_most_direct():
    enum = _decl_body(_code(_REPORT_MODELS), r"enum CompetitorListOrder\s*\{")
    init = _decl_body(enum, r"init\(wireValue:\s*String\?\)\s*\{")
    ternary = re.search(
        r'self\s*=\s*\(?\s*wireValue\s*==\s*"direct"\s*\)?\s*\?\s*\.mostDirect\s*:\s*\.highestThreat\b',
        init,
    )
    switch = (
        re.search(r'case\s+"direct"\s*:\s*self\s*=\s*\.mostDirect', init)
        and re.search(r"default\s*:\s*self\s*=\s*\.highestThreat", init)
    )
    assert ternary or switch, (
        "CompetitorListOrder(wireValue:) must map exactly \"direct\" to .mostDirect and "
        "EVERYTHING else (nil included) to .highestThreat"
    )
    assert init.count(".mostDirect") == 1, "a second road to .mostDirect"
    for loosener in ("lowercased", "contains(", "hasPrefix", "!=", "caseInsensitive"):
        assert loosener not in init, f"`{loosener}` loosens the exact-match rule"


def test_caption_claims_most_direct_only_for_the_direct_marker():
    enum = _decl_body(_code(_REPORT_MODELS), r"enum CompetitorListOrder\s*\{")
    caption = _decl_body(enum, r"var caption:\s*String\s*\{")
    direct = _switch_arm(caption, "mostDirect")
    threat = _switch_arm(caption, "highestThreat")
    assert '"Most direct first · badge and score = competitive threat (0–10)"' in direct
    assert '"Highest threat first · score 0–10"' in threat
    assert "direct" not in threat.lower(), "the threat caption must never mention directness"


# ── the row ─────────────────────────────────────────────────────────────────────


def test_row_draws_the_segment_on_its_own_wrapping_line():
    body = _decl_body(_code(_PEER_ROW), r"var body:\s*some View\s*\{")
    header = _decl_body(body, r"HStack\s*\{")          # name/ticker + badge row
    assert "competitor.segment" not in header, (
        "the segment must sit on its own full-width line, not squeezed beside the badge"
    )
    seg = _decl_body(body, r"if let segment = competitor\.segment\s*\{")
    for token in (
        "Text(segment)",
        _SEGMENT_LINE_LIMIT,
        ".fixedSize(horizontal: false, vertical: true)",
        "AppColors.textSecondary",
        ".frame(maxWidth: .infinity, alignment: .leading)",
    ):
        assert token in seg, f"segment line lost {token}"
    # Order: name/ticker row → segment → score bar.
    assert body.index("competitor.threatLevel.rawValue") < body.index("competitor.segment")
    assert body.index("competitor.segment") < body.index("GeometryReader")


def test_segment_line_limit_lifts_at_accessibility_sizes():
    """Review ios/F2: the server caps the label at 48 chars, so 2 lines always fit at standard
    sizes — a hard `.lineLimit(2)` bites ONLY at AX sizes (~15 chars a line at AX5), cutting
    "Custom AI accelerators & netw…" for exactly the low-vision readers who enlarged it."""
    src = _code(_PEER_ROW)
    row = _decl_body(src, r"struct ReportPeerComparisonRow\s*:\s*View\s*\{")
    assert re.search(
        r"@Environment\(\\\.dynamicTypeSize\)\s*(?:private\s+)?var\s+dynamicTypeSize\b", row
    ), "the row must read the environment's Dynamic Type size"
    seg = _decl_body(
        _decl_body(row, r"var body:\s*some View\s*\{"), r"if let segment = competitor\.segment\s*\{"
    )
    limits = re.findall(r"\.lineLimit\([^)]*\)", seg)
    assert limits == [_SEGMENT_LINE_LIMIT], (
        f"the segment line must carry exactly {_SEGMENT_LINE_LIMIT} (got {limits}) — "
        "nil at accessibility sizes, 2 otherwise"
    )


def test_threat_badge_never_compresses():
    header = _decl_body(_decl_body(_code(_PEER_ROW), r"var body:\s*some View\s*\{"), r"HStack\s*\{")
    badge = header[header.index("competitor.threatLevel.rawValue"):]
    assert ".fixedSize()" in badge, "a long name must truncate, never squeeze the badge"


def test_score_bar_is_clamped():
    src = _code(_PEER_ROW)
    frac = _decl_body(src, r"private var barFraction:\s*Double\s*\{")
    assert "isFinite" in frac and "min(" in frac and "max(" in frac
    assert "geo.size.width * barFraction" in _decl_body(src, r"var body:\s*some View\s*\{")


# ── the section: legend + info button, only over a non-empty list ───────────────


def test_legend_and_info_button_live_only_over_a_non_empty_list():
    src = _code(_MOAT_SECTION)
    section = _decl_body(src, r"private var peerComparisonSection:\s*some View\s*\{")
    empty = _decl_body(section, r"if data\.competitors\.isEmpty\s*\{")
    after = section[section.index(empty) + len(empty):]
    m = re.match(r"\s*else\s*\{", after)
    assert m, "the non-empty branch is no longer the `else` of `if data.competitors.isEmpty`"
    non_empty = _decl_body(after, r"else\s*\{")
    assert "competitorOrderLegend" not in empty and "info.circle" not in empty
    assert "competitorOrderLegend" in non_empty
    assert section.count("competitorOrderLegend") == 1
    assert re.search(r"\.sheet\(isPresented:\s*\$showCompetitorsInfo\)", section)
    assert "CompetitorsInfoSheet(order: data.competitorOrder)" in section


def test_legend_caption_follows_the_marker_and_the_button_meets_the_target():
    src = _code(_MOAT_SECTION)
    legend = _decl_body(src, r"private var competitorOrderLegend:\s*some View\s*\{")
    assert "Text(data.competitorOrder.caption)" in legend
    assert "Most direct first" not in src, (
        "a hard-coded caption bypasses the order marker — old reports would claim it"
    )
    button = legend[legend.index("Button"):]
    assert 'Image(systemName: "info.circle")' in button
    assert "showCompetitorsInfo = true" in button
    assert ".frame(width: HitSlop.minimumTarget, height: HitSlop.minimumTarget" in button
    assert ".hitSlop(reaching: HitSlop.minimumTarget)" in button
    assert button.index(".frame(width: HitSlop.minimumTarget") < button.index(".hitSlop("), (
        "size the box first: slop alone is clipped by a parent that fits it tightly"
    )
    assert '.accessibilityLabel("How competitors are chosen")' in button
    assert "GrowthInfoIcon" not in src


# ── the info sheet ──────────────────────────────────────────────────────────────

_VENDOR_OR_MODEL = re.compile(
    r"gemini|google|openai|chatgpt|\bfmp\b|financial modeling prep|\bllm\b|language model",
    re.I,
)


def test_info_sheet_follows_the_marker():
    src = _code(_INFO_SHEET)
    assert re.search(r"var order:\s*CompetitorListOrder\s*=\s*\.highestThreat", src)
    order = _decl_body(src, r"private var orderText:\s*String\s*\{")
    direct, threat = _switch_arm(order, "mostDirect"), _switch_arm(order, "highestThreat")
    assert "Most direct first" in direct
    assert "Highest threat first" in threat and "Most direct first" not in threat
    source = _decl_body(src, r"private var sourceText:\s*String\s*\{")
    # The customer/partner exclusion is only true of the list that applied it.
    assert "customers, suppliers or partners" in _switch_arm(source, "mostDirect")
    assert "customers" not in _switch_arm(source, "highestThreat")


def test_info_sheet_matches_reports_built_after_web_research_was_retired():
    """Web research for rivals was retired 2026-10-02: a new report is threat-ordered,
    built from the industry peer list, and scores every rival with a neutral (1.0) moat
    factor (`peer_moats = {}` in the collector). Stored reports kept their moat scaling and
    research list, so the sheet may describe both only as conditional or past."""
    src = _code(_INFO_SHEET)
    scoring = _decl_body(src, r"private var scoringText:\s*String\s*\{")
    for case in ("mostDirect", "highestThreat"):
        arm = _switch_arm(scoring, case)
        assert "on reports where the rival's own moat score was available" in arm, (
            f"the .{case} scoring sentence must make the moat scaling conditional"
        )
        assert "then scales the result" not in arm, (
            f"the .{case} scoring sentence says every score is scaled by the rival's moat"
        )
    threat = _switch_arm(_decl_body(src, r"private var sourceText:\s*String\s*\{"),
                         "highestThreat")
    assert "same industry" in threat and "research" in threat, "anti-vacuity: wrong arm"
    assert threat.index("same industry") < threat.index("research"), (
        "a threat-ordered report must lead with the industry peer list — every new report "
        "comes from it — and mention Cay's research only as what earlier reports used"
    )


def test_info_sheet_explains_the_threat_bands_and_the_midpoint():
    src = _code(_INFO_SHEET)
    assert "5 is a neutral midpoint" in src
    threat = _decl_body(src, r"private var threatSection:\s*some View\s*\{")
    for level in (".high", ".moderate", ".low"):
        assert f"row(level: {level}" in threat
    # The collector's bands: ≥ 7.0 high, ≤ 3.0 low.
    assert "7 or above" in threat and "3 or below" in threat


def _without_most_direct_arms(src: str) -> tuple[str, list[str]]:
    """``src`` with the `case .mostDirect:` arm cut out of every `switch order { … }`,
    plus the arms that were cut. Whatever is left is what a threat-ordered report can
    show — the shared views AND every `.highestThreat` arm."""
    arms: list[str] = []
    out, pos = [], 0
    for m in re.finditer(r"switch\s+order\s*\{", src):
        if m.start() < pos:
            continue
        start = m.end() - 1
        end = _match_close(src, start, "{", "}")
        body = src[start:end + 1]
        arm = _switch_arm(body, "mostDirect")
        arms.append(arm)
        out.append(src[pos:start])
        out.append(body.replace(arm, "", 1))
        pos = end + 1
    out.append(src[pos:])
    return "".join(out), arms


def test_info_sheet_scoring_claims_directness_only_for_the_direct_list():
    """Review ios/F1: on a threat-ordered report the score's "directness" input is only
    the rival's position in its source list (the industry peer list's own order on the
    fallback path — "NOT a directness rank", per the collector), and Cay's chat says so.
    So "how directly" may appear only inside a `.mostDirect` arm."""
    src = _code(_INFO_SHEET)
    threat_section = _decl_body(src, r"private var threatSection:\s*some View\s*\{")
    assert "Text(scoringText)" in threat_section, (
        "the scoring paragraph must come from the order-aware `scoringText`"
    )
    assert "directly" not in threat_section.lower(), (
        "a hard-coded scoring sentence in the shared section reaches threat-ordered reports"
    )
    scoring = _decl_body(src, r"private var scoringText:\s*String\s*\{")
    direct, threat = _switch_arm(scoring, "mostDirect"), _switch_arm(scoring, "highestThreat")
    assert "how directly the rival competes" in direct
    assert "directly" not in threat.lower()
    # True of BOTH sources a threat-ordered list can come from.
    for phrase in ("position in the list it came from", "Cay's research", "industry peer list"):
        assert phrase in threat, f"the threat-ordered scoring sentence lost {phrase!r}"
    for shared in ("return on invested capital", "rival's own moat", "sector's median"):
        assert shared in direct and shared in threat, f"{shared!r} must stay in both arms"

    # Path-agnostic: with every `.mostDirect` arm cut out, nothing says "how directly".
    rest, arms = _without_most_direct_arms(src)
    assert len(arms) >= 3, f"expected the source/order/scoring switches, got {len(arms)}"
    assert sum("how directly" in a for a in arms) >= 2, (
        "anti-vacuity: the cut arms must be where the phrase lives"
    )
    assert "how directly" not in rest.lower(), (
        "a threat-ordered report would read that the score measures how directly each "
        "rival competes"
    )


def test_info_sheet_previews_both_orders():
    src = _code(_INFO_SHEET)
    previews = src[src.index("#Preview"):]
    assert "CompetitorsInfoSheet(order: .mostDirect)" in previews
    assert "CompetitorsInfoSheet(order: .highestThreat)" in previews


def test_info_sheet_names_no_vendor_or_model():
    hits = _VENDOR_OR_MODEL.findall(_code(_INFO_SHEET))
    assert not hits, f"user-facing copy names a vendor/model: {hits}"


@pytest.mark.parametrize("path", [_PEER_ROW, _MOAT_SECTION, _INFO_SHEET])
def test_every_touched_view_keeps_a_preview(path):
    assert "#Preview" in _code(path)


# ── sample data exercises the new line at its limit ─────────────────────────────


def test_sample_rows_carry_segments_up_to_the_cap():
    src = _code(_REPORT_MODELS)
    segs = [
        m.group(1)
        for m in re.finditer(r'CompetitorComparison\([^\n]*?segment:\s*"([^"]*)"', src)
    ]
    assert len(segs) >= 2, f"sample rows with a segment: {segs}"
    assert any(len(s) == _SEGMENT_MAX_CHARS for s in segs), (
        "one sample segment must be exactly the 48-char cap, so the preview shows the "
        "longest label the server can send"
    )
    assert all(len(s) <= _SEGMENT_MAX_CHARS for s in segs)


# ── anti-vacuity ────────────────────────────────────────────────────────────────


def test_the_comment_stripper_actually_strips():
    """The doc comment on the legend quotes the tester; if the stripper stopped working,
    every token scan above could pass on prose after the code was reverted."""
    raw = _MOAT_SECTION.read_text(encoding="utf-8")
    phrase = "Is NVIDIA the main"
    assert phrase in raw, "the control phrase moved — pick one that exists ONLY in a comment"
    assert phrase not in _code(_MOAT_SECTION)
    # …and string literals survive stripping (a `//` inside a string is not a comment).
    assert '"How competitors are chosen"' in _code(_MOAT_SECTION)


def test_the_decl_bounding_actually_bounds():
    """The segment's line limit lives in the segment line and NOT in the header row,
    while the whole body has both — the distinction is visible only because the scan is
    bounded."""
    body = _decl_body(_code(_PEER_ROW), r"var body:\s*some View\s*\{")
    header = _decl_body(body, r"HStack\s*\{")
    assert _SEGMENT_LINE_LIMIT in body and _SEGMENT_LINE_LIMIT not in header
    assert ".lineLimit(1)" in header and ".lineLimit(1)" not in _decl_body(
        body, r"if let segment = competitor\.segment\s*\{"
    )
    assert "competitor.name" in header


def test_the_stored_property_scanner_skips_computed_properties():
    body = "{\n    let a: Int\n    var b: String? = nil\n    var c: Int { 1 }\n    var d: Int {\n        let inner = 2\n        return inner\n    }\n}"
    assert _stored_properties(body) == ["a", "b"]
