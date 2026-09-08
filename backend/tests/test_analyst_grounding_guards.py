"""Nothing may hand Cay AI the all-zero analyst payload as if it were data.

THE SHAPE OF THE BUG
--------------------
`grades` and `price-target-consensus` fell outside the signed FMP Order Form on 2026-09-03.
`analyst_service` keeps answering — it has to, the response feeds a card — but every field is a
zero default: `consensus=HOLD, total_analysts=0, low/average/high = 0.0`. Those zeros are
STRUCTURALLY INDISTINGUISHABLE from measurements unless a caller checks, and `section_available`
was added to make the check possible.

It was then applied to exactly ONE of four consumers. The other three each shipped the same
falsehood through a different door:

  * `TickerDetailViewModel.analysisContext` → the chat's grounding context, which read
    "Analyst Consensus: HOLD (0 analysts). Price Target: Low $0, Avg $0, High $0" and got the
    model to state Wall Street's consensus on Apple was HOLD with a $0 target — on a
    credit-charged turn.
  * `ChatService._fetch_analyst_data` → `model_dump()` of the same zeros, handed to Gemini
    whenever it called the `get_analyst_analysis` tool.
  * `build_financial_context` → the same two sentences in the Stage-A prompt of a **20-credit**
    report, plus `_wall_street_insight_prompt` asserting "Consensus rating: hold" while the two
    lines around it correctly said there was no coverage.

So the guards below are per-DOOR, not per-file. A fifth consumer is the thing to be afraid of,
which is why `analyst_is_usable` lives in `_analyst_common` and why the iOS side renders through
`AnalystRatingsData.groundingLines` rather than reading fields.

No network, no Supabase, no Gemini.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.schemas.analyst import (
    AnalystActionsSummary,
    AnalystAnalysisResponse,
    AnalystConsensus,
    AnalystPriceTarget,
    AnalystRatingDistribution,
)
from app.services import chat_service as chat_service_module
from app.services._analyst_common import analyst_is_usable, analyst_section_available
from app.services.chat_service import ChatService

_REPO = Path(__file__).resolve().parents[2]


def _response(**over) -> AnalystAnalysisResponse:
    """The exact shape `analyst_service` produces when the section is blocked."""
    base = dict(
        symbol="AAPL",
        has_coverage=False,
        section_available=False,
        total_analysts=0,
        updated_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        consensus=AnalystConsensus.HOLD,
        target_price=0.0,
        target_upside=0.0,
        distributions=[
            AnalystRatingDistribution(label=lbl, count=0, percentage=0.0)
            for lbl in ("Strong Buy", "Buy", "Hold", "Sell", "Strong Sell")
        ],
        price_target=AnalystPriceTarget(
            low_price=0.0, average_price=0.0, high_price=0.0, current_price=231.0
        ),
        momentum_data=[],
        net_positive=0,
        net_negative=0,
        actions_summary=AnalystActionsSummary(upgrades=0, downgrades=0, maintains=0),
        actions=[],
    )
    base.update(over)
    return AnalystAnalysisResponse(**base)


# ── 0. The premise, and the one predicate everything keys off ───────────────

def test_the_section_really_is_unlicensed_today():
    """If this ever flips, the guards below stop testing the interesting branch — they will
    still pass, but vacuously. Fail loudly here instead."""
    assert analyst_section_available() is False


def test_all_three_absent_states_collapse_for_a_consumer():
    """Distinct at the UI layer (the card renders an honest empty state for one of them),
    identical for anything that grounds a model or prints a number."""
    assert analyst_is_usable(None) is False
    assert analyst_is_usable(_response()) is False
    assert analyst_is_usable(_response(section_available=True)) is False   # no coverage
    assert analyst_is_usable(_response(has_coverage=True)) is False        # unlicensed


def test_a_covered_licensed_response_IS_usable():
    """Anti-vacuity: a predicate that returned False for everything would satisfy every
    assertion in this file while silently deleting the feature if the package is repurchased."""
    assert analyst_is_usable(
        _response(section_available=True, has_coverage=True, total_analysts=31)
    ) is True


# ── 1. Door one: the chat tool ──────────────────────────────────────────────

def test_the_analyst_tool_hands_back_a_marker_not_zeros(monkeypatch):
    svc = ChatService.__new__(ChatService)
    monkeypatch.setattr(
        chat_service_module,
        "analyst_is_usable",
        lambda a: False,
    )
    out = asyncio.run(ChatService._fetch_analyst_data(svc, "AAPL"))

    assert out["available"] is False
    # The zeros must not be in there under ANY key — `model_dump()` spreads them flat, so a
    # partial fix that kept the dump and merely added a flag would still feed the model
    # `consensus: "HOLD"` and `average_price: 0.0`.
    assert "consensus" not in out and "price_target" not in out
    assert "0" not in str(out.get("total_analysts", ""))
    # Stated, not silent. An empty answer invites the model to fill the gap from training data.
    assert "not available" in out["message"].lower()
    assert "do not estimate" in out["message"].lower()


def test_the_analyst_tool_still_returns_real_data_when_it_has_some(monkeypatch):
    """Anti-vacuity for the test above."""
    svc = ChatService.__new__(ChatService)
    good = _response(section_available=True, has_coverage=True, total_analysts=31)

    class _Svc:
        get_analysis = AsyncMock(return_value=good)

    monkeypatch.setattr(
        "app.services.analyst_service.get_analyst_service", lambda: _Svc()
    )
    out = asyncio.run(ChatService._fetch_analyst_data(svc, "AAPL"))
    assert out["available"] is True
    assert out["total_analysts"] == 31


def test_the_tool_is_not_even_offered_while_it_is_unlicensed():
    """The primary guard — `_fetch_analyst_data` above is belt and braces. Both chat tool
    registries filter through `tools_for_asset_type`, so this closes both."""
    from app.services.agents.chat_tools import tools_for_asset_type

    assert "get_analyst_analysis" not in tools_for_asset_type("STOCK")


def test_the_system_prompt_does_not_advertise_a_tool_that_is_gone():
    """It used to say "incorporate the consensus rating, price targets, analyst counts…"
    unconditionally. Telling a model to incorporate output from a tool it does not have is how
    it starts supplying that output from memory."""
    svc = ChatService.__new__(ChatService)
    instruction = ChatService._build_system_instruction(svc, "general", None)

    assert "get_analyst_analysis" not in instruction
    assert "incorporate the consensus rating" not in instruction
    lowered = instruction.lower()
    assert "no analyst ratings or price-target data" in lowered
    assert "rather than estimating or recalling it" in lowered


# ── 2. Door two: the 20-credit report's Stage-A prompt ──────────────────────

def _financial_context(analyst) -> str:
    from app.services.agents.ticker_report_data_collector import (
        CollectedTickerData,
        build_financial_context,
    )

    out = CollectedTickerData(ticker="AAPL", persona_key="value")
    out.analyst_analysis = analyst
    return build_financial_context(out)


def test_the_report_prompt_carries_no_fabricated_consensus():
    text = _financial_context(_response())

    assert "Analyst Consensus: HOLD" not in text, (
        "the paid report's Stage-A prompt still asserts a consensus nobody published"
    )
    assert "$0.00 / $0.00 / $0.00" not in text
    assert "(0 analysts)" not in text
    # Explicit, for the same reason as the chat marker.
    assert "NOT AVAILABLE" in text
    assert "Do not estimate" in text


def test_the_report_prompt_still_carries_a_real_consensus():
    """Anti-vacuity: a gate that suppressed the block unconditionally would pass above."""
    good = _response(
        section_available=True,
        has_coverage=True,
        total_analysts=31,
        # BUY, not HOLD, on purpose: the negative test asserts "Analyst Consensus: HOLD" is
        # absent, and a positive case that also said HOLD could not distinguish "the block was
        # rendered" from "the block was suppressed and something else printed HOLD".
        consensus=AnalystConsensus.BUY,
        target_price=250.0,
        price_target=AnalystPriceTarget(
            low_price=180.0, average_price=250.0, high_price=310.0, current_price=231.0
        ),
    )
    text = _financial_context(good)
    assert "Analyst Consensus: BUY (31 analysts)" in text
    assert "$250.00 / $180.00 / $310.00" in text
    assert "NOT AVAILABLE" not in text


def test_a_missing_response_is_handled_like_an_unusable_one():
    """`None` reaches here whenever the collector's gather leg failed."""
    text = _financial_context(None)
    assert "NOT AVAILABLE" in text
    assert "Analyst Consensus: HOLD" not in text


