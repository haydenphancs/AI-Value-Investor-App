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


# ── The card's day change has a neutral state too (2026-09-17) ───────────────
#
# `PriceService._shape` emits change=None for "unknown — 0.0 would be a fabricated flat
# day", but the card's `or 0` wire coercion turned that into a green "+$0.00 (+0.00%)" with
# an up arrow, and the LIVE QUOTE line told the model "($+0.00, +0.00%)".


def test_the_wire_model_decodes_the_change_flag_and_every_reader_is_neutral():
    body = _braced(_stripped(_MODELS), "struct StockChartWidgetData")
    assert 'case changeKnown = "change_known"' in body
    assert "let changeKnown: Bool?" in body, "Optional — an older server sends nothing"
    assert "var hasKnownChange: Bool { changeKnown != false }" in body
    for prop in ("formattedChange", "formattedAbsChange"):
        block = body[body.index(f"var {prop}"):]
        block = block[:block.index("}\n", block.index("{"))]
        assert "hasKnownChange" in block and "—" in block, f"{prop} formats a placeholder as a number"
    series = body[body.index("var isSeriesPositive"):]
    assert "hasKnownChange ? isPositive : true" in series[:400]


def test_the_card_view_paints_no_direction_for_an_unknown_change():
    import re
    from pathlib import Path
    view = (Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios" / "Views" / "Molecules"
            / "ChatStockWidgetView.swift").read_text(encoding="utf-8")
    view = re.sub(r"//[^\n]*", "", view)
    price = view[view.index("private var priceSection"):]
    price = price[:price.index("private var chartSection")]
    assert "if widget.hasKnownChange {" in price, "the arrow must be gated"
    assert "!widget.hasKnownChange ? AppColors.textMuted" in price, "the ink must be neutral"
    header = view[:view.index("private var priceSection")]
    assert "!widget.hasKnownChange" in header, "the ticker badge tint must be neutral too"


def test_the_backend_flags_an_unknown_change_and_the_prompt_says_so():
    from app.services.chat_service import ChatService
    known = ChatService._build_stock_widget("AAPL", {"price": 200.0, "change": 1.5, "changePercentage": 0.75},
                                            [], 1, True)
    assert known["change_known"] is True and known["change_percent"] == 0.75
    unknown = ChatService._build_stock_widget("BTCUSD", {"price": 64000.0, "change": None,
                                                         "changePercentage": None}, [], 1, True)
    assert unknown["change_known"] is False and unknown["change"] == 0 and unknown["change_percent"] == 0
    line = ChatService._widget_grounding_line(unknown)
    assert "(day change unknown)" in line and "+0.00" not in line
    zero = ChatService._build_stock_widget("X", {"price": 10.0, "change": 0.0, "changePercentage": 0.0}, [], 1, True)
    assert zero["change_known"] is True, "an explicit flat day is a real number"


# ── The thinking-card labels ─────────────────────────────────────────────────

_LABELLED_TOOLS = (
    "get_stock_chart_data",
    "get_sentiment_analysis",
    "get_market_overview",
    "get_ticker_news",
    "get_market_snapshot",
    "explain_price_move",
    "web_search",
    "check_ownership_filings",
    # 2026-10-09 (1.01 Phase B): labelled on iOS AHEAD of the backend tools (Phase A wave 2), so
    # the build that ships first never renders them as a de-snake-cased identifier.
    "check_company_financials",
    "check_asset_profile",
)

# Tools that answer from Caydex's own cached data. Their labels must never claim a web search —
# the "Searching the web" status is reserved for the one tool that does search the web.
_CAYDEX_DATA_TOOLS = ("check_ownership_filings", "check_company_financials", "check_asset_profile")


@pytest.mark.parametrize("tool", _LABELLED_TOOLS)
def test_every_chat_tool_has_a_human_thinking_label(tool):
    """Without a case, `thinkingLabel(forTool:)` falls through to a de-snake-cased function
    name — so the progress card showed the user "Get stock chart data". Every tool the
    backend can actually call needs a written label."""
    body = _braced(_stripped(_VIEWMODEL), "static func thinkingLabel(forTool name: String)")
    assert f'case "{tool}"' in body, f"{tool} falls through to the raw-identifier default"


def test_every_declared_backend_tool_has_an_ios_label():
    """DERIVED from the backend's own registry, so a tool added there without an iOS label
    fails here — the hand-kept tuple above only pins the ones someone remembered.
    `TOOL_DESCRIPTIONS` includes the gated `web_search` (declared only on a turn whose web
    gate opened), imported by its constant rather than retyped."""
    from app.services.agents.chat_tools import TOOL_DESCRIPTIONS, WEB_SEARCH_TOOL

    body = _braced(_stripped(_VIEWMODEL), "static func thinkingLabel(forTool name: String)")
    assert WEB_SEARCH_TOOL in TOOL_DESCRIPTIONS
    missing = sorted(t for t in TOOL_DESCRIPTIONS if f'case "{t}"' not in body)
    assert not missing, f"these backend tools render as a raw identifier on iOS: {missing}"


