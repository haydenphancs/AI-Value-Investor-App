"""Pin the Cay AI identity rule to a SINGLE source (no drift across surfaces).

The rule previously existed as independent hardcoded copies on the persona surface and the
chat surface, which can silently drift (one gets tightened, the other doesn't → a leak path).
These tests assert both user-facing surfaces derive from the one exported constant
`persona_config.IDENTITY_RULE`, and that the old drifted wording is gone.
"""

from __future__ import annotations

from app.services.agents.persona_config import (
    ADVICE_BOUNDARY,
    IDENTITY_RULE,
    PersonaConfig,
    _IDENTITY_RULE,
)
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


# ── Advice boundary: single source, unconditional, covers suitability ─────────
#
# The buy/sell line used to live INSIDE PersonaConfig._bias_block(), which returns ""
# early when a persona sets none of the structured style fields — so such a persona
# shipped with no compliance instruction at all. It is now appended unconditionally.
# Nothing previously addressed suitability, while the app itself shipped "Should I
# buy?" and "Is this ETF right for me?" prompts.


def test_advice_boundary_covers_directives_and_suitability():
    low = ADVICE_BOUNDARY.lower()
    assert "never tell the user to buy, sell, or hold" in low
    assert "suitability" in low
    assert "risk tolerance" in low
    assert "not a registered investment adviser" in low


def test_every_persona_prompt_carries_the_advice_boundary():
    from app.services.agents.persona_config import PERSONA_KEYS, get_persona_config

    for key in PERSONA_KEYS:
        prompt = get_persona_config(key).system_prompt
        assert ADVICE_BOUNDARY in prompt, f"{key} is missing the advice boundary"


def test_advice_boundary_applies_even_with_no_structured_style_fields():
    """The regression this guards: a bare persona used to get NO compliance text."""
    bare = PersonaConfig(
        key="bare", agent_tag="bare", display_name="Bare",
        system_prompt="Philosophy only, no score_rules/bull_priority/etc.",
    )
    assert bare._bias_block() == "", "precondition: no structured fields → empty block"
    assert ADVICE_BOUNDARY in bare.system_prompt


def test_chat_shares_the_same_advice_boundary_text():
    svc = ChatService.__new__(ChatService)
    instr = svc._build_system_instruction("NORMAL", None)
    assert ADVICE_BOUNDARY in instr
    # The old inline wording must be gone (it would signal a re-forked copy that can
    # drift from the persona surface).
    assert "explain the tradeoffs and let the user decide" not in instr


# ── Attribution values must not name the provider ─────────────────────────────
#
# The prompt-level rule above only governs what the MODEL says. It does not stop
# us from hardcoding the model name into an attribution field ourselves. Two such
# leaks shipped: `generated_by="Gemini 2.0 Flash"` in index_service (which rides in
# the wire payload IndexSnapshotsDataResponse.generated_by) and
# `"generated_by": "gemini-2.5-flash"` in crypto_service (persisted to
# crypto_snapshots, an anon-readable table). This scans the source so a third one
# fails here instead of in production.

_BANNED_IN_ATTRIBUTION = (
    "gemini", "google", "openai", "gpt-", "claude", "anthropic",
    "llama", "mistral", "palm", "vertex",
)


def _generated_by_literals() -> list[tuple[str, int, str]]:
    """Every `generated_by` assignment literal in backend/app, as (file, line, text)."""
    import re
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[1] / "app"
    # generated_by="X"  |  "generated_by": "X"  |  generated_by = 'X'
    pattern = re.compile(r"""["']?generated_by["']?\s*[:=]\s*["']([^"']*)["']""")
    found = []
    for py in app_dir.rglob("*.py"):
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            for m in pattern.finditer(line):
                found.append((str(py.relative_to(app_dir)), i, m.group(1)))
    return found


def test_generated_by_attribution_never_names_the_provider():
    literals = _generated_by_literals()
    assert literals, "scan found no generated_by literals — the regex has rotted"
    offenders = [
        (f, ln, val)
        for f, ln, val in literals
        if any(b in val.lower() for b in _BANNED_IN_ATTRIBUTION)
    ]
    assert not offenders, (
        "generated_by must stay provider-neutral (e.g. 'Cay AI'); found: "
        + "; ".join(f"{f}:{ln} -> {val!r}" for f, ln, val in offenders)
    )


def test_index_snapshot_attribution_is_cay_ai():
    """Pins the specific value the iOS client decodes and may render."""
    literals = {
        (f, val) for f, _ln, val in _generated_by_literals()
        if f.endswith("index_service.py")
    }
    assert literals, "index_service.py no longer sets generated_by"
    assert all(val == "Cay AI" for _f, val in literals), literals
