"""Every user-visible Gemini call must carry the identity + advice guards.

`IDENTITY_RULE` and `ADVICE_BOUNDARY` are single-sourced in `persona_config`, and until
2026-08-07 they reached exactly two surfaces: `PersonaConfig` prompts (report Stage A/B and
report chat) and `chat_service._build_system_instruction`.

Five other generators built their own `system_instruction` string literal — the Updates
AI-Insight card, index commentary, the crypto deep-dive, the ETF one-liner, and news
enrichment — and got neither guard, while the UI attributes their output to "Cay AI" exactly
like the guarded surfaces. So nothing stopped them naming the model when a headline or company
description invited it (CLAUDE.md invariant #7), and nothing stopped a verdict being phrased as
a buy/sell instruction — the likelier failure, and the one with legal weight given
`support.html` publicly describes how these are produced.

`neutral_system_instruction(body)` is the non-persona equivalent of what `PersonaConfig`
applies. This file fails the build if a new call site skips it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.agents.persona_config import (
    ADVICE_BOUNDARY,
    IDENTITY_RULE,
    neutral_system_instruction,
)

_SERVICES = Path(__file__).resolve().parents[1] / "app/services"

# Call sites that legitimately do NOT go through the helper, each with the reason.
_EXEMPT = {
    # Persona prompts get both guards from PersonaConfig.__post_init__, plus the persona's
    # own philosophy and bias block — routing them through the helper would double them.
    "agents/research_agent.py",
    # Builds its instruction from IDENTITY_RULE/ADVICE_BOUNDARY directly (imported by name).
    "chat_service.py",
    # INTERNAL router: classifies a query into a route enum. Its output is never shown to a
    # user, so an identity guard would be noise in the prompt and cost tokens on every turn.
    "agents/chat_router.py",
}


def _rel(path: Path) -> str:
    return str(path.relative_to(_SERVICES))


def test_the_helper_applies_both_guards_in_the_right_order():
    out = neutral_system_instruction("BODY TEXT")
    assert out.startswith(IDENTITY_RULE), "the identity rule must lead"
    assert "BODY TEXT" in out
    assert out.endswith(ADVICE_BOUNDARY), "the advice boundary must be last"


def test_every_user_visible_generator_is_guarded():
    offenders = []
    for path in _SERVICES.rglob("*.py"):
        if _rel(path) in _EXEMPT:
            continue
        src = path.read_text()
        for m in re.finditer(r"system_instruction\s*=\s*(.{0,40})", src, re.S):
            tail = m.group(1)
            # A variable reference is fine — the definition site is checked separately below.
            if "neutral_system_instruction(" in tail:
                continue
            if re.match(r"\s*[\w.\[\]\"']+\s*[,)]", tail):
                continue
            line = src[: m.start()].count("\n") + 1
            offenders.append(f"{_rel(path)}:{line}")

    assert not offenders, (
        "Gemini call(s) building a raw system_instruction without the identity/advice "
        f"guards: {offenders}. Wrap the body in `neutral_system_instruction(...)`, or add "
        f"the file to _EXEMPT with the reason."
    )


@pytest.mark.parametrize(
    "module,symbol",
    [("news_insight_service.py", "_SYSTEM_INSTRUCTION")],
)
def test_module_level_instruction_constants_are_guarded(module, symbol):
    """A constant assigned once and passed by name would slip past the call-site scan."""
    import importlib

    mod = importlib.import_module(f"app.services.{module[:-3]}")
    value = getattr(mod, symbol)
    assert value.startswith(IDENTITY_RULE), f"{module}.{symbol} is missing the identity rule"
    assert value.endswith(ADVICE_BOUNDARY), f"{module}.{symbol} is missing the advice boundary"


def test_the_five_repaired_generators_stay_repaired():
    """Named explicitly so a refactor cannot quietly drop one and still pass the scan above
    by, say, moving the literal into a variable."""
    expected = {
        "news_cache_service.py",
        "index_service.py",
        "crypto_service.py",
        "etf_service.py",
        "news_insight_service.py",
    }
    for name in expected:
        src = (_SERVICES / name).read_text()
        assert "neutral_system_instruction" in src, (
            f"{name} no longer applies the shared guards"
        )


def test_the_guards_are_still_single_sourced():
    """Anti-drift: a copy-pasted identity rule elsewhere would satisfy every test above while
    silently diverging from the real one."""
    hits = []
    for path in _SERVICES.rglob("*.py"):
        if _rel(path) == "agents/persona_config.py":
            continue
        if "CRITICAL IDENTITY RULE" in path.read_text():
            hits.append(_rel(path))
    assert not hits, (
        f"the identity rule text is duplicated in {hits} — import IDENTITY_RULE instead"
    )
