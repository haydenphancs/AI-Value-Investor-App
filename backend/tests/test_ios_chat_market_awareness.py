"""Source-scan guards over the iOS half of the chat market-awareness change.

There is no XCTest target, so iOS invariants that must not regress are pinned from Python by
reading the Swift tree. Per `.claude/rules/testing.md` §3 every scan below strips comments
(`^[ \t]*//`, never `^\s*//` — that would eat a trailing `//` inside a string), brace-bounds
the declaration it means to check, and was mutation-tested by hand.

Comment stripping is LOAD-BEARING here specifically: the fix for each of these carries an
explanatory comment containing every token the scan greps for, so an unstripped scan would
pass on the prose left behind after the code was reverted.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_MODELS = _IOS / "Models" / "ChatConversationModels.swift"
_VIEWMODEL = _IOS / "ViewModels" / "ChatViewModel.swift"

# `^[ \t]*//` — NOT `^\s*//`. `\s` matches a newline, so with MULTILINE the latter can
# consume the line break before a comment and swallow the preceding line of CODE.
_COMMENT = re.compile(r"^[ \t]*//.*$", re.MULTILINE)


def _stripped(path: Path) -> str:
    assert path.exists(), f"{path} moved — this guard is now scanning nothing"
    return _COMMENT.sub("", path.read_text())


def _braced(source: str, declaration: str) -> str:
    """The body of one brace-delimited declaration.

    Whole-file assertions are how a fix to a preview-only duplicate once looked like a fix to
    the live screen. Two Swift models in this very file declare `dayHigh`, so a whole-file
    scan here would pass with the guard deleted from the one that decodes the wire.
    """
    start = source.index(declaration)
    open_brace = source.index("{", start)
    depth, i = 0, open_brace
    while i < len(source):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace : i + 1]
        i += 1
    raise AssertionError(f"unbalanced braces after {declaration!r}")


# ── The fabricated $0.00 day range ───────────────────────────────────────────

def test_the_chat_card_never_renders_a_zero_day_range():
    """⚠️ THIS SHIPPED. The card printed "Day High $0.00 / Day Low $0.00" beside a live price
    because `day_high`/`day_low` come from FMP's `/stable/quote`, which is 402 under the
    signed Order Form, and the backend's `or 0` turned the missing key into a number.

    The `> 0` test is the load-bearing half: it holds against any server, including one too
    old to send `day_range_known`. A guard on the flag ALONE would silently do nothing when
    the field is absent — which is exactly the state every currently-shipped build is in.
    """
    body = _braced(_stripped(_MODELS), "struct StockChartWidgetData")
    assert "var hasDayRange" in body, "the day-range guard is gone from the wire model"
    assert "dayHigh > 0" in body and "dayLow > 0" in body, (
        "hasDayRange must test the VALUES, not only the server's flag — a build that "
        "predates `day_range_known` receives nil and would fall straight through"
    )
    for prop in ("formattedDayHigh", "formattedDayLow"):
        line = next(l for l in body.splitlines() if f"var {prop}" in l)
        assert "hasDayRange" in line, f"{prop} formats unconditionally again"
        assert "—" in line, f"{prop} must render an em dash when the range is unknown"


def test_the_wire_model_decodes_the_day_range_flag():
    body = _braced(_stripped(_MODELS), "struct StockChartWidgetData")
    assert "dayRangeKnown" in body
    assert 'case dayRangeKnown = "day_range_known"' in body, (
        "the CodingKey must match the backend's snake_case field or the flag silently "
        "decodes as nil forever"
    )
    assert "let dayRangeKnown: Bool?" in body, (
        "Optional, not Bool. This struct uses the SYNTHESISED decoder, so a non-optional "
        "Bool makes an older server's payload fail to decode — losing the whole card"
    )


# ── The thinking-card labels ─────────────────────────────────────────────────

_LABELLED_TOOLS = (
    "get_stock_chart_data",
    "get_sentiment_analysis",
    "get_market_overview",
    "get_ticker_news",
    "get_market_snapshot",
    "explain_price_move",
)


@pytest.mark.parametrize("tool", _LABELLED_TOOLS)
def test_every_chat_tool_has_a_human_thinking_label(tool):
    """Without a case, `thinkingLabel(forTool:)` falls through to a de-snake-cased function
    name — so the progress card showed the user "Get stock chart data". Every tool the
    backend can actually call needs a written label."""
    body = _braced(_stripped(_VIEWMODEL), "static func thinkingLabel(forTool name: String)")
    assert f'case "{tool}"' in body, f"{tool} falls through to the raw-identifier default"


def test_the_web_search_step_is_not_called_searching_the_web():
    """A product decision, made explicitly: "Searching the web…" reads as a generic chatbot.

    It would also be wrong most of the time — `explain_price_move` answers from deterministic
    attribution and cached news first, and escalates to a paid search only for a large move it
    cannot otherwise explain.
    """
    body = _braced(_stripped(_VIEWMODEL), "static func thinkingLabel(forTool name: String)")
    assert "Digging deeper" in body
    assert "Searching the web" not in body


def test_the_label_scan_is_not_vacuous():
    """Anti-vacuity: prove the brace-bounded body is real code, not an empty string. Every
    assertion above is an `in` test, which passes trivially against "" reversed — and against
    a body the extractor failed to find."""
    body = _braced(_stripped(_VIEWMODEL), "static func thinkingLabel(forTool name: String)")
    assert len(body) > 200
    assert "switch name" in body
    assert "//" not in body, "comments must be stripped before asserting"