# ── 3. Door three: the report's Wall Street insight prompt ──────────────────

def _insight_prompt(ws: dict) -> str:
    from app.services.agents.narrative_prompts import _wall_street_insight_prompt
    from app.services.agents.persona_config import get_persona_config

    persona = get_persona_config("value")
    return _wall_street_insight_prompt(persona, "evidence", {"wall_street_consensus": ws})


_NO_COVERAGE_WS = {
    "rating": "hold",          # `_consensus_to_key(None)` defaults to this
    "current_price": 231.0,
    "target_price": None,      # already honest
    "low_target": None,
    "high_target": None,
    "valuation_status": "fair_value",
    "discount_percent": 0.0,
    "analyst_strong_buy": 0, "analyst_buy": 0, "analyst_hold": 0,
    "analyst_sell": 0, "analyst_strong_sell": 0,
}


def test_the_insight_prompt_does_not_state_a_rating_nobody_published():
    """The target line already degraded honestly; the RATING line did not, and a stated rating
    is the half the model quotes."""
    prompt = _insight_prompt(dict(_NO_COVERAGE_WS))
    assert "Consensus rating: hold" not in prompt
    assert "none published" in prompt
    assert "no analyst coverage" in prompt  # the target line, unchanged


def test_the_insight_prompt_states_a_real_rating():
    prompt = _insight_prompt(
        dict(_NO_COVERAGE_WS, rating="buy", target_price=250.0,
             low_target=180.0, high_target=310.0, analyst_buy=20)
    )
    assert "Consensus rating: buy" in prompt
    assert "20 Buy" in prompt
    assert "none published" not in prompt


