"""The three answer-scope rules (TestFlight 2026-09-16, E2 / E4 / E5).

Cay AI dead-ended where it should answer: "I cannot predict what GOOGL will be like in
5 years" (E2), "I cannot advise you on where to buy DOGE, as Caydex is not a registered
investment adviser" (E4), "Caydex does not have information on who maintains DOGE"
(E5). No prompt line asked for any of those — the model over-generalised the advice
boundary, the analyst-data clause and the capability block's "never supply a reason".
`ChatService._FORWARD_LOOKING_RULE` / `_ACCESS_RULE` / `_KNOWLEDGE_RULE` draw the line.

These pins keep the rules present on every turn shape, placed where the other prompt
guards expect them, and free of the two things that would break sibling guards: a tool
identifier (`test_chat_capability_block` asserts the instruction names exactly the
granted tools) and the injection words `test_chat_prompt_fencing` forbids.
"""

import re

import pytest

from app.services.agents.chat_tools import TOOL_DESCRIPTIONS, capability_block, tools_for_asset_type
from app.services.agents.persona_config import ADVICE_BOUNDARY, IDENTITY_RULE
from app.services.chat_service import ChatService

_ASSET_TYPES = ["STOCK", "NORMAL", "ETF", "CRYPTO", "INDEX", "COMMODITY"]
_SENTINELS = ("OUTLOOK QUESTIONS:", "ACCESS QUESTIONS:", "WHAT YOU KNOW:")
_RULES = (ChatService._FORWARD_LOOKING_RULE, ChatService._ACCESS_RULE, ChatService._KNOWLEDGE_RULE)
_FORBIDDEN = ("disregard", "ignore previous", "ignore all previous", "new system prompt", "<<<")


def _svc() -> ChatService:
    return ChatService.__new__(ChatService)


def _instr(asset_type, tools_granted=True, is_deep_dive=False, **kw):
    symbol = {"NORMAL": None, "INDEX": "^GSPC", "COMMODITY": "GCUSD", "CRYPTO": "DOGEUSD"}.get(asset_type, "AAPL")
    return _svc()._build_system_instruction(
        "NORMAL", symbol, asset_type=asset_type, tools_granted=tools_granted,
        is_deep_dive=is_deep_dive, **kw,
    )


# ── the constants themselves ────────────────────────────────────────────────

def test_the_rules_are_substantive_not_placeholders():
    for rule in _RULES:
        assert len(rule) >= 200, rule[:60]
        assert rule.startswith("\n")


def test_the_rules_name_no_tool_identifier():
    for rule in _RULES:
        for name in TOOL_DESCRIPTIONS:
            assert not re.search(rf"\b{re.escape(name)}\b", rule), (name, rule[:60])


def test_the_rules_carry_none_of_the_injection_words():
    for rule in _RULES:
        low = rule.lower()
        for word in _FORBIDDEN:
            assert word not in low, (word, rule[:60])


def test_the_outlook_rule_forbids_the_refusal_and_the_forecast():
    rule = ChatService._FORWARD_LOOKING_RULE
    assert "do NOT say you cannot predict the future" in rule
    # The soft opener is the failure mode the first live run produced ("Predicting the
    # exact future is challenging, but…"); the rule names it.
    assert "do not open with ANY caveat about predicting, forecasting or uncertainty" in rule
    assert "Never answer by restating numbers already given" in rule
    assert "Never give a price target" in rule
    assert "bull and bear scenarios" in rule


def test_the_access_rule_names_venues_as_availability_not_advice():
    rule = ChatService._ACCESS_RULE
    assert "answer them, never decline them" in rule
    for venue in ("Coinbase", "Kraken", "Robinhood"):
        assert venue in rule
    assert "never as a recommendation" in rule
    assert "do not say whether they should buy" in rule


def test_the_knowledge_rule_separates_numbers_from_background():
    rule = ChatService._KNOWLEDGE_RULE
    assert "come ONLY from the data provided or a tool result" in rule
    assert "who founded or maintains a project" in rule
    assert "Never answer a background question with 'Caydex has no information about X'" in rule


