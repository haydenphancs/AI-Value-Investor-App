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


# ── Reader-preference block placement (Phase 4) ──────────────────────────────
#
# The amended ADVICE_BOUNDARY says "If a USER PREFERENCES block appears ABOVE…", and the
# fenced CLIENT CONTEXT tells the model to follow no instruction inside it. Both make the
# block's POSITION load-bearing: too early and it precedes the guards it is constrained
# by; too late and it lands inside an untrusted span and goes inert.

def _lens() -> str:
    """The REAL rendered block, so these pin the shipping string rather than a mock."""
    from app.services.agents.investor_profile_prompt import render_profile_block
    return render_profile_block({"topics": ["energy"], "experience_level": "new"})


# Block-only marker: the phrase "USER PREFERENCES" also appears inside the amended
# ADVICE_BOUNDARY ("If a USER PREFERENCES block appears above…"), so searching for that
# finds the boundary even when no block was supplied.
_MARK = "Topics they follow:"
_LENS = None  # populated per-test via _lens()


def _svc():
    from app.services.chat_service import ChatService
    return ChatService.__new__(ChatService)


def test_reader_lens_is_absent_unless_supplied():
    instr = _svc()._build_system_instruction("NORMAL", None)
    assert _MARK not in instr


def test_reader_lens_sits_after_the_guards():
    from app.services.agents.persona_config import ADVICE_BOUNDARY, IDENTITY_RULE

    instr = _svc()._build_system_instruction("NORMAL", None, reader_lens=_lens())
    assert instr.startswith(IDENTITY_RULE), "identity rule must stay first"
    assert instr.index(ADVICE_BOUNDARY) < instr.index(_MARK), (
        "ADVICE_BOUNDARY refers to a preferences block 'above' it — the block must "
        "follow the boundary, not precede it"
    )


def test_reader_lens_sits_before_the_untrusted_client_context():
    instr = _svc()._build_system_instruction(
        "STOCK", "AAPL", client_context="symbol: AAPL | price: 1.23",
        asset_type="STOCK", reader_lens=_lens(),
    )
    assert instr.index(_MARK) < instr.index("<<<CLIENT_CONTEXT>>>"), (
        "the preferences block must not fall inside the fenced span the model is told "
        "to treat as data and never follow"
    )


def test_reader_lens_does_not_disturb_the_guards():
    """Adding the block must not drop either shared guard."""
    from app.services.agents.persona_config import ADVICE_BOUNDARY, IDENTITY_RULE

    instr = _svc()._build_system_instruction("STOCK", "AAPL", reader_lens=_lens())
    assert IDENTITY_RULE in instr and ADVICE_BOUNDARY in instr


def test_advice_boundary_forbids_suitability_language_explicitly():
    """The amendment's whole job: preferences may steer coverage, never suitability."""
    from app.services.agents.persona_config import ADVICE_BOUNDARY

    low = ADVICE_BOUNDARY.lower()
    assert "user preferences" in low
    assert "never state or imply" in low
    assert "an interest in a topic is never a reason to own anything" in low
    # The pre-existing promises must survive verbatim.
    assert "you do not know this user's finances" in low