def _case_line(body: str, tool: str) -> str:
    lines = [l for l in body.splitlines() if f'case "{tool}":' in l]
    assert len(lines) == 1, f"expected exactly one `case \"{tool}\":` line, found {lines}"
    return lines[0]


@pytest.mark.parametrize("tool", _CAYDEX_DATA_TOOLS)
def test_caydex_data_tool_labels_never_claim_the_web(tool):
    body = _braced(_stripped(_VIEWMODEL), "static func thinkingLabel(forTool name: String)")
    line = _case_line(body, tool)
    label = re.search(r'return "([^"]+)"', line)
    assert label, f"{tool}'s case does not return a literal label: {line!r}"
    text = label.group(1)
    assert "web" not in text.lower() and "search" not in text.lower(), (
        f"{tool} reads Caydex's own data, but its label says {text!r}"
    )
    assert text[:1].isupper() and not text.endswith((".", "…")), (
        f"{text!r} does not match the label style (sentence case, no trailing punctuation)"
    )


def test_the_new_labels_match_the_existing_style():
    """Matched against the shipped style: a gerund phrase ("Checking ownership filings",
    "Scanning recent news"), never the de-snake-cased fallback ("Check company financials")."""
    body = _braced(_stripped(_VIEWMODEL), "static func thinkingLabel(forTool name: String)")
    assert 'return "Checking company financials"' in _case_line(body, "check_company_financials")
    assert 'return "Looking up key facts"' in _case_line(body, "check_asset_profile")


def test_explain_price_move_still_reads_digging_deeper():
    """`explain_price_move` answers only from deterministic attribution and the ticker's cached
    news — its paid web-search tier was retired on 2026-10-02 — so it must never claim a web
    search. (Superseded the old `test_the_web_search_step_is_not_called_searching_the_web`.)"""
    body = _braced(_stripped(_VIEWMODEL), "static func thinkingLabel(forTool name: String)")
    line = _case_line(body, "explain_price_move")
    assert 'return "Digging deeper"' in line
    assert "web" not in line.lower(), f"explain_price_move's label mentions the web: {line!r}"


def test_only_the_explicit_web_search_tool_says_searching_the_web():
    """DECISION CHANGED, deliberately (owner, 2026-10-02). This file used to pin that NO label
    said "Searching the web" — a product call made when the only web search was
    `explain_price_move`'s escalation, which rarely ran. Report chat now has a real,
    explicit-request-only `web_search` tool, and the owner asked for a visible "Searching the
    web…" status while it runs. So the phrase is now allowed in exactly ONE place: the
    `web_search` case. Never on `explain_price_move` (above), never in `default:` (which would
    put it on every unknown tool). Since 2026-10-09 that tool also runs in any chat and
    automatically (consent v3); the label is still true, because it renders only for a search
    that actually started."""
    body = _braced(_stripped(_VIEWMODEL), "static func thinkingLabel(forTool name: String)")
    line = _case_line(body, "web_search")
    assert 'return "Searching the web"' in line
    assert body.count("Searching the web") == 1, (
        "the web-search wording must belong to the web_search case alone"
    )
    default = body[body.index("default:"):]
    assert "Searching the web" not in default


def test_the_label_scan_is_not_vacuous():
    """Anti-vacuity: prove the brace-bounded body is real code, not an empty string. Every
    assertion above is an `in` test, which passes trivially against "" reversed — and against
    a body the extractor failed to find."""
    body = _braced(_stripped(_VIEWMODEL), "static func thinkingLabel(forTool name: String)")
    assert len(body) > 200
    assert "switch name" in body
    assert "//" not in body, "comments must be stripped before asserting"


def test_the_financials_tool_reads_truthfully_on_builds_without_its_label():
    """1.0 and the first 1.01 builds have no `case "check_company_financials"`: their `default:`
    de-snake-cases the name. The backend name was chosen so that fallback is still a true,
    verb-first progress line — replicate the Swift default exactly and pin the words."""
    from app.services.agents.chat_tools import FINANCIALS_TOOL

    body = _braced(_stripped(_VIEWMODEL), "static func thinkingLabel(forTool name: String)")
    default = body[body.index("default:"):]
    assert 'replacingOccurrences(of: "_", with: " ")' in default
    assert "prefix(1).uppercased()" in default
    words = FINANCIALS_TOOL.replace("_", " ")
    assert words[:1].upper() + words[1:] == "Check company financials"


def test_the_profile_tool_reads_truthfully_on_builds_without_its_label():
    """Same as the financials tool: shipped builds without `case "check_asset_profile"` render
    the de-snake-cased name — "Check asset profile", a true, verb-first progress line. Replicates
    the Swift default exactly (asserted above) and pins the words."""
    from app.services.agents.chat_tools import PROFILE_TOOL

    body = _braced(_stripped(_VIEWMODEL), "static func thinkingLabel(forTool name: String)")
    default = body[body.index("default:"):]
    assert 'replacingOccurrences(of: "_", with: " ")' in default
    assert "prefix(1).uppercased()" in default
    words = PROFILE_TOOL.replace("_", " ")
    assert words[:1].upper() + words[1:] == "Check asset profile"