def test_the_knowledge_rule_keeps_an_honest_exit():
    """Review finding: without one, the rule + the crypto persona left no way to say "I
    don't know" for an obscure coin — a licence to invent a founder or a date."""
    rule = ChatService._KNOWLEDGE_RULE
    assert "If you genuinely do not know a background fact" in rule
    assert "never invent a founder, a date, a mechanism or a figure" in rule
    persona = _instr("CRYPTO")
    assert "When you know this coin's origin, creators, maintainers and mechanics" in persona
    assert "You know this coin's origin" not in persona


def test_venue_brands_appear_only_in_the_access_rule():
    """A brand name anywhere else in the prompt would read as an endorsement."""
    instr = _instr("CRYPTO")
    for venue in ("Coinbase", "Kraken", "Robinhood"):
        assert instr.count(venue) == 1, venue


# ── placement in the assembled instruction ───────────────────────────────────

@pytest.mark.parametrize("asset_type", _ASSET_TYPES)
@pytest.mark.parametrize("tools_granted", [True, False])
@pytest.mark.parametrize("is_deep_dive", [False, True])
def test_each_rule_appears_exactly_once_on_every_turn_shape(asset_type, tools_granted, is_deep_dive):
    instr = _instr(asset_type, tools_granted=tools_granted, is_deep_dive=is_deep_dive)
    for sentinel in _SENTINELS:
        assert instr.count(sentinel) == 1, (asset_type, tools_granted, is_deep_dive, sentinel)


@pytest.mark.parametrize("asset_type", _ASSET_TYPES)
def test_the_rules_sit_after_the_disclaimer_clause_and_before_the_advice_boundary(asset_type):
    instr = _instr(asset_type)
    disclaimer = instr.index("DISCLAIMER:")
    boundary = instr.index(ADVICE_BOUNDARY)
    for sentinel in _SENTINELS:
        pos = instr.index(sentinel)
        assert disclaimer < pos < boundary, (asset_type, sentinel)
    assert instr.startswith(IDENTITY_RULE)


@pytest.mark.parametrize("asset_type", _ASSET_TYPES)
def test_the_rules_precede_the_reader_lens_and_the_client_context(asset_type):
    instr = _instr(asset_type, reader_lens="\nTopics they follow: value investing.\n",
                   client_context="Price 1.00")
    last_rule = max(instr.index(s) for s in _SENTINELS)
    assert last_rule < instr.index("Topics they follow:")
    assert last_rule < instr.index("<<<CLIENT_CONTEXT>>>")


def test_the_assembled_instruction_still_carries_no_injection_words():
    for asset_type in _ASSET_TYPES:
        low = _instr(asset_type).lower()
        for word in ("disregard", "ignore all previous", "ignore previous", "new system prompt"):
            assert word not in low, (asset_type, word)


# ── the crypto persona now owns the coin's background ────────────────────────

def test_the_crypto_persona_owns_the_coins_background():
    instr = _instr("CRYPTO")
    assert "origin, creators, maintainers and mechanics" in instr
    assert "origin, creators, maintainers and mechanics" not in _instr("STOCK")


# ── the capability block's reworded cause clause ─────────────────────────────

def test_the_capability_block_guards_invented_causes_not_all_facts():
    block = capability_block(tools_for_asset_type("STOCK"))
    assert "Never invent a CAUSE for a price move that a tool did not give you" in block
    assert "Never supply a reason a tool did not give you" not in block
    assert "never stop at 'I don't know' either" in block
    assert capability_block(frozenset()) == ""


def test_the_old_over_general_wording_is_gone_from_the_prompt_tree():
    """The sentence the DOGE refusal generalised from must not come back anywhere the
    model reads."""
    for asset_type in _ASSET_TYPES:
        for granted in (True, False):
            assert "Never supply a reason a tool did not give you" not in _instr(asset_type, tools_granted=granted)
