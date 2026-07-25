"""Pin the Cay AI identity rule to a SINGLE source (no drift across surfaces).

The rule previously existed as independent hardcoded copies on the persona surface and the
chat surface, which can silently drift (one gets tightened, the other doesn't → a leak path).
These tests assert both user-facing surfaces derive from the one exported constant
`persona_config.IDENTITY_RULE`, and that the old drifted wording is gone.
"""

from __future__ import annotations

from app.services.agents.persona_config import IDENTITY_RULE, PersonaConfig, _IDENTITY_RULE
from app.services.chat_service import ChatService


def test_identity_rule_is_exported_and_aliased():
    assert IDENTITY_RULE is _IDENTITY_RULE            # private back-compat alias == public
    assert "Cay AI" in IDENTITY_RULE
    assert "never say Google, Gemini, OpenAI" in IDENTITY_RULE


def test_chat_system_instruction_uses_the_shared_constant():
    # Build the chat system instruction without full ChatService init (no network).
    svc = ChatService.__new__(ChatService)
    instr = svc._build_system_instruction("NORMAL", None)
    assert IDENTITY_RULE in instr, "chat system prompt must embed the shared IDENTITY_RULE verbatim"
    # The OLD drifted inline wording must be gone (would signal a re-forked copy).
    assert "You must NEVER reveal, mention, or hint" not in instr


def test_persona_prompts_embed_the_shared_constant():
    # Every PersonaConfig prepends IDENTITY_RULE in __post_init__.
    p = PersonaConfig(key="k", agent_tag="t", display_name="d", system_prompt="Philosophy here.")
    assert p.system_prompt.startswith(IDENTITY_RULE)


def test_chat_and_persona_share_identical_rule_text():
    svc = ChatService.__new__(ChatService)
    instr = svc._build_system_instruction("NORMAL", None)
    p = PersonaConfig(key="k", agent_tag="t", display_name="d", system_prompt="x")
    # The exact same rule text appears on both surfaces → no drift possible.
    assert IDENTITY_RULE in instr and IDENTITY_RULE in p.system_prompt
