"""Source-scan guards over the Diversification card's Swift (E2 mirror + E4 hint).

There is no XCTest target, so the two invariants below are pinned from Python by reading
the Swift source. Both were TestFlight reports on the same card:

  * 1.0 (8) — "I have crypto, but it doesn't reflect in here": the OFFLINE mirror
    (`DiversificationCalculator`) must bucket a coin as "Crypto" in both donuts, keep it
    out of the equity size mix (the feed hands it CoinGecko's cap, which would file Dogecoin
    under "Large Cap"), and fold a placeholder sector ("N/A") into "Other" — the same set of
    literals the backend's `PLACEHOLDER_TEXT` uses. The legend renders a sub-half-percent
    slice as "<1%", not "0%".
  * 1.0 (7) — "add something like 'need to add new tickers'": a client-derived hint under
    the verdict, rendered as a plain caption (no Button, no lineLimit), threaded screen →
    section → card, keyed on the SCORED holdings count (`holdingsCount`, which the DTO
    decoded and the UI model used to drop) so it can never disagree with "Based on N of M".
    The copy is a data-entry instruction; the June-2026 decision removed advice from this
    card and the disclaimer sits three rows below it.

Per `.claude/rules/testing.md` §3 and `project_source_scan_guard_vacuity`: comment-stripped,
brace-bound, mutation-tested by hand (see the STATUS log).
"""

import re
from pathlib import Path

import pytest

from app.services._classification_common import PLACEHOLDER_TEXT
from app.services.portfolio_insights_service import (
    CRYPTO_BUCKET, MIN_HOLDINGS, OTHER_SECTOR, UNKNOWN_CAP,
)

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"
_CALC = _IOS / "Core/Utilities/DiversificationCalculator.swift"
_CARD = _IOS / "Views/Molecules/DiversificationCard.swift"
_DONUT = _IOS / "Views/Atoms/DonutChartView.swift"
_MODELS = _IOS / "Models/TrackingModels.swift"
_VM = _IOS / "ViewModels/TrackingViewModel.swift"
_SECTION = _IOS / "Views/Organisms/PortfolioInsightsSection.swift"
_SCREEN = _IOS / "Views/Screens/TrackingView.swift"


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^[ \t]*//.*$", "", src, flags=re.M)


def _decl_body(src: str, prefix: str) -> str:
    at = src.find(prefix)
    assert at != -1, f"{prefix!r} not found — this guard has drifted"
    start = src.index("{", at)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"unbalanced braces after {prefix!r}")


def _code(path: Path) -> str:
    assert path.exists(), f"{path} moved — update this guard, do not delete it"
    return _strip_comments(path.read_text())


# ── 0. Anti-vacuity ────────────────────────────────────────────────────────────


def test_the_comment_stripper_actually_strips():
    raw = _CALC.read_text()
    phrase = "Bucketing mirrors the backend too"
    assert phrase in raw, "the calculator's header comment moved"
    assert phrase not in _code(_CALC)
    # `///` doc comments too — the hint enum's doc names every forbidden word.
    assert "never a buy/sell/sector recommendation" in raw
    assert "never a buy/sell/sector recommendation" not in _code(_CALC)


def test_the_decl_bounding_actually_bounds():
    src = _code(_CALC)
    assert ".crypto" not in _decl_body(src, "private static func capBucket("), (
        "capBucket must know nothing about asset class — the crypto arm lives in the callers"
    )
    assert ".crypto" in _decl_body(src, "static func sectorBucket(")


# ── 1. The offline mirror buckets like the server ─────────────────────────────


def test_the_mirror_buckets_a_coin_as_crypto_in_both_donuts():
    src = _code(_CALC)
    assert f'static let cryptoBucket = "{CRYPTO_BUCKET}"' in src
    sector = _decl_body(src, "static func sectorBucket(")
    size = _decl_body(src, "static func sizeBucket(")
    for body in (sector, size):
        assert ".crypto" in body and "cryptoBucket" in body
    assert f'"{OTHER_SECTOR}"' in sector
    assert f'"{UNKNOWN_CAP}"' in size

    calc = _decl_body(src, "static func calculate(")
    assert "sectorWeights[sectorBucket(h)" in calc, "the sector loop must key on sectorBucket"
    assert "capAlloc[sizeBucket(h)" in calc, "the size donut must key on sizeBucket"
    # The equity size MIX skips coins: the guard sits inside the cap loop, before capBucket.
    cap_loop_at = calc.index("var capWeights")
    guard_at = calc.index("guard h.assetType != .crypto else { continue }")
    first_cap_bucket = calc.index("capBucket(h.marketCap)", cap_loop_at)
    assert cap_loop_at < guard_at < first_cap_bucket


def test_the_swift_placeholder_set_equals_the_python_one():
    src = _code(_CALC)
    at = src.index("static let placeholderText: Set<String> = [")
    literal = src[at:src.index("]", at)]
    swift = set(re.findall(r'"((?:[^"\\]|\\.)*)"', literal))
    swift = {s.replace("\\u{2014}", "—").replace("\\u{2013}", "–") for s in swift}
    assert swift == set(PLACEHOLDER_TEXT), swift ^ set(PLACEHOLDER_TEXT)
    assert "unknown" not in swift