# ── 4. Door four: iOS. Structural — a consumer cannot spell the fields out ──

def _strip_comments(src: str) -> str:
    """Every token asserted below appears verbatim in the comments explaining the fix."""
    return "\n".join(
        "" if l.strip().startswith("//") else re.sub(r"\s//.*$", "", l)
        for l in src.splitlines()
    )


def _decl_block(src: str, header: str) -> str:
    i = src.index(header)
    j = src.index("{", i)
    depth, k = 0, j
    while k < len(src):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[j:k + 1]
        k += 1
    raise AssertionError(f"unbalanced braces after {header!r}")


_VIEWMODEL = _REPO / "frontend/ios/ios/ViewModels/TickerDetailViewModel.swift"
_MODELS = _REPO / "frontend/ios/ios/Models/TickerDetailModels.swift"
_CONSENSUS_BAR = _REPO / "frontend/ios/ios/Views/Molecules/ReportConsensusBar.swift"


def test_the_chat_context_cannot_name_an_analyst_field():
    """Brace-bounded to `analysisContext`, or the scan passes on `financialsContext` below it."""
    block = _decl_block(
        _strip_comments(_VIEWMODEL.read_text()), "private var analysisContext: String?"
    )
    assert "groundingLines" in block, (
        "analysisContext renders the analyst fields itself again — it cannot see "
        "sectionAvailable/hasCoverage and will ship '$0 target' to a paid turn"
    )
    for banned in (
        "consensus.rawValue", "priceTarget.lowPrice", "priceTarget.averagePrice",
        "priceTarget.highPrice", "formattedUpside", "totalAnalysts",
    ):
        assert banned not in block, (
            f"analysisContext reads `{banned}` directly, bypassing the availability gate"
        )


