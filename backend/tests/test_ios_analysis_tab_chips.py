"""Analysis-tab Cay AI chips must ask only what the stock chat can answer (TestFlight E7).

Three of the four chips asked for analyst grades / price targets / upgrades — FMP endpoints
outside the licence, whose tool is removed from the chat toolset and whose absence the
prompt tells Cay AI to state plainly. A chip that invites a refusal is worse than no chip.
The replacements are grounded: the sentiment tool ("market mood"), the technical readings
now carried in `analysisContext` (RSI/MACD/Stoch/MA levels, prefetched when the tab
appears), the key-stats/valuation lines in the base context, and the technical summary.

Comment-stripped, brace-bounded (`.claude/rules/testing.md` §3).
"""
from __future__ import annotations

import re
from pathlib import Path

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_VM = _IOS / "ViewModels" / "TickerDetailViewModel.swift"
_VIEW = _IOS / "Views" / "Screens" / "TickerDetailView.swift"

_UNLICENSED = ("price target", "analysts say", "upgrade", "consensus", "street", "downgrade")


def _strip_comments(src: str) -> str:
    """Block comments, full-line `//` comments AND trailing `//` comments — a trailing
    comment on a code line (`foo() // fetchHolders(...)`) must not satisfy an `in`."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", line) for line in src.splitlines())


def _code(path: Path) -> str:
    assert path.exists(), f"{path} moved — update this guard, do not delete it"
    return _strip_comments(path.read_text(encoding="utf-8"))


def _decl_block(src: str, prefix: str) -> str:
    at = src.find(prefix)
    assert at != -1, f"{prefix!r} not found — this scan has drifted"
    start = src.index("{", at)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start: i + 1]
    raise AssertionError("unbalanced braces")


def _analysis_arm() -> str:
    block = _decl_block(_code(_VM), "var aiSuggestions: [TickerAISuggestion]")
    assert "case .financials:" in block and "case .news:" in block  # anti-vacuity
    arm = block[block.index("case .analysis:"): block.index("case .news:")]
    assert len(arm) < len(block)
    return arm


def test_analysis_tab_chips_never_name_unlicensed_analyst_data():
    arm = _analysis_arm().lower()
    for token in _UNLICENSED:
        assert token not in arm, f"chip still asks for {token!r}: {arm}"


def test_analysis_tab_has_four_grounded_chips():
    arm = _analysis_arm()
    chips = re.findall(r'TickerAISuggestion\(text:\s*"([^"]+)"\)', arm)
    assert len(chips) == 4, chips
    assert chips == [
        "What's the market mood?",
        "Is it overbought or oversold?",
        "Is it fairly valued?",
        "Technical outlook?",
    ]


def test_analysis_context_carries_the_indicator_readings_for_the_overbought_chip():
    body = _decl_block(_code(_VM), "private var analysisContext: String?")
    assert "technicalAnalysisDetailData" in body
    assert "detail.oscillators" in body and "detail.movingAverages" in body
    assert "osc.value" in body and "guard let v = osc.value else { return nil }" in body, \
        "a null reading must be omitted, never grounded as 0"
    assert "matchingIndicators" in body
    # the licence-gated analyst lines still go through the accessor that nils when unlicensed
    assert "analystRatingsData?.groundingLines" in body


def test_the_valuation_grounding_drops_placeholder_glyphs_and_an_unavailable_rating():
    body = _decl_block(_code(_VM), "private var analysisContext: String?")
    assert re.search(r'\.filter \{ !\["—", "N/A", "--", ""\]\.contains\(\$0\.value\) \}', body), body
    assert "snapshot.rating != .unavailable || !multiples.isEmpty" in body, body
    assert "rating unavailable" in body


def test_the_analysis_tab_prefetches_the_technical_detail():
    view = _code(_VIEW)
    arm = view[view.index("case .analysis:"): view.index("case .financials:")]
    assert ".onAppear { viewModel.fetchTechnicalAnalysisDetail() }" in arm


def test_analysis_context_doc_comment_no_longer_promises_price_targets():
    raw = _VM.read_text(encoding="utf-8")
    i = raw.index("private var analysisContext")
    assert "price targets" not in raw[max(0, i - 400): i]