def test_the_legend_never_prints_zero_for_a_real_slice():
    body = _decl_body(_code(_DONUT), "struct DonutChartLegendItem")
    assert '"<1%"' in body and "value < 0.5" in body
    assert 'Text(formattedValue)' in body


# ── 2. holdingsCount reaches the UI model on both paths (S1) ──────────────────


def test_scored_holdings_count_reaches_the_ui_model_on_both_paths():
    models = _code(_MODELS)
    score = _decl_body(models, "struct DiversificationScore")
    assert "let holdingsCount: Int?" in score, "Int?, never Int = 0 — a forgotten constructor would fire the small-book hint"
    assert "holdingsCount: Int? = nil" in score
    mapper = _decl_body(models, "func toDiversificationScore()")
    assert "holdingsCount: holdingsCount" in mapper, "the DTO decodes it; the mapper used to drop it"
    calc = _decl_body(_code(_CALC), "static func calculate(")
    assert re.search(r"holdingsCount: n\b", calc), "the offline mirror must report its own count (not nil)"

    note = _decl_body(_code(_VM), "var portfolioInsightsCoverageNote")
    assert "displayedDiversificationScore?.holdingsCount" in note
    assert note.index("holdingsCount") < note.index("isHolding"), "scored count first, client count as fallback"
    # Clamped to the local total: the score is the LAST server answer and an in-tab removal
    # shrinks the group first — "Based on 3 of 2 tickers" otherwise.
    assert re.search(r"let used = min\(", note), "the scored count must be clamped to the local total"
    assert note.index("let total") < note.index("let used"), "the clamp needs the total first"


# ── 3. The hint (E4) ──────────────────────────────────────────────────────────


def test_hint_rules_are_the_three_branch_table():
    src = _code(_CALC)
    make = _decl_body(src, "static func make(scoredHoldings: Int, enteredTickers: Int, totalTickers: Int) -> String?")
    assert "DiversificationThresholds.smallBookHoldings" in make
    assert "totalTickers - enteredTickers" in make
    assert make.rstrip().endswith("return nil\n    }"), "the default must be nil (no hint)"
    returns = re.findall(r'return "', make)
    assert len(returns) == 3, f"expected exactly 3 copy branches, found {len(returns)}"


def test_hint_copy_never_reads_as_investment_advice():
    body = _decl_body(_code(_CALC), "enum DiversificationHint")
    strings = re.findall(r'"((?:[^"\\]|\\.)*)"', body)
    assert len(strings) >= 3, "anti-vacuity: the copy strings were not found"
    forbidden = ("buy", "sell", "should", "diversif", "improve", "across sectors", "risk")
    for s in strings:
        low = s.lower()
        for word in forbidden:
            assert word not in low, f"hint copy reads as advice: {word!r} in {s!r}"


def test_small_book_threshold_sits_above_the_scoring_floor():
    thresholds = _decl_body(_code(_CALC), "enum DiversificationThresholds")
    small = int(re.search(r"static let smallBookHoldings = (\d+)", thresholds).group(1))
    minimum = int(re.search(r"static let minimumHoldings = (\d+)", thresholds).group(1))
    assert small > minimum
    assert minimum == MIN_HOLDINGS, "the iOS floor must equal the backend MIN_HOLDINGS"


def test_the_card_renders_the_hint_under_the_score_row_as_a_plain_caption():
    card = _code(_CARD)
    overall = _decl_body(card, "private var overallSection")
    assert "GradientProgressBar(" in overall and "Text(coverageNote)" in overall, "scan drifted"
    assert "if let hint" in overall and "Text(hint)" in overall
    assert overall.index("Text(hint)") > overall.index("Text(coverageNote)"), "the hint sits under the score row"
    assert "Button" not in overall, "the hint is a caption, not a control"
    assert "lineLimit(" not in overall, "the hint must wrap at large Dynamic Type"
    assert "fixedSize(horizontal: false, vertical: true)" in overall
    # Control: brace-bounding separates the two declarations — the picker DOES hold a Button.
    assert "Button" in _decl_body(card, "private var breakdownPicker")


def test_the_hint_is_threaded_screen_to_section_to_card():
    section = _decl_body(_code(_SECTION), "private var content")
    assert re.search(r"DiversificationCard\(\s*score:\s*score,\s*coverageNote:\s*coverageNote,\s*hint:\s*hint\s*\)", section)
    screen = _decl_body(_code(_SCREEN), "struct AssetsTabContent")
    assert "hint: viewModel.portfolioInsightsHint" in screen
    accessor = _decl_body(_code(_VM), "var portfolioInsightsHint")
    assert "let scored = score.holdingsCount" in accessor, "keyed on the SCORED count, nil → no hint"
    assert "DiversificationHint.make(" in accessor
    assert "scoredHoldings: min(scored, active.items.count)" in accessor, "clamp like the caption"
    assert "enteredTickers: enteredHoldingsCount" in accessor
    assert "totalTickers: active.items.count" in accessor
