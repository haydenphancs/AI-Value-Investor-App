"""Follow-up chips must be questions Cay AI will answer (TestFlight 2026-09-16, E3).

The chips under an answer proposed "where can I buy DOGE?" and "Who maintains DOGE?" and
the next turn declined both; the tester: "all suggestion question must have answer!".
`chat_chip_filter` is the deterministic half — the prompt describes the answerable scope,
this drops anything the chat would decline even when the model ignores it — applied at
generation AND on replay of stored chips.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.services.chat_chip_filter import filter_answerable_chips, is_answerable_chip

# From the screenshots, the templates, and the shapes the prompt tells the model to refuse.
REFUSED = [
    "Should I buy DOGE?",
    "should I buy?",
    "Should I hold or sell TSLA?",
    "Is DOGE a good buy right now?",
    "Is it a good time to buy MSFT?",
    "Buy or sell?",
    "Is it right for me?",
    "Does this fit my risk profile?",
    "How much should I allocate to AAPL?",
    "What's the price target?",
    "What is the target price for NVDA?",
    "What will the price be in 2027?",
    "Will it go up next week?",
    "Will DOGE reach $1?",
    "Could the stock double this year?",
    "How high will it go?",
    "predict the price of BTC",
    "What do analysts rate it?",
    "What's the analyst consensus?",
    "Any recent upgrades or downgrades?",
    "What is Wall Street's price target?",
    "where can I buy DOGE, should I?",
    # review corpus (2026-09-19): refusal shapes the disclaimer classifier has no frame for
    "Is DOGE a buy?",
    "Is DOGE a good investment?",
    "Which is the better buy, DOGE or SHIB?",
    "What price should I buy at?",
    "Do you recommend DOGE?",
    "Should I add DOGE to my portfolio?",
    "Could DOGE 10x from here?",
    "What's the consensus on DOGE?",
    "Is it rated a buy?",
    "What’s the analysts’ consensus?",          # curly apostrophes
    "Could the stock double this year?",
    "What should I consider buying?",
    "How much should I consider investing?",
    "What is DOGE? Should I buy it?",
    "Is DOGE worth buying?",
    "What is the projected price?",
    "Will it recover?",
    "Will it drop?",
    "Could the stock crash?",
    "would you buy DOGE?",
]

# Everything the same brief made answerable — outlook as scenarios, venues, background.
ANSWERABLE = [
    "What's next for tech?",
    "What are the biggest tech risks?",
    "What are the top tech stocks?",
    "where can I buy DOGE?",
    "Who maintains DOGE?",
    "what is DOGE?",
    "how does doge work?",
    "what is doge's market cap?",
    "what is doge's price?",
    "What is MATIC's current supply?",
    "How does MATIC's supply change over time?",
    "what are GOOGL's historical revenues",
    "what are GOOGL's current financials",
    "What could drive it higher?",
    "What would make it fail?",
    "How does it compare to Bitcoin?",
    "What's the P/E?",
    "Why is it down today?",
    "What drives demand for DOGE?",
    "How volatile has DOGE been?",
    "What is the outlook for semiconductors?",
    "Where is it headed?",
    "What happened to its price last week?",
    "How is DOGE regulated?",
    "What questions should I ask before buying anything?",   # a bundled evergreen starter
    "What should I look for in a 10-K?",
    "Continue your answer",                                    # the cut-answer chip
    # review corpus (2026-09-19): outlook / fundamentals / background shapes that a broad
    # prediction or upgrade regex used to drop
    "Will earnings be up?",
    "Could it double revenue?",
    "Could it double earnings this year?",
    "Could revenue recover next year?",
    "Will margins rebound?",
    "Will the Fed cut rates?",
    "Which sectors could rebound?",
    "Could the network upgrade change fees?",
    "What was the last DOGE upgrade?",
    "Was the credit rating downgraded?",
    "Will Bitcoin's halving matter?",
    "What could push it higher?",
]


@pytest.mark.parametrize("chip", REFUSED)
def test_refused_shapes_are_dropped(chip):
    assert is_answerable_chip(chip) is False, chip


@pytest.mark.parametrize("chip", ANSWERABLE)
def test_answerable_questions_are_kept(chip):
    assert is_answerable_chip(chip) is True, chip


def test_tables_are_populated():
    assert len(REFUSED) >= 15 and len(ANSWERABLE) >= 20


@pytest.mark.parametrize("garbage", [None, "", "   ", 0, 3.5, [], {}, b"bytes"])
def test_non_questions_are_never_answerable(garbage):
    assert is_answerable_chip(garbage) is False


# ── the list normaliser ──────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", [None, "", "a string", 12, {"suggestions": ["x"]}, object()])
def test_a_non_list_degrades_to_empty(raw: Any):
    assert filter_answerable_chips(raw) == []


def test_non_string_elements_are_skipped():
    assert filter_answerable_chips([None, 1, "What is DOGE?", {"q": "x"}, "how does it work?"]) == \
        ["What is DOGE?", "how does it work?"]


def test_whitespace_is_stripped_and_blanks_dropped():
    assert filter_answerable_chips(["  What is DOGE?  ", "   ", "\n"]) == ["What is DOGE?"]


def test_case_insensitive_dedup_preserves_first_spelling_and_order():
    assert filter_answerable_chips(["What is DOGE?", "what is doge?", "How does it work?"]) == \
        ["What is DOGE?", "How does it work?"]


def test_refused_chips_are_dropped_before_the_cap():
    """A dead chip must never displace a live one."""
    assert filter_answerable_chips(["Should I buy?", "A question?", "B question?", "C question?"]) == \
        ["A question?", "B question?"]


def test_the_cap_is_honoured_and_order_kept():
    assert filter_answerable_chips(["Z?", "Y?", "X?"], limit=2) == ["Z?", "Y?"]
    assert filter_answerable_chips(["Z?", "Y?", "X?"], limit=3) == ["Z?", "Y?", "X?"]
    assert filter_answerable_chips(["Z?"], limit=0) == []


def test_all_refused_yields_empty_not_an_exception():
    assert filter_answerable_chips(["Should I buy?", "Will it go up?"]) == []


# ── wired into the generator and the replay ──────────────────────────────────

@pytest.mark.asyncio
async def test_generate_followup_suggestions_drops_a_refused_chip_and_keeps_two(monkeypatch):
    from app.services.chat_service import ChatService

    svc = ChatService.__new__(ChatService)
    captured = {}

    class _Gem:
        async def generate_json(self, prompt, system_instruction=None, model_name=None):
            captured["prompt"] = prompt
            captured["system"] = system_instruction
            return {"text": json.dumps({"suggestions": [
                "Should I buy DOGE?", "Who maintains DOGE?", "where can I buy DOGE?",
            ]})}

    svc.gemini = _Gem()
    out = await svc.generate_followup_suggestions(
        "what is doge?", "Dogecoin is a meme coin.", context_type="CRYPTO", reference_id="DOGE",
    )
    assert out == ["Who maintains DOGE?", "where can I buy DOGE?"]
    assert "ANSWERABLE SCOPE" in captured["prompt"]
    assert "tokenomics" in captured["prompt"], "the crypto scope reached the prompt"
    assert "NEVER propose" in captured["prompt"]


@pytest.mark.asyncio
async def test_generate_followup_suggestions_scope_names_no_tool(monkeypatch):
    """The chip instruction runs with `tools_granted=False`; the scope block is prose."""
    import re
    from app.services.agents.chat_tools import TOOL_DESCRIPTIONS, chip_scope_block
    for asset_type in ("STOCK", "NORMAL", "ETF", "CRYPTO", "INDEX", "COMMODITY", None, "weird"):
        for ctx in (None, "BOOK", "MONEY_MOVES_ARTICLE", "JOURNEY_LESSON"):
            block = chip_scope_block(asset_type, ctx)
            assert len(block) > 300
            for name in TOOL_DESCRIPTIONS:
                assert not re.search(rf"\b{re.escape(name)}\b", block), (asset_type, ctx, name)


def test_the_chip_scope_follows_the_granted_tools(monkeypatch):
    """Review finding: a fixed scope advertised sentiment and a live quote on INDEX and
    COMMODITY chats that have neither tool — the dead-end chip the block exists to stop."""
    from app.services.agents import chat_tools
    from app.services.agents.chat_tools import chip_scope_block, tools_for_asset_type
    monkeypatch.setattr(chat_tools, "analyst_section_available", lambda: False)
    index = chip_scope_block("INDEX")
    assert "mood in news" not in index and "live price" not in index
    assert "how the market and its sectors are doing" in index
    commodity = chip_scope_block("COMMODITY")
    assert "mood in news" not in commodity and "live price" not in commodity
    assert "recent news" in commodity
    crypto = chip_scope_block("CRYPTO")
    assert "mood in news" in crypto and "live price" in crypto and "tokenomics" in crypto
    assert "get_sentiment_analysis" in tools_for_asset_type("CRYPTO")
    # analyst ratings are forbidden while the package is unlicensed…
    assert "analyst ratings, consensus or upgrades/downgrades" in chip_scope_block("STOCK")
    # …and the forbidden line drops when it is granted
    monkeypatch.setattr(chat_tools, "analyst_section_available", lambda: True)
    assert "analyst ratings, consensus or upgrades/downgrades" not in chip_scope_block("STOCK")
    assert "analyst ratings and consensus" in chip_scope_block("STOCK")


def test_learn_chats_get_the_concept_scope_not_an_asset_one():
    from app.services.agents.chat_tools import chip_scope_block
    for ctx in ("BOOK", "MONEY_MOVES_ARTICLE", "JOURNEY_LESSON", "book"):
        block = chip_scope_block("STOCK", ctx)
        assert "idea just discussed" in block
        assert "live price" not in block and "fundamentals, margins" not in block
    assert "idea just discussed" not in chip_scope_block("STOCK", "STOCK")
    assert "idea just discussed" not in chip_scope_block("CRYPTO", None)


@pytest.mark.asyncio
async def test_generate_followup_suggestions_threads_the_learn_context():
    from app.services.chat_service import ChatService

    svc = ChatService.__new__(ChatService)
    captured = {}

    class _Gem:
        async def generate_json(self, prompt, system_instruction=None, model_name=None):
            captured["prompt"] = prompt
            return {"text": json.dumps({"suggestions": ["How do I apply this?", "What's a common mistake?"]})}

    svc.gemini = _Gem()
    out = await svc.generate_followup_suggestions(
        "what is a moat?", "A moat is…", context_type="BOOK", reference_id="3",
    )
    assert out == ["How do I apply this?", "What's a common mistake?"]
    assert "idea just discussed" in captured["prompt"]
    assert "live price" not in captured["prompt"]


def test_replay_of_stored_chips_is_filtered_and_an_all_refused_row_shows_none():
    from app.api.v1.endpoints.chat import _row_to_message
    base = {"id": "m", "session_id": "s", "role": "assistant", "content": "answer",
            "created_at": "2026-07-09T00:00:00.000000+00:00"}
    mixed = _row_to_message({**base, "rich_content": {
        "suggestions": ["Should I buy DOGE?", "Who maintains DOGE?"]}})
    assert mixed.suggestions == ["Who maintains DOGE?"]
    dead = _row_to_message({**base, "rich_content": {"suggestions": ["Should I buy DOGE?"]}})
    assert dead.suggestions is None, "an all-refused stored row renders no chips, not []"
    none = _row_to_message({**base, "rich_content": {}})
    assert none.suggestions is None
    legacy = _row_to_message(base)
    assert legacy.suggestions is None
    kept = _row_to_message({**base, "rich_content": {"suggestions": ["Continue your answer"]}})
    assert kept.suggestions == ["Continue your answer"]