def test_grounding_lines_are_gated_on_BOTH_flags():
    block = _decl_block(
        _strip_comments(_MODELS.read_text()), "var groundingLines: [String]?"
    )
    guard = block.index("guard")
    assert "sectionAvailable" in block[:guard + 120]
    assert "hasCoverage" in block[:guard + 120]
    assert "return nil" in block[guard:block.index("\n", guard) + 60]


def test_the_report_momentum_strip_is_gated():
    """"0 upgrades · 0 maintains · 0 downgrades" reads as "no analyst moved on this company"
    when the truth is that we cannot see analyst actions at all. Gated on whether coverage
    EXISTS, not on the counts — a genuine zero over 12 months is a real fact worth rendering."""
    block = _decl_block(
        _strip_comments(_CONSENSUS_BAR.read_text()), "private var momentumSection: some View"
    )
    assert "hasAnalystDistribution" in block and "hasAnalystTargets" in block, (
        "the Momentum strip renders unconditionally again"
    )
    strip = block.index("ReportMetricsStrip")
    assert block.index("hasAnalystDistribution") < strip, "the gate is after the render"


def test_the_scanners_are_not_vacuous():
    """The helpers must actually bite — a `_decl_block` that returned the whole file, or a
    comment stripper that left prose in, would make every assertion above meaningless."""
    src = "// groundingLines everywhere\nlet a = 1 // groundingLines\n"
    assert "groundingLines" not in _strip_comments(src)

    body = _decl_block("func a() { X }\nfunc b() { Y }", "func a()")
    assert "X" in body and "Y" not in body

    for p in (_VIEWMODEL, _MODELS, _CONSENSUS_BAR):
        assert p.exists(), p


# ── MUTATION_LOG ────────────────────────────────────────────────────────────
#
# Broken by hand, observed to fail, restored (`.claude/rules/testing.md` §3). Run 2026-09-07.
#
#  1. `analysisContext` reverted to spelling the fields out
#     (`parts.append("Analyst Consensus: \(ar.consensus.rawValue) ...")`)
#       -> test_the_chat_context_cannot_name_an_analyst_field FAILED ✅
#  2. `groundingLines` gated on `hasCoverage` only, dropping `sectionAvailable` — the exact
#     half-fix that shipped: the card checked both, the AI checked neither
#       -> test_grounding_lines_are_gated_on_BOTH_flags FAILED ✅
#  3. `momentumSection`'s gate replaced with `if true`
#       -> test_the_report_momentum_strip_is_gated FAILED ✅
#  4. Stage-A prompt reverted to `if out.analyst_analysis:` — truthy on a response that is
#     ALWAYS present and always zeroed, which is the original bug verbatim
#       -> test_the_report_prompt_carries_no_fabricated_consensus FAILED ✅
#  5. The licence filter in `tools_for_asset_type` replaced with `if False:`
#       -> test_the_tool_is_not_even_offered_while_it_is_unlicensed FAILED ✅
#  6. `_wall_street_insight_prompt`'s new `if dist_parts or tgt:` replaced with `if True:`
#       -> test_the_insight_prompt_does_not_state_a_rating_nobody_published FAILED ✅
#
# The anti-vacuity companions (`..._still_returns_real_data...`,
# `..._still_carries_a_real_consensus`, `test_a_covered_licensed_response_IS_usable`) exist
# because the cheapest way to pass items 1-6 is to suppress the analyst block unconditionally,
# which would silently delete the feature the day the package is repurchased.
